"""Les agents posés sur un modèle Claude d'avant passent à la génération 5.5.

Le catalogue (`runner_models.MODELES`) REMPLACE le 11/11/2026 `claude-sonnet-5`,
`claude-opus-5` et `claude-haiku-4-5` par `claude-sonnet-5-5`, `claude-opus-5-5` et
`claude-haiku-5-5`. Un agent qui nommerait encore un id retiré verrait sa livraison
refusée (`runner_models.motif_sans_famille` : « modèle inconnu du catalogue ») — il
cesserait de tourner sans que personne ne l'ait décidé. Cette révision le réécrit.

**Ce qui change.** Sur `runner_triggers.model`, `runner_triggers.forfait_prete_model` et
`runner_fleets.model`, chaque id retiré devient son remplaçant (`_REMPLACES`). Les deux
colonnes d'un déclencheur bougent dans la MÊME écriture : un prêt de forfait n'est vivant
que si `forfait_prete_model = model` (`db.runner_triggers._COLS`) — réécrire l'une sans
l'autre le rendrait caduc. `updated_at`/`updated_by` ne bougent pas : personne n'a
retouché l'agent.

**Ce qui ne change PAS : `runner_jobs`.** Un travail enfilé porte déjà sa charge
(`model`, `model_family`, `effort`) ; il se route par `model_family` (`anthropic`,
inchangée), et Anthropic sert toujours les ids d'avant — il tourne tel quel. Un `continue`
reprend À DESSEIN le modèle du départ de son run (`runner_jobs.modele_du_run`) : réécrire
l'historique ferait poursuivre un fil sur un autre modèle que celui où il a commencé. Les
tours de ces travaux se règlent sous les lignes `anthropic.messages:<id d'avant>`, que le
barème garde (`credits_tours.fonctions_connues` les connaît encore).

**Verrous.** `RowExclusiveLock` et le verrou des seules lignes visées ; le tick lit et
écrit `runner_triggers` en continu, d'où `lock_timeout` 5 s : un échec net, qu'on rejoue.

**Ordre avec le code.** Avant le nouveau code : l'ancien catalogue ne connaît pas les ids
5.5 — un agent réécrit ne partirait pas jusqu'au déploiement. Après : un agent resté sur
un id d'avant ne partirait pas jusqu'à la révision. Le déploiement joue la révision au
démarrage du nouveau code : la fenêtre est celle du démarrage.

**Retour arrière.** La correspondance inverse, sur les mêmes colonnes : l'ancien code ne
sert que les ids d'avant. Un agent posé sur un modèle 5.5 APRÈS la révision redescend
lui aussi — c'est ce dont l'ancien catalogue a besoin, pas une perte.

Révision : 20261111_1400_modeles_claude_55
Précédente : 20261111_0920_jetons_echeance
"""
from __future__ import annotations

from alembic import op

revision = "20261111_1400_modeles_claude_55"
down_revision = "20261111_0920_jetons_echeance"
branch_labels = None
depends_on = None

_ATTENTE_MAX = "SET LOCAL lock_timeout = '5s'"

#: La correspondance au jour de la révision — écrite en dur, pas lue de
#: `runner_models.REMPLACES` : une révision rejoue ce qu'elle a fait, pas ce que le code
#: dira demain.
_REMPLACES = (
    ("claude-sonnet-5", "claude-sonnet-5-5"),
    ("claude-opus-5", "claude-opus-5-5"),
    ("claude-haiku-4-5", "claude-haiku-5-5"),
)


def _cas(colonne: str, paires) -> str:
    branches = " ".join(f"WHEN '{de}' THEN '{vers}'" for de, vers in paires)
    return f"CASE {colonne} {branches} ELSE {colonne} END"


def _liste(ids) -> str:
    return ", ".join(f"'{i}'" for i in ids)


def ordres(paires) -> list[str]:
    """Les UPDATE qui portent `paires` (`(de, vers)`) sur les colonnes des agents."""
    de = _liste(d for d, _ in paires)
    return [
        "UPDATE runner_triggers "
        f"SET model = {_cas('model', paires)}, "
        f"forfait_prete_model = {_cas('forfait_prete_model', paires)} "
        f"WHERE model IN ({de}) OR forfait_prete_model IN ({de})",
        f"UPDATE runner_fleets SET model = {_cas('model', paires)} WHERE model IN ({de})",
    ]


def upgrade() -> None:
    op.execute(_ATTENTE_MAX)
    for ordre in ordres(_REMPLACES):
        op.execute(ordre)


def downgrade() -> None:
    op.execute(_ATTENTE_MAX)
    for ordre in ordres(tuple((vers, de) for de, vers in _REMPLACES)):
        op.execute(ordre)
