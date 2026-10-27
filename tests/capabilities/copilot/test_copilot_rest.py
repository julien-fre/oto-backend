"""The in-app agent ON THE SERVED ROUTES, against a real PostgreSQL and the real tool
catalogue — only the model is scripted (no model call leaves the test).

What is proven here, end to end: the gate (403 off the allowlist), the event stream
served by the capability adapter, a write that waits for its card and runs once
approved — in-process, under the conversation's run and the viewed org —, the Undo read
back from the revision journal (and refusing a cell changed since), a conversation that
another account cannot answer, and an answer that is not for what waits.
"""
from __future__ import annotations

import json
import os
import uuid

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

A, B, HORS = "usr_copilot_a", "usr_copilot_b", "usr_copilot_hors"
#: Spends past a ceiling lowered for one test: its own account, so the day's spend of
#: the others stays far below the default ceiling.
C = "usr_copilot_c"


class _Claims:
    def __init__(self, sub: str):
        self.claims = {"sub": sub, "email": f"{sub}@copilot.invalid", "name": sub}


class _Verifier:
    async def verify_token(self, token: str):
        return _Claims(token)


@pytest.fixture(scope="module")
def monde(pg_dsn):
    psycopg = pytest.importorskip("psycopg")
    from oto_mcp import tool_registry
    from oto_mcp.db import _conn as dbconn

    nom = "oto_copilot_" + uuid.uuid4().hex[:8]
    root = psycopg.connect(pg_dsn, autocommit=True)
    root.execute(f'CREATE DATABASE "{nom}"')
    url_avant, pool_avant = os.environ.get("DATABASE_URL"), dbconn._pool
    inst_avant = tool_registry.bound_instance()
    os.environ["DATABASE_URL"] = pg_dsn.rsplit("/", 1)[0] + "/" + nom
    dbconn._pool = None
    try:
        from oto_mcp import db, org_store
        from oto_mcp.db import init_db
        from oto_mcp.server import _build_mcp
        init_db()
        oid = org_store.create_org("Org du copilote", created_by=A)
        for sub in (A, B, C, HORS):
            db.upsert_user(sub, email=f"{sub}@copilot.invalid", name=sub)
            org_store.add_org_member(oid, sub, "org_admin")
            org_store.set_active_org(sub, oid)
        _build_mcp("noauth")            # binds the served catalogue (`tool_registry`)
        from oto_mcp.api import routes as api_routes
        client = TestClient(Starlette(routes=api_routes.make_routes(
            _Verifier(), mcp_instance=None)))
        yield {"org": oid, "client": client}
    finally:
        tool_registry.bind(inst_avant)
        if dbconn._pool is not None:
            dbconn._pool.close()
        dbconn._pool = pool_avant
        if url_avant is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = url_avant
        root.execute(f'DROP DATABASE IF EXISTS "{nom}" WITH (FORCE)')
        root.close()


class _Modele:
    """The scripted model: each call answers the next message of the script."""

    def __init__(self):
        self.script: list[dict] = []
        self.requetes: list[dict] = []
        self.usage = {"input_tokens": 1, "output_tokens": 1}

    async def flux(self, cle, workspace, corps, releve=None):
        self.requetes.append(corps)
        usage = dict(self.usage)
        if releve is not None:
            releve["usage"] = usage
        msg = self.script.pop(0)
        for b in msg["content"]:
            if b.get("type") == "text":
                yield ("text", b["text"])
        yield ("end", {**msg, "usage": usage})


@pytest.fixture
def modele(monde, monkeypatch):
    from oto_mcp.capabilities.copilot import cle, modele as m
    faux = _Modele()
    monkeypatch.setattr(m, "flux", faux.flux)
    monkeypatch.setattr(cle, "resoudre",
                        lambda org, sub: cle.Cle("sk-test", None, None))
    monkeypatch.setenv("OTO_COPILOT_SUBS", f"{A},{B},{C}")
    return faux


def _h(sub: str, org: int) -> dict:
    return {"Authorization": f"Bearer {sub}", "X-Oto-Org": str(org)}


def _evs(r) -> list[dict]:
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/event-stream")
    return [json.loads(l[5:]) for l in r.text.split("\n") if l.startswith("data:")]


def _outil(id_, name, args):
    return {"stop_reason": "tool_use", "content": [
        {"type": "text", "text": "On it."},
        {"type": "tool_use", "id": id_, "name": name, "input": args}]}


def _fin(texte):
    return {"stop_reason": "end_turn", "content": [{"type": "text", "text": texte}]}


def _tour(monde, sub, texte, thread_id=None, projet=None):
    page = {"path": "/", "orgId": monde["org"], "page": "home", "title": "Home",
            "params": {"projectId": str(projet)} if projet else {}, "surfaces": {}}
    return monde["client"].post("/api/copilot/turns", headers=_h(sub, monde["org"]), json={
        "thread_id": thread_id, "text": texte, "input": "text", "mode": "ask",
        "page_context": page, "client_tools": [
            {"name": "navigate", "description": "Open a page",
             "input_schema": {"type": "object"}}]})


def _carte(monde, sub, thread, approval, decision="approve", **k):
    return monde["client"].post(
        f"/api/copilot/threads/{thread}/approvals/{approval}",
        headers=_h(sub, monde["org"]), json={"decision": decision, **k})


def test_hors_liste_403(monde, modele):
    r = _tour(monde, HORS, "hello")
    assert r.status_code == 403 and r.json()["error"] == "copilot_not_enabled"


def test_lecture_puis_reponse(monde, modele):
    modele.script = [_outil("tu_r", "data_list_datastores", {}),
                     _fin("Nothing yet.\n<next>Create a table</next>")]
    evs = _evs(_tour(monde, A, "what tables do I have?"))
    types = [e["type"] for e in evs]
    assert types[0] == "thread" and types[-1] == "done"
    assert "approval_required" not in types
    assert {"type": "suggestions", "items": ["Create a table"]} in evs
    texte = "".join(e["text"] for e in evs if e["type"] == "text_delta")
    assert "<next>" not in texte and "Nothing yet." in texte
    # The tools offered: the core set without the server's axes, then the page's tools.
    outils = modele.requetes[0]["tools"]
    noms = [t["name"] for t in outils]
    assert "data_rows" in noms and noms[-1] == "navigate"
    assert all("_org" not in (t.get("input_schema") or {}).get("properties", {})
               for t in outils)


def test_ecriture_carte_approbation_annulation(monde, modele):
    from oto_mcp.db._conn import _connect
    nom = "copilot-" + uuid.uuid4().hex[:6]
    modele.script = [_outil("tu_c", "data_create_datastore", {
        "datastore": nom, "schema": {"fields": [{"key": "statut", "type": "text"}]},
        "_org": 999999, "_group": 5})]
    evs = _evs(_tour(monde, A, "create a table"))
    thread = evs[0]["thread_id"]
    carte = evs[-1]
    assert carte["type"] == "approval_required" and carte["id"] == "tu_c"
    assert carte["rememberable"] is True
    assert "_org" not in carte["steps"][0]["detail"]

    # An answer for another card, or from another account, is refused.
    faux = _evs(_carte(monde, A, thread, "tu_autre"))
    assert faux[0]["type"] == "error" and faux[-1]["type"] == "done"
    autre = _evs(_carte(monde, B, thread, "tu_c"))
    assert "not found" in autre[0]["message"]

    modele.script = [_fin("Created.")]
    evs = _evs(_carte(monde, A, thread, "tu_c"))
    res = next(e for e in evs if e["type"] == "tool_result")
    assert res["ok"] and res["changed"]["scope"] == "data" and res["revert_id"]
    # The call ran under the conversation's run and the VIEWED org (the planted `_org`
    # was replaced).
    with _connect() as c:
        ligne = c.execute("SELECT run_id, org_id FROM tool_calls WHERE tool = "
                          "'data_create_datastore' ORDER BY id DESC LIMIT 1").fetchone()
        run = c.execute("SELECT run_id FROM copilot_threads WHERE id = %s",
                        (thread,)).fetchone()["run_id"]
    assert ligne["run_id"] == run and ligne["org_id"] == monde["org"]

    # Another account cannot undo it; its owner can, once.
    r = monde["client"].post(f"/api/copilot/actions/{res['revert_id']}/revert",
                             headers=_h(B, monde["org"]), json={})
    assert r.status_code == 404 and r.json()["error"] == "nothing_to_undo"
    r = monde["client"].post(f"/api/copilot/actions/{res['revert_id']}/revert",
                             headers=_h(A, monde["org"]), json={})
    assert r.status_code == 200, r.text
    assert r.json()["ok"] and r.json()["skipped"] == 0
    r = monde["client"].post(f"/api/copilot/actions/{res['revert_id']}/revert",
                             headers=_h(A, monde["org"]), json={})
    assert r.status_code == 404


def _table(sub):
    from oto_mcp import db
    ns = db.create_datastore("user", sub, "copilot-l-" + uuid.uuid4().hex[:6])
    db.set_datastore_schema(ns, {"fields": [{"key": "statut", "type": "text"},
                                            {"key": "note", "type": "text"}]})
    db.datastore_insert_row(ns, "r0", {"statut": "a_faire", "note": "n"})
    return ns


def _cellule(ns, champ):
    from oto_mcp import db
    return (db.datastore_get_row(ns, "r0") or {}).get("data", {}).get(champ)


def test_annulation_par_le_journal(monde, modele):
    ns = _table(A)
    modele.script = [_outil("tu_w", "data_write", {
        "datastore": ns, "id": "r0", "row": {"statut": "fait", "note": "m"}})]
    evs = _evs(_tour(monde, A, "mark it done"))
    thread = evs[0]["thread_id"]
    modele.script = [_fin("Done.")]
    evs = _evs(_carte(monde, A, thread, "tu_w", remember=True))
    res = next(e for e in evs if e["type"] == "tool_result")
    assert res["ok"], res
    assert _cellule(ns, "statut") == "fait"
    # Someone changes one cell since: undo leaves it, puts the other back.
    from oto_mcp.db._conn import _connect
    with _connect() as c:
        c.execute("UPDATE datastore_rows SET data = data || '{\"note\": \"humain\"}'::jsonb "
                  "WHERE ns_id = %s AND row_id = 'r0'", (ns,))
    r = monde["client"].post(f"/api/copilot/actions/{res['revert_id']}/revert",
                             headers=_h(A, monde["org"]), json={})
    assert r.status_code == 200, r.text
    assert r.json()["skipped"] == 1
    assert _cellule(ns, "statut") == "a_faire" and _cellule(ns, "note") == "humain"


def test_retenu_ne_redemande_pas(monde, modele):
    ns = _table(A)
    modele.script = [_outil("tu_1", "data_write", {"datastore": ns, "id": "r0",
                                                   "row": {"statut": "x"}})]
    thread = _evs(_tour(monde, A, "write"))[0]["thread_id"]
    modele.script = [_outil("tu_2", "data_write", {"datastore": ns, "id": "r0",
                                                   "row": {"statut": "y"}}),
                     _fin("ok")]
    evs = _evs(_carte(monde, A, thread, "tu_1", remember=True))
    assert "approval_required" not in [e["type"] for e in evs]
    assert _cellule(ns, "statut") == "y"
    # The next request carries the edited history append-only: earlier turns untouched.
    premiers = modele.requetes[-1]["messages"]
    assert premiers[1]["content"][1]["input"] == {"datastore": ns, "id": "r0",
                                                  "row": {"statut": "x"}}


def test_conversation_d_un_autre_jamais_reprise(monde, modele):
    modele.script = [_fin("hi")]
    thread = _evs(_tour(monde, A, "hi"))[0]["thread_id"]
    modele.script = [_fin("hello")]
    evs = _evs(_tour(monde, B, "continue", thread_id=thread))
    assert evs[0]["thread_id"] != thread


# --- the corrections of the review --------------------------------------------------

@pytest.mark.parametrize("kind", ["user", "delegation"])
def test_un_jeton_oto_est_refuse_nomme(monde, modele, kind):
    """A person's API token, or a delegated one, never reaches the in-app agent: the
    authentication refuses it on all four routes, named."""
    from oto_mcp import db
    jeton = db.create_api_token(A, label="copilot-test", kind=kind)
    h = {"Authorization": f"Bearer {jeton}", "X-Oto-Org": str(monde["org"])}
    for chemin in ("/api/copilot/turns", "/api/copilot/threads/thr_x/client-results",
                   "/api/copilot/threads/thr_x/approvals/tu_x",
                   "/api/copilot/actions/rv_x/revert"):
        r = monde["client"].post(chemin, headers=h, json={})
        assert r.status_code == 403, (chemin, r.text)
        assert r.json()["error"] == "api_token_forbidden", chemin


def test_plafond_atteint_refuse_avant_le_flux(monde, modele, monkeypatch):
    from oto_mcp.db import copilot as store
    monkeypatch.setenv("OTO_COPILOT_DAILY_CAP_USD", "0.5")
    store.ajouter_depense(C, 600_000)               # 0.60 $
    r = _tour(monde, C, "hello")
    assert r.status_code == 429 and r.json()["error"] == "copilot_daily_cap"
    assert modele.requetes == []


def test_plafond_franchi_refuse_l_appel_suivant_dans_le_flux(monde, modele, monkeypatch):
    """Measured on what the provider reported: a call of 1 M input tokens (4 $) crosses a
    3 $ ceiling; the NEXT model call of the same leg is refused, named, never sent."""
    from oto_mcp.db import copilot as store
    monkeypatch.setenv("OTO_COPILOT_DAILY_CAP_USD", str(store.depense_du_jour(C) / 1e6 + 3))
    modele.usage = {"input_tokens": 1_000_000, "output_tokens": 0}
    modele.script = [_outil("tu_cap", "data_list_datastores", {}), _fin("never")]
    evs = _evs(_tour(monde, C, "list"))
    assert len(modele.requetes) == 1
    assert evs[-2]["type"] == "error" and "limit" in evs[-2]["message"]
    assert evs[-1]["type"] == "done"


def test_la_requete_suivante_relit_l_historique_au_cache(monde, modele):
    """The breakpoint is on the last message of what is SENT, never stored: the next
    request carries it on ITS last message only."""
    modele.script = [_outil("tu_h", "data_list_datastores", {}), _fin("ok")]
    _evs(_tour(monde, A, "tables?"))
    for corps in modele.requetes[-2:]:
        poses = [i for i, m in enumerate(corps["messages"]) for b in m["content"]
                 if isinstance(b, dict) and "cache_control" in b]
        assert poses == [len(corps["messages"]) - 1]


def test_echec_d_annulation_sans_le_message_du_store(monde, modele, monkeypatch):
    from oto_mcp.capabilities.copilot import annulation
    from oto_mcp.db import copilot as store
    thread = f"thr_{uuid.uuid4().hex}"
    store.creer(thread, A, monde["org"], None, "")
    store.offrir_annulation("rv_" + thread, thread, A, "x", "rows",
                            {"geste_id": "g", "repere": 0}, 3600)

    def casse(sub, ligne):
        raise ValueError("table secrete_interne colonne privee")
    monkeypatch.setattr(annulation, "defaire", casse)
    r = monde["client"].post(f"/api/copilot/actions/rv_{thread}/revert",
                             headers=_h(A, monde["org"]), json={})
    assert r.status_code == 409 and r.json()["error"] == "undo_failed"
    assert "secrete_interne" not in r.text


def test_maintenance_purge_a_trente_jours(monde):
    from oto_mcp import maintenance
    from oto_mcp.db import copilot as store
    from oto_mcp.db._conn import _connect
    vieux, frais = f"thr_{uuid.uuid4().hex}", f"thr_{uuid.uuid4().hex}"
    for t in (vieux, frais):
        store.creer(t, B, monde["org"], None, "")
        store.offrir_annulation("rv_" + t, t, B, "x", "rows", {}, 3600)
    with _connect() as c:
        c.execute("UPDATE copilot_threads SET last_seen = NOW() - interval '31 days' "
                  "WHERE id = %s", (vieux,))
        c.execute("INSERT INTO copilot_spend (sub, day, usd_micros) VALUES "
                  "(%s, (NOW() AT TIME ZONE 'UTC')::date - 31, 5)", (B,))
    assert maintenance.copilot(dry_run=True)["threads"] >= 1
    out = maintenance.copilot()
    assert out["threads"] >= 1 and out["spend_days"] >= 1
    with _connect() as c:
        fils = {r["id"] for r in c.execute(
            "SELECT id FROM copilot_threads WHERE id IN (%s, %s)", (vieux, frais))}
        undos = {r["id"] for r in c.execute(
            "SELECT id FROM copilot_undos WHERE thread_id IN (%s, %s)", (vieux, frais))}
        anciens = c.execute("SELECT COUNT(*) AS n FROM copilot_spend WHERE sub = %s AND "
                            "day < (NOW() AT TIME ZONE 'UTC')::date - 30", (B,)).fetchone()
    assert fils == {frais} and undos == {"rv_" + frais} and anciens["n"] == 0
    assert "copilot" in maintenance._ALL


def test_reponse_dite_par_le_chemin_normal_d_un_appel(monde, modele, monkeypatch):
    """`speak`: the final answer, as prose, synthesised ONCE by the real `elevenlabs_act`
    (only ElevenLabs and the storage are doubled), journaled as an `elevenlabs_act`
    call with its credits in `quantity`, and the `audio` event goes out before `done`."""
    from unittest.mock import MagicMock
    from oto_mcp.db import _conn as dbconn
    from oto.tools.elevenlabs import ElevenLabsClient
    el = MagicMock()
    el.create_speech.return_value = {"content": b"ID3", "content_type": "audio/mpeg",
                                     "extension": "mp3", "output_format": "mp3_44100_128",
                                     "character_count": 13}
    # The catalogue is built once per module: its tool holds the class, not the name.
    monkeypatch.setattr(ElevenLabsClient, "create_speech",
                        lambda self, *a, **k: el.create_speech(*a, **k))
    monkeypatch.setattr("oto_mcp.access.resolve_api_key",
                        lambda provider, account=None, units=1: ("org-key", False))
    monkeypatch.setattr("oto_mcp.media_store.upload_private",
                        lambda *a, **k: "https://media.test/speech.mp3?sig=x")
    modele.script = [_fin("All **done**: [table](https://x.test/t).\n<next>Next</next>")]
    page = {"path": "/", "orgId": monde["org"], "page": "home", "params": {}}
    r = monde["client"].post("/api/copilot/turns", headers=_h(A, monde["org"]), json={
        "text": "status?", "page_context": page, "speak": True})
    evs = _evs(r)
    types = [e["type"] for e in evs]
    assert types[-2:] == ["audio", "done"], evs
    assert evs[-2] == {"type": "audio", "url": "https://media.test/speech.mp3?sig=x",
                       "content_type": "audio/mpeg", "chars": len("All done: table.")}
    el.create_speech.assert_called_once()
    args, kw = el.create_speech.call_args
    assert args[1] == "All done: table." and kw["model_id"] == "eleven_flash_v2_5"
    import time
    for _ in range(50):
        with dbconn._connect() as c:
            row = c.execute("SELECT tool, quantity, org_id FROM tool_calls WHERE sub = %s "
                            "AND tool = 'elevenlabs_act' ORDER BY id DESC LIMIT 1",
                            (A,)).fetchone()
        if row:
            break
        time.sleep(0.1)
    assert row is not None, "the spoken answer must be journaled as an elevenlabs_act call"
    assert row["quantity"] == 7, "eleven_flash_v2_5: 13 characters → ceil(6.5) credits"
    assert row["org_id"] == monde["org"]
