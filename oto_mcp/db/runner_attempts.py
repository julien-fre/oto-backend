"""La TENTATIVE d'un travail hébergé — `runner_job_attempts`, source unique.

Contrat : `oto-runner/docs/tentatives-journaux-budget.md` §1 (la tentative) et §3 (les
postes, `U`), partagé par oto-backend#943 (Coûts et Consommation), oto#196 (journaux)
et oto#197 (budget). Une ligne naît au CLAIM, dans la transaction qui fait
`attempts + 1` (`db/runner_jobs.claim_next_job`), et se ferme `done`, `failed` ou `lost`.

⚠️ **Deux familles d'écriture, deux régimes d'échec.**
- Une DONNÉE reçue (charge, résultat déclaré par un worker) ne fait JAMAIS échouer une
  écriture : un entier illisible devient NULL, un texte perd ses octets NUL, une
  couverture impossible à sérialiser devient NULL (donc non attestée).
- Une vraie erreur de BASE n'est jamais avalée : ces fonctions prennent la connexion de
  l'appelant et laissent tout lever. La tentative est le socle du fencing.

⚠️ **NULL n'est pas 0.** Un poste reçu est stocké tel quel ; NULL = inconnu. Un résultat
sans `usage_couverture` garde ses nombres mais n'est PAS ATTESTÉ. Le seul zéro que le
SERVEUR atteste est celui d'une tentative refusée au claim : aucun travail servi, donc
aucun appel de modèle.

**Rétention : durable, jamais élaguée.** Aucune clé étrangère — `runner_jobs.run_id`
est `ON DELETE CASCADE` et `prune_orphan_runs` efface les runs anciens, travaux
compris. Aucun identifiant de PERSONNE : `worker_sub` est l'identité d'une machine,
donc rien à pseudonymiser au sens de l'ADR 0062. Au retour arrière d'une bascule, ces
faits sont conservés (contrat §4).
"""
from __future__ import annotations

import json
import uuid
from typing import Any, Mapping, Optional

from .. import runner_prix
from ._conn import _connect

TENTATIVE_OUVERTE, TENTATIVE_REUSSIE, TENTATIVE_ECHOUEE, TENTATIVE_PERDUE = (
    "open", "done", "failed", "lost")
#: Qui paie les appels de modèle d'une tentative. Toute autre valeur est INCONNUE (NULL).
PAYEURS_TENTATIVE = ("org", "platform")
#: Les postes REÇUS du worker. `usage_input_total` est un majorant, jamais tarifé.
POSTES_TENTATIVE = runner_prix.POSTES_RECUS
#: Plafond d'une page de lignes d'un run — au-delà, `truncated` le dit.
LIGNES_TENTATIVES_MAX = 100
#: Les regroupements servis par la ventilation. Une table FERMÉE.
VENTILATIONS_TENTATIVES = ("model", "source", "provider_family", "key_source")

# La couverture d'un refus au claim : la forme que lit `oto_runner.comptage.Compteur.depuis`
# (ses propres noms de postes), plus la source (contrat §1).
_POSTES_DU_COMPTAGE = ("input_tokens", "input_total_tokens", "output_tokens",
                       "cache_creation_input_tokens", "cache_read_input_tokens")
COUVERTURE_REFUS_SERVEUR = {"tours": 0, "declares": dict.fromkeys(_POSTES_DU_COMPTAGE, 0),
                            "sommes": dict.fromkeys(_POSTES_DU_COMPTAGE, 0),
                            "source": "serveur"}

_TENTATIVE_BIGINT_MAX = 2**63 - 1
_TENTATIVE_INT_MAX = 2**31 - 1
_TENTATIVE_TEXTE_MAX = 200
_TENTATIVE_JSON_MAX = 16_384
_TENTATIVE_JSON_PROFONDEUR = 32

# Même vocabulaire que `runner_jobs._SOURCES`, lu sur les rattachements recopiés.
_TENTATIVE_SOURCE_SQL = ("(CASE WHEN fleet_id IS NOT NULL THEN 'batch' "
                         "WHEN trigger_id IS NOT NULL THEN 'scheduled' ELSE 'manual' END)")
_TENTATIVE_FILTRE_ORG = "org_id = %s"
_TENTATIVE_ATTESTEE = "usage_couverture IS NOT NULL"
_TENTATIVE_NON_ATTESTEE = ("usage_couverture IS NULL AND (" + " OR ".join(
    f"{p} IS NOT NULL" for p in POSTES_TENTATIVE) + ")")
_TENTATIVE_SANS_MESURE = ("usage_couverture IS NULL AND outcome <> 'open' AND " + " AND ".join(
    f"{p} IS NULL" for p in POSTES_TENTATIVE))

#: Ce qu'une ligne SERT — la liste exacte, et rien d'autre (ni `org_id`, ni `worker_sub`).
_TENTATIVE_COLONNES_LIGNE = (
    "id::text AS attempt_id, job_id, attempt_no, run_id, trigger_id, fleet_id, "
    f"{_TENTATIVE_SOURCE_SQL} AS source, provider_family, model, key_source, outcome, "
    "stopped, steps, claimed_at, ended_at, " + ", ".join(POSTES_TENTATIVE) + ", "
    f"({_TENTATIVE_ATTESTEE}) AS attested, {runner_prix.unite_budget_sql()} AS budget_units, "
    "nano_usd, bareme, unpriced_reason, price_unverified")


# ── ce qu'une DONNÉE reçue devient ────────────────────────────────────────────

def _tentative_entier(v: Any, maximum: int = _TENTATIVE_BIGINT_MAX) -> Optional[int]:
    """Un entier positif ou nul qui tient dans la colonne — sinon NULL, jamais une levée."""
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        n = v
    elif isinstance(v, float) and v.is_integer():
        n = int(v)
    elif isinstance(v, str) and v.strip().isascii() and v.strip().isdigit():
        n = int(v.strip())
    else:
        return None
    return n if 0 <= n <= maximum else None


def _tentative_texte(v: Any) -> Optional[str]:
    """Un texte que PostgreSQL accepte : ni NUL ni surrogate isolé, borné."""
    if not isinstance(v, str):
        return None
    propre = v.encode("utf-8", "replace").decode("utf-8").replace("\x00", "")
    return propre[:_TENTATIVE_TEXTE_MAX] or None


def _tentative_sans_nul(obj: Any, profondeur: int = 0) -> Any:
    if profondeur > _TENTATIVE_JSON_PROFONDEUR:
        raise ValueError("couverture trop profonde")
    if isinstance(obj, str):
        return obj.encode("utf-8", "replace").decode("utf-8").replace("\x00", "")
    if isinstance(obj, dict):
        return {_tentative_sans_nul(str(k), profondeur + 1): _tentative_sans_nul(x, profondeur + 1)
                for k, x in obj.items()}
    if isinstance(obj, list):
        return [_tentative_sans_nul(x, profondeur + 1) for x in obj]
    return obj


def _tentative_json(v: Any) -> Optional[str]:
    """La couverture en JSON que `jsonb` accepte — sinon NULL (non attestée). Refus
    ÉTROITS et nommés : NaN, profondeur, taille, type."""
    if not isinstance(v, (dict, list)):
        return None
    try:
        texte = json.dumps(_tentative_sans_nul(v), ensure_ascii=False, allow_nan=False)
    except (ValueError, TypeError, RecursionError):
        return None
    return texte if len(texte) <= _TENTATIVE_JSON_MAX else None


def _tentative_uuid(v: Any) -> Optional[str]:
    try:
        return str(uuid.UUID(str(v)))
    except (ValueError, TypeError, AttributeError):
        return None


def _tentative_exiger(nom: str, v: Any, maximum: int = _TENTATIVE_BIGINT_MAX) -> int:
    """Un identifiant INTERNE (lu sur `runner_jobs`) : invalide = bogue d'appelant, levée."""
    n = _tentative_entier(v, maximum)
    if n is None:
        raise ValueError(f"{nom} invalide pour une tentative : {v!r}")
    return n


def _tentative_resultat(resultat: Optional[Mapping]) -> dict:
    """Ce qu'un résultat déclaré apporte, chaque valeur déjà rendue inoffensive."""
    r = resultat if isinstance(resultat, Mapping) else {}
    lu = {p: _tentative_entier(r.get(p)) for p in POSTES_TENTATIVE}
    lu["usage_couverture"] = _tentative_json(r.get("usage_couverture"))
    lu["stopped"] = _tentative_texte(r.get("stopped"))
    lu["steps"] = _tentative_entier(r.get("steps"), _TENTATIVE_INT_MAX)
    lu["model"] = _tentative_texte(r.get("model"))
    return lu


def _tentative_tarif(ligne: Mapping) -> runner_prix.Tarif:
    return runner_prix.tarifer(ligne.get("provider_family"), ligne.get("model"),
                               {p: ligne.get(p) for p in POSTES_TENTATIVE},
                               atteste=ligne.get("usage_couverture") is not None)


def _tentative_conclusible(outcome: Optional[str]) -> bool:
    """Seule la tentative OUVERTE se conclut. Une conclusion rejouée rend le résultat
    enregistré sans rien recompter ; une tentative perdue passe par le complément."""
    return outcome == TENTATIVE_OUVERTE


def _tentative_fusion(actuel: Mapping, lu: Mapping) -> dict:
    """Le complément TARDIF (contrat §1) : les postes restés NULL, puis la couverture qui
    les atteste et le modèle, chacun seulement s'il est NULL — et RIEN du tout si aucun
    poste n'est rempli. Une valeur déjà posée n'est jamais écrasée."""
    apport = {p: lu[p] for p in POSTES_TENTATIVE
              if actuel.get(p) is None and lu.get(p) is not None}
    if not apport:
        return {}
    for c in ("usage_couverture", "model"):
        if actuel.get(c) is None and lu.get(c) is not None:
            apport[c] = lu[c]
    return apport


# ── les écritures, dans la transaction de l'appelant ──────────────────────────

def ouvrir_tentative(conn, *, job_id: int, attempt_no: int, org_id: int,
                     worker_sub: str, fleet_id: Any = None, run_id: Any = None,
                     trigger_id: Any = None, key_source: Any = None,
                     provider_family: Any = None, model: Any = None) -> dict:
    """Ouvre la tentative `attempt_no` du travail — rend sa ligne, `attempt_id` compris.

    ⚠️ Deux refus STRUCTURELS lèvent, et c'est voulu : la même tentative deux fois
    (clé `(job_id, attempt_no)`), ou une seconde tentative OUVERTE sur le même travail
    (index unique partiel) — la précédente doit d'abord être close `lost`."""
    sub_machine = _tentative_texte(worker_sub)
    if sub_machine is None:
        raise ValueError("une tentative exige l'identité machine qui la prend")
    ligne = {"provider_family": _tentative_texte(provider_family),
             "model": _tentative_texte(model)}
    tarif = _tentative_tarif(ligne)
    return dict(conn.execute(
        f"""
        INSERT INTO runner_job_attempts
               (id, job_id, attempt_no, org_id, fleet_id, run_id, trigger_id,
                worker_sub, key_source, provider_family, model, unpriced_reason)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING {_TENTATIVE_COLONNES_LIGNE}
        """,
        (str(uuid.uuid4()), _tentative_exiger("job_id", job_id),
         _tentative_exiger("attempt_no", attempt_no, _TENTATIVE_INT_MAX),
         _tentative_exiger("org_id", org_id), _tentative_entier(fleet_id),
         _tentative_texte(run_id), _tentative_entier(trigger_id), sub_machine,
         key_source if key_source in PAYEURS_TENTATIVE else None,
         ligne["provider_family"], ligne["model"], tarif.raison),
    ).fetchone())


def clore_tentatives_perdues(conn, job_id: int, *, run_id: Any = None) -> list[dict]:
    """Ferme `lost` la tentative encore ouverte d'un travail (bail mort repris, épave).

    `run_id` = le run où elle a réellement travaillé, s'il n'était pas encore noté."""
    return [dict(r) for r in conn.execute(
        f"""
        UPDATE runner_job_attempts
           SET outcome = 'lost', ended_at = NOW(), run_id = COALESCE(run_id, %s)
         WHERE job_id = %s AND outcome = 'open'
        RETURNING {_TENTATIVE_COLONNES_LIGNE}
        """,
        (_tentative_texte(run_id), _tentative_exiger("job_id", job_id)),
    ).fetchall()]


def refuser_tentative(conn, attempt_id: str, code: str) -> dict:
    """Ferme `failed` une tentative REFUSÉE AU CLAIM par le serveur (contrat §1).

    Aucun travail n'est parti : ses postes valent 0, attestés par le serveur
    (`COUVERTURE_REFUS_SERVEUR`), et ce zéro attesté vaut `U = 0` et un coût nul quelle
    que soit la famille. `stopped` porte le code du refus. ⚠️ Un refus du WORKER après
    service ne passe jamais ici : seul son `complete` déclare ce qui a tourné."""
    zeros = dict.fromkeys(POSTES_TENTATIVE, 0)
    tarif = runner_prix.tarifer(None, None, zeros, atteste=True)
    return dict(conn.execute(
        f"""
        UPDATE runner_job_attempts
           SET outcome = 'failed', ended_at = NOW(), stopped = %s,
               {", ".join(f"{p} = 0" for p in POSTES_TENTATIVE)},
               usage_couverture = %s::jsonb, nano_usd = %s, bareme = %s,
               unpriced_reason = NULL, price_unverified = FALSE
         WHERE id = %s AND outcome = 'open'
        RETURNING {_TENTATIVE_COLONNES_LIGNE}
        """,
        (_tentative_texte(code), json.dumps(COUVERTURE_REFUS_SERVEUR), tarif.nano_usd,
         tarif.bareme, attempt_id),
    ).fetchone())


def tentative_du_travail(conn, attempt_id: Any, *, job_id: int,
                         worker_sub: str) -> Optional[dict]:
    """La tentative `attempt_id` DE CE travail et DE CETTE machine, verrouillée — ou `None`.

    Le socle du fencing : `bind_run`, `extend` et `complete` n'agissent que par elle."""
    ident = _tentative_uuid(attempt_id)
    if ident is None:
        return None
    row = conn.execute(
        "SELECT id::text AS attempt_id, outcome, run_id FROM runner_job_attempts "
        "WHERE id = %s AND job_id = %s AND worker_sub = %s FOR UPDATE",
        (ident, job_id, _tentative_texte(worker_sub))).fetchone()
    return dict(row) if row else None


def lier_run_tentative(conn, attempt_id: str, run_id: Any) -> None:
    """Le run où la tentative COURANTE travaille, noté avec le travail (`bind_run`)."""
    conn.execute("UPDATE runner_job_attempts SET run_id = %s WHERE id = %s AND outcome = 'open'",
                 (_tentative_texte(run_id), attempt_id))


def _tentative_lue(conn, attempt_id: str) -> Optional[dict]:
    row = conn.execute(
        "SELECT outcome, provider_family, model, usage_couverture::text AS usage_couverture, "
        + ", ".join(POSTES_TENTATIVE)
        + " FROM runner_job_attempts WHERE id = %s FOR UPDATE", (attempt_id,)).fetchone()
    return dict(row) if row else None


def _tentative_ligne(conn, attempt_id: str) -> dict:
    return dict(conn.execute(
        f"SELECT {_TENTATIVE_COLONNES_LIGNE} FROM runner_job_attempts WHERE id = %s",
        (attempt_id,)).fetchone())


def conclure_tentative(conn, *, attempt_id: Any, ok: bool,
                       resultat: Optional[Mapping] = None,
                       run_id: Any = None) -> Optional[dict]:
    """Conclut la tentative OUVERTE : `{conclue: True, tentative}`.

    Déjà conclue ou perdue : `{conclue: False, tentative}` tel qu'enregistré, rien n'est
    recompté. Inconnue (ou identifiant illisible) : `None`."""
    ident = _tentative_uuid(attempt_id)
    actuel = _tentative_lue(conn, ident) if ident else None
    if actuel is None:
        return None
    if not _tentative_conclusible(actuel["outcome"]):
        return {"conclue": False, "tentative": _tentative_ligne(conn, ident)}
    lu = _tentative_resultat(resultat)
    lu["model"] = lu["model"] or actuel.get("model")
    tarif = _tentative_tarif({**lu, "provider_family": actuel.get("provider_family")})
    colonnes = POSTES_TENTATIVE + ("stopped", "steps", "model")
    ligne = conn.execute(
        f"""
        UPDATE runner_job_attempts
           SET outcome = %s, ended_at = NOW(), run_id = COALESCE(%s, run_id),
               {", ".join(f"{c} = %s" for c in colonnes)},
               usage_couverture = %s::jsonb,
               nano_usd = %s, bareme = %s, unpriced_reason = %s, price_unverified = %s
         WHERE id = %s
        RETURNING {_TENTATIVE_COLONNES_LIGNE}
        """,
        (TENTATIVE_REUSSIE if ok else TENTATIVE_ECHOUEE, _tentative_texte(run_id),
         *(lu[c] for c in colonnes), lu["usage_couverture"],
         tarif.nano_usd, tarif.bareme, tarif.raison, tarif.prix_non_verifie, ident),
    ).fetchone()
    return {"conclue": True, "tentative": dict(ligne)}


def completer_tentative_perdue(conn, *, attempt_id: Any,
                               resultat: Optional[Mapping]) -> Optional[dict]:
    """Le `complete` TARDIF d'une tentative `lost`, en UNE écriture : ses postes restés
    NULL, leur couverture et le modèle se remplissent, son état ne change pas.
    `{usage_enregistre, tentative}` — ou `None` si inconnue.

    Sur une tentative qui n'est pas `lost`, ou si aucun poste n'est rempli, rien ne
    s'écrit : `usage_enregistre: False`."""
    ident = _tentative_uuid(attempt_id)
    actuel = _tentative_lue(conn, ident) if ident else None
    if actuel is None:
        return None
    apport = (_tentative_fusion(actuel, _tentative_resultat(resultat))
              if actuel["outcome"] == TENTATIVE_PERDUE else {})
    if not apport:
        return {"usage_enregistre": False, "tentative": _tentative_ligne(conn, ident)}
    tarif = _tentative_tarif({**actuel, **apport})
    noms = list(apport)
    sets = ", ".join(f"{c} = %s::jsonb" if c == "usage_couverture" else f"{c} = %s"
                     for c in noms)
    ligne = conn.execute(
        f"""
        UPDATE runner_job_attempts
           SET {sets}, nano_usd = %s, bareme = %s, unpriced_reason = %s,
               price_unverified = %s
         WHERE id = %s
        RETURNING {_TENTATIVE_COLONNES_LIGNE}
        """,
        (*(apport[c] for c in noms), tarif.nano_usd, tarif.bareme, tarif.raison,
         tarif.prix_non_verifie, ident),
    ).fetchone()
    return {"usage_enregistre": True, "tentative": dict(ligne)}


# ── les projections (lecture seule, org-scopées) ──────────────────────────────

def _tentative_portee(op: str, org_id: int, *, run_id=None, trigger_id=None,
                      fleet_id=None, jours: int = 30) -> tuple[str, list, str, list]:
    """`(filtre, paramètres, ancre, paramètres de l'ancre)` d'un regroupement. L'ANCRE est
    l'instant depuis lequel il a besoin d'être mesuré : une ancre antérieure au premier
    fait enregistré = un total qui ne peut pas être complet."""
    fenetre = "NOW() - make_interval(days => %s)"
    if op == "run":
        return ("run_id = %s", [run_id],
                "(SELECT started_at FROM runs WHERE run_id = %s AND org_id = %s)",
                [run_id, org_id])
    if op == "fleet":
        return ("fleet_id = %s", [fleet_id],
                "(SELECT created_at FROM runner_fleets WHERE id = %s AND org_id = %s)",
                [fleet_id, org_id])
    if op == "agent":
        return (f"trigger_id = %s AND claimed_at >= {fenetre}", [trigger_id, jours],
                fenetre, [jours])
    if op == "org":
        return f"claimed_at >= {fenetre}", [jours], fenetre, [jours]
    raise ValueError(f"regroupement inconnu : {op!r}")


def _tentative_agregats() -> str:
    """La liste SELECT commune au total et à chaque part de la ventilation."""
    def somme(expr: str, filtre: str, nom: str) -> str:
        return f"(SUM({expr}) FILTER (WHERE {filtre}))::bigint AS {nom}"

    def compte(filtre: str, nom: str) -> str:
        return f"COUNT(*) FILTER (WHERE {filtre})::int AS {nom}"

    budget = runner_prix.unite_budget_sql()
    morceaux = [
        "COUNT(*)::int AS attempts", "COUNT(DISTINCT run_id)::int AS runs",
        compte("outcome = 'open'", "open"), compte("outcome = 'lost'", "lost"),
        compte(_TENTATIVE_ATTESTEE, "attested"),
        compte(_TENTATIVE_NON_ATTESTEE, "not_attested"),
        compte(_TENTATIVE_SANS_MESURE, "unmeasured"),
        compte(f"{_TENTATIVE_ATTESTEE} AND nano_usd IS NULL", "unpriced"),
        compte(f"{_TENTATIVE_ATTESTEE} AND price_unverified", "price_unverified_attempts"),
        compte(f"{_TENTATIVE_NON_ATTESTEE} AND price_unverified", "na_price_unverified_attempts"),
        somme("nano_usd", _TENTATIVE_ATTESTEE, "nano_usd"),
        somme(budget, _TENTATIVE_ATTESTEE, "budget_units"),
        compte(f"{_TENTATIVE_ATTESTEE} AND {budget} IS NULL", "budget_units_unknown"),
        somme("nano_usd", _TENTATIVE_NON_ATTESTEE, "na_nano_usd"),
    ]
    for p in POSTES_TENTATIVE:
        morceaux.append(somme(p, _TENTATIVE_ATTESTEE, p))
        morceaux.append(somme(p, _TENTATIVE_NON_ATTESTEE, f"na_{p}"))
    for raison in runner_prix.RAISONS:
        morceaux.append(compte(f"{_TENTATIVE_ATTESTEE} AND unpriced_reason = '{raison}'",
                               f"raison_{raison}"))
    return ",\n       ".join(morceaux)


def _tentative_mesure(conn, ancre: str, params: list) -> dict:
    row = conn.execute(
        f"""
        WITH m AS (SELECT MIN(claimed_at) AS debut FROM runner_job_attempts)
        SELECT m.debut AS measured_since,
               CASE WHEN m.debut IS NULL THEN TRUE
                    WHEN {ancre} IS NULL THEN FALSE
                    ELSE {ancre} < m.debut END AS before_measurement
          FROM m
        """, params + params).fetchone()
    return dict(row)


def cout_des_tentatives(org_id: int, op: str, **portee) -> dict:
    """Le total d'un regroupement (Coûts et Consommation), sa mesure, et ce qu'il avoue."""
    filtre, params, ancre, params_ancre = _tentative_portee(op, org_id, **portee)
    with _connect() as conn:
        mesure = _tentative_mesure(conn, ancre, params_ancre)
        agregat = conn.execute(
            f"SELECT {_tentative_agregats()} FROM runner_job_attempts "
            f"WHERE {_TENTATIVE_FILTRE_ORG} AND {filtre}",
            [org_id, *params]).fetchone()
    return {**mesure, "agregat": dict(agregat)}


def ventilation_des_tentatives(org_id: int, op: str, par: str, **portee) -> list[dict]:
    """Les parts d'un regroupement, par une clé de la table FERMÉE."""
    if par not in VENTILATIONS_TENTATIVES:
        raise ValueError(f"ventilation inconnue : {par!r}")
    cle = _TENTATIVE_SOURCE_SQL if par == "source" else par
    filtre, params, _, _ = _tentative_portee(op, org_id, **portee)
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT {cle} AS key, {_tentative_agregats()} FROM runner_job_attempts "
            f"WHERE {_TENTATIVE_FILTRE_ORG} AND {filtre} GROUP BY 1 ORDER BY 1",
            [org_id, *params]).fetchall()
    return [dict(r) for r in rows]


def lignes_des_tentatives(org_id: int, run_id: str) -> tuple[list[dict], bool]:
    """Les tentatives d'un run, une par ligne, et si la page a été TRONQUÉE."""
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT {_TENTATIVE_COLONNES_LIGNE} FROM runner_job_attempts "
            f"WHERE {_TENTATIVE_FILTRE_ORG} AND run_id = %s "
            "ORDER BY job_id, attempt_no LIMIT %s",
            (org_id, run_id, LIGNES_TENTATIVES_MAX + 1)).fetchall()
    lignes = [dict(r) for r in rows]
    return lignes[:LIGNES_TENTATIVES_MAX], len(lignes) > LIGNES_TENTATIVES_MAX
