"""Les agents programmés BLOQUÉS — leur relevé et la marque de l'épisode annoncé.

Un agent dont les travaux meurent l'un après l'autre ne prévient personne : sa file se
vide, son écran le dit « actif », et la panne se découvre en cherchant autre chose
(signal 1168 : une routine arrêtée 33 passages de suite sur une clé refusée, sans
personne de prévenu). `usage.agents_en_echec` rend ces pannes LISIBLES ; ce module les
rend ANNONÇABLES, une fois par épisode (`alerte_agents_bloques`).

**L'épisode** = les travaux morts d'un agent depuis son dernier travail réussi — donc
consécutifs ; mort et réussi au sens de `issues_des_travaux` (un `done` que l'agent a
conclu `blocked` est mort). Il s'ouvre quand il atteint le seuil, il se clôt au premier
succès. `runner_triggers.echec_annonce_at` dit qu'il a été annoncé : un épisode est
annoncé si cette date est postérieure à son dernier succès (ou s'il n'y en a aucun).
Aucune remise à zéro à écrire : le succès suivant suffit à rouvrir la porte.

⚠️ La marque n'est PAS une écriture de configuration : elle ne date pas l'agent
(`updated_at`, `db/runner_triggers.py`).
"""
from __future__ import annotations

from typing import Any

from ._conn import _connect
from . import issues_des_travaux

# La date de l'annonce du dernier épisode de blocage d'un agent : posée par la révision
# `20261023_1040_echec_annonce` ET par le démarrage, sous garde de catalogue —
# même DDL. NULL = aucun épisode annoncé.
COLONNE_ECHEC_ANNONCE = "echec_annonce_at"
DDL_COLONNE_ECHEC_ANNONCE = (f"ALTER TABLE runner_triggers ADD COLUMN IF NOT EXISTS "
                             f"{COLONNE_ECHEC_ANNONCE} TIMESTAMPTZ")

#: Les travaux morts que l'on regarde : ceux de la dernière semaine. Un épisode plus
#: ancien dont rien n'a bougé depuis n'est plus une panne qui court.
FENETRE_JOURS = 7

#: Ce qu'on garde du dernier motif pour le courriel.
MOTIF_TETE = 200

#: Le discriminant d'un travail de déclencheur, écrit comme l'index partiel
#: `idx_runner_jobs_declencheurs` (`runner_jobs.INDEX_DECLENCHEURS`) pour qu'il serve.
_DU_DECLENCHEUR = ("j.org_id = t.org_id AND j.payload->>'trigger_id' ~ '^[0-9]+$' "
                   "AND j.payload->>'trigger_id' = t.id::text")


def agents_bloques(seuil: int) -> list[dict[str, Any]]:
    """Les agents ALLUMÉS dont l'épisode de blocage atteint `seuil` travaux morts
    consécutifs et n'a pas encore été annoncé. Une ligne par agent : son org, son
    libellé, le compte, la date du premier et du dernier travail mort, le dernier motif."""
    with _connect() as conn:
        return [dict(r) for r in conn.execute(
            f"""
            SELECT t.id AS trigger_id, t.org_id, t.sub AS proprietaire,
                   COALESCE(NULLIF(t.label, ''), t.procedure) AS libelle,
                   f.travaux, f.depuis, f.dernier, f.motif
              FROM runner_triggers t
              CROSS JOIN LATERAL (
                    SELECT MAX(j.finished_at) AS dernier_succes
                      FROM runner_jobs j
                     WHERE {_DU_DECLENCHEUR} AND {issues_des_travaux.reussi()}) s
              CROSS JOIN LATERAL (
                    SELECT COUNT(*)::int AS travaux,
                           MIN(j.finished_at) AS depuis, MAX(j.finished_at) AS dernier,
                           (array_agg(LEFT({issues_des_travaux.motif()}, %(tete)s)
                                      ORDER BY j.finished_at DESC))[1] AS motif
                      FROM runner_jobs j
                     WHERE {_DU_DECLENCHEUR} AND {issues_des_travaux.mort()}
                       AND j.finished_at > NOW() - make_interval(days => %(fenetre)s)
                       AND (s.dernier_succes IS NULL
                            OR j.finished_at > s.dernier_succes)) f
             WHERE t.enabled
               AND f.travaux >= %(seuil)s
               AND (t.{COLONNE_ECHEC_ANNONCE} IS NULL
                    OR (s.dernier_succes IS NOT NULL
                        AND t.{COLONNE_ECHEC_ANNONCE} < s.dernier_succes))
             ORDER BY t.org_id, t.id
            """,
            {"seuil": max(1, int(seuil)), "fenetre": FENETRE_JOURS, "tete": MOTIF_TETE},
        ).fetchall()]


def marquer_annonces(trigger_ids: list) -> int:
    """Pose `echec_annonce_at` sur les agents annoncés — APRÈS l'envoi, jamais avant :
    marquer d'abord ferait d'un envoi raté un silence définitif. Rend le nombre marqué."""
    if not trigger_ids:
        return 0
    with _connect() as conn:
        return conn.execute(
            f"UPDATE runner_triggers SET {COLONNE_ECHEC_ANNONCE} = NOW() "
            "WHERE id = ANY(%s)", ([int(i) for i in trigger_ids],)).rowcount or 0


#: Combien de temps un travail attend, et l'abonnement est resté à reconnecter, avant
#: qu'on l'annonce : une sonde peut écrire `needs_login` le temps d'un jeton qui se
#: renouvelle, et un travail tout juste enfilé n'a encore rien manqué.
ATTENTE_FORFAIT_MIN = 30


def agents_sans_forfait(attente_min: int = ATTENTE_FORFAIT_MIN) -> list[dict[str, Any]]:
    """Les agents ALLUMÉS dont les travaux ATTENDENT parce que l'abonnement personnel
    qui doit les servir est à reconnecter (`needs_login`) ou déconnecté — et dont la
    panne n'a pas encore été annoncée.

    L'angle mort d'`agents_bloques` : un tel travail ne MEURT pas. La réservation le
    saute (`runner_jobs`, `clause_abonnement`), il attend, puis expire à l'échéance
    suivante (`expired`, jamais `failed`) ; aucun épisode ne s'ouvre, l'écran dit
    « actif ». Une org est restée des jours sans un passage, sans un mot à personne.

    Lu sur les travaux EUX-MÊMES, avec la règle de la réservation : seuls ceux qui ne
    passent PAS par le pool de l'org (`runner_jobs._par_le_pool`, l'expression même que
    lit la prise) attendent l'abonnement de leur titulaire — un travail que le pool sert
    n'attend pas, un pool sans prêteur servable est une autre panne. `paused_limit`
    n'en est pas : il repart seul à l'échéance du forfait.

    La marque est celle de l'épisode (`echec_annonce_at`) : une annonce vaut jusqu'au
    prochain succès de l'agent OU jusqu'à la prochaine preuve que l'abonnement tenait
    (`last_ok_at`) — une personne qui reconnecte puis retombe est prévenue de nouveau."""
    from .. import runner_models
    from .runner_jobs import _par_le_pool
    familles = sorted(runner_models.FAMILLES_PERSONNELLES)
    modeles = runner_models.MODELES_PERSONNELS
    if not familles or not modeles:
        return []
    with _connect() as conn:
        return [dict(r) for r in conn.execute(
            f"""
            SELECT t.id AS trigger_id, t.org_id, t.sub AS proprietaire,
                   COALESCE(NULLIF(t.label, ''), t.procedure) AS libelle,
                   w.famille, w.statut AS forfait, w.travaux, w.depuis
              FROM runner_triggers t
              CROSS JOIN LATERAL (
                    SELECT MAX(j.finished_at) AS dernier_succes
                      FROM runner_jobs j
                     WHERE {_DU_DECLENCHEUR} AND {issues_des_travaux.reussi()}) s
              CROSS JOIN LATERAL (
                    SELECT rj.payload->>'model_family' AS famille, ab.statut,
                           ab.last_ok_at, COUNT(*)::int AS travaux,
                           MIN(rj.created_at) AS depuis
                      FROM runner_jobs rj
                      JOIN user_model_subscriptions ab
                        ON ab.sub = rj.sub AND ab.famille = rj.payload->>'model_family'
                     WHERE rj.org_id = t.org_id
                       -- Le travail porte l'identité du TITULAIRE : un `run_now` lancé
                       -- par un autre membre attend SON forfait, pas celui de l'agent.
                       AND rj.sub = t.sub
                       AND rj.payload->>'trigger_id' ~ '^[0-9]+$'
                       AND rj.payload->>'trigger_id' = t.id::text
                       AND rj.status IN ('pending', 'expired')
                       AND rj.created_at > NOW() - make_interval(days => %(fenetre)s)
                       AND rj.created_at < NOW() - make_interval(mins => %(attente)s)
                       AND (s.dernier_succes IS NULL OR rj.created_at > s.dernier_succes)
                       AND rj.payload->>'model_family' = ANY(%(familles)s)
                       AND ab.statut IN ('needs_login', 'disconnected')
                       AND ab.updated_at < NOW() - make_interval(mins => %(attente)s)
                       AND NOT {_par_le_pool()}
                     GROUP BY rj.payload->>'model_family', ab.statut, ab.last_ok_at) w
             WHERE t.enabled
               -- L'agent tourne ENCORE sur cette famille : un agent passé depuis sur
               -- une clé n'a plus rien à reconnecter, même si un vieux travail attend.
               AND (t.model, w.famille) IN (
                     SELECT * FROM unnest(%(modeles)s::text[], %(de_famille)s::text[]))
               AND (t.{COLONNE_ECHEC_ANNONCE} IS NULL
                    OR t.{COLONNE_ECHEC_ANNONCE} < GREATEST(
                           COALESCE(s.dernier_succes, '-infinity'::timestamptz),
                           COALESCE(w.last_ok_at, '-infinity'::timestamptz)))
             ORDER BY t.org_id, t.id
            """,
            {"fenetre": FENETRE_JOURS, "attente": max(1, int(attente_min)),
             "familles": familles, "modeles": list(modeles),
             "de_famille": [runner_models.famille(m) for m in modeles]},
        ).fetchall()]
