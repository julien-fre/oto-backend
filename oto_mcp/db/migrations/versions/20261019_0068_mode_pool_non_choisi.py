"""org_model_subscription_modes.mode : NULL = rien CHOISI.

Le défaut du pool change (19/10/2026) : une org qui n'a pas réglé son mode passe en
`pool` dès qu'un membre vivant lui prête son abonnement (`org_subscription_pool.
mode_effectif`) — partager avec l'équipe sert tout de suite, sans admin. Un mode
RÉGLÉ gagne toujours.

Mais une ligne naissait aussi du repli API (`set_repli_api`), figée à `personnel` :
ouvrir le repli aurait fermé le nouveau défaut sans que personne ne l'ait choisi. Elle
naît désormais SANS mode, ce qu'il faut que la colonne accepte. La CHECK laisse passer
NULL telle quelle.

Les lignes déjà nées du repli à `personnel` le restent : rien ne les distingue d'un
choix d'admin, et les rouvrir déciderait à sa place.

**Ordre avec le code : indifférent.** Le code neuf essaie NULL et, refusé (révision pas
encore jouée), fait naître la ligne à `personnel` comme avant en le journalisant
(`set_repli_api`). L'ancien code lit NULL comme `personnel` (`get_mode(...)["mode"]`
falsy ; SQL `m.mode = 'pool'` faux). Geste sur notre box : `oto-mcp migrer upgrade
head` par le lanceur (docs/migrations-versionnees.md §5.1).

**Verrous.** `AccessExclusiveLock` bref sur une petite table (une ligne par org
réglée), aucun parcours : relâcher un NOT NULL ne relit pas les lignes.

Retour arrière : `UPDATE org_model_subscription_modes SET mode = 'personnel' WHERE
mode IS NULL`, puis `ALTER COLUMN mode SET NOT NULL`.

Révision : 0068_mode_pool_non_choisi
Précédente : 0067_runner_jobs_declencheurs
"""
from __future__ import annotations

from alembic import op

revision = "0068_mode_pool_non_choisi"
down_revision = "0067_runner_jobs_declencheurs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("ALTER TABLE org_model_subscription_modes ALTER COLUMN mode DROP NOT NULL")


def downgrade() -> None:
    op.execute("UPDATE org_model_subscription_modes SET mode = 'personnel' WHERE mode IS NULL")
    op.execute("ALTER TABLE org_model_subscription_modes ALTER COLUMN mode SET NOT NULL")
