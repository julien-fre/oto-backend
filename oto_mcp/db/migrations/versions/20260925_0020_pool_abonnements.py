"""Pool d'org des abonnements : le mode de l'org, les prêts de ses membres.

Un seul geste, additif : les DEUX tables NEUVES du fragment
`db/schema/runs.py::MODEL_SUBSCRIPTION_POOL`, exécuté tel quel —
`org_model_subscription_modes` (le mode `personnel` | `pool` d'une org, par famille) et
`user_model_subscription_loans` (le prêt d'un abonnement au pool d'une org), avec son
index. Le démarrage les crée aussi (`CREATE TABLE IF NOT EXISTS`, sauté par le garde des
DDL une fois posées) : idempotents l'un envers l'autre. Les deux clés étrangères vers
`orgs` prennent un verrou sur `orgs` à la création, borné par `lock_timeout`.

**Aucun `ALTER` sur une table existante.** L'abonnement qui sert un travail s'écrit
dans sa charge (`runner_jobs.payload._plateforme.abonnement`), jamais dans une colonne
de `runner_jobs` : la table la plus sondée de la base partagée n'est pas touchée.

⚠️ Prod et préprod partagent la MÊME base. L'ancien code ne lit aucune des deux tables :
jouer cette révision avant ou après le déploiement ne change rien pour lui. Le code du
lot, lui, les LIT à chaque réservation d'un travail d'abonnement (`claim_next_job`,
dépôt `claude_subscription` seulement — le SQL des autres dépôts ne les nomme pas) :
à jouer AVANT la fusion, pour fermer la fenêtre où un démarrage raté sur
`lock_timeout` les laisserait absentes. Le retour arrière retire les deux tables et
les prêts qu'elles portaient ; le code qui les lit doit être retiré avant lui.

Révision : 0020_pool_abonnements
Précédente : 0019_plafond_abonnements
"""
from __future__ import annotations

from alembic import op

from oto_mcp.db.schema.runs import MODEL_SUBSCRIPTION_POOL

revision = "0020_pool_abonnements"
down_revision = "0019_plafond_abonnements"
branch_labels = None
depends_on = None

_ATTENTE_MAX = "SET LOCAL lock_timeout = '5s'"


def upgrade() -> None:
    op.execute(_ATTENTE_MAX)
    op.execute(MODEL_SUBSCRIPTION_POOL)


def downgrade() -> None:
    op.execute(_ATTENTE_MAX)
    op.execute("DROP TABLE IF EXISTS user_model_subscription_loans")
    op.execute("DROP TABLE IF EXISTS org_model_subscription_modes")
