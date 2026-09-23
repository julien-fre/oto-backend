"""Le vide est une valeur (oto#140, jalon J2) — le préavis daté, et `null` qui démarque.

Deux moitiés :

- **le préavis** : `""` / `[]` sur une valeur en place est encore écarté (#608), ou refusé
  quand il est tout le geste (#724) ; rien ne change à l'écriture, mais la réponse — et le
  refus — annoncent la date à partir de laquelle ce vide REMPLACERA la valeur ;
- **`null` retire le vide assumé** : `@clear` sera refusé, `null` doit donc effacer une case
  marquée `@empty`, marqueur compris. Un `null` sur une case vraiment vide reste sans effet
  (oto#182).
"""
from __future__ import annotations

import inspect
import pathlib
import uuid

import pytest

from oto_mcp.datastore import couches as dsl
from oto_mcp.datastore import vide_remplace as vr
from oto_mcp.datastore.champs_reserves import _en_francais
from oto_mcp.datastore.columns import refuser_geste_sans_effet, sans_les_nulls_sans_effet

QUAND = _en_francais(vr.VIDE_REMPLACE_LE)
MARQUEE = {"valeur": "", dsl.VIDE_ASSUME: True}
_SCHEMA = {"fields": [{"key": k, "type": "text"} for k in ("raison", "fonction")]}


# ── le texte, sans base ──────────────────────────────────────────────────────

def test_la_date_est_le_6_octobre_et_le_texte_en_derive():
    assert vr.VIDE_REMPLACE_LE.isoformat() == "2026-10-06"
    t = vr.avertissement(["contacts", "site_web"])
    assert QUAND == "6 octobre 2026" and f"À partir du {QUAND}" in t
    assert "`\"\"` / `[]` REMPLACERA la valeur en place de `contacts`, `site_web`" in t
    assert "pour la garder, omets la colonne" in t
    assert vr.avertissement([]) is None


def test_seuls_chaine_et_liste_vides_sont_annonces():
    ecartes = [{"champ": c} for c in ("a", "b", "c", "d")]
    corps = {"a": "", "b": {"valeur": [], "origine": "x"}, "c": {}, "d": {"valeur": {}}}
    assert vr.colonnes_annoncees(corps, ecartes) == ["a", "b"]
    assert vr.annonce(corps, []) is None


def test_le_refus_sans_effet_porte_l_annonce():
    annonce = vr.avertissement(["contacts"])
    with pytest.raises(ValueError, match="écriture sans effet") as e:
        refuser_geste_sans_effet({}, [{"champ": "contacts"}], annonce)
    assert f"À partir du {QUAND}" in str(e.value) and '"contacts": null' in str(e.value)


def test_la_description_de_data_write_et_le_guide_annoncent_la_date():
    from oto_mcp.tools import datastore as tools_ds

    assert vr.VIDE_REMPLACE_LE.isoformat() in vr.DESCRIPTION_ECRITURE
    assert "<<vide_remplace>>" in inspect.getsource(tools_ds), "la date est recopiée"
    guide = pathlib.Path(__file__).parents[2] / "oto_mcp/guides/datastore-semantics.md"
    assert f"à partir du {QUAND}" in guide.read_text()


def test_l_annonce_est_posee_sur_les_deux_chemins_de_fusion():
    from oto_mcp.datastore import ecriture, ecriture_par_id

    for mod in (ecriture, ecriture_par_id):
        assert "vr.annonce(" in inspect.getsource(mod), mod.__name__


# ── `null` sur un vide assumé, la règle ──────────────────────────────────────

@pytest.mark.parametrize("en_place", [MARQUEE, {**MARQUEE, "comment": "registre : rien"}],
                         ids=["nu", "commente"])
def test_un_null_sur_un_vide_assume_est_ecrit(en_place):
    corps = {"raison": "ACME", "fonction": None}
    assert sans_les_nulls_sans_effet(corps, lambda: {"fonction": en_place}, _SCHEMA) == corps


def test_un_null_sur_un_vide_ordinaire_reste_sans_effet():
    sortie = sans_les_nulls_sans_effet({"raison": "ACME", "fonction": None},
                                       lambda: {"fonction": ""}, _SCHEMA)
    assert sortie == {"raison": "ACME"}


# ── sur PostgreSQL ───────────────────────────────────────────────────────────

def _table():
    from oto_mcp import db
    from oto_mcp.datastore.core import make_store
    ns = "tj2-" + uuid.uuid4().hex[:6]
    ns_id = db.create_datastore("user", "sub-test", ns)
    st = make_store("sub-test")
    st.set_schema(ns, {"key": "siren", "fields": [
        {"key": "siren", "type": "text"}, {"key": "raison", "type": "text"},
        {"key": "site_web", "type": "text"}, {"key": "fonction", "type": "text"},
        {"key": "tags", "type": "list", "of": {"type": "text"}}]})
    return st, ns, ns_id


def _data(ns_id, rid):
    from oto_mcp import db
    return db.datastore_get_row(ns_id, rid)["data"]


def _annonces(st):
    return [n for n in st.off_notices if f"À partir du {QUAND}" in n]


def test_patch_par_id_le_vide_est_ecarte_et_la_bascule_annoncee(live):
    st, ns, ns_id = _table()
    rid = st.append_row(ns, {"siren": "1", "site_web": "a.fr", "tags": ["x"]})["_id"]
    st.off_notices.clear()

    st.update_row(ns, rid, {"raison": "ACME", "site_web": "", "tags": []})

    assert _data(ns_id, rid)["site_web"] == "a.fr" and _data(ns_id, rid)["tags"] == ["x"]
    (annonce,) = _annonces(st)
    assert "`site_web`, `tags`" in annonce


def test_fusion_et_lot_annoncent_aussi(live):
    st, ns, ns_id = _table()
    rid = st.append_row(ns, {"siren": "2", "site_web": "b.fr"})["_id"]
    st.off_notices.clear()
    st.append_row(ns, {"siren": "2", "raison": "BETA", "site_web": ""})
    assert _data(ns_id, rid)["site_web"] == "b.fr"
    assert any("`site_web`" in n for n in _annonces(st)), st.off_notices

    st.off_notices.clear()
    st.write_rows(ns, [{"siren": "2", "raison": "BETA2", "site_web": ""}], key="siren")
    assert _data(ns_id, rid)["site_web"] == "b.fr"
    assert any("`site_web`" in n for n in _annonces(st)), st.off_notices


def test_un_vide_seul_est_refuse_avec_l_annonce(live):
    st, ns, ns_id = _table()
    rid = st.append_row(ns, {"siren": "3", "site_web": "c.fr"})["_id"]
    with pytest.raises(ValueError, match="écriture sans effet") as e:
        st.update_row(ns, rid, {"site_web": ""})
    assert f"À partir du {QUAND}" in str(e.value)
    assert _data(ns_id, rid)["site_web"] == "c.fr"


def test_rien_d_annonce_sans_valeur_en_place(live):
    st, ns, ns_id = _table()
    rid = st.append_row(ns, {"siren": "4", "raison": "D"})["_id"]
    st.off_notices.clear()
    st.update_row(ns, rid, {"site_web": "", "raison": "D2"})
    assert not _annonces(st), st.off_notices


@pytest.mark.parametrize("chemin", ["par_id", "fusion", "lot"])
def test_null_efface_un_vide_assume_marqueur_compris(live, chemin):
    st, ns, ns_id = _table()
    rid = st.append_row(ns, {"siren": "5", "fonction": {
        "valeur": dsl.VIDE_DELIBERE, "comment": "registre : rien"}})["_id"]
    assert dsl.vide_assume(_data(ns_id, rid)["fonction"]), "le banc ne pose pas le marqueur"

    if chemin == "par_id":
        st.update_row(ns, rid, {"fonction": None})
    elif chemin == "fusion":
        st.append_row(ns, {"siren": "5", "fonction": None})
    else:
        st.write_rows(ns, [{"siren": "5", "fonction": None}], key="siren")

    assert not dsl.vide_assume(_data(ns_id, rid).get("fonction")), _data(ns_id, rid)
    assert dsl.unwrap(_data(ns_id, rid).get("fonction")) is None
    assert st.get_row(ns, rid, empties="sentinel")["fonction"] is None


def test_null_sur_une_case_vraiment_vide_reste_sans_effet(live):
    from oto_mcp import db
    st, ns, ns_id = _table()
    rid = st.append_row(ns, {"siren": "6", "raison": "F"})["_id"]
    avant = db.datastore_get_row(ns_id, rid)
    st.update_row(ns, rid, {"fonction": None})
    apres = db.datastore_get_row(ns_id, rid)
    assert apres["data"] == avant["data"] and apres["rev"] == avant["rev"]
