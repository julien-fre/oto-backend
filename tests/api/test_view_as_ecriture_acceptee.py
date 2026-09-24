"""« Voir en tant que » : l'écriture, seulement après un geste d'acceptation (24/09/2026).

Décision d'Alexis : un opérateur doit pouvoir ÉCRIRE au nom d'un membre (cas vécu :
poser les clés de paie PERSONNELLES d'une admin cliente, à sa place), mais jamais par
accident. Le contrat, que le dashboard suit :

- une écriture en `X-Oto-View-As` reste refusée (`view_as_read_only`) sans l'en-tête
  d'acceptation `X-Oto-View-As-Write: 1` — compatibilité stricte ;
- avec lui, seul un **super_admin** passe ; le rôle `admin` (supervision) reçoit
  `view_as_write_forbidden` ;
- l'écriture s'exécute sous l'identité de la CIBLE, qui doit être membre de l'org
  consultée ; l'opérateur reste nommé (journal, `meta.set_by_operator` d'une clé) ;
- le journal marque la ligne (`args.view_as_write`), la range sous l'org où elle a agi,
  et l'org de la cible la lit (`org.monitoring.view_as_writes`) ;
- le secret d'une clé ne passe JAMAIS par le journal.

Les règles de rôle sont les vraies ; seule la base est doublée, sauf pour la lecture
du journal côté org, jouée contre PostgreSQL.
"""
from __future__ import annotations

import asyncio
import json
import logging

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from oto_mcp import access, db, org_store, session_org
from oto_mcp.access import scope as access_scope
from oto_mcp.api import routes as api_routes

ORG = 353
CIBLE, OPERATEUR = "u-cible", "u-operateur"
SECRET = "pf_live_CLE_FICTIVE_0123456789abcdef"
VUE = {"X-Oto-View-As": CIBLE}
ACCEPTE = {"X-Oto-View-As-Write": "1"}
ROUTE_CLE = "/api/settings/api-keys/payfit"
CORPS_CLE = {"fields": {"api_key": SECRET, "account": "ELITE CENTER"}}

vu_par_la_route: dict = {}


async def _atteint(request):
    vu_par_la_route.clear()
    vu_par_la_route.update(cible=session_org.current_view_user(),
                           operateur=session_org.current_view_as_operator())
    return JSONResponse({"atteint": True})


@pytest.fixture
def monter(monkeypatch):
    def _monter(plateforme, *, cible_membre=True):
        async def authentifie(request, verifier, **kw):
            return OPERATEUR, None
        monkeypatch.setattr(api_routes, "_authenticate", authentifie)
        monkeypatch.setattr(access_scope, "get_user_role",
                            lambda sub: plateforme if sub == OPERATEUR else "member")
        monkeypatch.setattr(db, "get_user", lambda sub: {"sub": sub})
        # L'opérateur n'est membre d'aucune org ; la cible l'est de l'org consultée.
        monkeypatch.setattr(org_store, "get_org_role",
                            lambda org_id, sub: ("org_admin" if sub == CIBLE and cible_membre
                                                 else None))
        monkeypatch.setattr(access, "current_org", lambda sub: ORG)
        app = Starlette(routes=[Route("/{p:path}", _atteint,
                                      methods=["GET", "POST", "DELETE", "PUT"])])
        return TestClient(api_routes.ViewAsMiddleware(app, verifier=None))
    return _monter


def _poser_la_cle(c, entetes):
    r = c.request("POST", ROUTE_CLE, json=CORPS_CLE,
                  headers={"Authorization": "Bearer x", **entetes})
    return r.status_code, r.json().get("error")


# ── Les gardes ──────────────────────────────────────────────────────────────

def test_sans_le_geste_d_acceptation_l_ecriture_reste_refusee(monter):
    """(1) Compatibilité : rien ne change pour qui ne pose pas l'en-tête."""
    assert _poser_la_cle(monter("super_admin"), VUE) == (403, "view_as_read_only")


def test_le_role_admin_ne_peut_pas_accepter_l_ecriture(monter):
    """(2) La supervision n'écrit pas au nom d'un autre, même en acceptant."""
    assert _poser_la_cle(monter("admin"), {**VUE, **ACCEPTE}) == (
        403, "view_as_write_forbidden")


def test_le_super_admin_ecrit_sous_l_identite_de_la_cible(monter):
    """(3) L'écriture traverse, sous la cible, l'opérateur restant nommé."""
    c = monter("super_admin")
    assert _poser_la_cle(c, {**VUE, **ACCEPTE}) == (200, None)
    assert vu_par_la_route == {"cible": CIBLE, "operateur": OPERATEUR}


def test_le_contexte_operateur_ne_fuit_pas_hors_de_la_requete(monter):
    _poser_la_cle(monter("super_admin"), {**VUE, **ACCEPTE})
    assert session_org.current_view_as_operator() is None
    assert session_org.current_view_user() is None


def test_une_lecture_n_arme_pas_le_contexte_operateur(monter):
    """(6) La lecture en view-as est inchangée — et reste une LECTURE, même avec
    l'en-tête : l'opérateur n'y est pas publié comme auteur d'une écriture."""
    c = monter("super_admin")
    for entetes in (VUE, {**VUE, **ACCEPTE}):
        r = c.request("GET", "/api/me", headers={"Authorization": "Bearer x", **entetes})
        assert r.status_code == 200
        assert vu_par_la_route == {"cible": CIBLE, "operateur": None}
    lecture = c.request("POST", "/api/me/runner/fleets", json={"op": "list"},
                        headers={"Authorization": "Bearer x", **VUE})
    assert lecture.status_code == 200


def test_on_n_ecrit_pas_pour_la_cible_dans_une_org_qui_n_est_pas_la_sienne(monter):
    """Anti-IDOR : c'est la CIBLE qui agit ; hors de son org, pas d'écriture."""
    c = monter("super_admin", cible_membre=False)
    entetes = {**VUE, **ACCEPTE, "X-Oto-Org": str(ORG)}
    assert _poser_la_cle(c, entetes) == (403, "forbidden")
    # …mais l'inspection en lecture de cette org reste permise, comme avant.
    r = c.request("GET", f"/api/orgs/{ORG}", headers={"Authorization": "Bearer x", **entetes})
    assert r.status_code == 200


def test_la_cible_membre_de_l_org_consultee_y_ecrit(monter):
    c = monter("super_admin")
    assert _poser_la_cle(c, {**VUE, **ACCEPTE, "X-Oto-Org": str(ORG)}) == (200, None)


def test_l_en_tete_d_acceptation_est_autorise_par_cors():
    from oto_mcp.api import base
    origine = base._allowed_origins()[0]
    entetes = base._cors_headers(origine)["Access-Control-Allow-Headers"]
    assert "X-Oto-View-As-Write" in entetes and "X-Oto-View-As" in entetes


# ── Le journal ──────────────────────────────────────────────────────────────

def _journaliser(monkeypatch, *, plateforme="super_admin", entetes, corps=CORPS_CLE,
                 methode="POST", chemin=ROUTE_CLE):
    """Le vrai empilement servi : RestCallLogger enveloppe ViewAsMiddleware."""
    capture: dict = {}
    monkeypatch.setattr(db, "insert_tool_call", lambda row: capture.update(row))

    async def authentifie(request, verifier, **kw):
        request.scope["oto_principal"] = {"sub": OPERATEUR}
        return OPERATEUR, None
    monkeypatch.setattr(api_routes, "_authenticate", authentifie)
    monkeypatch.setattr(access_scope, "get_user_role",
                        lambda sub: plateforme if sub == OPERATEUR else "member")
    monkeypatch.setattr(db, "get_user", lambda sub: {"sub": sub})
    monkeypatch.setattr(org_store, "get_org_role",
                        lambda org_id, sub: "org_admin" if sub == CIBLE else None)
    monkeypatch.setattr(access, "current_org", lambda sub: ORG)

    async def aval(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})

    pile = api_routes.RestCallLogger(api_routes.ViewAsMiddleware(aval, verifier=None))
    brut = [(k.lower().encode(), v.encode())
            for k, v in {"Authorization": "Bearer x", **entetes}.items()]
    scope = {"type": "http", "path": chemin, "method": methode, "headers": brut,
             "query_string": b""}
    corps_brut = json.dumps(corps).encode()

    async def recevoir():
        return {"type": "http.request", "body": corps_brut, "more_body": False}

    async def envoyer(message):
        pass

    async def conduire():
        await pile(scope, recevoir, envoyer)
        await asyncio.sleep(0)
        await asyncio.gather(*list(api_routes._REST_LOG_TASKS), return_exceptions=True)

    asyncio.run(conduire())
    return capture


def test_l_ecriture_acceptee_est_journalisee_operateur_et_cible(monkeypatch):
    """(4) `sub` = l'opérateur, `view_as_sub` = la cible, marqueur d'écriture, et
    l'org où elle a agi — même sans `X-Oto-Org` (sinon aucune org ne la verrait)."""
    ligne = _journaliser(monkeypatch, entetes={**VUE, **ACCEPTE})
    assert ligne["sub"] == OPERATEUR
    assert ligne["view_as_sub"] == CIBLE
    assert ligne["args"] == {"view_as_write": True}
    assert ligne["org_id"] == ORG
    assert ligne["ok"] is True


def test_une_consultation_n_est_pas_marquee_ecriture(monkeypatch):
    ligne = _journaliser(monkeypatch, entetes=VUE, methode="GET", chemin="/api/me",
                         corps={})
    assert ligne["view_as_sub"] == CIBLE
    assert not (ligne.get("args") or {}).get("view_as_write")


def test_le_secret_n_entre_ni_dans_le_journal_ni_dans_les_logs(monkeypatch, caplog):
    """(5) Le corps de la pose n'est jamais journalisé : la clé fictive ne doit
    apparaître nulle part — ligne de journal comme logs applicatifs."""
    caplog.set_level(logging.DEBUG)
    ligne = _journaliser(monkeypatch, entetes={**VUE, **ACCEPTE})
    assert SECRET not in json.dumps(ligne, default=str)
    assert SECRET not in caplog.text


# ── La clé posée garde son opérateur ───────────────────────────────────────

def test_la_cle_perso_posee_en_view_as_nomme_son_operateur(monkeypatch):
    """La clé est celle de la cible (`set_by` = elle) ; `meta.set_by_operator` dit qui
    l'a saisie. Hors view-as, rien n'est ajouté."""
    from oto_mcp import credentials_store
    from oto_mcp.capabilities import me_credentials
    from oto_mcp.capabilities._types import ResolvedCtx

    posees: list = []
    monkeypatch.setattr(credentials_store, "set_credential",
                        lambda *a, **kw: posees.append(kw))
    monkeypatch.setattr(credentials_store, "pack_secret", lambda provider, fields: b"x")
    monkeypatch.setattr(credentials_store, "meta_fields", lambda provider, fields: {})
    pose = me_credentials._PoseCredentielle(account="ELITE CENTER", org_id=ORG, eid=CIBLE,
                                            fields={"api_key": SECRET}, st=None,
                                            pending=False)
    ctx = ResolvedCtx(sub=CIBLE)
    inp = me_credentials.CredentialSetInput(provider="payfit")

    jeton = session_org.set_view_as_operator(OPERATEUR)
    try:
        me_credentials._set_ecrire(ctx, inp, pose, verified=False)
    finally:
        session_org.reset_view_as_operator(jeton)
    me_credentials._set_ecrire(ctx, inp, pose, verified=False)

    assert posees[0]["set_by"] == CIBLE
    assert posees[0]["meta"] == {"set_by_operator": OPERATEUR}
    assert posees[1]["set_by"] == CIBLE
    assert not (posees[1]["meta"] or {}).get("set_by_operator")


# ── L'org de la cible lit ces écritures (contre PostgreSQL) ─────────────────

def test_l_org_lit_les_ecritures_faites_en_son_nom(live):
    """Seules les ÉCRITURES acceptées de CETTE org sortent : ni une consultation, ni
    une écriture d'une autre org, ni un appel ordinaire."""
    from oto_mcp.db import usage
    lignes = [
        # l'écriture acceptée, dans l'org 353
        {"kind": "rest", "tool": "POST /api/settings/api-keys/payfit", "sub": OPERATEUR,
         "org_id": ORG, "ok": True, "view_as_sub": CIBLE, "args": {"view_as_write": True}},
        # une consultation (pas de marqueur)
        {"kind": "rest", "tool": "GET /api/me", "sub": OPERATEUR, "org_id": ORG,
         "ok": True, "view_as_sub": CIBLE},
        # une écriture acceptée, mais ailleurs
        {"kind": "rest", "tool": "POST /api/settings/api-keys/payfit", "sub": OPERATEUR,
         "org_id": ORG + 1, "ok": True, "view_as_sub": CIBLE,
         "args": {"view_as_write": True}},
        # un geste ordinaire de la cible elle-même
        {"kind": "rest", "tool": "POST /api/settings/api-keys/payfit", "sub": CIBLE,
         "org_id": ORG, "ok": True},
    ]
    for l in lignes:
        usage.insert_tool_call(l)
    sortie = usage.list_view_as_writes(ORG)
    assert [(r["route"], r["operator_sub"], r["target_sub"]) for r in sortie] == [
        ("POST /api/settings/api-keys/payfit", OPERATEUR, CIBLE)]
