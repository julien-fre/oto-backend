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
    le `notices` de #390 la signale."""
    st, etat = banc
    row = st.append_row("viviers", {"raison_sociale": "ACME"})
    assert row["raison_sociale"] == "ACME" and len(etat["creees"]) == 1
    assert any("siren" in n for n in st.off_schema_report()["notices"])


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
