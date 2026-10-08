"""Clés d'API d'org : le compte de service d'une org, et l'émetteur d'une clé.

- `org_service_accounts` : une table NEUVE, née entière (fragment
  `db/schema/orgs.py::ORG_SERVICE_ACCOUNTS`, exécuté tel quel) — aucune réécriture.
- `user_api_tokens.created_by` : une colonne NULLABLE, sans défaut, sans index — une
  écriture de catalogue seule. L'`ALTER` prend un `AccessExclusiveLock` sur une table
  lue à chaque requête par jeton : d'où le `lock_timeout`, un échec net qu'on rejoue.

⚠️ **Ordre avec le code : AVANT la fusion.** Le code du même lot lit la table à chaque
lecture d'appartenance (`org_store.members`) et la colonne dans la liste des jetons :
joué après, ces lectures lèveraient (`UndefinedTable`, `UndefinedColumn`). L'ancien code
ignore les deux (ajout pur) : jouer la révision avant le déploiement est sûr. Prod et
préprod partagent la base.

Retour arrière : après le retrait du code qui les lit, et après révocation des clés
d'org (`DELETE FROM users` des comptes de service emporte leurs jetons).

Révision : 0047_cles_d_org
Précédente : 0046_abonnement_par_org
"""
from __future__ import annotations

from alembic import op

from oto_mcp.db.schema.orgs import ORG_SERVICE_ACCOUNTS

revision = "0047_cles_d_org"
down_revision = "0046_abonnement_par_org"
branch_labels = None
depends_on = None

_ATTENTE_MAX = "SET LOCAL lock_timeout = '5s'"


def upgrade() -> None:
    op.execute(_ATTENTE_MAX)
    op.execute(ORG_SERVICE_ACCOUNTS)
    op.execute("ALTER TABLE user_api_tokens ADD COLUMN IF NOT EXISTS created_by TEXT")


def downgrade() -> None:
    op.execute(_ATTENTE_MAX)
    op.execute("ALTER TABLE user_api_tokens DROP COLUMN IF EXISTS created_by")
    op.execute("DROP TABLE IF EXISTS org_service_accounts")
