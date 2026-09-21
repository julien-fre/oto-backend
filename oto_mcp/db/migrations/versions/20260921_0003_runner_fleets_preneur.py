"""runner_fleets : `taken_by`, l'ordonnanceur qui TIENT une campagne.

`op=take` (capacité `runner.fleets`) passait une campagne `armed` → `running` sans
noter QUI la prenait : `heartbeat_at` datait un battement sans dire de qui, et `sub`
est le DÉCLARANT, pas le preneur. Un ordonnanceur d'oto-runner qui redémarrait ne
pouvait donc pas savoir s'il reprenait SA campagne ou s'il en voyait une qu'un autre
tenait encore — et, faute de mieux, il tolérait `not_takeable` sur une campagne
`running` en supposant une reprise : deux ordonnanceurs pouvaient conduire la même
campagne (docs/runner-et-automatisations.md, « Qui tient une campagne »).

Colonne NULLABLE, sans défaut : pour PostgreSQL c'est une écriture de catalogue seule,
sans réécriture ni parcours de la table. Mais l'`ALTER` prend quand même un
`AccessExclusiveLock` sur `runner_fleets`, que chaque sondage de worker lit
(`campagne_a_servir`) : s'il attendait derrière une transaction longue, la file des
lecteurs bloqués derrière LUI gèlerait les sondages — le mécanisme de l'incident du
18/09 sur `orgs` (docs/migrations-versionnees.md). D'où le `lock_timeout` : un échec
net, qu'on rejoue, plutôt qu'un gel de la production.

Pourquoi ici et pas au démarrage (`db/_init.py`) : un `ALTER` au boot demande son
verrou exclusif à CHAQUE démarrage de chaque couleur, préprod comprise, sur la base
partagée — mesuré le 18/09 sur `orgs` : jusqu'à ~5 s de trafic bloqué par tentative,
trois déploiements préprod en échec (d8e7272b). Une base NEUVE reçoit la colonne du `CREATE TABLE` (`db/schema/runs.py`) ; une
base qui existe l'attend de cette révision, jouée à la main (ADR 0065).

⚠️ Prod et préprod partagent la MÊME base. L'ancien code ne lit ni n'écrit la colonne
(ajout pur) : jouer cette révision AVANT le déploiement est sûr, et un retour au tag
précédent aussi. L'inverse ne l'est pas — le code qui l'accompagne la lit sur chaque
verbe de `runner.fleets` et répondrait `UndefinedColumn` : **la révision s'applique
AVANT la fusion** (main = préprod).

Révision : 0003_runner_fleets_preneur
Précédente : 0002_runner_jobs_index_vivant
"""
from __future__ import annotations

from alembic import op

revision = "0003_runner_fleets_preneur"
down_revision = "0002_runner_jobs_index_vivant"
branch_labels = None
depends_on = None

# Au-delà, on abandonne plutôt que de faire attendre les sondages derrière nous.
_ATTENTE_MAX = "SET LOCAL lock_timeout = '5s'"


def upgrade() -> None:
    op.execute(_ATTENTE_MAX)
    op.execute("ALTER TABLE runner_fleets ADD COLUMN IF NOT EXISTS taken_by TEXT")


def downgrade() -> None:
    op.execute(_ATTENTE_MAX)
    op.execute("ALTER TABLE runner_fleets DROP COLUMN IF EXISTS taken_by")
