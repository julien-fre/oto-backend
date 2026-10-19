"""zoho/google/salesforce — la convention unique de retour OAuth
(`?connector=<nom>&connect=connected|error|forbidden|denied`, oto-backend#670) au
niveau de la ROUTE (callback réel, pas la fabrique seule).

- **zoho** et **google** (`api/datastore.py`) doublaient un temps l'ancienne
  forme lue par le dashboard (`?zoho=connected`, `?google=connected`) — retirée
  le 17/09/2026, sans préavis, faute de lecteur mesuré. Ces bancs vérifient
  désormais son ABSENCE, et rougissent si quelqu'un la remet.
- **salesforce** est déjà la forme cible : test de non-régression après le
  passage au fabricant partagé (`auth.flow.connector_return_url`).
"""
from __future__ import annotations

import asyncio
import urllib.parse

import pytest
from starlette.requests import Request
from starlette.responses import RedirectResponse

from oto_mcp.api import datastore as datastore_routes
from oto_mcp.api import salesforce as salesforce_routes
from oto_mcp.api import zoho as zoho_routes


def _json_response(_request, payload, status=200):
    return {"status": status, "body": payload}


def _json_error(_request, status, code, message=None):
    return {"status": status, "error": code, "detail": message}


async def _options_handler(_request):
    return None


def _get(path: str, query: str = "") -> Request:
    async def _receive():
        return {"type": "http.request", "body": b"", "more_body": False}
    return Request({"type": "http", "method": "GET", "path": path,
                    "headers": [], "query_string": query.encode(), "path_params": {}},
                   receive=_receive)


def _endpoint(module, path: str, *, with_cors: bool = False):
    args = [None, None, _json_response, _json_error]
    if with_cors:
        args.append(lambda origin: {})
    args.append(_options_handler)
    routes = module.make_routes(*args)
    return next(r.endpoint for r in routes if r.path == path)


def _qs(url: str) -> dict:
    return urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)


def _location(response) -> str:
    return response.headers["location"]


# --- zoho : l'ancienne forme et la nouvelle, DANS LA MÊME redirection ----------

ZOHO_CALLBACK = "/api/zoho/oauth/callback"


def test_zoho_state_illisible_double_zoho_error(monkeypatch):
    monkeypatch.setattr(zoho_routes.zoho_oauth, "verify_state", lambda s: None)
    handler = _endpoint(zoho_routes, ZOHO_CALLBACK)
    resp = asyncio.run(handler(_get(ZOHO_CALLBACK, "state=bad")))
    q = _qs(_location(resp))
    assert q.get("zoho") is None, "ancienne forme réapparue — elle est retirée depuis le 17/09"
    assert q["connector"] == ["zoho"] and q["connect"] == ["error"]


def test_zoho_succes_ne_sert_que_la_forme_neuve(monkeypatch):
    """Succès sur `zohodesk` (pas `zoho`) : seule la forme neuve est servie,
    portant le connecteur RÉEL."""
    parsed = {"sub": "u1", "org": 5, "group": None, "connector": "zohodesk",
             "data_center": "eu", "level": "full", "return_app": ""}
    monkeypatch.setattr(zoho_routes.zoho_oauth, "verify_state", lambda s: parsed)
    monkeypatch.setattr(zoho_routes.roles, "is_org_member", lambda sub, org: True)
    monkeypatch.setattr(zoho_routes.zoho_oauth, "app_fields", lambda *a, **k: {})
    monkeypatch.setattr(zoho_routes.zoho_oauth, "exchange_code",
                        lambda *a, **k: {"refresh_token": "rt"})
    monkeypatch.setattr(zoho_routes.zoho_oauth, "persist", lambda *a, **k: None)
    handler = _endpoint(zoho_routes, ZOHO_CALLBACK)
    resp = asyncio.run(handler(_get(ZOHO_CALLBACK, "code=c&state=s")))
    q = _qs(_location(resp))
    assert q.get("zohodesk") is None, "ancienne forme réapparue — elle est retirée depuis le 17/09"
    assert q["connector"] == ["zohodesk"] and q["connect"] == ["connected"]


def test_zoho_retour_rejoue_l_org_l_equipe_et_le_niveau_du_state(monkeypatch):
    """Le retour, appelé par Zoho sans session, résout l'app sous l'org ET l'équipe
    SIGNÉES au départ — pas sous l'équipe maison du compte — et range le niveau demandé :
    le code s'échange avec l'app pour laquelle il a été émis."""
    parsed = {"sub": "u1", "org": 5, "group": 9, "connector": "zoho",
              "data_center": "eu", "level": "read", "return_app": ""}
    vu = {}
    monkeypatch.setattr(zoho_routes.zoho_oauth, "verify_state", lambda s: parsed)
    monkeypatch.setattr(zoho_routes.roles, "is_org_member", lambda sub, org: True)
    monkeypatch.setattr(zoho_routes.zoho_oauth, "app_fields",
                        lambda c, sub, dc, *, org, group: vu.update(app=(org, group, dc))
                        or {"client_id": "a", "client_secret": "b"})
    monkeypatch.setattr(zoho_routes.zoho_oauth, "exchange_code",
                        lambda *a, **k: {"refresh_token": "rt"})
    monkeypatch.setattr(zoho_routes.zoho_oauth, "persist",
                        lambda *a, level, **k: vu.update(level=level))
    handler = _endpoint(zoho_routes, ZOHO_CALLBACK)
    resp = asyncio.run(handler(_get(ZOHO_CALLBACK, "code=c&state=s")))
    assert _qs(_location(resp))["connect"] == ["connected"]
    assert vu == {"app": (5, 9, "eu"), "level": "read"}


def test_zoho_retour_hors_de_l_org_du_depart_echoue(monkeypatch):
    """Sorti de l'org entre l'aller et le retour : rien n'est échangé ni rangé."""
    parsed = {"sub": "u1", "org": 5, "group": None, "connector": "zoho",
              "data_center": "eu", "level": "full", "return_app": ""}
    monkeypatch.setattr(zoho_routes.zoho_oauth, "verify_state", lambda s: parsed)
    monkeypatch.setattr(zoho_routes.roles, "is_org_member", lambda sub, org: False)

    def _jamais(*a, **k):
        raise AssertionError("aucun échange hors de l'org du départ")
    monkeypatch.setattr(zoho_routes.zoho_oauth, "exchange_code", _jamais)
    handler = _endpoint(zoho_routes, ZOHO_CALLBACK)
    resp = asyncio.run(handler(_get(ZOHO_CALLBACK, "code=c&state=s")))
    assert _qs(_location(resp))["connect"] == ["error"]


def test_zoho_echange_echoue_ne_sert_que_la_forme_neuve(monkeypatch):
    """La forme neuve porte le connecteur réellement visé, y compris pour
    zohodesk/zohoanalytics ; plus aucune ancienne forme à côté."""
    parsed = {"sub": "u1", "org": 5, "group": None, "connector": "zohoanalytics",
             "data_center": "eu", "level": "full", "return_app": ""}
    monkeypatch.setattr(zoho_routes.zoho_oauth, "verify_state", lambda s: parsed)
    monkeypatch.setattr(zoho_routes.roles, "is_org_member", lambda sub, org: True)
    monkeypatch.setattr(zoho_routes.zoho_oauth, "app_fields", lambda *a, **k: {})

    def _boom(*a, **k):
        raise RuntimeError("refus du fournisseur")
    monkeypatch.setattr(zoho_routes.zoho_oauth, "exchange_code", _boom)
    handler = _endpoint(zoho_routes, ZOHO_CALLBACK)
    resp = asyncio.run(handler(_get(ZOHO_CALLBACK, "code=c&state=s")))
    q = _qs(_location(resp))
    assert q.get("zoho") is None, "ancienne forme réapparue — elle est retirée depuis le 17/09"
    assert q["connector"] == ["zohoanalytics"] and q["connect"] == ["error"]


# --- google (api/datastore.py) : forme neuve seule, échec enfin redirigé -----

GOOGLE_CALLBACK = "/api/google/oauth/callback"


def test_google_succes_ne_sert_que_la_forme_neuve(monkeypatch):
    monkeypatch.setattr(datastore_routes.google_oauth, "verify_state", lambda s: ("sub-1", 7, "", "google"))
    monkeypatch.setattr(datastore_routes.google_oauth, "exchange_code",
                        lambda code, sub: {"access_token": "a", "refresh_token": "r"})
    monkeypatch.setattr(datastore_routes.google_oauth, "persist_token",
                        lambda sub, org, tok: "e@x.io")
    handler = _endpoint(datastore_routes, GOOGLE_CALLBACK, with_cors=True)
    resp = asyncio.run(handler(_get(GOOGLE_CALLBACK, "code=c&state=s")))
    q = _qs(_location(resp))
    assert q.get("google") is None, "ancienne forme réapparue — elle est retirée depuis le 17/09"
    assert q["connector"] == ["google"] and q["connect"] == ["connected"]


def test_google_partage_recoit_la_carte_du_state(monkeypatch):
    """Un compte PARTAGÉ est borné par la carte qui a demandé le consentement (revue
    de #1081) : le callback la lui passe, telle que le state signé la porte."""
    recus = []
    monkeypatch.setattr(datastore_routes.google_oauth, "verify_state",
                        lambda s: ("sub-1", 7, "", "drive", "org", None))
    monkeypatch.setattr(datastore_routes.google_oauth, "exchange_code",
                        lambda code, sub: {"access_token": "a", "refresh_token": "r"})
    monkeypatch.setattr(datastore_routes.google_oauth, "persist_token",
                        lambda sub, org, tok, **k: recus.append(k) or "e@x.io")
    handler = _endpoint(datastore_routes, GOOGLE_CALLBACK, with_cors=True)
    resp = asyncio.run(handler(_get(GOOGLE_CALLBACK, "code=c&state=s")))
    assert _qs(_location(resp))["connect"] == ["connected"]
    assert recus == [{"scope": "org", "group_id": None, "connector": "drive"}]


@pytest.mark.parametrize("brise", ["absent", "illisible", "echange"])
def test_google_echec_redirige_desormais_au_lieu_dun_json_brut(monkeypatch, brise):
    """Avant ce lot, ces trois cas rendaient un JSON brut (400/400/502) — aucune
    redirection du tout. Pur ajout : rien n'existait à doubler à cette place."""
    handler = _endpoint(datastore_routes, GOOGLE_CALLBACK, with_cors=True)
    if brise == "absent":
        req = _get(GOOGLE_CALLBACK, "")
    elif brise == "illisible":
        monkeypatch.setattr(datastore_routes.google_oauth, "verify_state", lambda s: None)
        req = _get(GOOGLE_CALLBACK, "code=c&state=bad")
    else:
        monkeypatch.setattr(datastore_routes.google_oauth, "verify_state", lambda s: ("sub-1", 7, "", "google"))

        def _boom(code, sub):
            raise RuntimeError("refus du fournisseur")
        monkeypatch.setattr(datastore_routes.google_oauth, "exchange_code", _boom)
        req = _get(GOOGLE_CALLBACK, "code=c&state=s")

    resp = asyncio.run(handler(req))
    assert isinstance(resp, RedirectResponse), (
        f"toujours un JSON brut, pas une redirection (oto-backend#670) : {resp!r}")
    q = _qs(_location(resp))
    assert q["connector"] == ["google"] and q["connect"] == ["error"]
    assert q.get("google") is None, "rien à doubler ici : l'échec ne redirigeait jamais avant"


# --- salesforce : déjà la forme cible, non-régression après le refactor -------

def test_salesforce_conserve_sa_forme_apres_le_passage_au_fabricant_partage(monkeypatch):
    monkeypatch.setattr(salesforce_routes.salesforce_oauth, "verify_state", lambda s: None)
    handler = _endpoint(salesforce_routes, "/api/salesforce/oauth/callback")
    resp = asyncio.run(handler(_get("/api/salesforce/oauth/callback", "state=bad")))
    q = _qs(_location(resp))
    assert q["connector"] == ["salesforce"] and q["connect"] == ["error"]
    assert "salesforce" not in q, "salesforce n'a jamais servi de clé `salesforce=`"


# --- le refus chez le fournisseur : `denied`, sur la carte du state (oto-app#11) ---
#
# Le refus revient SANS code (`error=access_denied`). Les trois callbacks le lisaient
# comme un « code absent » : `error`, et vers le front par défaut faute d'avoir lu le
# state. Le dashboard affichait un échec technique là où la personne avait refusé.

_STATE_LU = {
    "google": (datastore_routes, "google_oauth", GOOGLE_CALLBACK,
               ("sub-1", 7, "", "drive"), "drive"),
    "salesforce": (salesforce_routes, "salesforce_oauth", "/api/salesforce/oauth/callback",
                   ("sub-1", 7, "org", "verif", None, ""), "salesforce"),
    "zoho": (zoho_routes, "zoho_oauth", ZOHO_CALLBACK,
             {"sub": "u1", "org": 5, "group": None, "connector": "zohodesk",
              "data_center": "eu", "level": "full", "return_app": ""}, "zohodesk"),
}


def _callback_au_state_lu(monkeypatch, nom: str, query: str):
    module, auth, path, parsed, _carte = _STATE_LU[nom]

    def _jamais(*a, **k):
        raise AssertionError("un retour sans code n'échange rien et ne relit aucun rôle")
    monkeypatch.setattr(getattr(module, auth), "verify_state", lambda s: parsed)
    monkeypatch.setattr(getattr(module, auth), "exchange_code", _jamais)
    if nom == "zoho":
        monkeypatch.setattr(zoho_routes.roles, "is_org_member", _jamais)
    from oto_mcp import roles
    monkeypatch.setattr(roles, "is_org_admin", _jamais)
    handler = _endpoint(module, path, with_cors=(nom == "google"))
    return _qs(_location(asyncio.run(handler(_get(path, query)))))


@pytest.mark.parametrize("nom", sorted(_STATE_LU))
def test_un_refus_chez_le_fournisseur_revient_en_denied(monkeypatch, nom):
    q = _callback_au_state_lu(monkeypatch, nom, "error=access_denied&state=s")
    assert q["connect"] == ["denied"], "un refus du fournisseur n'est ni une panne ni un refus d'oto"
    assert q["connector"] == [_STATE_LU[nom][4]], "la carte du state, pas le défaut"


@pytest.mark.parametrize("nom", sorted(_STATE_LU))
@pytest.mark.parametrize("query", ["error=server_error&state=s", "state=s"])
def test_une_autre_erreur_ou_un_code_absent_reste_une_panne(monkeypatch, nom, query):
    q = _callback_au_state_lu(monkeypatch, nom, query)
    assert q["connect"] == ["error"]
    assert q["connector"] == [_STATE_LU[nom][4]]


@pytest.mark.parametrize("nom", sorted(_STATE_LU))
def test_un_refus_au_state_illisible_reste_une_panne(monkeypatch, nom):
    """Sans state lisible, rien ne dit que ce retour est le nôtre : `error`, jamais
    un `denied` sur la foi d'un paramètre que n'importe qui peut poser."""
    module, auth, path, _parsed, _carte = _STATE_LU[nom]
    monkeypatch.setattr(getattr(module, auth), "verify_state", lambda s: None)
    handler = _endpoint(module, path, with_cors=(nom == "google"))
    q = _qs(_location(asyncio.run(handler(_get(path, "error=access_denied&state=bad")))))
    assert q["connect"] == ["error"]
