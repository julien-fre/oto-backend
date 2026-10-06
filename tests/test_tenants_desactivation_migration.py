"""Les colonnes de la désactivation d'un tenant (`tenants.disabled_*`, #1165) : deux
chemins de naissance, et le second passe par la révision Alembic, jamais par le
démarrage.

```
base NEUVE       les colonnes naissent du CREATE TABLE (`db/schema/tenants.py`)
base EXISTANTE   le CREATE TABLE est sauté ; elles viennent de la révision
                 `0040_tenants_desactivation`, jouée À LA MAIN (ADR 0065)
```

Même banc que `test_jetons_revocation_migration.py`.
"""
from __future__ import annotations

from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parent.parent
_COLONNES = {"disabled_at", "disabled_by", "disabled_reason"}


def _alembic():
    from alembic.config import Config
    cfg = Config(str(RACINE / "alembic.ini"))
    cfg.set_main_option("script_location", str(RACINE / "oto_mcp" / "db" / "migrations"))
    return cfg


def _colonnes(dsn: str) -> set:
    import psycopg
    with psycopg.connect(dsn) as c:
        return {r[0] for r in c.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'tenants' AND column_name = ANY(%s)",
            (list(_COLONNES),)).fetchall()}


@pytest.fixture(scope="module")
def base_d_avant(live, pg_module_dsn):
    """Une base bootée, ramenée à l'état de la production avant ce lot."""
    import psycopg
    assert _colonnes(pg_module_dsn) == _COLONNES, "une base NEUVE les reçoit du CREATE TABLE"
    with psycopg.connect(pg_module_dsn, autocommit=True) as c:
        c.execute("ALTER TABLE tenants DROP COLUMN disabled_at, "
                  "DROP COLUMN disabled_by, DROP COLUMN disabled_reason")
    assert not _colonnes(pg_module_dsn), "le point de départ doit être celui d'avant"
    from alembic import command
    command.stamp(_alembic(), "0039_feed_synced_at_retiree")
    return pg_module_dsn


def test_le_boot_ne_pose_pas_les_colonnes(base_d_avant):
    from oto_mcp.db import init_db
    init_db()
    assert not _colonnes(base_d_avant)


def test_la_revision_pose_les_colonnes_et_se_defait(base_d_avant):
    from alembic import command
    cfg = _alembic()
    command.upgrade(cfg, "0040_tenants_desactivation")
    assert _colonnes(base_d_avant) == _COLONNES, "la révision n'a rien écrit"
    command.downgrade(cfg, "0039_feed_synced_at_retiree")
    assert not _colonnes(base_d_avant), "le retour arrière n'a rien retiré"
    command.upgrade(cfg, "head")
    assert _colonnes(base_d_avant) == _COLONNES
