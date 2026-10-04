"""`email_send(cc=…)` : des copies visibles, sur les trois transports et dans la file
des envois différés. Chaque transport a SON nom de champ — le banc tient les trois."""
from __future__ import annotations

import asyncio
import os
import sys
import types
import uuid
from datetime import datetime, timezone

import pytest
from mcp.shared.exceptions import McpError

from oto_mcp import email as E


def _fake_httpx(monkeypatch) -> dict:
    captured = {}

    class _Resp:
        status_code = 200
        text = ""

    monkeypatch.setitem(sys.modules, "httpx", types.SimpleNamespace(
        post=lambda url, headers=None, json=None, timeout=None: captured.update(json=json) or _Resp()))
    return captured


# ── les transports ──────────────────────────────────────────────────────────

def test_mailer_emet_cc_et_neutralise_crlf(monkeypatch):
    monkeypatch.setenv("OTO_MAILER_SEND_BEARER", "tok")
    captured = _fake_httpx(monkeypatch)
    assert E._send("a@x.fr", "s", "<p>x</p>", cc=["b@x.fr", "c@x.fr\r\nBcc: v@evil.com"])
    assert captured["json"]["cc"] == ["b@x.fr", "c@x.fr Bcc: v@evil.com"]


def test_mailer_sans_cc_n_emet_pas_la_cle(monkeypatch):
    monkeypatch.setenv("OTO_MAILER_SEND_BEARER", "tok")
    captured = _fake_httpx(monkeypatch)
    assert E._send("a@x.fr", "s", "<p>x</p>", cc=[])
    assert "cc" not in captured["json"]


def test_resend_emet_cc(monkeypatch):
    captured = _fake_httpx(monkeypatch)
    assert E.send_via_resend("a@x.fr", "s", "<p>x</p>", api_key="k",
                             from_email="f@x.fr", cc=["b@x.fr"])
    assert captured["json"]["cc"] == ["b@x.fr"]


def test_scaleway_tem_emet_cc_en_objets(monkeypatch):
    captured = _fake_httpx(monkeypatch)
    assert E.send_via_scaleway_tem("a@x.fr", "s", "<p>x</p>", secret_key="k",
                                   project_id="p", from_email="f@x.fr", cc=["b@x.fr"])
    assert captured["json"]["cc"] == [{"email": "b@x.fr"}]


# ── l'outil : les refus précèdent la route ───────────────────────────────────

@pytest.fixture
def outil():
    from fastmcp import FastMCP
    from oto_mcp.tools import email as T
    m = FastMCP("t")
    T.register(m)
    return asyncio.run(m.get_tool("email_send"))


def _envoyer(outil, **kw):
    base = dict(ctx=None, to="prospect@ailleurs.test", subject="objet", body="bonjour")
    base.update(kw)
    return outil.fn(**base)


@pytest.mark.parametrize("cc", [["pas-une-adresse"], ["a@x.fr, b@x.fr"],
                                ["a@x.fr\nBcc: v@evil.com"]])
def test_une_copie_invalide_est_refusee(outil, cc):
    with pytest.raises(McpError, match="`cc`"):
        _envoyer(outil, cc=cc)


def test_trop_de_copies_est_refuse(outil):
    with pytest.raises(McpError, match="10 adresses au plus"):
        _envoyer(outil, cc=[f"c{i}@x.fr" for i in range(11)])


def test_copies_dedoublonnees_et_sans_le_destinataire(outil, monkeypatch):
    from oto_mcp.tools import email as T
    route = {"org_id": 1, "connector": "scaleway", "transport": "scaleway",
             "from_email": "hello@org.test", "from_name": None, "reply_to": None,
             "quiet_hours": None, "footer": None}
    monkeypatch.setattr(T, "_resolve_route", lambda f: ("logto:x", route))
    monkeypatch.setattr(T.config, "front_for", lambda sub: (None, None))
    monkeypatch.setattr(T.access, "resolve_credential_fields",
                        lambda p: {"secret_key": "k", "project_id": "p"})
    parti = {}
    monkeypatch.setattr(T.mailer, "send_via_scaleway_tem",
                        lambda to, subject, html, **kw: parti.update(kw) or True)
    out = _envoyer(outil, force_now=True,
                   cc=[" b@x.fr ", "B@x.fr", "Prospect@ailleurs.test", "c@x.fr"])
    assert out["cc"] == ["b@x.fr", "c@x.fr"] and parti["cc"] == ["b@x.fr", "c@x.fr"]


# ── la file : les copies survivent au différé ────────────────────────────────

@pytest.fixture(scope="module")
def live(pg_module_dsn):
    pytest.importorskip("psycopg")
    url_avant = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = pg_module_dsn
    try:
        from oto_mcp.db import init_db
        init_db()
        yield
    finally:
        if url_avant is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = url_avant


def test_la_file_garde_les_copies_jusqu_a_l_envoi(live, monkeypatch):
    from oto_mcp import db, org_store, scheduler
    oid = org_store.create_org(f"cc-{uuid.uuid4().hex[:8]}", created_by="logto:cc")
    sid = db.enqueue_scheduled_email(
        org_id=oid, created_by="logto:cc", to_email="a@x.fr", subject="s",
        body_html="<p>x</p>", from_email="hello@org.test", from_name=None,
        reply_to=None, transport="mailer", scheduled_at=datetime.now(timezone.utc),
        cc=["b@x.fr"])
    rangee = next(r for r in db.list_scheduled_emails(oid) if r["id"] == sid)
    assert rangee["cc"] == ["b@x.fr"]
    from oto_mcp.capabilities.scheduled_emails import ScheduledEmail
    assert ScheduledEmail.model_validate(
        {k: (str(v) if hasattr(v, "isoformat") else v) for k, v in rangee.items()}).cc == ["b@x.fr"]
    ligne = next(r for r in db.claim_due_scheduled_emails(500) if r["id"] == sid)
    parti = {}
    monkeypatch.setattr(scheduler.email, "_send",
                        lambda to, subject, html, **kw: parti.update(kw) or True)
    scheduler._send_one(ligne)
    assert parti["cc"] == ["b@x.fr"]
