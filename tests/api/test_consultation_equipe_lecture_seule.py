"""Consultation d'équipe (`X-Oto-Group`) : la lecture seule suit la même règle que `/api/me`.

`/api/me` annonce `active_org_readonly` quand un opérateur plateforme n'a aucun rôle
RÉEL dans l'org consultée. Le middleware ne posait cette lecture seule que sur la
consultation d'ORG : par l'équipe, un super_admin (qui passe `can_read_group` par
escalade) ou un opérateur membre de la seule équipe écrivait pendant que l'écran
annonçait la lecture seule. Les règles de rôle sont les vraies (`roles.*`) ; seule la
base est doublée.
"""
from __future__ import annotations

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from oto_mcp import group_store, org_store
from oto_mcp.access import scope as access_scope
from oto_mcp.api import routes as api_routes

ORG, EQUIPE = 172, 7
PAR_ORG = {"X-Oto-Org": str(ORG)}
PAR_EQUIPE = {"X-Oto-Org": str(ORG), "X-Oto-Group": str(EQUIPE)}


async def _atteint(request):
    return JSONResponse({"atteint": True})


@pytest.fixture
def client(monkeypatch):
    def monter(plateforme, role_org, role_equipe):
        async def authentifie(request, verifier, **kw):
            return "u-acteur", None
        monkeypatch.setattr(api_routes, "_authenticate", authentifie)
        monkeypatch.setattr(access_scope, "get_user_role", lambda sub: plateforme)
        monkeypatch.setattr(org_store, "get_org_role", lambda org_id, sub: role_org)
        monkeypatch.setattr(group_store, "get_group", lambda gid: {"id": gid, "org_id": ORG})
        monkeypatch.setattr(group_store, "get_group_role", lambda gid, sub: role_equipe)
        app = Starlette(routes=[Route("/{p:path}", _atteint, methods=["GET", "POST", "DELETE"])])
        return TestClient(api_routes.ViewAsMiddleware(app, verifier=None))
    return monter


def _efface_le_logo(c, entetes):
    r = c.request("DELETE", f"/api/orgs/{ORG}/logo",
                  headers={"Authorization": "Bearer x", **entetes})
    return r.status_code, r.json().get("error")


@pytest.mark.parametrize("plateforme, role_org, role_equipe, attendu", [
    # Opérateur sans rôle réel dans l'org : lecture seule, comme `/api/me` l'annonce.
    ("super_admin", None, None, (403, "view_as_read_only")),
    ("super_admin", None, "group_member", (403, "view_as_read_only")),
    ("admin", None, "group_member", (403, "view_as_read_only")),
    # Aucun accès élargi : l'admin opérationnel hors org et hors équipe reste refusé.
    ("admin", None, None, (403, "forbidden")),
    # Rôle réel dans l'org : l'écriture traverse, la route la garde ensuite.
    ("super_admin", "org_member", None, (200, None)),
    ("admin", "org_admin", None, (200, None)),
    ("member", "org_member", "group_member", (200, None)),
    # Ni opérateur ni membre de l'org : `/api/me` n'annonce pas la lecture seule.
    ("member", None, "group_member", (200, None)),
])
def test_ecrire_en_consultant_une_equipe(client, plateforme, role_org, role_equipe, attendu):
    assert _efface_le_logo(client(plateforme, role_org, role_equipe), PAR_EQUIPE) == attendu


def test_la_lecture_seule_d_equipe_laisse_passer_les_lectures(client):
    c = client("super_admin", None, None)
    entetes = {"Authorization": "Bearer x", **PAR_EQUIPE}
    assert c.request("GET", f"/api/orgs/{ORG}", headers=entetes).status_code == 200
    lecture = c.request("POST", "/api/me/runner/triggers", headers=entetes, json={"op": "list"})
    assert lecture.status_code == 200
    ecriture = c.request("POST", "/api/me/runner/triggers", headers=entetes, json={"op": "create"})
    assert (ecriture.status_code, ecriture.json().get("error")) == (403, "view_as_read_only")


@pytest.mark.parametrize("plateforme, role_org, attendu", [
    ("super_admin", None, (403, "view_as_read_only")),
    ("admin", None, (403, "view_as_read_only")),
    ("member", None, (403, "forbidden")),
    ("member", "org_member", (200, None)),
])
def test_consulter_une_org_reste_inchange(client, plateforme, role_org, attendu):
    assert _efface_le_logo(client(plateforme, role_org, None), PAR_ORG) == attendu
