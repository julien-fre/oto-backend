"""Le gate de consentement d'achat (#487) — `subscribe` refuse sans acceptation.

Ce que le lot rend vrai, et que rien ne garantissait :

1. **on ne vend pas sans consentement** — `subscribe` refuse (409 `legal_required`)
   tant que CGU + CGV + DPA ne sont pas acceptés à leur version COURANTE, et le
   refus NOMME les documents, leurs versions et leurs adresses ;
2. **un bump de version rouvre le gate** — une acceptation périmée ne vaut pas ;
3. **les DEUX préalables partent ensemble** — identité de facturation (#486) ET
   consentement, en un seul aller-retour, pour que le tunnel n'ait pas à les
   découvrir un par un ;
4. **rien ne part chez le PSP avant** — un refus après création laisserait un
   customer et une page payable derrière lui ;
5. **le chemin heureux reste heureux** — identité + acceptation ⟹ checkout.

Mollie et le store sont simulés, comme dans toute la famille `test_billing_*` ; le
gate, lui, est exercé POUR DE VRAI (`legal_docs` lit `db.get_legal_acceptances`,
seul point stubbé).
"""
from __future__ import annotations

import pytest

from oto_mcp import billing, billing_consent, db, legal_docs
from oto_mcp.capabilities import billing as cap_billing
from oto_mcp.capabilities._types import AuthzDenied, ResolvedCtx
from oto_mcp.db import billing as db_billing

ORG = 4242
SUB = "u-payeur"
RETURN_URL = "https://dashboard.oto.cx/org/billing"

IDENTITE_FR = {"legal_name": "ACME SAS", "country_code": "FR", "vat_number": None,
               "address_line": "1 rue de la Paix", "postal_code": "13001",
               "city": "Marseille"}

PURCHASE = legal_docs.CONTEXTS["purchase"]          # terms + cgv + dpa


def _acceptations(**versions) -> dict:
    """`{slug: version}` → la forme rendue par `db.get_legal_acceptances`."""
    return {slug: {"version": v, "accepted_at": "2026-08-28 09:00:00"}
            for slug, v in versions.items()}


def _tout_a_jour() -> dict:
    return _acceptations(**{slug: meta["version"]
                            for slug, meta in legal_docs.CURRENT_DOCS.items()})


@pytest.fixture
def scene(monkeypatch):
    """Un PSP et un journal nus. `scene(identity=…, acceptances=…)` arme le décor et
    rend le relevé de ce qui est réellement parti chez Mollie."""
    def _armer(*, identity=IDENTITE_FR, acceptances=None):
        appels: dict = {}

        def paie(amount, **k):
            appels["psp"] = (amount, k)
            return {"id": "tr_1", "status": "open",
                    "_links": {"checkout": {"href": "https://mollie/tr_1"}}}

        monkeypatch.setattr(db, "get_legal_acceptances",
                            lambda sub: dict(acceptances or {}))
        monkeypatch.setattr(db_billing, "get_billing_identity", lambda org: identity)
        monkeypatch.setattr(db_billing, "get_org_subscription", lambda org: None)
        monkeypatch.setattr(db_billing, "pending_initial_payment",
                            lambda org, *, since: None)
        monkeypatch.setattr(db_billing, "last_customer_id_for_org", lambda org: "cst_1")
        monkeypatch.setattr(db_billing, "insert_billing_payment",
                            lambda *a, **k: appels.setdefault("journal", (a, k)) or 1)
        monkeypatch.setattr(billing.mollie_client, "create_first_payment", paie)
        monkeypatch.setattr(billing.mollie_client, "update_payment", lambda p, **k: {})
        return appels
    return _armer


# ══ 1. on ne vend pas sans consentement ══════════════════════════════════════

def test_sans_acceptation_la_souscription_est_refusee_en_nommant_les_trois_documents(scene):
    appels = scene()
    with pytest.raises(billing_consent.PurchaseBlocked) as e:
        billing.subscribe(ORG, "standard", RETURN_URL, sub=SUB)

    assert e.value.code == "legal_required"
    (manque,) = e.value.blockers
    assert [d["slug"] for d in manque["documents"]] == PURCHASE

    # Le refus se suffit à lui-même : chaque document, SA version courante, SON
    # adresse. Un tunnel n'a pas à faire un second appel pour peindre l'écran.
    for doc in manque["documents"]:
        courant = legal_docs.CURRENT_DOCS[doc["slug"]]
        assert (doc["version"], doc["url"], doc["label"]) == (
            courant["version"], courant["url"], courant["label"])
        assert doc["accepted_version"] is None, "jamais accepté, pas périmé"
        # …et la phrase les nomme aussi : le message est ce que lit un humain.
        assert f"{courant['label']} {courant['version']}" in str(e.value)
        assert courant["url"] in str(e.value)

    assert appels == {}, "rien ne doit être parti chez Mollie"


def test_une_acceptation_partielle_ne_nomme_que_ce_qui_manque(scene):
    scene(acceptances=_acceptations(terms=legal_docs.CURRENT_DOCS["terms"]["version"]))
    with pytest.raises(billing_consent.PurchaseBlocked) as e:
        billing.subscribe(ORG, "standard", RETURN_URL, sub=SUB)
    (manque,) = e.value.blockers
    assert [d["slug"] for d in manque["documents"]] == ["cgv", "dpa"]


def test_un_appelant_sans_identite_de_sub_est_refuse(scene):
    """Fail-closed : pas de sub ⟹ aucune acceptation ⟹ tout est dû. Le gate se
    ferme, il ne s'ouvre pas."""
    scene()
    with pytest.raises(billing_consent.PurchaseBlocked) as e:
        billing.subscribe(ORG, "standard", RETURN_URL, sub=None)
    assert e.value.code == "legal_required"


# ══ 2. une version périmée ne vaut pas ═══════════════════════════════════════

def test_une_acceptation_a_une_vieille_version_est_refusee_en_nommant_la_courante(scene):
    vieilles = _acceptations(terms="1.0", cgv="1.0", dpa="1.0")
    scene(acceptances=vieilles)
    with pytest.raises(billing_consent.PurchaseBlocked) as e:
        billing.subscribe(ORG, "standard", RETURN_URL, sub=SUB)

    (manque,) = e.value.blockers
    for doc in manque["documents"]:
        assert doc["version"] == legal_docs.CURRENT_DOCS[doc["slug"]]["version"]
        # `accepted_version` distingue « jamais accepté » de « accepté à une version
        # périmée » — sans lui, le payeur est renvoyé chercher une case déjà cochée.
        assert doc["accepted_version"] == "1.0"
    assert "1.0" in str(e.value) and "version antérieure" in str(e.value)


def test_un_bump_de_version_rouvre_un_gate_qui_etait_passe(scene, monkeypatch):
    scene(acceptances=_tout_a_jour())
    billing.subscribe(ORG, "standard", RETURN_URL, sub=SUB)      # passe

    monkeypatch.setitem(legal_docs.CURRENT_DOCS["cgv"], "version", "9.0")
    with pytest.raises(billing_consent.PurchaseBlocked) as e:
        billing.subscribe(ORG, "standard", RETURN_URL, sub=SUB)
    (manque,) = e.value.blockers
    assert [d["slug"] for d in manque["documents"]] == ["cgv"]
    assert manque["documents"][0]["version"] == "9.0"


# ══ 3. les deux préalables, en UN aller-retour ═══════════════════════════════

def test_identite_absente_et_legal_absent_sont_nommes_ENSEMBLE(scene):
    """Le point du lot : le tunnel ne doit pas découvrir les refus un par un."""
    appels = scene(identity=None)
    with pytest.raises(billing_consent.PurchaseBlocked) as e:
        billing.subscribe(ORG, "standard", RETURN_URL, sub=SUB)

    codes = [b["code"] for b in e.value.blockers]
    assert codes == ["billing_identity_required", "legal_required"], (
        "l'ordre est celui du tunnel : on chiffre avant de faire consentir")
    # Le code de TÊTE reste le code historique du premier manque — un client qui ne
    # lit que lui n'a rien perdu de ce qu'il attendait déjà (#486).
    assert e.value.code == "billing_identity_required"
    # …et les deux messages sont là, chacun actionnable.
    assert "address_line" in str(e.value) and "CGV" in str(e.value)
    assert appels == {}


def test_identite_seule_manquante_garde_le_code_et_le_message_du_486(scene):
    scene(identity=None, acceptances=_tout_a_jour())
    with pytest.raises(ValueError) as e:
        billing.subscribe(ORG, "standard", RETURN_URL, sub=SUB)
    assert str(e.value).startswith("billing_identity_required:")
    assert len(e.value.blockers) == 1


def test_un_particulier_de_l_union_et_un_legal_absent_partent_aussi_ensemble(scene):
    scene(identity={**IDENTITE_FR, "country_code": "DE"})
    with pytest.raises(billing_consent.PurchaseBlocked) as e:
        billing.subscribe(ORG, "standard", RETURN_URL, sub=SUB)
    assert [b["code"] for b in e.value.blockers] == [
        "vat_consumer_unsupported", "legal_required"]


# ══ 4. la traduction en refus servi ══════════════════════════════════════════

def _refus(scene_armee, **kw):
    ctx = ResolvedCtx(sub=SUB, org_id=ORG, role="admin")
    inp = cap_billing.SubscribeInput(plan="standard", return_url=RETURN_URL)
    with pytest.raises(AuthzDenied) as e:
        cap_billing._subscribe(ctx, inp)
    return e.value


def test_la_capacite_rend_409_et_porte_les_deux_manques_dans_details(scene):
    scene(identity=None)
    refus = _refus(scene)
    assert refus.status == 409, (
        "l'org n'est pas en état d'être débitée — le corps de l'appel, lui, est bon")
    assert refus.code == "billing_identity_required"
    blockers = refus.details["blockers"]
    assert [b["code"] for b in blockers] == ["billing_identity_required",
                                             "legal_required"]
    assert [d["slug"] for d in blockers[1]["documents"]] == PURCHASE


def test_le_refus_legal_seul_porte_le_code_legal_required(scene):
    scene()
    refus = _refus(scene)
    assert (refus.status, refus.code) == (409, "legal_required")
    assert len(refus.details["blockers"]) == 1


# ══ 5. le chemin heureux ═════════════════════════════════════════════════════

def test_identite_plus_acceptation_ouvrent_le_checkout(scene):
    appels = scene(acceptances=_tout_a_jour())
    out = billing.subscribe(ORG, "standard", RETURN_URL, sub=SUB)

    assert out["checkout_url"] == "https://mollie/tr_1"
    assert appels["psp"][0] == 2280, "19,00 € HT + 20 % — le gate n'a rien changé au TTC"
    assert out["vat_scheme"] == "fr_ttc"


def test_le_renouvellement_ne_redemande_pas_de_consentement(monkeypatch):
    """Une échéance n'est pas une vente : le consentement a été donné à la
    souscription, le runner ne le rejoue pas. `billing_runner._charge_one` ne prend
    d'ailleurs pas de `sub` — c'est la garde structurelle de ce fait."""
    import inspect

    from oto_mcp import billing_runner

    assert "sub" not in inspect.signature(billing_runner._charge_one).parameters


# ══ 6. ce que le tunnel reçoit VRAIMENT, par la route ════════════════════════

def test_la_route_rend_un_409_dont_le_corps_porte_les_deux_manques(scene, monkeypatch):
    """Bout en bout, à travers l'adaptateur REST : c'est là que `AuthzDenied.details`
    devient (ou non) une clé du corps JSON. Un test au niveau du handler ne verrait
    pas l'enveloppe."""
    from _datastore_rest import call, stub_authz

    scene(identity=None)
    stub_authz(monkeypatch, org_id=ORG, role="admin")
    monkeypatch.setattr("oto_mcp.roles.is_org_admin", lambda sub, org: True)

    code, corps = call("billing.subscribe",
                       body={"plan": "standard", "return_url": RETURN_URL})
    assert code == 409
    assert corps["error"] == "billing_identity_required"
    blockers = corps["details"]["blockers"]
    assert [b["code"] for b in blockers] == ["billing_identity_required",
                                             "legal_required"]
    documents = blockers[1]["documents"]
    assert [d["slug"] for d in documents] == PURCHASE
    assert all(d["url"].startswith("http") and d["version"] for d in documents)


def test_un_refus_sans_details_garde_l_enveloppe_d_avant(scene, monkeypatch):
    """Additif : les refus qui ne posent pas `details` rendent exactement le corps
    d'avant — la clé n'apparaît pas, elle ne vaut pas `null`."""
    from _datastore_rest import call, stub_authz

    scene(acceptances=_tout_a_jour())
    stub_authz(monkeypatch, org_id=ORG, role="admin")
    monkeypatch.setattr("oto_mcp.roles.is_org_admin", lambda sub, org: True)
    monkeypatch.setattr(db_billing, "get_org_subscription",
                        lambda org: {"status": "active", "canceled_at": None,
                                     "customer_id": "cst_1"})

    code, corps = call("billing.subscribe",
                       body={"plan": "standard", "return_url": RETURN_URL})
    assert (code, corps["error"]) == (409, "already_subscribed")
    assert "details" not in corps
