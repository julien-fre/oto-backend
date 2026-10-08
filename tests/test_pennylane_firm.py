"""`pennylane_firm` — les outils MCP du connecteur cabinet, et ses gardes d'écriture.

Sans base, comme la CI : le client de la lib est remplacé sur son paquet, la
résolution de clé et l'org de l'appel sont posées par `monkeypatch`. Ce que ces épreuves tiennent :

- chaque outil appelle la bonne méthode de la lib avec les bons arguments, et
  `company_id` est requis partout où l'API le prend ;
- une erreur de la lib devient un refus NOMMÉ (code dans `data`), un 403 nomme le
  scope manquant, et le jeton n'apparaît jamais dans une phrase ;
- la frappe refuse un doublon, et rend un lien que seule la route du relais ouvre.
"""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from oto_mcp.mcp_errors import McpError

JETON = "jeton-de-cabinet-secret-123"
ORG = 7


def _outils():
    from fastmcp import FastMCP
    from oto_mcp.tools import pennylane_firm as P

    m = FastMCP("t")
    P.register(m)
    return {t.name: t for t in asyncio.run(m.list_tools())}


def _outil(nom):
    return _outils()[nom].fn


def _code(exc: pytest.ExceptionInfo) -> str:
    return exc.value.error.data["code"]


@pytest.fixture(autouse=True)
def _cache_vide():
    from oto_mcp.tools import pennylane_firm_socle as socle
    socle._CACHE.clear()
    yield
    socle._CACHE.clear()


@pytest.fixture
def client(monkeypatch):
    import oto.tools.pennylane_firm as pkg
    from oto_mcp.tools import pennylane_firm as P

    inst = MagicMock()
    inst.list_dms_files.return_value = {"items": [], "has_more": False,
                                        "next_cursor": None, "pages": 1}
    construits = []

    def _fabrique(key, **kw):
        construits.append(key)
        return inst

    monkeypatch.setattr(pkg, "PennylaneFirmClient", _fabrique)
    monkeypatch.setattr("oto_mcp.access.resolve_api_key", lambda *a, **k: (JETON, False))
    monkeypatch.setattr("oto_mcp.access.current_org", lambda sub: ORG)
    monkeypatch.setattr(P, "current_user_sub_from_token", lambda: "u-cabinet")
    monkeypatch.setenv("OTO_MCP_OAUTH_STATE_SECRET", "secret-de-banc-assez-long")
    monkeypatch.setenv("OTO_MCP_PUBLIC_URL", "https://oto.example.test/")
    inst.construits = construits
    return inst


# ── Le registre ────────────────────────────────────────────────────────────────

def test_le_prefixe_resout_vers_le_connecteur_cabinet_et_pas_vers_pennylane():
    from oto_mcp.tool_visibility import namespace_of
    for nom in _outils():
        assert namespace_of(nom) == "pennylane_firm", nom
    assert namespace_of("pennylane_invoice") == "pennylane"


def test_la_declaration_est_byo_org_mono_compte_a_jeton_secret():
    from oto_mcp import providers
    c = providers.REGISTRY["pennylane_firm"]
    assert c.auth_modes == frozenset({"byo_org"}) and c.keyed
    assert not c.auth_multi_account
    assert [(f.name, f.secret) for f in c.secret_fields] == [("key", True)]
    assert providers.is_org_shareable("pennylane_firm")


def test_company_id_est_requis_partout_ou_l_api_le_prend():
    outils = _outils()
    for nom in ("pennylane_firm_tree", "pennylane_firm_create_folder",
                "pennylane_firm_upload_url"):
        assert "company_id" in outils[nom].parameters["required"], nom
    assert "parent_folder_id" in outils["pennylane_firm_upload_url"].parameters["required"]


# ── Lectures ───────────────────────────────────────────────────────────────────

def test_la_liste_des_societes_filtre_le_code_client_et_rend_la_vue_de_tri(client):
    client.list_companies.return_value = {
        "items": [{"id": 1, "name": "Acme", "client_code": "0042", "address": "x",
                   "city": "y", "postal_code": "z"}], "total_pages": 1}
    r = _outil("pennylane_firm_companies")(client_code="0042", per_page=50)
    client.list_companies.assert_called_once_with(
        page=None, per_page=50,
        filter=[{"field": "client_code", "operator": "eq", "value": "0042"}])
    assert r["items"] == [{"id": 1, "name": "Acme", "client_code": "0042"}]
    assert "address" in r["projection"]["omitted"] and r["total_pages"] == 1
    assert client.construits == [JETON]


def test_full_rend_le_brut(client):
    brut = {"items": [{"id": 1, "address": "x"}], "total_pages": 1}
    client.list_companies.return_value = brut
    assert _outil("pennylane_firm_companies")(full=True) == brut


def test_company_id_rend_la_fiche_d_une_societe(client):
    client.get_company.return_value = {"id": 9, "name": "Acme"}
    assert _outil("pennylane_firm_companies")(company_id=9) == {"id": 9, "name": "Acme"}
    client.get_company.assert_called_once_with(9)
    client.list_companies.assert_not_called()


def test_l_arbre_liste_les_fichiers_d_un_dossier_au_curseur(client):
    client.list_dms_files.return_value = {
        "items": [{"id": 3, "name": "a.pdf", "url": "https://signed", "path": "/A/a.pdf",
                   "parent_folder": {"id": 5}}], "has_more": True, "next_cursor": "c2"}
    r = _outil("pennylane_firm_tree")(company_id=9, parent_folder_id=5, cursor="c1",
                                      limit=50)
    client.list_dms_files.assert_called_once_with(9, limit=50, cursor="c1",
                                                  parent_folder_id=5)
    assert r["items"] == [{"id": 3, "name": "a.pdf", "path": "/A/a.pdf",
                           "parent_folder": {"id": 5}}]
    assert r["has_more"] is True and r["next_cursor"] == "c2"


def test_l_arbre_des_dossiers_refuse_un_filtre_que_l_api_n_a_pas(client):
    with pytest.raises(McpError) as e:
        _outil("pennylane_firm_tree")(company_id=9, kind="folders", parent_folder_id=5)
    assert _code(e) == "unsupported_filter"
    client.list_dms_folders.assert_not_called()
    client.list_dms_folders.return_value = {"items": [], "has_more": False}
    _outil("pennylane_firm_tree")(company_id=9, kind="folders")
    client.list_dms_folders.assert_called_once_with(9, limit=None, cursor=None)


# ── Traduction des erreurs de la lib ───────────────────────────────────────────

def test_un_403_nomme_le_scope_manquant_sans_le_jeton(client):
    from oto.tools.pennylane_firm import PennylaneFirmScopeMissing
    client.list_companies.side_effect = PennylaneFirmScopeMissing(
        403, "scope_missing", "The firm token lacks the scope. Required scope: "
        "companies:readonly.", scope="companies:readonly")
    with pytest.raises(McpError) as e:
        _outil("pennylane_firm_companies")()
    assert _code(e) == "pennylane_scope_missing"
    assert "companies:readonly" in e.value.error.message
    assert JETON not in e.value.error.message


def test_un_429_est_un_refus_reessayable_qui_dit_d_attendre(client):
    from oto.tools.pennylane_firm import PennylaneFirmRateLimited
    client.get_company.side_effect = PennylaneFirmRateLimited(
        429, "rate_limited", "rate exceeded", retryable=True)
    with pytest.raises(McpError) as e:
        _outil("pennylane_firm_companies")(company_id=9)
    assert _code(e) == "pennylane_rate_limited"
    assert e.value.error.data["retryable"] is True
    assert e.value.error.data["retry_after_seconds"] == 60


@pytest.mark.parametrize("statut, code", [
    (401, "pennylane_token_invalid"), (404, "pennylane_not_found"),
    (422, "pennylane_rejected"), (503, "pennylane_unavailable"),
    (409, "pennylane_error")])
def test_chaque_statut_amont_a_son_refus_nomme(client, statut, code):
    from oto.tools.pennylane_firm import PennylaneFirmError
    client.list_dms_files.side_effect = PennylaneFirmError(statut, "x", "refus amont")
    with pytest.raises(McpError) as e:
        _outil("pennylane_firm_tree")(company_id=9)
    assert _code(e) == code


def test_une_panne_reseau_est_nommee_et_reessayable(client):
    client.list_companies.side_effect = RuntimeError("pennylane_firm: GET /companies")
    with pytest.raises(McpError) as e:
        _outil("pennylane_firm_companies")()
    assert _code(e) == "pennylane_unreachable"
    assert e.value.error.data["retryable"] is True


# ── Écriture ───────────────────────────────────────────────────────────────

def test_la_creation_de_dossier_appelle_la_lib(client):
    client.create_dms_folder.return_value = {"id": 11, "name": "2026"}
    r = _outil("pennylane_firm_create_folder")(company_id=9, name="2026",
                                               parent_folder_id=5)
    client.create_dms_folder.assert_called_once_with(9, "2026", 5)
    assert r["id"] == 11


def test_la_frappe_hors_org_est_refusee(client, monkeypatch):
    """Le lien scelle l'org qui porte le jeton de cabinet : sans org, rien à sceller."""
    monkeypatch.setattr("oto_mcp.access.current_org", lambda sub: None)
    with pytest.raises(McpError) as e:
        _outil("pennylane_firm_upload_url")(company_id=9, parent_folder_id=5,
                                            name="a.pdf")
    assert _code(e) == "no_organization"
    client.list_dms_files.assert_not_called()


# ── Frappe du lien de relais ───────────────────────────────────────────────────

def test_la_frappe_rend_un_lien_de_relais_et_la_commande(client):
    from oto_mcp import upload_tokens
    from oto_mcp.tools import pennylane_firm_relais as relais
    r = _outil("pennylane_firm_upload_url")(company_id=9, parent_folder_id=5,
                                            name=" facture.pdf ")
    assert r["url"].startswith("https://oto.example.test/api/relay/")
    assert r["command"] == f"curl -sS -F 'file=@\"<local path>\"' '{r['url']}'", \
        "le chemin entre guillemets doubles : curl lit sinon `;` et `,` comme séparateurs"
    assert r["method"] == "POST" and r["max_bytes"] == relais.max_bytes()
    jeton = r["url"].rsplit("/", 1)[1]
    payload = relais.verifier(jeton)
    assert payload["sub"] == "u-cabinet" and payload["org"] == ORG
    assert payload["target"] == {"kind": "pennylane_firm_dms", "company_id": 9,
                                 "parent_folder_id": 5, "name": "facture.pdf"}
    # Un jeton de relais n'est PAS un jeton d'upload oto, et inversement.
    assert upload_tokens.verify(jeton) is None
    oto = upload_tokens.sign("u-cabinet", ORG, {"kind": "image"})[0]
    assert relais.verifier(oto) is None
    # Le contrôle de doublon a lu le dossier visé, en entier.
    client.list_dms_files.assert_called_once_with(9, parent_folder_id=5, limit=100,
                                                  all_pages=True, max_pages=100)


def test_la_frappe_refuse_un_nom_deja_present(client):
    client.list_dms_files.return_value = {"items": [{"id": 1, "name": "a.pdf"}],
                                          "has_more": False}
    with pytest.raises(McpError) as e:
        _outil("pennylane_firm_upload_url")(company_id=9, parent_folder_id=5,
                                            name="a.pdf")
    assert _code(e) == "name_already_exists"


def test_un_dossier_lu_en_partie_ne_conclut_jamais_a_l_absence(client):
    client.list_dms_files.return_value = {"items": [], "has_more": True,
                                          "next_cursor": "c"}
    with pytest.raises(McpError) as e:
        _outil("pennylane_firm_upload_url")(company_id=9, parent_folder_id=5,
                                            name="a.pdf")
    assert _code(e) == "duplicate_check_incomplete"


def test_le_cache_sert_la_seconde_frappe_sans_relire_le_dossier(client):
    outil = _outil("pennylane_firm_upload_url")
    outil(company_id=9, parent_folder_id=5, name="a.pdf")
    outil(company_id=9, parent_folder_id=5, name="b.pdf")
    assert client.list_dms_files.call_count == 1


@pytest.mark.parametrize("nom", ["", "   ", "x" * 256])
def test_un_nom_hors_bornes_est_refuse(client, nom):
    with pytest.raises(McpError) as e:
        _outil("pennylane_firm_upload_url")(company_id=9, parent_folder_id=5, name=nom)
    assert _code(e) == "invalid_name"


# ── Le cache, seul ─────────────────────────────────────────────────────────────

def test_un_nom_en_vol_est_refuse_puis_libere_ou_confirme():
    from oto_mcp.tools import pennylane_firm_socle as socle
    c = MagicMock()
    c.list_dms_files.return_value = {"items": [], "has_more": False}
    socle.reserver(c, ORG, 9, 5, "a.pdf")
    with pytest.raises(socle.Refus) as e:
        socle.reserver(c, ORG, 9, 5, "a.pdf")
    assert e.value.code == "name_already_exists"
    socle.liberer(ORG, 9, 5, "a.pdf")
    socle.reserver(c, ORG, 9, 5, "a.pdf")
    socle.confirmer(ORG, 9, 5, "a.pdf")
    with pytest.raises(socle.Refus):
        socle.verifier_absent(c, ORG, 9, 5, "a.pdf")
    # Une autre org a son propre cache : rien ne fuit d'une org à l'autre.
    socle.verifier_absent(c, ORG + 1, 9, 5, "a.pdf")


def test_l_invalidation_force_une_relecture_et_garde_les_noms_en_vol():
    from oto_mcp.tools import pennylane_firm_socle as socle
    c = MagicMock()
    c.list_dms_files.return_value = {"items": [], "has_more": False}
    socle.reserver(c, ORG, 9, 5, "en-vol.pdf")
    socle.invalider(ORG, 9, 5)
    socle.verifier_absent(c, ORG, 9, 5, "autre.pdf")
    assert c.list_dms_files.call_count == 2
    with pytest.raises(socle.Refus):
        socle.verifier_absent(c, ORG, 9, 5, "en-vol.pdf")
