"""Une valeur refusée par le schéma s'écarte ; la fiche, elle, s'écrit (#667).

Mesuré le 02/09/2026 sur une vague de 40 écritures d'agents : **8 rejets, dont 5
pour ce seul motif** — une sous-valeur de contact hors des options déclarées
faisait repartir l'appel ENTIER. L'effectif relevé au registre, la convention
collective vérifiée, les interlocuteurs trouvés, la qualification rédigée et
sourcée : tout jeté, ~60 000 jetons par fiche, déjà payés une fois.

⚠️ **Ce banc garde le PARTAGE, pas l'indulgence.** Ce qui s'écarte est une règle
violée sur une VALEUR isolée ; ce qui refuse tout reste ce qui porte sur la
COHÉRENCE de l'enregistrement — un requis manquant écrirait une fiche fausse.
Un test qui vérifierait seulement « ça passe maintenant » raterait la moitié qui
compte, et laisserait le prochain lot élargir l'écartement sans le voir.

Éprouvé rouge le 2026-09-03 : `hors` retiré de l'appel à `validate_row` dans
`_check_row` ⟹ le premier test nomme la fiche perdue ; la revalidation retirée
⟹ le quatrième nomme la ligne amputée écrite quand même.
"""
from __future__ import annotations

import pytest

from oto_mcp.datastore import ecartes as dsec
from oto_mcp.datastore.core import DatastorePg

# Le cas mesuré : une colonne fermée sur `__non_conserve__` parce que le client
# exige que le profil professionnel d'une personne physique ne soit pas gardé.
SCHEMA_CONTACTS = {
    "strict": True,
    "fields": [
        {"key": "nom", "type": "text", "required": True},
        {"key": "contacts", "type": "list", "of": {"fields": [
            {"key": "nom", "type": "text"},
            {"key": "linkedin", "type": "enum", "options": ["__non_conserve__"]},
        ]}},
    ],
}


def _fiche() -> dict:
    return {"nom": "ACME", "contacts": [{"nom": "Jane Doe",
                                         "linkedin": "https://example.test/in/jd"}]}


def test_la_fiche_est_ecrite_sans_la_valeur_refusee():
    store = DatastorePg("sub-test")
    ligne = _fiche()
    store._check_row(SCHEMA_CONTACTS, ligne)
    assert ligne["nom"] == "ACME", "le travail de la fiche doit survivre au refus"
    assert ligne["contacts"] == [{"nom": "Jane Doe"}], (
        "la valeur interdite doit avoir disparu de ce qui sera écrit")


def test_l_ecart_est_DIT_avec_le_champ_et_la_valeur():
    """Un écartement muet serait pire que le refus : l'agent croirait sa fiche
    complète. Le relevé nomme le champ (pour réécrire CE champ seul) et la valeur
    rejetée (la réponse est la seule copie qui en reste)."""
    store = DatastorePg("sub-test")
    store._check_row(SCHEMA_CONTACTS, _fiche())
    rapport = store.off_schema_report()
    assert "valeurs_ecartees" in rapport
    ecarte = rapport["valeurs_ecartees"][0]
    assert ecarte["champ"] == "contacts[0].linkedin"
    assert ecarte["valeur_rejetee"] == "https://example.test/in/jd"
    assert "hors options" in ecarte["motif"]
    assert rapport["valeurs_ecartees_hint"], "le relevé dit quoi faire, pas seulement quoi"


def test_un_defaut_de_COHERENCE_refuse_toujours_TOUT():
    """La contre-épreuve, et c'est elle qui borne le lot : un champ requis
    manquant rendrait la fiche fausse. Là, refuser tout est correct — et le
    mélange des deux refus retombe du côté strict."""
    store = DatastorePg("sub-test")
    ligne = _fiche()
    del ligne["nom"]
    with pytest.raises(Exception) as exc:
        store._check_row(SCHEMA_CONTACTS, ligne)
    assert "requis" in str(exc.value)


def test_une_ligne_AMPUTEE_qui_devient_invalide_refuse_tout():
    """Retirer une valeur peut en défaire une autre. Sans second tour de
    validation, on écrirait une ligne incomplète sur la foi d'un contrôle qui
    n'a pas vu sa forme finale."""
    schema = {"strict": True, "fields": [
        {"key": "qualification", "type": "enum", "options": ["ok"], "required": True},
        {"key": "note", "type": "text"},
    ]}
    store = DatastorePg("sub-test")
    ligne = {"qualification": "a_revoir", "note": "le travail"}
    with pytest.raises(Exception):
        store._check_row(schema, ligne)
    assert ligne["qualification"] == "a_revoir", (
        "un refus final ne doit pas laisser derrière lui une ligne à moitié amputée")


def test_un_PATCH_ne_se_fait_pas_amputer_ce_qu_il_n_ecrit_pas():
    """La valeur fautive vient de la base, pas du geste : l'écarter serait un
    effacement silencieux. Le cran juge le geste, pas le passé qu'il hérite."""
    schema = {"strict": True, "fields": [
        {"key": "statut", "type": "enum", "options": ["a", "b"]},
        {"key": "note", "type": "text"},
    ]}
    store = DatastorePg("sub-test")
    with pytest.raises(Exception):
        store._check_row(schema, {"statut": "hérité", "note": "neuve"},
                         written={"note"})


def test_le_relevé_reste_VIDE_quand_tout_est_conforme():
    """Pas de clé parasite dans une réponse normale — même règle que les cinq
    relevés voisins."""
    store = DatastorePg("sub-test")
    store._check_row(SCHEMA_CONTACTS, {"nom": "ACME", "contacts": [{"nom": "Jane"}]})
    assert "valeurs_ecartees" not in store.off_schema_report()


def test_le_chemin_se_lit_sans_reparser_une_phrase():
    """Le relevé porte un chemin STRUCTURÉ, celui que la validation compose.
    Reconstituer la cible depuis le message français serait un contrat déguisé —
    et le message, lui, a le droit de changer."""
    data = {"contacts": [{"nom": "Jane", "linkedin": "u"}]}
    assert dsec.tete("contacts[0].linkedin") == "contacts"
    assert dsec.retirer(data, "contacts[0].linkedin") is True
    assert data == {"contacts": [{"nom": "Jane"}]}
    assert dsec.retirer(data, "contacts[0].linkedin") is False, (
        "retirer ce qui n'est plus là ne doit pas mentir sur ce qu'il a fait")


def test_une_valeur_MAL_RANGEE_n_est_pas_une_valeur_a_jeter():
    """La nuance qui a failli manquer, et c'est la plus coûteuse (#545/#667).

    Quand le schéma déclare une DESTINATION pour la valeur refusée — une colonne
    qui se dit requise par celle qui vient de refuser — l'agent n'a pas écrit une
    donnée indésirable : il l'a mise au mauvais endroit, et le refus le lui dit
    (35 refus mesurés le 29/08, 27 rattrapés sur 27). L'écarter écrirait une fiche
    qui prétend ne pas avoir été qualifiée, sous un `ok: true`. **Perdre du travail
    coûte cher ; en corrompre en silence coûte plus cher.**

    Ma première version écartait uniformément. C'est le banc de #545 qui l'a
    attrapée — pas un raisonnement.
    """
    schema = {"fields": [
        {"key": "retraitement", "type": "enum",
         "options": ["injoignable", "hors_cible"]},
        {"key": "retraitement_motif", "type": "text", "max_length": 300,
         "required_when": {"retraitement": ["injoignable", "hors_cible"]}},
    ]}
    store = DatastorePg("sub-test")
    ligne = {"retraitement": "doublon de la ligne 412, même SIREN"}
    with pytest.raises(Exception) as exc:
        store._check_row(schema, ligne)
    assert "retraitement_motif" in str(exc.value), (
        "le refus doit continuer de dire OÙ va la valeur")
    assert ligne["retraitement"], "et la valeur reste à l'appelant, pour la replacer"


def test_ecarter_la_SEULE_valeur_du_geste_ne_fabrique_pas_une_ligne_vide():
    """La deuxième borne, rappelée par le banc du régime strict (#319).

    Quand la valeur refusée est TOUT ce que le geste pose, l'amputer ne sauve
    aucune fiche : elle en crée une vide, sous un `ok`. Le motif de ce lot est de
    préserver un travail DÉJÀ FAIT — là où il n'y en a pas, refuser reste juste.
    """
    schema = {"strict": True,
              "fields": [{"key": "priorite", "type": "enum",
                          "options": ["haute", "basse"]}]}
    store = DatastorePg("sub-test")
    with pytest.raises(Exception):
        store._check_row(schema, {"priorite": "Moyenne"})


def test_la_notion_de_VIDE_est_celle_du_validateur():
    """Deux définitions du vide divergeraient au premier cas limite — le défaut
    exact de #608, où le validateur et le merge lisaient `""` autrement."""
    from oto_mcp.datastore import schema as dsv2
    assert dsv2.est_vide is dsv2._is_empty
