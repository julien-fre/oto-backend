"""Une réponse FICHIER porte son CORS — sinon le navigateur la jette (2026-09-09).

Le défaut, mesuré en production : `GET /api/me/billing/invoices/1/pdf` répondait
**200** (journal `oto-mcp@green`, 09/09 14:40) et le dashboard n'affichait qu'un
« Failed to fetch ». Ce serveur n'a pas de `CORSMiddleware` : le CORS se pose
réponse par réponse, et seuls `_json`, `_json_error` et `options_handler` le
posaient. Le préflight passait, les 404 et 409 passaient — **le 200 qui porte le
PDF sortait nu**, et un 200 refusé par le navigateur ne se voit dans aucun journal
serveur.

Quatre réponses le construisaient à la main (PDF de facture, export ZIP d'un
projet, favicon et markdown public) : le défaut n'était pas dans les appels mais
dans la façon de poser le CORS. `base._file` est désormais le seul chemin, et le
dernier test de ce fichier refuse le cinquième oubli.

⚠️ Le premier test lit sur un **vrai socket**. `TestClient` court-circuite le fil,
et c'est le fil qui a menti ici : la réponse était juste côté serveur, refusée côté
client. Même précédent que `tests/test_response_charset.py`.
"""
from __future__ import annotations

import ast
import contextlib
import pathlib
import threading
import time

import pytest
import requests
import uvicorn
from starlette.applications import Starlette
from starlette.routing import Route

# Une origine de la liste par défaut (`base._allowed_origins`) : le dashboard de
# production. Prise dans la vraie liste, jamais fabriquée — c'est elle qui a été
# refusée le 09/09.
ORIGINE = "https://manage.oto.cx"
ETRANGERE = "https://exemple.invalid"


@contextlib.contextmanager
def _servi(app):
    """Sert `app` sur un port libre, pour lire les en-têtes tels qu'ils arrivent."""
    serveur = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0,
                                            log_level="warning"))
    fil = threading.Thread(target=serveur.run, daemon=True)
    fil.start()
    try:
        limite = time.time() + 30
        while not serveur.started:
            assert time.time() < limite, "uvicorn n'a pas démarré"
            time.sleep(0.02)
        yield f"http://127.0.0.1:{serveur.servers[0].sockets[0].getsockname()[1]}"
    finally:
        serveur.should_exit = True
        fil.join(timeout=30)


@pytest.fixture
def sans_liste_env(monkeypatch):
    """La liste d'origines SERVIE par défaut, sans l'`OTO_MCP_CORS_ORIGINS` du poste."""
    monkeypatch.delenv("OTO_MCP_CORS_ORIGINS", raising=False)


# ── le PDF d'une facture ─────────────────────────────────────────────────────

def _app_pdf(monkeypatch, *, pdf: bytes = b"%PDF-1.4 test", nom: str = "F-2026-09-7.pdf"):
    """La route RÉELLE d'`api/billing.py`, avec les vraies primitives de `base`."""
    from oto_mcp import roles
    from oto_mcp.api import base
    from oto_mcp.api import billing as api_billing
    from oto_mcp.db import billing_invoices as db_invoices

    monkeypatch.setenv("OTO_BILLING_ENABLED", "1")
    monkeypatch.setattr(db_invoices, "get_billing_invoice_pdf",
                        lambda _id: {"id": _id, "org_id": 302, "kind": "invoice",
                                     "number": "F-2026-09-7", "pdf": pdf,
                                     "pdf_filename": nom})
    monkeypatch.setattr(roles, "is_org_member", lambda *a, **k: True)

    async def _auth(_req, _verifier, **kw):
        return "u-1", None

    return Starlette(routes=api_billing.make_routes(
        base.options_handler, verifier=None, authenticate=_auth,
        json_error=base._json_error))


def test_le_pdf_sort_avec_son_cors_sur_un_vrai_socket(monkeypatch, sans_liste_env):
    """LE test du lot : la réponse qui répondait 200 sans que le front la voie."""
    with _servi(_app_pdf(monkeypatch)) as base_url:
        rep = requests.get(f"{base_url}/api/me/billing/invoices/1/pdf",
                           headers={"Origin": ORIGINE}, timeout=10)

    assert rep.status_code == 200 and rep.content.startswith(b"%PDF")
    assert rep.headers.get("access-control-allow-origin") == ORIGINE, (
        "sans cet en-tête le navigateur JETTE la réponse — c'est le défaut du 09/09")
    assert "Origin" in rep.headers.get("vary", "")
    # Le nom de fichier ne sert à rien s'il n'est pas LISIBLE par `fetch` : un
    # en-tête de réponse non exposé n'existe pas pour le front.
    assert "F-2026-09-7.pdf" in rep.headers["content-disposition"]
    assert "Content-Disposition" in rep.headers.get("access-control-expose-headers", "")


def test_une_origine_hors_liste_nobtient_toujours_rien(monkeypatch, sans_liste_env):
    """Poser le CORS n'est pas l'ouvrir : l'allowlist décide, comme pour le JSON."""
    with _servi(_app_pdf(monkeypatch)) as base_url:
        rep = requests.get(f"{base_url}/api/me/billing/invoices/1/pdf",
                           headers={"Origin": ETRANGERE}, timeout=10)

    assert rep.status_code == 200
    assert "access-control-allow-origin" not in rep.headers


def test_un_nom_de_fichier_ne_compose_pas_un_entete(monkeypatch, sans_liste_env):
    """Le numéro vient du FOURNISSEUR : CR/LF et guillemets n'atteignent pas le fil."""
    app = _app_pdf(monkeypatch, nom='F-2026\r\nX-Injecte: 1"')
    with _servi(app) as base_url:
        rep = requests.get(f"{base_url}/api/me/billing/invoices/1/pdf",
                           headers={"Origin": ORIGINE}, timeout=10)

    assert rep.status_code == 200
    assert "x-injecte" not in rep.headers
    assert rep.headers["content-disposition"] == 'attachment; filename="F-2026X-Injecte: 1"'


# ── l'export ZIP d'un projet : le même défaut, la même correction ────────────

def test_lexport_zip_dun_projet_porte_le_meme_cors(monkeypatch, sans_liste_env):
    from oto_mcp import db, doc_export, ownership
    from oto_mcp.api import base
    from oto_mcp.api import projects as api_projects

    async def _auth(_req, _verifier=None, **kw):
        return "u-1", None

    monkeypatch.setattr(api_projects, "_authenticate", _auth)
    monkeypatch.setattr(ownership, "can_access", lambda *a, **k: True)
    # Ce banc éprouve le CORS, pas l'autorisation : il ouvre les DEUX portes de la
    # route pour atteindre le corps, comme il double déjà `_authenticate`. La seconde
    # est le gate de contexte d'org posé le 15/09/2026, qui aligne l'export sur les
    # quatre autres routes projet par-id. Il est doublé ICI plutôt que sa dépendance
    # `visible_in_org`, parce qu'il lit aussi le contexte en base : sans base, la
    # route rendrait 500 et le banc mesurerait des en-têtes d'erreur, pas ceux du ZIP.
    monkeypatch.setattr(api_projects, "_project_org_context_error", lambda *a, **k: None)
    monkeypatch.setattr(db, "get_project_by_id", lambda pid: {"id": pid, "name": "Ma KB"})
    monkeypatch.setattr(db, "list_docs_for_project", lambda pid: [])
    monkeypatch.setattr(doc_export, "build_export", lambda docs, racine: b"PK\x03\x04zip")

    app = Starlette(routes=[Route("/api/me/projects/{id}/export",
                                  base.bind(api_projects.me_project_export,
                                            verifier=None), methods=["GET"])])
    with _servi(app) as base_url:
        rep = requests.get(f"{base_url}/api/me/projects/7/export",
                           headers={"Origin": ORIGINE}, timeout=10)

    assert rep.status_code == 200 and rep.content.startswith(b"PK")
    assert rep.headers.get("access-control-allow-origin") == ORIGINE
    assert rep.headers["content-disposition"].endswith('.zip"')
    assert "Content-Disposition" in rep.headers.get("access-control-expose-headers", "")


# ── le cliquet : plus une seule réponse fichier hors du chemin commun ────────

RACINE = pathlib.Path(__file__).resolve().parents[2] / "oto_mcp" / "api"


def test_aucune_reponse_fichier_ne_se_construit_hors_de_base_file():
    """Une `Response(..., media_type=…)` écrite à la main sort SANS CORS.

    Ce que la garde parcourt : tout module de `oto_mcp/api/`, à la recherche d'une
    construction de réponse Starlette portant un `media_type` explicite — la forme
    exacte des quatre oublis. `base.py` est exempté : c'est lui qui la construit,
    une fois, pour tout le monde.

    Ce qu'elle NE voit pas, et qu'il faut savoir : une `HTMLResponse` (son type est
    implicite) et tout ce qui sort d'un module hors `api/`. Elle ferme l'axe qui a
    coûté, pas tous les axes possibles.
    """
    coupables = []
    for f in sorted(RACINE.rglob("*.py")):
        if f.name == "base.py":
            continue
        arbre = ast.parse(f.read_text(), filename=str(f))
        for noeud in ast.walk(arbre):
            if not isinstance(noeud, ast.Call):
                continue
            nom = getattr(noeud.func, "id", None) or getattr(noeud.func, "attr", None)
            if not (nom or "").endswith("Response"):
                continue
            if any(kw.arg == "media_type" for kw in noeud.keywords):
                coupables.append(f"{f.relative_to(RACINE.parent.parent)}:{noeud.lineno}")

    assert coupables == [], (
        "réponse fichier construite à la main (donc sans CORS, donc invisible au "
        f"navigateur) : {coupables} — passer par `api/base.py::_file`")
