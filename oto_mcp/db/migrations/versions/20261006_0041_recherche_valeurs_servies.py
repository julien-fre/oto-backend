"""RÉFÉRENCE du registre : le schéma est celui que le démarrage crée au 06/10/2026.

Squash du 06/10/2026 (oto-backend#1162, docs/migrations-versionnees.md §5.4). Une révision
ne vit que tant qu'une base vivante est en retard sur elle ; ce jour-là, la plus ancienne
révision portée par une base vivante était celle-ci. Les quarante qui la précédaient
(0001 à 0040) sont retirées du registre — elles restent dans git, et le dernier tag qui
les porte est `v1.441.0`.

Cette révision garde son identifiant EXACT — les bases qui l'ont reçue le portent dans
`alembic_version` — mais n'a plus ni précédente ni ordre : elle DOCUMENTE un état, elle
ne le produit pas. Son ancien corps (la fonction `datastore_valeurs_texte_v1` et les deux
index de `q_scope=values`, oto-backend#307) est désormais dans le démarrage, qui le pose
sur une base neuve (`db/paths.py`, `db/search.py::INDEX_VALEURS`).

Une base y arrive de deux façons seulement : elle y est déjà, ou elle NAÎT plus loin (le
démarrage pose la tête, §5.2). Une base sans version ne « monte » pas jusqu'à elle — ce
serait déclarer, sans le vérifier, qu'elle porte tout le schéma de la référence — et rien
ne descend sous elle. Une base à une révision retirée est refusée nommément avant toute
commande (`db/_version_alembic.py`).

Révision : 0041_recherche_valeurs_servies
Précédente : aucune (référence)
"""
from __future__ import annotations

revision = "0041_recherche_valeurs_servies"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    raise RuntimeError(
        "0041_recherche_valeurs_servies est la RÉFÉRENCE du registre : une base n'y monte "
        "pas. Une base neuve naît à la tête (le démarrage l'estampille) ; une base existante "
        "sans version s'estampille à la main après lecture de son schéma "
        "(docs/migrations-versionnees.md §5.2).")


def downgrade() -> None:
    raise RuntimeError(
        "0041_recherche_valeurs_servies est la RÉFÉRENCE du registre : rien ne descend "
        "sous elle (docs/migrations-versionnees.md §5.4).")
