"""Fixtures partagées.

`pg_dsn` — un PostgreSQL RÉEL, pour les rares tests qui n'ont de valeur que là :
une contrainte (la PK que viole un renommage naïf, #295) ou un opérateur JSONB
(`data - key`, qui efface là où `null` conserve, #296) ne s'exerce pas contre un
stub. Le reste de la suite reste sans base — la convention du repo est de tester
la logique pure et les gardes par stub, le chemin SQL étant vérifié au déploiement.

Source, dans l'ordre : `OTO_TEST_PG_DSN`, sinon un conteneur jetable si `docker`
répond, sinon `skip`. Session-scopé : un seul conteneur pour toute la suite.

Le conteneur ne doit rien laisser derrière lui (#640, `_pg_hygiene.py`) : il est
étiqueté et daté, son `PGDATA` est un tmpfs (aucun volume), sa sortie est couverte
par `atexit` + SIGTERM/SIGINT en plus du finalizer, et chaque session commence par
balayer ce qu'une session tuée a laissé (`pytest_sessionstart`).

Ce fichier porte aussi le **forçage du pin oto-core** (`_oto_core_pin.py`) : quand
le venv n'exécute pas le tag qu'épingle le manifeste, la suite le DIT en bannière
— aux deux bouts du run — au lieu de laisser des rouges fidèles au venv passer
pour des rouges du dépôt. ⚠️ **La bannière ne survit pas à un `| grep passed`**
(#790, elle est écrite à côté de la ligne qui contient ce mot) : c'est pourquoi
`pytest_report_teststatus` ci-dessous range EN PLUS ces skips sous une catégorie
parlante, directement dans le résumé final de pytest — la ligne qui, elle,
survit au filtre parce qu'elle contient déjà « passed ».
"""
from __future__ import annotations

import os
import subprocess
import time
import uuid
from functools import lru_cache
from typing import Iterator, NamedTuple, Optional

import pytest

from _oto_core_pin import (MARQUEUR, categorie_non_concluante, ecart,
                           lignes_de_banniere, skips_autorises)
from _pg_hygiene import Guard, docker_available, run_args, sweep_orphans


# --------------------------------------------------------------------------- #
# Pin oto-core : le venv exécute-t-il ce que le tronc épingle ?
# --------------------------------------------------------------------------- #
#
# Sept sessions ont enquêté sur le même faux rouge le 01/09/2026, dont une qui a
# conclu « le tronc est rouge, plus aucune PR ne peut entrer » pendant que la CI
# était verte. La doc décrivait déjà le piège — donc ce n'est pas la doc qui
# manquait, c'est le forçage. Le voici.


@lru_cache(maxsize=1)
def _ecart_de_session():
    """Mesuré une fois par run. `.cache_clear()` pour les tests du garde-fou."""
    return ecart()


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        f"{MARQUEUR}: ce test n'a de SENS que face à l'oto-core épinglé — il est "
        "passé (non concluant) en local quand le venv est en retard sur le pin, "
        "et reste mordant en CI.")


def pytest_collection_modifyitems(config: pytest.Config, items) -> None:
    """Un rouge qui ne prouve rien vaut moins qu'un test explicitement non
    concluant — mais SEULEMENT en local : en CI la garde version-skew doit mordre,
    c'est tout son objet (cf. `skips_autorises`)."""
    config.stash_oto_core_skips = 0            # type: ignore[attr-defined]
    e = _ecart_de_session()
    if e is None or not skips_autorises():
        return
    marque = pytest.mark.skip(
        reason=f"oto-core installé ({e.installe or 'aucun'}) ≠ épinglé "
               f"({e.epingle}) — non concluant dans cet environnement")
    vises = [item for item in items if item.get_closest_marker(MARQUEUR)]
    for item in vises:
        item.add_marker(marque)
    config.stash_oto_core_skips = len(vises)   # type: ignore[attr-defined]


def pytest_report_teststatus(report: pytest.TestReport, config: pytest.Config
                              ) -> tuple[str, str, str] | None:
    """#790 — le nombre survit déjà à `| grep passed` (il vit dans la MÊME ligne
    que ce mot) ; ce qui lui manquait, c'est une phrase. On ne rajoute pas une
    ligne (filtrable, comme la bannière) : on renomme la CATÉGORIE sous laquelle
    pytest compte ces skips précis, donc le nom change directement dans le
    résumé final que pytest imprime de toute façon.

    Seuls les skips posés par `pytest_collection_modifyitems` ci-dessus
    (marqueur `MARQUEUR`, phase setup) migrent vers cette catégorie — un skip
    ORDINAIRE (docker absent, etc.) reste compté sous « skipped » : le but est
    de séparer les deux effectifs, pas de maquiller l'un en l'autre.
    """
    if report.when != "setup" or not report.skipped:
        return None
    if MARQUEUR not in report.keywords:
        return None
    e = _ecart_de_session()
    if e is None:
        return None
    return categorie_non_concluante(e), "s", "NON CONCLUANT"


def _ecrire_banniere(reporter, config) -> None:
    e = _ecart_de_session()
    if e is None or reporter is None:
        return
    skips = getattr(config, "stash_oto_core_skips", 0)
    reporter.write_sep("=", "PIN oto-core", red=True, bold=True)
    for ligne in lignes_de_banniere(e, skips=skips):
        reporter.write_line(ligne, red=True, bold=ligne.startswith("oto-core"))


class PgBox(NamedTuple):
    dsn: str
    container: Optional[str]   # None quand la base vient d'`OTO_TEST_PG_DSN`


def pytest_sessionstart(session: pytest.Session) -> None:
    """Le balai (#640) : un conteneur `oto-test=1` de plus de deux heures est un orphelin
    d'une session morte sans finalizer. On le dit, une ligne par conteneur.

    Et la bannière du pin : la voir AVANT le run évite d'attendre la fin pour
    apprendre qu'on mesurait le mauvais oto-core."""
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    _ecrire_banniere(reporter, session.config)
    lines = sweep_orphans(time.time())
    if not lines:
        return
    for line in lines:
        if reporter is not None:
            reporter.write_line(line)
        else:
            print(line)


def pytest_terminal_summary(terminalreporter, exitstatus, config) -> None:
    """La MÊME bannière en fin de run — et c'est celle-ci qui compte.

    Une ligne juste écrite là où personne ne regarde est exactement le mode de
    panne qu'on ferme : au démarrage, la bannière a défilé depuis longtemps quand
    les `FAILED` s'affichent. Ici elle atterrit contre eux, au moment précis où on
    se demande à qui sont ces rouges."""
    _ecrire_banniere(terminalreporter, config)


@pytest.fixture(scope="session")
def pg_box() -> Iterator[PgBox]:
    dsn = os.environ.get("OTO_TEST_PG_DSN")
    if dsn:
        yield PgBox(dsn, None)
        return
    if not docker_available():
        pytest.skip("aucun PostgreSQL joignable (ni OTO_TEST_PG_DSN, ni docker)")
    name = f"oto-test-pg-{uuid.uuid4().hex[:8]}"
    subprocess.run(run_args(name), capture_output=True, check=True)
    guard = Guard(name)
    guard.install()
    try:
        port = subprocess.run(
            ["docker", "port", name, "5432/tcp"],
            capture_output=True, text=True, check=True).stdout.strip().rsplit(":", 1)[1]
        dsn = f"postgresql://postgres:test@127.0.0.1:{port}/postgres"
        # L'attente se fait avec L'INSTRUMENT DU TEST — une vraie connexion depuis
        # l'hôte. `pg_isready` dans le conteneur répond OK pendant la phase d'INIT
        # de l'image postgres (serveur temporaire, socket locale), puis le serveur
        # redémarre : les premiers tests tombaient alors sur « server closed the
        # connection unexpectedly ». Un sondage qui n'emprunte pas le chemin du test
        # ne prouve pas que le chemin du test est prêt.
        psycopg = pytest.importorskip("psycopg")
        deadline = time.time() + 60
        while True:
            try:
                with psycopg.connect(dsn, connect_timeout=3) as c:
                    c.execute("SELECT 1")
                break
            except Exception:
                if time.time() > deadline:
                    pytest.skip("le PostgreSQL jetable n'est pas devenu prêt")
                time.sleep(1)
        yield PgBox(dsn, name)
    finally:
        guard.remove()
        guard.uninstall()


@pytest.fixture(scope="session")
def pg_dsn(pg_box: PgBox) -> str:
    return pg_box.dsn
