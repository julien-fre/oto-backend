"""`@keep` et `@clear` dépréciés : l'écriture réussit, la réponse l'annonce, datée (oto#140).

Le contrat d'une case tient en deux gestes — `null` efface, `@empty` dit « cherché,
rien ». `@clear` doublait `null`, `@keep` doublait l'omission : ils seront refusés à
la date de `MOTS_DEPRECIES_REFUSES_LE`, et d'ici là rien ne change à ce qu'ils font.
Ce banc tient les deux moitiés : l'avertissement est servi, et l'écriture a bien eu lieu.
"""
from __future__ import annotations

import inspect
import pathlib
import uuid

import pytest

from oto_mcp.datastore import couches as dsl
from oto_mcp.datastore import mots_deprecies as mdp
from oto_mcp.datastore.champs_reserves import _en_francais

QUAND = _en_francais(mdp.MOTS_DEPRECIES_REFUSES_LE)


# ── ce que l'avertissement VOIT ──────────────────────────────────────────────

def test_les_deux_mots_sont_vus_nus_en_couche_et_dans_une_liste():
    assert mdp.mots_nommes({
        "a": dsl.EFFACEMENT,
        "b": {"valeur": "x", "comment": dsl.GARDE},
        "c": [{"nom": "Alice", "fonction": dsl.GARDE}],
    }) == {dsl.EFFACEMENT: ["a"], dsl.GARDE: ["b", "c"]}


def test_ni_null_ni_empty_ni_une_sous_chaine_ne_declenchent_rien():
    """`null` et `@empty` sont LES deux gestes : les avertir enseignerait le contraire.
    Le mot ne mord qu'entier — `contact@keepcool.fr` n'est pas un `@keep`."""
    assert mdp.mots_nommes({
        "a": None, "b": {"valeur": None}, "c": dsl.VIDE_DELIBERE,
        "d": "contact@keepcool.fr", "e": "@keep ; vu au registre", "f": [],
    }) == {}
    assert mdp.avertissement({}) is None


# ── ce que le texte DIT ──────────────────────────────────────────────────────

def test_l_avertissement_dit_la_date_et_le_remplacement_de_chaque_mot():
    t = mdp.avertissement(mdp.mots_nommes({"a": dsl.EFFACEMENT,
                                           "b": {"comment": dsl.GARDE}}))
    assert t.count(QUAND) == 2 and "8 octobre 2026" in t
    assert "`@clear` (`a`)" in t and "`null`" in t
    assert "`@keep` (`b`)" in t and "omets le sous-champ" in t
    assert "`@empty`" in t and "cherché, rien" in t


def test_la_description_de_data_write_derive_de_la_meme_date():
    from oto_mcp.tools import datastore as tools_ds  # noqa: F401 — pose la description

    assert mdp.MOTS_DEPRECIES_REFUSES_LE.isoformat() in mdp.DESCRIPTION_ECRITURE
    src = inspect.getsource(tools_ds)
    assert "<<mots_deprecies>>" in src, "la description porte la marque, pas une date recopiée"


def test_le_guide_annonce_la_meme_date():
    guide = pathlib.Path(__file__).parents[2] / "oto_mcp/guides/datastore-semantics.md"
    assert f"REFUSÉS à partir du {QUAND}" in guide.read_text()


def test_le_texte_servi_ne_prescrit_plus_les_mots_deprecies_dans_les_refus():
    """Un refus prescrit `null` pour effacer et `@empty` pour « cherché, rien »."""
    from oto_mcp.datastore import columns

    src = inspect.getsource(columns)
    assert "comme `@clear` et `@empty` — EFFACE" not in src
    assert "(vide sans rien affirmer)" not in src


def test_l_avertissement_est_pose_sur_les_trois_chemins_d_ecriture():
    from oto_mcp.datastore import ecriture, ecriture_par_id, lots

    for mod in (ecriture, ecriture_par_id, lots):
        assert "mdp.mots_nommes(" in inspect.getsource(mod), mod.__name__


# ── sur une base réelle : l'écriture a lieu ET l'avertissement est servi ────

SUB = "usr_mots_deprecies_140"


@pytest.fixture(scope="module")
def store(live):
    from oto_mcp import db
    from oto_mcp.datastore.core import make_store

    db.upsert_user(SUB, email=f"{SUB}@deprecies.invalid", name=SUB)
    ns = "tdep-" + uuid.uuid4().hex[:6]
    db.create_datastore("user", SUB, ns)
    st = make_store(SUB)
    st.set_schema(ns, {"fields": [{"key": "nom", "type": "text"},
                                  {"key": "site", "type": "text"}]})
    return st, ns


def _avertis(st) -> list[str]:
    return [n for n in st.off_notices if "déprécié" in n]


def test_clear_efface_encore_et_l_annonce(store):
    st, ns = store
    rid = st.append_row(ns, {"nom": "ACME", "site": "acme.test"})["_id"]
    st.off_notices.clear()
    out = st.update_row(ns, rid, {"site": dsl.EFFACEMENT})
    assert not out.get("site"), out
    (t,) = _avertis(st)
    assert "`@clear` (`site`)" in t and QUAND in t


def test_keep_garde_encore_et_l_annonce(store):
    st, ns = store
    rid = st.append_row(ns, {"nom": "ACME",
                             "site": {"valeur": "acme.test", "comment": "registre"}})["_id"]
    st.off_notices.clear()
    st.update_row(ns, rid, {"site": {"valeur": "acme.example", "comment": dsl.GARDE}})
    from oto_mcp import db

    ns_id = st._resolve(ns)
    cell = db.datastore_get_row(ns_id, rid)["data"]["site"]
    assert cell["valeur"] == "acme.example" and cell["comment"] == "registre"
    (t,) = _avertis(st)
    assert "`@keep` (`site`)" in t


def test_null_efface_sans_avertissement(store):
    """L'annulation de la fin de `null` : il efface, et plus rien ne l'annonce comme
    un geste en sursis."""
    st, ns = store
    rid = st.append_row(ns, {"nom": "ACME", "site": "acme.test"})["_id"]
    st.off_notices.clear()
    out = st.update_row(ns, rid, {"site": None})
    assert not out.get("site"), out
    assert not any("null" in n and "REFUSÉ" in n for n in st.off_notices), st.off_notices


def test_le_lot_annonce_aussi(store):
    st, ns = store
    st.off_notices.clear()
    st.write_rows(ns, [{"nom": "B", "site": dsl.EFFACEMENT},
                       {"nom": "C", "site": dsl.EFFACEMENT}])
    assert len(_avertis(st)) == 1, st.off_notices
