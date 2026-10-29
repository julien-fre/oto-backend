"""Connecteur Zoho Sign : la paire `zohosign` / `zohosign_act`, ses niveaux, son consentement.

Outils appelés en direct sur un FastMCP nu, client oto-core remplacé par un faux : ni base,
ni coffre, ni réseau. Seul `test_le_client_se_construit_sur_la_region` importe le client de
la lib : il porte `exige_pin_oto_core` (le client n'existe qu'à partir du tag qui l'ajoute).
"""
from __future__ import annotations

import asyncio

import pytest
from fastmcp import FastMCP

from oto_mcp.auth import zoho as zoho_oauth
from oto_mcp.connectors import access_levels as al
from oto_mcp.mcp_errors import McpError
from oto_mcp.tools import lecture, zohosign as Z

TEMPLATE = {"templates": {"template_id": "T1", "template_name": "NDA", "actions": [
    {"action_id": "A1", "action_type": "SIGN", "role": "Client", "recipient_email": ""}],
    "document_fields": [{"document_id": "D1", "fields": [
        {"field_id": "F1", "field_label": "Company", "field_type_name": "Textfield",
         "is_mandatory": True, "x_coord": 3}]}]}}
REQUEST = {"request_id": "R1", "request_name": "NDA", "request_status": "inprogress",
           "owner_email": "o@example.test", "notes": "long", "actions": [
               {"action_id": "A1", "action_type": "SIGN", "action_status": "UNOPENED",
                "recipient_name": "Ann", "recipient_email": "ann@example.test",
                "fields": [{"x": 1}]},
               {"action_id": "A2", "action_type": "SIGN", "action_status": "SIGNED",
                "recipient_name": "Bob", "recipient_email": "bob@example.test"}]}


class _Faux:
    def __init__(self):
        self.calls = []

    def list_requests(self, **kw):
        self.calls.append(("list_requests", kw))
        return {"requests": [REQUEST, {**REQUEST, "request_id": "R2",
                                       "request_status": "completed"}],
                "page_context": {"has_more_rows": True}}

    def get_request(self, rid):
        self.calls.append(("get_request", rid))
        return {"requests": REQUEST}

    def get_template(self, tid):
        self.calls.append(("get_template", tid))
        return TEMPLATE

    def list_templates(self, **kw):
        self.calls.append(("list_templates", kw))
        return {"templates": [TEMPLATE["templates"]], "page_context": {}}

    def download_pdf(self, rid, with_coc=False, merge=False):
        self.calls.append(("download_pdf", rid, with_coc, merge))
        return {"filename": "NDA.pdf", "content_type": "application/pdf", "content": b"%PDF"}

    def download_completion_certificate(self, rid):
        self.calls.append(("certificate", rid))
        return {"filename": "c.pdf", "content_type": "application/pdf", "content": b"%PDF"}

    def create_from_template(self, tid, actions, **kw):
        self.calls.append(("create_from_template", tid, actions, kw))
        return {"requests": {**REQUEST, "request_id": "R9"}}

    def remind(self, rid):
        self.calls.append(("remind", rid))
        return {"message": "Reminder has been sent"}

    def recall(self, rid):
        self.calls.append(("recall", rid))
        return {"message": "Document has been recalled"}


@pytest.fixture
def env(monkeypatch):
    faux = _Faux()
    monkeypatch.setattr(Z, "_client", lambda: faux)
    rendus = []

    def _rendu(data, filename, mime, *, sub, prefix, **_):
        rendus.append((data, filename, mime, sub, prefix))
        return {"encoding": "url", "url": "https://signed.example.test/x", "filename": filename}

    monkeypatch.setattr(Z.file_content, "render_for_agent", _rendu)
    monkeypatch.setattr(Z.access, "current_user_sub_or_raise", lambda: "sub-1")
    m = FastMCP("t")
    Z.register(m)
    outils = {n: asyncio.run(m.get_tool(n)) for n in ("zohosign", "zohosign_act")}
    return faux, rendus, outils


def _fn(env, name):
    return env[2][name].fn


def test_la_paire_et_ses_annotations(env):
    outils = env[2]
    assert outils["zohosign"].annotations == lecture.LECTURE
    assert outils["zohosign_act"].annotations == lecture.ACTE


def test_les_ops_servies_sont_celles_declarees(env):
    decl = al.DECLARED["zohosign"]
    for nom, outil in env[2].items():
        assert set(outil.parameters["properties"]["op"]["enum"]) == set(decl.tools[nom])


def test_la_liste_est_allegee_et_le_filtre_de_statut_le_dit(env):
    faux, _, _ = env
    out = _fn(env, "zohosign")(op="list", status="completed",
                               search={"recipient_email": "ann@example.test"})
    assert [r["request_id"] for r in out["requests"]] == ["R2"]
    assert "notes" not in out["requests"][0]
    assert out["requests"][0]["recipients"][0] == {
        "action_id": "A1", "action_type": "SIGN", "action_status": "UNOPENED",
        "recipient_name": "Ann", "recipient_email": "ann@example.test"}
    assert "page only" in out["note"]
    assert faux.calls[0][1]["search_columns"] == {"recipient_email": "ann@example.test"}


def test_full_rend_l_enregistrement_brut(env):
    out = _fn(env, "zohosign")(op="get", request_id="R1", full=True)
    assert out["request"] is REQUEST


def test_le_modele_dit_ses_destinataires_et_ses_champs(env):
    out = _fn(env, "zohosign")(op="get_template", template_id="T1")
    assert out["template"]["recipients"] == [
        {"action_id": "A1", "action_type": "SIGN", "role": "Client", "recipient_email": ""}]
    assert out["template"]["fields"] == [{"field_id": "F1", "field_label": "Company",
                                          "field_type_name": "Textfield",
                                          "is_mandatory": True}]


def test_le_pdf_passe_par_le_rendu_commun(env):
    faux, rendus, _ = env
    out = _fn(env, "zohosign")(op="download_pdf", request_id="R1", with_certificate=True)
    assert out["encoding"] == "url"
    assert faux.calls[-1] == ("download_pdf", "R1", True, False)
    assert rendus == [(b"%PDF", "NDA.pdf", "application/pdf", "sub-1", "zohosign-files")]


@pytest.mark.parametrize("kwargs", [
    {"op": "get"},                                          # request_id manquant
    {"op": "get", "request_id": "R1", "status": "x"},       # argument hors op
    {"op": "list", "with_certificate": True},
    {"op": "get_template", "template_id": "T1", "request_id": "R1"},
])
def test_un_argument_manquant_ou_hors_op_est_refuse(env, kwargs):
    with pytest.raises(McpError):
        _fn(env, "zohosign")(**kwargs)


def test_l_envoi_a_blanc_n_ecrit_rien(env):
    faux, _, _ = env
    dest = [{"action_id": "A1", "recipient_name": "Ann", "recipient_email": "ann@example.test"}]
    out = _fn(env, "zohosign_act")(op="send_from_template", template_id="T1",
                                   recipients=dest, dry_run=True,
                                   field_data={"field_text_data": {"Company": "Acme"}})
    assert out["dry_run"] and out["would_send"]["request_name"] == "NDA"
    assert out["would_send"]["prefilled"] == ["Company"]
    assert [c[0] for c in faux.calls] == ["get_template"]


def test_l_envoi_part_en_envoi_immediat(env):
    faux, _, _ = env
    dest = [{"action_id": "A1", "recipient_name": "Ann", "recipient_email": "ann@example.test"}]
    out = _fn(env, "zohosign_act")(op="send_from_template", template_id="T1",
                                   recipients=dest, notes="Hello")
    assert out["sent"] and out["request"]["request_id"] == "R9"
    assert faux.calls[-1] == ("create_from_template", "T1", dest,
                              {"request_name": None, "field_data": None, "notes": "Hello",
                               "quick_send": True})


def test_l_envoi_exige_chaque_destinataire_du_modele(env):
    faux, _, _ = env
    with pytest.raises(McpError, match="same count"):
        _fn(env, "zohosign_act")(op="send_from_template", template_id="T1", recipients=[
            {"action_id": "A9", "recipient_name": "X", "recipient_email": "x@example.test"}])
    assert "create_from_template" not in [c[0] for c in faux.calls]


def test_relance_a_blanc_nomme_qui_n_a_pas_signe(env):
    out = _fn(env, "zohosign_act")(op="remind", request_id="R1", dry_run=True)
    assert [a["recipient_name"] for a in out["request"]["would_remind"]] == ["Ann"]


@pytest.mark.parametrize("op, geste", [("remind", "remind"), ("cancel", "recall")])
def test_relancer_et_rappeler(env, op, geste):
    faux, _, _ = env
    out = _fn(env, "zohosign_act")(op=op, request_id="R1")
    assert out["done"] and faux.calls[-1] == (geste, "R1")


def test_le_consentement_demande_les_scopes_du_niveau_sans_verification():
    assert zoho_oauth.supports("zohosign")
    assert zoho_oauth.consent_scopes("zohosign", "read") == (
        "ZohoSign.documents.READ", "ZohoSign.templates.READ")
    assert zoho_oauth.VERIFICATION_SCOPES["zohosign"] == ()


def test_une_app_qui_nomme_une_organisation_est_refusee(monkeypatch):
    revoques = []
    monkeypatch.setattr(zoho_oauth, "revoke", lambda dc, t: revoques.append(dc))
    with pytest.raises(zoho_oauth.ZohoOrgUnverifiable):
        zoho_oauth.check_org("zohosign", "eu", {"access_token": "a", "refresh_token": "r"},
                             {"client_id": "c", "zoho_org_id": "123"})
    assert revoques == ["eu"]


def test_une_app_sans_organisation_passe_avec_l_avertissement():
    verdict = zoho_oauth.check_org("zohosign", "eu", {"access_token": "a"},
                                   {"client_id": "c"})
    assert verdict == zoho_oauth.OrgCheck(zoho_oauth.ORG_UNVERIFIED, ())


def test_chaque_region_du_consentement_a_ses_hotes():
    assert set(Z._DC_DOMAINS) == set(zoho_oauth._ACCOUNTS)
    for dc, (_api, accounts) in Z._DC_DOMAINS.items():
        assert accounts == zoho_oauth._ACCOUNTS[dc]
    with pytest.raises(McpError):
        Z._domains("us")


@pytest.mark.exige_pin_oto_core
def test_le_client_se_construit_sur_la_region():
    c = Z._build({"client_id": "id", "client_secret": "s", "refresh_token": "r",
                  "data_center": "eu"})
    assert (c.api_domain, c.accounts_url) == ("https://sign.zoho.eu", "https://accounts.zoho.eu")
