"""tenants : un tenant se DÉSACTIVE par un état explicite (`disabled_at`, `disabled_by`,
`disabled_reason`) — oto-backend#1165.

Désactiver un tenant se faisait en vidant `issuer` et `jwks_uri` de sa ligne, puis en
rechargeant le registre d'émetteurs : ça n'éteignait que les jetons signés par SON
annuaire, dans le seul processus rechargé, et laissait servis les jetons d'API, les
jetons de délégation et les anciens identifiants redirigés vers ses comptes. L'état
devient une colonne, lue à chaque vérification d'identité d'un compte qualifié sous un
tenant (`garde_identite`, `docs/tenants.md` §Désactiver un tenant).

Trois colonnes NULLABLES, sans défaut ni index : pour PostgreSQL, une écriture de
catalogue seule, sans réécriture ni parcours (`tenants` compte quelques lignes). L'`ALTER`
prend un `AccessExclusiveLock` sur `tenants`, que le registre d'émetteurs lit au boot et
au rechargement, et que la fiche des tenants lit : d'où le `lock_timeout` — un échec net,
qu'on rejoue, plutôt qu'une file derrière nous.

Une base NEUVE les reçoit du `CREATE TABLE` (`db/schema/tenants.py`).

⚠️ **Ordre avec le code : AVANT la fusion.** Le code du même lot lit `disabled_at` à
chaque requête d'un compte QUALIFIÉ sous un tenant tiers (sub `<slug>:…`) : jouée après,
ces requêtes-là échoueraient en `UndefinedColumn` — fermées, jamais ouvertes, mais
fermées pour tout le monde. Les comptes du tenant primaire (subs nus) ne lisent pas la
colonne. L'ancien code l'ignore (ajout pur) : jouer la révision avant le déploiement est
sûr. Prod et préprod partagent la base.

Retour arrière : retire les colonnes — un tenant désactivé redevient servi (ses jetons
révoqués, eux, le restent : la révocation vit sur `user_api_tokens`).

Révision : 0040_tenants_desactivation
Précédente : 0039_feed_synced_at_retiree
"""
from __future__ import annotations

from alembic import op

revision = "0040_tenants_desactivation"
down_revision = "0039_feed_synced_at_retiree"
branch_labels = None
depends_on = None

_ATTENTE_MAX = "SET LOCAL lock_timeout = '5s'"

_COLONNES = (("disabled_at", "TIMESTAMPTZ"), ("disabled_by", "TEXT"),
             ("disabled_reason", "TEXT"))


def upgrade() -> None:
    op.execute(_ATTENTE_MAX)
    op.execute("ALTER TABLE tenants " + ", ".join(
        f"ADD COLUMN IF NOT EXISTS {nom} {type_}" for nom, type_ in _COLONNES))


def downgrade() -> None:
    op.execute(_ATTENTE_MAX)
    op.execute("ALTER TABLE tenants " + ", ".join(
        f"DROP COLUMN IF EXISTS {nom}" for nom, _ in _COLONNES))
