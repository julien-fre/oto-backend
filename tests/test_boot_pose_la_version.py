"""Le démarrage pose la version Alembic d'une base NEUVE, et d'elle seule (#969).

Trois cas, contre un PostgreSQL réel, chacun sur sa base jetable :

1. **base neuve** — aucune table : le démarrage crée le schéma ET écrit la tête du
   registre dans `alembic_version`. Alembic, lu par son propre chemin (`env.py`), la
   voit à la tête : `upgrade head` n'y rejoue rien — c'est le scénario qui casse sans
   le stamp (les révisions rejouées sur un schéma déjà à jour) ;
2. **base existante sans version** — des tables, pas d'`alembic_version` : le
   démarrage ne devine pas, n'estampille pas, et le DIT en erreur ;
3. **base déjà versionnée** — à la référence du registre : le démarrage ne touche
   pas à sa version, et la révision que la base n'a pas reçue s'y applique encore ;
4. **base antérieure à la référence** — une révision retirée par le squash
   (docs/migrations-versionnees.md §5.4) : le démarrage la REFUSE en la nommant, et
   Alembic (`env.py`) comme `oto-mcp migrer` aussi, avant leur « Can't locate
   revision » ; une révision inconnue mais NON retirée (base plus récente que le code)
   ne bloque pas le démarrage.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

from _base_jetable import base_jetable as _base_jetable  # noqa: E402

RACINE = Path(__file__).resolve().parent.parent
RETIREE = "0039_feed_synced_at_retiree"


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

    from oto_mcp.db._version_alembic import REFERENCE

    tete = tete_du_registre()
    assert tete != REFERENCE, "le banc suppose un registre de plus d'une révision"
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


def test_une_base_a_la_reference_garde_sa_version_et_monte_a_la_tete(pg_dsn, caplog):
    from oto_mcp.db import init_db
    from oto_mcp.db._version_alembic import REFERENCE, tete_du_registre

    with _base_jetable(pg_dsn) as ouvrir:
        init_db()
        # Une base restée à la référence : la révision suivante pas reçue.
        with ouvrir() as c:
            c.execute("UPDATE alembic_version SET version_num = %s", (REFERENCE,))
        with caplog.at_level(logging.INFO, logger="oto_mcp.db._version_alembic"):
            init_db()
        assert _version(ouvrir) == [REFERENCE], "le démarrage a touché une base versionnée"
        assert "registre de migrations" not in caplog.text
        # La révision suivante s'applique encore à elle — le stamp n'a rien masqué.
        _alembic("upgrade", "head")
        assert _version(ouvrir) == [tete_du_registre()]


def test_une_base_a_une_revision_retiree_est_refusee_nommement(pg_dsn, monkeypatch):
    from alembic.util import CommandError

    from oto_mcp import migrer
    from oto_mcp.db import init_db
    from oto_mcp.db._version_alembic import BaseAnterieureALaReference

    attendu = (f"révision {RETIREE} antérieure à la référence "
               "0041_recherche_valeurs_servies (squash du 06/10/2026) : monter d'abord cette "
               "base avec un tag antérieur au squash (v1.441.0)")
    with _base_jetable(pg_dsn) as ouvrir:
        init_db()
        with ouvrir() as c:
            c.execute("UPDATE alembic_version SET version_num = %s", (RETIREE,))
        # Le démarrage refuse, avant son premier ordre.
        with pytest.raises(BaseAnterieureALaReference) as refus:
            init_db()
        assert attendu in str(refus.value)
        # Alembic, par son propre chemin : le refus nommé, jamais « Can't locate revision ».
        for commande in (("upgrade", "head"), ("current",), ("downgrade", "-1"),
                         ("stamp", "head")):
            try:
                _alembic(*commande)
            except BaseAnterieureALaReference as e:
                assert attendu in str(e), commande
            except CommandError as e:
                pytest.fail(f"{commande} : Alembic a levé sans nommer — {e}")
            else:
                pytest.fail(f"{commande} : une base antérieure à la référence est passée")
        # `oto-mcp migrer` sort en le disant.
        with pytest.raises(SystemExit) as sortie:
            migrer.main(["upgrade", "head"])
        assert attendu in str(sortie.value.code)
        assert _version(ouvrir) == [RETIREE], "la version d'une base refusée a bougé"


def test_une_revision_inconnue_non_retiree_ne_bloque_pas_le_demarrage(pg_dsn):
    """Une base plus récente que le code (migrée par le tag suivant, sur la base
    partagée) : le démarrage du code qui sert continue — le refuser casserait le
    bleu/vert. `deploy/cible/migrations_a_jour.py` la dit, lui, avant une montée."""
    from oto_mcp.db import init_db

    with _base_jetable(pg_dsn) as ouvrir:
        init_db()
        with ouvrir() as c:
            c.execute("UPDATE alembic_version SET version_num = '9999_revision_du_futur'")
        init_db()
        assert _version(ouvrir) == ["9999_revision_du_futur"]
