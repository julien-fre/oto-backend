"""`lifecycle.labels` : le nom affiché de chaque étape (oto#140, 25/09/2026).

Présentation, jamais validation : la plateforme en juge la FORME à la pose — un objet,
des clés qui sont des états déclarés, des chaînes non vides d'au plus 60 caractères —
et n'en fait rien d'autre. Une ligne porte toujours le code de l'état.

⚠️ Le refus qui compte est celui d'un état INCONNU : un libellé posé sur une faute de
frappe serait stocké, servi, et jamais affiché — l'auteur croirait l'étape renommée.
"""
from __future__ import annotations

from oto_mcp.datastore import schema_keys as K
from oto_mcp.datastore.cycle_de_vie import LIBELLE_ETAT_MAX, merge_lifecycle
from oto_mcp.datastore.definition import validate_schema_def
from oto_mcp.datastore.effacements import merge_fields
from oto_mcp.datastore.validation import validate_row

_ETATS = ["a_qualifier", "en_cours", "perdu"]


def _schema(labels=..., **lc):
    cycle = {"states": list(_ETATS), "transitions": {"a_qualifier": ["en_cours"],
                                                      "en_cours": ["perdu"]},
             "terminal": ["perdu"], **lc}
    if labels is not ...:
        cycle["labels"] = labels
    return {"fields": [{"key": "ref", "type": "text"},
                       {"key": "statut", "type": "text", "role": "status",
                        "lifecycle": cycle}]}


def test_des_libelles_sur_une_partie_des_etats_sont_acceptes():
    assert validate_schema_def(_schema({"a_qualifier": "À qualifier"})) == []
    assert validate_schema_def(_schema({})) == []
    assert validate_schema_def(_schema()) == []


def test_un_etat_INCONNU_est_refuse_et_NOMME():
    errs = validate_schema_def(_schema({"a_qualifer": "À qualifier",
                                        "en_cours": "En cours"}))
    assert len(errs) == 1, errs
    assert "'a_qualifer'" in errs[0] and "état inconnu" in errs[0]
    assert "a_qualifier, en_cours, perdu" in errs[0], "le refus dit les états permis"


def test_la_forme_est_refusee_quand_ce_n_est_pas_un_objet():
    for mauvais in (["À qualifier"], "À qualifier", None):
        errs = validate_schema_def(_schema(mauvais))
        assert len(errs) == 1 and "lifecycle.labels doit être un objet" in errs[0], errs


def test_un_libelle_vide_ou_qui_n_est_pas_une_chaine_est_refuse():
    for mauvais in ("", "   ", 3, None, ["x"]):
        errs = validate_schema_def(_schema({"en_cours": mauvais}))
        assert len(errs) == 1 and "chaîne non vide" in errs[0], (mauvais, errs)


def test_la_borne_est_60_caracteres():
    assert LIBELLE_ETAT_MAX == 60
    assert validate_schema_def(_schema({"en_cours": "x" * 60})) == []
    errs = validate_schema_def(_schema({"en_cours": "x" * 61}))
    assert len(errs) == 1 and "61 caractères" in errs[0], errs


def test_jugee_aussi_sur_un_cycle_de_vie_SECONDAIRE():
    """Un tableau peut porter des états humains à côté de sa file : ce sont justement
    eux qu'un écran nomme, et `lifecycle_of` ne voit que la colonne de file."""
    s = _schema(claimable={"statut": "a_qualifier"})
    s["fields"].append({"key": "suivi", "type": "text",
                        "lifecycle": {"states": ["relance"],
                                      "labels": {"relancee": "Relancé"}}})
    errs = validate_schema_def(s)
    assert len(errs) == 1 and "`suivi`" in errs[0] and "'relancee'" in errs[0], errs


def test_jamais_lue_par_une_ECRITURE():
    """Présentation, jamais validation : une ligne porte le CODE, et le libellé ne
    change rien à ce qui passe ou non."""
    avec, sans = _schema({"a_qualifier": "À qualifier"}), _schema()
    for ligne in ({"statut": "a_qualifier"}, {"statut": "À qualifier"}):
        assert validate_row(avec, ligne) == validate_row(sans, ligne)


def test_la_fusion_descend_ETAT_PAR_ETAT():
    """Le défaut d'oto#64 ne doit pas se reformer sur la nouvelle clé : nommer une
    étape ne retire pas le libellé des autres."""
    cur = _schema({"a_qualifier": "À qualifier", "en_cours": "En cours"})
    lc = cur["fields"][1]["lifecycle"]
    out = merge_lifecycle(lc, {"labels": {"perdu": "Perdu"}})
    assert out["labels"] == {"a_qualifier": "À qualifier", "en_cours": "En cours",
                             "perdu": "Perdu"}
    assert out["states"] == _ETATS and out["transitions"] == lc["transitions"]
    assert merge_lifecycle(lc, {"labels": {"en_cours": None}})["labels"] == {
        "a_qualifier": "À qualifier"}
    assert "labels" not in merge_lifecycle(lc, {"labels": None})
    # un patch qui ne nomme pas `labels` les laisse en place
    assert merge_lifecycle(lc, {"terminal": ["perdu"]})["labels"] == lc["labels"]


def test_le_patch_de_colonne_pose_les_libelles_sans_rien_effacer():
    """Le geste de `data_patch_schema` sur un tableau existant, sans libellés."""
    cur = _schema()["fields"]
    out, added, updated = merge_fields(
        cur, [{"key": "statut", "lifecycle": {"labels": {"a_qualifier": "À qualifier"}}}])
    lc = out[1]["lifecycle"]
    assert lc["labels"] == {"a_qualifier": "À qualifier"}
    assert lc["states"] == _ETATS and lc["terminal"] == ["perdu"]
    assert lc["transitions"] == {"a_qualifier": ["en_cours"], "en_cours": ["perdu"]}
    assert out[1]["role"] == "status" and updated == ["statut"] and added == []
    assert validate_schema_def({"fields": out}) == []


def test_declaree_front_seul_dans_la_liste_fermee():
    cle = next(c for c in K.CLES_DU_CYCLE if c.nom == "labels")
    assert cle.lecteurs == ("front",)
    assert "jamais validation" in cle.quoi
    # tout ce que les modules du cycle de vie lisent dans le bloc y est déclaré
    for nom in ("states", "transitions", "terminal", "max_claims", "abandon_state",
                "claimable", "labels"):
        assert nom in K.CYCLE_RECONNUES, nom
