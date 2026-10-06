"""Le registre des migrations reste UNE file, et une seule.

Ce que ce test attrape : deux sessions qui ajoutent chacune une migration la même
semaine, chacune branchée sur la même précédente. Alembic accepte — il gère les
branches — mais la file n'a alors plus de fin unique, et « migrer » ne veut plus
rien dire sans préciser laquelle. Le défaut ne se voit pas à l'écriture ; il se voit
le jour où on applique, c'est-à-dire sur la base de production.

Et le registre part de la RÉFÉRENCE (squash, docs/migrations-versionnees.md §5.4) : un
seul point de départ, celui que `db/_version_alembic.py` nomme, vide, et aucune révision
retirée qui revienne — une base qui la porte serait refusée au démarrage.

Aucun accès à la base : on lit le dossier des révisions, rien d'autre.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

from oto_mcp.db._version_alembic import REFERENCE, SQUASHS

RACINE = Path(__file__).resolve().parent.parent


def _script() -> ScriptDirectory:
    cfg = Config(str(RACINE / "alembic.ini"))
    cfg.set_main_option("script_location", str(RACINE / "oto_mcp" / "db" / "migrations"))
    return ScriptDirectory.from_config(cfg)


def test_une_seule_tete():
    tetes = _script().get_heads()
    assert len(tetes) == 1, (
        f"{len(tetes)} files de migrations au lieu d'une : {tetes}. "
        "Deux révisions ont la même précédente — les fusionner (alembic merge) "
        "avant d'appliquer quoi que ce soit."
    )


def test_un_seul_point_de_depart():
    depart = [r for r in _script().walk_revisions() if r.down_revision is None]
    assert len(depart) == 1, f"{len(depart)} points de départ : {[r.revision for r in depart]}"


def test_chaque_revision_dit_ce_quelle_fait():
    for rev in _script().walk_revisions():
        assert (rev.doc or "").strip(), (
            f"la révision {rev.revision} n'a pas de description : une migration qu'on "
            "ne peut pas lire sans l'ouvrir ne se relit pas en incident."
        )


def test_le_point_de_depart_est_la_reference():
    depart = [r.revision for r in _script().walk_revisions() if r.down_revision is None]
    assert depart == [REFERENCE], (
        f"le registre part de {depart}, la référence nommée est {REFERENCE} : un squash "
        "met à jour les deux ensemble (docs/migrations-versionnees.md §5.4)")


def test_la_reference_ne_fait_rien_et_ne_se_traverse_pas():
    """Une référence documente un état ; elle ne le produit pas. Y monter déclarerait,
    sans le vérifier, qu'une base sans version porte tout son schéma ; rien ne descend
    sous elle."""
    module = _script().get_revision(REFERENCE).module
    for sens in (module.upgrade, module.downgrade):
        with pytest.raises(RuntimeError, match="RÉFÉRENCE du registre"):
            sens()


def test_aucune_revision_retiree_ne_revient():
    vivantes = {r.revision for r in _script().walk_revisions()}
    for squash in SQUASHS:
        assert not vivantes & squash.retirees, (
            f"révision(s) retirée(s) par le squash du {squash.date} revenue(s) au registre : "
            f"{sorted(vivantes & squash.retirees)} — une base qui les porte est refusée")
        assert squash.reference not in squash.retirees
