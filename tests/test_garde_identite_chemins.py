"""Un tenant désactivé est refusé sur CHAQUE chemin d'identité — et la garde est centrale.

oto-backend#1165. Désactiver un tenant en vidant son émetteur laissait une session de
tableau de bord ouverte servir un compte du tenant. Les chemins qui ne passent pas par
l'émetteur du tenant sont énumérés ici, un par un, avec leur contrefactuel (un compte
d'un AUTRE tenant passe, un compte du tenant primaire passe sans aucune lecture) :

  1. REST, jeton `oto_` d'API ;
  2. REST, jeton `oto_` de délégation (travail d'agent) ;
  3. REST, JWT de l'annuaire du tenant (sub qualifié par le verifier) ;
  4. REST, JWT de NOTRE annuaire dont l'ancien identifiant est redirigé vers le compte
     du tenant (drain d'alias) — la cause racine de la session restée ouverte ;
  5. MCP, toute requête, sub canonique (JWT OAuth ou jeton `oto_`) ;
  6. MCP, naissance d'un compte du tenant refusée par `upsert_user` ;
  7. lien d'upload signé, qui porte le sub scellé sans aucun jeton ;
  8. façade OAuth sur le host du tenant (enregistrement, autorisation, rafraîchissement).

Puis le cliquet de CENTRALITÉ : les deux prédicats (`tenant_desactive.refus`,
`account_suspension.refus`) ne sont appelés que par `garde_identite.refus`, et celle-ci
par les quatre portes nommées — un cinquième chemin d'identité qui l'omettrait se voit
ici, pas en production.
"""
from __future__ import annotations

import ast
import asyncio
import json
import pathlib
import types

import pytest

from oto_mcp import db, garde_identite, tenant_desactive
from oto_mcp.api import routes as api_routes
from oto_mcp.auth import token_scopes

RACINE = pathlib.Path(__file__).resolve().parent.parent / "oto_mcp"

COUPE = {"slug": "acme", "disabled_at": "2026-10-06T10:00:00", "disabled_by": "op-1",
         "disabled_reason": "contrat terminé"}
DU_TENANT = "acme:u-1"
AUTRE_TENANT = "beta:u-2"
PRIMAIRE = "u-nu"


def _etat_par_prefixe(sub):
    """Le prédicat SQL, rejoué : `acme` est désactivé, `beta` ne l'est pas."""
    return dict(COUPE) if sub.startswith("acme:") else None


@pytest.fixture
def base(monkeypatch):
    """Personne en pause ; `acme` désactivé ; chaque lecture du prédicat de tenant est
    comptée (un sub nu ne doit en coûter aucune)."""
    lectures = []

    def _lit(sub):
        lectures.append(sub)
        return _etat_par_prefixe(sub)
    monkeypatch.setattr(db, "tenant_desactive_du_sub", _lit)
    monkeypatch.setattr(db, "get_suspension", lambda sub: None)
    monkeypatch.setattr(db, "upsert_user", lambda *a, **k: None)
    yield lectures
    token_scopes.set_current(None)


def _req(token: str):
    from starlette.requests import Request
    return Request({
        "type": "http", "method": "GET", "path": "/api/admin/users", "query_string": b"",
        "root_path": "", "scheme": "http", "server": ("test", 80), "http_version": "1.1",
        "headers": [(b"authorization", f"Bearer {token}".encode())],
    })


class _Verifier:
    """Le verifier partagé, réduit à ce qu'il rend : un sub DÉJÀ qualifié (ou nu)."""

    def __init__(self, sub):
        self.sub = sub

    async def verify_token(self, token):
        return types.SimpleNamespace(claims={"sub": self.sub, "email": "x@y.test"})


def _rest(token, sub_du_jwt=None):
    return asyncio.run(api_routes._authenticate(_req(token),
                                                verifier=_Verifier(sub_du_jwt)))


def _corps(reponse) -> dict:
    return json.loads(bytes(reponse.body).decode())


def _refuse_tenant(resultat):
    sub, err = resultat
    assert sub is None and err is not None
    assert err.status_code == 403
    assert _corps(err)["error"] == "tenant_disabled"


# ── 1-2. REST, jetons `oto_` (API, délégation) ───────────────────────────────

@pytest.mark.parametrize("kind", ["user", "delegation"])
def test_rest_jeton_oto_dun_compte_du_tenant_est_refuse(base, monkeypatch, kind):
    monkeypatch.setattr(api_routes.db, "verify_api_token",
                        lambda t: {"sub": DU_TENANT, "scopes": None, "token_kind": kind})
    _refuse_tenant(_rest("oto_x"))


@pytest.mark.parametrize("kind", ["user", "delegation"])
def test_rest_jeton_oto_dun_autre_tenant_passe(base, monkeypatch, kind):
    monkeypatch.setattr(api_routes.db, "verify_api_token",
                        lambda t: {"sub": AUTRE_TENANT, "scopes": None, "token_kind": kind})
    assert _rest("oto_x") == (AUTRE_TENANT, None)


# ── 3. REST, JWT de l'annuaire du tenant ─────────────────────────────────────

def test_rest_jwt_du_tenant_est_refuse(base):
    _refuse_tenant(_rest("eyJ.a.b", sub_du_jwt=DU_TENANT))


def test_rest_jwt_dun_autre_tenant_passe(base):
    assert _rest("eyJ.a.b", sub_du_jwt=AUTRE_TENANT) == (AUTRE_TENANT, None)


def test_rest_jwt_du_tenant_primaire_ne_coute_aucune_lecture(base):
    """Le coût du mécanisme : zéro pour un sub nu, l'immense majorité du trafic."""
    assert _rest("eyJ.a.b", sub_du_jwt=PRIMAIRE) == (PRIMAIRE, None)
    assert base == []


# ── 4. La CAUSE RACINE : un JWT de NOTRE annuaire, redirigé vers le tenant ─────

def test_rest_jwt_de_notre_annuaire_redirige_vers_le_tenant_est_refuse(base, monkeypatch):
    """La session restée ouverte. Le jeton est signé par NOTRE annuaire, sous l'ANCIEN
    identifiant (nu) de la personne ; la bascule vers le tenant l'a redirigé vers son
    compte qualifié (`sub_aliases`). Le drain le canonicalise en `acme:…` : retirer
    l'émetteur du tenant n'y change rien, ce jeton n'a jamais été le sien.

    Discriminant sur l'ORDRE : le prédicat ne refuse que le sub CANONIQUE — une garde
    posée avant le drain regarderait `u-ancien` et servirait la requête."""
    from oto_mcp.api import base as api_base
    monkeypatch.setattr(api_base, "alias_drain_armed", lambda: True)
    monkeypatch.setattr(api_routes.db, "resolve_sub",
                        lambda s: DU_TENANT if s == "u-ancien" else s)
    _refuse_tenant(_rest("eyJ.a.b", sub_du_jwt="u-ancien"))


def test_le_meme_drain_sert_un_ancien_identifiant_dun_tenant_servi(base, monkeypatch):
    from oto_mcp.api import base as api_base
    monkeypatch.setattr(api_base, "alias_drain_armed", lambda: True)
    monkeypatch.setattr(api_routes.db, "resolve_sub",
                        lambda s: AUTRE_TENANT if s == "u-ancien" else s)
    assert _rest("eyJ.a.b", sub_du_jwt="u-ancien") == (AUTRE_TENANT, None)


# ── 5-6. MCP, toute requête ───────────────────────────────────────────────────

def _mcp(monkeypatch, identite):
    from oto_mcp.middleware import account_suspended as mod
    monkeypatch.setattr(mod, "current_user_sub_from_token", identite)
    passe = {"n": 0}

    async def _next(ctx):
        passe["n"] += 1
        return "servi"
    return mod.AccountSuspendedMiddleware(), _next, passe


def test_mcp_refuse_toute_requete_dun_compte_du_tenant(base, monkeypatch):
    from oto_mcp.mcp_errors import McpError
    mw, _next, passe = _mcp(monkeypatch, lambda: DU_TENANT)
    with pytest.raises(McpError) as leve:
        asyncio.run(mw.on_request(object(), _next))
    assert leve.value.error.data["code"] == "tenant_disabled"
    assert passe["n"] == 0


def test_mcp_sert_un_compte_dun_autre_tenant(base, monkeypatch):
    mw, _next, passe = _mcp(monkeypatch, lambda: AUTRE_TENANT)
    assert asyncio.run(mw.on_request(object(), _next)) == "servi"


def test_mcp_la_naissance_dun_compte_du_tenant_est_un_refus_nomme(base, monkeypatch):
    """Un annuaire désactivé qui continue d'émettre : la première présentation d'une
    identité neuve lève dans `upsert_user` (sous le drain), et sort NOMMÉE — pas en
    « base indisponible »."""
    from oto_mcp.mcp_errors import McpError

    def _naissance():
        raise db.TenantDesactive("acme", "contrat terminé", "le compte acme:neuf")
    mw, _next, passe = _mcp(monkeypatch, _naissance)
    with pytest.raises(McpError) as leve:
        asyncio.run(mw.on_request(object(), _next))
    assert leve.value.error.data["code"] == "tenant_disabled"
    assert passe["n"] == 0


def test_la_raison_journalisee_dune_naissance_refusee_nest_pas_une_panne():
    from oto_mcp.auth.hooks import _raison_d_echec
    assert _raison_d_echec(db.TenantDesactive("acme", "m", "q")) == "tenant_desactive"


# ── 7. Le lien d'upload signé ─────────────────────────────────────────────────

def test_un_lien_dupload_scelle_pour_un_compte_du_tenant_nest_pas_servi(base, monkeypatch):
    from starlette.applications import Starlette
    from starlette.routing import Route
    from starlette.testclient import TestClient
    from oto_mcp import upload_tokens
    from oto_mcp.api import uploads
    monkeypatch.setenv("OTO_MCP_OAUTH_STATE_SECRET", "secret-de-banc-assez-long-pour-etre-credible")
    ecrit = []
    monkeypatch.setattr(upload_tokens, "check_target_access", lambda s, t: None)
    monkeypatch.setattr(upload_tokens, "materialize",
                        lambda s, t, d, c: ecrit.append(s) or {"ok": True, "kind": "doc",
                                                               "bytes": len(d)})
    monkeypatch.setattr(db, "consume_upload_token", lambda jti: ecrit.append(jti) or True)
    client = TestClient(Starlette(routes=[
        Route("/api/upload/{token}", uploads.upload_receive, methods=["PUT"])]))
    cible = {"kind": "project_file", "project_id": 1, "filename": "f.txt"}

    jeton = upload_tokens.sign(DU_TENANT, 7, cible)[0]
    r = client.put(f"/api/upload/{jeton}", content=b"x")
    assert r.status_code == 403 and r.json()["error"] == "tenant_disabled"
    assert ecrit == [], "ni jeton brûlé, ni écriture"

    jeton = upload_tokens.sign(AUTRE_TENANT, 7, cible)[0]
    assert client.put(f"/api/upload/{jeton}", content=b"x").status_code == 200


# ── 8. La façade OAuth sur le host du tenant ─────────────────────────────────

def _entree(slug, **kw):
    from oto_mcp import tenancy
    return tenancy.TenantIssuer(slug=slug, issuer=f"https://auth.{slug}.test/oidc",
                                jwks_uri="", **kw)


def test_le_registre_garde_le_tenant_desactive_et_le_marque(monkeypatch):
    """L'entrée RESTE (son émetteur sert à reconnaître et refuser nommément ses jetons,
    et ses comptes restent classés sous lui) ; elle porte l'état lu en base."""
    from oto_mcp import tenancy
    monkeypatch.setenv("OTO_TENANT_PRIMAIRE_SLUG", "oto")
    reg = tenancy.IssuerRegistry(tenancy.build("https://auth.oto.test/oidc", tenants=[
        {"slug": "acme", "issuer": "https://auth.acme.test/oidc",
         "disabled_at": "2026-10-06T10:00:00"},
        {"slug": "beta", "issuer": "https://auth.beta.test/oidc", "disabled_at": None}]))
    assert reg.get("https://auth.acme.test/oidc").disabled is True
    assert reg.get("https://auth.beta.test/oidc").disabled is False
    assert reg.tenant_of(DU_TENANT) == "acme"


def test_la_facade_refuse_une_autorisation_nouvelle_sur_le_host_du_tenant():
    from oto_mcp.auth import facade
    refus = facade.refus_tenant_desactive(_entree("acme", disabled=True))
    assert refus is not None and refus.status_code == 403
    assert json.loads(refus.body)["error"] == "tenant_disabled"
    assert facade.refus_tenant_desactive(_entree("beta")) is None
    assert facade.refus_tenant_desactive(None) is None


def test_les_trois_routes_oauth_du_host_tenant_appellent_le_refus():
    """Enregistrement (DCR), autorisation relayée, échange de jeton (code ET
    rafraîchissement) : les trois points où l'annuaire du tenant délivre du neuf."""
    for fichier, attendu in (("auth/facade.py", 1), ("auth/relay.py", 2)):
        src = (RACINE / fichier).read_text()
        appels = src.count("(coupe := refus_tenant_desactive(") + \
            src.count("(coupe := facade.refus_tenant_desactive(")
        assert appels == attendu, fichier


def test_la_dcr_dun_host_de_tenant_desactive_est_refusee(monkeypatch):
    """Bout en bout sur la route servie : rien n'est enregistré dans son annuaire."""
    from starlette.applications import Starlette
    from starlette.testclient import TestClient
    from oto_mcp.auth import facade
    entree = _entree("acme", disabled=True, hosts=("mcp.acme.test",),
                     oauth_client_id="app-acme")
    monkeypatch.setattr(facade, "tenant_for_host",
                        lambda host: entree if host == "mcp.acme.test" else None)
    ecrit = []
    monkeypatch.setattr(facade, "_register_redirects", lambda *a, **k: ecrit.append(a))
    client = TestClient(Starlette(routes=facade.make_routes(
        "https://mcp.oto.test", "app-oto")), base_url="https://mcp.acme.test")
    r = client.post("/oauth/register",
                    json={"redirect_uris": ["https://claude.ai/api/mcp/auth_callback"]})
    assert r.status_code == 403 and r.json()["error"] == "tenant_disabled"
    assert ecrit == []


# ── Le prédicat : fail-closed, et le préfixe exact ───────────────────────────

def test_un_hoquet_de_base_ne_blanchit_pas_un_tenant_desactive(monkeypatch):
    def _boum(sub):
        raise RuntimeError("pool épuisé")
    monkeypatch.setattr(db, "tenant_desactive_du_sub", _boum)
    with pytest.raises(RuntimeError):
        garde_identite.refus(DU_TENANT)


def test_le_tenant_passe_avant_la_pause(monkeypatch):
    """Un compte d'un tenant désactivé ET en pause reçoit le refus de l'ESPACE : c'est
    lui que l'exploitant doit lever d'abord, et le seul qui concerne tous ses comptes."""
    monkeypatch.setattr(db, "tenant_desactive_du_sub", _etat_par_prefixe)
    monkeypatch.setattr(db, "get_suspension", lambda sub: {"suspended_at": "x"})
    assert garde_identite.refus(DU_TENANT)[0] == "tenant_disabled"
    assert garde_identite.refus(AUTRE_TENANT)[0] == "account_suspended"


def test_un_sub_nu_ne_lit_jamais_le_prédicat_de_tenant(monkeypatch):
    def _interdit(sub):
        raise AssertionError("un sub nu ne relève d'aucun tenant tiers")
    monkeypatch.setattr(db, "tenant_desactive_du_sub", _interdit)
    assert tenant_desactive.etat(PRIMAIRE) is None
    assert tenant_desactive.etat(None) is None


# ── Le cliquet de CENTRALITÉ ──────────────────────────────────────────────────

def _appels(nom_module: str, nom_fonction: str) -> set[str]:
    """Les fichiers de `oto_mcp/` qui appellent `<module>.<fonction>`, en appel direct
    ou passé en argument (`run_in_threadpool(module.fonction, …)`)."""
    trouves = set()
    for f in RACINE.rglob("*.py"):
        arbre = ast.parse(f.read_text(encoding="utf-8"))
        for n in ast.walk(arbre):
            if (isinstance(n, ast.Attribute) and n.attr == nom_fonction
                    and isinstance(n.value, ast.Name) and n.value.id == nom_module):
                trouves.add(str(f.relative_to(RACINE)))
    return trouves


def test_les_predicats_ne_sont_lus_que_par_la_garde_centrale():
    assert _appels("tenant_desactive", "refus") == {"garde_identite.py"}
    assert _appels("account_suspension", "refus") == {"garde_identite.py"}


def test_la_garde_centrale_est_traversee_par_les_quatre_portes():
    """REST (jetons `oto_` et JWT : `api/base.py`, deux branches), MCP (le middleware sur
    `on_request`), lien d'upload signé, relais d'upload vers la GED Pennylane (cabinet,
    `/api/relay/{token}` : son jeton scelle un compte, rejoué à la réception) — et le
    worker de `jev_rows(background=true)`,
    qui rejoue chaque tranche sous l'identité de l'appelant SANS requête : la garde y
    est relue avant chaque tranche ; de même `recipe_scheduler` (`recipes/programmes.py`),
    qui rejoue un programme au nom de qui l'a posé. Une porte d'identité neuve doit s'ajouter ICI —
    c'est l'endroit où elle se déclare."""
    assert _appels("garde_identite", "refus") == {
        "api/base.py", "middleware/account_suspended.py", "api/uploads.py",
        "api/pennylane_firm.py", "jev_jobs_worker.py",
        "recipes/programmes.py"}
    base_src = (RACINE / "api" / "base.py").read_text()
    assert base_src.count("garde_identite.refus") == 2
