"""La colonne d'origine d'une suspension d'org (`orgs.suspended_tenant_id`, #1165) : deux
chemins de naissance, et le second passe par la révision Alembic, jamais par le
démarrage.

```
base NEUVE       la colonne naît du CREATE TABLE (`db/schema/orgs.py`)
base EXISTANTE   le CREATE TABLE est sauté ; elle vient de la révision
                 `0042_orgs_suspension_par_tenant`, jouée À LA MAIN (ADR 0065)
```

La précédente est la RÉFÉRENCE du registre (squash, docs/migrations-versionnees.md §5.4) :
une base estampillée là, sans la colonne, est exactement une base vivante restée à la
référence — elle monte à la tête par cette révision.
"""
from __future__ import annotations

from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parent.parent
_PRECEDENTE = "0041_recherche_valeurs_servies"
_REVISION = "0042_orgs_suspension_par_tenant"


def _alembic():
    from alembic.config import Config
    cfg = Config(str(RACINE / "alembic.ini"))
    cfg.set_main_option("script_location", str(RACINE / "oto_mcp" / "db" / "migrations"))
    return cfg


def _colonne(dsn: str) -> bool:
    import psycopg
    with psycopg.connect(dsn) as c:
        return c.execute(
            "SELECT 1 FROM information_schema.columns WHERE table_name = 'orgs' "
            "AND column_name = 'suspended_tenant_id'").fetchone() is not None


@pytest.fixture(scope="module")
def base_d_avant(live, pg_module_dsn):
    """Une base bootée, ramenée à l'état de la production avant ce lot."""
    import psycopg
    assert _colonne(pg_module_dsn), "une base NEUVE la reçoit du CREATE TABLE"
    with psycopg.connect(pg_module_dsn, autocommit=True) as c:
        c.execute("ALTER TABLE orgs DROP COLUMN suspended_tenant_id")
    assert not _colonne(pg_module_dsn), "le point de départ doit être celui d'avant"
    from alembic import command
    command.stamp(_alembic(), _PRECEDENTE)
    return pg_module_dsn


def test_le_boot_ne_pose_pas_la_colonne(base_d_avant):
    from oto_mcp.db import init_db
    init_db()
    assert not _colonne(base_d_avant)


def test_la_revision_pose_la_colonne_et_se_defait(base_d_avant):
    from alembic import command
    cfg = _alembic()
    command.upgrade(cfg, _REVISION)
    assert _colonne(base_d_avant), "la révision n'a rien écrit"
    command.downgrade(cfg, _PRECEDENTE)
    assert not _colonne(base_d_avant), "le retour arrière n'a rien retiré"
    command.upgrade(cfg, "head")
    assert _colonne(base_d_avant)
