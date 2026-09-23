"""Monitoring REST (ADR 0017, kind='rest') : le middleware journalise chaque
requête /api/* dans le flux unifié, sans toucher /mcp ni le service."""
import asyncio

import pytest

from oto_mcp.api import routes as ar


def test_normalize_route_collapses_ids():
    assert ar._normalize_route("/api/orgs/7/audit-log") == "/api/orgs/:id/audit-log"
    assert ar._normalize_route("/api/me") == "/api/me"
    uuid = "/api/x/3f2504e0-4f89-41d3-9a0c-0305e82c3301/y"
    assert ar._normalize_route(uuid) == "/api/x/:id/y"


def test_claimed_sub_parses_jwt_payload_unverified():
    import base64, json
    payload = base64.urlsafe_b64encode(json.dumps({"sub": "u-42"}).encode()).rstrip(b"=").decode()
    req = _req(headers={"authorization": f"Bearer h.{payload}.sig"})
    assert ar._claimed_sub(req) == "u-42"


def test_claimed_sub_none_for_opaque_token():
    req = _req(headers={"authorization": "Bearer oto_opaquetoken"})
    assert ar._claimed_sub(req) is None
    assert ar._claimed_sub(_req(headers={})) is None


def _req(headers: dict):
    from starlette.requests import Request
    raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    return Request({"type": "http", "headers": raw, "query_string": b""})


def _run_mw(monkeypatch, *, path, method="GET", status=200, headers: dict | None = None):
    """Exécute le middleware sur une requête simulée ; renvoie la ligne loggée (ou None)."""
    captured = {}

    def fake_insert(row):
        captured.update(row)

    monkeypatch.setattr(ar.db, "insert_tool_call", fake_insert)

    async def downstream(scope, receive, send):
        await send({"type": "http.response.start", "status": status, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})

    mw = ar.RestCallLogger(downstream)
    raw_headers = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    scope = {"type": "http", "path": path, "method": method, "headers": raw_headers, "query_string": b""}
    sent = []

    async def send(m):
        sent.append(m)

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def drive():
        await mw(scope, receive, send)
        # laisse la tâche fire-and-forget s'exécuter
        await asyncio.sleep(0)
        await asyncio.gather(*list(ar._REST_LOG_TASKS), return_exceptions=True)

    asyncio.run(drive())
    return captured, sent


def test_logs_api_request_as_rest_event(monkeypatch):
    row, sent = _run_mw(monkeypatch, path="/api/orgs/7/members", method="POST", status=201)
    assert row["kind"] == "rest"
    assert row["tool"] == "POST /api/orgs/:id/members"
    assert row["ok"] is True and row["error"] is None
    assert "duration_ms" in row
    # la réponse downstream est bien passée (service intact)
    assert sent[0]["status"] == 201


def test_logs_error_status(monkeypatch):
    row, _ = _run_mw(monkeypatch, path="/api/me", status=403)
    assert row["ok"] is False and row["error"] == "HTTP 403"


def test_un_plantage_s_ecrit_sans_code(monkeypatch):
    """Ce que `by_status: null` de la lentille REST veut dire (oto#179) : une exception
    non rattrapée ne laisse passer AUCUNE réponse par ce middleware — le 500 est servi
    plus haut. La ligne est en échec, sans code : c'est la forme d'un plantage."""
    import pytest
    captured = {}
    monkeypatch.setattr(ar.db, "insert_tool_call", lambda row: captured.update(row))

    async def plante(scope, receive, send):
        raise KeyError("jeton")

    async def drive():
        scope = {"type": "http", "path": "/api/auth/token", "method": "POST",
                 "headers": [], "query_string": b""}
        with pytest.raises(KeyError):
            await ar.RestCallLogger(plante)(scope, None, None)
        await asyncio.sleep(0)
        await asyncio.gather(*list(ar._REST_LOG_TASKS), return_exceptions=True)

    asyncio.run(drive())
    assert captured["ok"] is False and captured["error"] is None


def test_passthrough_non_api_does_not_log(monkeypatch):
    row, sent = _run_mw(monkeypatch, path="/mcp", status=200)
    assert row == {}  # jamais journalisé → /mcp intact
    assert sent[0]["status"] == 200


def test_options_preflight_skipped(monkeypatch):
    row, _ = _run_mw(monkeypatch, path="/api/me", method="OPTIONS", status=204)
    assert row == {}


# ── Les jetons du chemin d'URL (#558) ────────────────────────────────────────

def _routes_declarees():
    """La VRAIE table servie : c'est elle qui déclare `{token}`."""
    ar.make_routes(object(), mcp_instance=None)


def test_un_appel_sur_une_route_a_jeton_laisse_un_journal_masque(monkeypatch):
    """Le fait de #558 : `PUT /api/upload/<jeton>` partait EN CLAIR dans
    `tool_calls.tool`, sur une fenêtre de rétention, relu par les surfaces de
    supervision — alors que le modèle de données refuse de persister ce genre de
    secret ailleurs."""
    _routes_declarees()
    jeton = "eyJ0eXAiOiJ1cGxvYWQiLCJqdGkiOiJ4In0.c2lnbmF0dXJl"
    row, _ = _run_mw(monkeypatch, path=f"/api/upload/{jeton}", method="PUT", status=204)
    assert jeton not in str(row)
    assert row["tool"] == "PUT /api/upload/:token"
    # L'empreinte, elle, va dans `args` : « le même jeton a-t-il été rejoué ? » se
    # répond, « lequel était-ce » non. Et `tool` garde une cardinalité agrégeable.
    assert row["args"]["token"].startswith("#")


def test_une_route_sans_jeton_n_ecrit_aucun_args(monkeypatch):
    """Pas de colonne `args` remplie pour rien : la ligne de route reste de la
    télémétrie de surface (cf. `calllog.log_rest_call`)."""
    _routes_declarees()
    row, _ = _run_mw(monkeypatch, path="/api/orgs/7/members", method="POST", status=201)
    assert row["args"] is None


# ── La cible du « voir en tant que » (#572 point 4, oto-backend#962) ────────
# Le journal enregistre la cible APPLIQUÉE par `ViewAsMiddleware`, jamais
# l'en-tête `X-Oto-View-As` brut, que n'importe quel appelant peut poser. La
# chaîne réelle (`RestCallLogger` enveloppe `ViewAsMiddleware`) est montée ici.

def _run_chaine(monkeypatch, *, operateur, cible_existe=True, method="GET",
                vue="sub-cible-42"):
    from oto_mcp import access
    from oto_mcp import db as oto_db

    async def authentifie(request, verifier, **kw):
        return "u-operateur", None
    monkeypatch.setattr(ar, "_authenticate", authentifie)
    monkeypatch.setattr(access, "is_platform_operator", lambda sub: operateur)
    monkeypatch.setattr(oto_db, "get_user",
                        lambda sub: {"sub": sub} if cible_existe else None)
    captured = {}
    monkeypatch.setattr(ar.db, "insert_tool_call", captured.update)

    async def downstream(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})

    mw = ar.RestCallLogger(ar.ViewAsMiddleware(downstream, verifier=None))
    scope = {"type": "http", "path": "/api/me", "method": method, "query_string": b"",
             "headers": [(b"authorization", b"Bearer x"), (b"x-oto-view-as", vue.encode())]}
    sent = []

    async def send(m):
        sent.append(m)

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def drive():
        await mw(scope, receive, send)
        await asyncio.sleep(0)
        await asyncio.gather(*list(ar._REST_LOG_TASKS), return_exceptions=True)

    asyncio.run(drive())
    return captured, sent[0]["status"]


def test_la_vue_appliquee_est_journalisee(monkeypatch):
    row, status = _run_chaine(monkeypatch, operateur=True)
    assert status == 200
    assert row["view_as_sub"] == "sub-cible-42"
    assert row["sub"] is None or row["sub"] != "sub-cible-42"   # le porteur reste le sub


@pytest.mark.parametrize("cas", [
    dict(operateur=False),                          # non-opérateur : 403, rien d'appliqué
    dict(operateur=True, vue="u-operateur"),        # cible = soi : no-op
    dict(operateur=True, cible_existe=False),       # cible inconnue : no-op
    dict(operateur=True, method="DELETE"),          # écriture en consultation : 403
])
def test_une_vue_refusee_ou_sans_effet_n_est_pas_journalisee(monkeypatch, cas):
    row, _ = _run_chaine(monkeypatch, **cas)
    assert row["kind"] == "rest"                    # la requête est bien journalisée…
    assert row["view_as_sub"] is None               # …sans la cible revendiquée


def test_view_as_target_absent_by_default(monkeypatch):
    row, _ = _run_mw(monkeypatch, path="/api/me")
    assert row["view_as_sub"] is None


def test_l_en_tete_seul_ne_fait_pas_une_vue(monkeypatch):
    """Sans `ViewAsMiddleware` pour l'appliquer, l'en-tête n'est qu'une revendication."""
    row, _ = _run_mw(monkeypatch, path="/api/me", headers={"x-oto-view-as": "sub-cible-42"})
    assert row["view_as_sub"] is None
