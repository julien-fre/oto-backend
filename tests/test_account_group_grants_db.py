"""Grants de compte connecteur ciblant un GROUPE (extension otomata-private#55) —
exercés en SQL réel.

Les tests capacité (`test_account_grants_capability.py`) mockent `db.*` : ils ne
peuvent pas prouver que les requêtes elles-mêmes (les deux `UNION ALL` de
`granted_accounts_for`/`list_account_grants_to`, la table `connector_account_group_grants`)
sont correctes contre le VRAI schéma. Ce fichier les exerce sur une base Postgres
éphémère (patron `test_connector_cardinality_override.py::live`)."""
from __future__ import annotations

import os
import uuid

import pytest


@pytest.fixture(scope="module")
def live(pg_dsn):
    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    name = "oto_grpgrant_" + uuid.uuid4().hex[:8]
    root = psycopg.connect(pg_dsn, autocommit=True)
    root.execute(f'CREATE DATABASE "{name}"')
    dsn = pg_dsn.rsplit("/", 1)[0] + "/" + name

    avant_url, avant_pool = os.environ.get("DATABASE_URL"), dbconn._pool
    avant_key = os.environ.get("OTO_MCP_MASTER_KEY")
    os.environ["DATABASE_URL"] = dsn
    os.environ["OTO_MCP_MASTER_KEY"] = "4" * 64
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


@pytest.fixture
def scenario(live):
    """Un owner avec un LinkedIn connecté dans son org, un groupe de cette org, et
    deux users potentiellement bénéficiaires — un seul dedans, un dehors. Noms
    uniques par test : la base module-scope survit d'un test à l'autre."""
    from oto_mcp import db, group_store, org_store
    uniq = uuid.uuid4().hex[:8]
    owner, member_in, member_out = f"owner_{uniq}", f"in_{uniq}", f"out_{uniq}"
    for sub in (owner, member_in, member_out):
        db.upsert_user(sub)
    org = org_store.create_org(f"org_{uniq}", created_by=owner)
    db.set_unipile_account(owner, "ACC_LIVE", "Avatar", org_id=org, provider="LINKEDIN")
    group = group_store.create_group(org, f"group_{uniq}")
    group_store.add_group_member(group, member_in)
    return {"owner": owner, "member_in": member_in, "member_out": member_out,
            "org": org, "group": group}


def test_group_grant_reaches_current_members_only(scenario):
    from oto_mcp import db
    s = scenario
    db.set_account_group_grant(s["owner"], "LINKEDIN", "ACC_LIVE", s["group"],
                               granted_by=s["owner"])
    reached = db.granted_accounts_for(s["member_in"], "LINKEDIN")
    assert reached == {"ACC_LIVE": {"owner_sub": s["owner"], "owner_email": None}}
    # Le user hors du groupe n'a rien reçu du même grant.
    assert db.granted_accounts_for(s["member_out"], "LINKEDIN") == {}


def test_leaving_the_group_revokes_immediately(scenario):
    from oto_mcp import db, group_store
    s = scenario
    db.set_account_group_grant(s["owner"], "LINKEDIN", "ACC_LIVE", s["group"],
                               granted_by=s["owner"])
    assert db.granted_accounts_for(s["member_in"], "LINKEDIN") != {}
    group_store.remove_group_member(s["group"], s["member_in"])
    # Live, pas un snapshot : partir du groupe suffit, rien à révoquer côté grant.
    assert db.granted_accounts_for(s["member_in"], "LINKEDIN") == {}


def test_list_account_grants_to_reports_the_group_provenance(scenario):
    from oto_mcp import db, group_store
    s = scenario
    db.set_account_group_grant(s["owner"], "LINKEDIN", "ACC_LIVE", s["group"],
                               granted_by=s["owner"])
    rows = db.list_account_grants_to(s["member_in"])
    assert len(rows) == 1
    row = rows[0]
    assert row["account_id"] == "ACC_LIVE"
    assert row["via_group_id"] == s["group"]
    assert row["via_group_name"] == group_store.get_group(s["group"])["name"]
    assert row["active"] is True


def test_disconnecting_the_owner_makes_the_group_grant_inert(scenario):
    from oto_mcp import db
    s = scenario
    db.set_account_group_grant(s["owner"], "LINKEDIN", "ACC_LIVE", s["group"],
                               granted_by=s["owner"])
    db.clear_unipile_account(s["owner"], s["org"], "LINKEDIN")
    assert db.granted_accounts_for(s["member_in"], "LINKEDIN") == {}
    row = db.list_account_grants_to(s["member_in"])[0]
    assert row["active"] is False


def test_revoking_the_group_grant_is_idempotent(scenario):
    from oto_mcp import db
    s = scenario
    assert db.clear_account_group_grant(s["owner"], "LINKEDIN", s["group"]) is False
    db.set_account_group_grant(s["owner"], "LINKEDIN", "ACC_LIVE", s["group"],
                               granted_by=s["owner"])
    assert db.clear_account_group_grant(s["owner"], "LINKEDIN", s["group"]) is True
    assert db.clear_account_group_grant(s["owner"], "LINKEDIN", s["group"]) is False


def test_list_account_group_grants_by_owner(scenario):
    from oto_mcp import db, group_store
    s = scenario
    db.set_account_group_grant(s["owner"], "LINKEDIN", "ACC_LIVE", s["group"],
                               granted_by=s["owner"])
    rows = db.list_account_group_grants_by_owner(s["owner"])
    assert len(rows) == 1
    assert rows[0]["grantee_group_id"] == s["group"]
    assert rows[0]["grantee_group_name"] == group_store.get_group(s["group"])["name"]
    assert rows[0]["active"] is True


def test_disconnecting_the_owner_makes_a_nominative_grant_inert_too(scenario):
    """Régression : ce fix (`disconnected_at IS NULL` manquant sur les jointures
    `unipile_accounts`) touchait le grant NOMINATIF d'origine, pas seulement
    l'extension groupe — jamais couvert avant ce lot (aucun test DB réel
    n'existait pour `db/connector_grants.py`)."""
    from oto_mcp import db
    s = scenario
    db.set_account_grant(s["owner"], "LINKEDIN", "ACC_LIVE", s["member_out"],
                         granted_by=s["owner"])
    assert db.granted_accounts_for(s["member_out"], "LINKEDIN") != {}
    db.clear_unipile_account(s["owner"], s["org"], "LINKEDIN")
    assert db.granted_accounts_for(s["member_out"], "LINKEDIN") == {}
    row = db.list_account_grants_by_owner(s["owner"])[0]
    assert row["active"] is False and row["account_id"] is None


def test_group_and_nominative_grants_coexist_without_leaking(scenario):
    """Le UNION ALL ne mélange pas les deux mécanismes : chacun ne voit QUE ce qui
    lui revient — le témoin du lot (une fusion bâclée ferait fuir l'un chez l'autre)."""
    from oto_mcp import db
    s = scenario
    db.set_account_grant(s["owner"], "LINKEDIN", "ACC_LIVE", s["member_out"],
                         granted_by=s["owner"])
    db.set_account_group_grant(s["owner"], "LINKEDIN", "ACC_LIVE", s["group"],
                               granted_by=s["owner"])
    assert len(db.list_account_grants_by_owner(s["owner"])) == 1
    assert len(db.list_account_group_grants_by_owner(s["owner"])) == 1
    assert db.granted_accounts_for(s["member_in"], "LINKEDIN") != {}
    assert db.granted_accounts_for(s["member_out"], "LINKEDIN") != {}
    to_in = db.list_account_grants_to(s["member_in"])
    to_out = db.list_account_grants_to(s["member_out"])
    assert len(to_in) == 1 and to_in[0]["via_group_id"] == s["group"]
    assert len(to_out) == 1 and to_out[0]["via_group_id"] is None
