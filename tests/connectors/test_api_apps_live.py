"""Les applications de Custom API (`connectors/api_apps.py`, `capabilities/connectors/
api_apps.py`), sur une vraie base et par la face REST servie (`make_routes`).

Le parcours qu'une org demande : DEUX applications sur la même API interne — une pour
ses responsables (clé d'ÉQUIPE, droits étendus côté API), une pour ses commerciaux (clé
PERSONNELLE de chacun) —, créées une fois par un admin, sans clé d'org. Ce qui se vérifie :

- l'application se crée sans clé ; un membre ne la crée pas ;
- une clé d'équipe ou personnelle se pose avec ses SEULS secrets : l'adresse et le mode
  viennent de l'application, un champ de connexion différent est refusé, la clé est liée ;
- à l'appel, chacun est servi par SA clé avec la définition de l'application ; qui n'a
  pas de clé n'est servi par aucune autre ; la liste dit le palier de chacun ;
- une clé posée AVANT l'application, du même nom, n'est pas adoptée en silence ;
- seul un admin d'org change les actions, même porteur d'une clé ;
- l'application change d'hôte : ses clés refusent (`api_app_key_stale`) jusqu'à repose,
  leur secret ne part jamais vers le nouvel hôte ; retirée, elles refusent
  (`api_app_removed`) ;
- une carte d'org existante devient une application d'un geste (`adopt_org_key`), et
  l'ancienne route de définition renvoie vers l'application.
"""
from __future__ import annotations

import base64
import os

import pytest

ADMIN, LEAD, VENTE, SANS = "usr_app_admin", "usr_app_lead", "usr_app_vente", "usr_app_sans"
_KEY = base64.b64encode(b"\x5a" * 32).decode()
GQL = {"name": "graphql", "description": "Run a GraphQL query.", "method": "POST",
       "path": "/graphql", "input": {"type": "object", "properties": {
           "query": {"type": "string"}, "variables": {"type": "object"}},
           "required": ["query"]}, "in": {"query": "body", "variables": "body"},
       "class": "read"}
HOTES = {"crm.example.test": {"93.184.216.34"}, "crm2.example.test": {"93.184.216.35"},
         "old.example.test": {"93.184.216.36"}}


@pytest.fixture(scope="module")
def cle_maitre():
    avant = os.environ.get("OTO_MCP_MASTER_KEY")
    os.environ["OTO_MCP_MASTER_KEY"] = _KEY
    yield
    if avant is None:
        os.environ.pop("OTO_MCP_MASTER_KEY", None)
    else:
        os.environ["OTO_MCP_MASTER_KEY"] = avant


@pytest.fixture(scope="module")
def monde(live, cle_maitre):
    from oto_mcp import db, group_store, org_store
    from oto_mcp.connectors import activation
    for sub in (ADMIN, LEAD, VENTE, SANS):
        db.upsert_user(sub, email=f"{sub}@t.invalid", name=sub)
    org = org_store.create_org("Org apps", created_by=ADMIN)
    org_store.add_org_member(org, ADMIN, "org_admin")
    for sub in (LEAD, VENTE, SANS):
        org_store.add_org_member(org, sub)
    squad = group_store.create_group(org, "Squad leads", created_by=ADMIN)
    group_store.add_group_member(squad, LEAD, "group_admin")
    group_store.set_active_group(LEAD, squad)
    activation.set_activation("api", True, org_id=None, set_by=ADMIN)
    return {"org": org, "squad": squad}


@pytest.fixture(autouse=True)
def dns(dns_simule):
    dns_simule.update(HOTES)


class _Claims:
    def __init__(self, sub: str):
        self.claims = {"sub": sub, "email": f"{sub}@t.invalid", "name": sub}


class _Verifier:
    async def verify_token(self, token: str):
        return _Claims(token)


@pytest.fixture(scope="module")
def rest(monde):
    from starlette.applications import Starlette
    from starlette.testclient import TestClient
    from oto_mcp.api import routes as api_routes
    app = Starlette(routes=api_routes.make_routes(_Verifier(), mcp_instance=None))
    return TestClient(api_routes.RestCallLogger(app))


def _h(sub: str, org: int) -> dict:
    return {"Authorization": f"Bearer {sub}", "X-Oto-Org": str(org)}


def _apps(monde) -> str:
    return f"/api/orgs/{monde['org']}/connectors/api/apps"


def _creer(rest, monde, account: str, host: str = "crm.example.test", sub: str = ADMIN,
           **extra):
    body = {"account": account, "label": account.replace("_", " ").title(),
            "connection": {"base_url": f"https://{host}", "auth_mode": "bearer"},
            "operations": [GQL], **extra}
    return rest.post(_apps(monde), json=body, headers=_h(sub, monde["org"]))


def _cle_equipe(rest, monde, account: str, fields: dict, sub: str = LEAD):
    return rest.put(f"/api/groups/{monde['squad']}/secrets/api",
                    json={"account": account, "fields": fields},
                    headers=_h(sub, monde["org"]))


def _cle_perso(rest, monde, sub: str, account: str, fields: dict):
    return rest.post("/api/settings/api-keys/api", json={"account": account, **fields},
                     headers=_h(sub, monde["org"]))


def _cle_org(rest, monde, account: str, fields: dict):
    return rest.put(f"/api/orgs/{monde['org']}/secrets/api",
                    json={"account": account, "fields": fields},
                    headers=_h(ADMIN, monde["org"]))


@pytest.fixture
def comme(monde, monkeypatch):
    """Rejoue une résolution d'outil `api*` sous l'identité de `sub` (son org, son équipe
    active) — les coutures d'un appel MCP, comme le banc bout en bout."""
    from oto_mcp import access, group_store
    from oto_mcp.tools import api_ops

    def _comme(sub: str):
        monkeypatch.setattr(api_ops, "current_user_sub_from_token", lambda: sub)
        monkeypatch.setattr(access, "current_user_sub_or_raise", lambda: sub)
        monkeypatch.setattr(access, "current_org", lambda s: monde["org"])
        monkeypatch.setattr(access, "current_group",
                            lambda s: group_store.get_active_group(s))
        monkeypatch.setattr(access, "project_pinned_instance", lambda p: None)
        return api_ops
    return _comme


def _lien(et: str, eid: str, account: str):
    from oto_mcp.db import api_apps
    return api_apps.lien(et, eid, account)


def test_un_admin_cree_l_application_sans_cle_un_membre_non(rest, monde):
    r = _creer(rest, monde, "crm_admin")
    assert r.status_code == 200, r.text
    a = r.json()
    assert (a["account"], a["actions_count"], a["access"], a["keys"]) == \
        ("crm_admin", 1, None, [])
    assert a["connection"] == {"base_url": "https://crm.example.test", "auth_mode": "bearer"}
    assert _creer(rest, monde, "crm_user").status_code == 200
    assert _creer(rest, monde, "pirate", sub=VENTE).status_code == 403
    assert _creer(rest, monde, "crm_admin").json()["error"] == "api_app_exists"
    r = rest.post(_apps(monde), json={"account": "x", "connection": {
        "base_url": "https://crm.example.test", "auth_mode": "header"}},
        headers=_h(ADMIN, monde["org"]))
    assert (r.status_code, r.json()["error"]) == (400, "invalid_app_connection")


def test_une_cle_se_pose_avec_ses_seuls_secrets_et_se_lie(rest, monde):
    from oto_mcp.db import api_apps
    r = _cle_equipe(rest, monde, "crm_admin", {"token": "T-squad"})
    assert r.status_code == 200, r.text
    app = api_apps.vivante(monde["org"], "crm_admin")
    assert _lien("group", str(monde["squad"]), "crm_admin") == app["id"]
    r = _cle_equipe(rest, monde, "crm_admin",
                    {"token": "T", "base_url": "https://old.example.test"})
    assert (r.status_code, r.json()["error"]) == (400, "api_app_field_fixed")
    r = _cle_perso(rest, monde, VENTE, "crm_user", {"token": "T-vente"})
    assert r.status_code == 200, r.text
    from oto_mcp import credentials_store as cs
    assert _lien(cs.MEMBER, cs.member_id(monde["org"], VENTE), "crm_user") is not None


def test_chacun_est_servi_par_sa_cle_avec_la_definition_de_l_application(rest, monde,
                                                                          comme):
    ops = comme(LEAD)
    rc = ops._resolved("crm_admin")
    assert (rc.entity_type, rc.fields["token"], rc.fields["base_url"]) == \
        ("group", "T-squad", "https://crm.example.test")
    assert [o["name"] for o in ops._definition(rc)["operations"]] == ["graphql"]
    ops = comme(VENTE)
    rc = ops._resolved("crm_user")
    assert (rc.entity_type, rc.fields["token"]) == ("member", "T-vente")
    # Le commercial n'a aucune clé de l'application des responsables : aucune autre ne
    # le sert (ni celle de l'équipe, ni une clé d'org absente).
    with pytest.raises(Exception, match="crm_admin"):
        ops._resolved("crm_admin")
    vues = {a["account"]: a["access"] for a in rest.get(
        _apps(monde), headers=_h(VENTE, monde["org"])).json()["apps"]}
    assert vues == {"crm_admin": None, "crm_user": "member"}
    vues = {a["account"]: a["access"] for a in rest.get(
        _apps(monde), headers=_h(LEAD, monde["org"])).json()["apps"]}
    assert vues == {"crm_admin": "group", "crm_user": None}
    # La page dit au responsable QUELLE équipe le sert : la sienne, pour qu'elle
    # s'affiche posée, même quand la page consulte l'org sans désigner d'équipe.
    a = rest.get(_apps(monde) + "/crm_admin", headers=_h(LEAD, monde["org"])).json()
    assert (a["access"], a["access_team"]) == ("group", monde["squad"])
    a = rest.get(_apps(monde) + "/crm_admin", headers=_h(VENTE, monde["org"])).json()
    assert a["access_team"] is None
    assert "keys" not in a and a["definition"]["operations"][0]["name"] == "graphql"
    a = rest.get(_apps(monde) + "/crm_admin", headers=_h(ADMIN, monde["org"])).json()
    assert [(k["level"], k["owner"]) for k in a["keys"]] == \
        [("group", str(monde["squad"]))]


def test_les_actions_d_une_application_restent_aux_admins_de_l_org(rest, monde, comme):
    from oto_mcp.tools import api_calls
    ops = comme(VENTE)
    rc = ops._resolved("crm_user")
    with pytest.raises(Exception) as e:
        ops._require_admin(VENTE, rc)
    assert "admins" in str(e.value)
    ops = comme(ADMIN)
    out = api_calls._set_app_definition(ADMIN, rc, {"description": "The CRM, sales."})
    assert out["actions"] == ["graphql"]
    a = rest.get(_apps(monde) + "/crm_user", headers=_h(VENTE, monde["org"])).json()
    assert a["description"] == "The CRM, sales."


def test_une_cle_d_avant_du_meme_nom_n_est_pas_adoptee(rest, monde, comme):
    from oto_mcp import credentials_store as cs
    r = _cle_perso(rest, monde, SANS, "crm_tard", {
        "base_url": "https://old.example.test", "auth_mode": "bearer", "token": "T-old"})
    assert r.status_code == 200, r.text
    assert _creer(rest, monde, "crm_tard").status_code == 200
    assert _lien(cs.MEMBER, cs.member_id(monde["org"], SANS), "crm_tard") is None
    rc = comme(SANS)._resolved("crm_tard")
    assert (rc.app, rc.fields["base_url"]) == (None, "https://old.example.test")


def test_l_application_change_d_hote_ses_cles_refusent_jusqu_a_repose(rest, monde,
                                                                     comme):
    r = rest.patch(_apps(monde) + "/crm_admin", json={"connection": {
        "base_url": "https://crm2.example.test"}}, headers=_h(ADMIN, monde["org"]))
    assert (r.status_code, r.json()["stale_keys"]) == (200, 1), r.text
    ops = comme(LEAD)
    with pytest.raises(Exception) as e:
        ops._resolved("crm_admin")
    assert "crm2.example.test" in str(e.value) and "not sent there" in str(e.value)
    assert _cle_equipe(rest, monde, "crm_admin", {"token": "T-squad-2"}).status_code == 200
    rc = ops._resolved("crm_admin")
    assert (rc.fields["base_url"], rc.fields["token"]) == \
        ("https://crm2.example.test", "T-squad-2")


def test_une_application_retiree_fait_refuser_ses_cles(rest, monde, comme):
    r = rest.delete(_apps(monde) + "/crm_user", headers=_h(ADMIN, monde["org"]))
    assert r.json() == {"ok": True, "keys_left": 1}
    with pytest.raises(Exception) as e:
        comme(VENTE)._resolved("crm_user")
    assert "removed" in str(e.value)
    assert rest.get(_apps(monde) + "/crm_user",
                    headers=_h(ADMIN, monde["org"])).status_code == 404
    # Le nom se réutilise ; les clés de l'ancienne ne sont pas adoptées par la nouvelle.
    assert _creer(rest, monde, "crm_user").status_code == 200
    with pytest.raises(Exception, match="removed"):
        comme(VENTE)._resolved("crm_user")


def test_une_carte_d_org_devient_une_application_d_un_geste(rest, monde, comme):
    """Les cartes d'avant se nomment souvent comme elles s'affichent : le nom porte des
    espaces, et la route de l'application le prend encodé."""
    from oto_mcp.connectors import api_operations as ops_store
    r = _cle_org(rest, monde, "Acme ERP", {"base_url": "https://crm.example.test",
                                      "auth_mode": "bearer", "token": "T-org",
                                      "label": "ERP"})
    assert r.status_code == 200, r.text
    ops_store.save_definition("org", str(monde["org"]), "Acme ERP", {"operations": [GQL]})
    r = _creer(rest, monde, "Acme ERP", operations=None)
    assert (r.status_code, r.json()["error"]) == (409, "api_app_org_key_exists")
    r = rest.post(_apps(monde), json={"account": "Acme ERP", "adopt_org_key": True},
                  headers=_h(ADMIN, monde["org"]))
    assert r.status_code == 200, r.text
    a = r.json()
    assert (a["label"], a["actions_count"], a["connection"]["base_url"]) == \
        ("ERP", 1, "https://crm.example.test")
    assert [k["level"] for k in a["keys"]] == ["org"]
    rc = comme(SANS)._resolved("Acme ERP")
    assert (rc.entity_type, rc.fields["token"], rc.app["account"]) == ("org", "T-org", "Acme ERP")
    r = rest.get(f"/api/orgs/{monde['org']}/connectors/api/definition?account=Acme%20ERP",
                 headers=_h(ADMIN, monde["org"]))
    assert (r.status_code, r.json()["error"]) == (409, "api_app_owns_definition")


def test_la_page_dit_la_cle_de_l_equipe_active_meme_en_consultant_l_org(monde, monkeypatch):
    """Le tableau de bord consulte l'org (`X-Oto-Org`) sans désigner d'équipe : la cascade
    d'un appel REST est alors au niveau org (`current_group` → None). Les agents du
    responsable, eux, appellent depuis son équipe ACTIVE — c'est elle que la page dit,
    jamais une équipe d'une autre org."""
    from oto_mcp import access, org_store
    from oto_mcp.connectors import api_apps
    monkeypatch.setattr(access, "current_group", lambda s: None)
    assert api_apps.equipe_active(LEAD, monde["org"]) == monde["squad"]
    autre = org_store.create_org("Autre org", created_by=ADMIN)
    assert api_apps.equipe_active(LEAD, autre) is None
    assert api_apps.equipe_active(VENTE, monde["org"]) is None


# ── la surface servie aux agents : les vrais outils `api`, `api_act`, `api_get` ────────

@pytest.fixture
def outils(monde, monkeypatch):
    """Les outils montés à nu, appelés sous l'identité de `sub` (son org, son équipe
    active) et, si donné, le compte désigné (`_account`) — les coutures d'un appel MCP,
    comme le banc bout en bout."""
    import asyncio
    from fastmcp import FastMCP
    from oto_mcp import access, group_store, session_org
    from oto_mcp.tools import api as t_http, api_calls, api_ops
    m = FastMCP("apps")
    for mod in (t_http, api_ops, api_calls):
        mod.register(m)

    def _sous(sub: str, account=None):
        for mod in (t_http, api_ops):
            monkeypatch.setattr(mod, "current_user_sub_from_token", lambda: sub)
        monkeypatch.setattr(access, "current_user_sub_or_raise", lambda: sub)
        monkeypatch.setattr(access, "current_org", lambda s: monde["org"])
        monkeypatch.setattr(access, "current_group",
                            lambda s: group_store.get_active_group(s))
        monkeypatch.setattr(access, "project_pinned_instance", lambda p: None)
        monkeypatch.setattr(session_org, "current_call_account", lambda: account)
        return lambda name: asyncio.run(m.get_tool(name)).fn
    return _sous


def test_les_outils_servent_l_application_par_la_cle_de_chacun(rest, monde, outils,
                                                                 monkeypatch):
    """`api(op=list)` dit l'application et le palier de la clé de l'appelant ;
    `api_act(op=call, dry_run)` planifie d'après la définition de l'APPLICATION ;
    `api_get` part vers l'adresse de l'application avec la clé de l'appelant ; un porteur
    de clé qui n'est pas admin ne réécrit pas les actions."""
    from oto_mcp.tools import api as t_http
    assert _creer(rest, monde, "outils_crm").status_code == 200
    assert _cle_perso(rest, monde, VENTE, "outils_crm", {"token": "T-v"}).status_code == 200
    outil = outils(VENTE)
    vue = {c["account"]: c for c in outil("api")(op="list")["connectors"]}
    assert (vue["outils_crm"]["scope"], vue["outils_crm"]["access"],
            vue["outils_crm"]["actions"]) == ("app", "member", ["graphql"])
    plan = outils(VENTE, account="outils_crm")("api_act")(
        op="call", operation="graphql", dry_run=True, args={"query": "{ me { id } }"})
    assert (plan["account"], plan["method"], plan["path"], plan["body"]["query"]) == \
        ("outils_crm", "POST", "/graphql", "{ me { id } }")
    with pytest.raises(Exception, match="admins"):
        outils(VENTE, account="outils_crm")("api_act")(op="set_definition",
                                                      description="Hijacked.")
    vus = []

    class _Client:
        def get(self, path, params=None):
            return {"ok": True}

    def _client_de(fields, headers=None, *, row=None):
        vus.append((fields["base_url"], fields["token"], row))
        return _Client()
    monkeypatch.setattr(t_http, "client_from_fields", _client_de)
    outil = outils(VENTE, account="outils_crm")
    assert outil("api_get")(path="/me") == {"ok": True}
    assert vus == [("https://crm.example.test", "T-v",
                    ("member", f"{monde['org']}:{VENTE}", "outils_crm"))]


@pytest.mark.parametrize("change", [
    {"base_url": "http://crm.example.test"},                 # schéma
    {"base_url": "https://crm.example.test:8443"},           # port
])
def test_le_meme_hote_sur_un_autre_schema_ou_port_fait_refuser(rest, monde, comme,
                                                               change):
    account = "origine_" + str(abs(hash(str(change))) % 10_000)
    assert _creer(rest, monde, account).status_code == 200
    assert _cle_equipe(rest, monde, account, {"token": "T"}).status_code == 200
    r = rest.patch(_apps(monde) + "/" + account, json={"connection": change},
                   headers=_h(ADMIN, monde["org"]))
    assert (r.status_code, r.json()["stale_keys"]) == (200, 1), r.text
    with pytest.raises(Exception, match="not sent there"):
        comme(LEAD)._resolved(account)


def test_oauth2_un_autre_token_url_ne_recoit_pas_le_secret_client(rest, monde, comme):
    """En oauth2, le secret client est POSTÉ au `token_url` : le changer est changer de
    destinataire, même si `base_url` ne bouge pas."""
    body = {"account": "crm_oauth", "connection": {
        "base_url": "https://crm.example.test", "auth_mode": "oauth2",
        "token_url": "https://crm.example.test/oauth/token"}}
    assert rest.post(_apps(monde), json=body,
                     headers=_h(ADMIN, monde["org"])).status_code == 200
    r = _cle_equipe(rest, monde, "crm_oauth", {"client_id": "cid", "client_secret": "S"})
    assert r.status_code == 200, r.text
    assert comme(LEAD)._resolved("crm_oauth").fields["client_secret"] == "S"
    r = rest.patch(_apps(monde) + "/crm_oauth", json={"connection": {
        "token_url": "https://old.example.test/token"}}, headers=_h(ADMIN, monde["org"]))
    assert (r.status_code, r.json()["stale_keys"]) == (200, 1), r.text
    with pytest.raises(Exception, match="not sent there"):
        comme(LEAD)._resolved("crm_oauth")
