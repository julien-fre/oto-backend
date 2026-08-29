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
           {"key": "adresse", "type": "text", "readonly": True},
           {"key": "naf", "type": "text", "readonly": True, "origine": "system"},
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
                                                "link": "https://x", "origine": "fichier"}})
    assert etat["lignes"]["r1"]["adresse"] == {"valeur": "1 rue A", "origine": "fichier",
                                              "comment": "registre — 2 rue B",
                                              "link": "https://x"}


def test_readonly_ET_origine_systeme_se_combinent(banc):
    """`naf` : valeur verrouillée, ET sa couche d'origine fermée à l'appelant. La
    pose par le système n'a jamais lieu tant que la valeur ne bouge pas — et elle ne
    bouge pas ; le jour où le propriétaire lève `readonly`, le cran d'origine joue."""
    st, etat = banc
    with pytest.raises(RowValidationError, match="`naf`"):
        st.update_row("viviers", "r1", {"naf": "70.10Z"})
    with pytest.raises(RowValidationError, match="naf.origine"):
        st.update_row("viviers", "r1", {"naf": {"origine": "moi"}})
    st.update_row("viviers", "r1", {"naf": {"comment": "registre — 70.10Z"}})
    assert etat["lignes"]["r1"]["naf"] == {"valeur": "62.01Z",
                                          "comment": "registre — 70.10Z"}


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
    assert exc.value.details == {"expected_column": "adresse.comment"}
    assert etat["lignes"]["r1"]["adresse"] == "1 rue A"


# ── ce que le cran ne ferme PAS, et c'est voulu ──────────────────────────────

def test_la_valeur_IDENTIQUE_d_une_readonly_est_un_no_op_qui_garde_le_comment(banc):
    """29/08/2026, l'erreur d'une heure : #623 refusait l'identique sur `readonly`.
    Huit charges d'écriture échantillonnées sur le terrain, toutes : le geste dominant
    RÉÉMET la fiche entière, valeurs verrouillées comprises. « Identique compris »
    aurait arrêté la campagne — une flotte à l'arrêt, pas un garde-fou. L'identique
    n'est pas une écriture : no-op silencieux, couches préservées. Le refus ne porte
    que sur un CHANGEMENT."""
    st, etat = banc
    st.update_row("viviers", "r1", {"adresse": {"comment": "registre — 2 rue B"}})
    st.update_row("viviers", "r1", {"adresse": "1 rue A"})
    st.update_row("viviers", "r1", {"adresse": {"valeur": "1 rue A"}})
    assert etat["lignes"]["r1"]["adresse"] == {"valeur": "1 rue A",
                                              "comment": "registre — 2 rue B"}
    assert st.off_schema_report() == {}                     # silencieux


def test_le_round_trip_ENTIER_sur_une_readonly_PASSE(banc):
    """Relire → repousser la ligne entière porte la valeur nue de la colonne source,
    identique : la fiche passe, rien n'est perdu."""
    st, etat = banc
    st.update_row("viviers", "r1", {"adresse": {"comment": "registre — 2 rue B"}})
    st.update_row("viviers", "r1", dict(_LIGNE, libre="note"))
    assert etat["lignes"]["r1"]["libre"] == "note"
    assert etat["lignes"]["r1"]["adresse"]["comment"] == "registre — 2 rue B"


def test_le_lot_accepte_l_identique_sur_une_readonly_et_garde_le_comment(banc):
    st, etat = banc
    st.update_row("viviers", "r1", {"adresse": {"comment": "registre — 2 rue B"}})
    out = st._write_rows_to_ns(7, [{"siren": "552081317", "adresse": "1 rue A",
                                    "libre": "x"}], key="siren")
    assert out["updated"] == 1
    assert etat["lignes"]["r1"]["adresse"]["comment"] == "registre — 2 rue B"


def test_comment_et_link_accompagnant_une_valeur_identique_sont_ECRITS(banc):
    """Le geste utile : `{"valeur": <identique>, "comment": "…"}` annote sans toucher
    la valeur — le comment est écrit, le link existant reste (la valeur n'a pas
    changé, rien ne tombe)."""
    st, etat = banc
    st.update_row("viviers", "r1", {"adresse": {"link": "https://l"}})
    st.update_row("viviers", "r1", {"adresse": {"valeur": "1 rue A",
                                                "comment": "registre — 2 rue B"}})
    assert etat["lignes"]["r1"]["adresse"] == {"valeur": "1 rue A", "link": "https://l",
                                              "comment": "registre — 2 rue B"}


# ── #586 : une `.origine` égale à ce que le système poserait est un no-op ─────

def test_ecrire_l_origine_EGALE_a_la_valeur_en_place_est_acceptee(banc):
    """Le geste dominant du terrain sur une colonne système :
    `{"valeur": <identique>, "origine": <la même>}` — c'est exactement ce que le
    système poserait. Accepté ; rien de perdu, rien de refusé."""
    st, etat = banc
    st.update_row("viviers", "r1", {"raison_sociale": {"comment": "c"}})
    st.update_row("viviers", "r1", {"raison_sociale": {"valeur": "ACME", "origine": "ACME"}})
    assert etat["lignes"]["r1"]["raison_sociale"] == {"valeur": "ACME", "origine": "ACME",
                                                     "comment": "c"}
    # Puis l'agent modifie en réémettant l'origine STOCKÉE : accepté, jamais réécrite.
    st.update_row("viviers", "r1", {"raison_sociale": {"valeur": "ACME HOLDING",
                                                       "origine": "ACME"}})
    assert etat["lignes"]["r1"]["raison_sociale"] == {"valeur": "ACME HOLDING",
                                                     "origine": "ACME"}
    # Une origine DIFFÉRENTE reste refusée, avec le message existant.
    with pytest.raises(RowValidationError, match="raison_sociale.origine"):
        st.update_row("viviers", "r1", {"raison_sociale": {"valeur": "ACME GROUP",
                                                           "origine": "ACME GROUP"}})
    assert etat["lignes"]["r1"]["raison_sociale"]["valeur"] == "ACME HOLDING"


def test_a_la_creation_une_origine_egale_a_la_valeur_est_acceptee(banc):
    st, etat = banc
    st.append_row("viviers", {"siren": "389256712",
                              "raison_sociale": {"valeur": "X", "origine": "X"}})
    assert etat["creees"][0]["raison_sociale"] == {"valeur": "X", "origine": "X"}


# ── le substrat : une valeur nue IDENTIQUE est un no-op qui garde les couches ──

def test_une_valeur_nue_identique_PRESERVE_les_couches(banc):
    """Le round-trip relire → repousser (#390) ne doit jamais détruire un `comment`,
    un `link` ou une `origine` : la lecture sert la valeur nue (`flat_layers` met les
    couches à côté, sous `champ.couche`), donc un round-trip fidèle repousse la valeur
    nue — et la règle « réécrire la valeur emporte ses couches » la détruisait. Une
    valeur IDENTIQUE n'est pas une réécriture : rien ne bouge, rien ne tombe. Sur
    toute colonne, readonly ou non — ici `libre`, sur les trois chemins."""
    st, etat = banc
    st.update_row("viviers", "r1", {"libre": {"valeur": "x", "comment": "c",
                                              "link": "https://l", "origine": "o"}})
    couches = {"valeur": "x", "comment": "c", "link": "https://l", "origine": "o"}
    st.update_row("viviers", "r1", {"libre": "x"})
    assert etat["lignes"]["r1"]["libre"] == couches
    st.append_row("viviers", {"siren": "552081317", "libre": "x"})
    assert etat["lignes"]["r1"]["libre"] == couches
    st._write_rows_to_ns(7, [{"siren": "552081317", "libre": "x"}], key="siren")
    assert etat["lignes"]["r1"]["libre"] == couches
    # Une valeur DIFFÉRENTE, elle, reste une réécriture : comment/link tombent,
    # origine survit — la règle de #322/#326, inchangée.
    st.update_row("viviers", "r1", {"libre": "y"})
    assert etat["lignes"]["r1"]["libre"] == {"valeur": "y", "origine": "o"}


# ── la colonne-clé : de l'adressage, pas une écriture de valeur ──────────────

def test_la_pose_refuse_readonly_sur_la_cle_metier():
    """La clé figure dans CHAQUE écriture pour désigner la ligne : `readonly` dessus,
    identique refusé, fermerait toutes les écritures du tableau. Elle se protège par
    `key_required` (une autre valeur est une autre ligne), pas par `readonly`."""
    errs = dsv2.validate_schema_def(
        {"key": "siren", "fields": [{"key": "siren", "readonly": True}]})
    assert errs and any("clé métier" in e and "key_required" in e for e in errs), errs


def test_sur_un_schema_legacy_la_cle_identique_est_de_l_ADRESSAGE(banc):
    """Un schéma déjà en base qui porterait `readonly` sur la clé (posé avant ce
    garde, ou « complété » dans six mois) ne ferme pas le tableau : la valeur de clé
    IDENTIQUE passe comme toute valeur identique — c'est l'adresse de la ligne —, une
    valeur DIFFÉRENTE reste refusée (sur la ligne visée, c'est une réécriture)."""
    st, etat = banc
    etat["schema"] = {"key": "siren",
                      "fields": [{"key": "siren", "readonly": True},
                                 {"key": "adresse", "readonly": True},
                                 {"key": "libre"}]}
    st.append_row("viviers", {"siren": "552081317", "libre": "x",
                              "adresse": {"comment": "registre — 2 rue B"}})
    st.update_row("viviers", "r1", {"siren": "552081317", "libre": "y"})
    assert etat["lignes"]["r1"]["libre"] == "y" and etat["creees"] == []
    assert etat["lignes"]["r1"]["adresse"]["comment"] == "registre — 2 rue B"
    with pytest.raises(RowValidationError, match="`siren`"):
        st.update_row("viviers", "r1", {"siren": "999999999"})
    with pytest.raises(RowValidationError, match="`adresse`"):
        st.update_row("viviers", "r1", {"siren": "552081317", "adresse": "1 rue A"})


def test_identique_se_juge_au_TYPE_pres():
    """`0` et `False` sont égaux pour Python, pas pour une colonne : écrire `False`
    sur `0` est une réécriture, pas un no-op."""
    from oto_mcp.datastore.columns import _merge_column
    assert _merge_column({"valeur": 0, "comment": "c"}, False) is False   # couches tombées, scalaire nu
    assert _merge_column({"valeur": 0, "comment": "c"}, 0) == {"valeur": 0, "comment": "c"}
    assert _merge_column("x", "x") == "x"
    assert _merge_column(None, None) is None


def test_annoter_sans_toucher_la_valeur_PASSE(banc):
    """`adresse.comment` seul : la forme que quatre fiches sur quatorze avaient déjà
    trouvée — en écrasant la valeur en plus. Ici la valeur reste."""
    st, etat = banc
    st.update_row("viviers", "r1", {"adresse": {"comment": "registre — 20 B AV. HUGO"}})
    assert etat["lignes"]["r1"]["adresse"] == {"valeur": "1 rue A",
                                              "comment": "registre — 20 B AV. HUGO"}


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
    assert dsv2.system_origin_fields(_SCHEMA) == {"raison_sociale", "naf"}
    assert dsv2.readonly_fields(_SCHEMA) == {"adresse", "naf"}


@pytest.mark.parametrize("field, attendu", [
    ({"key": "x", "origine": "agent"}, "system"),          # vocabulaire fermé
    ({"key": "x", "type": "json", "origine": "system"}, "json"),
    ({"key": "x", "type": "list", "of": {"type": "text"}, "origine": "system"}, "list"),
    ({"key": "x", "readonly": "oui"}, "readonly"),
])
def test_une_declaration_qui_ne_peut_pas_s_appliquer_se_refuse_a_la_POSE(field, attendu):
    """Jamais acceptée-inerte (#347) : le refus nomme l'attendu."""
    errs = _errs([field, {"key": "y"}])
    assert errs and any(attendu in e for e in errs), errs


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
    schema = {"fields": [{"key": "a", "readonly": None}, {"key": "b", "origine": None}]}
    assert dsv2.validate_schema_def(schema) == []
    assert dsv2.readonly_fields(schema) == set() == dsv2.system_origin_fields(schema)


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
