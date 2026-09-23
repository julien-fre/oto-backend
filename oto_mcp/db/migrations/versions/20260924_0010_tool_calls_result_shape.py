"""tool_calls : `result_shape`, la FORME du résultat servi — oto-backend#644.

`empty` | `non_empty` | `refused(<code>)`, jamais le contenu : ce que `result_size`
laisse ambigu (un 0 ne sépare pas une liste vide d'un refus rendu poliment). Écrite
par le journal d'appels (`calllog.forme_servie`), rendue par la fiche et la liste.

Une colonne NULLABLE, sans défaut ni index : pour PostgreSQL, une écriture de
catalogue seule, sans réécriture ni parcours de la table — qui porte plusieurs
millions de lignes. Mais l'`ALTER` prend un `AccessExclusiveLock` sur `tool_calls`,
où CHAQUE appel écrit sa ligne : d'où le `lock_timeout` — un échec net, qu'on rejoue,
plutôt qu'une file d'insertions bloquées derrière nous (incident du 18/09 sur `orgs`,
docs/migrations-versionnees.md). Et c'est pourquoi elle n'est PAS posée au démarrage.

Le vocabulaire est fermé PAR LA BASE (contrainte `tool_calls_result_shape_ferme`) :
une colonne TEXT libre sous un nom de résultat pourrait garder une réponse. Posée
`NOT VALID` — aucun parcours des lignes existantes, toutes à NULL ; elle vaut pour
chaque ligne écrite ensuite. Une colonne ajoutée AVEC sa contrainte, elle, ferait
vérifier toute la table sous le verrou exclusif.

Une base NEUVE les reçoit du `CREATE TABLE` (`db/schema/usage.py`) : l'`ALTER` est
sauté (`IF NOT EXISTS`) et la contrainte n'est posée que si elle manque.

⚠️ Prod et préprod partagent la MÊME base. L'ancien code ne lit ni n'écrit cette
colonne (ajout pur) : jouer cette révision AVANT le déploiement est sûr, et un retour
au tag précédent aussi. L'inverse n'est pas sûr : le code qui l'accompagne l'ÉCRIT à
chaque appel (`insert_tool_call`) et la LIT dans la liste et la fiche — sans elle,
chaque ligne de journal échouerait (`UndefinedColumn`, avalé en warning : le journal
se viderait en silence) et les lectures du monitoring répondraient en erreur.
**La révision s'applique AVANT la fusion** (main = préprod).

Révision : 0010_tool_calls_result_shape
Précédente : 0009_coffre_secret_obligatoire
"""
from __future__ import annotations

from alembic import op

revision = "0010_tool_calls_result_shape"
down_revision = "0009_coffre_secret_obligatoire"
branch_labels = None
depends_on = None

# Au-delà, on abandonne plutôt que de faire attendre le journal derrière nous.
_ATTENTE_MAX = "SET LOCAL lock_timeout = '5s'"

_CONTRAINTE = "tool_calls_result_shape_ferme"
_VOCABULAIRE = "'^(empty|non_empty|refused[(][a-z][a-z_]{0,39}[)])$'"


def upgrade() -> None:
    op.execute(_ATTENTE_MAX)
    op.execute("ALTER TABLE tool_calls ADD COLUMN IF NOT EXISTS result_shape TEXT")
    op.execute(f"""
        DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = '{_CONTRAINTE}') THEN
                ALTER TABLE tool_calls ADD CONSTRAINT {_CONTRAINTE}
                    CHECK (result_shape ~ {_VOCABULAIRE}) NOT VALID;
            END IF;
        END $$""")


def downgrade() -> None:
    op.execute(_ATTENTE_MAX)
    op.execute("ALTER TABLE tool_calls DROP COLUMN IF EXISTS result_shape")
