"""« Voir en tant que » ouvert à l'org_admin, BORNÉ à son org (oto#270) — les gardes.

Un org_admin vérifie ce que voit un membre de SON org (onboarding) sans demander un
partage d'écran. Même `ViewAsMiddleware`, même garde de lecture seule, même journal que
la vue d'opérateur ; la vue est bornée à l'org O. Ce banc joue le VRAI middleware sur
des doublures de rôles, sans base : il tient les gardes d'entrée, la liste fermée de
lectures et l'épinglage des chemins. Ce que la vue MONTRE (le seam `ownership`, les
routes servies) se joue contre PostgreSQL dans `test_vue_bornee_org_admin.py`.
"""
from __future__ import annotations

import asyncio
import json
import pathlib
import re

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from oto_mcp import access, db, group_store, org_store, session_org
from oto_mcp.access import scope as access_scope
from oto_mcp.api import routes as api_routes

ORG, AUTRE = 270, 271
EQUIPE, EQUIPE_AUTRE = 2700, 2710
ADMIN, CIBLE, SIMPLE, DEHORS, OPERATEUR = (
    "u-admin", "u-cible", "u-simple", "u-dehors", "u-operateur")
ROLES = {  # (org, sub) → rôle RÉEL
    (ORG, ADMIN): "org_admin", (ORG, CIBLE): "org_member", (ORG, SIMPLE): "org_member",
    (ORG, OPERATEUR): "org_member", (AUTRE, CIBLE): "org_admin",
    (AUTRE, DEHORS): "org_member",
}
EQUIPES = {EQUIPE: {"id": EQUIPE, "org_id": ORG}, EQUIPE_AUTRE: {"id": EQUIPE_AUTRE,
                                                                 "org_id": AUTRE}}
VUE = {"X-Oto-View-As": CIBLE, "X-Oto-Org": str(ORG)}

vu: dict = {}


async def _atteint(request):
    vu.clear()
    vu.update(borne=session_org.current_view_as_bound_org(),
              cible=session_org.current_view_user(), org=session_org.current_view_org())
    return JSONResponse({"atteint": True})


@pytest.fixture
def client(monkeypatch):
    def _client(appelant: str):
        async def authentifie(request, verifier, **kw):
            request.scope["oto_principal"] = {"sub": appelant}
            return appelant, None
        monkeypatch.setattr(api_routes, "_authenticate", authentifie)
        monkeypatch.setattr(access_scope, "get_user_role",
                            lambda sub: "admin" if sub == OPERATEUR else "member")
        monkeypatch.setattr(db, "get_user", lambda sub: {"sub": sub})
        monkeypatch.setattr(org_store, "get_org_role",
                            lambda org_id, sub: ROLES.get((int(org_id), sub)))
        monkeypatch.setattr(group_store, "get_group", lambda gid: EQUIPES.get(int(gid)))
        from oto_mcp import roles
        monkeypatch.setattr(roles, "can_read_group",
                            lambda sub, gid: ROLES.get((EQUIPES[int(gid)]["org_id"], sub))
                            is not None)
        app = Starlette(routes=[Route("/{p:path}", _atteint,
                                      methods=["GET", "POST", "DELETE", "PUT", "PATCH"])])
        return TestClient(api_routes.ViewAsMiddleware(app, verifier=None))
    return _client


def _get(c, chemin, entetes=VUE):
    r = c.get(chemin, headers={"Authorization": "Bearer x", **entetes})
    return r.status_code, r.json().get("error")


def _post(c, chemin, corps, entetes=VUE):
    r = c.post(chemin, json=corps, headers={"Authorization": "Bearer x", **entetes})
    return r.status_code, r.json().get("error")


# ── L'entrée ────────────────────────────────────────────────────────────────

def test_l_org_admin_voit_en_tant_qu_un_membre_de_son_org(client):
    c = client(ADMIN)
    assert _get(c, "/api/me") == (200, None)
    assert vu == {"borne": ORG, "cible": CIBLE, "org": ORG}


def test_l_equipe_consultee_donne_l_org_de_la_vue(client):
    c = client(ADMIN)
    assert _get(c, "/api/me", {"X-Oto-View-As": CIBLE, "X-Oto-Group": str(EQUIPE)}) == (
        200, None)
    assert vu["borne"] == ORG


def test_un_simple_membre_est_refuse(client):
    assert _get(client(SIMPLE), "/api/me") == (403, "forbidden")


def test_l_admin_d_une_org_n_ouvre_pas_la_vue_dans_une_autre(client):
    assert _get(client(ADMIN), "/api/me", {"X-Oto-View-As": CIBLE,
                                           "X-Oto-Org": str(AUTRE)}) == (403, "forbidden")


def test_sans_org_la_vue_est_refusee_nommement(client):
    assert _get(client(ADMIN), "/api/me", {"X-Oto-View-As": CIBLE}) == (
        400, "view_as_org_required")


def test_une_cible_hors_de_l_org_est_refusee(client):
    assert _get(client(ADMIN), "/api/me", {"X-Oto-View-As": DEHORS,
                                           "X-Oto-Org": str(ORG)}) == (403, "view_as_hors_org")


def test_une_cible_operateur_plateforme_est_refusee(client):
    """Ses droits débordent toute org : sa vue ne se borne pas."""
    assert _get(client(ADMIN), "/api/me", {"X-Oto-View-As": OPERATEUR,
                                           "X-Oto-Org": str(ORG)}) == (403, "view_as_hors_org")


def test_une_equipe_d_une_autre_org_est_refusee(client):
    assert _get(client(ADMIN), "/api/me", {**VUE, "X-Oto-Group": str(EQUIPE_AUTRE)}) == (
        403, "view_as_hors_org")


def test_un_run_place_la_requete_hors_de_l_org(client):
    assert _get(client(ADMIN), "/api/me", {**VUE, "X-Oto-Run": "run-1"}) == (
        403, "view_as_hors_org")


def test_soi_meme_n_ouvre_pas_de_vue(client):
    c = client(ADMIN)
    assert _get(c, "/api/me", {"X-Oto-View-As": ADMIN, "X-Oto-Org": str(ORG)}) == (200, None)
    assert vu["borne"] is None and vu["cible"] is None


# ── Lecture seule stricte ───────────────────────────────────────────────────

def test_l_org_admin_n_ecrit_jamais(client):
    c = client(ADMIN)
    creer = {"op": "create", "name": "x"}
    assert _post(c, "/api/me/projects", creer) == (403, "view_as_read_only")
    assert _post(c, "/api/me/projects", creer, {**VUE, "X-Oto-View-As-Write": "1"}) == (
        403, "view_as_write_forbidden")
    r = c.delete(f"/api/datastores/1", headers={"Authorization": "Bearer x", **VUE,
                                                  "X-Oto-View-As-Write": "1"})
    assert (r.status_code, r.json()["error"]) == (403, "view_as_write_forbidden")


# ── Liste fermée et épinglage ───────────────────────────────────────────────

@pytest.mark.parametrize("chemin", [
    "/api/me/tokens", "/api/me/connector-accounts/grants", "/api/me/datastores/shared",
    "/api/me/model-subscriptions", "/api/me/connector-instances", "/api/me/billing",
    "/api/admin/users", "/api/datastore/namespaces", "/api/me/legal"])
def test_les_listes_compte_entier_sont_refusees(client, chemin):
    assert _get(client(ADMIN), chemin) == (403, "view_as_hors_org")


def test_une_lecture_op_aware_non_listee_est_refusee(client):
    c = client(ADMIN)
    assert _post(c, "/api/resources", {"op": "list"}) == (403, "view_as_hors_org")
    assert _post(c, "/api/me/projects", {"op": "list"}) == (200, None)


def test_org_et_equipe_du_chemin_sont_epinglees_sur_l_org(client):
    c = client(ADMIN)
    assert _get(c, f"/api/orgs/{ORG}") == (200, None)
    assert _get(c, f"/api/orgs/{ORG}/groups") == (200, None)
    assert _get(c, f"/api/orgs/{AUTRE}") == (403, "view_as_hors_org")
    assert _get(c, f"/api/orgs/{AUTRE}/monitoring/summary") == (403, "view_as_hors_org")
    assert _get(c, f"/api/groups/{EQUIPE}") == (200, None)
    assert _get(c, f"/api/groups/{EQUIPE_AUTRE}/instructions") == (403, "view_as_hors_org")
    assert _get(c, "/api/groups/abc") == (403, "view_as_hors_org")


def test_le_contexte_borne_ne_fuit_pas_hors_de_la_requete(client):
    _get(client(ADMIN), "/api/me")
    assert session_org.current_view_as_bound_org() is None
    assert session_org.current_view_user() is None


# ── La vue d'opérateur ne change pas ────────────────────────────────────────

def test_la_vue_d_operateur_reste_non_bornee(client):
    c = client(OPERATEUR)
    assert _get(c, "/api/me/tokens", {"X-Oto-View-As": CIBLE}) == (200, None)
    assert vu == {"borne": None, "cible": CIBLE, "org": None}


# ── Le journal ──────────────────────────────────────────────────────────────

def _journal(monkeypatch, appelant, entetes, chemin="/api/me"):
    capture: dict = {}
    monkeypatch.setattr(db, "insert_tool_call", lambda row: capture.update(row))

    async def authentifie(request, verifier, **kw):
        request.scope["oto_principal"] = {"sub": appelant}
        return appelant, None
    monkeypatch.setattr(api_routes, "_authenticate", authentifie)
    monkeypatch.setattr(access_scope, "get_user_role", lambda sub: "member")
    monkeypatch.setattr(db, "get_user", lambda sub: {"sub": sub})
    monkeypatch.setattr(org_store, "get_org_role",
                        lambda org_id, sub: ROLES.get((int(org_id), sub)))

    async def aval(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})

    pile = api_routes.RestCallLogger(api_routes.ViewAsMiddleware(aval, verifier=None))
    brut = [(k.lower().encode(), v.encode())
            for k, v in {"Authorization": "Bearer x", **entetes}.items()]
    scope = {"type": "http", "path": chemin, "method": "GET", "headers": brut,
             "query_string": b""}

    async def recevoir():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def envoyer(message):
        pass

    async def conduire():
        await pile(scope, recevoir, envoyer)
        await asyncio.sleep(0)
        await asyncio.gather(*list(api_routes._REST_LOG_TASKS), return_exceptions=True)

    asyncio.run(conduire())
    return capture


def test_meme_journal_que_la_vue_d_operateur(monkeypatch):
    ligne = _journal(monkeypatch, ADMIN, VUE)
    assert (ligne["sub"], ligne["view_as_sub"], ligne["ok"]) == (ADMIN, CIBLE, True)
    assert not (ligne.get("args") or {}).get("view_as_write")


def test_une_vue_refusee_ne_journalise_pas_de_cible(monkeypatch):
    ligne = _journal(monkeypatch, SIMPLE, VUE)
    assert ligne["view_as_sub"] is None and ligne["ok"] is False


# ── Hygiène de la liste ─────────────────────────────────────────────────────

def test_la_liste_ne_porte_que_des_ops_de_lecture():
    for (verbe, chemin), ops in api_routes._LECTURES_VUE_BORNEE.items():
        assert (ops is None) == (verbe == "GET"), (verbe, chemin)
        assert ops is None or ops <= api_routes._READ_OPS, (chemin, ops - api_routes._READ_OPS)


def test_la_liste_ne_nomme_que_des_routes_servies():
    """Une entrée sans route est une porte ouverte à la prochaine route de ce nom."""
    table = (pathlib.Path(__file__).parent / "api_routes_table.txt").read_text()
    servies = set()
    for ligne in table.splitlines():
        verbes, _, reste = ligne.partition(" ")
        chemin = reste.split(" -> ")[0]
        for v in verbes.split(","):
            servies.add((v, re.sub(r"\{([^}:]+):[^}]+\}", r"{\1}", chemin)))
    # Les capacités ne figurent pas toutes dans la table écrite à la main : on y
    # ajoute leurs liaisons REST, telles que l'adaptateur les monte.
    from oto_mcp.capabilities import registry
    for cap in registry.CAPABILITIES:
        for b in cap.rest_bindings():
            servies.add((b.verb, re.sub(r"\{([^}:]+):[^}]+\}", r"{\1}", b.path)))
    manquantes = [k for k in api_routes._LECTURES_VUE_BORNEE if k not in servies]
    assert not manquantes, manquantes
