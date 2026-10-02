"""Notion : jeton collé PUIS « Connecter avec Notion », contre un vrai PostgreSQL.

Ce que les stubs de `tests/auth/test_notion_oauth.py` ne peuvent pas prouver : la
cascade RÉELLE sert le jeton du consentement (défaut posé) sans ambiguïté, alors qu'un
jeton collé existe déjà au même palier — et `rc.account` nomme ce compte, sans quoi le
renouvellement à l'usage ne trouverait jamais sa ligne.
"""
from __future__ import annotations

import base64
import os
import uuid

import pytest

from oto_mcp import access, credentials_store as cs
from oto_mcp.auth import notion as n

ORG = 4343
SUB = "usr_notion"
_KEY = base64.b64encode(b"\x22" * 32).decode()
TOKENS = {"access_token": "ntn_oauth", "refresh_token": "ntn_refresh",
          "workspace_id": "ws-1", "workspace_name": "Acme", "expires_in": 3600}


def _exec(sql, params=()):
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        conn.execute(sql, params)


@pytest.fixture(scope="module")
def live(pg_dsn):
    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    name = "oto_notion_" + uuid.uuid4().hex[:8]
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
        _exec("INSERT INTO orgs (id, name) VALUES (%s, 'o') ON CONFLICT DO NOTHING", (ORG,))
        _exec("INSERT INTO users (sub) VALUES (%s) ON CONFLICT DO NOTHING", (SUB,))
        yield
    finally:
        if dbconn._pool is not None:
            dbconn._pool.close()
        dbconn._pool = avant_pool
        for cle, valeur in (("DATABASE_URL", avant_url), ("OTO_MCP_MASTER_KEY", avant_key)):
            if valeur is None:
                os.environ.pop(cle, None)
            else:
                os.environ[cle] = valeur
        root.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        root.close()


@pytest.fixture
def contexte(live, monkeypatch):
    _exec("DELETE FROM connector_credentials")
    monkeypatch.setattr(access, "current_org", lambda sub: ORG)
    monkeypatch.setattr(access, "current_group", lambda sub: None)
    monkeypatch.setattr(access, "project_pinned_instance", lambda p: None)


MEMBRE = (cs.MEMBER, cs.member_id(ORG, SUB))


def test_le_consentement_devient_le_defaut_a_cote_du_jeton_colle(contexte):
    cs.set_credential(*MEMBRE, "notion", "ntn_colle", set_by=SUB)
    assert access.resolve_credential("notion", sub=SUB).key == "ntn_colle"

    compte = n.persist(SUB, ORG, "member", None, n.PLATFORM_APP_KEY, TOKENS)

    rc = access.resolve_credential("notion", sub=SUB)
    assert (rc.key, rc.account) == ("ntn_oauth", compte)
    # Le jeton collé n'est pas détruit : renommé, et toujours désignable.
    comptes = {r["account"] for r in cs.list_accounts(*MEMBRE, "notion")}
    assert comptes == {"principal", "Acme"}
    assert access.resolve_credential("notion", sub=SUB, account="principal").key == "ntn_colle"


def test_le_refresh_chiffre_se_relit_apres_un_renommage(contexte, monkeypatch):
    """L'AAD du refresh token est liée à l'entité, pas au compte : il survit au
    passage '' → « principal » que provoque un deuxième compte."""
    from oto_mcp.auth import flow as oauth_flow
    monkeypatch.setattr(cs, "get_editor_app", lambda c, k: {"client_id": "i",
                                                            "client_secret": "s"})
    n.persist(SUB, ORG, "member", None, n.PLATFORM_APP_KEY, {**TOKENS, "expires_in": 0})
    cs.rename_account(*MEMBRE, "notion", "Acme", "Renomme")
    vu = {}

    class _R:
        status_code = 200

        def json(self):
            return {"access_token": "frais", "refresh_token": "rote", "expires_in": 3600}
    monkeypatch.setattr(oauth_flow.requests, "post",
                        lambda url, **kw: (vu.update(kw=kw), _R())[1])
    rc = access.resolve_credential("notion", sub=SUB)
    assert n.fresh_key(rc) == "frais"
    assert vu["kw"]["json"]["refresh_token"] == "ntn_refresh"
    assert access.resolve_credential("notion", sub=SUB).key == "frais"
