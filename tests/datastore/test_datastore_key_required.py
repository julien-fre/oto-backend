"""`key_required` — sur un tableau à clé métier, une écriture VISE une ligne (#516).

Le défaut que ce cran ferme : la clé métier est déclarée, l'agent écrit sans elle (ou
avec une valeur que personne ne porte), et la plateforme **crée** une ligne. Le seul
signal était un `notices` dans la réponse — un texte qu'un agent ne consomme pas. Un
refus nommé, lui, est lu par construction.

**Deux incidents datés, rejoués ici** :

1. **28/08/2026, 17:06:49 UTC** — 8 911 lignes pour 8 910 attendues sur un tableau de
   production : la ligne `01a04956-…` est née SANS `siren`, contenu bon, doublon
   parfait d'une ligne existante. Rien ne la rapprochera jamais.
2. **29/08/2026, 03:20:56 et 03:21:30 UTC** — deux agents refusés sur un identifiant
   INVENTÉ (`670d56b3-0628-436a-994d-16a60b04854b`, `6723d393f9b0481d9b83b2b2` : deux
   conventions étrangères, aucune n'a la forme d'un `_id` d'ici) réécrivent SANS
   identifiant, avec un SIREN. Les deux SIREN n'existent ni au registre ni dans le
   tableau : deux lignes créées, deux entreprises fictives portant « registre — lu via
   fr_get ». **Une clé n'a rien empêché puisqu'elle était inconnue** — ce que refuse
   `key_required`, et rien d'autre ne le pouvait.

⚠️ **Le défaut ne change pas** : sans `key_required`, une création reste possible et
reste SIGNALÉE (`notices`, #390) — un tableau se remplit souvent avant d'avoir sa clé,
et le cran est une déclaration de son propriétaire, jamais une politique de plateforme.
"""
from __future__ import annotations

import pytest

from oto_mcp.datastore import core as dsm
from oto_mcp.datastore import schema as dsv2
from oto_mcp.datastore.errors import BusinessKeyRequired


_FIELDS = [{"key": "siren", "type": "text"},
           {"key": "raison_sociale", "type": "text"}]
# Le tableau de l'incident : clé métier déclarée, régime de création OUVERT (le défaut).
_OUVERT = {"key": "siren", "fields": _FIELDS}
# Le même, fermé : toute écriture vise une ligne existante.
_FERME = {"key": "siren", "key_required": True, "fields": _FIELDS}


def _fake_merge_locked(rows):
    """Stub du seam verrou de ligne (#197), comme `test_datastore_business_key`."""
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
    """Un tableau `viviers` peuplé d'UNE ligne (siren 552081317), schéma commutable.

    Rend `(store, etat)` — `etat["schema"]` bascule le régime, `etat["creees"]` relève
    les insertions réellement parties en base : c'est ce compteur qui distingue « la
    ligne n'a pas été créée » de « on a rendu une erreur après l'avoir créée ».
    """
    st = dsm.DatastorePg("u", acting_org=35)
    etat = {"schema": _OUVERT,
            "lignes": {"r-existante": {"siren": "552081317", "raison_sociale": "ACME"}},
            "creees": []}
    monkeypatch.setattr(st, "_resolve", lambda ns, write=False: 7)
    monkeypatch.setattr(dsm.db, "get_datastore_by_id",
                        lambda ns_id: {"id": ns_id, "datastore": "viviers",
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

    monkeypatch.setattr(dsm.db, "datastore_find_row_id_by_key", find)
    monkeypatch.setattr(dsm.db, "datastore_get_row", get_row)
    monkeypatch.setattr(dsm.db, "datastore_insert_row", insert)
    monkeypatch.setattr(dsm.db, "datastore_merge_row_locked",
                        _fake_merge_locked(etat["lignes"]))
    return st, etat


# ── le refus, sur les deux formes de « ça ne vise aucune ligne » ──────────────

def test_incident_1_une_ligne_SANS_la_cle_est_refusee(banc):
    """28/08 : la 8 911ᵉ ligne, née sans `siren` sur un tableau qui en déclare un."""
    st, etat = banc
    etat["schema"] = _FERME
    with pytest.raises(BusinessKeyRequired) as exc:
        st.append_row("viviers", {"raison_sociale": "ACME"})
    assert etat["creees"] == []                    # rien n'a atterri en base
    assert "siren" in str(exc.value)               # la clé est NOMMÉE
    assert "data_write(id=" in str(exc.value)      # le geste est NOMMÉ


def test_incident_2_un_SIREN_que_personne_ne_porte_est_refuse(banc):
    """29/08 : la clé était là, mais elle ne désignait rien — donc elle n'a rien
    empêché. C'est cette porte-là qui a fabriqué deux entreprises fictives."""
    st, etat = banc
    etat["schema"] = _FERME
    with pytest.raises(BusinessKeyRequired) as exc:
        st.append_row("viviers", {"siren": "349763571",
                                  "raison_sociale": "Société qui n'existe pas"})
    assert etat["creees"] == []
    msg = str(exc.value)
    assert "349763571" in msg and "siren" in msg   # la valeur refusée ET la clé


def test_incident_2_l_identifiant_INVENTE_ne_cree_toujours_rien(banc):
    """Le premier geste des deux agents : un `_id` d'une convention étrangère. Il
    était DÉJÀ refusé (#354) — on le fige, parce que c'est la moitié du chemin qui a
    mené à la réécriture sans identifiant."""
    st, etat = banc
    etat["schema"] = _FERME
    with pytest.raises(ValueError, match="ne correspond à aucune ligne"):
        st.append_row("viviers", {"_id": "6723d393f9b0481d9b83b2b2", "siren": "349763571"})
    assert etat["creees"] == []


# ── ce qui NE change pas ─────────────────────────────────────────────────────

def test_le_DEFAUT_cree_et_signale(banc):
    """Sans le cran, le comportement du 28/08 est intact : la ligne est écrite, et
    le relevé de #390 la signale.

    ⚠️ Ce relevé a changé de PLACE le 09/09/2026 — de `notices` vers
    `non_rapprochable`, au premier niveau. Son texte était exact et il n'a rien
    empêché : servi dix fois en une soirée à une campagne qui lisait le statut et
    l'`_id`. Un fait qui contredit le succès annoncé juste à côté ne se range pas dans
    une liste qui sonne comme « informations diverses »."""
    st, etat = banc
    row = st.append_row("viviers", {"raison_sociale": "ACME"})
    assert row["raison_sociale"] == "ACME" and len(etat["creees"]) == 1
    assert st.off_schema_report()["non_rapprochable"] == ["siren"]


def test_le_DEFAUT_cree_sur_une_cle_inconnue(banc):
    """Une clé qu'aucune ligne ne porte reste une CRÉATION en régime ouvert : c'est
    ainsi qu'un tableau se peuple."""
    st, etat = banc
    st.append_row("viviers", {"siren": "349763571"})
    assert len(etat["creees"]) == 1


def test_la_fusion_par_cle_est_INCHANGEE_en_ferme(banc):
    """Le cran ne touche pas au chemin nominal : une clé qui DÉSIGNE une ligne
    fusionne, en ligne seule (pas seulement en lot)."""
    st, etat = banc
    etat["schema"] = _FERME
    out = st.append_row("viviers", {"siren": "552081317", "raison_sociale": "ACME SA"})
    assert out["_id"] == "r-existante"
    assert etat["lignes"]["r-existante"]["raison_sociale"] == "ACME SA"
    assert etat["creees"] == []


def test_un_tableau_SANS_cle_declaree_ignore_le_cran(banc):
    """`key_required` sans `key` n'a pas de sens — et se refuse à la POSE, pas à
    l'écriture (cf. `test_la_pose_refuse_le_cran_sans_cle`). Un schéma déjà en base
    qui le porterait ne doit pas faire exploser les écritures."""
    st, etat = banc
    etat["schema"] = {"key_required": True, "fields": _FIELDS}
    st.append_row("viviers", {"raison_sociale": "ACME"})
    assert len(etat["creees"]) == 1


# ── le second chemin : le LOT ────────────────────────────────────────────────

def test_le_lot_refuse_AUSSI_et_nomme_la_ligne(banc):
    """Un lot est le second chemin de création, et le plus volumineux : 1 778 lignes
    tournaient sur le fichier de la cliente. Le refus y nomme la ligne fautive et dit
    ce qui est déjà écrit (#412) — un lot n'est pas atomique."""
    st, etat = banc
    etat["schema"] = _FERME
    with pytest.raises(BusinessKeyRequired) as exc:
        st._write_rows_to_ns(7, [{"siren": "552081317", "raison_sociale": "ACME SA"},
                                 {"raison_sociale": "sans clé"}], key="siren")
    msg = str(exc.value)
    assert "ligne 2/2" in msg and "1 ligne déjà écrite" in msg
    assert etat["creees"] == []                    # la 1ʳᵉ a FUSIONNÉ, rien créé
    assert etat["lignes"]["r-existante"]["raison_sociale"] == "ACME SA"


def test_le_lot_refuse_une_cle_que_personne_ne_porte(banc):
    st, etat = banc
    etat["schema"] = _FERME
    with pytest.raises(BusinessKeyRequired):
        st._write_rows_to_ns(7, [{"siren": "389256712"}], key="siren")
    assert etat["creees"] == []


def test_le_lot_en_regime_OUVERT_cree_comme_avant(banc):
    st, etat = banc
    out = st._write_rows_to_ns(7, [{"siren": "389256712"}], key="siren")
    assert out["inserted"] == 1 and len(etat["creees"]) == 1


# ── la déclaration ───────────────────────────────────────────────────────────

def test_la_pose_refuse_le_cran_sans_cle():
    """Même parti que `max_claims` sans `abandon_state` : une garde qui ne peut pas
    s'appliquer se refuse là où le tableau se déclare, devant celui qui peut corriger
    — pas à la première écriture d'une campagne déjà lancée."""
    errs = dsv2.validate_schema_def({"key_required": True, "fields": _FIELDS})
    assert errs and any("key" in e for e in errs)


def test_la_pose_accepte_le_cran_avec_la_cle():
    assert dsv2.validate_schema_def(_FERME) == []


def test_cette_version_ANNONCE_le_cran():
    """`enforced` dit ce que cette version EXÉCUTE (#389) : un client doit pouvoir
    vérifier, contre le serveur qui lui répond, que le cran qu'il pose mordra."""
    dsv2.reset_enforced_keys()
    try:
        assert "key_required" in dsv2.enforced_keys()
    finally:
        dsv2.reset_enforced_keys()


# ── le refus est ACTIONNABLE sur les deux faces ──────────────────────────────

def test_la_face_REST_rend_un_code_NOMME():
    """400 `business_key_required`, pas le `invalid_row_input` générique : un refus
    qui se distingue est un refus sur lequel un front peut agir."""
    from oto_mcp.capabilities.datastore.rows import _write_refusal

    refus = _write_refusal(BusinessKeyRequired("peu importe", key="siren"))
    assert refus.status == 400 and refus.code == "business_key_required"


def test_le_refus_est_une_ValueError():
    """La face MCP traduit `ValueError` en INVALID_PARAMS actionnable : en dériver
    est ce qui évite un « Erreur interne du serveur » sur une faute d'appel."""
    assert issubclass(BusinessKeyRequired, ValueError)


# ── #527 : la clé métier RÉÉCRITE par un patch par identifiant ────────────────
#
# Mesuré le 28/08/2026 (table jetable, `key: siren`, index unique en place) : id juste +
# siren INEXISTANT (faute de frappe) → accepté sans un mot. La ligne existe toujours, le
# compte ne bouge pas, mais elle porte un numéro d'entreprise qui n'existe pas et plus
# rien ne la rapproche du fichier client. Aucune garde de comptage ne le voit ; et,
# contrairement à la création sans clé (`notices`), ce cas ne recevait RIEN. Une ligne
# rendue orpheline qu'on ne retrouvera plus est plus grave qu'une orpheline créée.
#
# Deux crans, comme #516 : le tableau OUVERT le SIGNALE (corriger un SIREN mal saisi à
# l'import est parfois légitime) ; le tableau FERMÉ (`key_required`) le REFUSE — et la
# sortie passe par le SCHÉMA, jamais par un paramètre « forcer » sur l'écriture.

def test_527_cas_1_id_juste_et_siren_juste_met_a_jour_sans_bruit(banc):
    st, etat = banc
    etat["schema"] = _FERME
    st.update_row("viviers", "r-existante", {"siren": "552081317", "raison_sociale": "ACME SA"})
    assert etat["lignes"]["r-existante"]["raison_sociale"] == "ACME SA"
    assert not any("clé métier" in n for n in st.off_notices)


def test_527_cas_2_id_faux_est_refuse_ligne_introuvable(banc):
    st, etat = banc
    with pytest.raises(dsm.RowNotFound):
        st.update_row("viviers", "r-inexistante", {"siren": "552081317"})
    assert etat["creees"] == []


def test_527_cas_4_ferme_un_siren_inexistant_est_refuse_et_rien_n_est_ecrit(banc):
    """Le cas silencieux du 28/08, sur un tableau qui a déclaré `key_required`."""
    st, etat = banc
    etat["schema"] = _FERME
    with pytest.raises(ValueError) as exc:
        st.update_row("viviers", "r-existante", {"siren": "349763571"})
    msg = str(exc.value)
    assert "552081317" in msg and "349763571" in msg, "l'ancienne ET la nouvelle valeur"
    assert "siren" in msg
    assert "key_required" in msg, "la sortie passe par le schéma, pas par un « forcer »"
    assert etat["lignes"]["r-existante"]["siren"] == "552081317", "rien n'est écrit"


def test_527_cas_4_ouvert_un_siren_inexistant_passe_mais_est_dit(banc):
    """Le tableau ouvert reste ouvert : corriger une clé mal saisie est légitime. Mais
    plus en silence — la réponse porte l'ancienne et la nouvelle valeur."""
    st, etat = banc
    etat["schema"] = _OUVERT
    st.update_row("viviers", "r-existante", {"siren": "349763571"})
    assert etat["lignes"]["r-existante"]["siren"] == "349763571"
    dits = [n for n in st.off_notices if "clé métier" in n]
    assert len(dits) == 1, st.off_notices
    assert "552081317" in dits[0] and "349763571" in dits[0] and "r-existante" in dits[0]


def test_527_une_cle_qui_n_etait_pas_posee_n_est_pas_une_reecriture(banc):
    """Poser la clé d'une ligne qui n'en avait pas EST le geste légitime (la ligne
    « non rapprochable » qu'on répare) — ni refus ni notice, même fermé."""
    st, etat = banc
    etat["schema"] = _FERME
    etat["lignes"]["r-sans-cle"] = {"raison_sociale": "SANS CLE"}
    st.update_row("viviers", "r-sans-cle", {"siren": "349763571"})
    assert etat["lignes"]["r-sans-cle"]["siren"] == "349763571"
    assert not any("clé métier" in n for n in st.off_notices)


def test_527_la_cle_annotee_est_jugee_sur_sa_valeur(banc):
    """Une clé métier annotée est la MÊME identité qu'une clé nue (cf. `lots.py`) :
    enrichir sa provenance sans changer sa valeur n'est pas une réécriture."""
    st, etat = banc
    etat["schema"] = _FERME
    st.update_row("viviers", "r-existante",
                  {"siren": {"valeur": "552081317", "comment": "registre"}})
    assert not any("clé métier" in n for n in st.off_notices)


# ── oto#151 §3 : le refus DISTINGUE la clé absente de la clé inconnue ─────────
#
# Un client REST pur a reçu « la clé métier n'est pas renseignée » pour une clé qu'il
# venait d'envoyer, et en a conclu qu'il fallait chercher ailleurs. Les deux cas
# tombaient sur le même code, sans `details` : un front ne pouvait les séparer qu'en
# reparsant la phrase. Le refus porte désormais `details` — la clé, si l'écriture la
# portait, la valeur refusée — et la charge à renvoyer (`a_renvoyer`, oto#135).

_FERME_MOTIF = {"key": "siren", "key_required": True,
                "fields": [{"key": "siren", "type": "text", "max_length": 9,
                            "pattern": r"^\d{9}$"},
                           {"key": "code", "type": "text"},
                           {"key": "raison_sociale", "type": "text"}]}
_GABARIT_SIREN = r"<texte, ≤ 9 caractères, motif ^\d{9}$>"


def test_151_la_cle_ABSENTE_est_dite_absente_et_structuree(banc):
    st, etat = banc
    etat["schema"] = _FERME_MOTIF
    with pytest.raises(BusinessKeyRequired) as exc:
        st.append_row("viviers", {"raison_sociale": "ACME"})
    assert exc.value.details == {"key": "siren", "cle_portee": False,
                                 "a_renvoyer": {"siren": _GABARIT_SIREN}}
    assert str(exc.value).startswith("cette écriture ne porte pas `siren`")


def test_151_la_cle_PORTEE_mais_inconnue_est_dite_portee(banc):
    st, etat = banc
    etat["schema"] = _FERME_MOTIF
    with pytest.raises(BusinessKeyRequired) as exc:
        st.append_row("viviers", {"siren": {"valeur": "349763571", "comment": "fichier"}})
    assert exc.value.details == {"key": "siren", "cle_portee": True,
                                 "valeur": "349763571",
                                 "a_renvoyer": {"siren": _GABARIT_SIREN}}
    msg = str(exc.value)
    assert msg.startswith("cette écriture porte `siren` = '349763571'")
    assert "n'est pas renseign" not in msg


def test_151_le_lot_garde_les_details_en_nommant_la_ligne(banc):
    st, etat = banc
    etat["schema"] = _FERME_MOTIF
    with pytest.raises(BusinessKeyRequired) as exc:
        st._write_rows_to_ns(7, [{"siren": "552081317"}, {"siren": "389256712"}],
                             key="siren")
    assert "ligne 2/2" in str(exc.value)
    assert exc.value.details["cle_portee"] is True
    assert exc.value.details["valeur"] == "389256712"


def test_151_le_lot_qui_dedoublonne_sur_une_AUTRE_colonne_le_dit(banc):
    """Le lot porte une clé — celle de `key=` — mais pas la clé DÉCLARÉE, seule jugée
    par le cran. « `siren` n'est pas renseigné » laissait croire la sienne ignorée."""
    st, etat = banc
    etat["schema"] = _FERME_MOTIF
    with pytest.raises(BusinessKeyRequired) as exc:
        st._write_rows_to_ns(7, [{"code": "C1", "raison_sociale": "X"}], key="code")
    assert exc.value.details["cle_portee"] is False
    assert exc.value.details["cle_du_lot"] == "code"
    msg = str(exc.value)
    assert "`code`" in msg and "clé DÉCLARÉE" in msg


def test_151_un_champ_nomme_key_dans_la_ligne_est_nomme(banc):
    """Le geste du rapport : `key` posé dans le corps pour désigner la clé. Sur
    l'écriture d'une ligne, le corps EST la ligne : `key` y est une colonne."""
    st, etat = banc
    etat["schema"] = _FERME_MOTIF
    with pytest.raises(BusinessKeyRequired) as exc:
        st.append_row("viviers", {"key": "siren", "raison_sociale": "ACME"})
    assert "`key` est lu comme une COLONNE" in str(exc.value)


def test_151_la_face_REST_rend_les_details():
    from oto_mcp.capabilities.datastore.rows import _write_refusal

    details = {"key": "siren", "cle_portee": False, "a_renvoyer": {"siren": "<texte>"}}
    refus = _write_refusal(BusinessKeyRequired("x", key="siren", details=details))
    assert refus.code == "business_key_required" and refus.details == details
