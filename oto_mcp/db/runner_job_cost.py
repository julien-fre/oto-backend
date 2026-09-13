"""Ce qu'un travail a COÛTÉ — écrit une fois, gardé pour toujours.

Une ligne par TENTATIVE de travail. Le travail est l'unité atomique d'exécution
et la seule chose qui rende un compte de jetons ; « run », « agent » et
« passage » n'en sont que des REGROUPEMENTS :

    run     → les travaux qui partagent `run_id`  (un `continue` en ajoute un)
    agent   → les travaux qui partagent `trigger_id`
    passage → les travaux qui partagent `fleet_id`
    org     → les travaux qui partagent `org_id`  (la ligne qu'on facturera)

⚠️ **Un run n'est pas UN travail.** Un `continue` se rattache à un `run_id`
existant et y ajoute des tours. Lire un seul travail et l'appeler « le coût du
run » sous-compte exactement les déroulés longs — ceux qu'on veut voir.

⚠️ **La table est une table de FAITS, pas un compteur.** Pour mesurer, c'est la
bonne forme : elle se ré-agrège ensuite par run, agent, passage, modèle ou
payeur, et une facturation a besoin des lignes, pas d'un total. Les compteurs
tenus à l'écriture viendront avec les PLAFONDS, où le chemin chaud interdit une
somme — ce sont deux chantiers, et celui-ci ne refuse rien.

⚠️ **NULL n'est pas ZÉRO.** Des jetons `NULL` disent « non mesuré » (un travail
mort sans rien rendre) ; `0` dit « mesuré, et nul ». Toute lecture rend donc
`incomplet` : sans lui, un total amputé d'un travail perdu se présenterait comme
un total.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from ._conn import _connect

#: Ce qu'une tentative est devenue. `lost` = le bail a expiré sans conclusion :
#: les jetons ont été dépensés chez le fournisseur et personne ne les a rendus.
DONE, FAILED, LOST = "done", "failed", "lost"

#: D'où vient le travail — même vocabulaire que `runner_jobs._SOURCES`, plus
#: `hook`, que le webhook a rendu distinct d'un `scheduled` (les deux portent un
#: `trigger_id` ; seul le premier porte `payload.hook`).
MANUAL, SCHEDULED, HOOK, BATCH = "manual", "scheduled", "hook", "batch"

_COLS = ("job_id, attempt, org_id, sub, run_id, trigger_id, fleet_id, source, "
         "modele, famille, key_source, outcome, input_tokens, output_tokens, "
         "cache_write_tokens, cache_read_tokens, nano_usd, bareme, steps, "
         "finished_at")


def source_du_travail(payload: Optional[dict], fleet_id: Optional[int]) -> str:
    """D'où vient ce travail, dérivé de ce qu'il porte déjà.

    L'ordre compte : une flotte l'emporte (un travail de passage porte aussi son
    déclencheur dans certains montages), puis le webhook, puis le programmé.
    """
    if fleet_id is not None:
        return BATCH
    p = payload or {}
    if p.get("trigger_id") is None:
        return MANUAL
    return HOOK if p.get("hook") else SCHEDULED


def enregistrer(conn, *, job_id: int, attempt: int, org_id: int,
                sub: Optional[str], run_id: Optional[str],
                payload: Optional[dict], fleet_id: Optional[int],
                key_source: Optional[str], outcome: str,
                resultat: Optional[dict]) -> bool:
    """Écrit le coût d'UNE tentative. Rend True si la ligne est neuve.

    ⚠️ Prend la connexion de l'appelant : la ligne de coût et le changement
    d'état du travail sont posés dans LA MÊME transaction, ou pas du tout. Une
    conclusion sans son coût, c'est de la dépense hors livre ; un coût sans sa
    conclusion, c'est un montant qu'aucun travail n'explique.

    ⚠️ `ON CONFLICT DO NOTHING` sur `(job_id, attempt)` : une conclusion rejouée
    (worker qui retente son appel, redélivrance) ne doit pas doubler le compte.
    Une clé primaire à numéro de série aurait accepté le doublon en silence.

    ⚠️ Ne LÈVE JAMAIS pour une raison de mesure. Le prix d'un modèle inconnu est
    NULL, pas une erreur : refuser d'écrire la ligne perdrait aussi les jetons,
    qui sont le fait, alors que le montant n'en est qu'une lecture.
    """
    from .. import runner_models, runner_prix

    r = resultat or {}
    modele = r.get("model") or (payload or {}).get("model")
    # Absent du résultat comme de la charge : le travail a tourné sur le modèle
    # de l'environnement du worker, que nous ne connaissons pas. NULL, pas un défaut.
    entree = r.get("usage_input")
    sortie = r.get("usage_output")
    ecriture = r.get("usage_cache_write")
    lecture = r.get("usage_cache_read")
    nano, bareme = runner_prix.cout_nano_usd(
        modele, entree=entree, sortie=sortie,
        ecriture_cache=ecriture, lecture_cache=lecture)
    cur = conn.execute(
        f"""
        INSERT INTO runner_job_cost ({_COLS})
             VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                     %s, %s, %s, %s, %s, %s, %s, NOW())
        ON CONFLICT (job_id, attempt) DO NOTHING
        """,
        (job_id, int(attempt), org_id, sub, run_id,
         (payload or {}).get("trigger_id"), fleet_id,
         source_du_travail(payload, fleet_id),
         modele, runner_models.famille(modele),
         key_source or "platform", outcome,
         entree, sortie, ecriture, lecture, nano, bareme, r.get("steps")),
    )
    return bool(cur.rowcount)


def _somme(ou: str, valeurs: tuple, org_id: int) -> dict:
    """Le total d'un regroupement, et s'il est COMPLET.

    `incomplet` est vrai dès qu'une tentative du lot n'a pas rendu ses jetons :
    le total est alors un PLANCHER, et l'écran doit le dire. Un total qui tait un
    travail perdu vaut moins que pas de total.
    """
    with _connect() as conn:
        row = conn.execute(
            f"""
            SELECT COUNT(*)::int                                   AS tentatives,
                   COUNT(DISTINCT run_id)::int                     AS runs,
                   COALESCE(SUM(input_tokens), 0)::bigint          AS input_tokens,
                   COALESCE(SUM(output_tokens), 0)::bigint         AS output_tokens,
                   COALESCE(SUM(cache_write_tokens), 0)::bigint    AS cache_write_tokens,
                   COALESCE(SUM(cache_read_tokens), 0)::bigint     AS cache_read_tokens,
                   COALESCE(SUM(nano_usd), 0)::bigint              AS nano_usd,
                   -- ⚠️ Deux incomplétudes distinctes : des jetons non mesurés,
                   -- et des jetons mesurés qu'aucun barème ne sait tarifer. La
                   -- première ampute le compte, la seconde le seul montant.
                   bool_or(input_tokens IS NULL)                   AS jetons_manquants,
                   bool_or(input_tokens IS NOT NULL
                           AND nano_usd IS NULL)                   AS non_tarifes
              FROM runner_job_cost
             WHERE org_id = %s AND {ou}
            """,
            (org_id, *valeurs),
        ).fetchone()
    d = dict(row) if row else {}
    d["incomplet"] = bool(d.get("jetons_manquants") or d.get("non_tarifes"))
    return d


def cout_du_run(run_id: str, org_id: int) -> dict:
    """Ce qu'un run a coûté — TOUS ses travaux, `continue` compris."""
    return _somme("run_id = %s", (run_id,), org_id)


def cout_de_l_agent(trigger_id: int, org_id: int, jours: int = 30) -> dict:
    """Ce qu'un agent a coûté sur une fenêtre (défaut : 30 jours)."""
    return _somme(
        "trigger_id = %s AND finished_at > NOW() - make_interval(days => %s)",
        (trigger_id, max(1, int(jours))), org_id)


def cout_du_passage(fleet_id: int, org_id: int) -> dict:
    """Ce qu'un passage a coûté — sur toute sa vie, jamais une fenêtre : un
    passage est un objet fini, pas un flux."""
    return _somme("fleet_id = %s", (fleet_id,), org_id)


def cout_de_l_org(org_id: int, jours: int = 30) -> dict:
    return _somme("finished_at > NOW() - make_interval(days => %s)",
                  (max(1, int(jours)),), org_id)


def ventilation(org_id: int, jours: int = 30, par: str = "modele") -> list[dict]:
    """Le détail d'une org sur une fenêtre, par `modele`, `source` ou
    `key_source` — de quoi répondre « où part l'argent ? » sans exporter la table.

    ⚠️ `par` n'est PAS interpolé depuis l'appelant : il est résolu dans une table
    fermée. Une colonne libre ici serait une injection SQL.
    """
    colonne = {"modele": "modele", "source": "source",
               "key_source": "key_source", "famille": "famille"}.get(par)
    if colonne is None:
        raise ValueError(f"ventilation inconnue : {par!r}")
    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT {colonne} AS cle,
                   COUNT(*)::int                          AS tentatives,
                   COALESCE(SUM(nano_usd), 0)::bigint     AS nano_usd,
                   COALESCE(SUM(input_tokens), 0)::bigint AS input_tokens,
                   COALESCE(SUM(output_tokens), 0)::bigint AS output_tokens,
                   bool_or(input_tokens IS NULL)          AS jetons_manquants
              FROM runner_job_cost
             WHERE org_id = %s AND finished_at > NOW() - make_interval(days => %s)
             GROUP BY 1
             ORDER BY nano_usd DESC
            """,
            (org_id, max(1, int(jours))),
        ).fetchall()
    return [dict(r) for r in rows]


def lignes_du_run(run_id: str, org_id: int, limit: int = 100) -> list[dict]:
    """Les tentatives d'un run, une par ligne — ce qu'un écran de détail montre,
    et ce qu'une facture citera."""
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT {_COLS} FROM runner_job_cost "
            "WHERE run_id = %s AND org_id = %s ORDER BY job_id, attempt LIMIT %s",
            (run_id, org_id, max(1, min(int(limit), 500))),
        ).fetchall()
    return [dict(r) for r in rows]


def marquer_paye_par_l_org(job_id: int) -> bool:
    """Note que la clé de l'ORG a été servie pour ce travail.

    Appelé par la capacité au moment exact de la remise (`_avec_cle`), c'est-à-dire
    le seul instant où « qui paie » est une certitude. Le défaut posé au claim est
    `platform` ; ceci le corrige pour les travaux qui reçoivent vraiment la clé.

    ⚠️ Best-effort : un défaut d'étiquetage ne doit pas empêcher un travail de
    partir. Le pire cas est une dépense attribuée à la plateforme au lieu de
    l'org — visible, et corrigible — jamais un agent qui ne tourne pas.
    """
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE runner_jobs SET key_source = 'org' WHERE id = %s", (job_id,))
        return bool(cur.rowcount)
