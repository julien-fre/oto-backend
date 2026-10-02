"""agents_partages_a_l_org : chaque agent existant reste modifiable par toute son org.

Avant le partage d'agents (`capabilities/_acces_agent.py`), tout membre de l'org
modifiait n'importe quel agent hébergé. Désormais un agent est à son propriétaire, qui
le partage nommément. Pour ne rien retirer à personne en silence, chaque agent DÉJÀ
posé reçoit un partage `editor` à son org entière (`principal_type='org'`) — l'écran
« Partager » le montre (« Everyone in the org »), son propriétaire le retire s'il veut
le fermer. Un agent créé ENSUITE naît privé.

⚠️ Base PARTAGÉE prod/preprod : la révision part avec la preprod. Un agent créé par
le code d'avant entre ce moment et le tag de prod naît sans ce partage, donc privé une
fois le nouveau code en prod — comme tout agent neuf. La rejouer après le tag de prod
(elle est idempotente) le rattrape si l'on préfère.

Idempotente : `ON CONFLICT DO NOTHING` — un partage déjà posé (ou retiré puis reposé
autrement) n'est pas réécrit. Volume : une ligne par agent (quelques dizaines).

Retour arrière : retire les partages d'org posés par cette révision (`granted_by`).

Révision : 0032_agents_partages_a_l_org
Précédente : 0031_selection_org_reelle
"""
from __future__ import annotations

from alembic import op

revision = "0032_agents_partages_a_l_org"
down_revision = "0031_selection_org_reelle"
branch_labels = None
depends_on = None

_MARQUE = "migration:0032_agents_partages_a_l_org"


def upgrade() -> None:
    op.execute(f"""
INSERT INTO resource_grants
       (resource_type, resource_id, principal_type, principal_id, permission, role,
        granted_by)
SELECT 'runner_trigger', id::text, 'org', org_id::text, 'write', 'editor', '{_MARQUE}'
  FROM runner_triggers
ON CONFLICT (resource_type, resource_id, principal_type, principal_id) DO NOTHING""")


def downgrade() -> None:
    op.execute(f"""
DELETE FROM resource_grants
 WHERE resource_type = 'runner_trigger' AND principal_type = 'org'
   AND granted_by = '{_MARQUE}'""")
