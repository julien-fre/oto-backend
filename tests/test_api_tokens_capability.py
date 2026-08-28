"""Jetons API et clés plateforme, en capacités : et la garde qui les y retenait.

Neuf routes ont quitté `api_routes_admin.py` (module supprimé) et
`api/datastore.py` pour `capabilities/api_tokens.py` (27/08).

**Le cœur de ce fichier est le premier test.** Les six routes de jetons portaient
`allow_api_token=False` — un jeton `oto_` ne peut ni lister, ni créer, ni révoquer de
jeton, sinon une fuite s'auto-entretient : révoquer le jeton fuité ne suffit plus,
l'attaquant s'en est fait un second, non expirant. `_rest_adapter` ne savait pas
exprimer ce cran, et c'est LA raison pour laquelle ces six routes étaient restées
écrites à la main. Le migrer sans le porter aurait été une régression de sécurité — on
vérifie donc que le drapeau PART vraiment vers `authenticate`, sur les six et sur elles
seules.

Le reste garde trois asymétries membre/admin, toutes servies telles quelles :
201 vs 200 à la création, `{ok}` vs `{ok, id}` à la suppression, et le contrôle de
visibilité des tableaux qui n'existe qu'au palier membre.
"""
from __future__ import annotations

import json

import pytest
from starlette.requests import Request
from starlette.responses import JSONResponse

from _datastore_rest import cap, call, stub_authz

from oto_mcp import credentials_store
from oto_mcp.datastore import core as datastore
from oto_mcp.capabilities import _rest_adapter, api_tokens as at

_JETONS = [{"id": 7, "label": "cli", "created_at": "2026-08-01", "last_used_at": None,
            "expires_at": None, "scopes": None}]
_PORTEE = {"namespaces": {"clients": "read"}}


# --- 1. LA garde : un jeton ne fabrique pas de jeton ------------------------

_SANS_JETON = ("me.token.list", "me.token.create", "me.token.delete",
               "platform.token.list", "platform.token.create", "platform.token.delete")
_AVEC_JETON = ("platform.key.list", "platform.key.create", "platform.key.delete")


@pytest.mark.parametrize("cle", _SANS_JETON)
def test_les_six_routes_de_jetons_refusent_un_porteur_de_jeton(cle):
    """On n'inspecte pas le descripteur : on JOUE la route et on lit ce qui arrive à
    `authenticate`. Un test qui relit `binding.allow_api_token` prouverait que le champ
    est posé, pas qu'il est APPLIQUÉ — or c'est l'application qui est la garde."""
    recu = _jouer_et_capturer_auth(cle)
    assert recu == {"allow_api_token": False}, (
        f"{cle} n'interdit plus le porteur de jeton : reçu {recu}. Un jeton qui peut "
        "en créer d'autres rend sa fuite auto-entretenue.")


@pytest.mark.parametrize("cle", _AVEC_JETON)
def test_les_cles_plateforme_n_heritent_pas_de_la_garde(cle):
    """Le cran est posé route par route, pas en bloc : les clés plateforme n'ont aucune
    raison de refuser un jeton, et l'adaptateur ne doit donc RIEN passer pour elles."""
    assert _jouer_et_capturer_auth(cle) == {}


def _jouer_et_capturer_auth(cle: str) -> dict:
    """Joue la route de `cle` avec un `authenticate` qui enregistre ses kwargs."""
    vus: dict = {}

    async def _auth(_req, _verifier, **kw):
        vus.update(kw)
        return None, JSONResponse({"error": "stop"}, status_code=401)   # on s'arrête là

    c = cap(cle)
    binding = c.rest_bindings()[0]
    handler = _rest_adapter._make_handler(
        c, binding, None, _auth,
        lambda _r, p, status=200: JSONResponse(p, status_code=status),
        lambda _r, s, code, d=None: JSONResponse({"error": code}, status_code=s))

    async def _receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    req = Request({"type": "http", "method": binding.verb, "path": binding.path,
                   "query_string": b"", "headers": [], "path_params": {}},
                  receive=_receive)
    import asyncio
    asyncio.run(handler(req))
    return vus


# --- Socle ------------------------------------------------------------------

@pytest.fixture()
def socle(monkeypatch):
    vus: list = []
    monkeypatch.setattr(at.db, "get_user", lambda sub: {"sub": sub})
    monkeypatch.setattr(at.db, "list_api_tokens", lambda sub: list(_JETONS))
    monkeypatch.setattr(at.db, "create_api_token",
                        lambda sub, label=None, ttl_days=None, scopes=None:
                        vus.append(("create", sub, label, ttl_days, scopes)) or "oto_SECRET")
    monkeypatch.setattr(at.db, "delete_api_token",
                        lambda sub, tid: vus.append(("delete", sub, tid)) or True)
    monkeypatch.setattr(at.db, "KEY_PROVIDERS", {"serper", "apollo"})
    monkeypatch.setattr(credentials_store, "list_platform_credentials",
                        lambda provider=None: [{"provider": "serper", "label": "prod",
                                                "set_at": "2026-08-01"}])
    monkeypatch.setattr(credentials_store, "set_credential",
                        lambda et, eid, conn, secret, set_by=None, **kw:
                        vus.append(("set", et, eid, conn, set_by)))
    monkeypatch.setattr(credentials_store, "clear_credential",
                        lambda et, eid, conn, **kw: vus.append(("clear", et, eid, conn)) or True)

    class _Store:
        def list_namespaces(self):
            return [{"namespace": "clients"}]

    monkeypatch.setattr(datastore, "make_store", lambda sub: _Store())
    return vus


@pytest.fixture()
def super_admin(monkeypatch):
    stub_authz(monkeypatch)
    from oto_mcp.capabilities import _authz
    monkeypatch.setattr(_authz.access, "is_super_admin", lambda sub: True)


# --- 2. Les trois asymétries membre / admin ---------------------------------

def test_la_creation_membre_rend_201_et_l_admin_200(monkeypatch, socle, super_admin):
    """Forme historique de chaque palier. « Harmoniser » casserait un appelant."""
    assert call("me.token.create", body={})[0] == 201
    assert call("platform.token.create", path_params={"sub": "u-9"}, body={})[0] == 200


def test_la_suppression_membre_rend_ok_seul_l_admin_rend_l_id(monkeypatch, socle,
                                                              super_admin):
    assert call("me.token.delete", path_params={"token_id": "7"})[1] == {"ok": True}
    assert call("platform.token.delete",
                path_params={"sub": "u-9", "token_id": "7"})[1] == {"ok": True, "id": 7}


def test_seul_le_palier_membre_refuse_un_tableau_invisible(monkeypatch, socle,
                                                           super_admin):
    """Au palier membre, un tableau que l'ÉMETTEUR ne voit pas est refusé : sinon le
    jeton serait muet et on le croirait branché. Au palier admin, le catalogue visé
    n'est pas celui de l'émetteur — la garde n'aurait aucun sens et n'existe pas."""
    portee = {"namespaces": {"inconnu": "read"}}
    code, out = call("me.token.create", body={"scopes": portee})
    assert code == 400 and out["error"] == "unknown_namespace"
    assert "inconnu" in out["detail"]
    # Le même corps passe au palier admin.
    assert call("platform.token.create", path_params={"sub": "u-9"},
                body={"scopes": portee})[0] == 200


# --- 3. Le secret, et ce qui n'en sort pas ----------------------------------

def test_le_secret_n_est_rendu_qu_a_la_creation(monkeypatch, socle):
    """La liste ne porte JAMAIS le secret : il n'est stocké que haché. Le déclarer dans
    `ApiToken` par erreur aurait fait générer chez un intégrateur un champ qu'il aurait
    attendu en vain."""
    stub_authz(monkeypatch)
    code, out = call("me.token.list")
    assert code == 200
    assert set(out["tokens"][0]) == set(at.ApiToken.model_fields)
    assert "token" not in out["tokens"][0]
    assert call("me.token.create", body={})[1]["token"] == "oto_SECRET"


def test_le_libelle_rendu_est_BRUT_celui_ecrit_est_nettoye(monkeypatch, socle):
    """Asymétrie servie : la réponse rend ce qu'on a envoyé, la base garde une version
    `strip()[:32]`. Le figer évite qu'un « nettoyage » change la réponse."""
    stub_authz(monkeypatch)
    brut = "  un libellé vraiment très long au-delà de trente-deux  "
    _, out = call("me.token.create", body={"label": brut})
    assert out["label"] == brut
    assert socle[0][2] == brut.strip()[:32]


def test_une_cle_plateforme_ne_rend_jamais_son_secret(monkeypatch, socle, super_admin):
    code, out = call("platform.key.list")
    assert code == 200
    assert set(out["platform_keys"][0]) == set(at.PlatformKey.model_fields)
    assert "api_key" not in out["platform_keys"][0]


# --- 4. Les refus, avec leur code servi -------------------------------------

@pytest.mark.parametrize("cle,pp", [("me.token.delete", {"token_id": "zz"}),
                                    ("platform.token.delete",
                                     {"sub": "u-9", "token_id": "zz"})])
def test_un_id_illisible_rend_invalid_id_pas_invalid_input(monkeypatch, socle,
                                                           super_admin, cle, pp):
    """Pydantic dirait `invalid_input` : `token_id` est donc déclaré en TEXTE et converti
    au handler, pour garder le code que la console lit."""
    code, out = call(cle, path_params=pp)
    assert code == 400 and out["error"] == "invalid_id"


def test_un_jeton_inconnu_est_un_404(monkeypatch, socle):
    stub_authz(monkeypatch)
    monkeypatch.setattr(at.db, "delete_api_token", lambda sub, tid: False)
    code, out = call("me.token.delete", path_params={"token_id": "7"})
    assert code == 404 and out["error"] == "unknown_token"


def test_emettre_pour_un_compte_inexistant_est_refuse(monkeypatch, socle, super_admin):
    monkeypatch.setattr(at.db, "get_user", lambda sub: None)
    for cle, pp in (("platform.token.list", {"sub": "u-9"}),
                    ("platform.token.create", {"sub": "u-9"})):
        code, out = call(cle, path_params=pp, body={})
        assert (code, out["error"]) == (404, "unknown_user")


@pytest.mark.parametrize("corps,erreur", [
    ({"provider": "zzz", "label": "p", "api_key": "K"}, "invalid_provider"),
    ({"provider": "serper", "label": "p"}, "missing_fields"),
    ({"provider": "serper", "api_key": "K"}, "missing_fields"),
    ({}, "invalid_provider"),
])
def test_les_refus_de_pose_d_une_cle_plateforme(monkeypatch, socle, super_admin,
                                                corps, erreur):
    code, out = call("platform.key.create", body=corps)
    assert code == 400 and out["error"] == erreur
    assert socle == [], "rien ne doit être écrit quand la pose est refusée"


def test_un_coffre_qui_refuse_le_provider_est_nomme(monkeypatch, socle, super_admin):
    def _boum(*a, **kw):
        raise ValueError("serper n'accepte pas de clé plateforme")

    monkeypatch.setattr(credentials_store, "set_credential", _boum)
    code, out = call("platform.key.create",
                     body={"provider": "serper", "label": "p", "api_key": "K"})
    assert code == 400 and out["error"] == "invalid_platform_provider"
    assert out["detail"] == "serper n'accepte pas de clé plateforme"


def test_une_cle_inconnue_est_un_404(monkeypatch, socle, super_admin):
    monkeypatch.setattr(credentials_store, "clear_credential", lambda *a, **kw: False)
    code, out = call("platform.key.delete",
                     path_params={"provider": "serper", "label": "prod"})
    assert code == 404 and out["error"] == "unknown_key"


# --- 5. `ttl_days`, tel qu'il est interprété --------------------------------

@pytest.mark.parametrize("brut,attendu", [
    (30, 30), ("30", 30),
    (-1, None),          # str(-1) n'est pas fait de chiffres → ignoré
    ("abc", None),
    (None, None),
])
def test_ttl_days_n_accepte_que_des_chiffres(monkeypatch, socle, super_admin,
                                             brut, attendu):
    """Un `-1` ou un texte donnent « pas d'expiration » plutôt qu'une erreur : c'est le
    comportement servi. Le figer évite qu'un jeton censé expirer devienne éternel APRÈS
    un durcissement de la validation… ou l'inverse."""
    corps = {} if brut is None else {"ttl_days": brut}
    code, out = call("platform.token.create", path_params={"sub": "u-9"}, body=corps)
    assert code == 200 and out["ttl_days"] == attendu
    assert socle[0][3] == attendu


# --- 6. Ce qui CHANGE pour un appelant --------------------------------------

@pytest.mark.parametrize("cle,pp", [("me.token.create", {}),
                                    ("platform.token.create", {"sub": "u-9"})])
def test_une_PORTEE_illisible_ne_produit_JAMAIS_un_jeton_non_porte(monkeypatch, socle,
                                                                  super_admin, cle, pp):
    """⚠️ **Le silence le plus cher de l'inventaire du 2026-08-27 (site B2), et il ne
    peut plus se reproduire ici.**

    Le patron `try: body = await request.json() / except: body = {}` transformait un
    `{"scopes": …}` mal formé en `scopes=None`, c'est-à-dire en jeton **NON PORTÉ** —
    les droits pleins du sub — à la place du jeton borné demandé ; au palier super-admin,
    non porté ET sans expiration. L'appelant recevait 200 et un secret : rien ne disait
    qu'il venait d'obtenir l'inverse de ce qu'il avait écrit.

    Le seam `json_body.read_json_body` (posé sur `main` par #459) refuse désormais un
    corps illisible, et l'adaptateur de capacités passe par lui. On le VÉRIFIE ici, sur
    les deux paliers : ce n'est pas parce qu'un seam existe qu'il est branché."""
    code, out = call(cle, path_params=pp, body=b'{"scopes": {malforme}')
    assert code == 400 and out["error"] == "invalid_json"
    assert socle == [], "aucun jeton ne doit être émis sur un corps illisible"


@pytest.mark.parametrize("cle,pp", [("me.token.create", {}),
                                    ("platform.token.create", {"sub": "u-9"})])
def test_un_corps_ABSENT_reste_un_jeton_cli_non_porte(monkeypatch, socle, super_admin,
                                                      cle, pp):
    """Le pendant du test précédent, et la raison pour laquelle le seam distingue les
    deux : corps ABSENT ⇒ `{}` ⇒ jeton `cli` non porté. C'est le contrat, pas un
    silence — durcir ça casserait tous les appelants qui postent sans corps."""
    code, out = call(cle, path_params=pp, body=None)
    assert code in (200, 201)
    assert out["label"] == "cli" and out["scopes"] is None


def test_un_corps_illisible_est_refuse_sur_la_pose_de_cle(monkeypatch, socle,
                                                         super_admin):
    """Le seam rend le MÊME `400 invalid_json` que la route écrite à la main : c'est un
    écart que ce chantier avait dû signaler, et que #459 a supprimé des deux côtés."""
    code, out = call("platform.key.create", body=b"{pas du json")
    assert code == 400 and out["error"] == "invalid_json"
    assert socle == []


def test_un_corps_non_objet_est_refuse_sur_la_pose_de_cle(monkeypatch, socle,
                                                          super_admin):
    """`invalid_body` — une liste n'a pas de champs à fusionner. La route d'origine
    rendait déjà ce code exact."""
    code, out = call("platform.key.create", body=[1, 2])
    assert code == 400 and out["error"] == "invalid_body"
    assert socle == []


def test_un_champ_inconnu_est_desormais_refuse(monkeypatch, socle, super_admin):
    code, out = call("platform.key.create",
                     body={"provider": "serper", "label": "p", "api_key": "K",
                           "apiKey": "K"})
    assert code == 400
    assert out["error"] == "unknown_fields" and "apiKey" in out["detail"]
