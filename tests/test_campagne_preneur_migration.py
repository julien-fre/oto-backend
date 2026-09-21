"""La colonne du preneur (`runner_fleets.taken_by`) : deux chemins de naissance, et le
second passe par la révision Alembic, jamais par le démarrage.

```
base NEUVE       la colonne naît du CREATE TABLE (`db/schema/runs.py`)
base EXISTANTE   le CREATE TABLE est sauté ; elle vient de la révision
                 `0003_runner_fleets_preneur`, jouée À LA MAIN (ADR 0065)
```

Ce banc joue le second chemin sur une vraie base PostgreSQL, ramenée à l'état de la
production d'avant ce lot. Il vérifie ce qu'on ne voit pas en lisant la révision : que
le boot ne la pose PAS (un `ALTER` au démarrage prend son verrou exclusif à chaque
déploiement, sur la base partagée préprod/prod — l'incident du 18/09), que la révision
la pose vraiment (l'essai à blanc a déjà menti une fois, `docs/migrations-versionnees.md`
§5), et qu'elle se défait.
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


def _a_la_colonne(dsn: str) -> bool:
    import psycopg
    with psycopg.connect(dsn) as c:
        return c.execute(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = 'runner_fleets' AND column_name = 'taken_by'"
        ).fetchone() is not None


@pytest.fixture(scope="module")
def base_d_avant(live, pg_module_dsn):
    """Une base bootée, ramenée à l'état de la production avant ce lot."""
    import psycopg
    assert _a_la_colonne(pg_module_dsn), "une base NEUVE la reçoit du CREATE TABLE"
    with psycopg.connect(pg_module_dsn, autocommit=True) as c:
        c.execute("ALTER TABLE runner_fleets DROP COLUMN taken_by")
    assert not _a_la_colonne(pg_module_dsn), "le point de départ doit être celui d'avant"
    return pg_module_dsn


def test_le_boot_ne_pose_pas_la_colonne(base_d_avant):
    """Hors du démarrage, par décision : sur une base existante, redémarrer ne suffit
    pas — c'est la révision qui la pose, et elle se joue avant la fusion."""
    from oto_mcp.db import init_db
    init_db()
    assert not _a_la_colonne(base_d_avant)


def test_la_revision_pose_la_colonne_et_se_defait(base_d_avant):
    from alembic import command
    cfg = _alembic()
    command.upgrade(cfg, "0003_runner_fleets_preneur")
    assert _a_la_colonne(base_d_avant), "la révision n'a rien écrit"
    command.downgrade(cfg, "0002_runner_jobs_index_vivant")
    assert not _a_la_colonne(base_d_avant), "le retour arrière n'a rien retiré"
    command.upgrade(cfg, "head")
    assert _a_la_colonne(base_d_avant)
