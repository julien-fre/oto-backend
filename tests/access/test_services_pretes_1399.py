"""Un prêt de compte Google ou Microsoft prête une LISTE de services (signal 1399).

La liste est retenue à la pose (le service nommé, une liste donnée, sinon ce que le
compte a autorisé ce jour-là), exigée à l'usage par le résolveur unique, et un service
autorisé plus tard n'est jamais prêté implicitement. Toutes les valeurs sont fictives.
"""
from types import SimpleNamespace

import pytest

from oto_mcp import access, credentials_store
from oto_mcp.auth import google as google_auth
from oto_mcp.capabilities._types import AuthzDenied, ResolvedCtx
from oto_mcp.capabilities.connectors import sharing
from oto_mcp.mcp_errors import McpError

ORG, PRETEUSE, PAIR = 7, "preteuse", "pair"
CTX = ResolvedCtx(sub=PRETEUSE, org_id=ORG)
COMPTE = "preteuse@exemple.test"


def _scopes(*services):
    return " ".join(list(google_auth.IDENTITY_SCOPES)
                    + [sc for svc in services for sc in google_auth.SERVICE_SCOPES[svc]])


@pytest.fixture
def coffre(monkeypatch):
    """La ligne Google de la prêteuse : son partage, ses services prêtés, ses scopes."""
    etat = {"side": [], "services": {}, "journal": [],
            "scopes": _scopes("gmail", "google_ads")}
    monkeypatch.setattr(sharing.access, "current_org", lambda sub: ORG)
    monkeypatch.setattr(sharing.db, "get_user", lambda sub: {"sub": sub})
    monkeypatch.setattr(credentials_store, "list_accounts", lambda et, eid, con: [
        {"account": COMPTE, "meta": {"scopes": etat["scopes"]}}])
    monkeypatch.setattr(credentials_store, "get_instance_sharing",
                        lambda *a, **k: ([], list(etat["side"])))
    monkeypatch.setattr(credentials_store, "services_pretes",
                        lambda *a, **k: {c: list(v) for c, v in etat["services"].items()})

    def ecrire(et, eid, con, acc="", *, share_side=None, share_services=None,
               journal=(), **k):
        etat["side"] = list(share_side)
        if share_services is not None:
            etat["services"] = {c: list(v) for c, v in share_services.items()}
        etat["journal"] += [(g["target"], g["action"]) for g in journal]
        return True
    monkeypatch.setattr(credentials_store, "set_instance_sharing", ecrire)
    return etat


def _preter(connector="google", **kw):
    return sharing._lend_instance(CTX, sharing.LendInstanceInput(
        connector=connector, to=PAIR, **kw))


# ── à la pose ────────────────────────────────────────────────────────────────

def test_preter_la_carte_google_ads_ne_prete_que_google_ads(coffre):
    out = _preter("google_ads")
    assert (out["connector"], out["services"]) == ("google", ["google_ads"])
    assert coffre["services"] == {f"user:{PAIR}": ["google_ads"]}
    assert "google_ads only" in out["note"]


def test_preter_le_compte_fige_les_services_autorises_ce_jour(coffre):
    out = _preter()
    assert out["services"] == ["gmail", "google_ads"]
    coffre["scopes"] = _scopes("gmail", "google_ads", "drive")   # autorisé ensuite
    assert coffre["services"][f"user:{PAIR}"] == ["gmail", "google_ads"], (
        "un service autorisé après la pose n'entre pas dans le prêt")


def test_etendre_puis_reduire_remplace_la_liste_et_se_journalise(coffre):
    _preter("google_ads")
    coffre["scopes"] = _scopes("gmail", "google_ads", "drive")
    assert _preter(services=["drive", "google_ads"])["services"] == ["drive", "google_ads"]
    assert _preter(services=["drive"])["services"] == ["drive"]
    _preter(services=["drive"])                       # répété : rien ne change
    assert coffre["journal"] == [(f"user:{PAIR}", "lend")] * 3


def test_reprendre_le_pret_retire_sa_liste(coffre):
    _preter("google_ads")
    out = _preter(revoke=True)
    assert (coffre["side"], coffre["services"], out["services"]) == ([], {}, [])


@pytest.mark.parametrize("services,code", [
    (["calendar"], "service_not_authorized"),        # pas autorisé par le compte
    (["slack"], "unknown_service"),                   # pas un service Google
    ([], "no_service_to_lend"),
])
def test_une_liste_impossible_est_refusee_et_rien_n_est_prete(coffre, services, code):
    with pytest.raises(AuthzDenied) as refus:
        _preter(services=services)
    assert refus.value.code == code
    assert coffre["side"] == [] and coffre["services"] == {}


def test_une_cle_sans_services_se_prete_entiere(coffre, monkeypatch):
    monkeypatch.setattr(credentials_store, "list_accounts",
                        lambda et, eid, con: [{"account": "", "meta": {}}])
    with pytest.raises(AuthzDenied) as refus:
        _preter("zoho", services=["gmail"])
    assert refus.value.code == "services_not_applicable"
    assert _preter("zoho")["services"] is None


# ── l'état du prêt, lu par l'écran ───────────────────────────────────────────

def test_la_pose_et_la_lecture_rendent_le_meme_etat(coffre):
    out = _preter("google_ads")
    assert out["services_by_borrower"] == {f"user:{PAIR}": ["google_ads"]}
    assert out["services_lendable"] == ["gmail", "google_ads"]
    assert out["service_labels"]["google_ads"] == "Google Ads"
    lu = sharing._get_loan_state(CTX, sharing.LoanStateInput(connector="gmail"))
    assert lu == {k: out[k] for k in sharing.LoanState.model_fields}
    assert lu["connector"] == "google" and lu["lent_to"] == [PAIR]


def test_la_lecture_d_une_cible_sans_liste_dit_aucun_service(coffre):
    coffre["side"] = ["group:3"]
    lu = sharing._get_loan_state(CTX, sharing.LoanStateInput(connector="google"))
    assert (lu["lent_to_groups"], lu["services_by_borrower"]) == ([3], {"group:3": []})


def test_la_lecture_d_une_cle_entiere_ne_dit_pas_de_services(coffre, monkeypatch):
    monkeypatch.setattr(credentials_store, "list_accounts",
                        lambda et, eid, con: [{"account": "", "meta": {}}])
    coffre["side"] = [f"user:{PAIR}"]
    lu = sharing.LoanState.model_validate(
        sharing._get_loan_state(CTX, sharing.LoanStateInput(connector="zoho")))
    assert lu.lent_to == [PAIR]
    assert lu.services_by_borrower is None and lu.services_lendable is None
    assert lu.service_labels is None


# ── à l'usage ────────────────────────────────────────────────────────────────

def _servi(entity_type, entity_id, account):
    # Une ligne de membre n'est jamais la clé plateforme : le résolveur lit `is_platform`
    # pour noter le porteur que le règlement débite (L8).
    return SimpleNamespace(entity_type=entity_type, entity_id=entity_id, account=account,
                           is_platform=False)


LIGNE = _servi(credentials_store.MEMBER, f"{ORG}:{PRETEUSE}", COMPTE)


@pytest.fixture
def pret(monkeypatch):
    etat = {"side": [f"user:{PAIR}", "group:3"],
            "services": {f"user:{PAIR}": ["google_ads"], "group:3": ["drive"]},
            "equipes": {3}}
    monkeypatch.setattr(credentials_store, "get_instance_sharing",
                        lambda *a, **k: ([], list(etat["side"])))
    monkeypatch.setattr(credentials_store, "services_pretes",
                        lambda *a, **k: dict(etat["services"]))
    from oto_mcp import group_store, roles
    monkeypatch.setattr(group_store, "is_group_member",
                        lambda sub, gid: gid in etat["equipes"])
    monkeypatch.setattr(roles, "is_org_member", lambda sub, org: False)
    monkeypatch.setattr(access, "libelles_preteurs",
                        lambda subs: {s: f"{s}@exemple.test" for s in subs})
    return etat


def test_un_service_prete_sert_par_la_personne_ou_par_son_equipe(pret):
    access.exiger_service_prete(PAIR, "google_ads", LIGNE)
    access.exiger_service_prete(PAIR, "drive", LIGNE)


def test_un_service_non_prete_est_refuse_en_nommant_le_pret(pret):
    with pytest.raises(McpError) as refus:
        access.exiger_service_prete(PAIR, "gmail", LIGNE)
    data = refus.value.error.data
    assert (data["code"], data["services_lent"], data["lent_by"]) == (
        "service_not_lent", ["drive", "google_ads"], f"{PRETEUSE}@exemple.test")
    assert "Nothing was done" in refus.value.error.message
    assert "card in the console" in refus.value.error.message


def test_une_cible_sans_liste_ne_prete_aucun_service(pret):
    pret["services"] = {}
    with pytest.raises(McpError) as refus:
        access.exiger_service_prete(PAIR, "google_ads", LIGNE)
    assert refus.value.error.data["services_lent"] == []


@pytest.mark.parametrize("sub,service,ligne", [
    (PRETEUSE, "gmail", LIGNE),                               # sa propre ligne
    (PAIR, "gmail", _servi("group", "3", COMPTE)),            # compte de l'équipe elle-même
    (PAIR, "google", LIGNE),                                  # le compte lui-même
    (PAIR, "hunter", object()),                               # clé entière : rien n'est lu
])
def test_ce_qui_n_est_pas_un_service_prete_n_est_pas_filtre(pret, sub, service, ligne):
    access.exiger_service_prete(sub, service, ligne)


def test_le_resolveur_unique_exige_le_service_prete(pret, monkeypatch):
    servie = LIGNE
    monkeypatch.setattr(access, "_resolve_credential_impl", lambda *a, **k: servie)
    monkeypatch.setattr(access, "_emit_connector_failure", lambda *a: None)
    monkeypatch.setattr(access, "_note_resolved_instance", lambda rc: rc)
    assert access.resolve_credential("google_ads", sub=PAIR) is servie
    with pytest.raises(McpError):
        access.resolve_credential("gmail", sub=PAIR)
