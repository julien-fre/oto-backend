"""PayFit — le PLANCHER de rédaction, éprouvé sur des valeurs SENTINELLES.

Depuis le 17/09/2026, le connecteur `payfit` ne retire plus rien en dur : ce qui
protège NIR, IBAN/BIC et motif d'absence est un **défaut serveur de filtre de
champs** (`field_filter_defaults.SERVER_DEFAULTS["payfit"]`), que l'org_admin peut
lever. Un défaut qu'on ne prouve pas est une intention, pas une protection — ce
fichier le prouve par le chemin RÉEL (`redaction.redact_payload`, celui du
middleware et d'`oto_call`), jamais en relisant la table de règles.

Chaque valeur sensible est une **sentinelle** reconnaissable : un test qui vérifie
« la clé a changé » passerait sur un masque partiel. Ici on cherche la chaîne exacte
dans tout le JSON rendu — si elle en sort, où que ce soit, le test est rouge.

Toutes les valeurs sont factices.
"""
import json

import pytest

from oto_mcp import field_filter_defaults, redaction

# Sentinelles : aucune ne ressemble à une vraie donnée, toutes sont introuvables
# ailleurs que dans le payload de test.
NIR = "SENTINELLE-NIR-0000000000000"
NTT = "SENTINELLE-NTT-000000000"
IBAN = "SENTINELLE-IBAN-FR0000000000"
BIC = "SENTINELLE-BIC-0000"
MOTIF = "fr_maladie_ordinaire"
# Ce qui doit RESTER lisible : c'est ce que la cliente vient chercher.
SALAIRE = "SENTINELLE-MONTANT-4242"
NOM = "SENTINELLE-NOM"


def _payload() -> dict:
    """La forme réelle d'une réponse du connecteur : une page de collaborateurs, un
    contrat FR, des absences, des écritures comptables — imbriqués comme l'amont
    les imbrique."""
    return {
        "count": 1,
        "collaborators": [{
            "id": "c1",
            "firstName": NOM,
            "socialSecurityNumber": NIR,
            "temporaryTechnicalNumber": NTT,
            "iban": IBAN,
            "bic": BIC,
            "birthDate": "1990-01-01",
            "nationality": "FR",
            "emails": [{"email": "ada@exemple.test", "type": "professional"}],
            "addresses": [{"address": "1 rue Factice", "type": "main"}],
        }],
        "contract": {
            "contractId": "k1",
            "numeroSecuriteSociale": NIR,
            "numeroTechniqueTemporaire": NTT,
            "natureContratDsn": "01",
            "idcc": "1234",
            "workingTimeModality": "forfait_jours",
            "motifRuptureDeContratDsn": "081",
        },
        "absences": [
            {"id": "a1", "absence_type": MOTIF, "absence_category": "restricted"},
            {"id": "a2", "absence_type": "fr_conges_payes",
             "absence_category": "ordinary_leave"},
        ],
        "entries": [{"accountId": "641000", "accountName": "Salaires",
                     "employeeFullName": NOM, "debit": SALAIRE}],
    }


@pytest.fixture
def org_sans_politique(monkeypatch):
    """Une org qui n'a RIEN posé : le repli est le défaut serveur."""
    monkeypatch.setattr("oto_mcp.access.rbac.current_user_sub_from_token",
                        lambda: "sub-test")
    monkeypatch.setattr("oto_mcp.access.rbac.scope.current_org", lambda sub: 1)
    monkeypatch.setattr("oto_mcp.access.rbac.org_store.get_org_field_filters",
                        lambda org_id: {})


@pytest.fixture
def org_qui_leve(monkeypatch):
    """Un org_admin a posé `rules: []` sur `payfit` : sa politique est AUTORITAIRE
    et vide ⟹ plus rien n'est masqué. C'est le geste exact qui lève le plancher."""
    monkeypatch.setattr("oto_mcp.access.rbac.current_user_sub_from_token",
                        lambda: "sub-test")
    monkeypatch.setattr("oto_mcp.access.rbac.scope.current_org", lambda sub: 1)
    monkeypatch.setattr("oto_mcp.access.rbac.org_store.get_org_field_filters",
                        lambda org_id: {"payfit": {"rules": []}})


def _rendu(payload) -> str:
    out = redaction.redact_payload("payfit", payload)
    assert out is not redaction.PASSTHROUGH, (
        "aucune politique ne s'est appliquée à `payfit` : le plancher serveur n'est "
        "pas monté, tout sort en clair.")
    return json.dumps(out, ensure_ascii=False)


# --- le plancher masque -------------------------------------------------------

@pytest.mark.parametrize("sentinelle,quoi", [
    (NIR, "le NIR"),
    (NTT, "le numéro technique temporaire (qui tient lieu de NIR)"),
    (IBAN, "l'IBAN"),
    (BIC, "le BIC"),
    (MOTIF, "le motif d'absence (donnée de santé, RGPD art. 9)"),
])
def test_the_server_default_masks_the_sentinel(org_sans_politique, sentinelle, quoi):
    rendu = _rendu(_payload())
    assert sentinelle not in rendu, (
        f"{quoi} est sorti en clair malgré le défaut serveur `payfit`.")


def test_the_nir_is_masked_in_BOTH_of_its_names(org_sans_politique):
    """L'amont nomme le NIR `socialSecurityNumber` sur un collaborateur et
    `numeroSecuriteSociale` sur un contrat FR : couvrir un seul nom laisserait
    l'autre passer, et personne ne le verrait."""
    out = redaction.redact_payload("payfit", _payload())
    assert out["collaborators"][0]["socialSecurityNumber"] != NIR
    assert out["contract"]["numeroSecuriteSociale"] != NIR


def test_the_masked_iban_still_looks_like_an_iban(org_sans_politique):
    """`preserve: iban` garde de quoi reconnaître un compte sans pouvoir s'en
    servir — un masque total rendrait le rapprochement bancaire impossible."""
    masque = redaction.redact_payload("payfit", _payload())["collaborators"][0]["iban"]
    assert masque != IBAN
    # Le moteur garde le code pays et les 4 derniers caractères, et masque tout le
    # milieu : de quoi rapprocher un versement d'un compte connu, jamais d'en émettre.
    assert masque.startswith(IBAN[:2]) and masque.endswith(IBAN[-4:])
    assert IBAN[2:-4] not in masque


# --- le plancher ne masque QUE ça ---------------------------------------------

@pytest.mark.parametrize("sentinelle,quoi", [
    (SALAIRE, "le montant d'une écriture comptable"),
    (NOM, "le nom du salarié"),
])
def test_what_the_client_came_for_stays_readable(org_sans_politique, sentinelle, quoi):
    assert sentinelle in _rendu(_payload()), (
        f"{quoi} a été masqué : le plancher déborde de son périmètre.")


def test_the_absence_category_survives_the_mask(org_sans_politique):
    """C'est le point d'équilibre du connecteur : le MOTIF est masqué, la CATÉGORIE
    reste — on planifie une charge sans lire un arrêt maladie."""
    out = redaction.redact_payload("payfit", _payload())
    assert [a["absence_category"] for a in out["absences"]] == [
        "restricted", "ordinary_leave"]


def test_the_contract_is_served_whole(org_sans_politique):
    """Nature du contrat, convention collective, forfait jours, motif de RUPTURE :
    ouverts par décision (17/09/2026). Seul le NIR du contrat est masqué."""
    contrat = redaction.redact_payload("payfit", _payload())["contract"]
    assert contrat["natureContratDsn"] == "01"
    assert contrat["idcc"] == "1234"
    assert contrat["workingTimeModality"] == "forfait_jours"
    assert contrat["motifRuptureDeContratDsn"] == "081"


def test_the_mask_never_touches_a_homonym_of_type(org_sans_politique):
    """⚠️ Le moteur matche par NOM DE FEUILLE : une règle sur `type` aurait abîmé le
    type d'un e-mail et d'une adresse. C'est pour ça que le motif d'absence est servi
    sous `absence_type`, et ce test est la garde de ce choix."""
    out = redaction.redact_payload("payfit", _payload())
    collab = out["collaborators"][0]
    assert collab["emails"][0]["type"] == "professional"
    assert collab["addresses"][0]["type"] == "main"


# --- l'org_admin peut tout lever ----------------------------------------------

@pytest.mark.parametrize("sentinelle", [NIR, NTT, IBAN, BIC, MOTIF])
def test_an_org_that_lifts_the_filter_gets_everything(org_qui_leve, sentinelle):
    """`rules: []` = une politique d'org VIDE et autoritaire. L'entreprise
    propriétaire de ses données de paie peut les lire."""
    out = redaction.redact_payload("payfit", _payload())
    assert out is redaction.PASSTHROUGH or sentinelle in json.dumps(out)


def test_clearing_the_policy_does_the_OPPOSITE_of_lifting(monkeypatch):
    """⚠️ Le piège : `rules: null` EFFACE la politique d'org, donc REMET le défaut
    serveur. Lever un plancher se fait avec `rules: []`, jamais en effaçant."""
    monkeypatch.setattr("oto_mcp.access.rbac.current_user_sub_from_token",
                        lambda: "sub-test")
    monkeypatch.setattr("oto_mcp.access.rbac.scope.current_org", lambda sub: 1)
    monkeypatch.setattr("oto_mcp.access.rbac.org_store.get_org_field_filters",
                        lambda org_id: {})       # politique effacée = clé absente
    assert NIR not in _rendu(_payload())


# --- ce que le plancher change ailleurs ---------------------------------------

def test_payfit_now_fails_CLOSED_when_the_policy_cannot_be_resolved(monkeypatch):
    """Conséquence assumée d'avoir un défaut serveur : si la résolution échoue (DB),
    `payfit` RETIENT sa sortie au lieu de la laisser passer en clair. Avant ce lot,
    le connecteur n'avait pas de défaut et serait passé en `PASSTHROUGH`."""
    def _boom(service):
        raise RuntimeError("base indisponible")

    monkeypatch.setattr(redaction, "_resolve_field_filter", _boom)
    with pytest.raises(redaction.RedactionWithheld):
        redaction.redact_payload("payfit", _payload())


def test_the_default_only_targets_unambiguous_leaf_names():
    """Garde de conception : `FieldFilter` matche la feuille à toute profondeur, donc
    un nom générique dans le plancher abîmerait ses homonymes ailleurs. La liste
    interdite est celle des noms qu'on a réellement vus partagés dans les réponses
    PayFit."""
    partages = {"type", "name", "id", "status", "date", "value", "code", "email"}
    vises = {f.lower() for regle in field_filter_defaults.SERVER_DEFAULTS["payfit"]["rules"]
             for f in regle["fields"]}
    assert not vises & partages, (
        f"{sorted(vises & partages)} : nom de feuille PARTAGÉ dans les réponses "
        "PayFit — la règle abîmerait d'autres champs. Sers la donnée sous un nom qui "
        "n'appartient qu'à elle (cf. `absence_type`) avant de la viser.")
