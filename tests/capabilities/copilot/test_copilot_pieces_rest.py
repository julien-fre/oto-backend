"""Files attached to the in-app agent ON THE SERVED ROUTES, against a real PostgreSQL —
only the model and the object storage are stand-ins (nothing leaves the test).

Proven here: the upload's gate (the copilot routes' own: allowlist, interactive session
only, org member), its refusals (type judged on the bytes, size per type), the row bound
to its sub AND org, the turn refused BEFORE its stream on an id that is not the caller's,
the marker stored in the thread (never the bytes), the current turn resolved into the
model request and an earlier one reduced to a line, and `GET` of what is accepted.
"""
from __future__ import annotations

import json
import os
import uuid

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

A, B, HORS = "usr_pieces_a", "usr_pieces_b", "usr_pieces_hors"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
PDF = b"%PDF-1.7\n" + b"x" * 64


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

    nom = "oto_pieces_" + uuid.uuid4().hex[:8]
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
        oid = org_store.create_org("Org des pièces", created_by=A)
        autre = org_store.create_org("Autre org", created_by=A)
        for sub in (A, B, HORS):
            db.upsert_user(sub, email=f"{sub}@copilot.invalid", name=sub)
            org_store.add_org_member(oid, sub, "org_admin")
            org_store.set_active_org(sub, oid)
        org_store.add_org_member(autre, A, "org_admin")
        _build_mcp("noauth")
        from oto_mcp.api import routes as api_routes
        client = TestClient(Starlette(routes=api_routes.make_routes(
            _Verifier(), mcp_instance=None)))
        yield {"org": oid, "autre": autre, "client": client}
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


class _Stockage:
    """The object storage, in memory."""

    def __init__(self):
        self.objets: dict[str, bytes] = {}
        self.signes = 0

    def upload_object(self, prefix, owner_id, data, content_type, filename=None, *,
                      max_bytes=None, propre_au_depot=False):
        assert prefix == "copilot-attachments" and propre_au_depot
        cle = f"{prefix}/{owner_id}/{uuid.uuid4().hex}/{filename}"
        self.objets[cle] = data
        return cle

    def presign_get(self, key, *, expiry=None):
        self.signes += 1
        return f"https://stockage.invalid/{key}?sig={self.signes}"

    def fetch_object(self, key, *, max_bytes=None):
        return self.objets[key]


class _Modele:
    def __init__(self):
        self.script: list[dict] = []
        self.requetes: list[dict] = []

    async def flux(self, cle, workspace, corps, releve=None):
        self.requetes.append(json.loads(json.dumps(corps)))
        usage = {"input_tokens": 1, "output_tokens": 1}
        if releve is not None:
            releve["usage"] = usage
        msg = self.script.pop(0)
        for b in msg["content"]:
            if b.get("type") == "text":
                yield ("text", b["text"])
        yield ("end", {**msg, "usage": usage})


@pytest.fixture
def outils(monde, monkeypatch):
    from oto_mcp import media_store
    from oto_mcp.capabilities.copilot import cle, modele as m
    stock, faux = _Stockage(), _Modele()
    monkeypatch.setattr(media_store, "upload_object", stock.upload_object)
    monkeypatch.setattr(media_store, "fetch_object", stock.fetch_object)
    monkeypatch.setattr(media_store, "presign_get", stock.presign_get)
    monkeypatch.setattr(m, "flux", faux.flux)
    monkeypatch.setattr(cle, "resoudre", lambda org, sub: cle.Cle("sk-test", None, None))
    monkeypatch.setenv("OTO_COPILOT_SUBS", f"{A},{B}")
    return {"stock": stock, "modele": faux}


def _h(sub, org):
    return {"Authorization": f"Bearer {sub}", "X-Oto-Org": str(org)}


def _depot(monde, sub, data, nom, ct, org=None):
    return monde["client"].post("/api/copilot/attachments",
                                headers=_h(sub, org or monde["org"]),
                                files={"file": (nom, data, ct)})


def _tour(monde, sub, texte, attachments=(), thread_id=None):
    return monde["client"].post("/api/copilot/turns", headers=_h(sub, monde["org"]), json={
        "thread_id": thread_id, "text": texte, "input": "text", "mode": "ask",
        "page_context": {"path": "/", "params": {}, "surfaces": {}},
        "client_tools": [], "attachments": list(attachments)})


def _evs(r):
    assert r.status_code == 200, r.text
    return [json.loads(l[5:]) for l in r.text.split("\n") if l.startswith("data:")]


def _fin(texte):
    return {"stop_reason": "end_turn", "content": [{"type": "text", "text": texte}]}


def test_depot_rend_le_contrat_et_lie_la_piece(monde, outils):
    r = _depot(monde, A, PNG, "../Capture d'écran.png", "image/png")
    assert r.status_code == 200, r.text
    out = r.json()
    assert set(out) == {"id", "name", "media_type", "size"}
    assert out["id"].startswith("att_") and len(out["id"]) == 36
    assert out == {**out, "name": "Capture d'écran.png", "media_type": "image/png",
                   "size": len(PNG)}
    from oto_mcp.db import copilot as store
    ligne = store.pieces([out["id"]], A, monde["org"])[out["id"]]
    assert ligne["s3_key"] in outils["stock"].objets
    assert store.pieces([out["id"]], B, monde["org"]) == {}
    assert store.pieces([out["id"]], A, monde["autre"]) == {}


def test_depot_refus_de_type_et_de_taille(monde, outils):
    r = _depot(monde, A, PNG, "photo.pdf", "application/pdf")
    assert (r.status_code, r.json()["error"]) == (415, "attachment_type")
    r = _depot(monde, A, b"\xff\xfe\x00x", "a.txt", "text/plain")
    assert (r.status_code, r.json()["error"]) == (415, "attachment_type")
    r = _depot(monde, A, PNG + b"\x00" * (5 * 1024 * 1024), "big.png", "image/png")
    assert (r.status_code, r.json()["error"]) == (413, "attachment_too_large")
    r = _depot(monde, A, PDF + b"x" * (21 * 1024 * 1024), "big.pdf", "application/pdf")
    assert (r.status_code, r.json()["error"]) == (413, "attachment_too_large")
    assert outils["stock"].objets == {} or all(
        not k.endswith(("photo.pdf", "a.txt", "big.png", "big.pdf"))
        for k in outils["stock"].objets)


def test_depot_porte(monde, outils):
    r = _depot(monde, HORS, PNG, "a.png", "image/png")
    assert (r.status_code, r.json()["error"]) == (403, "copilot_not_enabled")
    r = monde["client"].post("/api/copilot/attachments",
                             headers={"Authorization": "Bearer oto_xyz",
                                      "X-Oto-Org": str(monde["org"])},
                             files={"file": ("a.png", PNG, "image/png")})
    assert (r.status_code, r.json()["error"]) == (403, "api_token_forbidden")
    r = monde["client"].post("/api/copilot/attachments", files={"file": ("a", PNG)})
    assert r.status_code == 401


def test_get_dit_ce_qui_est_accepte(monde, outils):
    r = monde["client"].get("/api/copilot/attachments", headers=_h(A, monde["org"]))
    assert r.status_code == 200, r.text
    assert r.json() == {"types": {
        "image/png": 5242880, "image/jpeg": 5242880, "image/gif": 5242880,
        "image/webp": 5242880, "application/pdf": 20971520, "text/plain": 524288,
        "text/markdown": 524288, "text/csv": 524288, "application/json": 524288},
        "max_per_turn": 5}
    r = monde["client"].get("/api/copilot/attachments", headers=_h(HORS, monde["org"]))
    assert (r.status_code, r.json()["error"]) == (403, "copilot_not_enabled")


def test_tour_refuse_un_id_qui_n_est_pas_le_sien_avant_le_flux(monde, outils):
    id_b = _depot(monde, B, PNG, "b.png", "image/png").json()["id"]
    for ids in ([id_b], ["att_" + "0" * 32], ["nimporte"]):
        r = _tour(monde, A, "regarde", ids)
        assert (r.status_code, r.json()["error"]) == (400, "unknown_attachment"), ids
    r = _tour(monde, A, "trop", ["att_" + "0" * 32] * 6)
    assert r.status_code == 400
    assert outils["modele"].requetes == []


def test_marque_stockee_contenu_au_tour_courant_ligne_au_suivant(monde, outils):
    img = _depot(monde, A, PNG, "photo.png", "image/png").json()
    txt = _depot(monde, A, "a,b\n1,é\n".encode(), "t.csv", "text/csv").json()
    m = outils["modele"]
    m.script = [_fin("Vu.")]
    evs = _evs(_tour(monde, A, "regarde", [img["id"], txt["id"]]))
    thread = evs[0]["thread_id"]
    envoye = m.requetes[-1]["messages"][-1]["content"]
    assert [b["type"] for b in envoye] == ["text", "text", "image", "text", "document",
                                           "text"]
    assert envoye[1]["text"] == "Attachment: photo.png"
    assert envoye[2]["source"]["type"] == "url"
    assert envoye[4]["source"] == {"type": "text", "media_type": "text/plain",
                                   "data": "a,b\n1,é\n"}
    assert envoye[-1]["text"] == "regarde"
    from oto_mcp.db import copilot as store
    stocke = store.lire(thread, A)["messages"][0]["content"]
    assert [b["type"] for b in stocke] == ["text", "oto_attachment", "oto_attachment",
                                           "text"]
    assert stocke[1] == {"type": "oto_attachment", "id": img["id"], "name": "photo.png",
                         "media_type": "image/png"}
    # The next turn: the file is a line, never re-sent.
    m.script = [_fin("Ok.")]
    _evs(_tour(monde, A, "et ensuite", thread_id=thread))
    premier = m.requetes[-1]["messages"][0]["content"]
    assert [b["type"] for b in premier] == ["text", "text", "text", "text"]
    assert "was shown with an earlier message" in premier[1]["text"]
    assert all(b.get("type") not in ("image", "document", "oto_attachment")
               for msg in m.requetes[-1]["messages"] for b in msg["content"])


def test_maintenance_purge_les_pieces_de_plus_de_trente_jours(monde, monkeypatch):
    from oto_mcp import maintenance, media_store
    from oto_mcp.db import copilot as store
    from oto_mcp.db._conn import _connect
    retires = []
    monkeypatch.setattr(media_store, "delete_by_key", retires.append)
    vieille, fraiche = f"att_{uuid.uuid4().hex}", f"att_{uuid.uuid4().hex}"
    for i in (vieille, fraiche):
        store.creer_piece(i, B, monde["org"], f"copilot-attachments/k/{i}", "f.png",
                          "image/png", 3)
    with _connect() as c:
        c.execute("UPDATE copilot_attachments SET created_at = NOW() - interval '31 days' "
                  "WHERE id = %s", (vieille,))
    assert maintenance.copilot(dry_run=True)["attachments"] >= 1
    out = maintenance.copilot()
    assert out["attachments"] >= 1
    assert f"copilot-attachments/k/{vieille}" in retires
    assert f"copilot-attachments/k/{fraiche}" not in retires
    assert set(store.pieces([vieille, fraiche], B, monde["org"])) == {fraiche}
