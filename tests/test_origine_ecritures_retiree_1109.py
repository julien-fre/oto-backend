"""`origine_ecritures` retirée par une révision (oto-backend#1109).

Le relevé qui comptait les écrivains de la couche `origine` pendant le préavis d'oto#70
a fini de servir. Le refus d'écrire l'origine sans la déclarer reste ; la table part, par
la révision `0048_origine_ecritures_retiree` :

```
base NEUVE              le démarrage ne crée plus la table ; la révision ne fait rien
base qui a la table     la révision la retire ; le démarrage ne la repose pas
base déjà nettoyée      la révision ne fait rien, sans prendre de verrou
```

La garde sans base tient le premier point : plus aucun code servi ne nomme la table.
"""
from __future__ import annotations

from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parent.parent
AVANT, REVISION = "0047_cles_d_org", "0048_origine_ecritures_retiree"

# Le DDL de la table telle que la production la porte avant ce lot (index compris).
_DDL_D_AVANT = """
CREATE TABLE origine_ecritures (
    sub TEXT, org_id BIGINT, ns_id BIGINT NOT NULL, colonne TEXT NOT NULL, face TEXT,
    format_declare BOOLEAN NOT NULL DEFAULT false, ecritures BIGINT NOT NULL DEFAULT 1,
    ecritures_declarees BIGINT NOT NULL DEFAULT 0, derniere_declaree_at TIMESTAMPTZ,
    premiere_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    derniere_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE UNIQUE INDEX idx_origine_ecritures_qui
    ON origine_ecritures (COALESCE(sub, ''), ns_id, colonne);
CREATE INDEX idx_origine_ecritures_fraicheur ON origine_ecritures (derniere_at DESC);
"""


# ── Sans base ─────────────────────────────────────────────────────────────────

def test_plus_aucun_code_servi_ne_nomme_la_table():
    """Hors du registre des révisions, la table n'existe plus : ni dans le DDL du
    démarrage (une base neuve la recréerait), ni dans un ordre, un module ou un
    classement d'export."""
    porteurs = sorted(
        p.relative_to(RACINE).as_posix()
        for p in (RACINE / "oto_mcp").rglob("*.py")
        if "/migrations/" not in p.as_posix()
        and "origine_ecritures" in p.read_text(encoding="utf-8"))
    assert not porteurs, f"`origine_ecritures` encore nommée hors des révisions : {porteurs}"


# ── Sur une vraie base ────────────────────────────────────────────────────────

def _alembic():
    from alembic.config import Config
    cfg = Config(str(RACINE / "alembic.ini"))
    cfg.set_main_option("script_location", str(RACINE / "oto_mcp" / "db" / "migrations"))
    return cfg


def _a_la_table(dsn: str) -> bool:
    import psycopg
    with psycopg.connect(dsn) as c:
        return c.execute("SELECT to_regclass('origine_ecritures') IS NOT NULL"
                         ).fetchone()[0]


def _version(dsn: str) -> str:
    import psycopg
    with psycopg.connect(dsn) as c:
        return c.execute("SELECT version_num FROM alembic_version").fetchone()[0]


def test_une_base_neuve_n_a_pas_la_table_et_la_revision_ne_fait_rien(live, pg_module_dsn):
    from alembic import command
    assert not _a_la_table(pg_module_dsn), "le démarrage ne doit plus créer la table"
    cfg = _alembic()
    command.stamp(cfg, AVANT)
    command.upgrade(cfg, REVISION)
    assert not _a_la_table(pg_module_dsn)
    assert _version(pg_module_dsn) == REVISION


def test_une_base_qui_a_la_table_la_perd_et_le_demarrage_ne_la_repose_pas(
        live, pg_module_dsn):
    import psycopg
    from alembic import command

    from oto_mcp.db import init_db
    with psycopg.connect(pg_module_dsn, autocommit=True) as c:
        c.execute(_DDL_D_AVANT)
        c.execute("INSERT INTO origine_ecritures (sub, ns_id, colonne) VALUES ('s', 1, 'c')")
    assert _a_la_table(pg_module_dsn), "le point de départ est celui d'une base d'avant"
    cfg = _alembic()
    command.stamp(cfg, AVANT)
    command.upgrade(cfg, REVISION)
    assert not _a_la_table(pg_module_dsn), "la révision n'a rien retiré"
    assert _version(pg_module_dsn) == REVISION
    init_db()
    assert not _a_la_table(pg_module_dsn), "le démarrage a reposé la table"
    # Rejouée sur la base nettoyée : rien à faire, rien ne lève.
    command.stamp(cfg, AVANT)
    command.upgrade(cfg, REVISION)
    assert not _a_la_table(pg_module_dsn)


def test_le_retour_arriere_leve_et_ne_touche_a_rien(live, pg_module_dsn):
    from alembic import command
    cfg = _alembic()
    command.stamp(cfg, REVISION)
    with pytest.raises(RuntimeError, match="irréversible"):
        command.downgrade(cfg, AVANT)
    assert _version(pg_module_dsn) == REVISION
    assert not _a_la_table(pg_module_dsn)
