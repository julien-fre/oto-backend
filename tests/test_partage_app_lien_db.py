"""Partager une APP mène À l'app : le mail d'un compte existant pointe son adresse
(`/org/<org>/apps/<id>` quand le front la déclare), et l'invitée sans compte qui accepte
reçoit de quoi y arriver (`resource_org_id`). Contre un PostgreSQL réel."""
from __future__ import annotations

import uuid

import pytest

CHEMINS = '{"app": "/org/{org}/apps/{id}"}'


@pytest.fixture
def monde(live, monkeypatch):
    from oto_mcp import db, links, org_store
    from oto_mcp.auth import annuaire
    from oto_mcp.capabilities import resources as R
    monkeypatch.setenv(links.ENV_INSTANCE_PATHS, CHEMINS)
    monkeypatch.setenv("OTO_INVITE_BASE_URL", "https://front.example.test")
    monkeypatch.setattr(annuaire, "magic_url", lambda url, email, **_: url)
    envois: list[dict] = []
    monkeypatch.setattr(R.email, "send_resource_shared_email",
                        lambda to, **kw: envois.append({"to": to, **kw}) or True)
    u = uuid.uuid4().hex[:8]
    owner, collegue = f"owner_{u}", f"collegue_{u}"
    db.upsert_user(owner, email=f"{owner}@example.test")
    db.upsert_user(collegue, email=f"{collegue}@example.test")
    org = org_store.create_org(f"a_{u}", created_by=owner)
    org_store.add_org_member(org, owner, "org_admin")
    org_store.add_org_member(org, collegue, "org_member")
    app = db.apps.create(("user", owner), org, title=f"Inbox {u}", manifest={}, html="",
                         thumbnail=None, by=owner)
    return {"owner": owner, "collegue": collegue, "org": org, "app": app["id"],
            "envois": envois, "u": u, "invitee": f"kenza_{u}@example.test"}


def _partager(m, **kw):
    from oto_mcp.capabilities import resources as R
    from oto_mcp.capabilities._types import ResolvedCtx
    return R._resources(ResolvedCtx(sub=m["owner"], org_id=m["org"]),
                        R.ResourceInput(op="share", resource_type="app",
                                        resource_id=str(m["app"]), audience="person", **kw))


def test_le_mail_d_un_compte_existant_ouvre_l_app(monde):
    from oto_mcp import config
    m = monde
    _partager(m, email=f"{m['collegue']}@example.test", role="viewer")
    (envoi,) = m["envois"]
    assert envoi["app_url"] == f"{config.dashboard_url()}/org/{m['org']}/apps/{m['app']}"
    assert envoi["type_label"] == "app"


def test_sans_chemin_declare_le_mail_garde_la_racine(monde, monkeypatch):
    from oto_mcp import config, links
    m = monde
    monkeypatch.delenv(links.ENV_INSTANCE_PATHS, raising=False)
    _partager(m, email=f"{m['collegue']}@example.test", role="viewer")
    assert m["envois"][0]["app_url"] == config.dashboard_url()


def test_l_invitee_sans_compte_arrive_sur_l_app(monde):
    from oto_mcp import db, ownership
    from oto_mcp.capabilities._types import ResolvedCtx
    from oto_mcp.capabilities.orgs import invites
    m = monde
    _partager(m, email=m["invitee"], role="viewer")
    token = m["envois"][0]["app_url"].rsplit("/", 1)[1]
    sub = f"new_{m['u']}"
    db.upsert_user(sub, email=m["invitee"])
    r = invites._invite_accept(ResolvedCtx(sub=sub, org_id=None),
                               invites.InviteAcceptInput(token=token))
    assert (r["resource_type"], r["resource_id"], r["resource_org_id"]) == (
        "app", str(m["app"]), m["org"])
    assert ownership.can_access(sub, "app", str(m["app"]))
