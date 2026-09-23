"""La table des droits (`org_entitlements`) : deux chemins de naissance, un seul DDL.

```
démarrage        le fragment `db/schema/entitlements.py`, dans l'assemblage
révision 0004    le MÊME fragment, exécuté par Alembic, jouée à la main (ADR 0065)
```

Ce banc joue la révision sur une vraie base ramenée à l'état d'avant ce lot : elle
pose vraiment la table (l'essai à blanc a déjà menti une fois,
`docs/migrations-versionnees.md` §5), elle se défait, et le démarrage la sait déjà
posée sans lever.
"""
from __future__ import annotations

from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parent.parent


def _alembic():
    from alembic.config import Config
    cfg = Config(str(RACINE / "alembic.ini"))
    cfg.set_main_option("script_location", str(RACINE / "oto_mcp" / "db" / "migrations"))
    return cfg


def _a_la_table(dsn: str) -> bool:
    import psycopg
    with psycopg.connect(dsn) as c:
        return c.execute("SELECT to_regclass('org_entitlements') IS NOT NULL AS t"
                         ).fetchone()[0]


@pytest.fixture(scope="module")
def base_d_avant(live, pg_module_dsn):
    """Une base bootée, ramenée à l'état d'avant ce lot, au registre de la 0003."""
    import psycopg
    from alembic import command
    assert _a_la_table(pg_module_dsn), "une base NEUVE la reçoit du démarrage"
    with psycopg.connect(pg_module_dsn, autocommit=True) as c:
        c.execute("DROP TABLE org_entitlements")
    command.stamp(_alembic(), "0003_runner_fleets_preneur")
    assert not _a_la_table(pg_module_dsn), "le point de départ doit être celui d'avant"
    return pg_module_dsn


def test_la_revision_pose_la_table_et_se_defait(base_d_avant):
    from alembic import command
    cfg = _alembic()
    command.upgrade(cfg, "0004_org_entitlements")
    assert _a_la_table(base_d_avant), "la révision n'a rien écrit"
    command.downgrade(cfg, "0003_runner_fleets_preneur")
    assert not _a_la_table(base_d_avant), "le retour arrière n'a rien retiré"
    command.upgrade(cfg, "head")
    assert _a_la_table(base_d_avant)


def test_le_demarrage_apres_la_revision_est_un_no_op(base_d_avant):
    from oto_mcp.db import init_db
    init_db()
    assert _a_la_table(base_d_avant)
