"""Les tables d'étapes email, téléphone et personne (`waterfall/email.py`,
`telephone.py`, `personne.py`) — ce que chaque fournisseur reçoit et comment sa réponse
est lue ; les étapes réservées à la clé de l'appelant sont sautées AVANT l'appel sur la
clé partagée."""
from oto_mcp.tools.meta import IssueCible
from oto_mcp.waterfall import email, personne

from _waterfall_faux import _made, _run, calls  # noqa: F401 — la fixture des fournisseurs


# --- lemlist : le profil LinkedIn ---------------------------------------------------

def test_lemlist_personne_lit_le_profil_linkedin(calls):
    calls["answers"]["lemlist_enrich"] = IssueCible(ok=True, result={"enrichment_id": "enr_3"})
    calls["answers"]["lemlist_enrich_result"] = IssueCible(ok=True, result={"results": [{
        "done": True, "found": {"linkedin": True},
        "data": {"linkedin": {"firstName": "Jane", "jobTitle": "CTO", "companyName": "Acme",
                              "locationName": "Paris"}}}]})
    out = _run("waterfall_person", linkedin_url="https://linkedin.com/in/jane", providers=["lemlist"])
    assert out["person"]["title"] == "CTO" and out["person"]["company_name"] == "Acme"


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


def test_reel_linkedin_non_connecte_est_saute(calls):
    calls["answers"]["linkedin_unipile_profile"] = IssueCible(
        ok=False, code="invalid_input",
        message="Connect your Linkedin account at https://example.test/console/connections "
                "(or via `unipile_connect_start`) before using these tools.")
    out = _run("waterfall_person", linkedin_url="https://linkedin.com/in/jane", providers=["linkedin"])
    assert out["attempts"][0]["status"] == "skipped" and out["attempts"][0]["code"] == "not_connected"


# --- Forager : la clé partagée ne sert que le téléphone ----------------------------

def test_forager_email_et_personne_exigent_la_cle_de_l_appelant(calls):
    """La clé partagée de Forager ne sert qu'une recherche — les numéros d'une personne
    (`tools/forager.py::PLATFORM_OPS`) : l'email et le profil sont sautés AVANT l'appel
    sur toute autre clé que celle de l'appelant."""
    for steps in (email.EMAIL_STEPS, personne.PERSON_STEPS):
        assert next(s for s in steps if s.key == "forager").own_key_only
    calls["modes"] = {"forager_person": "platform"}
    for ns in ("waterfall_email", "waterfall_person"):
        out = _run(ns, linkedin_url="https://linkedin.com/in/jane", providers=["forager"])
        assert out["attempts"][0]["code"] == "own_key_required"
    assert "forager_person" not in _made(calls)
    calls["modes"] = {"forager_person": "org"}
    _run("waterfall_email", linkedin_url="https://linkedin.com/in/jane", providers=["forager"])
    assert _made(calls) == ["forager_person"]


def test_forager_email_part_sur_la_cle_partagee_quand_l_instance_l_ouvre(calls, monkeypatch):
    """L'instance ouvre `forager_person:work_emails` sur sa clé partagée (`platform.ops`) :
    l'étape email n'est plus « clé de l'appelant seulement » pour cet appel — le plan le
    dit comme l'appel. Le profil (`detail`), non ouvert, reste sauté."""
    from oto_mcp.waterfall import ordre, plan
    calls["forager_ops"] = "forager_person:phone_numbers,forager_person:work_emails"
    calls["modes"] = {"forager_person": "platform"}
    _run("waterfall_email", linkedin_url="https://linkedin.com/in/jane", providers=["forager"])
    assert _made(calls) == ["forager_person"]
    out = _run("waterfall_person", linkedin_url="https://linkedin.com/in/jane",
               providers=["forager"])
    assert out["attempts"][0]["code"] == "own_key_required"
    monkeypatch.setattr(ordre, "_configured_steps", lambda ns: ["forager"])
    (row,) = plan.plan("waterfall_email", "sub-test")["steps"]
    assert row["usable"] is True and "own_key_only" not in row


def test_un_reglage_forager_illisible_garde_l_etape_sur_la_cle_de_l_appelant(calls, monkeypatch):
    from oto_mcp.tools import forager

    def illisible():
        raise ConnectionError("la base ne répond pas")
    monkeypatch.setattr(forager, "platform_ops", illisible)
    calls["modes"] = {"forager_person": "platform"}
    out = _run("waterfall_email", linkedin_url="https://linkedin.com/in/jane",
               providers=["forager"])
    assert out["attempts"][0]["code"] == "own_key_required"
    assert "forager_person" not in _made(calls)
