"""oto-backend#701 : `upsert_user(locale=...)` ne pose la langue déduite que si la
ligne n'en porte encore aucune — un `PUT /api/me/locale` (`me.locale.set`) reste
prioritaire à vie. C'est un COALESCE dans le `ON CONFLICT DO UPDATE` : un stub qui
rejoue lui-même la logique ne prouverait rien (il redirait juste le code), donc vrai
PostgreSQL, comme le reste des tests DB de ce module (cf. `docs/commands.md`).

`pg_dsn` (conftest) : `OTO_TEST_PG_DSN`, sinon un conteneur jetable, sinon skip.
"""
from __future__ import annotations

import base64
import os
import uuid

import pytest

_KEY = base64.b64encode(b"\x22" * 32).decode()


@pytest.fixture(scope="module")
def live(pg_dsn):
    """Une base JETABLE, le VRAI `init_db()`, le vrai pool — cf. la recette de
    `test_connector_instances_birth_live.py` (ne jamais rejouer `init_db()` sur le
    conteneur PARTAGÉ, session-scopé : ~67 tables y feraient rougir des tests sans
    rapport)."""
    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    name = "oto_locale701_" + uuid.uuid4().hex[:8]
    root = psycopg.connect(pg_dsn, autocommit=True)
    root.execute(f'CREATE DATABASE "{name}"')
    dsn = pg_dsn.rsplit("/", 1)[0] + "/" + name

    avant_url, avant_pool = os.environ.get("DATABASE_URL"), dbconn._pool
    avant_key = os.environ.get("OTO_MCP_MASTER_KEY")
    os.environ["DATABASE_URL"] = dsn
    os.environ["OTO_MCP_MASTER_KEY"] = _KEY
    dbconn._pool = None
    try:
        from oto_mcp.db import init_db
        init_db()
        yield
    finally:
        if dbconn._pool is not None:
            dbconn._pool.close()
        dbconn._pool = avant_pool
        for cle, valeur in (("DATABASE_URL", avant_url),
                            ("OTO_MCP_MASTER_KEY", avant_key)):
            if valeur is None:
                os.environ.pop(cle, None)
            else:
                os.environ[cle] = valeur
        root.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        root.close()


def _locale(sub: str):
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        row = conn.execute("SELECT locale FROM users WHERE sub = %s", (sub,)).fetchone()
        return row["locale"] if row else None


@pytest.fixture(autouse=True)
def table_rase(live):
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        conn.execute("DELETE FROM users")


def test_signup_sans_locale_deduite_reste_null(live):
    """Les 14 autres sites d'appel (`upsert_user(sub)`, sans `locale=`) : le premier
    insert pose NULL, exactement comme avant #701."""
    from oto_mcp.db.users import upsert_user
    upsert_user("u-1", email="a@b.c")
    assert _locale("u-1") is None


def test_premier_login_rest_pose_la_locale_deduite(live):
    """1er login REST (Accept-Language: fr) : la ligne naît directement avec `fr`."""
    from oto_mcp.db.users import upsert_user
    upsert_user("u-1", email="a@b.c", locale="fr")
    assert _locale("u-1") == "fr"


def test_login_suivant_ne_change_pas_une_locale_deja_deduite(live):
    """Deux logins REST avec des navigateurs différents : le premier gagne, le
    second n'écrase rien — la ligne n'a jamais vu de choix EXPLICITE ici, seulement
    des déductions, et pourtant la première déduction tient."""
    from oto_mcp.db.users import upsert_user
    upsert_user("u-1", email="a@b.c", locale="fr")
    upsert_user("u-1", email="a@b.c", locale="en")
    assert _locale("u-1") == "fr"


def test_choix_explicite_survit_a_tous_les_logins_suivants(live):
    """Le cœur de la garde : un `me.locale.set` (ici simulé par `set_user_locale`,
    la fonction que la capacité appelle) reste prioritaire à VIE face à n'importe
    quelle déduction ultérieure — y compris si le navigateur suggère l'autre langue."""
    from oto_mcp.db.users import set_user_locale, upsert_user
    upsert_user("u-1", email="a@b.c")            # login initial, sans signal
    set_user_locale("u-1", "en")                 # choix explicite (PUT /api/me/locale)
    upsert_user("u-1", email="a@b.c", locale="fr")  # login suivant, navigateur FR
    assert _locale("u-1") == "en"


def test_absence_de_signal_ne_pose_toujours_rien_sur_une_ligne_existante(live):
    """Un chemin qui n'a pas de `Request` (MCP, invite) rappelle `upsert_user` sans
    `locale=` : ça ne doit ni écraser une valeur posée, ni en fabriquer une."""
    from oto_mcp.db.users import upsert_user
    upsert_user("u-1", email="a@b.c", locale="fr")
    upsert_user("u-1", email="a@b.c")             # pas de locale= (comportement historique)
    assert _locale("u-1") == "fr"
