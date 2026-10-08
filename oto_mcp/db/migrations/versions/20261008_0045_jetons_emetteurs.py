"""user_api_tokens : un jeton dit quel jeton ÉMETTEUR l'a émis (`parent_id`).

Un jeton porté `{"issue": <plafond>}` émet lui-même des jetons à portée, sans humain à
chaque clé (`auth/token_scopes.ISSUE`). Un enfant ne fonctionne que tant que son parent
fonctionne (`db.tokens.verify_api_token`), la révocation du parent le révoque, et
l'émetteur ne liste ni ne révoque que ses propres enfants : la colonne le permet.

Une colonne NULLABLE, sans défaut, sans index ni clé étrangère : pour PostgreSQL, une
écriture de catalogue seule, sans réécriture ni parcours. L'`ALTER` prend un
`AccessExclusiveLock` sur `user_api_tokens`, lue à chaque requête portée par un jeton :
d'où le `lock_timeout`, un échec net qu'on rejoue plutôt qu'une file de lecteurs
derrière nous.

Une base NEUVE la reçoit du `CREATE TABLE` (`db/schema/tokens.py`).

⚠️ **Ordre avec le code : AVANT la fusion.** Le code du même lot la lit à CHAQUE
vérification de jeton `oto_` : joué après, tout jeton API serait refusé
(`UndefinedColumn`). L'ancien code l'ignore (ajout pur) : jouer la révision avant le
déploiement est sûr. Prod et préprod partagent la base.

Retour arrière : retire la colonne — après le retrait du code qui la lit, et après
révocation des jetons émetteurs (leurs enfants perdraient sinon leur lien au parent).

Révision : 0045_jetons_emetteurs
Précédente : 0044_jev_jobs
"""
from __future__ import annotations

from alembic import op

revision = "0045_jetons_emetteurs"
down_revision = "0044_jev_jobs"
branch_labels = None
depends_on = None

_ATTENTE_MAX = "SET LOCAL lock_timeout = '5s'"


def upgrade() -> None:
    op.execute(_ATTENTE_MAX)
    op.execute("ALTER TABLE user_api_tokens ADD COLUMN IF NOT EXISTS parent_id BIGINT")


def downgrade() -> None:
    op.execute(_ATTENTE_MAX)
    op.execute("ALTER TABLE user_api_tokens DROP COLUMN IF EXISTS parent_id")
