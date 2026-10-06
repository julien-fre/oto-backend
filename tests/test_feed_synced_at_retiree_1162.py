"""`unipile_accounts.feed_synced_at` retirée par une révision, plus par un geste à la main
(oto-backend#1162).

Le `DROP` de cette colonne morte n'était écrit nulle part : un commentaire de démarrage
le confiait à l'exploitation, qui l'a joué sur la base partagée. Une base née avant ce
geste l'a gardée, et un import de périmètre vers elle a refusé des « colonnes
différentes ». La révision `0039_feed_synced_at_retiree` rejoue le retrait :

```
base NEUVE              le CREATE TABLE ne déclare pas la colonne ; la révision ne fait rien
base qui a la colonne   la révision la retire ; le démarrage ne la repose pas
base déjà nettoyée      la révision ne fait rien, sans prendre de verrou
```

La garde sans base tient le premier point : plus aucun code servi ne nomme la colonne.
"""
from __future__ import annotations

from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parent.parent
AVANT, REVISION = "0038_abandon_run", "0039_feed_synced_at_retiree"


# ── Sans base ─────────────────────────────────────────────────────────────────

def test_plus_aucun_code_servi_ne_nomme_la_colonne():
    """Hors du registre des révisions, la colonne n'existe plus : ni dans le `CREATE
    TABLE` (une base neuve la recréerait), ni dans un ordre ou un commentaire de
    démarrage (un commentaire n'est pas la trace d'un DDL)."""
    porteurs = sorted(
        p.relative_to(RACINE).as_posix()
        for p in (RACINE / "oto_mcp").rglob("*.py")
        if "/migrations/" not in p.as_posix()
        and "feed_synced_at" in p.read_text(encoding="utf-8"))
    assert not porteurs, f"`feed_synced_at` encore nommée hors des révisions : {porteurs}"


# ── Sur une vraie base ────────────────────────────────────────────────────────

def _alembic():
    from alembic.config import Config
    cfg = Config(str(RACINE / "alembic.ini"))
    cfg.set_main_option("script_location", str(RACINE / "oto_mcp" / "db" / "migrations"))
    return cfg


def _a_la_colonne(dsn: str) -> bool:
    import psycopg
    with psycopg.connect(dsn) as c:
        return c.execute(
            "SELECT 1 FROM information_schema.columns WHERE table_name = "
            "'unipile_accounts' AND column_name = 'feed_synced_at'").fetchone() is not None


def _version(dsn: str) -> str:
    import psycopg
    with psycopg.connect(dsn) as c:
        return c.execute("SELECT version_num FROM alembic_version").fetchone()[0]


def test_une_base_neuve_n_a_pas_la_colonne_et_la_revision_n_y_prend_aucun_verrou(
        live, pg_module_dsn):
    """Une lecture tenue sur `unipile_accounts` : un `DROP COLUMN` attendrait son
    verrou exclusif et tomberait sur `lock_timeout`. La révision passe : elle n'a rien
    demandé."""
    import psycopg
    from alembic import command
    assert not _a_la_colonne(pg_module_dsn), "le CREATE TABLE ne doit plus la déclarer"
    cfg = _alembic()
    command.stamp(cfg, AVANT)
    with psycopg.connect(pg_module_dsn) as lecteur:
        lecteur.execute("LOCK TABLE unipile_accounts IN ACCESS SHARE MODE")
        command.upgrade(cfg, REVISION)
        lecteur.rollback()
    assert not _a_la_colonne(pg_module_dsn)
    assert _version(pg_module_dsn) == REVISION


def test_une_base_qui_a_la_colonne_la_perd_et_le_demarrage_ne_la_repose_pas(
        live, pg_module_dsn):
    import psycopg
    from alembic import command

    from oto_mcp.db import init_db
    with psycopg.connect(pg_module_dsn, autocommit=True) as c:
        c.execute("ALTER TABLE unipile_accounts ADD COLUMN feed_synced_at TIMESTAMPTZ")
    assert _a_la_colonne(pg_module_dsn), "le point de départ est celui d'une base d'avant"
    cfg = _alembic()
    command.stamp(cfg, AVANT)
    command.upgrade(cfg, REVISION)
    assert not _a_la_colonne(pg_module_dsn), "la révision n'a rien retiré"
    assert _version(pg_module_dsn) == REVISION
    init_db()
    assert not _a_la_colonne(pg_module_dsn), "le démarrage a reposé la colonne"
    # Rejouée sur la base nettoyée : rien à faire, rien ne lève.
    command.stamp(cfg, AVANT)
    command.upgrade(cfg, REVISION)
    assert not _a_la_colonne(pg_module_dsn)


def test_le_retour_arriere_leve_et_ne_touche_a_rien(live, pg_module_dsn):
    from alembic import command
    cfg = _alembic()
    command.stamp(cfg, REVISION)
    with pytest.raises(RuntimeError, match="irréversible"):
        command.downgrade(cfg, AVANT)
    assert _version(pg_module_dsn) == REVISION
    assert not _a_la_colonne(pg_module_dsn)
