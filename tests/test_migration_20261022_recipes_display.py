"""La révision `20261022_2100_recipes_display` pose `recipes.display` (22/10/2026).

Ce qu'une page de processus montre à l'endroit où il lance une recette. Une colonne
NEUVE, nullable sans défaut, que le démarrage pose aussi (même DDL) : la révision doit
la poser sur une base d'AVANT (qui ne l'a pas), ne rien faire sur une base que le
démarrage a déjà servie, et la retirer au retour arrière. Joué sur une vraie base.
"""
from __future__ import annotations

from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parent.parent
_PRECEDENTE = "20261021_1352_outils_act_envois"
_REVISION = "20261022_2100_recipes_display"


def _alembic():
    from alembic.config import Config
    cfg = Config(str(RACINE / "alembic.ini"))
    cfg.set_main_option("script_location", str(RACINE / "oto_mcp" / "db" / "migrations"))
    return cfg


def _colonne(dsn):
    import psycopg
    with psycopg.connect(dsn) as c:
        return c.execute(
            "SELECT data_type, is_nullable, column_default FROM information_schema.columns "
            "WHERE table_name = 'recipes' AND column_name = 'display'"
        ).fetchall()


@pytest.fixture(scope="module")
def base_d_avant(live, pg_module_dsn):
    """Une base bootée, ramenée à l'état d'avant : colonne retirée, estampillée à la
    révision précédente."""
    import psycopg
    from alembic import command

    with psycopg.connect(pg_module_dsn, autocommit=True) as c:
        c.execute("ALTER TABLE recipes DROP COLUMN display")
    command.stamp(_alembic(), _PRECEDENTE)
    return pg_module_dsn


def test_la_revision_pose_la_colonne_et_le_retour_arriere_la_retire(base_d_avant):
    from alembic import command

    assert _colonne(base_d_avant) == []
    command.upgrade(_alembic(), _REVISION)
    assert _colonne(base_d_avant) == [("jsonb", "YES", None)]
    command.downgrade(_alembic(), _PRECEDENTE)
    assert _colonne(base_d_avant) == []
    command.upgrade(_alembic(), _REVISION)
    assert _colonne(base_d_avant) == [("jsonb", "YES", None)]


def test_rejouee_sur_une_base_que_le_demarrage_a_servie_elle_ne_fait_rien(base_d_avant):
    """Ordre indifférent avec le code : le démarrage a pu poser la colonne avant."""
    from alembic import command

    command.upgrade(_alembic(), _REVISION)
    assert _colonne(base_d_avant), "la colonne est déjà là"
    command.stamp(_alembic(), _PRECEDENTE)
    command.upgrade(_alembic(), _REVISION)   # la colonne est là : IF NOT EXISTS
    assert _colonne(base_d_avant) == [("jsonb", "YES", None)]
