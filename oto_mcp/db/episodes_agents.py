"""Les ÉPISODES de panne d'un agent hébergé — relevé, ouverture, guérison, réparations.

Un épisode = les travaux morts (`failed`) d'un agent ALLUMÉ depuis son dernier travail
réussi (`done`), donc consécutifs — la même définition que l'alerte au titulaire
(`db/alertes_agents.py`), mais dès le PREMIER travail mort : soigner commence avant
d'annoncer. La ligne `agent_episodes` est la source unique de la panne : la boucle
`medecin_agents` l'ouvre et la range, l'agent médecin (`oto_admin_agent_repair`) la lit
et la soigne, l'alerte au titulaire la consulte pour se taire quand la panne est la
nôtre.

⚠️ Une réparation s'écrit sur l'épisode (`reparations`, ajout seul) AVANT d'être
considérée comme faite par l'appelant : un travail relancé sans trace serait un second
mécanisme invisible sur le même objet — exactement ce que la décision du bail refuse.
"""
from __future__ import annotations

import json
from datetime import timedelta
from typing import Any, Optional

from ._conn import _connect
from .alertes_agents import FENETRE_JOURS, MOTIF_TETE, _DU_DECLENCHEUR

#: Les statuts d'un épisode qui n'est pas clos — lus par l'index unique partiel.
VIVANTS = ("ouvert", "en_soin", "escalade")

#: La classe d'un agent qui conclut `done` APRÈS un refus de capacité (ci-dessous) : la
#: panne qui ne meurt pas. Un agent refusé d'une procédure ou d'un outil dont il avait
#: besoin peut faire la partie qu'il lui reste et rendre « fait » — vécu : une chaîne de
#: procédures coupée au premier maillon, cinq jours de runs verts qui ne faisaient que le
#: premier tiers du travail. Aucun travail mort, donc aucun épisode, aucune alerte.
FINI_MALGRE_REFUS = "fini_malgre_refus"

#: Les refus de CAPACITÉ : ce que le travail n'a pas le droit de faire, et que l'agent ne
#: peut pas lever lui-même (seule une personne repointe la procédure ou complète les
#: outils). Lus sur le journal d'appels du run (`tool_calls.run_id`) sous deux formes :
#: - un refus LEVÉ (`ok` faux) : le journal ne garde que le message (`error_kind` est la
#:   taxonomie générique, `invalid_input`), d'où un fragment de chaque message — testé
#:   contre le vrai message (`tests/test_fins_malgre_refus.py`), jamais recopié ;
#: - un refus RENDU en donnée (le reçu d'une recette refusée avant tout appel) : sa forme
#:   `refused(<code>)` (`calllog.forme_servie`).
REFUS_DE_CAPACITE_TEXTES = (
    # procedure_outside_job : la queue commune à tous ses refus, et la tête de celui qui
    # nomme un slug hors du travail — le journal tronque à `calllog.MAX_ERROR_CHARS`, et
    # deux slugs très longs pourraient pousser la queue au-delà.
    "%A hosted agent reads only its own job's procedure%",
    "%is neither this job's procedure%",
    "%is not in this hosted agent's tool list%",             # tool_not_in_job
)
REFUS_DE_CAPACITE_FORMES = ("refused(procedure_outside_job)", "refused(tool_not_in_job)")

#: La fenêtre du relevé : les travaux réussis conclus depuis six heures. Assez pour qu'une
#: boucle arrêtée une heure ne perde rien, assez court pour rester une lecture d'index.
FENETRE_FINS_H = 6

#: Le run `j.run_id` a reçu au moins un refus de capacité.
_RUN_REFUSE = """EXISTS (
    SELECT 1 FROM tool_calls c
     WHERE c.run_id = j.run_id
       AND ((NOT c.ok AND c.error LIKE ANY(%(textes)s))
            OR c.result_shape = ANY(%(formes)s)))"""


def _params_refus() -> dict:
    return {"textes": list(REFUS_DE_CAPACITE_TEXTES),
            "formes": list(REFUS_DE_CAPACITE_FORMES)}

_COLONNES = ("id, org_id, trigger_id, classe, cote, statut, motif, dernier_job_id, "
             "travaux_morts, reparations, diagnostic, classe_manuelle, ouvert_at, vu_at, "
             "clos_at, resolution")


def pannes_en_cours() -> list[dict[str, Any]]:
    """Les agents ALLUMÉS qui ont au moins un travail mort depuis leur dernier succès,
    dans la fenêtre de l'alerte. Une ligne par agent : org, libellé, modèle, le compte,
    le DERNIER travail mort (id, motif, genre, charge, identité) — de quoi le classer et
    le relancer sans relire."""
    with _connect() as conn:
        return [dict(r) for r in conn.execute(
            f"""
            SELECT t.id AS trigger_id, t.org_id, t.model,
                   COALESCE(NULLIF(t.label, ''), t.procedure) AS libelle,
                   f.travaux, d.id AS job_id, LEFT(d.last_error, %(tete)s) AS motif,
                   d.kind, d.payload, d.sub, d.finished_at AS dernier
              FROM runner_triggers t
              CROSS JOIN LATERAL (
                    SELECT MAX(j.finished_at) AS dernier_succes
                      FROM runner_jobs j
                     WHERE {_DU_DECLENCHEUR} AND j.status = 'done') s
              CROSS JOIN LATERAL (
                    SELECT COUNT(*)::int AS travaux
                      FROM runner_jobs j
                     WHERE {_DU_DECLENCHEUR} AND j.status = 'failed'
                       AND j.finished_at > NOW() - make_interval(days => %(fenetre)s)
                       AND (s.dernier_succes IS NULL
                            OR j.finished_at > s.dernier_succes)) f
              CROSS JOIN LATERAL (
                    SELECT j.id, j.last_error, j.kind, j.payload, j.sub, j.finished_at
                      FROM runner_jobs j
                     WHERE {_DU_DECLENCHEUR} AND j.status = 'failed'
                       AND j.finished_at > NOW() - make_interval(days => %(fenetre)s)
                     ORDER BY j.finished_at DESC LIMIT 1) d
             WHERE t.enabled AND f.travaux >= 1
             ORDER BY t.org_id, t.id
            """,
            {"fenetre": FENETRE_JOURS, "tete": MOTIF_TETE},
        ).fetchall()]


def ouvrir_ou_rafraichir(*, org_id: int, trigger_id: int, classe: str, cote: str,
                         motif: Optional[str], job_id: Optional[int],
                         travaux: int) -> dict[str, Any]:
    """L'épisode vivant de l'agent, ouvert s'il n'existe pas, rafraîchi sinon (dernier
    travail, motif, compte, `vu_at`). La classe suit le DERNIER motif : une panne qui
    change de cause change de remède — sauf une classe posée à la main (`poser`,
    `classe_manuelle`) : un diagnostic d'humain ou de l'agent médecin n'est pas
    défait au passage suivant par la regex qu'il corrigeait. Rend la ligne."""
    with _connect() as conn:
        return dict(conn.execute(
            f"""
            INSERT INTO agent_episodes (org_id, trigger_id, classe, cote, motif,
                                        dernier_job_id, travaux_morts)
            VALUES (%(org)s, %(trigger)s, %(classe)s, %(cote)s, %(motif)s, %(job)s,
                    %(travaux)s)
            ON CONFLICT (trigger_id) WHERE statut <> 'clos' DO UPDATE
               SET classe = CASE WHEN agent_episodes.classe_manuelle
                                 THEN agent_episodes.classe ELSE EXCLUDED.classe END,
                   cote = CASE WHEN agent_episodes.classe_manuelle
                               THEN agent_episodes.cote ELSE EXCLUDED.cote END,
                   motif = EXCLUDED.motif, dernier_job_id = EXCLUDED.dernier_job_id,
                   travaux_morts = EXCLUDED.travaux_morts, vu_at = NOW()
            RETURNING {_COLONNES}
            """,
            {"org": int(org_id), "trigger": int(trigger_id), "classe": classe,
             "cote": cote, "motif": motif, "job": job_id, "travaux": int(travaux)},
        ).fetchone())


def fins_malgre_refus() -> list[dict[str, Any]]:
    """Les agents ALLUMÉS dont le DERNIER travail réussi de la fenêtre a reçu un refus de
    capacité. Une ligne par agent : org, libellé, ce dernier travail (id, run, date), le
    compte des travaux réussis-mais-refusés de la fenêtre, et le premier refus de ce run
    (tronqué comme un motif). Le dernier seulement : un agent dont le travail suivant
    passe sans refus est guéri, son épisode se clôt (`clore_les_gueris`) et ne se rouvre
    pas sur l'historique."""
    with _connect() as conn:
        return [dict(r) for r in conn.execute(
            f"""
            SELECT t.id AS trigger_id, t.org_id,
                   COALESCE(NULLIF(t.label, ''), t.procedure) AS libelle,
                   d.id AS job_id, d.run_id, d.finished_at AS dernier, n.travaux,
                   (SELECT LEFT(COALESCE(c.error, c.result_shape), %(tete)s)
                      FROM tool_calls c
                     WHERE c.run_id = d.run_id
                       AND ((NOT c.ok AND c.error LIKE ANY(%(textes)s))
                            OR c.result_shape = ANY(%(formes)s))
                     ORDER BY c.created_at LIMIT 1) AS motif
              FROM runner_triggers t
              CROSS JOIN LATERAL (
                    SELECT j.id, j.run_id, j.finished_at
                      FROM runner_jobs j
                     WHERE {_DU_DECLENCHEUR} AND j.status = 'done'
                       AND j.run_id IS NOT NULL
                       AND j.finished_at > NOW() - make_interval(hours => %(fenetre)s)
                     ORDER BY j.finished_at DESC LIMIT 1) d
              CROSS JOIN LATERAL (
                    SELECT COUNT(*)::int AS travaux
                      FROM runner_jobs j
                     WHERE {_DU_DECLENCHEUR} AND j.status = 'done'
                       AND j.run_id IS NOT NULL
                       AND j.finished_at > NOW() - make_interval(hours => %(fenetre)s)
                       AND {_RUN_REFUSE}) n
             WHERE t.enabled AND n.travaux >= 1
               AND EXISTS (SELECT 1 FROM tool_calls c
                            WHERE c.run_id = d.run_id
                              AND ((NOT c.ok AND c.error LIKE ANY(%(textes)s))
                                   OR c.result_shape = ANY(%(formes)s)))
             ORDER BY t.org_id, t.id
            """,
            {"fenetre": FENETRE_FINS_H, "tete": MOTIF_TETE, **_params_refus()},
        ).fetchall()]


def ouvrir_fin_malgre_refus(*, org_id: int, trigger_id: int, motif: Optional[str],
                            job_id: int, travaux: int) -> Optional[dict[str, Any]]:
    """L'épisode `fini_malgre_refus` de l'agent, ouvert ou rafraîchi. Côté `inconnu` :
    le refus dit QUOI manque, pas QUI l'a retiré (une procédure mal pointée par le
    titulaire, ou un changement de la plateforme — le cas vécu). Il reste `ouvert` pour
    l'agent médecin, jamais relancé : refaire un run qui a agi referait ses effets.

    ⚠️ Un épisode vivant d'une AUTRE classe (des travaux morts) garde la main : la
    ligne n'est pas touchée et la fonction rend None. Rend la ligne et `neuf` (vrai à
    l'ouverture) : on n'alerte qu'une fois par épisode. `travaux_morts` porte ici le
    compte des travaux réussis-mais-refusés."""
    with _connect() as conn:
        row = conn.execute(
            f"""
            INSERT INTO agent_episodes (org_id, trigger_id, classe, cote, motif,
                                        dernier_job_id, travaux_morts)
            VALUES (%(org)s, %(trigger)s, %(classe)s, 'inconnu', %(motif)s, %(job)s,
                    %(travaux)s)
            ON CONFLICT (trigger_id) WHERE statut <> 'clos' DO UPDATE
               SET motif = EXCLUDED.motif, dernier_job_id = EXCLUDED.dernier_job_id,
                   travaux_morts = EXCLUDED.travaux_morts, vu_at = NOW()
             WHERE agent_episodes.classe = %(classe)s
            RETURNING {_COLONNES}, (xmax = 0) AS neuf
            """,
            {"org": int(org_id), "trigger": int(trigger_id), "classe": FINI_MALGRE_REFUS,
             "motif": motif, "job": int(job_id), "travaux": int(travaux)},
        ).fetchone()
    return dict(row) if row else None


def clore_les_gueris() -> list[dict[str, Any]]:
    """Clôt les épisodes vivants dont l'agent a réussi un travail APRÈS l'ouverture
    (`gueri`), ou qui a été éteint ou supprimé (`agent_eteint`). Rend les épisodes clos —
    ceux qui avaient une réparation disent qu'elle a marché.

    ⚠️ Un épisode `fini_malgre_refus` ne guérit que d'un succès SANS refus de capacité :
    ses travaux réussissent tous, c'est justement la panne."""
    with _connect() as conn:
        return [dict(r) for r in conn.execute(
            f"""
            UPDATE agent_episodes e
               SET statut = 'clos', clos_at = NOW(),
                   resolution = CASE WHEN t.id IS NULL OR NOT t.enabled
                                     THEN 'agent_eteint' ELSE 'gueri' END
              FROM agent_episodes e2
              LEFT JOIN runner_triggers t ON t.id = e2.trigger_id
             WHERE e.id = e2.id AND e.statut <> 'clos'
               AND (t.id IS NULL OR NOT t.enabled OR EXISTS (
                     SELECT 1 FROM runner_jobs j
                      WHERE j.org_id = t.org_id
                        AND j.payload->>'trigger_id' ~ '^[0-9]+$'
                        AND j.payload->>'trigger_id' = t.id::text
                        AND j.status = 'done' AND j.finished_at > e.ouvert_at
                        AND (e.classe <> %(fini)s OR (j.run_id IS NOT NULL
                                                      AND NOT {_RUN_REFUSE}))))
            RETURNING {", ".join("e." + c.strip() for c in _COLONNES.split(","))}
            """, {"fini": FINI_MALGRE_REFUS, **_params_refus()}).fetchall()]


def noter_reparation(episode_id: int, *, action: str, acteur: str,
                     job_id: Optional[int] = None, detail: Optional[str] = None,
                     maximum: Optional[int] = None) -> Optional[dict[str, Any]]:
    """Ajoute un geste à l'épisode et le passe `en_soin` — sous garde : un épisode clos
    ne reçoit rien, `maximum` borne le nombre de gestes de cette `action`, et un même
    `job_id` ne reçoit qu'UN geste de chaque `action` (une relance par travail mort).
    La garde est dans le WHERE de l'UPDATE, qui relit `reparations` : deux processus
    sur le même épisode, le second attend le verrou de ligne, relit la ligne écrite par
    le premier, et refuse. Rend la ligne, ou None si la garde a refusé — l'appelant ne
    fait alors PAS le geste."""
    geste = {"action": action, "acteur": acteur, "job_id": job_id,
             "detail": (detail or "")[:500] or None}
    with _connect() as conn:
        row = conn.execute(
            f"""
            UPDATE agent_episodes
               SET reparations = reparations || jsonb_build_array(
                       %(geste)s::jsonb || jsonb_build_object(
                           'at', to_char(NOW() AT TIME ZONE 'UTC',
                                         'YYYY-MM-DD"T"HH24:MI:SSZ'))),
                   statut = 'en_soin', vu_at = NOW()
             WHERE id = %(id)s AND statut <> 'clos'
               AND (%(max)s::int IS NULL OR (
                     SELECT COUNT(*) FROM jsonb_array_elements(reparations) r
                      WHERE r->>'action' = %(action)s) < %(max)s::int)
               AND (%(job)s::bigint IS NULL OR NOT EXISTS (
                     SELECT 1 FROM jsonb_array_elements(reparations) r
                      WHERE r->>'action' = %(action)s
                        AND r->>'job_id' = (%(job)s::bigint)::text))
            RETURNING {_COLONNES}
            """,
            {"geste": json.dumps(geste, ensure_ascii=False), "id": int(episode_id),
             "max": maximum, "action": action, "job": job_id},
        ).fetchone()
    return dict(row) if row else None


def poser(episode_id: int, *, statut: Optional[str] = None,
          diagnostic: Optional[str] = None, classe: Optional[str] = None,
          cote: Optional[str] = None,
          resolution: Optional[str] = None) -> Optional[dict[str, Any]]:
    """Le geste d'un humain ou de l'agent médecin sur un épisode : son diagnostic, sa
    classe corrigée, l'escalade ou la clôture. Un champ absent ne change pas.

    Une `classe` ou un `cote` posé ici marque l'épisode `classe_manuelle` : le passage
    suivant de la boucle ne le réécrit plus (`ouvrir_ou_rafraichir`)."""
    with _connect() as conn:
        row = conn.execute(
            f"""
            UPDATE agent_episodes
               SET statut = COALESCE(%(statut)s, statut),
                   diagnostic = COALESCE(%(diag)s, diagnostic),
                   classe = COALESCE(%(classe)s, classe),
                   cote = COALESCE(%(cote)s, cote),
                   classe_manuelle = classe_manuelle
                       OR %(classe)s::text IS NOT NULL OR %(cote)s::text IS NOT NULL,
                   resolution = COALESCE(%(resolution)s, resolution),
                   clos_at = CASE WHEN %(statut)s = 'clos' THEN NOW() ELSE clos_at END,
                   vu_at = NOW()
             WHERE id = %(id)s
            RETURNING {_COLONNES}
            """,
            {"statut": statut, "diag": diagnostic, "classe": classe, "cote": cote,
             "resolution": resolution, "id": int(episode_id)},
        ).fetchone()
    return dict(row) if row else None


def lister(*, statut: Optional[str] = None, org_id: Optional[int] = None,
           cote: Optional[str] = None, limit: int = 50) -> list[dict[str, Any]]:
    """Les épisodes, les plus récemment vus d'abord. `statut='vivants'` = tout sauf
    `clos`."""
    filtres, params = [], {"limit": max(1, min(int(limit), 200))}
    if statut == "vivants":
        filtres.append("statut <> 'clos'")
    elif statut:
        filtres.append("statut = %(statut)s")
        params["statut"] = statut
    if org_id is not None:
        filtres.append("org_id = %(org)s")
        params["org"] = int(org_id)
    if cote:
        filtres.append("cote = %(cote)s")
        params["cote"] = cote
    where = f"WHERE {' AND '.join(filtres)}" if filtres else ""
    with _connect() as conn:
        return [dict(r) for r in conn.execute(
            f"SELECT {_COLONNES} FROM agent_episodes {where} "
            "ORDER BY vu_at DESC LIMIT %(limit)s", params).fetchall()]


def lire(episode_id: int) -> Optional[dict[str, Any]]:
    with _connect() as conn:
        row = conn.execute(f"SELECT {_COLONNES} FROM agent_episodes WHERE id = %s",
                           (int(episode_id),)).fetchone()
    return dict(row) if row else None


def agents_soignes_par_nous(trigger_ids: list, *, vus_depuis: timedelta) -> set[int]:
    """Parmi ces agents, ceux dont l'épisode vivant est du côté PLATEFORME, tant qu'il n'est pas
    clos — escaladé compris : une panne plateforme qui résiste s'escalade chez NOUS. Leur
    titulaire ne peut rien y faire, l'alerte se tait pour eux.

    ⚠️ Seulement un épisode VU depuis moins de `vus_depuis` (`medecin_agents.FRAICHEUR`,
    quelques passages de la boucle) : un épisode que plus personne ne rafraîchit — boucle
    éteinte, jamais armée, ou arrêtée — ne fait taire personne. Sans cette borne, une
    boucle éteinte après avoir tourné laissait ses agents muets pour toujours."""
    if not trigger_ids:
        return set()
    with _connect() as conn:
        return {int(r["trigger_id"]) for r in conn.execute(
            "SELECT trigger_id FROM agent_episodes "
            "WHERE trigger_id = ANY(%s) AND cote = 'plateforme' "
            "AND statut <> 'clos' AND vu_at > NOW() - %s",
            ([int(i) for i in trigger_ids], vus_depuis)).fetchall()}
