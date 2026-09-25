"""Remplir `user_datastores.context_org_id` des tableaux personnels d'avant la colonne
(oto#160, phase 2).

La 0017 a posé la colonne ; le code la remplit à chaque création depuis. Les
personnels d'avant restent NULL. Cette révision reconstitue l'org de création de ceux
qu'une trace désigne sans ambiguïté, et de ceux-là seulement — la logique de la mesure
du 24/09 (36 personnels en production : 13 par le journal, 8 par un projet, 15
indécidables, aucun désaccord entre sources) :

1. **journal** — l'appel qui l'a créé, dans `tool_calls` (rétention 90 j) : l'outil
   MCP `data_create_datastore` du même compte (`coalesce(effective_sub, sub)`), au
   même nom (`args->>'datastore'` = le nom actuel), à ±2 min de la création ; ou la
   route `POST /api/datastores` du même compte à ±10 s (le nom n'y est pas journalisé).
   Retenu si ces appels portent UNE seule org non NULL ;
2. **projet** — sinon, les projets qui le lient (`project_links`, `tableau`) : l'org
   du projet (propriétaire org, org de l'équipe, ou `context_org_id` d'un projet
   personnel). Retenu si UNE seule org.

Rien d'autre n'est deviné. « L'org dont le propriétaire est l'unique membre » n'est
PAS une source : elle ne désigne l'org de création de personne. Un tableau qu'aucune
source ne décide garde NULL — il reste visible dans toutes les orgs de son propriétaire.
Une org désignée mais supprimée depuis n'est pas posée : la clé étrangère l'aurait
remise à NULL (`ON DELETE SET NULL`).

**Idempotente** : ne touche que `owner_type = 'user' AND context_org_id IS NULL`. Un
tableau déjà rempli — par le code, ou par un premier passage — n'est jamais réécrit ;
la rejouer ne remplit que ce qu'une trace nouvelle décide (un projet lié depuis).

Un seul `UPDATE`, sous `lock_timeout` (verrous de ligne sur `user_datastores`, que
toute résolution de tableau lit) et `statement_timeout` (la lecture de `tool_calls`,
bornée par l'index sur `tool` ; la mesure a tourné sous 15 s). **Rien au démarrage** :
c'est une donnée à reconstituer une fois, pas un schéma à tenir.

**Ordre indifférent** avec le tag : l'ancien code ne lit pas la colonne ; le nouveau
filtre la liste sur elle, et un tableau encore NULL y reste visible partout — la
révision ne fait que retirer des tableaux des orgs où ils n'ont pas été créés.

Retour arrière : **ne fait rien**. Une valeur remplie ici ne se distingue pas d'une
valeur posée par le code à la création ; les remettre à NULL effacerait aussi les
secondes.

Révision : 0018_contexte_org_rempli
Précédente : 0017_tableaux_contexte_org
"""
from __future__ import annotations

from alembic import op

revision = "0018_contexte_org_rempli"
down_revision = "0017_tableaux_contexte_org"
branch_labels = None
depends_on = None

_ATTENTE_MAX = "SET LOCAL lock_timeout = '5s'"
_DUREE_MAX = "SET LOCAL statement_timeout = '120s'"

REMPLISSAGE = """
WITH perso AS (
    SELECT d.id, d.owner_id AS sub, d.namespace, d.created_at
      FROM user_datastores d
     WHERE d.owner_type = 'user' AND d.context_org_id IS NULL
),
creations AS MATERIALIZED (
    SELECT t.kind, coalesce(t.effective_sub, t.sub) AS sub, t.args->>'datastore' AS nom,
           t.org_id, t.created_at
      FROM tool_calls t
     WHERE t.tool IN ('data_create_datastore', 'POST /api/datastores')
       AND t.ok
),
journal AS (
    SELECT p.id,
           count(DISTINCT c.org_id) FILTER (WHERE c.org_id IS NOT NULL) AS n_orgs,
           min(c.org_id) FILTER (WHERE c.org_id IS NOT NULL) AS org_id
      FROM perso p
      JOIN creations c
        ON c.sub = p.sub
       AND ((c.kind = 'mcp' AND c.nom = p.namespace
             AND c.created_at BETWEEN p.created_at - interval '2 minutes'
                                  AND p.created_at + interval '2 minutes')
         OR (c.kind = 'rest'
             AND c.created_at BETWEEN p.created_at - interval '10 seconds'
                                  AND p.created_at + interval '10 seconds'))
     GROUP BY p.id
),
projet AS (
    SELECT p.id, count(DISTINCT o.org_id) AS n_orgs, min(o.org_id) AS org_id
      FROM perso p
      JOIN project_links l ON l.target_type = 'tableau' AND l.target_ref = p.id::text
      JOIN projects pr ON pr.id = l.project_id
      LEFT JOIN org_groups g ON pr.owner_type = 'group' AND g.id::text = pr.owner_id
      CROSS JOIN LATERAL (SELECT CASE pr.owner_type
                                   WHEN 'org' THEN pr.owner_id::bigint
                                   WHEN 'group' THEN g.org_id
                                   ELSE pr.context_org_id END AS org_id) o
     WHERE o.org_id IS NOT NULL
     GROUP BY p.id
),
verdict AS (
    SELECT p.id,
           CASE WHEN j.n_orgs = 1 THEN j.org_id
                WHEN pj.n_orgs = 1 THEN pj.org_id END AS org_id
      FROM perso p
      LEFT JOIN journal j ON j.id = p.id
      LEFT JOIN projet pj ON pj.id = p.id
)
UPDATE user_datastores d
   SET context_org_id = v.org_id
  FROM verdict v
  JOIN orgs o ON o.id = v.org_id
 WHERE d.id = v.id
   AND d.owner_type = 'user'
   AND d.context_org_id IS NULL
"""


def upgrade() -> None:
    op.execute(_ATTENTE_MAX)
    op.execute(_DUREE_MAX)
    op.execute(REMPLISSAGE)


def downgrade() -> None:
    """Rien : un contexte rempli ici ne se distingue pas d'un contexte posé à la
    création (voir l'en-tête)."""
