"""user_api_tokens : la révocation laisse une trace (`revoked_at`, `revoked_by`,
`revoked_reason`) — oto-backend#523.

Révoquer un jeton était un `DELETE` de la ligne : après coup, on ne savait plus qui le
détenait, ni quand ni pourquoi il avait été coupé — précisément le cas où la trace
compte (un jeton trop large stocké chez un tiers). La ligne reste désormais, et
`verify_api_token` refuse un jeton dont `revoked_at` est posé.

Trois colonnes NULLABLES, sans défaut ni index : pour PostgreSQL, une écriture de
catalogue seule, sans réécriture ni parcours. Mais l'`ALTER` prend un
`AccessExclusiveLock` sur `user_api_tokens`, que CHAQUE requête authentifiée par jeton
lit et écrit (`verify_api_token` pose `last_used_at`) : d'où le `lock_timeout` — un
échec net, qu'on rejoue, plutôt qu'une file de requêtes bloquées derrière nous
(incident du 18/09 sur `orgs`, docs/migrations-versionnees.md). Et c'est pourquoi ces
colonnes ne sont PAS posées au démarrage : cette table a déjà connu le deadlock
`ALTER` de boot contre requête (docstring de `db/tokens.verify_api_token`).

Une base NEUVE les reçoit du `CREATE TABLE` (`db/schema/tokens.py`).

⚠️ Prod et préprod partagent la MÊME base. L'ancien code ne lit ni n'écrit ces
colonnes (ajout pur) : jouer cette révision AVANT le déploiement est sûr, et un retour
au tag précédent aussi — un jeton révoqué par le nouveau code redeviendrait toutefois
valide sous l'ancien, qui ne connaît pas `revoked_at`. L'inverse n'est pas sûr : le
code qui l'accompagne lit `revoked_at` à chaque authentification par jeton et
répondrait `UndefinedColumn` — **la révision s'applique AVANT la fusion** (main =
préprod).

Révision : 0007_jetons_revocation_tracee
Précédente : 0006_unipile_fin_de_droit
"""
from __future__ import annotations

from alembic import op

revision = "0007_jetons_revocation_tracee"
down_revision = "0006_unipile_fin_de_droit"
branch_labels = None
depends_on = None

# Au-delà, on abandonne plutôt que de faire attendre les requêtes par jeton derrière nous.
_ATTENTE_MAX = "SET LOCAL lock_timeout = '5s'"

_COLONNES = (("revoked_at", "TIMESTAMPTZ"), ("revoked_by", "TEXT"),
             ("revoked_reason", "TEXT"))


def upgrade() -> None:
    op.execute(_ATTENTE_MAX)
    op.execute("ALTER TABLE user_api_tokens " + ", ".join(
        f"ADD COLUMN IF NOT EXISTS {nom} {type_}" for nom, type_ in _COLONNES))


def downgrade() -> None:
    op.execute(_ATTENTE_MAX)
    op.execute("ALTER TABLE user_api_tokens " + ", ".join(
        f"DROP COLUMN IF EXISTS {nom}" for nom, _ in _COLONNES))
