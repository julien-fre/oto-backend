"""`readonly` veut dire « ne se modifie plus une fois POSÉ » (oto#140, palier J5).

La lecture d'avant — « ne s'écrit plus après la création » — refusait toute valeur qui
DIFFÉRAIT de celle en place, y compris quand il n'y en avait aucune. Une colonne
déclarée `readonly` APRÈS l'import restait donc définitivement vide sur les lignes
existantes, sauf par forçage : le verrou fermait une case que personne n'avait remplie.

La règle, une seule, jugée par `couches.valeur_posee` (le même juge que le filtre des
`null` en écho, `columns`) :

- **pas de valeur posée** = case VIDE au sens d'`est_vide` : clé absente, `null`, `""`
  ordinaire (une cellule CSV vide remise telle quelle), ou une cellule qui ne porte que
  des couches (`{"comment": …}`) → la poser est permis ;
- **`@empty` EST une valeur posée** (« cherché, rien ») : stocké `{"valeur": "",
  "oto.vide_assume": true}`, sa valeur est `""`, pas `null` — le remplacer reste refusé ;
- une fois posée, la valeur ne se modifie plus : refus, forçage (#658) inchangé ;
- effacer (`null`) une valeur posée est un changement : refusé.

Hors du changement, et le fichier le tient aussi : une colonne CALCULÉE (formule,
readonly implicite) reste fermée même vide — elle n'est pas « à remplir », elle se
recalcule ; et `agent_access: "read"` ne suit PAS : c'est la destination qui n'est pas
à l'agent, vide ou pas.
"""
from __future__ import annotations

import pytest

from oto_mcp.datastore import forcage as fcg
from oto_mcp.datastore import schema as dsv2
from oto_mcp.datastore.couches import VIDE_ASSUME, valeur_posee
from oto_mcp.datastore.errors import RowValidationError

from champs_reserves_banc import banc  # noqa: F401

SCHEMA = {"fields": [{"key": "adresse", "type": "text", "readonly": True},
                     {"key": "libre", "type": "text"}]}
VIDE_ASSUME_STOCKE = {"valeur": "", VIDE_ASSUME: True}


def _refus(payload, avant, **k):
    return dsv2.reserved_refusals(SCHEMA, payload, avant, **k)[0]


# ── la garde, cas par cas ────────────────────────────────────────────────────

@pytest.mark.parametrize("avant", [
    {},                                         # clé absente
    {"adresse": None},                          # valeur null
    {"adresse": ""},                            # chaîne vide ordinaire
    {"adresse": {"valeur": "", "comment": "x"}},  # chaîne vide enveloppée
    {"adresse": {"comment": "registre"}},       # couches seules : pas de valeur
], ids=["absente", "null", "chaine-vide", "chaine-vide-enveloppee", "couches-seules"])
@pytest.mark.parametrize("neuf", ["1 rue A", {"valeur": "1 rue A"}, "@empty"],
                         ids=["nue", "enveloppee", "@empty"])
def test_une_case_SANS_valeur_posee_se_remplit(avant, neuf):
    assert _refus({"adresse": neuf}, avant) == []


def test_une_case_EMPTY_est_une_valeur_posee_qu_on_ne_remplace_pas():
    erreurs = _refus({"adresse": "1 rue A"}, {"adresse": VIDE_ASSUME_STOCKE})
    assert len(erreurs) == 1 and "`adresse`" in erreurs[0]


def test_une_valeur_posee_ne_se_modifie_plus():
    assert len(_refus({"adresse": "2 rue B"}, {"adresse": "1 rue A"})) == 1


def test_seul_le_marqueur_distingue_empty_d_une_chaine_vide():
    """Même valeur `""`, deux verdicts : le marqueur du vide ASSUMÉ fait la valeur
    posée, la chaîne vide ordinaire reste une case à remplir."""
    assert valeur_posee(VIDE_ASSUME_STOCKE)
    assert not valeur_posee("")
    assert not valeur_posee({"valeur": ""})


def test_effacer_une_valeur_posee_est_un_changement():
    assert len(_refus({"adresse": None}, {"adresse": "1 rue A"})) == 1
    assert len(_refus({"adresse": {"valeur": None}}, {"adresse": "1 rue A"})) == 1


def test_le_forcage_leve_le_refus_d_une_valeur_posee_et_le_releve():
    forcage = fcg.Forcage(demande=True, autorise=True)
    assert _refus({"adresse": "2 rue B"}, {"adresse": "1 rue A"}, forcage=forcage) == []
    assert [e["col"] for e in forcage.forcees] == ["adresse"]


def test_remplir_une_case_vide_n_est_pas_un_forcage():
    """Le forçage ne relève que ce que le cran REFUSAIT : remplir une case vide n'en
    est pas un, et le journal ne doit pas le compter."""
    forcage = fcg.Forcage(demande=True, autorise=True)
    assert _refus({"adresse": "1 rue A"}, {}, forcage=forcage) == []
    assert forcage.forcees == []


def test_une_colonne_CALCULEE_reste_fermee_meme_vide():
    schema = {"fields": [{"key": "a", "type": "text"},
                         {"key": "f", "type": "formula", "formula": "IF(a=\"x\";1;0)"}]}
    erreurs, _ = dsv2.reserved_refusals(schema, {"f": 1}, {"a": "x"})
    assert len(erreurs) == 1 and "CALCULÉE" in erreurs[0]


def test_agent_access_read_ne_suit_pas_la_case_vide():
    """La destination n'est pas à l'agent, vide ou pas — la création y est déjà
    refusée, la case vide d'une ligne en place l'est tout autant."""
    schema = {"fields": [{"key": "note", "type": "text", "agent_access": "read"}]}
    for avant in (None, {}, {"note": None}):
        erreurs, _ = dsv2.reserved_refusals(schema, {"note": "x"}, avant, agent=True)
        assert len(erreurs) == 1, avant


# ── le geste du store, sur les chemins qu'un agent emprunte ──────────────────

def test_patch_par_id_remplit_une_readonly_chaine_vide(banc):
    st, etat = banc
    etat["lignes"]["r1"]["adresse"] = ""
    st.update_row("viviers", "r1", {"adresse": "1 rue A"})
    assert etat["lignes"]["r1"]["adresse"] == "1 rue A"


def _sans_adresse(etat):
    etat["lignes"]["r1"].pop("adresse")


def test_patch_par_id_remplit_une_readonly_declaree_apres_l_import(banc):
    st, etat = banc
    _sans_adresse(etat)
    st.update_row("viviers", "r1", {"adresse": "1 rue A"})
    assert etat["lignes"]["r1"]["adresse"] == "1 rue A"
    with pytest.raises(RowValidationError, match="`adresse`"):
        st.update_row("viviers", "r1", {"adresse": "2 rue B"})
    assert etat["lignes"]["r1"]["adresse"] == "1 rue A"


def test_fusion_par_cle_remplit_une_readonly_null(banc):
    st, etat = banc
    etat["lignes"]["r1"]["adresse"] = None
    st.append_row("viviers", {"siren": "552081317", "adresse": "1 rue A"},
                  key="siren")
    assert etat["lignes"]["r1"]["adresse"] == "1 rue A"


def test_le_lot_remplit_une_readonly_absente(banc):
    st, etat = banc
    _sans_adresse(etat)
    out = st._write_rows_to_ns(7, [{"siren": "552081317", "adresse": "1 rue A"}],
                               key="siren")
    assert out["updated"] == 1
    assert etat["lignes"]["r1"]["adresse"] == "1 rue A"


def test_empty_pose_puis_remplace_est_refuse(banc):
    st, etat = banc
    _sans_adresse(etat)
    st.update_row("viviers", "r1", {"adresse": "@empty"})
    cellule = etat["lignes"]["r1"]["adresse"]
    assert cellule == VIDE_ASSUME_STOCKE                    # la forme stockée
    assert valeur_posee(cellule)
    with pytest.raises(RowValidationError, match="`adresse`"):
        st.update_row("viviers", "r1", {"adresse": "1 rue A"})
    assert etat["lignes"]["r1"]["adresse"] == VIDE_ASSUME_STOCKE


def test_effacer_une_readonly_posee_par_le_store_est_refuse(banc):
    st, etat = banc
    with pytest.raises(RowValidationError, match="`adresse`"):
        st.update_row("viviers", "r1", {"adresse": None})
    assert etat["lignes"]["r1"]["adresse"] == "1 rue A"
