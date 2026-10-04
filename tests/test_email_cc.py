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
from oto_mcp.mcp_errors import McpError

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


@pytest.mark.parametrize("cc", [["Bob <b@x.fr>"], ["b c@x.fr"], ["b@x"], ["b@@x.fr"],
                                ["b@x.fr;c@x.fr"], [""]])
def test_une_copie_hors_forme_stricte_est_refusee(outil, cc):
    with pytest.raises(McpError, match="adresse invalide"):
        _envoyer(outil, cc=cc)


def test_trop_de_copies_est_refuse(outil):
    with pytest.raises(McpError, match="10 adresses au plus"):
        _envoyer(outil, cc=[f"c{i}@x.fr" for i in range(11)])


def test_la_liste_brute_est_bornee_avant_le_dedoublonnage(outil):
    """Mille fois la même adresse ne se réduit pas à une seule copie : la borne porte
    sur ce que l'appel a envoyé, pas sur ce qu'il en reste."""
    with pytest.raises(McpError, match="10 adresses au plus"):
        _envoyer(outil, cc=["b@x.fr"] * 1000)


def test_copies_et_desabonnement_nominatif_s_excluent():
    with pytest.raises(ValueError, match="s'excluent"):
        E.send_composed_email("a@x.fr", "s", "corps", cc=["b@x.fr"],
                              unsubscribe_url="https://x.test/d?t=1")


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


# ── le plafond quotidien du transport commun ─────────────────────────────────

_ROUTE_COMMUNE = {"org_id": None, "connector": None, "transport": "mailer",
                  "from_email": None, "from_name": None, "reply_to": None,
                  "quiet_hours": None, "footer": None}


@pytest.fixture
def commun(monkeypatch):
    """Un envoi sur le transport commun, le journal du jour simulé à `deja`."""
    from oto_mcp.tools import email as T
    etat = {"deja": 0, "lu": [], "trace": {}, "parti": []}
    monkeypatch.setattr(T, "_resolve_route", lambda f: ("logto:sa", dict(_ROUTE_COMMUNE)))
    monkeypatch.setattr(T.config, "front_for", lambda sub: (None, None))
    monkeypatch.setattr(T.access, "current_org", lambda sub: 42)
    monkeypatch.setattr(T.mailer, "_mail_from", lambda: "oto@instance.test")

    def _lu(*, org_id, sub):
        etat["lu"].append((org_id, sub))
        return etat["deja"]
    monkeypatch.setattr(T.db, "destinataires_communs_du_jour", _lu)
    monkeypatch.setattr(T.session_org, "note_call_trace", lambda **kw: etat["trace"].update(kw))
    monkeypatch.setattr(T.mailer, "send_composed_email",
                        lambda to, subject, body, **kw: etat["parti"].append(kw) or True)
    monkeypatch.delenv("OTO_EMAIL_PLATFORM_DAILY_RECIPIENTS", raising=False)
    return etat


def test_le_plafond_commun_refuse_au_dela_nomme_et_rejouable(outil, commun):
    commun["deja"] = 195
    with pytest.raises(McpError) as e:
        _envoyer(outil, force_now=True, cc=[f"c{i}@x.fr" for i in range(5)])
    assert e.value.error.data == {"code": "platform_email_daily_cap", "retryable": True,
                                  "limit": 200, "used": 195, "units": 6}
    assert "minuit UTC" in e.value.error.message
    assert commun["lu"] == [(42, "logto:sa")] and not commun["parti"]


def test_le_plafond_commun_laisse_passer_et_metre_les_destinataires(outil, commun):
    commun["deja"] = 194
    out = _envoyer(outil, force_now=True, cc=[f"c{i}@x.fr" for i in range(5)])
    assert out["sent"] and commun["trace"] == {"quantity": 6, "key_mode": "platform"}


def test_un_envoi_differe_compte_le_jour_ou_il_est_programme(outil, commun, monkeypatch):
    from oto_mcp.tools import email as T
    monkeypatch.setattr(T.db, "enqueue_scheduled_email", lambda **kw: 7)
    out = _envoyer(outil, send_at="2099-01-01T08:00", cc=["b@x.fr"])
    assert out["scheduled"] and commun["trace"] == {"quantity": 2, "key_mode": "platform"}


def test_le_plafond_commun_se_regle_par_instance(outil, commun, monkeypatch):
    monkeypatch.setenv("OTO_EMAIL_PLATFORM_DAILY_RECIPIENTS", "3")
    commun["deja"] = 1
    with pytest.raises(McpError, match="1/3"):
        _envoyer(outil, force_now=True, cc=["b@x.fr", "c@x.fr"])


@pytest.mark.parametrize("valeur", ["beaucoup", "-1"])
def test_un_plafond_illisible_leve(outil, commun, monkeypatch, valeur):
    monkeypatch.setenv("OTO_EMAIL_PLATFORM_DAILY_RECIPIENTS", valeur)
    with pytest.raises(RuntimeError, match="OTO_EMAIL_PLATFORM_DAILY_RECIPIENTS"):
        _envoyer(outil, force_now=True)


def test_la_cle_de_l_org_n_a_aucun_plafond_de_plateforme(outil, monkeypatch):
    from oto_mcp.tools import email as T
    route = {"org_id": 1, "connector": "scaleway", "transport": "scaleway",
             "from_email": "hello@org.test", "from_name": None, "reply_to": None,
             "quiet_hours": None, "footer": None}
    monkeypatch.setattr(T, "_resolve_route", lambda f: ("logto:x", route))
    monkeypatch.setattr(T.config, "front_for", lambda sub: (None, None))
    monkeypatch.setattr(T.access, "resolve_credential_fields",
                        lambda p: {"secret_key": "k", "project_id": "p"})
    monkeypatch.setattr(T.mailer, "send_via_scaleway_tem", lambda *a, **kw: True)

    def _jamais(**kw):
        raise AssertionError("le plafond commun lu sur la clé de l'org")
    monkeypatch.setattr(T.db, "destinataires_communs_du_jour", _jamais)
    trace = {}
    monkeypatch.setattr(T.session_org, "note_call_trace", lambda **kw: trace.update(kw))
    assert _envoyer(outil, force_now=True, cc=[f"c{i}@x.fr" for i in range(10)])["sent"]
    assert "key_mode" not in trace


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


def test_le_compte_du_jour_lit_le_journal_de_l_org(live):
    """Seuls comptent les `email_send` réussis, passés sur la clé commune, aujourd'hui
    (UTC), sous l'org — `quantity` NULL valant 1, comme pour tout consommateur."""
    from oto_mcp import db
    from oto_mcp.db._conn import _connect
    org = 900000 + uuid.uuid4().int % 99999
    sub = f"logto:cap-{uuid.uuid4().hex[:8]}"
    lignes = [  # (org_id, sub, tool, ok, key_mode, quantity, il y a)
        (org, sub, "email_send", True, "platform", 6, "0 seconds"),
        (org, "logto:autre", "email_send", True, "platform", None, "0 seconds"),
        (org, sub, "email_send", False, "platform", 4, "0 seconds"),
        (org, sub, "email_send", True, "org", 9, "0 seconds"),
        (org, sub, "serper_search", True, "platform", 9, "0 seconds"),
        (org, sub, "email_send", True, "platform", 9, "2 days"),
        (None, sub, "email_send", True, "platform", 3, "0 seconds"),
    ]
    with _connect() as conn:
        for o, s_, t, ok, km, q, age in lignes:
            conn.execute(
                "INSERT INTO tool_calls (org_id, sub, tool, ok, key_mode, quantity, kind, "
                "created_at) VALUES (%s, %s, %s, %s, %s, %s, 'mcp', NOW() - %s::interval)",
                (o, s_, t, ok, km, q, age))
    assert db.destinataires_communs_du_jour(org_id=org, sub=sub) == 7
    assert db.destinataires_communs_du_jour(org_id=None, sub=sub) == 3
