"""La TVA n'était JAMAIS ajoutée au montant débité (#486) — la règle, et ses refus.

Le prix d'un palier est un HT ; jusqu'au 28/08/2026 c'était ce HT qui partait au
PSP. Un client « à 19 € » était débité de 19,00 € alors que la TVA française de
20 % est due par Otomata quoi qu'il arrive : l'encaissement réel ne permettait pas
d'émettre une facture correcte.

Ce fichier fige les quatre choses que le lot rend vraies :

1. **la règle par pays**, branche par branche (le module `billing_vat` est PUR :
   aucune base, aucun réseau, aucune horloge — chaque cas se teste en une ligne) ;
2. **l'ordre imposé** — `subscribe` refuse tant que l'identité n'est pas là, et le
   refus NOMME les champs manquants ;
3. **ce qui part vraiment au PSP** — le TTC, à la souscription comme à l'échéance,
   par le MÊME seam ;
4. **ce qu'on ne réécrit pas** — les encaissements d'avant la règle restent tels
   quels, et un `amount_ht` nul les distingue.

Mollie et le store sont simulés, comme dans toute la famille `test_billing_*`.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from oto_mcp import billing, billing_runner, billing_vat
from oto_mcp.db import billing as db_billing

ORG = 4242
RETURN_URL = "https://dashboard.oto.cx/org/billing"
HT = billing.PLANS["standard"]["amount"]          # 1900 c = 19,00 € HT

# Consentement d'achat par défaut : depuis #487, `subscribe` REFUSE tant que
# l'appelant n'a pas accepté CGU + CGV + DPA à leur version courante. Une org qui
# souscrit l'a donc forcément fait, et le câbler ici garde ces tests sur LEUR sujet.
# Le gate lui-même est exercé par `test_billing_legal_gate_487.py`.
SUB = "u-payeur"


def _tout_accepte(monkeypatch):
    from oto_mcp import db as oto_db, legal_docs
    monkeypatch.setattr(oto_db, "get_legal_acceptances", lambda sub: {
        slug: {"version": meta["version"], "accepted_at": "2026-08-28 09:00:00"}
        for slug, meta in legal_docs.CURRENT_DOCS.items()})



def _identite(**over) -> dict:
    base = {"legal_name": "ACME SAS", "country_code": "FR", "vat_number": None,
            "address_line": "1 rue de la Paix", "postal_code": "13001",
            "city": "Marseille", "address_line2": None, "billing_email": None}
    base.update(over)
    return base


# ══ 1. la règle, pays par pays ═══════════════════════════════════════════════

def test_la_france_paie_la_tva_a_20_pour_cent():
    tax = billing_vat.tax_for(1900, "FR", None)
    assert (tax["vat_scheme"], tax["vat_rate_bps"]) == ("fr_ttc", 2000)
    assert (tax["amount_ht"], tax["vat_amount"], tax["amount_ttc"]) == (1900, 380, 2280)
    # Pas de mention à porter : une facture AVEC TVA n'a rien à justifier. C'est
    # l'exonération qui doit se motiver, jamais l'imposition.
    assert tax["vat_mention"] is None


def test_un_assujetti_de_l_union_autoliquide_a_zero():
    tax = billing_vat.tax_for(1900, "BE", "BE0123456789")
    assert (tax["vat_scheme"], tax["vat_rate_bps"]) == ("reverse_charge", 0)
    assert (tax["vat_amount"], tax["amount_ttc"]) == (0, 1900)
    # La mention n'est pas décorative : sans elle, la facture est irrégulière.
    assert "196" in tax["vat_mention"] and "2006/112/CE" in tax["vat_mention"]


def test_un_particulier_de_l_union_hors_france_est_REFUSE():
    """Le cadre, pas un oubli : sans guichet OSS, il faudrait collecter la TVA du
    pays du client, la déclarer et la reverser. Encaisser en attendant, ce serait
    une TVA due et non collectée — on refuse de souscrire plutôt que de facturer
    faux, et le refus DIT pourquoi."""
    with pytest.raises(ValueError) as e:
        billing_vat.tax_for(1900, "DE", None)
    assert str(e.value).startswith("vat_consumer_unsupported:")
    assert "OSS" in str(e.value), "le refus doit dire CE QUI MANQUE, pas juste refuser"


def test_hors_union_c_est_un_export_a_zero():
    tax = billing_vat.tax_for(1900, "US", None)
    assert (tax["vat_scheme"], tax["vat_rate_bps"]) == ("export", 0)
    assert tax["amount_ttc"] == 1900
    assert "259-1" in tax["vat_mention"]


def test_les_vingt_sept_sont_bien_vingt_sept_et_la_france_en_est():
    # Une liste en dur se vérifie, sinon elle dérive en silence : 27 depuis la
    # sortie du Royaume-Uni (2020-01-31), Croatie comprise (2013-07-01).
    assert len(billing_vat.EU_COUNTRIES) == 27
    assert {"FR", "HR"} <= billing_vat.EU_COUNTRIES
    assert "GB" not in billing_vat.EU_COUNTRIES
    # Chaque membre a un format de numéro : sans lui, un client d'un pays oublié
    # serait refusé pour une raison qui n'a rien à voir avec sa situation.
    assert set(billing_vat.VAT_FORMATS) == billing_vat.EU_COUNTRIES


def test_un_pays_inconnu_est_refuse_pas_traite_en_export():
    """Le piège qui coûte de l'argent en silence : « FR » mal tapé sort de l'Union
    et devient un export à 0 %. Rien ne le signalerait — la facture serait juste
    fausse, du côté qui ne se voit pas."""
    for saisie in ("FT", "France", "", None, "F"):
        with pytest.raises(ValueError, match="country_invalid"):
            billing_vat.tax_for(1900, saisie, None)


# ── l'arrondi ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("ht, ttc", [
    (1900, 2280),      # 19,00 € → 22,80 €   (les quatre paliers du catalogue)
    (4900, 5880),      # 49,00 € → 58,80 €
    (24900, 29880),    # 249,00 € → 298,80 €
    (49900, 59880),    # 499,00 € → 598,80 €
])
def test_les_paliers_du_catalogue_donnent_des_ttc_exacts(ht, ttc):
    assert billing_vat.tax_for(ht, "FR", None)["amount_ttc"] == ttc


@pytest.mark.parametrize("ht, tva", [(1, 0), (3, 1), (2, 0), (7, 1), (13, 3)])
def test_la_tva_s_arrondit_au_centime_demi_vers_le_haut(ht, tva):
    """En flottant, `24900 * 0.2` vaut 4979.999999999999 et `int()` perdrait un
    centime par facture — invisible jusqu'au rapprochement comptable. Le calcul est
    donc en Decimal, et l'arrondi est celui de l'usage : demi vers le haut."""
    assert billing_vat.vat_amount(ht, 2000) == tva
    assert billing_vat.vat_amount(24900, 2000) == 4980   # pas 4979


# ── la FORME du numéro de TVA ────────────────────────────────────────────────

@pytest.mark.parametrize("pays, numero", [
    ("FR", "FR12345678901"), ("BE", "BE0123456789"), ("DE", "DE123456789"),
    ("NL", "NL123456789B01"), ("LU", "LU12345678"), ("ES", "ESA1234567Z"),
    ("IT", "IT12345678901"),
])
def test_les_formats_nationaux_attendus_passent(pays, numero):
    assert billing_vat.check_vat_number(pays, numero) == numero


# Un numéro TYPE par État membre, à la forme officielle (VIES / Commission
# européenne). Le test ci-dessous n'est pas une redite du précédent : celui-là
# vérifiait que chaque pays A un format, celui-ci que ce format ACCEPTE un numéro
# valide. Une expression régulière fautive sur un pays que personne n'exerce
# fermerait la souscription à ses clients sans qu'aucun test ne rougisse — et le
# symptôme serait « votre numéro de TVA est invalide » sur un numéro correct.
_NUMEROS_TYPES = {
    "AT": "ATU12345678", "BE": "BE0123456789", "BG": "BG123456789",
    "CY": "CY12345678L", "CZ": "CZ12345678", "DE": "DE123456789",
    "DK": "DK12345678", "EE": "EE123456789", "ES": "ESA1234567Z",
    "FI": "FI12345678", "FR": "FR12345678901", "GR": "EL123456789",
    "HR": "HR12345678901", "HU": "HU12345678", "IE": "IE1234567FA",
    "IT": "IT12345678901", "LT": "LT123456789", "LU": "LU12345678",
    "LV": "LV12345678901", "MT": "MT12345678", "NL": "NL123456789B01",
    "PL": "PL1234567890", "PT": "PT123456789", "RO": "RO1234567890",
    "SE": "SE123456789012", "SI": "SI12345678", "SK": "SK1234567890",
}


@pytest.mark.parametrize("pays, numero", sorted(_NUMEROS_TYPES.items()))
def test_chacun_des_vingt_sept_accepte_un_numero_de_sa_forme(pays, numero):
    assert billing_vat.check_vat_number(pays, numero) == numero
    # …et le client passe bien en autoliquidation, pas seulement le contrôle.
    assert billing_vat.tax_for(1900, pays, numero)["vat_scheme"] == (
        "fr_ttc" if pays == "FR" else "reverse_charge")


def test_la_table_des_numeros_types_couvre_exactement_l_union():
    """Sinon le test paramétré ci-dessus laisserait un pays hors du filet en
    silence, ce qui est précisément le mode d'échec qu'il vise."""
    assert set(_NUMEROS_TYPES) == billing_vat.EU_COUNTRIES


@pytest.mark.parametrize("numero", ["IE1234567A", "IE1A23456B", "IE1234567FA"])
def test_l_irlande_a_trois_formes_legales_et_les_trois_passent(numero):
    """Le seul pays des 27 dont le format officiel est une alternance — la source
    fréquente d'un refus à tort sur un numéro parfaitement valide."""
    assert billing_vat.check_vat_number("IE", numero) == numero


def test_le_numero_est_normalise_avant_d_etre_controle():
    """Un numéro se recopie d'un PDF : espaces, points et tirets arrivent avec. Les
    refuser serait refuser une saisie correcte."""
    assert billing_vat.check_vat_number("FR", " fr 123 456.789-01 ") == "FR12345678901"
    assert billing_vat.normalize_vat_number("") is None      # vide ≠ chaîne vide
    assert billing_vat.normalize_vat_number(None) is None


def test_la_grece_est_GR_en_iso_et_EL_en_tva():
    """La seule divergence des 27, et elle est piégeuse : un contrôle naïf
    « le numéro commence par le code pays » refuserait tout numéro grec valide."""
    assert billing_vat.vat_prefix("GR") == "EL"
    assert billing_vat.check_vat_number("GR", "EL123456789") == "EL123456789"
    with pytest.raises(ValueError, match="vat_number_invalid"):
        billing_vat.check_vat_number("GR", "GR123456789")


@pytest.mark.parametrize("pays, numero, motif", [
    ("FR", "BE0123456789", "commence par"),      # numéro d'un AUTRE pays
    ("FR", "FR1234567890", "format inattendu"),  # 10 chiffres au lieu de 9 + clé
    ("DE", "DE12345678", "format inattendu"),    # 8 chiffres au lieu de 9
    ("NL", "NL123456789X01", "format inattendu"),
])
def test_un_numero_mal_forme_est_refuse_en_nommant_la_forme_attendue(pays, numero, motif):
    with pytest.raises(ValueError) as e:
        billing_vat.check_vat_number(pays, numero)
    assert "vat_number_invalid" in str(e.value) and motif in str(e.value)
    # Le message s'adresse à quelqu'un qui recopie un numéro, pas à un développeur :
    # il dit la forme en toutes lettres, jamais une expression régulière.
    assert "\\d" not in str(e.value)


def test_un_numero_intracom_hors_union_n_a_pas_de_sens():
    with pytest.raises(ValueError, match="vat_number_unexpected"):
        billing_vat.check_vat_number("US", "US123456789")


# ══ 2. l'identité : l'ordre imposé ═══════════════════════════════════════════

def test_les_champs_manquants_sont_nommes_un_par_un():
    assert billing_vat.missing_identity_fields(None) == list(
        billing_vat.REQUIRED_IDENTITY_FIELDS)
    partielle = {"legal_name": "ACME", "country_code": "FR", "city": "  "}
    # Un champ rempli d'espaces est un champ vide : le formulaire l'accepterait,
    # la facture non.
    assert billing_vat.missing_identity_fields(partielle) == [
        "address_line", "postal_code", "city"]
    assert billing_vat.missing_identity_fields(_identite()) == []


def test_subscribe_refuse_sans_identite_et_nomme_les_champs(monkeypatch):
    """Le refus arrive AVANT tout appel au PSP : un refus après création laisserait
    un customer et une page payable derrière lui."""
    appels = _wire(monkeypatch, identity=None)
    with pytest.raises(ValueError) as e:
        billing.subscribe(ORG, "standard", RETURN_URL, sub=SUB)
    assert str(e.value).startswith("billing_identity_required:")
    for champ in billing_vat.REQUIRED_IDENTITY_FIELDS:
        assert champ in str(e.value)
    assert appels == {}, "rien ne doit être parti chez Mollie"


def test_subscribe_refuse_un_particulier_de_l_union_hors_france(monkeypatch):
    appels = _wire(monkeypatch, identity=_identite(country_code="DE"))
    with pytest.raises(ValueError, match="vat_consumer_unsupported"):
        billing.subscribe(ORG, "standard", RETURN_URL, sub=SUB)
    assert appels == {}


def test_une_identite_incomplete_refuse_comme_une_identite_absente(monkeypatch):
    # Le point de la liste unique : que la ligne existe ou non, le refus dit la
    # même chose — voilà ce qu'il reste à fournir.
    _wire(monkeypatch, identity={"legal_name": "ACME", "country_code": "FR"})
    with pytest.raises(ValueError) as e:
        billing.subscribe(ORG, "standard", RETURN_URL, sub=SUB)
    assert "address_line, postal_code, city" in str(e.value)


# ══ 3. ce qui part vraiment au PSP ═══════════════════════════════════════════

def _wire(monkeypatch, *, identity, existing=None):
    """Un PSP et un journal nus : on ne regarde ici que le MONTANT."""
    appels: dict = {}
    _tout_accepte(monkeypatch)

    def journalise(*a, **k):
        appels["journal"] = (a, k)
        return 1

    def paie(amount, **k):
        appels["psp"] = (amount, k)
        return {"id": "tr_1", "status": "open",
                "_links": {"checkout": {"href": "https://mollie/tr_1"}}}

    monkeypatch.setattr(db_billing, "get_billing_identity", lambda org: identity)
    monkeypatch.setattr(db_billing, "get_org_subscription", lambda org: existing)
    monkeypatch.setattr(db_billing, "pending_initial_payment",
                        lambda org, *, since: None)
    monkeypatch.setattr(db_billing, "last_customer_id_for_org", lambda org: "cst_1")
    monkeypatch.setattr(db_billing, "insert_billing_payment", journalise)
    monkeypatch.setattr(billing.mollie_client, "create_first_payment", paie)
    monkeypatch.setattr(billing.mollie_client, "update_payment", lambda p, **k: {})
    return appels


def test_le_montant_debite_est_le_ttc_et_la_reponse_le_decompose(monkeypatch):
    appels = _wire(monkeypatch, identity=_identite())
    out = billing.subscribe(ORG, "standard", RETURN_URL, sub=SUB)

    montant, _ = appels["psp"]
    assert montant == 2280, "19,00 € HT + 20 % = 22,80 € — c'est CE montant qui est pris"
    # …et la réponse porte la décomposition, pour que le tunnel puisse l'annoncer
    # AVANT d'envoyer le payeur sur la page hébergée.
    assert (out["amount_ht"], out["vat_amount"], out["amount_ttc"]) == (1900, 380, 2280)
    assert out["vat_scheme"] == "fr_ttc" and out["vat_rate_bps"] == 2000


def test_le_journal_fige_la_decomposition_de_la_tentative(monkeypatch):
    appels = _wire(monkeypatch, identity=_identite())
    billing.subscribe(ORG, "standard", RETURN_URL, sub=SUB)
    args, kw = appels["journal"]
    assert args[2] == 2280, "le journal dit ce que le PSP a pris, donc le TTC"
    assert kw["tax"]["amount_ht"] == 1900 and kw["tax"]["vat_scheme"] == "fr_ttc"
    assert kw["tax"]["country_code"] == "FR"


def test_un_assujetti_belge_est_debite_du_ht_sans_tva(monkeypatch):
    appels = _wire(monkeypatch,
                   identity=_identite(country_code="BE", vat_number="BE0123456789"))
    out = billing.subscribe(ORG, "standard", RETURN_URL, sub=SUB)
    assert appels["psp"][0] == 1900 == out["amount_ttc"]
    assert out["vat_scheme"] == "reverse_charge"
    assert "196" in out["vat_mention"]


def test_le_renouvellement_prend_exactement_le_meme_montant(monkeypatch):
    """Le seam unique, vérifié là où il compte : un client ne peut pas payer 22,80 €
    le premier mois et 19,00 € les suivants. Les deux chemins appellent le MÊME
    calcul, et ce test le prouve en comparant les montants réellement passés."""
    souscription = _wire(monkeypatch, identity=_identite())
    billing.subscribe(ORG, "standard", RETURN_URL, sub=SUB)
    premier = souscription["psp"][0]

    echeance: dict = {}

    def journalise(*a, **k):
        echeance["journal"] = (a, k)
        return 11

    def rejoue(amount, **k):
        echeance["psp"] = amount
        return {"id": "tr_r1", "status": "paid"}

    monkeypatch.setattr(db_billing, "count_renewal_attempts", lambda org, since: 0)
    monkeypatch.setattr(db_billing, "update_billing_payment", lambda r, **k: True)
    monkeypatch.setattr(db_billing, "schedule_next_billing", lambda *a: True)
    monkeypatch.setattr(db_billing, "insert_billing_payment", journalise)
    monkeypatch.setattr(billing_runner.mollie_client, "create_recurring_payment",
                        rejoue)
    now = datetime.now(timezone.utc)
    issue = billing_runner._charge_one(
        {"org_id": ORG, "plan": "standard", "method": "card", "status": "active",
         "customer_id": "cst_1", "mandate_id": "mdt_1",
         "current_period_end": now - timedelta(hours=1)}, now)

    assert issue == "renewed"
    assert echeance["psp"] == premier == 2280
    assert echeance["journal"][1]["tax"]["vat_rate_bps"] == 2000


def test_un_renouvellement_sans_identite_ne_prend_RIEN(monkeypatch):
    """Pas de repli sur le HT : c'est précisément le défaut que ce lot répare. Le
    cycle n'est pas décalé non plus — l'échéance reste due et repartira dès que
    l'identité sera réparée."""
    etat: dict = {}
    monkeypatch.setattr(db_billing, "get_billing_identity", lambda org: None)
    for nom in ("insert_billing_payment", "schedule_next_billing", "retry_billing_at",
                "set_subscription_status"):
        monkeypatch.setattr(db_billing, nom,
                            (lambda n: lambda *a, **k: etat.setdefault(n, True))(nom))
    monkeypatch.setattr(billing_runner.mollie_client, "create_recurring_payment",
                        lambda *a, **k: etat.setdefault("psp", True))
    now = datetime.now(timezone.utc)
    issue = billing_runner._charge_one(
        {"org_id": ORG, "plan": "standard", "method": "card", "status": "active",
         "customer_id": "cst_1", "mandate_id": "mdt_1",
         "current_period_end": now - timedelta(hours=1)}, now)

    assert issue == "tax_blocked"
    assert etat == {}, "ni débit, ni ligne de journal, ni décalage du cycle"


# ══ 4. ce qu'on ne réécrit PAS ═══════════════════════════════════════════════

def test_un_encaissement_d_avant_la_regle_reste_lisible_tel_quel():
    """Les deux paiements du 25/08 ont réellement été débités de 19,00 € sans TVA.
    Leur inventer une décomposition ferait mentir le journal sur ce que le PSP a
    pris : on rend `null`, et c'est ce `null` qui les distingue d'une ligne
    calculée — jamais un zéro, qui affirmerait une exonération."""
    ancienne = {"amount": 1900, "amount_ht": None, "vat_rate_bps": None,
                "vat_amount": None, "vat_scheme": None}
    vue = billing_vat.tax_view(ancienne)
    assert vue["amount"] == 1900
    assert vue["amount_ht"] is None and vue["vat_amount"] is None
    assert vue["vat_scheme"] is None

    recente = billing_vat.tax_view({"amount": 2280, "amount_ht": 1900,
                                    "vat_rate_bps": 2000, "vat_amount": 380,
                                    "vat_scheme": "fr_ttc"})
    assert recente["amount_ht"] == 1900 and recente["vat_scheme"] == "fr_ttc"


# ══ la lecture qui ne refuse jamais ══════════════════════════════════════════

def test_un_ecran_reste_lisible_quand_la_regle_refuserait():
    """`tax_preview` est le seul endroit où l'absence de calcul est SERVIE au lieu
    d'être levée — parce qu'ici rien n'est débité. Le formulaire d'identité doit
    justement pouvoir s'afficher quand l'identité est incomplète."""
    vide = billing_vat.tax_preview(1900, None)
    assert vide["amount_ttc"] is None and vide["vat_scheme"] is None
    assert vide["vat_blocked"] == "billing_identity_required"

    bloque = billing_vat.tax_preview(1900, _identite(country_code="DE"))
    assert bloque["vat_blocked"] == "vat_consumer_unsupported"

    ok = billing_vat.tax_preview(1900, _identite())
    assert (ok["amount_ttc"], ok["vat_blocked"]) == (2280, None)


def test_status_annonce_le_ttc_de_la_prochaine_echeance(monkeypatch):
    monkeypatch.setattr(db_billing, "get_org_subscription", lambda org: {
        "org_id": ORG, "plan": "standard", "status": "active", "method": "card",
        "provider": "mollie", "current_period_end": None, "next_billing_at": None,
        "grace_until": None, "canceled_at": None})
    monkeypatch.setattr(db_billing, "get_billing_identity", lambda org: _identite())
    etat = billing.status(ORG)
    assert etat["amount"] == 1900, "`amount` reste le prix HT du catalogue"
    assert etat["amount_ttc"] == 2280 and etat["vat_amount"] == 380
    assert etat["vat_scheme"] == "fr_ttc" and etat["vat_blocked"] is None


def test_status_dit_pourquoi_le_ttc_est_inconnu_plutot_que_d_echouer(monkeypatch):
    """Un abonnement ACTIF avec un `vat_blocked` posé est un signal : c'est une
    échéance que le runner ne pourra pas prélever."""
    monkeypatch.setattr(db_billing, "get_org_subscription", lambda org: {
        "org_id": ORG, "plan": "standard", "status": "active", "method": "card",
        "provider": "mollie", "current_period_end": None, "next_billing_at": None,
        "grace_until": None, "canceled_at": None})
    monkeypatch.setattr(db_billing, "get_billing_identity", lambda org: None)
    etat = billing.status(ORG)
    assert etat["subscribed"] is True
    assert etat["amount_ttc"] is None
    assert etat["vat_blocked"] == "billing_identity_required"


def test_un_abonnement_OFFERT_n_annonce_ni_ttc_ni_blocage(monkeypatch):
    """Un `comp` (forcé par un admin) n'est jamais prélevé : il n'y a pas de TTC à
    annoncer, et poser `vat_blocked` sur une org offerte sans identité serait une
    FAUSSE alerte — sur un écran dont c'est tout le rôle de signaler les échéances
    en danger. On ne va même pas lire l'identité."""
    lectures = []
    monkeypatch.setattr(db_billing, "get_org_subscription", lambda org: {
        "org_id": ORG, "plan": "standard", "status": "active", "method": "comp",
        "provider": "comp", "current_period_end": None, "next_billing_at": None,
        "grace_until": None, "canceled_at": None})
    monkeypatch.setattr(db_billing, "get_billing_identity",
                        lambda org: lectures.append(org))

    etat = billing.status(ORG)
    assert etat["comp"] is True and etat["subscribed"] is True
    assert etat["amount"] == 1900, "le prix du palier reste indicatif"
    assert etat["amount_ttc"] is None and etat["vat_scheme"] is None
    assert etat["vat_blocked"] is None, "rien ne bloque : rien ne sera prélevé"
    assert lectures == [], "inutile d'aller lire une identité qui ne servira pas"


# ══ la capacité : refus servis en 409, pas en 400 ════════════════════════════

def test_les_refus_de_tva_sont_des_conflits_d_etat_pas_des_entrees_invalides():
    """Le corps de l'appel est correct — c'est l'ORG qui n'est pas en état d'être
    débitée. Un 400 enverrait le client chercher le défaut dans sa requête."""
    from oto_mcp.capabilities import billing as cap_billing
    from oto_mcp.capabilities._types import AuthzDenied

    for message, code in (
        ("billing_identity_required: champs à renseigner : legal_name", 409),
        ("vat_consumer_unsupported: guichet OSS", 409),
        ("unknown_plan: gold", 400),
    ):
        def boum():
            raise ValueError(message)

        with pytest.raises(AuthzDenied) as e:
            cap_billing._domain(boum)
        assert e.value.status == code, message


def test_la_capacite_identite_est_declaree_sur_les_deux_verbes():
    from oto_mcp.capabilities.registry import CAPABILITIES

    caps = {c.key: c for c in CAPABILITIES if c.key.startswith("me.billing.identity")}
    assert set(caps) == {"me.billing.identity.get", "me.billing.identity.set"}
    # REST-only, comme toute la famille billing : poser l'identité de facturation
    # d'une société est un formulaire humain, pas un geste d'agent.
    assert all(c.mcp is None and c.rest for c in caps.values())
    assert [b.verb for b in caps["me.billing.identity.set"].rest_bindings()] == ["PUT"]
    assert [b.path for b in caps["me.billing.identity.get"].rest_bindings()] == [
        "/api/me/billing/identity"]
    # Même gate de dark launch que le reste (ADR 0043) : l'identité n'a pas de sens
    # si l'abonnement est dormant.
    assert all(c.gate is billing.is_enabled for c in caps.values())
