"""Waterfalls natifs (`tools/waterfall.py`) — verrouille : quatre connecteurs
(`waterfall_email|phone|company|person`), un outil `<ns>` chacun, en lecture, servis par
UN module ; chaque étape passe par `meta.executer_cible` (jamais un client fournisseur) ;
la cascade s'arrête au PREMIER trouvé ; une étape sans ses entrées / sans clé / refusée
par une garde / en échec / sans résultat passe la main en le disant dans `attempts` ;
l'ordre vient de l'appel, sinon du réglage `steps` de la carte, sinon du défaut — et les
fournisseurs dont l'appelant détient SA clé passent devant ; une étape asynchrone
(lemlist) soumet puis relève dans un délai borné, sinon reste `pending` ; `providers` ne
nomme que des clés de la table ; `dry_run` n'appelle rien."""
import asyncio

import pytest
from fastmcp import FastMCP
from mcp.types import ErrorData, INVALID_PARAMS

from oto_mcp import providers
from oto_mcp.mcp_errors import McpError
from oto_mcp.tool_visibility import namespace_of
from oto_mcp.tools import meta
from oto_mcp.tools import waterfall
from oto_mcp.tools.meta import IssueCible

def _servi(issue):
    """Ce qu'`executer_cible` rend VRAIMENT : le `ToolResult` servi, jamais le dict brut
    de l'outil (une liste y est enveloppée en `{"result": [...]}`, comme FastMCP le
    fait). Un faux qui rendait le dict a caché que la cascade ne lisait rien en vrai."""
    from dataclasses import replace
    from fastmcp.tools.tool import ToolResult
    r = issue.result
    if not issue.ok or issue.retenu or not isinstance(r, (dict, list)):
        return issue
    return replace(issue, result=ToolResult(
        structured_content=r if isinstance(r, dict) else {"result": r}))


NAMESPACES = ("waterfall_email", "waterfall_phone", "waterfall_company", "waterfall_person",
              "waterfall_people")


@pytest.fixture
def calls(monkeypatch):
    """Réponses par outil (`IssueCible`, liste d'`IssueCible` servies tour à tour, ou
    exception), réglage `steps`, modes de clé — et le relevé des appels faits."""
    state = {"answers": {}, "made": [], "setting": None, "modes": {}}

    async def resoudre(_mcp, name):
        return name

    async def executer(tool, sub, name, demande, args):
        state["made"].append((name, args))
        answer = state["answers"].get(name, IssueCible(ok=True, result={}))
        if isinstance(answer, list):
            answer = answer.pop(0) if len(answer) > 1 else answer[0]
        if isinstance(answer, Exception):
            raise answer
        return _servi(answer)

    monkeypatch.setattr(meta, "resoudre_outil", resoudre)
    monkeypatch.setattr(meta, "executer_cible", executer)
    monkeypatch.setattr(waterfall, "current_user_sub_from_token", lambda: "sub-test")
    monkeypatch.setattr(waterfall, "_configured_steps", lambda ns: state["setting"])
    monkeypatch.setattr(waterfall, "_key_modes", lambda sub, tools: {
        t: state["modes"].get(t, "platform") for t in tools})
    monkeypatch.setattr(waterfall, "POLL_FIRST_S", 0)
    monkeypatch.setattr(waterfall, "POLL_EVERY_S", 0)
    return state


def _run(ns, **kwargs):
    m = FastMCP("t")
    waterfall.register(m)
    return asyncio.run(asyncio.run(m.get_tool(ns)).fn(**kwargs))


def _made(calls):
    return [n for n, _ in calls["made"]]


# --- registre & surface -----------------------------------------------------------

@pytest.mark.parametrize("ns", NAMESPACES)
def test_quatre_connecteurs_qui_ne_portent_qu_un_reglage(ns):
    c = providers.REGISTRY[ns]
    assert c.kind == "tools" and c.modules == ("waterfall",)
    assert c.auth_modes == frozenset({"byo_user", "byo_org"}) and not c.keyed
    assert [f.name for f in c.credential_fields] == ["steps"]
    assert c.credential_fields[0].secret is False and c.default_active is False
    # Le nom entier EST le namespace : sans quoi l'outil tomberait hors de tout
    # connecteur, donc hors gate d'activation.
    assert namespace_of(ns) == ns and namespace_of(f"{ns}_act") == ns


def test_un_outil_par_connecteur_decrit_et_en_lecture():
    m = FastMCP("t")
    waterfall.register(m)
    tools = {t.name: t for t in asyncio.run(m._list_tools())}
    assert set(tools) == set(NAMESPACES)
    for t in tools.values():
        assert t.description and t.annotations.readOnlyHint is True
        props = t.parameters["properties"]
        assert props["providers"].get("description") and props["dry_run"].get("description")


def test_les_etapes_ne_nomment_que_des_outils_montes():
    from oto_mcp.tools import register_all
    m = FastMCP("t")
    register_all(m)
    names = {t.name for t in asyncio.run(m._list_tools())}
    for steps in waterfall.STEPS_BY_NAMESPACE.values():
        for s in steps:
            assert s.tool in names, s.tool
            assert not s.tool.startswith("waterfall")
            if s.collect:
                assert s.collect.tool in names, s.collect.tool


def test_pas_d_ai_ark_pour_le_telephone_mais_pour_societe_et_personne():
    assert "aiark" not in {s.key for s in waterfall.PHONE_STEPS}
    assert "aiark" in {s.key for s in waterfall.COMPANY_STEPS}
    assert "aiark" in {s.key for s in waterfall.PERSON_STEPS}
    for steps in (waterfall.EMAIL_STEPS, waterfall.PHONE_STEPS, waterfall.PERSON_STEPS):
        assert "lemlist" in {s.key for s in steps}


# --- cascade ----------------------------------------------------------------------

def test_s_arrete_au_premier_trouve(calls):
    calls["answers"]["hunter_email_finder"] = IssueCible(ok=True, result={"data": {"email": None}})
    calls["answers"]["apollo_match_person"] = IssueCible(
        ok=True, result={"person": {"email": "Jane@Acme.com", "email_status": "verified"}})
    out = _run("waterfall_email", first_name="Jane", last_name="Doe",
               domain="https://www.acme.com/x", linkedin_url="https://www.linkedin.com/in/jane-doe/")
    assert out["found"] is True and out["provider"] == "apollo"
    assert out["email"] == {"email": "jane@acme.com", "all": ["jane@acme.com"], "status": "verified"}
    # AI Ark (◎ 1) passes before Apollo (◎ 2): it found nothing here.
    assert _made(calls) == ["hunter_email_finder", "linkedin_aiark_person", "apollo_match_person"]
    assert calls["made"][0][1] == {"domain": "acme.com", "first_name": "Jane", "last_name": "Doe"}
    assert out["order_from"] == "default"


def test_les_cles_de_l_appelant_passent_devant(calls):
    calls["modes"] = {"lusha_search_and_enrich": "org", "kaspr_enrich_linkedin": "user"}
    out = _run("waterfall_email", full_name="Jane Doe", domain="acme.com",
               linkedin_url="https://linkedin.com/in/jane", dry_run=True)
    order = [a["provider"] for a in out["attempts"]]
    # Propres clés d'abord, dans l'ordre du défaut (kaspr avant lusha), puis le reste.
    assert order[:2] == ["kaspr", "lusha"]
    assert order[2:] == ["hunter", "aiark", "apollo", "dropcontact", "lemlist", "fullenrich",
                         "forager"]
    assert out["attempts"][0]["key"] == "user"


def test_un_fournisseur_sans_cle_est_saute_sans_appel(calls):
    calls["modes"] = {"hunter_email_finder": "forbidden"}
    _run("waterfall_email", full_name="Jane Doe", domain="acme.com", providers=["hunter", "apollo"])
    assert _made(calls) == ["apollo_match_person"]


def test_un_ordre_ecrit_n_est_pas_retrie(calls):
    calls["modes"] = {"hunter_email_finder": "user"}
    _run("waterfall_email", full_name="Jane Doe", domain="acme.com", providers=["lusha", "hunter"])
    assert _made(calls) == ["lusha_search_and_enrich", "hunter_email_finder"]


def test_les_etapes_gratuites_restent_devant_une_cle_payante(calls):
    calls["modes"] = {"apollo_enrich_organization": "org"}
    out = _run("waterfall_company", company_name="Acme", domain="acme.com", siren="123456789",
               dry_run=True)
    assert [a["provider"] for a in out["attempts"]][:2] == ["fr", "apollo"]


def test_le_reglage_de_la_carte_fixe_l_ordre_et_ignore_l_inconnu(calls):
    calls["modes"] = {"hunter_email_finder": "user"}
    calls["setting"] = ["lusha", "nope", "hunter"]
    out = _run("waterfall_email", full_name="Jane Doe", domain="acme.com")
    assert _made(calls) == ["lusha_search_and_enrich", "hunter_email_finder"]
    assert out["order_from"] == "setting" and out["ignored_steps"] == ["nope"]


def test_l_appel_l_emporte_sur_le_reglage(calls):
    calls["setting"] = ["lusha"]
    out = _run("waterfall_email", full_name="Jane Doe", domain="acme.com", providers=["hunter"])
    assert _made(calls) == ["hunter_email_finder"] and out["order_from"] == "call"


def test_etape_sans_ses_entrees_sautee_sans_appel(calls):
    calls["answers"]["linkedin_aiark_person"] = IssueCible(
        ok=True, result={"found": True, "email": {"output": [
            {"address": "x@acme.com", "status": "unknown"},
            {"address": "jane@acme.com", "status": "valid"}]}})
    out = _run("waterfall_email", linkedin_url="https://linkedin.com/in/jane-doe")
    first = out["attempts"][0]
    assert (first["provider"], first["status"], first["code"]) == ("hunter", "skipped", "missing_input")
    assert out["provider"] == "aiark" and out["email"]["email"] == "jane@acme.com"
    assert "hunter_email_finder" not in _made(calls)


def test_garde_et_echec_passent_la_main(calls):
    calls["answers"]["kaspr_enrich_linkedin"] = McpError(ErrorData(
        code=INVALID_PARAMS, message="connector off", data={"code": "connector_disabled"}))
    calls["answers"]["forager_person"] = IssueCible(ok=False, code="quota_exhausted", message="no credits")
    calls["answers"]["lusha_search_and_enrich"] = IssueCible(ok=True, result={
        "results": [{"phones": [{"number": "+33 6 12 34 56 78", "phoneType": "mobile"}]}]})
    # Lusha passes before Forager (◎ 33): it is the one that must find, after Forager's failure.
    out = _run("waterfall_phone", linkedin_url="https://www.linkedin.com/in/Jane-Doe/?trk=x",
               providers=["kaspr", "forager", "lusha"])
    assert out["provider"] == "lusha" and out["phone"]["phone"] == "+33 6 12 34 56 78"
    by = {a["provider"]: a for a in out["attempts"]}
    assert by["kaspr"]["status"] == "skipped" and by["kaspr"]["code"] == "connector_disabled"
    assert by["forager"]["status"] == "failed" and by["forager"]["code"] == "quota_exhausted"
    assert dict(calls["made"])["forager_person"]["linkedin_public_identifier"] == "jane-doe"


def test_404_du_fournisseur_est_un_non_trouve(calls):
    calls["answers"]["hunter_email_finder"] = IssueCible(ok=False, code="not_found", message="404")
    out = _run("waterfall_email", full_name="Jane Doe", domain="acme.com", providers=["hunter"])
    assert out["attempts"][0]["status"] == "no_match"


def test_resultat_retenu_par_la_redaction_n_est_pas_un_trouve(calls):
    calls["answers"]["hunter_email_finder"] = IssueCible(ok=True, result="withheld", retenu=True)
    out = _run("waterfall_email", full_name="Jane Doe", domain="acme.com", providers=["hunter"])
    assert out["found"] is False and out["attempts"][0]["code"] == "redaction_withheld"


def test_stub_apollo_ne_compte_pas(calls):
    calls["answers"]["apollo_match_person"] = IssueCible(
        ok=True, result={"person": {"_stub": True, "email": "jane@acme.com"}})
    out = _run("waterfall_email", full_name="Jane Doe", domain="acme.com", providers=["apollo"])
    assert out["found"] is False


def test_extraction_profonde_kaspr(calls):
    calls["answers"]["kaspr_enrich_linkedin"] = IssueCible(ok=True, result={
        "profile": {"professionalEmails": ["jane@acme.com"], "emailStatus": "x@y.zz"}})
    assert _run("waterfall_email", linkedin_url="https://linkedin.com/in/jane",
                providers=["kaspr"])["email"]["all"] == ["jane@acme.com"]


def test_lusha_erreur_par_ligne_est_un_non_trouve(calls):
    calls["answers"]["lusha_search_and_enrich"] = IssueCible(ok=True, result={
        "results": [{"error": {"code": "NOT_FOUND"}, "emails": ["echo@acme.com"]}]})
    out = _run("waterfall_email", full_name="Jane Doe", domain="acme.com", providers=["lusha"])
    assert out["found"] is False


# --- lemlist : soumettre puis relever ----------------------------------------------

def test_lemlist_soumet_puis_releve(calls):
    calls["answers"]["lemlist_enrich"] = IssueCible(ok=True, result={"enrichment_id": "enr_1"})
    calls["answers"]["lemlist_enrich_result"] = [
        IssueCible(ok=True, result={"results": [{"status": "in-progress", "done": False}]}),
        IssueCible(ok=True, result={"results": [{"status": "done", "done": True,
                                                 "found": {"email": "jane@acme.com",
                                                           "email_status": "deliverable"}}]}),
    ]
    out = _run("waterfall_email", linkedin_url="https://linkedin.com/in/jane", providers=["lemlist"])
    assert out["provider"] == "lemlist"
    assert out["email"] == {"email": "jane@acme.com", "all": ["jane@acme.com"], "status": "deliverable"}
    assert calls["made"][0] == ("lemlist_enrich", {
        "linkedin_url": "https://linkedin.com/in/jane", "find_email": True})
    assert _made(calls) == ["lemlist_enrich", "lemlist_enrich_result", "lemlist_enrich_result"]
    assert calls["made"][1][1] == {"enrichment_id": "enr_1"}


def test_lemlist_pas_fini_a_temps_reste_pending_et_la_cascade_continue(calls, monkeypatch):
    monkeypatch.setattr(waterfall, "POLL_BUDGET_S", 0)
    calls["answers"]["lemlist_enrich"] = IssueCible(ok=True, result={"enrichment_id": "enr_2"})
    calls["answers"]["lemlist_enrich_result"] = IssueCible(
        ok=True, result={"results": [{"status": "in-progress", "done": False}]})
    calls["answers"]["lusha_search_and_enrich"] = IssueCible(ok=True, result={
        "results": [{"phones": ["+33612345678"]}]})
    out = _run("waterfall_phone", linkedin_url="https://linkedin.com/in/jane",
               providers=["lemlist", "lusha"])
    assert out["provider"] == "lusha"
    assert out["pending"] == [{"provider": "lemlist", "tool": "lemlist_enrich_result",
                               "arguments": {"enrichment_id": "enr_2"}}]
    assert out["attempts"][0]["status"] == "pending"


def test_lemlist_personne_lit_le_profil_linkedin(calls):
    calls["answers"]["lemlist_enrich"] = IssueCible(ok=True, result={"enrichment_id": "enr_3"})
    calls["answers"]["lemlist_enrich_result"] = IssueCible(ok=True, result={"results": [{
        "done": True, "found": {"linkedin": True},
        "data": {"linkedin": {"firstName": "Jane", "jobTitle": "CTO", "companyName": "Acme",
                              "locationName": "Paris"}}}]})
    out = _run("waterfall_person", linkedin_url="https://linkedin.com/in/jane", providers=["lemlist"])
    assert out["person"]["title"] == "CTO" and out["person"]["company_name"] == "Acme"


# --- providers & dry_run ----------------------------------------------------------

def test_providers_refuse_un_nom_d_outil(calls):
    with pytest.raises(McpError, match="Unknown provider"):
        _run("waterfall_email", full_name="Jane Doe", domain="acme.com",
             providers=["hunter_email_finder"])
    assert calls["made"] == []


def test_aucune_etape_possible_est_refusee(calls):
    with pytest.raises(McpError, match="No provider"):
        _run("waterfall_email", first_name="Jane")
    assert calls["made"] == []


def test_dry_run_n_appelle_rien(calls):
    out = _run("waterfall_email", full_name="Jane Doe", domain="acme.com", dry_run=True)
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
    out = _run("waterfall_company", siren="123 456 789")
    assert out["provider"] == "fr" and out["attempts"][0]["via"] == "fr_siren"
    assert out["company"]["siren"] == "123456789" and out["company"]["domain"] == "acme.com"
    assert calls["made"] == [("fr_get", {"siren": "123456789"})]


def test_societe_homonyme_ne_stoppe_pas_la_cascade(calls):
    calls["answers"]["fr_search"] = IssueCible(ok=True, result={"results": [
        {"siren": "111111111", "nom_complet": "ACMEX HOLDING"}]})
    calls["answers"]["linkedin_aiark_search"] = IssueCible(ok=True, result={"content": [
        {"summary": {"name": "Acme", "industry": "Software", "staff": {"total": 42}},
         "link": {"linkedin": "https://linkedin.com/company/acme"},
         "location": {"headquarter": {"city": "Paris", "country": "France"}}}]})
    out = _run("waterfall_company", company_name="Acme", domain="acme.com")
    # Le registre (une ligne) : pas de SIREN, la recherche par nom rend un homonyme.
    assert [a["status"] for a in out["attempts"][:2]] == ["no_match", "found"]
    assert [t["via"] for t in out["attempts"][0]["tried"]] == ["fr_name"]
    assert out["provider"] == "aiark"
    assert out["company"]["employees"] == 42 and out["company"]["domain"] == "acme.com"
    assert calls["made"][-1][1]["account"] == {"domain": {"any": {"include": ["acme.com"]}}}


def test_societe_nom_contenu_dans_la_raison_sociale(calls):
    calls["answers"]["fr_search"] = IssueCible(ok=True, result={"results": [
        {"siren": "222222222", "nom_complet": "Société Acmé Industries"}]})
    out = _run("waterfall_company", company_name="acme")
    assert out["provider"] == "fr" and out["company"]["siren"] == "222222222"
    assert out["attempts"][0]["via"] == "fr_name"


def test_societe_theirstack_exige_le_meme_domaine(calls):
    calls["answers"]["theirstack_companies_search"] = IssueCible(ok=True, result={"data": [
        {"name": "Other", "domain": "other.com"}, {"name": "Acme", "domain": "acme.com",
                                                   "employee_count": 7}]})
    out = _run("waterfall_company", domain="acme.com", providers=["theirstack"])
    assert out["company"]["name"] == "Acme" and out["company"]["employees"] == 7


def test_societe_hors_france_saute_le_registre_en_le_disant(calls):
    out = _run("waterfall_company", company_name="Acme", domain="acme.de", country="DE",
               dry_run=True)
    plan = {a["provider"]: a for a in out["attempts"]}
    assert plan["fr"]["status"] == "skipped"
    assert "French company" in plan["fr"]["message"]
    assert plan["apollo"]["status"] == "planned"


def test_siren_malforme_refuse(calls):
    with pytest.raises(McpError, match="9 digits"):
        _run("waterfall_company", siren="12345")


# --- personne ---------------------------------------------------------------------

def test_personne_aiark_puis_apollo(calls):
    calls["answers"]["linkedin_unipile_profile"] = IssueCible(ok=False, code="invalid_input",
                                                              message="no LinkedIn account")
    calls["answers"]["linkedin_aiark_person"] = IssueCible(ok=True, result={"found": False})
    calls["answers"]["apollo_match_person"] = IssueCible(ok=True, result={"person": {
        "title": "Head of Sales", "seniority": "head", "city": "Lyon",
        "organization": {"name": "Acme", "primary_domain": "acme.com"}}})
    out = _run("waterfall_person", linkedin_url="https://linkedin.com/in/jane")
    assert out["provider"] == "apollo"
    assert out["person"] == {"title": "Head of Sales", "company_name": "Acme",
                             "company_domain": "acme.com", "seniority": "head", "city": "Lyon",
                             "linkedin_url": "https://linkedin.com/in/jane"}
    assert calls["made"][0] == ("linkedin_unipile_profile", {"op": "person", "identifier": "jane"})


def test_personne_sans_titre_ni_societe_n_est_pas_un_trouve(calls):
    calls["answers"]["apollo_match_person"] = IssueCible(ok=True, result={"person": {"name": "Jane"}})
    out = _run("waterfall_person", email="jane@acme.com", providers=["apollo"])
    assert out["found"] is False
    assert calls["made"][0][1]["email"] == "jane@acme.com"


# --- clés et réglage, contre la vraie couche d'accès (base simulée) ------------------

def test_modes_de_cle_lus_une_fois_par_porteur(monkeypatch):
    from oto_mcp import access
    vus = []

    def mode(sub, carrier):
        vus.append(carrier)
        return {"apollo": "org"}.get(carrier, "platform")
    monkeypatch.setattr(access, "credential_mode_for", mode)
    modes = waterfall._key_modes("sub-test", ["apollo_match_person", "apollo_enrich_organization",
                                              "hunter_email_finder", "linkedin_aiark_person"])
    assert modes == {"apollo_match_person": "org", "apollo_enrich_organization": "org",
                     "hunter_email_finder": "platform", "linkedin_aiark_person": "platform"}
    assert vus == ["apollo", "hunter", "aiark"]


def test_reglage_absent_ou_pose(monkeypatch):
    from oto_mcp import access

    class Rc:
        fields = {"steps": "Lusha, hunter;apollo"}

    def absent(*a, **k):
        raise McpError(ErrorData(code=INVALID_PARAMS, message="No credential"))
    monkeypatch.setattr(access, "resolve_credential", absent)
    assert waterfall._configured_steps("waterfall_email") is None
    monkeypatch.setattr(access, "resolve_credential", lambda *a, **k: Rc())
    assert waterfall._configured_steps("waterfall_email") == ["lusha", "hunter", "apollo"]


@pytest.mark.parametrize("ns", NAMESPACES)
def test_une_carte_de_reglages_se_pose_et_se_relit_par_le_coffre(ns):
    """Le front pose ce réglage par la MÊME route qu'une clé (champ unique : `api_key`
    côté org, le nom du champ côté membre). Ce qui revient du coffre doit être
    `{"steps": …}`, sans espace (copier-coller), sinon la cascade lirait le défaut."""
    from oto_mcp import credentials_store
    c = providers.REGISTRY[ns]
    assert c.secret_kind == "settings" and c.auth_method == "secret"
    kept = credentials_store.validate_fields(ns, {"steps": "lemlist, hunter , apollo"})
    stored = credentials_store.pack_secret(ns, kept)
    assert credentials_store.unpack_secret(ns, stored) == {"steps": "lemlist,hunter,apollo"}
    assert waterfall._parse_steps("lemlist,hunter,apollo") == ["lemlist", "hunter", "apollo"]


# --- le plan, pour un écran (`me.waterfall_plan`) ---------------------------------

def test_le_plan_dit_l_ordre_les_cles_et_tout_ce_qui_est_offert(monkeypatch):
    monkeypatch.setattr(waterfall, "_configured_steps", lambda ns: ["lusha", "nope", "hunter"])
    monkeypatch.setattr(waterfall, "_key_modes", lambda sub, tools: {
        "lusha_search_and_enrich": "org", "hunter_email_finder": "forbidden"})
    out = waterfall.plan("waterfall_email", "sub-test")
    assert out["order_from"] == "setting" and out["ignored_steps"] == ["nope"]
    assert [(s["key"], s["key_mode"], s["usable"]) for s in out["steps"]] == [
        ("lusha", "org", True), ("hunter", "forbidden", False)]
    assert [s["key"] for s in out["available"]] == [s.key for s in waterfall.EMAIL_STEPS]
    first = out["available"][0]
    assert first["connector"] == "hunter" and first["needs"] == "first_name+last_name+domain"
    assert first["requires"] == [["first_name", "last_name", "domain"]]
    labels = [s["label"] for s in waterfall.plan("waterfall_company", "sub-test")["available"]]
    assert len(labels) == len(set(labels)), labels
    assert "key_mode" not in first
    aiark = next(s for s in out["available"] if s["key"] == "aiark")
    assert aiark["connector"] == "aiark"


def test_le_plan_est_servi_par_une_capacite_rest_qui_refuse_un_autre_connecteur():
    import oto_mcp.capabilities  # noqa: F401 — les déclarations
    from oto_mcp.capabilities.connectors import waterfall_plan as cap
    from oto_mcp.capabilities._types import AuthzDenied
    from oto_mcp.capabilities.registry import CAPABILITIES
    decl = next(c for c in CAPABILITIES if c.key == "me.waterfall_plan")
    assert decl.rest.path == "/api/me/waterfalls/{name}/plan" and decl.mcp is None
    ctx = type("Ctx", (), {"sub": "sub-test"})()
    with pytest.raises(AuthzDenied):
        asyncio.run(cap._plan(ctx, cap.WaterfallPlanInput(name="hunter")))



def test_rien_trouve_rend_un_recu_complet(calls):
    out = _run("waterfall_phone", linkedin_url="https://linkedin.com/in/jane")
    assert out["found"] is False and out["phone"] is None
    # Apollo's reveal needs the caller's own key (the fixture's keys are Tulina's): skipped.
    assert [a["status"] for a in out["attempts"]] == [
        "skipped" if s.own_key_only else "no_match" for s in waterfall.PHONE_STEPS]


# --- les asynchrones ajoutés : FullEnrich, Dropcontact, révélation Apollo ----------

def test_fullenrich_telephone_soumet_un_slug_puis_releve(calls):
    calls["answers"]["fullenrich_enrich_linkedin"] = IssueCible(ok=True, result={
        "enrichment_id": "fe_1", "submitted_at": "2026-10-19T10:00:00Z"})
    calls["answers"]["fullenrich_result"] = [
        IssueCible(ok=True, result={"done": False, "retry_after_s": 20}),
        IssueCible(ok=True, result={"done": True, "profiles": [
            {"found": True, "phones": ["+33 6 11 22 33 44"], "work_emails": []}]}),
    ]
    out = _run("waterfall_phone", linkedin_url="https://www.linkedin.com/in/Jane-Doe/",
               full_name="Jane Doe", providers=["fullenrich"])
    assert out["provider"] == "fullenrich" and out["phone"]["phone"] == "+33 6 11 22 33 44"
    assert calls["made"][0] == ("fullenrich_enrich_linkedin", {
        "contacts": [{"first_name": "Jane", "last_name": "Doe", "linkedin_slug": "jane-doe"}],
        "enrich_fields": ["contact.phones"]})
    assert calls["made"][1][1] == {"enrichment_id": "fe_1", "submitted_at": "2026-10-19T10:00:00Z"}


def test_dropcontact_email_ecarte_une_adresse_qu_il_dit_invalide(calls):
    calls["answers"]["dropcontact_enrich"] = IssueCible(ok=True, result={"request_id": "dc_1"})
    calls["answers"]["dropcontact_result"] = IssueCible(ok=True, result={"done": True, "profiles": [
        {"email": [{"email": "old@acme.com", "qualification": "invalid@invalid"},
                   {"email": "jane@acme.com", "qualification": "nominative@pro"}]}]})
    out = _run("waterfall_email", full_name="Jane Doe", domain="acme.com",
               providers=["dropcontact"])
    assert out["email"] == {"email": "jane@acme.com", "all": ["jane@acme.com"],
                            "status": "nominative@pro"}
    assert calls["made"][0][1] == {"contacts": [
        {"first_name": "Jane", "last_name": "Doe", "full_name": "Jane Doe", "website": "acme.com"}]}


def test_revelation_apollo_mobile_d_abord_et_sans_correspondance_rien_n_est_releve(calls):
    calls["modes"] = {"apollo_reveal_phone": "org"}    # own key only
    calls["answers"]["apollo_reveal_phone"] = IssueCible(ok=True, result={"request_id": 123})
    calls["answers"]["apollo_reveal_phone_result"] = IssueCible(ok=True, result={
        "done": True, "result": {"webhook_result": {"people": [{"phone_numbers": [
            {"sanitized_number": "+14155550100", "type_cd": "work_direct"},
            {"sanitized_number": "+14155550199", "type_cd": "mobile"}]}]}}})
    out = _run("waterfall_phone", linkedin_url="https://linkedin.com/in/jane", providers=["apollo"])
    assert out["phone"]["phone"] == "+14155550199"
    assert calls["made"][1] == ("apollo_reveal_phone_result", {"request_id": "123"})

    calls["made"].clear()
    calls["answers"]["apollo_reveal_phone"] = IssueCible(ok=True, result={"matched": False})
    out = _run("waterfall_phone", linkedin_url="https://linkedin.com/in/jane", providers=["apollo"])
    assert out["attempts"][0]["status"] == "no_match"
    assert [n for n, _ in calls["made"]] == ["apollo_reveal_phone"]


def test_revelation_apollo_non_livree_n_est_pas_un_trouve(calls):
    calls["modes"] = {"apollo_reveal_phone": "org"}    # own key only
    calls["answers"]["apollo_reveal_phone"] = IssueCible(ok=True, result={"request_id": "9"})
    calls["answers"]["apollo_reveal_phone_result"] = IssueCible(ok=True, result={
        "done": True, "delivered": False, "reason": "out_of_credits"})
    out = _run("waterfall_phone", linkedin_url="https://linkedin.com/in/jane", providers=["apollo"])
    assert out["found"] is False



# --- la page LinkedIn d'une société : étapes de COMPLÉTION ------------------------

def test_la_page_linkedin_complete_une_societe_identifiee_sans_elle(calls):
    calls["answers"]["apollo_enrich_organization"] = IssueCible(ok=True, result={
        "organization": {"name": "Acme", "primary_domain": "acme.com"}})
    calls["answers"]["apify_run_sync"] = IssueCible(ok=True, result=[
        {"domain": "acme.com", "linkedIns": ["https://www.linkedin.com/in/jane-doe",
                                             "https://fr.linkedin.com/company/Acme-SAS/about"]}])
    out = _run("waterfall_company", domain="acme.com", providers=["apollo", "linkedin"])
    assert out["provider"] == "apollo"
    assert out["company"]["name"] == "Acme"
    assert out["company"]["linkedin_url"] == "https://www.linkedin.com/company/acme-sas/"
    assert out["company"]["linkedin_source"] == "website"
    assert calls["made"][1][1]["actor_id"] == "vdrmota/contact-info-scraper"
    assert calls["made"][1][1]["timeout_secs"] == waterfall._APIFY_TIMEOUT_S


def test_la_completion_ne_tourne_pas_quand_la_page_est_deja_la(calls):
    calls["answers"]["apollo_enrich_organization"] = IssueCible(ok=True, result={
        "organization": {"name": "Acme", "primary_domain": "acme.com",
                         "linkedin_url": "https://www.linkedin.com/company/acme"}})
    out = _run("waterfall_company", domain="acme.com", providers=["apollo", "linkedin"])
    assert _made(calls) == ["apollo_enrich_organization"]
    assert [a["provider"] for a in out["attempts"]] == ["apollo"]


def test_la_recherche_par_nom_ecarte_l_homonyme(calls):
    calls["answers"]["apify_run_sync"] = IssueCible(ok=True, result=[
        {"name": "FlyingBear Restaurant", "linkedinUrl": "https://www.linkedin.com/company/flyingbear-food",
         "website": "https://flyingbear-food.com"},
        {"name": "FlyingBear", "linkedinUrl": "https://www.linkedin.com/company/flyingbear3d",
         "website": "https://www.flyingbear3d.com", "employeeCount": 42}])
    out = _run("waterfall_company", company_name="FlyingBear", domain="flyingbear3d.com",
               providers=["linkedin"])
    # Le site n'a rien donné (même réponse servie : un homonyme écarté), la recherche par
    # nom a trouvé — et, rien d'autre n'ayant identifié la société, la page suffit.
    assert out["found"] is True and out["provider"] == "linkedin"
    assert out["attempts"][0]["via"] == "linkedin_search"
    assert [t["via"] for t in out["attempts"][0]["tried"]] == ["linkedin_site", "linkedin_search"]
    assert out["company"]["linkedin_url"] == "https://www.linkedin.com/company/flyingbear3d/"
    assert out["company"]["employees"] == 42


def test_une_execution_apify_coupee_rend_encore_ses_elements(calls):
    calls["answers"]["apify_run_sync"] = IssueCible(ok=True, result={
        "error": "TIMED-OUT", "run_id": "r1", "status": "TIMED-OUT",
        "items": [{"linkedIns": ["https://linkedin.com/company/acme"]}]})
    out = _run("waterfall_company", domain="acme.com", providers=["linkedin"])
    assert out["company"]["linkedin_url"] == "https://www.linkedin.com/company/acme/"


# --- les gens d'une société (`waterfall_people`) ----------------------------------

def test_people_apollo_garde_les_roles_et_l_id_de_revelation(calls):
    calls["answers"]["apollo_search_people"] = IssueCible(ok=True, result={"people": [
        {"id": "a1", "first_name": "Jane", "last_name_obfuscated": "Do***e", "title": "VP Sales"},
        {"id": "a2", "first_name": "Bob", "last_name_obfuscated": "Sm***h", "title": "Engineer"}]})
    out = _run("waterfall_people", domain="acme.com", roles="sales, CEO", country="France")
    assert out["provider"] == "apollo" and out["people"]["count"] == 1
    assert out["people"]["people"][0] == {"full_name": "Jane Do***e", "title": "VP Sales",
                                          "apollo_id": "a1", "name_obfuscated": True}
    assert calls["made"][0][1] == {"domains": ["acme.com"], "titles": ["sales", "CEO"],
                                   "person_locations": ["France"], "per_page": 20}


def test_people_employes_linkedin_ecarte_ceux_d_ailleurs(calls):
    calls["answers"]["apify_run_sync"] = IssueCible(ok=True, result=[
        {"firstName": "Li", "lastName": "Wei", "linkedinUrl": "https://linkedin.com/in/liwei",
         "currentPosition": [{"companyUniversalName": "kingroon", "position": "VP of B2B BU"}]},
        {"firstName": "Fake", "lastName": "Profile", "linkedinUrl": "https://linkedin.com/in/fake",
         "currentPosition": [{"companyUniversalName": "someone-else", "position": "CEO"}]}])
    out = _run("waterfall_people", company_linkedin_url="https://www.linkedin.com/company/kingroon/",
               providers=["linkedin_employees"])
    assert [p["full_name"] for p in out["people"]["people"]] == ["Li Wei"]
    assert out["people"]["people"][0]["title"] == "VP of B2B BU"
    assert calls["made"][0][1]["run_input"]["companies"] == ["https://www.linkedin.com/company/kingroon/"]


def test_people_google_lit_le_titre_et_exige_la_societe(calls):
    calls["answers"]["serper_search"] = IssueCible(ok=True, result={"organic": [
        {"title": "Zhang San - VP of Overseas Business - Haitian Additive | LinkedIn",
         "link": "https://cn.linkedin.com/in/zhangsan?trk=x", "snippet": ""},
        {"title": "John Roe - Director - Other Corp | LinkedIn",
         "link": "https://linkedin.com/in/johnroe", "snippet": ""}]})
    out = _run("waterfall_people", company_name="Haitian Additive", providers=["google"])
    assert out["people"]["people"] == [{"full_name": "Zhang San", "title": "VP of Overseas Business",
                                        "linkedin_url": "https://cn.linkedin.com/in/zhangsan"}]
    assert calls["made"][0][1]["site_filter"] == "linkedin.com/in"


def test_people_page_linkedin_malformee_refusee(calls):
    with pytest.raises(McpError, match="LinkedIn company page"):
        _run("waterfall_people", company_linkedin_url="https://example.com/acme")



def test_chaque_etape_dit_son_prix_en_credits_tulina():
    """Toute étape payante dit l'outil que Tulina facture sur SA clé (la grille publiée
    donne le chiffre) ; une étape gratuite n'a pas de prix ; aucun prix fournisseur."""
    for ns, steps in waterfall.STEPS_BY_NAMESPACE.items():
        for s in steps:
            row = waterfall._step_row(s, ns=ns)
            if s.free:
                assert row["price"] is None, (ns, s.key)
            else:
                assert set(row["price"]) == {"rate"} and row["price"]["rate"]["tool"], (ns, s.key)
    assert waterfall._step_row(next(s for s in waterfall.PHONE_STEPS if s.key == "fullenrich"),
                               ns="waterfall_phone")["price"]["rate"] == {
        "tool": "fullenrich_result", "pick": "phones"}
    assert set(waterfall.PRICES) <= {(ns, s.key) for ns, st in waterfall.STEPS_BY_NAMESPACE.items()
                                     for s in st}



def test_la_recherche_apollo_dit_le_prix_de_la_revelation():
    row = waterfall._step_row(waterfall.PEOPLE_STEPS[0], ns="waterfall_people")
    assert row["key"] == "apollo"
    assert row["price"]["rate"] == {"tool": "apollo_search_people", "reveal": "apollo_match_person"}



# --- ordre par défaut : crédits × résultats ---------------------------------------

def test_les_etapes_cheres_de_tulina_passent_apres_les_moins_cheres():
    assert [s.key for s in waterfall.EMAIL_STEPS][-2:] == ["fullenrich", "forager"]
    phone = [s.key for s in waterfall.PHONE_STEPS]
    assert phone.index("lusha") < phone.index("forager") < phone.index("fullenrich")
    assert phone.index("apollo") < phone.index("forager")


def test_des_noms_masques_ne_stoppent_pas_la_cascade_mais_restent_le_repli(calls):
    calls["answers"]["apollo_search_people"] = IssueCible(ok=True, result={"people": [
        {"id": "a1", "first_name": "Jane", "last_name_obfuscated": "Do***e", "title": "VP Sales"}]})
    calls["answers"]["serper_search"] = IssueCible(ok=True, result={"organic": [
        {"title": "Jane Doe - VP Sales - Acme | LinkedIn",
         "link": "https://linkedin.com/in/janedoe", "snippet": ""}]})
    out = _run("waterfall_people", domain="acme.com", company_name="Acme",
               providers=["apollo", "google"])
    assert out["provider"] == "google"
    assert out["people"]["people"][0]["full_name"] == "Jane Doe"
    assert [a["status"] for a in out["attempts"]] == ["weak", "found"]

    calls["made"].clear()
    calls["answers"]["serper_search"] = IssueCible(ok=True, result={"organic": []})
    out = _run("waterfall_people", domain="acme.com", company_name="Acme",
               providers=["apollo", "google"])
    # Rien de mieux : les noms masqués d'Apollo sont la réponse.
    assert out["found"] is True and out["provider"] == "apollo"
    assert out["people"]["people"][0]["apollo_id"] == "a1"


def test_on_ne_demande_pas_plus_que_ce_que_l_etape_garde(calls):
    _run("waterfall_people", domain="acme.com", providers=["aiark"], limit=7)
    assert calls["made"][0][1]["size"] == 7
    calls["made"].clear()
    _run("waterfall_company", domain="acme.com", providers=["theirstack", "aiark"])
    args = dict(calls["made"])
    assert args["theirstack_companies_search"]["limit"] == 1
    assert args["linkedin_aiark_search"]["size"] == 1


def test_les_plafonds_de_prix_sont_servis():
    row = lambda ns, key: waterfall._step_row(
        next(s for s in waterfall.STEPS_BY_NAMESPACE[ns] if s.key == key), ns=ns)["price"]["rate"]
    assert row("waterfall_people", "linkedin_employees")["typical_usd"] > 0
    assert row("waterfall_company", "linkedin")["typical_usd"] > 0
    assert row("waterfall_company", "aiark")["max_units"] == 3
    assert row("waterfall_people", "aiark")["per_item"] is True



def test_la_revelation_de_telephone_apollo_exige_la_cle_de_l_appelant(calls):
    calls["modes"] = {"apollo_reveal_phone": "platform"}
    out = _run("waterfall_phone", linkedin_url="https://linkedin.com/in/jane", providers=["apollo", "lusha"])
    assert out["attempts"][0]["code"] == "own_key_required"
    assert "apollo_reveal_phone" not in _made(calls)
    calls["modes"] = {"apollo_reveal_phone": "org"}
    calls["made"].clear()
    _run("waterfall_phone", linkedin_url="https://linkedin.com/in/jane", providers=["apollo"])
    assert _made(calls)[0] == "apollo_reveal_phone"



# --- réponses RÉELLES (essai sur des exposants d'un salon, 2026-10-19), anonymisées --------

def test_reel_aiark_export_statut_en_capitales(calls):
    calls["answers"]["linkedin_aiark_person"] = IssueCible(ok=True, result={
        "found": True, "profile": {"full_name": "Jane Doe", "title": "CEO"},
        "email": {"state": "DONE", "output": [
            {"address": "j.d@other.example", "status": "INVALID"},
            {"address": "jane@acme.com", "status": "VALID", "found": True}]}})
    out = _run("waterfall_email", linkedin_url="https://www.linkedin.com/in/jane-doe-1",
               providers=["aiark"])
    assert out["email"]["email"] == "jane@acme.com" and out["email"]["status"] == "VALID"


def test_reel_apollo_sans_email_ne_compte_pas_mais_la_personne_si(calls):
    person = {"person": {"name": "Sam Roe", "title": "Sales Leader", "seniority": "manager",
                         "linkedin_url": "http://www.linkedin.com/in/sam-ro%c3%a7-1",
                         "email": None, "email_status": "unavailable",
                         "organization": {"name": "ACME", "primary_domain": "acme.com"}}}
    calls["answers"]["apollo_match_person"] = IssueCible(ok=True, result=person)
    url = "https://www.linkedin.com/in/sam-roç-1"
    assert _run("waterfall_email", linkedin_url=url, providers=["apollo"])["found"] is False
    out = _run("waterfall_person", linkedin_url=url, providers=["apollo"])
    assert out["person"]["title"] == "Sales Leader"
    assert out["person"]["linkedin_url"] == url        # the caller's, not Apollo's %-encoded one


def test_reel_aiark_societe_par_domaine_avec_url_et_chiffre_d_affaires(calls):
    calls["answers"]["linkedin_aiark_search"] = IssueCible(ok=True, result={"content": [{
        "summary": {"name": "ACME GmbH", "founded_year": 2025, "industry": "it services",
                    "staff": {"total": 4, "range": {"start": 2, "end": 10}}},
        "link": {"website": "http://www.acme.de", "domain": "http://www.acme.de",
                 "linkedin": "https://www.linkedin.com/company/acme-gmbh"},
        "contact": {"phone": {"raw": "+49 1556 0000000", "sanitized": "+4915560000000"}},
        "financial": {"revenue": {"annual": {"amount": "0-100000"}}}}]})
    out = _run("waterfall_company", domain="acme.de", providers=["aiark"])
    c = out["company"]
    assert c["domain"] == "acme.de" and c["employees"] == 4 and c["revenue_range"] == "0-100000"
    assert c["phone"] == "+4915560000000" and c["linkedin_url"].endswith("/acme-gmbh")


def test_reel_google_lit_le_sous_titre_et_ecarte_la_simple_mention(calls):
    calls["answers"]["serper_search"] = IssueCible(ok=True, result={"organic": [
        {"title": "Dr. Ulla Jay – Acme Marking GmbH",
         "subtitle": "Paderborn, Deutschland · Acme Marking GmbH",
         "link": "https://de.linkedin.com/in/ulla-jay-1"},
        {"title": "Phil Bee – TRACEABLE AND ...",
         "subtitle": "Paderborn, Deutschland · CTO - Acme Marking · Acme Marking GmbH",
         "link": "https://de.linkedin.com/in/phil-bee-2"},
        {"title": "Dr Paul Roe - Senior Project Manager", "link": "https://uk.linkedin.com/in/paul-roe",
         "snippet": "... Acme Marking Techniques Deposition PECVD ..."}]})
    out = _run("waterfall_people", company_name="Acme Marking", providers=["google"])
    people = out["people"]["people"]
    # Le CTO d'abord (séniorité), la simple mention dans un extrait écartée.
    assert [p["full_name"] for p in people] == ["Phil Bee", "Dr. Ulla Jay"]
    assert people[0]["title"] == "CTO - Acme Marking" and people[1].get("title") is None


def test_reel_apollo_people_dit_qui_a_un_email(calls):
    calls["answers"]["apollo_search_people"] = IssueCible(ok=True, result={"people": [
        {"id": "a2", "first_name": "Dev", "last_name_obfuscated": "Pi***k", "title": "Software Developer",
         "has_email": True, "has_direct_phone": "Maybe: please request direct dial via people/bulk_match"},
        {"id": "a1", "first_name": "Ulla", "last_name_obfuscated": "Ja***y", "title": "CEO, Co-Founder",
         "has_email": True, "has_direct_phone": "Yes"}]})
    out = _run("waterfall_people", domain="acme.de", providers=["apollo"])
    first = out["people"]["people"][0]
    assert first["apollo_id"] == "a1" and first["has_email"] is True and first["has_direct_phone"] is True


def test_reel_linkedin_non_connecte_est_saute(calls):
    calls["answers"]["linkedin_unipile_profile"] = IssueCible(
        ok=False, code="invalid_input",
        message="Connect your Linkedin account at https://example.test/console/connections "
                "(or via `unipile_connect_start`) before using these tools.")
    out = _run("waterfall_person", linkedin_url="https://linkedin.com/in/jane", providers=["linkedin"])
    assert out["attempts"][0]["status"] == "skipped" and out["attempts"][0]["code"] == "not_connected"


def test_une_execution_apify_est_bornee_par_sa_taille_et_son_delai_sans_plafond_de_cout():
    """Pas de plafond de coût par défaut (décision du 19/10/2026) : ce qui borne une
    exécution, c'est son NOMBRE d'éléments et son DÉLAI — figés ici, sans quoi un appel
    pourrait faire tourner un acteur sans limite."""
    for steps in waterfall.STEPS_BY_NAMESPACE.values():
        for s in steps:
            for v in s.variants or (s,):
                if v.tool != "apify_run_sync":
                    continue
                args = v.build({"domain": "acme.com", "company_name": "Acme",
                                "company_linkedin_url": "https://www.linkedin.com/company/acme/",
                                "limit": 10})
                assert "max_total_charge_usd" not in args, v.key
                assert args["timeout_secs"] == waterfall._APIFY_TIMEOUT_S, v.key
                assert 1 <= args["max_items"] <= waterfall.PEOPLE_MAX, v.key


# --- limites fixées par l'appelant (aucune par défaut, décision du 19/10/2026) ------------

def test_max_paid_steps_saute_les_etapes_payantes_au_dela(calls):
    for t in ("hunter_email_finder", "linkedin_aiark_person", "apollo_match_person"):
        calls["answers"][t] = IssueCible(ok=True, result={})
    out = _run("waterfall_email", first_name="Jane", last_name="Doe", domain="acme.com",
               linkedin_url="https://www.linkedin.com/in/jane-doe", max_paid_steps=2,
               providers=["hunter", "aiark", "apollo"])
    assert _made(calls) == ["hunter_email_finder", "linkedin_aiark_person"]
    assert out["attempts"][2]["status"] == "skipped"
    assert out["attempts"][2]["code"] == "max_paid_steps"


def test_sans_limite_aucun_plafond_apify_et_avec_il_est_transmis(calls):
    calls["answers"]["apify_run_sync"] = IssueCible(ok=True, result={"result": []})
    _run("waterfall_people", company_linkedin_url="https://www.linkedin.com/company/acme/",
         providers=["linkedin_employees"])
    assert "max_total_charge_usd" not in calls["made"][-1][1]
    _run("waterfall_people", company_linkedin_url="https://www.linkedin.com/company/acme/",
         providers=["linkedin_employees"], apify_max_usd=1.0)
    assert calls["made"][-1][1]["max_total_charge_usd"] == 1.0


def test_un_plafond_apify_sous_le_minimum_est_refuse(calls):
    with pytest.raises(Exception, match="0.50"):
        _run("waterfall_people", company_linkedin_url="https://www.linkedin.com/company/acme/",
             apify_max_usd=0.1)
