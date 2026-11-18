"""Les applications de Custom API : une définition par org et par outil, partagée par
toutes ses clés, quel que soit leur palier.

Une table NEUVE, née entière : `api_apps` (`db/schema/connectors.py::API_APPS`) — posée
aussi par le démarrage (`CREATE TABLE IF NOT EXISTS` de l'assemblage), même DDL. FK vers
`orgs` (ON DELETE CASCADE) ; aucune vers le coffre : une clé se lie à son application
par `connector_credentials.meta.api_app` (l'`id` de la ligne), posé à la pose de la clé
(`connectors/api_apps.completer_la_pose`). Aucune donnée reprise : une carte d'org
existante devient une application par un geste d'admin (`adopt_org_key`), jamais
d'office.

**Verrous.** La table est neuve et vide ; sa FK prend un `ShareRowExclusiveLock` bref sur
`orgs`. `lock_timeout` 5 s : un échec net, qu'on rejoue, plutôt qu'une attente.

**Ordre avec le code : indifférent.** Le démarrage pose la même table ; l'ancien code
l'ignore, et une clé liée garde dans le coffre tous ses champs (adresse et mode compris) :
l'ancien code la sert comme une carte ordinaire.

Retour arrière : retire la table. Les clés liées restent des cartes ordinaires ; leur
`meta.api_app` n'est plus lu par personne.

Révision : 20261118_1200_apps_custom_api
Précédente : 20261116_2220_modeles_claude_55
"""
from __future__ import annotations

from alembic import op

from oto_mcp.db.schema.connectors import API_APPS

revision = "20261118_1200_apps_custom_api"
down_revision = "20261116_2220_modeles_claude_55"
branch_labels = None
depends_on = None

_ATTENTE_MAX = "SET LOCAL lock_timeout = '5s'"


def upgrade() -> None:
    op.execute(_ATTENTE_MAX)
    op.execute(API_APPS)


def downgrade() -> None:
    op.execute(_ATTENTE_MAX)
    op.execute("DROP TABLE IF EXISTS api_apps")
