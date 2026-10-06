"""Le journal des clés retirées sous des agents programmés — écriture, drain, marquage.

Une ligne = un moment où quelqu'un a retiré un credential alors que des **agents
programmés actifs** de l'org en dépendaient (oto#59). Le DDL vit dans
`db/schema/alertes.py` ; ici, les trois gestes qu'on fait dessus.

⚠️ **L'écriture ne casse jamais le retrait qu'elle observe.** Le credential est déjà
parti quand on écrit : faire échouer l'appel laisserait l'appelant devant une erreur
alors que sa clé n'est plus là — le pire des deux mondes. Best-effort, journalisé,
jamais avalé en silence (`scripts/lint_silences.py` l'exigerait de toute façon).

⚠️ **Le drain regroupe par ORG, pas par ligne.** Trois clés retirées le même jour font
un courriel, pas trois : un destinataire qui reçoit trois messages pour un incident
apprend à les ignorer, et c'est exactement ce qu'on cherche à éviter.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from ._conn import _connect

logger = logging.getLogger(__name__)

#: Libellés d'agents gardés par ligne. Le COMPTE reste juste au-delà ; c'est le courriel
#: qu'on borne, pas la mesure.
MAX_AGENTS = 10


def enregistrer(*, org_id: int, connector: str, account: str, acteur_sub: Optional[str],
                agents: list) -> Optional[int]:
    """Écrit la disparition. Rend son id, ou `None` si l'écriture a échoué.

    `agents` = les libellés lisibles des déclencheurs actifs qui dépendaient de la clé.
    Rendre `None` plutôt que lever : l'appelant vient de RÉUSSIR un retrait légitime et
    n'a rien à faire de cette panne-là. Mais elle est journalisée — un `None` qu'on
    ignore, c'est une alerte dont on croira les chiffres."""
    libelles = [str(a) for a in (agents or []) if a][:MAX_AGENTS]
    try:
        with _connect() as conn:
            row = conn.execute(
                """INSERT INTO credential_disparitions
                     (org_id, connector, account, acteur_sub, agents_count, agents)
                   VALUES (%s, %s, %s, %s, %s, %s::jsonb) RETURNING id""",
                (int(org_id), connector, account or "", acteur_sub,
                 len(agents or []), json.dumps(libelles, ensure_ascii=False)),
            ).fetchone()
            return int(row["id"]) if row else None
    except Exception as e:  # noqa: BLE001
        logger.warning("alerte credential : enregistrement impossible (%s org %s) : %s",
                       connector, org_id, e)
        return None


def a_notifier() -> list[dict[str, Any]]:
    """Ce qui n'a pas encore été annoncé, **groupé par org**.

    Une org = un courriel, quel que soit le nombre de clés parties. Rend, par org : les
    ids à marquer, les connecteurs concernés, et le plus gros compte d'agents touchés —
    de quoi écrire un message sans relire la table."""
    with _connect() as conn:
        return list(conn.execute(
            """SELECT org_id,
                      array_agg(id ORDER BY created_at) AS ids,
                      array_agg(DISTINCT connector) AS connectors,
                      max(agents_count) AS agents_max,
                      min(created_at) AS depuis
                 FROM credential_disparitions
                WHERE notifie_at IS NULL
                GROUP BY org_id
                ORDER BY min(created_at)"""))


def marquer_notifie(ids: list) -> int:
    """Pose `notifie_at` sur les lignes annoncées. Rend le nombre marqué.

    ⚠️ Appelé APRÈS l'envoi, jamais avant : marquer d'abord transformerait un envoi
    raté en silence définitif, ce qui est précisément la panne que cette table existe
    pour supprimer."""
    if not ids:
        return 0
    with _connect() as conn:
        return conn.execute(
            "UPDATE credential_disparitions SET notifie_at = NOW() "
            "WHERE id = ANY(%s) AND notifie_at IS NULL",
            ([int(i) for i in ids],)).rowcount or 0


# --- Clés REJETÉES ou À SEC sous des agents programmés (signaux oto #1168, #1189…) ----
#
# Une clé retirée n'est pas la seule à faire tourner des agents à l'aveugle : une clé
# que le fournisseur REFUSE (401/403) ou un compte À SEC (402) le font tout autant, et
# bien plus souvent. 33 passages programmés d'une même org ont buté sur une clé morte
# sans que personne soit prévenu. La marque existe déjà (`meta.health_ko` +
# `health_verdict`, posée par `connectors/health.py` au moment de l'appel) : on la LIT,
# on n'invente pas un second suivi.
#
# L'ÉPISODE est tenu ici, au passage quotidien, et pas au moment de la marque :
# `health_vu_ko_at` = le passage qui a vu la clé rouge en premier, `health_alerte_at` =
# celui qui l'a annoncée. Une clé redevenue verte les perd au passage suivant
# (`clore_episodes_gueris`) : sa prochaine chute est un épisode neuf, donc une alerte
# neuve. Cent refus de suite entre deux passages restent UN épisode.

#: Les paliers dont la marque est lisible ici : ceux que `connectors/health.py` accepte
#: de peindre en rouge (jamais tenant ni plateforme, partagés au-delà d'une org), et
#: dont on sait remonter à UNE org. Le palier `user` (legacy, sans org) n'y est pas.
_PALIERS_ORG = ("org", "member", "group")

_ORG_DE_LA_LIGNE = """
    CASE c.entity_type
      WHEN 'org' THEN CASE WHEN c.entity_id ~ '^[0-9]+$' THEN c.entity_id::bigint END
      WHEN 'member' THEN CASE WHEN split_part(c.entity_id, ':', 1) ~ '^[0-9]+$'
                              THEN split_part(c.entity_id, ':', 1)::bigint END
      WHEN 'group' THEN g.org_id
    END"""


def cles_ko_a_annoncer() -> list[dict[str, Any]]:
    """Les clés rouges dont l'épisode n'a pas encore été annoncé, avec leur org.

    Pose au passage `health_vu_ko_at` sur celles qu'on voit rouges pour la première
    fois : c'est le début de l'épisode tel que ce travail l'observe. Une ligne dont on
    ne sait pas remonter à une org n'est pas rendue (rien à qui l'annoncer)."""
    with _connect() as conn:
        conn.execute(
            "UPDATE connector_credentials SET meta = meta || "
            "jsonb_build_object('health_vu_ko_at', now()::text) "
            "WHERE entity_type = ANY(%s) AND meta->>'health_ko' = 'true' "
            "AND meta->>'health_vu_ko_at' IS NULL",
            (list(_PALIERS_ORG),))
        return list(conn.execute(
            f"""SELECT c.entity_type, c.entity_id, c.connector, c.account,
                       c.meta->>'health_reason' AS raison,
                       c.meta->>'health_verdict' AS verdict,
                       {_ORG_DE_LA_LIGNE} AS org_id
                  FROM connector_credentials c
                  LEFT JOIN org_groups g
                    ON c.entity_type = 'group' AND g.id::text = c.entity_id
                 WHERE c.entity_type = ANY(%s)
                   AND c.meta->>'health_ko' = 'true'
                   AND c.meta->>'health_alerte_at' IS NULL
                 ORDER BY c.connector""",
            (list(_PALIERS_ORG),)))


def marquer_cles_annoncees(lignes: list) -> int:
    """Pose `health_alerte_at` sur les clés annoncées — APRÈS l'envoi, jamais avant
    (même règle que `marquer_notifie`). Seulement si elles sont toujours rouges : une
    clé reposée entre la lecture et l'envoi ouvre un épisode neuf, qu'on ne clôt pas."""
    n = 0
    with _connect() as conn:
        for l in lignes:
            n += conn.execute(
                "UPDATE connector_credentials SET meta = meta || "
                "jsonb_build_object('health_alerte_at', now()::text) "
                "WHERE entity_type=%s AND entity_id=%s AND connector=%s AND account=%s "
                "AND meta->>'health_ko' = 'true'",
                (l["entity_type"], l["entity_id"], l["connector"], l["account"] or ""),
            ).rowcount or 0
    return n


def clore_episodes_gueris() -> int:
    """Retire l'épisode des clés redevenues vertes (sonde rejouée, crédits rechargés,
    clé reposée) : leur prochaine chute sera annoncée. Rend le nombre d'épisodes clos."""
    with _connect() as conn:
        return conn.execute(
            "UPDATE connector_credentials "
            "SET meta = meta - 'health_vu_ko_at' - 'health_alerte_at' "
            "WHERE entity_type = ANY(%s) "
            "AND COALESCE(meta->>'health_ko', 'false') <> 'true' "
            "AND (meta ? 'health_vu_ko_at' OR meta ? 'health_alerte_at')",
            (list(_PALIERS_ORG),)).rowcount or 0
