"""Le moteur des waterfalls (`waterfall/moteur.py`, `ordre.py`, `appel.py`) — verrouille :
chaque étape passe par `meta.executer_cible` (jamais un client fournisseur) ; la cascade
s'arrête au PREMIER trouvé ; une étape sans ses entrées / sans clé / refusée par une garde
/ en échec / sans résultat passe la main en le disant dans `attempts` ; l'ordre vient de
l'appel, sinon du réglage `steps` de la carte, sinon du défaut — et les fournisseurs dont
l'appelant détient SA clé passent devant ; une étape asynchrone soumet puis relève dans un
délai borné, sinon reste `pending` et ARRÊTE la cascade ; `providers` ne nomme que des clés
de la table ; `dry_run` n'appelle rien ; une exécution Apify est bornée."""
import asyncio

import pytest
from fastmcp import FastMCP
from mcp.types import ErrorData, INVALID_PARAMS

from oto_mcp.mcp_errors import McpError
from oto_mcp.tools.meta import IssueCible
from oto_mcp.waterfall import (appel, entrees, etape, fournisseurs, moteur, ordre, plan, societe,
                               telephone)

from _waterfall_faux import _made, _run, calls  # noqa: F401 — la fixture des fournisseurs


# --- cascade ----------------------------------------------------------------------

def test_s_arrete_au_premier_trouve(calls):
    calls["answers"]["hunter_email_finder"] = IssueCible(ok=True, result={"data": {"email": None}})
    calls["answers"]["apollo_match_person"] = IssueCible(
        ok=True, result={"person": {"email": "Jane@Acme.com", "email_status": "verified"}})
    out = _run("waterfall_email", first_name="Jane", last_name="Doe",
               domain="https://www.acme.com/x", linkedin_url="https://www.linkedin.com/in/jane-doe/")
    assert out["found"] is True and out["provider"] == "apollo"
    assert out["email"] == {"email": "jane@acme.com", "all": ["jane@acme.com"], "status": "verified"}
    # AI Ark passes before Apollo: it found nothing here.
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
    assert order[2:] == ["hunter", "aiark", "apollo", "forager", "dropcontact", "lemlist",
                         "fullenrich"]
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


def test_lemlist_pas_fini_a_temps_reste_pending_et_arrete_la_cascade(calls, monkeypatch):
    """Arbitrage du 20/10/2026 : une étape lente « en attente » est payée et sa réponse
    arrive encore — la cascade s'arrête là et rend l'appel de relève ; aucune étape
    suivante n'est payée pour la même recherche."""
    monkeypatch.setattr(appel, "POLL_BUDGET_S", 0)
    calls["answers"]["lemlist_enrich"] = IssueCible(ok=True, result={"enrichment_id": "enr_2"})
    calls["answers"]["lemlist_enrich_result"] = IssueCible(
        ok=True, result={"results": [{"status": "in-progress", "done": False}]})
    calls["answers"]["lusha_search_and_enrich"] = IssueCible(ok=True, result={
        "results": [{"phones": ["+33612345678"]}]})
    out = _run("waterfall_phone", linkedin_url="https://linkedin.com/in/jane",
               providers=["lemlist", "lusha"])
    assert out["found"] is False and out["provider"] is None
    assert out["pending"] == [{"provider": "lemlist", "tool": "lemlist_enrich_result",
                               "arguments": {"enrichment_id": "enr_2"}}]
    assert [a["status"] for a in out["attempts"]] == ["pending"]
    assert "lusha_search_and_enrich" not in _made(calls)


def test_une_etape_en_attente_ne_laisse_pas_tourner_la_completion(calls, monkeypatch):
    """La page LinkedIn d'une société (étape de complétion) est payée aussi : rien ne
    tourne après une étape en attente."""
    monkeypatch.setattr(appel, "POLL_BUDGET_S", 0)
    lente = etape.Step("lente", "lemlist_enrich", (("domain",),), lambda i: {},
                       lambda r, i: None, "test", collect=fournisseurs._LEMLIST_COLLECT)
    calls["answers"]["lemlist_enrich"] = IssueCible(ok=True, result={"enrichment_id": "e"})
    calls["answers"]["lemlist_enrich_result"] = IssueCible(
        ok=True, result={"results": [{"done": False}]})
    pas = (lente,) + tuple(s for s in societe.COMPANY_STEPS if s.fills)
    out = asyncio.run(moteur._run_cascade(FastMCP("t"), "waterfall_company", "company", pas,
                                          {"domain": "acme.com", "company_name": "Acme"},
                                          None, False))
    assert [a["provider"] for a in out["attempts"]] == ["lente"]
    assert "apify_run_sync" not in _made(calls)


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


# --- clés et réglage, contre la vraie couche d'accès (base simulée) ------------------

def test_modes_de_cle_lus_une_fois_par_porteur(monkeypatch):
    from oto_mcp import access
    vus = []

    def mode(sub, carrier):
        vus.append(carrier)
        return {"apollo": "org"}.get(carrier, "platform")
    monkeypatch.setattr(access, "credential_mode_for", mode)
    modes = ordre._key_modes("sub-test", ["apollo_match_person", "apollo_enrich_organization",
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
    assert ordre._configured_steps("waterfall_email") is None
    monkeypatch.setattr(access, "resolve_credential", lambda *a, **k: Rc())
    assert ordre._configured_steps("waterfall_email") == ["lusha", "hunter", "apollo"]


def test_rien_trouve_rend_un_recu_complet(calls):
    out = _run("waterfall_phone", linkedin_url="https://linkedin.com/in/jane")
    assert out["found"] is False and out["phone"] is None
    # Apollo's reveal needs the caller's own key (the fixture's keys are shared): skipped.
    assert [a["status"] for a in out["attempts"]] == [
        "skipped" if s.own_key_only else "no_match" for s in telephone.PHONE_STEPS]


def test_une_execution_apify_est_bornee_par_sa_taille_et_son_delai_sans_plafond_de_cout():
    """Pas de plafond de coût par défaut (décision du 19/10/2026) : ce qui borne une
    exécution, c'est son NOMBRE d'éléments et son DÉLAI — figés ici, sans quoi un appel
    pourrait faire tourner un acteur sans limite."""
    for steps in plan.STEPS_BY_NAMESPACE.values():
        for s in steps:
            for v in s.variants or (s,):
                if v.tool != "apify_run_sync":
                    continue
                args = v.build({"domain": "acme.com", "company_name": "Acme",
                                "company_linkedin_url": "https://www.linkedin.com/company/acme/",
                                "limit": 10})
                assert "max_total_charge_usd" not in args, v.key
                assert args["timeout_secs"] in (fournisseurs._APIFY_TIMEOUT_S,
                                                fournisseurs._APIFY_EMPLOYEES_TIMEOUT_S), v.key
                if v.key == "linkedin_site":
                    # Sans `max_items` (Apify en tirerait un plafond refusé) : borné par
                    # ses pages et sa profondeur.
                    assert "max_items" not in args, v.key
                    ri = args["run_input"]
                    assert ri["maxRequestsPerStartUrl"] <= 4 and ri["maxDepth"] <= 1
                elif v.key == "apify":
                    # Pile technique : facturé par site, UNE URL — un site au plus.
                    assert "max_items" not in args and args["run_input"]["urls"] == ["acme.com"]
                else:
                    assert 1 <= args["max_items"] <= entrees.PEOPLE_MAX, v.key


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


def test_une_execution_apify_trop_longue_pour_le_reste_n_est_pas_lancee(calls, monkeypatch):
    monkeypatch.setattr(moteur, "DEADLINE_S", 10)
    out = _run("waterfall_people", company_linkedin_url="https://www.linkedin.com/company/acme/",
               providers=["linkedin_employees"])
    assert "apify_run_sync" not in _made(calls)
    assert out["attempts"][0]["code"] == "time_budget"
