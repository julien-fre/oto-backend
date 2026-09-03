"""Une échéance qui porte des microsecondes doit PARTIR quand même.

⚠️ Le défaut d'origine ne produisait aucune erreur. Le compare-and-swap du tick
comparait `next_due` à **la valeur relue par le tick** — or toute date lue passe
par `_normalize_value`, qui retire **les microsecondes et le fuseau**. Le `WHERE`
ne matchait alors jamais, `consume_due` rendait `False`, et le tick lisait ça
comme « un pair a déjà consommé cette échéance » : le cas normal quand deux
environnements partagent la base.

Résultat : **le déclencheur reste éternellement dû**, sélectionné à chaque tour,
jamais consommé, jamais enfilé — avec l'air parfaitement sain.

Ça ne se produisait pas en pratique parce que croniter rend des secondes rondes.
*Une garantie qui tient par la propriété d'une bibliothèque tierce n'est pas une
garantie* — et c'est exactement ce que ce banc éprouve.
"""
from __future__ import annotations

import datetime
import uuid

import pytest


@pytest.fixture(scope="module")
def live(pg_dsn):
    import os

    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    nom = "oto_echeance_" + uuid.uuid4().hex[:8]
    root = psycopg.connect(pg_dsn, autocommit=True)
    root.execute(f'CREATE DATABASE "{nom}"')
    dsn = pg_dsn.rsplit("/", 1)[0] + "/" + nom
    url_avant, pool_avant = os.environ.get("DATABASE_URL"), dbconn._pool
    os.environ["DATABASE_URL"] = dsn
    dbconn._pool = None
    try:
        from oto_mcp.db import init_db
        init_db()
        yield dsn
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


@pytest.fixture(scope="module")
def org(live):
    from oto_mcp import db, org_store
    membre = "usr_echeance"
    db.upsert_user(membre, email=f"{membre}@ech.invalid", name=membre)
    oid = org_store.create_org("Org des échéances", created_by=membre)
    org_store.add_org_member(oid, membre, "org_admin")
    return {"id": oid, "membre": membre}


def _pose(org, quand, label):
    from oto_mcp import db
    return db.create_trigger(org["id"], org["membre"], procedure="veille",
                             cron="0 18 * * *", tz="Europe/Paris",
                             next_due=quand, tools=["data_write"], label=label)


def test_une_echeance_a_MICROSECONDES_est_consommee(live, org):
    """⚠️ LE test. Avant le correctif, ce déclencheur restait dû pour toujours —
    sans erreur, sans avertissement, avec l'air sain."""
    from oto_mcp import db, runner_tick

    passe = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=5)
    t = _pose(org, passe.replace(microsecond=482137), "avec microsecondes")

    assert t["id"] in [d["id"] for d in db.due_triggers(limit=100)], "pas même vu dû"
    assert db.consume_due(t["id"], t["next_due"],
                          passe + datetime.timedelta(days=1)) is True, (
        "l'échéance n'a pas été consommée : le déclencheur restera dû pour "
        "toujours, sans qu'aucune erreur ne le signale")


def test_une_seconde_consommation_ECHOUE(live, org):
    """L'exclusion mutuelle est intacte : deux ticks, un seul gagnant. Sans ce
    contrôle, le correctif remplacerait un déclencheur muet par un DOUBLON à
    chaque tour — un défaut bien plus cher."""
    from oto_mcp import db

    passe = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=5)
    t = _pose(org, passe.replace(microsecond=0), "double consommation")
    futur = passe + datetime.timedelta(days=1)

    assert db.consume_due(t["id"], t["next_due"], futur) is True
    assert db.consume_due(t["id"], t["next_due"], futur) is False, (
        "consommée deux fois : chaque échéance produirait DEUX exécutions")


def test_une_echeance_FUTURE_n_est_pas_consommee(live, org):
    """Le contrôle symétrique : le verrou porte sur l'éligibilité, donc ce qui
    n'est pas encore dû ne doit pas partir."""
    from oto_mcp import db

    futur = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=3)
    t = _pose(org, futur.replace(microsecond=0), "pas encore due")
    assert db.consume_due(t["id"], t["next_due"],
                          futur + datetime.timedelta(days=1)) is False


def test_un_declencheur_ETEINT_n_est_pas_consomme(live, org):
    """Éteint veut dire éteint, même dû : sinon rallumer n'aurait aucun sens."""
    from oto_mcp import db

    passe = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=5)
    t = _pose(org, passe.replace(microsecond=0), "éteint mais dû")
    db.update_trigger(t["id"], org["id"], {"enabled": False})
    assert db.consume_due(t["id"], t["next_due"],
                          passe + datetime.timedelta(days=1)) is False
