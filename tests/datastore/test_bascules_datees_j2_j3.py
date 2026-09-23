"""Les deux bascules DATÉES du contrat d'écriture d'une case (oto#140, J2 et J3).

Elles tombent à leur date, dans le code — rien n'est à déployer le jour J :

- **J2** (`OTO_VIDE_REMPLACE_LE`, défaut 2026-10-06) : `""` et `[]` REMPLACENT la valeur
  en place, et la valeur remplacée revient dans `valeurs_effacees` ; les gardes #608
  (vide ignoré) et #724 (« écriture sans effet ») ne valent plus que pour `{}` ;
- **J3** (`OTO_MOTS_DEPRECIES_REFUSES_LE`, défaut 2026-10-08) : une écriture qui porte
  `@keep` ou `@clear`, où que ce soit, est REFUSÉE entière, et le refus nomme le geste.

Chaque bascule a son AVANT et son APRÈS. Le jour est fixé à la veille par le `conftest`
racine ; l'APRÈS s'obtient en reculant la date par le RÉGLAGE — jamais par l'horloge.
"""
from __future__ import annotations

import uuid

import pytest

from oto_mcp.datastore import couches as dsl
from oto_mcp.datastore import mots_deprecies as mdp
from oto_mcp.datastore import vide_remplace as vr
from oto_mcp.datastore.columns import (
    arbitrer_les_vides,
    effacements_report,
    ignores_report,
    refuser_geste_sans_effet,
)
from oto_mcp.datastore.errors import RowValidationError

PASSEE = "2026-01-01"          # une date déjà franchie par le jour gréé : l'APRÈS


@pytest.fixture
def j2_passe(monkeypatch):
    monkeypatch.setenv(vr.ENV_VIDE_REMPLACE_LE, PASSEE)


@pytest.fixture
def j3_passe(monkeypatch):
    monkeypatch.setenv(mdp.ENV_MOTS_DEPRECIES_REFUSES_LE, PASSEE)


# ── les réglages ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("module, env, juge", [
    (vr, vr.ENV_VIDE_REMPLACE_LE, vr.bascule_faite),
    (mdp, mdp.ENV_MOTS_DEPRECIES_REFUSES_LE, mdp.refus_arme),
], ids=["J2", "J3"])
def test_un_reglage_illisible_leve_au_lieu_de_retomber_sur_le_defaut(monkeypatch, module,
                                                                    env, juge):
    monkeypatch.setenv(env, "8 octobre")
    with pytest.raises(ValueError, match=env):
        juge()


def test_la_veille_rien_n_a_bascule_et_le_reglage_deplace_la_date(monkeypatch):
    assert not vr.bascule_faite() and not mdp.refus_arme()
    monkeypatch.setenv(vr.ENV_VIDE_REMPLACE_LE, PASSEE)
    monkeypatch.setenv(mdp.ENV_MOTS_DEPRECIES_REFUSES_LE, PASSEE)
    assert vr.bascule_faite() and mdp.refus_arme()


# ── J2 : l'arbitrage des vides ───────────────────────────────────────────────

EN_PLACE = {"site": "a.fr", "tags": ["x"], "meta": {"k": 1}}


def test_j2_avant_les_vides_sont_ecartes():
    pose, effaces, ecartes = arbitrer_les_vides(EN_PLACE, {"site": "", "tags": [], "meta": {}})
    assert pose == {} and effaces == []
    assert sorted(r["champ"] for r in ecartes) == ["meta", "site", "tags"]
    assert "chaîne vide, liste vide" in ignores_report(ecartes)["valeurs_ignorees_hint"]


def test_j2_apres_chaine_et_liste_vides_remplacent_et_se_disent(j2_passe):
    pose, effaces, ecartes = arbitrer_les_vides(
        EN_PLACE, {"site": "", "tags": {"valeur": [], "comment": "vu"}, "meta": {}}, "r1")
    assert pose == {"site": "", "tags": {"valeur": [], "comment": "vu"}}
    assert effaces == [{"ligne": "r1", "champ": "site", "valeur": "a.fr"},
                       {"ligne": "r1", "champ": "tags", "valeur": ["x"]}]
    # `{}` n'est pas une valeur : toujours écarté, et le relevé ne cite plus que lui.
    assert [r["champ"] for r in ecartes] == ["meta"]
    hint = ignores_report(ecartes)["valeurs_ignorees_hint"]
    assert "objet vide `{}`" in hint and "chaîne vide" not in hint
    assert "REMPLACENT" in effacements_report(effaces)["valeurs_effacees_hint"]


def test_j2_avant_un_vide_seul_est_refuse_sans_effet():
    pose, _, ecartes = arbitrer_les_vides(EN_PLACE, {"site": ""})
    with pytest.raises(ValueError, match="écriture sans effet"):
        refuser_geste_sans_effet(pose, ecartes, vr.annonce({"site": ""}, ecartes))


def test_j2_apres_un_vide_seul_n_est_plus_refuse_sauf_objet_vide(j2_passe):
    pose, _, ecartes = arbitrer_les_vides(EN_PLACE, {"site": ""})
    refuser_geste_sans_effet(pose, ecartes, vr.annonce({"site": ""}, ecartes))
    assert vr.annonce({"site": ""}, ecartes) is None
    pose, _, ecartes = arbitrer_les_vides(EN_PLACE, {"meta": {}})
    with pytest.raises(ValueError, match=r"objet vide `\{\}`"):
        refuser_geste_sans_effet(pose, ecartes)


# ── J3 : le contrôle des mots dépréciés ──────────────────────────────────────

CORPS = {"a": dsl.EFFACEMENT, "b": {"valeur": "x", "comment": dsl.GARDE},
         "c": [{"nom": "Alice", "fonction": dsl.GARDE}]}


def test_j3_avant_l_ecriture_est_avertie():
    notices: set = set()
    mdp.controler(notices, CORPS)
    (t,) = notices
    assert "déprécié" in t and "8 octobre 2026" in t


def test_j3_apres_l_ecriture_est_refusee_et_le_refus_nomme_le_geste(j3_passe):
    notices: set = set()
    with pytest.raises(RowValidationError) as e:
        mdp.controler(notices, {"x": "ok"}, CORPS)
    t = str(e.value)
    assert "écriture refusée" in t and "rien n'a été écrit" in t
    assert "`@clear` (`a`)" in t and "`null`" in t
    assert "`@keep` (`b`, `c`)" in t and "omets le sous-champ" in t
    assert "renvoie sa valeur telle quelle" in t
    assert not notices


def test_j3_apres_rien_ne_mord_sans_mot(j3_passe):
    notices: set = set()
    mdp.controler(notices, {"a": None, "b": dsl.VIDE_DELIBERE, "c": "contact@keepcool.fr"})
    assert not notices


# ── le texte servi, des deux côtés de chaque date ────────────────────────────

def test_descriptions_avant_annoncent_la_date():
    assert "From 2026-10-06 on" in vr.description_ecriture()
    assert "REFUSED from 2026-10-08 on" in mdp.description_ecriture()


def test_descriptions_apres_disent_la_regle_au_present(j2_passe, j3_passe):
    j2, j3 = vr.description_ecriture(), mdp.description_ecriture()
    assert "REPLACE the value in place" in j2 and "valeurs_effacees" in j2
    assert "From " not in j2 and "Until then" not in j2
    assert "no longer accepted" in j3 and "REFUSED whole" in j3
    assert "until then" not in j3 and "write `null`" in j3


# ── sur PostgreSQL ───────────────────────────────────────────────────────────

SUB = "usr_bascules_datees_140"


def _table():
    from oto_mcp import db
    from oto_mcp.datastore.core import make_store

    db.upsert_user(SUB, email=f"{SUB}@bascules.invalid", name=SUB)
    ns = "tbd-" + uuid.uuid4().hex[:6]
    ns_id = db.create_datastore("user", SUB, ns)
    st = make_store(SUB)
    st.set_schema(ns, {"key": "siren", "fields": [
        {"key": "siren", "type": "text"}, {"key": "raison", "type": "text"},
        {"key": "site_web", "type": "text"}, {"key": "nom", "type": "text",
                                              "required": True},
        {"key": "tags", "type": "list", "of": {"type": "text"}}]})
    return st, ns, ns_id


def _data(ns_id, rid):
    from oto_mcp import db
    return db.datastore_get_row(ns_id, rid)["data"]


def test_j2_avant_live_le_vide_est_ecarte(live):
    st, ns, ns_id = _table()
    rid = st.append_row(ns, {"siren": "1", "nom": "A", "site_web": "a.fr"})["_id"]
    st.update_row(ns, rid, {"raison": "R", "site_web": ""})
    assert _data(ns_id, rid)["site_web"] == "a.fr"
    with pytest.raises(ValueError, match="écriture sans effet"):
        st.update_row(ns, rid, {"site_web": ""})


@pytest.mark.parametrize("chemin", ["par_id", "fusion", "lot"])
def test_j2_apres_live_le_vide_remplace_sur_les_trois_chemins(live, j2_passe, chemin):
    st, ns, ns_id = _table()
    rid = st.append_row(ns, {"siren": "2", "nom": "B", "site_web": "b.fr",
                             "tags": ["x", "y"]})["_id"]
    st.off_erased.clear()
    corps = {"site_web": "", "tags": []}
    if chemin == "par_id":
        st.update_row(ns, rid, corps)             # le vide SEUL : plus de #724
    elif chemin == "fusion":
        st.append_row(ns, {"siren": "2", **corps})
    else:
        st.write_rows(ns, [{"siren": "2", **corps}], key="siren")
    data = _data(ns_id, rid)
    assert data["site_web"] == "" and data["tags"] == []
    rendu = effacements_report(st.off_erased)["valeurs_effacees"]
    assert {(r["champ"], str(r["valeur"])) for r in rendu} == {
        ("site_web", "b.fr"), ("tags", "['x', 'y']")}


def test_j2_apres_live_un_vide_sur_un_requis_reste_refuse_et_compte_vide(live, j2_passe):
    """`""` posé ne satisfait pas `required` — comme sur une case vide aujourd'hui — et
    le filtre `empty` le compte vide. `@empty` reste le seul « cherché, rien »."""
    st, ns, ns_id = _table()
    rid = st.append_row(ns, {"siren": "3", "nom": "C", "site_web": "c.fr"})["_id"]
    with pytest.raises(ValueError, match="champ requis manquant"):
        st.update_row(ns, rid, {"nom": ""})
    assert _data(ns_id, rid)["nom"] == "C"
    with pytest.raises(ValueError, match="champ requis manquant"):
        st.append_row(ns, {"siren": "3b", "nom": ""})

    st.update_row(ns, rid, {"site_web": ""})
    vides = st.page_rows(ns, filters=[{"field": "site_web", "op": "empty"}])
    assert rid in {r["_id"] for r in vides["rows"]}


def test_j3_avant_live_l_ecriture_passe_et_avertit(live):
    st, ns, ns_id = _table()
    rid = st.append_row(ns, {"siren": "4", "nom": "D", "site_web": "d.fr"})["_id"]
    st.off_notices.clear()
    st.update_row(ns, rid, {"site_web": dsl.EFFACEMENT})
    assert not _data(ns_id, rid).get("site_web")
    assert any("déprécié" in n for n in st.off_notices), st.off_notices


def test_j3_apres_live_refuse_partout_et_n_ecrit_rien(live, j3_passe):
    st, ns, ns_id = _table()
    rid = st.append_row(ns, {"siren": "5", "nom": "E", "site_web": "e.fr"})["_id"]
    avant = _data(ns_id, rid)

    with pytest.raises(RowValidationError, match=r"`@clear` \(`site_web`\)"):
        st.update_row(ns, rid, {"site_web": dsl.EFFACEMENT})
    with pytest.raises(RowValidationError, match=r"`@keep` \(`site_web`\)"):
        st.append_row(ns, {"siren": "5", "raison": "R",
                           "site_web": {"valeur": "x.fr", "comment": dsl.GARDE}})
    with pytest.raises(RowValidationError, match=r"`@keep` \(`tags`\)"):
        st.upsert_row(ns, rid, {"siren": "5", "nom": "E", "tags": [dsl.GARDE]})
    assert _data(ns_id, rid) == avant

    # Le LOT est refusé entier : la première ligne, saine, n'est pas écrite non plus.
    with pytest.raises(RowValidationError, match=r"`@clear` \(`raison`\)"):
        st.write_rows(ns, [{"siren": "6", "nom": "F"},
                           {"siren": "5", "raison": dsl.EFFACEMENT}], key="siren")
    from oto_mcp import db
    assert db.datastore_find_row_id_by_key(ns_id, "siren", "6") is None
    assert _data(ns_id, rid) == avant
