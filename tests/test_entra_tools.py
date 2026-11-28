"""Tools `entra` / `entra_act` du connecteur « Microsoft 365 directory » (service du
porteur `microsoft`).

Ce que ce fichier verrouille :
- la SURFACE : la paire de tout connecteur, `entra` en lecture, `entra_act` qui agit et ne
  fait que trois gestes — licence, compte activé, membre d'un groupe (21/10/2026) ; le
  jeton du service `entra` ;
- le dispatch vers les VRAIES méthodes de `DirectoryClient` (autospec) ;
- les licences nommées par les abonnements du tenant, achetées / attribuées / libres ;
- les filtres des comptes, appliqués après lecture ; la dernière connexion refusée par
  Microsoft (403) dite avec son prérequis, pas en « accès refusé » générique ;
- un groupe Microsoft 365 avec son site d'équipe, une liste de diffusion sans ;
- les arguments d'une autre op refusés avant tout appel ;
- l'écriture : une licence nommée comme la lecture la montre, refusée sans licence libre ;
  un membre ajouté par son id objet, jamais dans une liste de diffusion (Exchange) ;
- l'approbation d'administrateur : l'annuaire n'est jamais dans le lien par défaut, seulement
  depuis sa carte ou nommé.
"""
import asyncio
from unittest.mock import create_autospec

import pytest
from oto_mcp.mcp_errors import McpError

pytestmark = pytest.mark.exige_pin_oto_core

BS = "f245ecc8-75af-4f8e-b61f-27d8114de5f3"
BB = "3b555118-da6a-4418-894f-7df1e2096870"
ABONNEMENTS = [
    {"skuId": BS, "skuPartNumber": "O365_BUSINESS_PREMIUM", "consumedUnits": 12,
     "prepaidUnits": {"enabled": 15}, "capabilityStatus": "Enabled"},
    {"skuId": BB, "skuPartNumber": "O365_BUSINESS_ESSENTIALS", "consumedUnits": 3,
     "prepaidUnits": {"enabled": 3}, "capabilityStatus": "Enabled"},
]


def _compte(id, nom, *, actif=True, licences=(), type="Member", **plus):
    return {"id": id, "displayName": nom, "userPrincipalName": f"{id}@contoso.example",
            "mail": f"{id}@contoso.example", "accountEnabled": actif, "userType": type,
            "assignedLicenses": [{"skuId": s} for s in licences], **plus}


COMPTES = [
    _compte("ana", "Ana", licences=[BS], department="Accueil"),
    _compte("bob", "Bob", licences=[BB]),
    _compte("cle", "Clé", actif=False, licences=[BS]),
    _compte("dan", "Dan"),
    _compte("eve", "Eve", type="Guest"),
]


@pytest.fixture
def client(monkeypatch):
    import oto.tools.microsoft as coeur

    inst = create_autospec(coeur.DirectoryClient, instance=True)
    construits = []
    monkeypatch.setattr(coeur, "DirectoryClient",
                        lambda jeton, *, session: construits.append(jeton) or inst)
    monkeypatch.setattr("oto_mcp.access.current_user_sub_or_raise", lambda: "sub-1")

    def jeton(sub, service):
        assert service == "entra"
        return f"AT-{sub}"

    monkeypatch.setattr("oto_mcp.auth.microsoft.access_token_for", jeton)
    inst.list_subscriptions.return_value = ABONNEMENTS
    inst.construits = construits
    return inst


def _tool(name="entra"):
    from fastmcp import FastMCP
    from oto_mcp.tools import entra as X

    m = FastMCP("t")
    X.register(m)
    return asyncio.run(m.get_tool(name)).fn


def test_surface_lecture_et_acte():
    from fastmcp import FastMCP
    from oto_mcp.tools import entra as X
    from oto_mcp.tools.lecture import ACTE, LECTURE

    m = FastMCP("t")
    X.register(m)
    outils = {t.name: t for t in asyncio.run(m.list_tools())}
    assert set(outils) == {"entra", "entra_act"}
    assert outils["entra"].annotations == LECTURE
    assert outils["entra_act"].annotations == ACTE


def test_licences_achetees_attribuees_libres(client):
    out = _tool()()
    assert out == {"licenses": [
        {"sku": "O365_BUSINESS_PREMIUM", "name": "Microsoft 365 Business Standard",
         "bought": 15, "assigned": 12, "free": 3, "status": "Enabled"},
        {"sku": "O365_BUSINESS_ESSENTIALS", "name": "Microsoft 365 Business Basic",
         "bought": 3, "assigned": 3, "free": 0, "status": "Enabled"}], "count": 2}
    assert client.construits == ["AT-sub-1"]


def test_comptes_vue_resserree_licences_nommees(client):
    client.list_users.return_value = COMPTES[:1]
    out = _tool()(op="users")
    assert out["users"] == [{
        "id": "ana", "name": "Ana", "email": "ana@contoso.example",
        "mail": "ana@contoso.example", "enabled": True, "type": "Member", "jobTitle": None,
        "department": "Accueil", "city": None, "office": None, "created": None,
        "usageLocation": None, "licenses": ["Microsoft 365 Business Standard"]}]
    client.list_users.assert_called_once_with(sign_in=False, limit=5000)


@pytest.mark.parametrize("filtre,attendus", [
    ({"enabled": False}, ["cle"]),
    ({"licensed": False}, ["dan", "eve"]),
    ({"licensed": True, "enabled": True}, ["ana", "bob"]),
    ({"license": "business standard"}, ["ana", "cle"]),
    ({"type": "Guest"}, ["eve"]),
    ({"query": "accueil"}, ["ana"]),
])
def test_filtres_des_comptes(client, filtre, attendus):
    client.list_users.return_value = COMPTES
    out = _tool()(op="users", **filtre)
    assert [u["id"] for u in out["users"]] == attendus


def test_limite_tronque_et_le_dit(client):
    client.list_users.return_value = COMPTES
    out = _tool()(op="users", limit=2, fields=["id"])
    assert out == {"users": [{"id": "ana"}, {"id": "bob"}], "count": 2, "truncated": True}


def test_derniere_connexion(client):
    client.list_users.return_value = [_compte("ana", "Ana", signInActivity={
        "lastSignInDateTime": "2026-10-20T07:00:00Z",
        "lastNonInteractiveSignInDateTime": "2026-10-21T05:00:00Z"})]
    out = _tool()(op="users", sign_in=True)
    assert out["users"][0]["lastSignIn"] == "2026-10-20T07:00:00Z"
    assert out["users"][0]["lastNonInteractiveSignIn"] == "2026-10-21T05:00:00Z"
    client.list_users.assert_called_once_with(sign_in=True, limit=5000)


def test_derniere_connexion_refusee_dit_le_prerequis(client):
    from oto.tools.common.errors import UpstreamHTTPError

    client.list_users.side_effect = UpstreamHTTPError(403, {"error": {"message": "x"}})
    with pytest.raises(McpError, match="Entra ID P1.*Retry without `sign_in`"):
        _tool()(op="users", sign_in=True)


def test_403_sans_derniere_connexion_reste_le_refus_graph(client):
    from oto.tools.common.errors import UpstreamHTTPError

    client.list_users.side_effect = UpstreamHTTPError(403, {"error": {"message": "x"}})
    with pytest.raises(McpError, match="denies access") as e:
        _tool()(op="users")
    assert "P1" not in str(e.value)


def test_un_compte_avec_responsable_et_groupes(client):
    client.get_user.return_value = _compte(
        "ana", "Ana", licences=[BS], manager={"id": "m", "displayName": "Mia", "mail": "m@c"})
    client.list_user_groups.return_value = [
        {"id": "g1", "displayName": "Direction", "groupTypes": ["Unified"],
         "resourceProvisioningOptions": ["Team"], "mailEnabled": True,
         "securityEnabled": False}]
    out = _tool()(op="user", user="ana@contoso.example")
    assert out["manager"] == {"id": "m", "name": "Mia", "mail": "m@c"}
    assert out["licenses"] == ["Microsoft 365 Business Standard"]
    assert out["groups"][0] == {"id": "g1", "name": "Direction", "kind": "microsoft365",
                                "mail": None, "team": True, "visibility": None,
                                "description": None}
    client.get_user.assert_called_once_with("ana@contoso.example")
    client.list_user_groups.assert_called_once_with("ana@contoso.example")


def test_groupes_par_genre(client):
    client.list_groups.return_value = [
        {"id": "g1", "displayName": "Équipe", "groupTypes": ["Unified"]},
        {"id": "g2", "displayName": "Tous", "mail": "tous@c", "mailEnabled": True},
        {"id": "g3", "displayName": "Admins", "securityEnabled": True}]
    out = _tool()(op="groups", kind="distribution")
    assert [g["id"] for g in out["groups"]] == ["g2"]
    assert _tool()(op="groups", query="tous@")["groups"][0]["id"] == "g2"
    client.list_groups.assert_called_with(limit=5000)


def test_groupe_microsoft_365_avec_membres_proprietaires_et_site(client):
    client.get_group.return_value = {"id": "g1", "displayName": "Équipe",
                                     "groupTypes": ["Unified"]}
    client.list_group_members.side_effect = lambda gid, owners=False: (
        [{"id": "o", "displayName": "Olga", "@odata.type": "#microsoft.graph.user"}] if owners
        else [{"id": "u", "displayName": "Ugo", "userPrincipalName": "u@c",
               "accountEnabled": True, "@odata.type": "#microsoft.graph.user"}])
    client.get_group_site.return_value = {"id": "s1", "displayName": "Équipe",
                                          "webUrl": "https://c.sharepoint.com/sites/eq"}
    out = _tool()(op="group", group_id="g1")
    assert out["members"] == [{"id": "u", "name": "Ugo", "email": "u@c", "enabled": True,
                               "type": "user"}]
    assert [o["id"] for o in out["owners"]] == ["o"]
    assert out["site"] == {"id": "s1", "name": "Équipe",
                           "webUrl": "https://c.sharepoint.com/sites/eq"}


def test_liste_de_diffusion_sans_site(client):
    client.get_group.return_value = {"id": "g2", "displayName": "Tous", "mailEnabled": True}
    client.list_group_members.return_value = []
    out = _tool()(op="group", group_id="g2")
    assert out["kind"] == "distribution" and out["site"] is None
    client.get_group_site.assert_not_called()


def test_sites(client):
    client.list_sites.return_value = [{"id": "s1", "displayName": "Qualité",
                                       "webUrl": "https://w", "createdDateTime": "t"}]
    out = _tool()(op="sites", query="qual")
    assert out == {"sites": [{"id": "s1", "name": "Qualité", "webUrl": "https://w",
                              "created": "t"}], "count": 1}
    client.list_sites.assert_called_once_with(query="qual", limit=500)


@pytest.mark.parametrize("args,motif", [
    ({"op": "licenses", "sign_in": True}, "only apply to op='users'"),
    ({"op": "groups", "enabled": True}, "only apply to op='users'"),
    ({"op": "users", "kind": "security"}, "only applies to op='groups'"),
    ({"op": "user", "query": "x"}, "does not use `query`"),
    ({"op": "group", "fields": ["id"]}, "only applies to the listing ops"),
    ({"op": "user"}, "user"),
    ({"op": "group"}, "group_id"),
    ({"op": "licenses", "user": "x"}, "user"),
])
def test_arguments_refuses_avant_le_jeton(client, args, motif):
    with pytest.raises(McpError, match=motif):
        _tool()(**args)
    assert client.construits == []


def test_l_annuaire_hors_du_lien_d_approbation_par_defaut(monkeypatch):
    from oto_mcp.auth import microsoft as ms

    vus = []
    monkeypatch.setattr(ms, "admin_consent_url",
                        lambda sub, services, tenant, app, connector:
                        vus.append((services, connector)) or "https://u")
    defaut = ms.admin_approval("sub-1")
    assert "entra" not in defaut["services"] and "sharepoint" in defaut["services"]
    depuis_sa_carte = ms.admin_approval("sub-1", connector="entra")
    assert "entra" in depuis_sa_carte["services"]
    # Un service d'une AUTRE ressource Entra (Power BI) n'entre que par sa propre carte.
    assert set(depuis_sa_carte["services"]) == set(ms.SERVICES) - set(ms.SERVICE_RESOURCE)
    assert "powerbi" not in defaut["services"]
    assert "powerbi" in ms.admin_approval("sub-1", connector="powerbi")["services"]
    assert ms.admin_approval("sub-1", services=["entra"])["services"] == ["entra"]


# ── L'écriture (`entra_act`) ────────────────────────────────────────────────────────

@pytest.mark.parametrize("license", ["Business Standard", "O365_BUSINESS_PREMIUM", BS,
                                     "microsoft 365 business standard"])
def test_licence_attribuee_par_son_nom_ou_son_sku(client, license):
    client.assign_licenses.return_value = {"assignedLicenses": [{"skuId": BS}, {"skuId": BB}]}
    out = _tool("entra_act")(op="add_license", user="ana@contoso.example", license=license)
    assert out == {"user": "ana@contoso.example",
                   "license": "Microsoft 365 Business Standard",
                   "licenses": ["Microsoft 365 Business Standard",
                                "Microsoft 365 Business Basic"]}
    client.assign_licenses.assert_called_once_with("ana@contoso.example", add=[BS])


def test_licence_retiree(client):
    client.assign_licenses.return_value = {"assignedLicenses": []}
    out = _tool("entra_act")(op="remove_license", user="bob", license="Business Basic")
    assert out["licenses"] == []
    client.assign_licenses.assert_called_once_with("bob", remove=[BB])


def test_sans_licence_libre_refuse_avant_l_ecriture(client):
    with pytest.raises(McpError, match="No free Microsoft 365 Business Basic licence"):
        _tool("entra_act")(op="add_license", user="dan", license="Business Basic")
    client.assign_licenses.assert_not_called()


@pytest.mark.parametrize("license,motif", [
    ("Microsoft 365", "names several"), ("Visio", "names none")])
def test_licence_ambigue_ou_inconnue_refusee(client, license, motif):
    with pytest.raises(McpError, match=motif):
        _tool("entra_act")(op="add_license", user="dan", license=license)
    client.assign_licenses.assert_not_called()


def test_compte_desactive_puis_reactive(client):
    assert _tool("entra_act")(op="disable_account", user="cle") == {"user": "cle",
                                                                    "enabled": False}
    _tool("entra_act")(op="enable_account", user="cle")
    assert [c.args for c in client.set_account_enabled.call_args_list] == [
        ("cle", False), ("cle", True)]


def test_membre_ajoute_par_son_id_objet(client):
    client.get_group.return_value = {"id": "g1", "displayName": "Qualité",
                                     "groupTypes": ["Unified"]}
    client.get_user.return_value = {"id": "uid-ana"}
    out = _tool("entra_act")(op="add_member", user="ana@contoso.example", group_id="g1")
    assert out == {"user": "ana@contoso.example", "group": "Qualité", "member": True}
    client.add_group_member.assert_called_once_with("g1", "uid-ana")
    _tool("entra_act")(op="remove_member", user="ana@contoso.example", group_id="g1")
    client.remove_group_member.assert_called_once_with("g1", "uid-ana")


@pytest.mark.parametrize("groupe", [
    {"displayName": "Tous", "mailEnabled": True},
    {"displayName": "Admins", "mailEnabled": True, "securityEnabled": True}])
def test_liste_de_diffusion_refusee_c_est_exchange(client, groupe):
    client.get_group.return_value = groupe
    with pytest.raises(McpError, match="changed in Exchange"):
        _tool("entra_act")(op="add_member", user="ana", group_id="g2")
    client.add_group_member.assert_not_called()


@pytest.mark.parametrize("args,motif", [
    ({"op": "add_license", "user": "a"}, "license"),
    ({"op": "add_license", "license": "x"}, "user"),
    ({"op": "add_member", "user": "a"}, "group_id"),
    ({"op": "disable_account", "user": "a", "license": "x"}, "license"),
    ({"op": "remove_license", "user": "a", "license": "x", "group_id": "g"}, "group_id"),
    ({"op": "delete_user", "user": "a"}, "op must be"),
])
def test_ecriture_refusee_avant_le_jeton(client, args, motif):
    with pytest.raises(McpError, match=motif):
        _tool("entra_act")(**args)
    assert client.construits == []
