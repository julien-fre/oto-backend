"""Waterfall natif (`tools/waterfall.py`) — verrouille : chaque étape passe par
`meta.executer_cible` (jamais un client fournisseur), la cascade s'arrête au PREMIER
trouvé, une étape sans ses entrées / refusée par une garde / en échec / sans résultat
passe la main à la suivante en le disant dans `attempts`, `providers` ne nomme que des
clés de la table (jamais un outil), `dry_run` n'appelle rien, l'entrée registre est celle
d'un composite sans credential, et la surface est UN outil `waterfall(op=…)` en lecture
(convention `<ns>` / `<ns>_act`, `tests/test_convention_act.py`)."""
import asyncio

import pytest
from fastmcp import FastMCP
from mcp.types import ErrorData, INVALID_PARAMS

from oto_mcp import providers
from oto_mcp.mcp_errors import McpError
from oto_mcp.tools import meta
from oto_mcp.tools import waterfall
from oto_mcp.tools.meta import IssueCible


@pytest.fixture
def calls(monkeypatch):
    """Les réponses par outil (`IssueCible` ou exception à lever), et le relevé des
    appels faits."""
    state = {"answers": {}, "made": []}

    async def resoudre(_mcp, name):
        return name  # l'« outil » résolu : son nom suffit au faux `executer_cible`

    async def executer(tool, sub, name, demande, args):
        state["made"].append((name, args))
        answer = state["answers"].get(name, IssueCible(ok=True, result={}))
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(meta, "resoudre_outil", resoudre)
    monkeypatch.setattr(meta, "executer_cible", executer)
    monkeypatch.setattr(waterfall, "current_user_sub_from_token", lambda: "sub-test")
    return state


def _run(op, **kwargs):
    m = FastMCP("t")
    waterfall.register(m)
    return asyncio.run(asyncio.run(m.get_tool("waterfall")).fn(op=op, **kwargs))


# --- registre & surface -----------------------------------------------------------

def test_composite_sans_credential():
    c = providers.REGISTRY["waterfall"]
    assert c.kind == "tools" and c.secret_kind == "none" and not c.keyed
    assert c.default_active is False


def test_un_seul_outil_decrit_et_en_lecture():
    m = FastMCP("t")
    waterfall.register(m)
    tools = asyncio.run(m._list_tools())
    assert [t.name for t in tools] == ["waterfall"]
    t = tools[0]
    assert t.description and t.annotations.readOnlyHint is True
    props = t.parameters["properties"]
    assert props["providers"].get("description") and props["dry_run"].get("description")


def test_les_etapes_ne_nomment_que_des_outils_montes():
    from oto_mcp.tools import register_all
    m = FastMCP("t")
    register_all(m)
    names = {t.name for t in asyncio.run(m._list_tools())}
    for steps in (waterfall.EMAIL_STEPS, waterfall.PHONE_STEPS, waterfall.COMPANY_STEPS):
        for s in steps:
            assert s.tool in names, s.tool
            assert not s.tool.startswith("waterfall")


# --- cascade ----------------------------------------------------------------------

def test_s_arrete_au_premier_trouve(calls):
    calls["answers"]["hunter_email_finder"] = IssueCible(ok=True, result={"data": {"email": None}})
    calls["answers"]["apollo_match_person"] = IssueCible(
        ok=True, result={"person": {"email": "Jane@Acme.com", "email_status": "verified"}})
    out = _run("email", first_name="Jane", last_name="Doe", domain="https://www.acme.com/x",
               linkedin_url="https://www.linkedin.com/in/jane-doe/")
    assert out["found"] is True and out["provider"] == "apollo"
    assert out["email"] == {"email": "jane@acme.com", "all": ["jane@acme.com"], "status": "verified"}
    assert [n for n, _ in calls["made"]] == ["hunter_email_finder", "apollo_match_person"]
    assert calls["made"][0][1] == {"domain": "acme.com", "first_name": "Jane", "last_name": "Doe"}
    assert [a["status"] for a in out["attempts"]] == ["no_match", "found"]


def test_etape_sans_ses_entrees_sautee_sans_appel(calls):
    calls["answers"]["linkedin_aiark_person"] = IssueCible(
        ok=True, result={"found": True, "email": {"output": [
            {"address": "x@acme.com", "status": "unknown"},
            {"address": "jane@acme.com", "status": "valid"}]}})
    out = _run("email", linkedin_url="https://linkedin.com/in/jane-doe")
    # hunter exige nom + domaine : sauté sans appel ; apollo tourne sur l'URL seule.
    assert out["attempts"][0] == {"provider": "hunter", "tool": "hunter_email_finder",
                                  "status": "skipped", "code": "missing_input",
                                  "message": "needs first_name+last_name+domain"}
    assert out["provider"] == "aiark" and out["email"]["email"] == "jane@acme.com"
    assert "hunter_email_finder" not in [n for n, _ in calls["made"]]


def test_garde_et_echec_passent_la_main(calls):
    calls["answers"]["kaspr_enrich_linkedin"] = McpError(ErrorData(
        code=INVALID_PARAMS, message="connector off", data={"code": "connector_disabled"}))
    calls["answers"]["linkedin_aiark_person"] = IssueCible(
        ok=False, code="quota_exhausted", message="no credits")
    calls["answers"]["forager_person"] = IssueCible(ok=True, result=[{"phone_number": "+33 6 12 34 56 78"}])
    out = _run("phone", linkedin_url="https://www.linkedin.com/in/Jane-Doe/?trk=x")
    assert out["provider"] == "forager" and out["phone"]["phone"] == "+33 6 12 34 56 78"
    by = {a["provider"]: a for a in out["attempts"]}
    assert by["kaspr"]["status"] == "skipped" and by["kaspr"]["code"] == "connector_disabled"
    assert by["aiark"]["status"] == "failed" and by["aiark"]["code"] == "quota_exhausted"
    assert dict(calls["made"])["forager_person"]["linkedin_public_identifier"] == "jane-doe"


def test_rien_trouve_rend_un_recu_complet(calls):
    out = _run("phone", linkedin_url="https://linkedin.com/in/jane")
    assert out["found"] is False and out["phone"] is None
    assert [a["status"] for a in out["attempts"]] == ["no_match"] * 4


def test_resultat_retenu_par_la_redaction_n_est_pas_un_trouve(calls):
    calls["answers"]["hunter_email_finder"] = IssueCible(ok=True, result="withheld", retenu=True)
    out = _run("email", full_name="Jane Doe", domain="acme.com", providers=["hunter"])
    assert out["found"] is False and out["attempts"][0]["code"] == "redaction_withheld"


def test_stub_apollo_et_echo_de_l_entree_ne_comptent_pas(calls):
    calls["answers"]["apollo_match_person"] = IssueCible(
        ok=True, result={"person": {"_stub": True, "email": "jane@acme.com"}})
    out = _run("email", full_name="Jane Doe", domain="acme.com", providers=["apollo"])
    assert out["found"] is False


def test_extraction_profonde_kaspr_et_lusha(calls):
    calls["answers"]["kaspr_enrich_linkedin"] = IssueCible(ok=True, result={
        "profile": {"professionalEmails": ["jane@acme.com"], "emailStatus": "x@y.zz"}})
    calls["answers"]["lusha_search_and_enrich"] = IssueCible(ok=True, result={
        "results": [{"phones": [{"number": "+1 415 555 0100", "phoneType": "mobile"}]}]})
    assert _run("email", linkedin_url="https://linkedin.com/in/jane",
                providers=["kaspr"])["email"]["all"] == ["jane@acme.com"]
    assert _run("phone", linkedin_url="https://linkedin.com/in/jane",
                providers=["lusha"])["phone"]["phone"] == "+1 415 555 0100"


def test_lusha_erreur_par_ligne_est_un_non_trouve(calls):
    calls["answers"]["lusha_search_and_enrich"] = IssueCible(ok=True, result={
        "results": [{"error": {"code": "NOT_FOUND"}, "emails": ["echo@acme.com"]}]})
    out = _run("email", full_name="Jane Doe", domain="acme.com", providers=["lusha"])
    assert out["found"] is False


# --- providers & dry_run ----------------------------------------------------------

def test_providers_reordonne_et_restreint(calls):
    _run("email", full_name="Jane Doe", domain="acme.com", providers=["lusha", "hunter"])
    assert [n for n, _ in calls["made"]] == ["lusha_search_and_enrich", "hunter_email_finder"]


def test_providers_refuse_un_nom_d_outil(calls):
    with pytest.raises(McpError, match="Unknown provider"):
        _run("email", full_name="Jane Doe", domain="acme.com",
             providers=["hunter_email_finder"])
    assert calls["made"] == []


def test_aucune_etape_possible_est_refusee(calls):
    with pytest.raises(McpError, match="No provider"):
        _run("email", first_name="Jane")
    assert calls["made"] == []


def test_dry_run_n_appelle_rien(calls):
    out = _run("email", full_name="Jane Doe", domain="acme.com", dry_run=True)
    assert calls["made"] == [] and out["dry_run"] is True
    plan = {a["provider"]: a for a in out["attempts"]}
    assert plan["hunter"]["status"] == "planned"
    assert plan["hunter"]["arguments"] == {"domain": "acme.com", "first_name": "Jane", "last_name": "Doe"}
    assert plan["kaspr"]["status"] == "skipped"


# --- société ----------------------------------------------------------------------

def test_societe_registre_par_siren(calls):
    calls["answers"]["fr_get"] = IssueCible(ok=True, result={"identity": {
        "siren": "123456789", "nom_complet": "ACME", "activite_principale": "62.01Z",
        "siege": {"libelle_commune": "PARIS"}, "site_internet": "https://acme.com"}})
    out = _run("company", siren="123 456 789")
    assert out["provider"] == "fr_get"
    assert out["company"]["siren"] == "123456789" and out["company"]["domain"] == "acme.com"
    assert calls["made"] == [("fr_get", {"siren": "123456789"})]


def test_societe_homonyme_ne_stoppe_pas_la_cascade(calls):
    calls["answers"]["fr_search"] = IssueCible(ok=True, result={"results": [
        {"siren": "111111111", "nom_complet": "ACMEX HOLDING"}]})
    calls["answers"]["apollo_enrich_organization"] = IssueCible(ok=True, result={
        "organization": {"name": "Acme", "primary_domain": "acme.com",
                         "estimated_num_employees": 42}})
    out = _run("company", company_name="Acme", domain="acme.com")
    assert [a["status"] for a in out["attempts"]] == ["skipped", "no_match", "found"]
    assert out["company"]["employees"] == 42


def test_societe_nom_contenu_dans_la_raison_sociale(calls):
    calls["answers"]["fr_search"] = IssueCible(ok=True, result={"results": [
        {"siren": "222222222", "nom_complet": "Société Acmé Industries"}]})
    out = _run("company", company_name="acme")
    assert out["provider"] == "fr_search" and out["company"]["siren"] == "222222222"


def test_societe_hors_france_saute_le_registre(calls):
    out = _run("company", company_name="Acme", domain="acme.de", country="DE",
               dry_run=True)
    plan = {a["provider"]: a["status"] for a in out["attempts"]}
    assert plan == {"fr_get": "skipped", "fr_search": "skipped", "apollo": "planned"}


def test_siren_malforme_refuse(calls):
    with pytest.raises(McpError, match="9 digits"):
        _run("company", siren="12345")


def test_404_du_fournisseur_est_un_non_trouve(calls):
    calls["answers"]["hunter_email_finder"] = IssueCible(ok=False, code="not_found", message="404")
    out = _run("email", full_name="Jane Doe", domain="acme.com", providers=["hunter"])
    assert out["attempts"][0]["status"] == "no_match"


def test_registre_saute_dit_ce_qu_il_faut(calls):
    out = _run("company", company_name="Acme", country="DE", domain="acme.de", dry_run=True)
    fr = next(a for a in out["attempts"] if a["provider"] == "fr_search")
    assert "French company" in fr["message"] and "+fr" not in fr["message"]
