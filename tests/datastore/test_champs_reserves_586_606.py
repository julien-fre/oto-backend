"""Les champs que l'appelant n'écrit pas — `origine: "system"` (#586) et
`readonly: true` (#606), sous UNE garde.

Deux gestes mesurés sur la même campagne, contre la donnée remise par le client :

1. **#586, 29/08/2026** — sur 41 fiches portant une couche `<champ>.origine` censée
   conserver la valeur remise, **une** l'a réécrite avec la valeur nouvelle. La couche
   était écrite par l'agent, donc destructible par lui ; c'était l'unique copie.
2. **#606, 29/08/2026** — quatorze valeurs source écrasées À L'EXACT sur douze fiches
   par cent (`adresse` ×9, `naf` ×3, `date_creation` ×2), onze sans aucune couche de
   récupération. La consigne l'interdisait depuis le début.

Hiérarchie : le chemin n'existe pas > la machine refuse > un contrôle détecte > la
consigne interdit. Ces deux crans montent d'un étage : une ligne de schéma, un refus
nommé, et pour l'origine la plateforme qui écrit à la place de l'agent.

⚠️ **Le cran borne tout le monde PAR DÉFAUT**, faces humaine et REST comprises : le
store ne sait pas distinguer un agent d'un humain, et une exemption par défaut serait un
trou. Ce que ce fichier fige est donc le régime SANS demande — et il n'a pas bougé.

⚠️ Ce qui a bougé le 02/09/2026 (#658) : la sortie du propriétaire n'est plus le schéma
(`data_patch_schema(readonly=false)`, écrire, refermer — une exécution interrompue entre
les deux laisse le verrou ouvert sans signal, mesuré sur `key_required`/#668) mais
`readonly_override=true` **sur l'appel**, sous palier et tracé. Il s'éprouve dans
`test_forcage_readonly_658.py` ; ici, aucun appel n'en demande, donc tout doit rester
refusé exactement comme avant.
"""
from __future__ import annotations

import pytest

from oto_mcp.datastore import core as dsm
from oto_mcp.datastore import schema as dsv2
from oto_mcp.datastore.errors import RowValidationError

from champs_reserves_banc import LIGNE as _LIGNE, SCHEMA as _SCHEMA, banc  # noqa: F401


# ══ #586 — l'origine posée par le système ════════════════════════════════════

def test_une_valeur_INCHANGEE_ne_pose_rien(banc):
    """Relire → repousser à l'identique n'est pas une modification : la colonne
    reste plate, aucune couche fantôme."""
    st, etat = banc
    st.update_row("viviers", "r1", {"raison_sociale": "ACME"})
    assert etat["lignes"]["r1"]["raison_sociale"] == "ACME"


def test_une_creation_ne_pose_rien(banc):
    """Créer n'est pas modifier : la valeur créée EST le point de départ."""
    st, etat = banc
    st.append_row("viviers", {"siren": "389256712", "raison_sociale": "NEUVE"})
    assert etat["creees"][0]["raison_sociale"] == "NEUVE"


# ── fermée à l'écriture ──────────────────────────────────────────────────────

def test_une_couche_deja_ecrite_par_un_agent_reste_lue_telle_quelle(banc):
    """Compatibilité : les 40 fiches de la campagne portent une origine écrite par
    l'agent AVANT la pose du cran. Elle n'est ni réécrite ni effacée."""
    st, etat = banc
    etat["lignes"]["r1"]["raison_sociale"] = {"valeur": "ACME", "origine": "fichier client"}
    out = st.update_row("viviers", "r1", {"raison_sociale": "ACME SA"},
                        versions=("current", "origine"))
    assert etat["lignes"]["r1"]["raison_sociale"] == {"valeur": "ACME SA",
                                                     "origine": "fichier client"}
    assert out["raison_sociale.origine"] == "fichier client"


def test_hors_declaration_l_origine_s_ecrit_comme_avant(banc):
    """Le défaut ne bouge pas : sur `libre`, l'agent pose et efface l'origine."""
    st, etat = banc
    st.update_row("viviers", "r1", {"libre": {"valeur": "x", "origine": "moi"}},
                  origine_override=True)
    assert etat["lignes"]["r1"]["libre"] == {"valeur": "x", "origine": "moi"}


# ══ #606 — la colonne du fichier source ══════════════════════════════════════

def test_changer_une_colonne_readonly_est_REFUSE_en_nommant_ou_va_la_chose(banc):
    """Le geste de l'incident : l'agent « complète » l'adresse avec le registre. La
    destination est la couche `comment` de la colonne ELLE-MÊME — la seule forme qui
    reste attachée au champ, se compte et se livre."""
    st, etat = banc
    with pytest.raises(RowValidationError) as exc:
        st.update_row("viviers", "r1", {"adresse": "2 rue B"})
    msg = str(exc.value)
    assert "`adresse`" in msg and "non modifiable" in msg
    assert "`adresse.comment`" in msg                       # où va la divergence
    assert exc.value.details == {"expected_column": "adresse.comment"}
    assert etat["maj"] == [] and etat["lignes"]["r1"]["adresse"] == "1 rue A"


def test_les_couches_d_une_colonne_readonly_restent_OUVERTES(banc):
    """Le cran verrouille la VALEUR ; `comment`, `link` — et `origine` quand elle
    n'est pas posée par le système — restent à l'appelant."""
    st, etat = banc
    st.update_row("viviers", "r1", {"adresse": {"comment": "registre — 2 rue B",
                                                "link": "https://x", "origine": "fichier"}},
                  origine_override=True)
    assert etat["lignes"]["r1"]["adresse"] == {"valeur": "1 rue A", "origine": "fichier",
                                              "comment": "registre — 2 rue B",
                                              "link": "https://x"}


def test_remplir_une_colonne_readonly_VIDE_est_permis(banc):
    """Renversé le 04/10/2026 (oto#140, J5) : ce banc disait « vide, elle reste vide ».
    `readonly` veut dire « ne se modifie plus une fois POSÉ » — l'autre lecture rendait
    toute colonne déclarée après son import définitivement vide. La case sans valeur
    se remplit ; une fois posée, elle est verrouillée (`test_readonly_case_vide_oto140`)."""
    st, etat = banc
    etat["lignes"]["r1"].pop("naf")
    st.update_row("viviers", "r1", {"naf": "62.01Z"})
    assert etat["lignes"]["r1"]["naf"] == "62.01Z"
    with pytest.raises(RowValidationError, match="`naf`"):
        st.update_row("viviers", "r1", {"naf": "70.22Z"})


def test_effacer_une_colonne_readonly_est_refuse(banc):
    st, etat = banc
    with pytest.raises(RowValidationError, match="`adresse`"):
        st.update_row("viviers", "r1", {"adresse": None})
    assert etat["lignes"]["r1"]["adresse"] == "1 rue A"


def test_la_fusion_par_cle_et_le_lot_refusent_aussi(banc):
    st, etat = banc
    with pytest.raises(RowValidationError, match="`adresse`"):
        st.append_row("viviers", {"siren": "552081317", "adresse": "2 rue B"})
    with pytest.raises(RowValidationError) as exc:
        st._write_rows_to_ns(7, [{"siren": "552081317", "libre": "ok"},
                                 {"siren": "552081317", "adresse": "2 rue B"}],
                             key="siren")
    assert "ligne 2/2" in str(exc.value) and "`adresse`" in str(exc.value)
    assert exc.value.details == {"expected_column": "adresse.comment"}
    assert etat["lignes"]["r1"]["adresse"] == "1 rue A"


# ── ce que le cran ne ferme PAS, et c'est voulu ──────────────────────────────

def test_annoter_sans_toucher_la_valeur_PASSE(banc):
    """`adresse.comment` seul : la forme que quatre fiches sur quatorze avaient déjà
    trouvée — en écrasant la valeur en plus. Ici la valeur reste."""
    st, etat = banc
    st.update_row("viviers", "r1", {"adresse": {"comment": "registre — 20 B AV. HUGO"}})
    assert etat["lignes"]["r1"]["adresse"] == {"valeur": "1 rue A",
                                              "comment": "registre — 20 B AV. HUGO"}


def test_un_objet_vide_est_ecarte_AVANT_le_cran(banc):
    """`{}` sur une valeur en place ne déplace rien (#608, oto#165) : rien à refuser.
    (`""`, lui, est une valeur depuis oto#140 J2 : il CHANGE la valeur, le cran juge.)"""
    st, etat = banc
    st.update_row("viviers", "r1", {"adresse": {}, "libre": "x"})
    assert etat["lignes"]["r1"]["adresse"] == "1 rue A"
    assert st.off_schema_report()["valeurs_ignorees"][0]["champ"] == "adresse"


def test_la_CREATION_d_une_ligne_PASSE(banc):
    """Rien n'est écrasé : le tableau qui ne doit pas grossir se ferme par
    `key_required` (#516), pas par `readonly`."""
    st, etat = banc
    st.append_row("viviers", {"siren": "389256712", "adresse": "3 rue C"})
    assert etat["creees"][0]["adresse"] == "3 rue C"


# ══ la déclaration ═══════════════════════════════════════════════════════════

def _errs(fields):
    return dsv2.validate_schema_def({"fields": fields})


# ⚠️ Trois cas RETIRÉS le 08/09/2026, tous sur `origine` : `{"origine": "agent"}`
# (vocabulaire fermé), et le cran posé sur un `json` ou une `list`. Ils vérifiaient que
# la POSE refuse une déclaration inapplicable — c'était juste tant que `origine:
# "system"` armait une capture. Le cran a été supprimé au profit de `donnees_d_origine`
# déclaré à l'import : ces refus promettaient donc un mécanisme mort, et leur motif
# décrivait une capture qui n'a plus lieu. Depuis le 01/10/2026 la clé est refusée comme
# toute clé RETIRÉE (`schema_keys.CLES_RETIREES`) quand un geste la pose ; celle que
# des colonnes portaient déjà est rangée par `scripts/durcir_schemas.py`. `readonly`,
# lui, s'applique toujours et reste gardé.
@pytest.mark.parametrize("field, attendu", [
    ({"key": "x", "readonly": "oui"}, "readonly"),
])
def test_une_declaration_qui_ne_peut_pas_s_appliquer_se_refuse_a_la_POSE(field, attendu):
    """Jamais acceptée-inerte (#347) : le refus nomme l'attendu."""
    errs = _errs([field, {"key": "y"}])
    assert errs and any(attendu in e for e in errs), errs


def test_les_crans_ne_se_posent_qu_au_PREMIER_niveau():
    errs = _errs([{"key": "o", "type": "object",
                   "fields": [{"key": "a", "readonly": True},
                              {"key": "b", "agent_access": "read"}]}])
    assert len([e for e in errs if "premier niveau" in e]) == 2, errs


def test_origine_est_REFUSEE_a_la_pose():
    errs = _errs([{"key": "b", "origine": "system"}])
    assert len(errs) == 1 and "retirée le 08/09/2026" in errs[0], errs


def test_les_crans_ne_se_posent_pas_sur_une_cible_de_couche():
    errs = _errs([{"key": "x"}, {"key": "x.comment", "readonly": True}])
    assert errs and any("COLONNE" in e for e in errs)


def test_le_retrait_est_une_valeur_nulle():
    """`data_patch_schema(fields=[{key, readonly: null}])` lève le cran sans
    réécrire : `null` est une absence, pour le lecteur comme pour la pose."""
    schema = {"fields": [{"key": "a", "readonly": None}, {"key": "b"}]}
    assert dsv2.validate_schema_def(schema) == []
    assert dsv2.readonly_fields(schema) == set() == dsv2.system_origin_fields(schema)


def test_les_clefs_sont_ADMISES():
    """Sans quoi `data_set_schema` refuserait un cran qui mord."""
    assert dsv2.validate_schema_def(_SCHEMA) == []


def test_la_face_REST_garde_son_code_et_porte_la_colonne_attendue():
    from oto_mcp.capabilities.datastore.rows import _write_refusal

    refus = _write_refusal(RowValidationError(["x"], details={"expected_column": "n"}))
    assert refus.status == 400 and refus.code == "row_invalid"
    assert refus.details == {"expected_column": "n"}
