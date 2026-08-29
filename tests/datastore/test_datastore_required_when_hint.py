"""Un refus `required_when` dit OÙ écrire, pas seulement que ça manque (#545).

Mesuré le 29/08/2026 sur le troisième passage d'une campagne (105 écritures refusées,
lues dans les arguments du journal des appels) : **35 refus sur 105** — un tiers — ne
viennent pas d'une erreur de fond mais de la FORME du champ « à reprendre ». Le motif
manque, ou il est écrit DANS le champ énuméré `retraitement` au lieu de la colonne
`retraitement_motif`. L'agent se corrige (27/27 rattrapés), donc rien ne casse : ce qui
se paie, c'est qu'**un tiers des écritures sont doublées** — sur 1 778 lignes, quelques
centaines d'appels et leurs jetons.

Le refus disait qu'une contrainte n'était pas satisfaite. Il ne disait pas OÙ écrire.
C'est la même famille que le refus d'identifiant qui nomme la forme attendue (#517) :
**un refus qui dit ce qu'il attendait vaut une règle que personne ne lit** — il tient
sans consigne, sur toutes les missions, et il arrive au seul moment où il est
actionnable.

⚠️ Le pointeur est DÉRIVÉ du schéma, jamais deviné : une colonne est désignée comme
destination parce qu'elle déclare `required_when` sur la colonne qui vient de refuser.
Contre-lecture gardée en tête (issue) : « c'est le champ qu'il faut changer, pas
l'agent » — un `retraitement` objet `{valeur, motif}` serait la voie longue. Si le
message suffit, c'est la voie courte, et c'est celle-ci.
"""
from __future__ import annotations

import pytest

from oto_mcp.datastore import schema as S


# Le tableau de la campagne, réduit à ce qui produit les 35 refus : un aiguillage
# énuméré, et la colonne de texte libre qu'il rend obligatoire.
_SCHEMA = {
    "fields": [
        {"key": "raison_sociale", "type": "text"},
        {"key": "retraitement", "type": "enum",
         "options": ["injoignable", "hors_cible", "doublon"]},
        {"key": "retraitement_motif", "type": "text", "max_length": 300,
         "required_when": {"retraitement": ["injoignable", "hors_cible", "doublon"]}},
    ],
}


def _refus(row: dict) -> str:
    errs = S.validate_row(_SCHEMA, row)
    assert errs, f"aucun refus sur {row!r}"
    return " ; ".join(errs)


# ── le geste naturel de l'agent : le motif DANS le champ énuméré ──────────────

def test_le_motif_ecrit_dans_l_ENUM_dit_ou_il_va(_=None):
    """Le cas majoritaire des 35. La colonne énumérée reçoit une phrase ; le refus
    disait « hors options » et laissait deviner où mettre la phrase."""
    msg = _refus({"retraitement": "doublon de la ligne 412, même SIREN"})
    assert "retraitement_motif" in msg          # la colonne ATTENDUE est nommée
    assert "pas dans `retraitement`" in msg     # et celle qui n'en veut pas
    assert "injoignable" in msg                 # ce que l'énuméré accepte


def test_le_pointeur_ne_remplace_pas_le_refus(_=None):
    """On AJOUTE au refus, on ne le remplace pas : la valeur vue et les options
    restent dites, c'est ce qui permet de corriger sans relire le schéma."""
    msg = _refus({"retraitement": "doublon de la ligne 412"})
    assert "hors options" in msg and "'doublon de la ligne 412'" in msg


def test_details_nomme_la_colonne_attendue(_=None):
    """`details.expected_column` — la face REST rend le refus STRUCTURÉ, pour qu'un
    front puisse pointer le bon champ sans parser une phrase française."""
    det: dict = {}
    S.validate_row(_SCHEMA, {"retraitement": "doublon de la ligne 412"}, details=det)
    assert det.get("expected_column") == "retraitement_motif"


# ── l'autre moitié des 35 : le motif tout simplement absent ──────────────────

def test_le_motif_MANQUANT_dit_la_forme_attendue(_=None):
    """L'énuméré est bien rempli, le motif manque : le refus nomme la colonne — il
    le faisait déjà — et dit désormais ce qu'elle ACCEPTE (sa forme)."""
    msg = _refus({"retraitement": "injoignable"})
    assert "retraitement_motif" in msg and "requis" in msg
    assert "300" in msg                          # la borne, donc la forme


def test_le_motif_manquant_previent_le_mauvais_champ(_=None):
    """Le refus préempte le geste suivant : sans ça, l'agent corrige en écrivant le
    motif dans l'énuméré, et paie un SECOND aller-retour — c'est exactement la
    séquence mesurée."""
    msg = _refus({"retraitement": "injoignable"})
    assert "n'accepte que" in msg               # ce que l'aiguillage refusera
    assert "hors_cible" in msg                  # ses options, dites une fois pour toutes


def test_details_sur_le_champ_manquant_aussi(_=None):
    det: dict = {}
    S.validate_row(_SCHEMA, {"retraitement": "injoignable"}, details=det)
    assert det.get("expected_column") == "retraitement_motif"


# ── ce qui NE change pas ─────────────────────────────────────────────────────

def test_l_ecriture_CORRECTE_reste_acceptee(_=None):
    assert S.validate_row(_SCHEMA, {"retraitement": "injoignable",
                                    "retraitement_motif": "trois appels sans réponse"}) == []


def test_une_ligne_SANS_retraitement_ne_declenche_rien(_=None):
    """`required_when` ne mord que quand sa condition est remplie — inchangé."""
    assert S.validate_row(_SCHEMA, {"raison_sociale": "ACME"}) == []


def test_un_enum_ORDINAIRE_n_invente_pas_de_pointeur(_=None):
    """Aucune colonne ne se déclare requise par `statut` : le refus reste ce qu'il
    était. Un pointeur inventé serait pire que pas de pointeur — il enverrait
    l'agent écrire dans une colonne qui n'attend rien."""
    schema = {"fields": [
        {"key": "statut", "type": "enum", "options": ["a", "b"]},
        {"key": "note", "type": "text", "required_when": {"autre": "x"}},
        {"key": "autre", "type": "text"},
    ]}
    errs = S.validate_row(schema, {"statut": "zzz"})
    assert errs and "hors options" in errs[0]
    assert "pas dans" not in errs[0] and "note" not in errs[0]


def test_le_CODE_de_refus_est_inchange(_=None):
    """Toujours `row_invalid` : c'est le TEXTE qui change, pas la taxonomie. Un code
    neuf ferait traiter comme nouveau un refus que les clients gèrent déjà."""
    from oto_mcp.capabilities.datastore.rows import _write_refusal
    from oto_mcp.datastore.errors import RowValidationError

    refus = _write_refusal(RowValidationError(["x: champ requis manquant"],
                                              details={"expected_column": "y"}))
    assert refus.status == 400 and refus.code == "row_invalid"
    assert refus.details == {"expected_column": "y"}


def test_le_relevé_des_regles_EXECUTEES_est_intact(_=None):
    """`enforced` (#389) est dérivé du comportement : enrichir un message ne doit
    pas faire disparaître la règle qui le produit."""
    S.reset_enforced_keys()
    try:
        assert "required_when" in S.enforced_keys() and "options" in S.enforced_keys()
    finally:
        S.reset_enforced_keys()


# ── le seam d'écriture porte les détails jusqu'aux surfaces ──────────────────

def test_le_store_porte_les_details_dans_son_refus(monkeypatch):
    """Le refus est levé par `DatastorePg._check_row` : sans le passage des détails
    là, `details` resterait une propriété du validateur que personne ne voit."""
    from oto_mcp.datastore import core as dsm
    from oto_mcp.datastore.errors import RowValidationError

    st = dsm.DatastorePg("u", acting_org=35)
    with pytest.raises(RowValidationError) as exc:
        st._check_row(_SCHEMA, {"retraitement": "doublon de la ligne 412"})
    assert exc.value.details.get("expected_column") == "retraitement_motif"
