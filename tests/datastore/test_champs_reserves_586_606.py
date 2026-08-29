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

⚠️ **Le cran borne tout le monde**, faces humaine et REST comprises : le store ne sait
pas distinguer un agent d'un humain, et une exemption par défaut serait un trou. La
sortie du propriétaire est le schéma (`data_patch_schema(readonly=false)`), jamais un
paramètre de `data_write`.
"""
from __future__ import annotations

import pytest

from oto_mcp.datastore import core as dsm
from oto_mcp.datastore import schema as dsv2
from oto_mcp.datastore.errors import RowValidationError


_FIELDS = [{"key": "siren", "type": "text"},
           {"key": "raison_sociale", "type": "text", "origine": "system"},
           {"key": "adresse", "type": "text", "readonly": True,
            "report_to": "notes_verification"},
           {"key": "naf", "type": "text", "readonly": True},
           {"key": "notes_verification", "type": "text"},
           {"key": "libre", "type": "text"}]
_SCHEMA = {"key": "siren", "fields": _FIELDS}
_LIGNE = {"siren": "552081317", "raison_sociale": "ACME",
          "adresse": "1 rue A", "naf": "62.01Z"}


def _fake_merge_locked(rows):
    """Stub du seam verrou de ligne (#197), comme `test_datastore_key_required`."""
    def merge_locked(ns_id, row_id, apply_fn, updated_at, **k):
        if row_id not in rows:
            return None
        merged = apply_fn(dict(rows[row_id]))
        rows[row_id] = dict(merged)
        return ({"row_id": row_id, "created_at": "t0", "updated_at": updated_at,
                 "data": dict(merged)}, merged)
    return merge_locked


@pytest.fixture()
def banc(monkeypatch):
    """Un tableau `viviers` d'UNE ligne, schéma commutable.

    Rend `(store, etat)` — `etat["lignes"]` est la base, `etat["creees"]` relève les
    insertions réellement parties, `etat["maj"]` les mises à jour par identifiant :
    c'est ce qui distingue « rien n'a été écrit » d'une erreur rendue après coup."""
    st = dsm.DatastorePg("u", acting_org=35)
    etat = {"schema": _SCHEMA, "lignes": {"r1": dict(_LIGNE)}, "creees": [], "maj": []}
    monkeypatch.setattr(st, "_resolve", lambda ns, write=False: 7)
    monkeypatch.setattr(dsm.db, "get_datastore_namespace_by_id",
                        lambda ns_id: {"id": ns_id, "namespace": "viviers",
                                       "schema": etat["schema"]})

    def find(ns_id, key, kv):
        for rid, data in etat["lignes"].items():
            if key and str(data.get(key)) == str(kv):
                return rid
        return None

    def insert(ns_id, rid, data, *a, **k):
        etat["creees"].append(data)
        etat["lignes"][rid] = dict(data)
        return {"row_id": rid, "created_at": "t", "updated_at": "t", "data": data}

    def get_row(ns_id, rid):
        data = etat["lignes"].get(rid)
        return ({"row_id": rid, "created_at": "t", "updated_at": "t",
                 "data": dict(data)} if data is not None else None)

    def update(ns_id, rid, data, updated_at):
        etat["maj"].append(rid)
        etat["lignes"][rid] = dict(data)
        return {"row_id": rid, "created_at": "t", "updated_at": updated_at,
                "data": dict(data)}

    monkeypatch.setattr(dsm.db, "datastore_find_row_id_by_key", find)
    monkeypatch.setattr(dsm.db, "datastore_get_row", get_row)
    monkeypatch.setattr(dsm.db, "datastore_insert_row", insert)
    monkeypatch.setattr(dsm.db, "datastore_update_row", update)
    monkeypatch.setattr(dsm.db, "datastore_active_lease", lambda ns_id, rid: None)
    monkeypatch.setattr(dsm.db, "datastore_merge_row_locked",
                        _fake_merge_locked(etat["lignes"]))
    return st, etat


# ══ #586 — l'origine posée par le système ════════════════════════════════════

def test_la_premiere_ecriture_qui_CHANGE_la_valeur_pose_l_origine(banc):
    """Le cas de l'issue : un homonyme adopté comme raison sociale. La valeur remise
    survit dans `raison_sociale.origine`, posée par la plateforme — pas par l'agent."""
    st, etat = banc
    out = st.update_row("viviers", "r1", {"raison_sociale": "ACME HOLDING"})
    assert etat["lignes"]["r1"]["raison_sociale"] == {"valeur": "ACME HOLDING",
                                                     "origine": "ACME"}
    assert out["raison_sociale"] == "ACME HOLDING"          # le nom nu = la valeur
    assert out["raison_sociale.origine"] == "ACME"          # servie à plat


def test_l_origine_n_est_JAMAIS_reecrite(banc):
    """Deuxième modification : l'origine reste la valeur remise, pas la première
    valeur de l'agent — c'est tout l'objet du cran."""
    st, etat = banc
    st.update_row("viviers", "r1", {"raison_sociale": "ACME HOLDING"})
    st.update_row("viviers", "r1", {"raison_sociale": "ACME GROUP"})
    assert etat["lignes"]["r1"]["raison_sociale"] == {"valeur": "ACME GROUP",
                                                     "origine": "ACME"}


def test_une_valeur_INCHANGEE_ne_pose_rien(banc):
    """Relire → repousser à l'identique n'est pas une modification : la colonne
    reste plate, aucune couche fantôme."""
    st, etat = banc
    st.update_row("viviers", "r1", {"raison_sociale": "ACME"})
    assert etat["lignes"]["r1"]["raison_sociale"] == "ACME"


def test_vide_a_l_origine_le_marqueur_tient_le_une_seule_fois(banc):
    """Le champ était VIDE quand l'agent l'a rempli : l'origine est `""` — le
    marqueur « rien n'avait été remis ». Sans lui, la deuxième écriture capturerait
    la première valeur de l'agent comme si elle venait du client."""
    st, etat = banc
    etat["lignes"]["r1"].pop("raison_sociale")
    st.update_row("viviers", "r1", {"raison_sociale": "ACME"})
    assert etat["lignes"]["r1"]["raison_sociale"] == {"valeur": "ACME", "origine": ""}
    st.update_row("viviers", "r1", {"raison_sociale": "ACME SA"})
    assert etat["lignes"]["r1"]["raison_sociale"] == {"valeur": "ACME SA", "origine": ""}


def test_effacer_la_valeur_garde_l_origine(banc):
    """`null` NOMMÉ efface la valeur (#407) — l'origine posée par le système
    survit, comme toute origine (« l'écriture ne touche que ce qu'elle nomme »)."""
    st, etat = banc
    st.update_row("viviers", "r1", {"raison_sociale": None})
    assert etat["lignes"]["r1"]["raison_sociale"] == {"origine": "ACME"}


def test_la_fusion_par_cle_pose_aussi(banc):
    """Le chemin d'un `data_write(row={siren: …})` sans `id` : fusion sur la clé
    métier, même règle."""
    st, etat = banc
    st.append_row("viviers", {"siren": "552081317", "raison_sociale": "ACME SA"})
    assert etat["lignes"]["r1"]["raison_sociale"] == {"valeur": "ACME SA",
                                                     "origine": "ACME"}


def test_le_lot_pose_aussi(banc):
    st, etat = banc
    st._write_rows_to_ns(7, [{"siren": "552081317", "raison_sociale": "ACME SA"}],
                         key="siren")
    assert etat["lignes"]["r1"]["raison_sociale"] == {"valeur": "ACME SA",
                                                     "origine": "ACME"}


def test_une_creation_ne_pose_rien(banc):
    """Créer n'est pas modifier : la valeur créée EST le point de départ."""
    st, etat = banc
    st.append_row("viviers", {"siren": "389256712", "raison_sociale": "NEUVE"})
    assert etat["creees"][0]["raison_sociale"] == "NEUVE"


# ── fermée à l'écriture ──────────────────────────────────────────────────────

@pytest.mark.parametrize("payload", [
    {"origine": "client"},                       # la couche seule
    {"valeur": "ACME SA", "origine": "ACME SA"},  # le geste exact de l'incident
    {"origine": None},                            # l'effacement
])
def test_ecrire_l_origine_d_un_champ_systeme_est_REFUSE_sur_la_ligne(banc, payload):
    st, etat = banc
    with pytest.raises(RowValidationError) as exc:
        st.update_row("viviers", "r1", {"raison_sociale": payload})
    msg = str(exc.value)
    assert "`raison_sociale.origine`" in msg and "posée par le système" in msg
    assert "rien n'a été écrit" in msg
    assert etat["maj"] == [] and etat["lignes"]["r1"]["raison_sociale"] == "ACME"


def test_le_refus_vaut_a_la_CREATION(banc):
    """Une origine posée à la création marquerait « déjà posée » avec la valeur de
    l'agent : c'est la porte de côté du défaut. Fermée aussi."""
    st, etat = banc
    with pytest.raises(RowValidationError, match="raison_sociale.origine"):
        st.append_row("viviers", {"siren": "389256712",
                                  "raison_sociale": {"valeur": "X", "origine": "moi"}})
    assert etat["creees"] == []


def test_le_lot_refuse_et_NOMME_la_ligne(banc):
    st, etat = banc
    with pytest.raises(RowValidationError) as exc:
        st._write_rows_to_ns(7, [{"siren": "552081317", "libre": "ok"},
                                 {"siren": "552081317",
                                  "raison_sociale": {"origine": "client"}}], key="siren")
    msg = str(exc.value)
    assert "ligne 2/2" in msg and "raison_sociale.origine" in msg
    assert etat["lignes"]["r1"]["libre"] == "ok"           # la 1ʳᵉ est passée


def test_une_couche_deja_ecrite_par_un_agent_reste_lue_telle_quelle(banc):
    """Compatibilité : les 40 fiches de la campagne portent une origine écrite par
    l'agent AVANT la pose du cran. Elle n'est ni réécrite ni effacée."""
    st, etat = banc
    etat["lignes"]["r1"]["raison_sociale"] = {"valeur": "ACME", "origine": "fichier client"}
    out = st.update_row("viviers", "r1", {"raison_sociale": "ACME SA"})
    assert etat["lignes"]["r1"]["raison_sociale"] == {"valeur": "ACME SA",
                                                     "origine": "fichier client"}
    assert out["raison_sociale.origine"] == "fichier client"


def test_hors_declaration_l_origine_s_ecrit_comme_avant(banc):
    """Le défaut ne bouge pas : sur `libre`, l'agent pose et efface l'origine."""
    st, etat = banc
    st.update_row("viviers", "r1", {"libre": {"valeur": "x", "origine": "moi"}})
    assert etat["lignes"]["r1"]["libre"] == {"valeur": "x", "origine": "moi"}


# ══ #606 — la colonne du fichier source ══════════════════════════════════════

def test_changer_une_colonne_readonly_est_REFUSE_en_nommant_ou_va_la_chose(banc):
    """Le geste de l'incident : l'agent « complète » l'adresse avec le registre."""
    st, etat = banc
    with pytest.raises(RowValidationError) as exc:
        st.update_row("viviers", "r1", {"adresse": "2 rue B"})
    msg = str(exc.value)
    assert "`adresse`" in msg and "non modifiable" in msg
    assert "`notes_verification`" in msg                    # où va la divergence
    assert exc.value.details == {"expected_column": "notes_verification"}
    assert etat["maj"] == [] and etat["lignes"]["r1"]["adresse"] == "1 rue A"


def test_sans_report_to_le_refus_le_DIT(banc):
    st, etat = banc
    with pytest.raises(RowValidationError) as exc:
        st.update_row("viviers", "r1", {"naf": "70.10Z"})
    assert "`naf`" in str(exc.value) and exc.value.details is None
    assert etat["lignes"]["r1"]["naf"] == "62.01Z"


def test_remplir_une_colonne_readonly_VIDE_est_refuse(banc):
    """La colonne est au client ; vide, elle reste vide. Une divergence se note
    ailleurs — c'est exactement « compléter avec ce que dit le registre »."""
    st, etat = banc
    etat["lignes"]["r1"].pop("naf")
    with pytest.raises(RowValidationError, match="`naf`"):
        st.update_row("viviers", "r1", {"naf": "62.01Z"})


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
    assert exc.value.details == {"expected_column": "notes_verification"}
    assert etat["lignes"]["r1"]["adresse"] == "1 rue A"


# ── ce que le cran ne ferme PAS, et c'est voulu ──────────────────────────────

def test_le_round_trip_a_l_identique_PASSE(banc):
    """Relire une ligne entière et la repousser est le geste normal (#390) : une
    valeur inchangée n'est pas une modification."""
    st, etat = banc
    st.update_row("viviers", "r1", dict(_LIGNE, libre="note"))
    assert etat["maj"] == ["r1"] and etat["lignes"]["r1"]["libre"] == "note"


def test_annoter_sans_toucher_la_valeur_PASSE(banc):
    st, etat = banc
    st.update_row("viviers", "r1", {"adresse": {"comment": "vérifiée au registre"}})
    assert etat["lignes"]["r1"]["adresse"] == {"valeur": "1 rue A",
                                              "comment": "vérifiée au registre"}


def test_un_vide_non_null_est_ecarte_AVANT_le_cran(banc):
    """`""` sur une valeur en place ne déplace rien (#608) : rien à refuser."""
    st, etat = banc
    st.update_row("viviers", "r1", {"adresse": "", "libre": "x"})
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


def test_la_pose_accepte_les_deux_crans():
    assert dsv2.validate_schema_def(_SCHEMA) == []
    assert dsv2.system_origin_fields(_SCHEMA) == {"raison_sociale"}
    assert dsv2.readonly_fields(_SCHEMA) == {"adresse": "notes_verification",
                                             "naf": None}


@pytest.mark.parametrize("field, attendu", [
    ({"key": "x", "origine": "agent"}, "system"),          # vocabulaire fermé
    ({"key": "x", "type": "json", "origine": "system"}, "json"),
    ({"key": "x", "type": "list", "of": {"type": "text"}, "origine": "system"}, "list"),
    ({"key": "x", "readonly": True, "origine": "system"}, "readonly"),
    ({"key": "x", "readonly": "oui"}, "readonly"),
    ({"key": "x", "report_to": "y"}, "readonly"),          # report_to sans le cran
    ({"key": "x", "readonly": True, "report_to": "absente"}, "absente"),
    ({"key": "x", "readonly": True, "report_to": "x"}, "elle-même"),
])
def test_une_declaration_qui_ne_peut_pas_s_appliquer_se_refuse_a_la_POSE(field, attendu):
    """Jamais acceptée-inerte (#347) : le refus nomme l'attendu."""
    errs = _errs([field, {"key": "y"}])
    assert errs and any(attendu in e for e in errs), errs


def test_report_to_vers_une_colonne_readonly_est_refuse():
    """Une colonne de report qu'on ne peut pas écrire serait un piège insatisfiable."""
    errs = _errs([{"key": "x", "readonly": True, "report_to": "y"},
                  {"key": "y", "readonly": True}])
    assert errs and any("`y`" in e and "readonly" in e for e in errs)


def test_les_crans_ne_se_posent_qu_au_PREMIER_niveau():
    errs = _errs([{"key": "o", "type": "object",
                   "fields": [{"key": "a", "readonly": True},
                              {"key": "b", "origine": "system"}]}])
    assert len([e for e in errs if "premier niveau" in e]) == 2


def test_les_crans_ne_se_posent_pas_sur_une_cible_de_couche():
    errs = _errs([{"key": "x"}, {"key": "x.comment", "readonly": True}])
    assert errs and any("COLONNE" in e for e in errs)


def test_le_retrait_est_une_valeur_nulle():
    """`data_patch_schema(fields=[{key, readonly: null}])` lève le cran sans
    réécrire : `null` est une absence, pour le lecteur comme pour la pose."""
    schema = {"fields": [{"key": "a", "readonly": None, "report_to": None},
                         {"key": "b", "origine": None}]}
    assert dsv2.validate_schema_def(schema) == []
    assert dsv2.readonly_fields(schema) == {} and dsv2.system_origin_fields(schema) == set()


def test_cette_version_ANNONCE_les_deux_crans():
    dsv2.reset_enforced_keys()
    try:
        assert {"readonly", "origine"} <= set(dsv2.enforced_keys())
    finally:
        dsv2.reset_enforced_keys()


def test_les_clefs_sont_INTERPRETEES():
    """Sans quoi `data_set_schema` avertirait « clé non lue » sur un cran qui mord."""
    assert dsv2.unknown_declaration_keys(_SCHEMA) == []


def test_la_face_REST_garde_son_code_et_porte_la_colonne_attendue():
    from oto_mcp.capabilities.datastore.rows import _write_refusal

    refus = _write_refusal(RowValidationError(["x"], details={"expected_column": "n"}))
    assert refus.status == 400 and refus.code == "row_invalid"
    assert refus.details == {"expected_column": "n"}
