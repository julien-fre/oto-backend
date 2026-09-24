"""Le démarrage pose la version Alembic d'une base NEUVE, et d'elle seule (#969).

Trois cas, contre un PostgreSQL réel, chacun sur sa base jetable :

1. **base neuve** — aucune table : le démarrage crée le schéma ET écrit la tête du
   registre dans `alembic_version`. Alembic, lu par son propre chemin (`env.py`), la
   voit à la tête : `upgrade head` n'y rejoue rien — c'est le scénario qui casse sans
   le stamp (les révisions rejouées sur un schéma déjà à jour) ;
2. **base existante sans version** — des tables, pas d'`alembic_version` : le
   démarrage ne devine pas, n'estampille pas, et le DIT en erreur ;
3. **base déjà versionnée** — même à une révision ancienne : le démarrage ne touche
   pas à sa version, et une révision que la base n'a pas reçue s'y applique encore.
"""
from __future__ import annotations

import logging
import os
import uuid
from contextlib import contextmanager
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

RACINE = Path(__file__).resolve().parent.parent
DEPART = "0001_point_de_depart"


@contextmanager
def _base_jetable(pg_dsn: str):
    """Une base vide À NOUS, pointée par `DATABASE_URL`, pool remis à neuf ; rend un
    ouvreur de connexion. Détruite à la sortie."""
    from oto_mcp.db import _conn as dbconn

    nom = "oto_version_" + uuid.uuid4().hex[:8]
    root = psycopg.connect(pg_dsn, autocommit=True)
    root.execute(f'CREATE DATABASE "{nom}"')
    dsn = pg_dsn.rsplit("/", 1)[0] + "/" + nom
    url_avant, pool_avant = os.environ.get("DATABASE_URL"), dbconn._pool
    os.environ["DATABASE_URL"] = dsn
    dbconn._pool = None
    try:
        yield lambda: psycopg.connect(dsn, autocommit=True)
    finally:
        if dbconn._pool is not None:
            dbconn._pool.close()
        dbconn._pool = pool_avant
        if url_avant is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = url_avant
        root.execute(f'DROP DATABASE IF EXISTS "{nom}" WITH (FORCE)')
        root.close()


def _version(ouvrir) -> list[str] | None:
    with ouvrir() as c:
        if c.execute("SELECT to_regclass('alembic_version')").fetchone()[0] is None:
            return None
        return [r[0] for r in c.execute("SELECT version_num FROM alembic_version")]


def _alembic(*commande: str) -> None:
    """Alembic par son VRAI chemin : `alembic.ini`, `env.py`, `DATABASE_URL`."""
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(RACINE / "alembic.ini"))
    cfg.set_main_option("script_location", str(RACINE / "oto_mcp" / "db" / "migrations"))
    getattr(command, commande[0])(cfg, *commande[1:])


def _courante_selon_alembic() -> str | None:
    from alembic.runtime.migration import MigrationContext
    from sqlalchemy import create_engine

    url = os.environ["DATABASE_URL"].replace("postgresql://", "postgresql+psycopg://", 1)
    moteur = create_engine(url)
    try:
        with moteur.connect() as c:
            return MigrationContext.configure(c).get_current_revision()
    finally:
        moteur.dispose()


def test_une_base_neuve_nait_a_la_tete_du_registre(pg_dsn, caplog):
    from oto_mcp.db import init_db
    from oto_mcp.db._version_alembic import tete_du_registre

    tete = tete_du_registre()
    assert tete != DEPART, "le banc suppose un registre de plus d'une révision"
    with _base_jetable(pg_dsn) as ouvrir:
        assert _version(ouvrir) is None
        with caplog.at_level(logging.INFO, logger="oto_mcp.db._version_alembic"):
            init_db()
        assert _version(ouvrir) == [tete]
        assert f"version posée à {tete}" in caplog.text
        # Alembic, lu par son propre chemin, la voit à la tête…
        assert _courante_selon_alembic() == tete
        # …donc `upgrade head` n'y rejoue AUCUNE révision : sans le stamp, il les
        # rejouerait toutes sur un schéma qui les porte déjà.
        _alembic("upgrade", "head")
        assert _version(ouvrir) == [tete]
        # Un second démarrage ne re-stampe pas : la base est désormais versionnée.
        caplog.clear()
        with caplog.at_level(logging.INFO, logger="oto_mcp.db._version_alembic"):
            init_db()
        assert "registre de migrations" not in caplog.text
        assert _version(ouvrir) == [tete]


def test_une_base_existante_sans_version_n_est_pas_estampillee_et_le_boot_le_dit(
        pg_dsn, caplog):
    from oto_mcp.db import init_db

    with _base_jetable(pg_dsn) as ouvrir:
        init_db()
        # L'état d'une base construite avant le registre : son schéma, sans version.
        with ouvrir() as c:
            c.execute("DROP TABLE alembic_version")
        with caplog.at_level(logging.ERROR, logger="oto_mcp.db._version_alembic"):
            init_db()
        assert _version(ouvrir) is None, "une base existante a été estampillée à l'aveugle"
        erreurs = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert len(erreurs) == 1 and "sans `alembic_version`" in erreurs[0].getMessage()


def test_une_base_versionnee_garde_sa_version_et_recoit_encore_ses_revisions(
        pg_dsn, caplog):
    from oto_mcp.db import init_db
    from oto_mcp.db._version_alembic import tete_du_registre

    with _base_jetable(pg_dsn) as ouvrir:
        init_db()
        # Une base ANCIENNE : versionnée au point de départ, révisions pas reçues.
        with ouvrir() as c:
            c.execute("UPDATE alembic_version SET version_num = %s", (DEPART,))
        with caplog.at_level(logging.INFO, logger="oto_mcp.db._version_alembic"):
            init_db()
        assert _version(ouvrir) == [DEPART], "le démarrage a touché une base versionnée"
        assert "registre de migrations" not in caplog.text
        # La révision suivante s'applique encore à elle — le stamp n'a rien masqué.
        _alembic("upgrade", "+1")
        assert _version(ouvrir) != [DEPART]
        assert _version(ouvrir) != [tete_du_registre()]
