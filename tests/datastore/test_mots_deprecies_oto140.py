"""`@keep` et `@clear` retirés : l'écriture est REFUSÉE, et le refus nomme le geste (oto#140).

Le contrat d'une case tient en deux gestes — `null` efface, `@empty` dit « cherché,
rien ». `@clear` doublait `null`, `@keep` doublait l'omission : une écriture qui les
porte est refusée entière, sans date ni préavis. Sans ce refus, `"@keep"` serait stocké
comme une valeur littérale. Le refus lui-même : `test_regle_finale_j2_j3.py`.
"""
from __future__ import annotations

import inspect
import pathlib
import uuid

import pytest

from oto_mcp.datastore import couches as dsl
from oto_mcp.datastore import mots_deprecies as mdp
from oto_mcp.datastore.errors import RowValidationError


# ── ce que le refus VOIT ─────────────────────────────────────────────────────

def test_les_deux_mots_sont_vus_nus_en_couche_et_dans_une_liste():
    assert mdp.mots_nommes({
        "a": dsl.EFFACEMENT,
        "b": {"valeur": "x", "comment": dsl.GARDE},
        "c": [{"nom": "Alice", "fonction": dsl.GARDE}],
    }) == {dsl.EFFACEMENT: ["a"], dsl.GARDE: ["b", "c"]}


def test_ni_null_ni_empty_ni_une_sous_chaine_ne_declenchent_rien():
    """`null` et `@empty` sont LES deux gestes : les refuser enseignerait le contraire.
    Le mot ne mord qu'entier — `contact@keepcalm.example` n'est pas un `@keep`."""
    assert mdp.mots_nommes({
        "a": None, "b": {"valeur": None}, "c": dsl.VIDE_DELIBERE,
        "d": "contact@keepcalm.example", "e": "@keep ; vu au registre", "f": [],
    }) == {}


# ── ce que le texte DIT ──────────────────────────────────────────────────────

def test_le_refus_dit_le_remplacement_de_chaque_mot_sans_date():
    with pytest.raises(RowValidationError) as e:
        mdp.controler({"a": dsl.EFFACEMENT, "b": {"comment": dsl.GARDE}})
    t = str(e.value)
    assert "`@clear` (`a`)" in t and "`null`" in t
    assert "`@keep` (`b`)" in t and "leave the sub-field out" in t
    assert "`@empty`" in t and "searched, nothing" in t
    assert "octobre" not in t and "déprécié" not in t and "October" not in t and "deprecated" not in t


def test_un_lot_refuse_nomme_la_couche_les_lignes_et_le_geste_signal_1481():
    """Signal 1481 : un lot de 46 lignes (clé `linkedin_url`), dont 44 ne voulaient
    réécrire que `comment`/`link` d'une colonne en gardant sa valeur par `@keep`. Le
    refus nommait la colonne, ni la couche ni les lignes, ni le geste exact."""
    rows = [{"linkedin_url": "https://profil.example/neuf", "nom": "Neuf"}]
    rows += [{"linkedin_url": {"valeur": f"https://profil.example/{i}", "comment": "vu"},
              "statut": {"valeur": dsl.GARDE, "comment": "relu", "link": "https://l.example"}}
             for i in range(44)]
    rows.append({"linkedin_url": "https://profil.example/dernier", "note": dsl.EFFACEMENT})
    with pytest.raises(RowValidationError) as e:
        mdp.controler(*rows, lot=True, key="linkedin_url")
    t = str(e.value)
    assert "`@keep` (`statut`) at `statut.valeur`" in t
    assert "on 44 of the 46 rows of the batch" in t
    assert "row 2 (linkedin_url=https://profil.example/0)" in t
    assert "row 1 " not in t and "row 7 " not in t        # cinq lignes citées, pas le lot
    assert "WITHOUT `valeur`" in t and "the stored value stays" in t
    assert "`@clear` (`note`), on 1 of the 46 rows of the batch: row 46 " in t
    mots = {w["word"]: w for w in e.value.details["words"]}
    assert e.value.details["code"] == "retired_word"
    assert mots[dsl.GARDE]["cells"] == ["statut.valeur"]
    assert mots[dsl.GARDE]["row_count"] == 44 and len(mots[dsl.GARDE]["rows"]) == 20
    assert mots[dsl.EFFACEMENT]["rows"] == ["row 46 (linkedin_url=https://profil.example/dernier)"]


def test_le_rang_d_un_lot_part_du_premier_rang_de_la_tranche():
    with pytest.raises(RowValidationError, match=r"row 501 \(k=b\)"):
        mdp.controler({"k": "a"}, {"k": "b", "x": dsl.GARDE}, lot=True, key="k",
                      premier_rang=500)


def test_une_ecriture_seule_nomme_la_case_sans_parler_de_lignes():
    with pytest.raises(RowValidationError) as e:
        mdp.controler({"c": [{"nom": "A", "fonction": dsl.GARDE}]},
                      {"c": [{"nom": "A", "fonction": dsl.GARDE}]})
    t = str(e.value)
    assert "`@keep` (`c`) at `c[0].fonction`" in t
    assert "rows of the batch" not in t and "WITHOUT `valeur`" not in t


def test_les_lots_et_l_import_designent_leurs_lignes():
    from oto_mcp import upload_tokens
    from oto_mcp.datastore import lots

    for mod in (lots, upload_tokens):
        assert "lot=True" in inspect.getsource(mod), mod.__name__


def test_la_description_de_data_write_porte_la_regle():
    from oto_mcp.tools import datastore as tools_ds

    src = inspect.getsource(tools_ds)
    assert "<<mots_deprecies>>" in src, "la description porte la marque, pas un texte recopié"
    assert "REFUSED whole" in mdp.DESCRIPTION_ECRITURE
    assert "2026" not in mdp.DESCRIPTION_ECRITURE


def test_le_guide_dit_le_refus_au_present():
    guide = pathlib.Path(__file__).parents[2] / "oto_mcp/guides/datastore-semantics.md"
    assert "`@keep` and `@clear` are not accepted" in guide.read_text()


def test_le_texte_servi_ne_prescrit_plus_les_mots_deprecies_dans_les_refus():
    """Un refus prescrit `null` pour effacer et `@empty` pour « cherché, rien »."""
    from oto_mcp.datastore import columns

    src = inspect.getsource(columns)
    assert "comme `@clear` et `@empty` — EFFACE" not in src
    assert "(vide sans rien affirmer)" not in src


def test_le_refus_est_pose_sur_les_quatre_chemins_d_ecriture():
    from oto_mcp import upload_tokens
    from oto_mcp.datastore import ecriture, ecriture_par_id, lots

    for mod in (ecriture_par_id, lots, upload_tokens):
        assert "mdp.controler(" in inspect.getsource(mod), mod.__name__
    assert "mdp.controler(" in inspect.getsource(ecriture.EcritureMixin.append_row)


# ── sur une base réelle ──────────────────────────────────────────────────────

SUB = "usr_mots_deprecies_140"


@pytest.fixture(scope="module")
def store(live):
    from oto_mcp import db
    from oto_mcp.datastore.core import make_store

    db.upsert_user(SUB, email=f"{SUB}@deprecies.invalid", name=SUB)
    ns = str(db.create_datastore("user", SUB, "tdep-" + uuid.uuid4().hex[:6]))
    st = make_store(SUB)
    st.set_schema(ns, {"fields": [{"key": "nom", "type": "text"},
                                  {"key": "site", "type": "text"}]})
    return st, ns


def test_keep_n_est_jamais_stocke(store):
    """Le refus est ce qui empêche `"@keep"` d'entrer comme une valeur littérale."""
    from oto_mcp import db

    st, ns = store
    rid = st.append_row(ns, {"nom": "ACME",
                             "site": {"valeur": "acme.test", "comment": "registre"}})["_id"]
    with pytest.raises(RowValidationError, match=r"`@keep` \(`site`\)"):
        st.update_row(ns, rid, {"site": dsl.GARDE})
    with pytest.raises(RowValidationError, match=r"`@keep` \(`nom`\)"):
        st.append_row(ns, {"nom": dsl.GARDE})
    cell = db.datastore_get_row(st._resolve(ns), rid)["data"]["site"]
    assert cell["valeur"] == "acme.test" and cell["comment"] == "registre"


def test_null_efface_sans_refus(store):
    """L'annulation de la fin de `null` : il efface, et rien ne l'annonce comme un geste
    en sursis."""
    st, ns = store
    rid = st.append_row(ns, {"nom": "ACME", "site": "acme.test"})["_id"]
    st.off_notices.clear()
    out = st.update_row(ns, rid, {"site": None})
    assert not out.get("site"), out
    assert not any("null" in n and "REFUSÉ" in n for n in st.off_notices), st.off_notices
