"""La révision `20261029_1200_pret_du_forfait` pose `runner_triggers.updated_by` et
`runner_triggers.forfait_prete_model`.

Deux colonnes NEUVES, nullables sans défaut, que le démarrage pose aussi : la révision
doit les poser sur une base d'AVANT, ne rien faire sur une base que le démarrage a déjà
servie, ne remplir AUCUN prêt (rien de rétroactif), et les retirer au retour arrière.
Joué sur une vraie base.
"""
from __future__ import annotations

from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parent.parent
_PRECEDENTE = "20261028_1900_access_requests"
_REVISION = "20261029_1200_pret_du_forfait"
_COLONNES = ("forfait_prete_model", "updated_by")


def _alembic():
    from alembic.config import Config
    cfg = Config(str(RACINE / "alembic.ini"))
    cfg.set_main_option("script_location", str(RACINE / "oto_mcp" / "db" / "migrations"))
    return cfg


def _colonnes(dsn):
    import psycopg
    with psycopg.connect(dsn) as c:
        return c.execute(
            "SELECT column_name, data_type, is_nullable, column_default "
            "FROM information_schema.columns WHERE table_name = 'runner_triggers' "
            "AND column_name = ANY(%s) ORDER BY column_name", (list(_COLONNES),)
        ).fetchall()


_POSEES = [("forfait_prete_model", "text", "YES", None), ("updated_by", "text", "YES", None)]


@pytest.fixture(scope="module")
def base_d_avant(live, pg_module_dsn):
    """Une base bootée, ramenée à l'état d'avant : colonnes retirées, estampillée à la
    révision précédente, un agent d'avant sur abonnement avec un éditeur nommé."""
    import psycopg
    from alembic import command

    with psycopg.connect(pg_module_dsn, autocommit=True) as c:
        for col in _COLONNES:
            c.execute(f"ALTER TABLE runner_triggers DROP COLUMN {col}")
        tid = c.execute(
            "INSERT INTO runner_triggers (org_id, sub, procedure, tools, tz, model, enabled) "
            "VALUES (8390, 'proprio', 'veille', '[]'::jsonb, 'UTC', 'sub:sonnet', false) "
            "RETURNING id").fetchone()[0]
        c.execute(
            "INSERT INTO resource_grants (resource_type, resource_id, principal_type, "
            "principal_id, permission, role, granted_by) "
            "VALUES ('runner_trigger', %s, 'user', 'nomme', 'write', 'editor', 'proprio')",
            (str(tid),))
    command.stamp(_alembic(), _PRECEDENTE)
    return pg_module_dsn


def test_la_revision_pose_les_colonnes_sans_rien_preter_et_le_retour_arriere_les_retire(
        base_d_avant):
    import psycopg
    from alembic import command

    assert _colonnes(base_d_avant) == []
    command.upgrade(_alembic(), _REVISION)
    assert _colonnes(base_d_avant) == _POSEES
    with psycopg.connect(base_d_avant) as c:
        assert c.execute("SELECT count(*) FROM runner_triggers "
                         "WHERE forfait_prete_model IS NOT NULL").fetchone()[0] == 0, (
            "aucun partage existant ne prête sans le geste du titulaire")
    command.downgrade(_alembic(), _PRECEDENTE)
    assert _colonnes(base_d_avant) == []
    command.upgrade(_alembic(), _REVISION)
    assert _colonnes(base_d_avant) == _POSEES


def test_rejouee_sur_une_base_que_le_demarrage_a_servie_elle_ne_fait_rien(base_d_avant):
    """Ordre indifférent avec le code : le démarrage a pu poser les colonnes avant."""
    from alembic import command

    command.upgrade(_alembic(), _REVISION)
    assert _colonnes(base_d_avant), "les colonnes sont déjà là"
    command.stamp(_alembic(), _PRECEDENTE)
    command.upgrade(_alembic(), _REVISION)   # IF NOT EXISTS
    assert _colonnes(base_d_avant) == _POSEES
