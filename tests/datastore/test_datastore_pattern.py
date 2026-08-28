"""Contraindre la FORME d'une valeur, pas seulement sa taille (#387).

Jumeau de `max_length`, et il dit ce que la borne ne sait pas dire. Cas mesuré : un
champ qui doit porter une ÉNUMÉRATION de catégories séparées par des points-virgules,
pas une phrase de positionnement. Les longueurs des deux formes se recouvrent (20 à
207 caractères) — borner à 150 tue les deux, borner à 250 n'attrape rien. **Ce qui
les sépare est la structure, pas la taille.**

Avant ce lot, `pattern` était accepté sans erreur et jamais appliqué : le pire des
deux mondes, puisque celui qui le pose croit avoir posé un contrat.

⚠️ **Une expression fournie par un appelant est une arme.** Elle s'exécute à chaque
écriture, dans la boucle UNIQUE du serveur. Ce banc fige donc autant le refus des
motifs qu'on ne sait pas exécuter sûrement que l'application de ceux qu'on accepte —
et il le fige avec des mesures, pas avec une intuition : `.*.*.*.*.*.*.*z` sur
60 caractères prend 14,8 s sans le moindre groupe quantifié.
"""
from __future__ import annotations

import time

import pytest

from oto_mcp.datastore import schema as S


def _pose(schema) -> list[str]:
    return S.validate_schema_def(schema)


def _ecrit(schema, row, written=None) -> list[str]:
    return S.validate_row(schema, row, written=written)


_SNAKE = {"fields": [{"key": "signal", "type": "text",
                      "max_length": 60, "pattern": "^[a-z0-9_]+$"}]}


# ── le motif MORD ──

def test_une_valeur_hors_motif_est_refusee():
    errs = _ecrit(_SNAKE, {"signal": "Pas Du Snake"})
    assert errs, "un motif déclaré doit refuser ce qui n'y répond pas"


def test_le_refus_cite_la_valeur_ET_le_motif():
    """Un refus qui ne dit pas ce qui était attendu fait deviner — et le motif est
    précisément l'information que l'auteur de la ligne n'a pas."""
    errs = _ecrit(_SNAKE, {"signal": "Pas Du Snake"})
    assert any("Pas Du Snake" in e and "^[a-z0-9_]+$" in e for e in errs), errs


def test_une_valeur_conforme_passe():
    assert _ecrit(_SNAKE, {"signal": "a_relancer_2"}) == []


def test_le_motif_arme_la_validation_a_lui_seul():
    """Sans ça la contrainte est inerte sur un schéma sans `required` — même réserve
    que `max_length` (#383), et elle a été retenue là-bas."""
    assert S.validation_active(_SNAKE) is True


def test_le_motif_ne_juge_que_ce_que_le_geste_ECRIT():
    """La validation porte sur le résultat MERGÉ : sans cette restriction, une ligne
    déjà non conforme deviendrait inécritable pour n'importe quel patch, y compris
    sur un champ sans rapport (23 lignes gelées chez un client, oto-backend#284)."""
    schema = {"fields": [{"key": "signal", "type": "text", "max_length": 60,
                          "pattern": "^[a-z0-9_]+$"},
                         {"key": "statut", "type": "text"}]}
    merged = {"signal": "Valeur Historique", "statut": "vu"}
    assert _ecrit(schema, merged, written={"statut"}) == []
    assert _ecrit(schema, merged, written={"signal"}) != []


# ── ce qui se refuse À LA POSE, devant celui qui pose ──

def test_une_regex_invalide_est_refusee_a_la_pose():
    """Pas à l'écriture d'une ligne : une regex fautive doit échouer devant celui qui
    la pose, pas devant celui qui écrira une ligne trois semaines plus tard."""
    errs = _pose({"fields": [{"key": "x", "max_length": 60, "pattern": "^[a-z"}]})
    assert any("pattern" in e for e in errs), errs


def test_un_motif_sans_borne_est_refuse():
    """Le budget d'exploration se calcule CONTRE la longueur du sujet : sans sujet
    borné il n'y a pas de budget, donc pas de garantie — on refuse en le disant."""
    errs = _pose({"fields": [{"key": "x", "pattern": "^[a-z]+$"}]})
    assert any("max_length" in e for e in errs), errs


def test_un_motif_sur_un_composite_est_refuse():
    errs = _pose({"fields": [{"key": "c", "type": "list", "of": {"type": "text"},
                              "max_length": 60, "pattern": "^a$"}]})
    assert any("pattern" in e for e in errs), errs


@pytest.mark.parametrize("motif", [
    "(a+)+$",                       # le classique du backtracking exponentiel
    "(a|aa)*$",                     # alternance ambiguë répétée
    ".*.*.*.*.*.*.*z",              # explosion POLYNOMIALE, sans un seul groupe
    "^(x+x+)+y$",
])
def test_un_motif_a_explosion_combinatoire_est_refuse(motif):
    """Mesuré : `.*.*.*.*.*.*.*z` sur 60 caractères = 14,8 s dans la boucle unique.
    Un garde purement syntaxique (« pas de groupe quantifié ») laisserait passer le
    troisième — c'est le nombre de FAÇONS de découper le sujet qui explose."""
    errs = _pose({"fields": [{"key": "x", "max_length": 250, "pattern": motif}]})
    assert errs, f"{motif!r} devrait être refusé"


@pytest.mark.parametrize("motif", ["(?=.*a)b", r"(a)\1", "(?<=a)b"])
def test_les_constructions_qu_on_ne_sait_pas_majorer_sont_refusees(motif):
    """Référence arrière et assertions : on ne les majore pas, donc on ne les
    exécute pas — et le refus les NOMME plutôt que de dire « motif invalide »."""
    errs = _pose({"fields": [{"key": "x", "max_length": 60, "pattern": motif}]})
    assert errs, f"{motif!r} devrait être refusé"


@pytest.mark.parametrize("motif", [
    "^[a-z0-9_]+$",                 # snake_case, le cas du signal
    "^[^A-Z].{0,59}$",              # pas de majuscule initiale, borné
    "^[0-9]{9}$",                   # un SIREN
    "^(oui|non)$",                   # une alternance NON répétée
])
def test_les_motifs_du_terrain_passent(motif):
    assert _pose({"fields": [{"key": "x", "max_length": 60,
                              "pattern": motif}]}) == []


@pytest.mark.parametrize("borne,motif", [
    (250, "^.*a.*$"),      # deux quantificateurs : 251² découpages, le plafond
    (1000, "^.*a$"),       # un seul, sur le sujet le plus long qu'on accepte
])
def test_le_motif_accepte_s_execute_vite_sur_le_pire_sujet(borne, motif):
    """Le garde ne vaut que par ce qu'il laisse passer : on MESURE le coût des motifs
    les plus chers que le budget autorise, sur le pire sujet (aucun `a`, donc
    exploration complète). Sans cette mesure, le budget n'est qu'un chiffre."""
    schema = {"fields": [{"key": "x", "max_length": borne, "pattern": motif}]}
    assert _pose(schema) == []
    debut = time.perf_counter()
    _ecrit(schema, {"x": "z" * borne})
    assert time.perf_counter() - debut < 0.05


def test_le_budget_refuse_le_meme_motif_sur_un_champ_plus_LARGE():
    """Le coût se majore CONTRE la borne : le même motif est bon marché sur 250
    caractères et hors budget sur 1000. Un garde qui jugerait le motif seul, sans
    son sujet, mentirait dans un sens ou dans l'autre."""
    assert _pose({"fields": [{"key": "x", "max_length": 250,
                              "pattern": "^.*a.*$"}]}) == []
    assert _pose({"fields": [{"key": "x", "max_length": 1000,
                              "pattern": "^.*a.*$"}]}) != []


# ── un motif déjà EN BASE qu'on ne sait pas exécuter reste inerte ──

def test_un_motif_stocke_hors_garde_n_explose_pas_a_l_ecriture():
    """Un schéma posé quand la clé était encore ignorée ne doit pas faire exploser
    une écriture — même doctrine que `max_length_of` (muette sur une déclaration mal
    formée). La pose, elle, le refuse : c'est là qu'on peut encore corriger."""
    stocke = {"fields": [{"key": "x", "max_length": 250, "pattern": "(a+)+$"}]}
    assert _ecrit(stocke, {"x": "a" * 200}) == []
    assert _pose(stocke) != []


def test_un_motif_non_textuel_est_refuse_a_la_pose():
    errs = _pose({"fields": [{"key": "x", "max_length": 60, "pattern": 42}]})
    assert any("pattern" in e for e in errs), errs


# ── ce que la POSE dit de l'existant ──

_PATTERNED = {"fields": [{"key": "signal", "type": "text", "max_length": 60,
                          "pattern": "^[a-z0-9_]+$"},
                         {"key": "notes", "type": "text"}]}


@pytest.fixture()
def store(monkeypatch):
    """Store dont les seams db sont stubés — la pose de schéma, sans PG."""
    from oto_mcp.datastore import core as dsm
    from oto_mcp.datastore import schema_ops as ops

    st = dsm.DatastorePg("u", acting_org=35)
    monkeypatch.setattr(st, "_resolve", lambda ns, write=False: 7)
    monkeypatch.setattr(st, "_ns_of", lambda ns_id: {})
    monkeypatch.setattr(ops.db, "set_datastore_schema", lambda *a: None)
    monkeypatch.setattr(ops.db, "datastore_key_dup_groups", lambda *a: [])
    monkeypatch.setattr(ops.db, "datastore_drop_key_index", lambda *a: None)
    monkeypatch.setattr(ops.db, "datastore_row_keys", lambda ns_id: [])
    monkeypatch.setattr(ops.db, "datastore_overlong_fields", lambda *a, **k: [])
    monkeypatch.setattr(ops.db, "datastore_offending_enum_values",
                        lambda *a, **k: [])
    return st, ops


def test_la_pose_nomme_les_lignes_qui_ne_suivent_DEJA_pas_le_motif(store, monkeypatch):
    """Un schéma ne vaut que pour l'avenir, mais on formalise une table DÉJÀ pleine :
    sans ce relevé, elle *paraît* conforme puisqu'elle a un format."""
    st, ops = store
    vu = {}
    monkeypatch.setattr(ops.db, "datastore_field_values",
                        lambda ns_id, fields, **k: (
                            vu.update(ns_id=ns_id, fields=list(fields)) or
                            {"signal": {"values": [
                                {"value": "a_relancer", "rows": 400},
                                {"value": "A Relancer", "rows": 12},
                                {"value": "À relancer !", "rows": 3}],
                                "truncated": False}}))
    out = st.set_schema("viviers", _PATTERNED)
    assert vu == {"ns_id": 7, "fields": ["signal"]}
    w = out["warning"]
    assert "15 ligne(s) hors motif" in w and "A Relancer" in w
    assert "a_relancer »" not in w          # la conforme n'est pas dénoncée


def test_un_releve_partiel_le_DIT(store, monkeypatch):
    """Un compte tronqué qui se présente comme un total rassure exactement là où il
    ne faut pas."""
    st, ops = store
    monkeypatch.setattr(ops.db, "datastore_field_values",
                        lambda ns_id, fields, **k: {
                            "signal": {"values": [{"value": "X", "rows": 1}],
                                       "truncated": True}})
    assert "PLANCHER" in st.set_schema("viviers", _PATTERNED)["warning"]


def test_aucun_motif_aucun_scan(store, monkeypatch):
    st, ops = store
    monkeypatch.setattr(ops.db, "datastore_field_values",
                        lambda *a, **k: pytest.fail("aucun motif : pas de scan"))
    assert "warning" not in st.set_schema(
        "viviers", {"fields": [{"key": "notes", "type": "text"}]})


def test_toutes_les_lignes_conformes_ne_disent_rien(store, monkeypatch):
    st, ops = store
    monkeypatch.setattr(ops.db, "datastore_field_values",
                        lambda *a, **k: {"signal": {
                            "values": [{"value": "a_relancer", "rows": 500}],
                            "truncated": False}})
    assert "hors motif" not in (st.set_schema("viviers", _PATTERNED).get("warning") or "")


# ── la version 3.10 de la box emprunte l'AUTRE parseur ──

def test_le_verdict_est_le_meme_avec_le_parseur_de_la_box(monkeypatch):
    """La box tourne en 3.10, où `re._parser` n'existe pas : c'est `sre_parse` qui
    sert. Un garde qui ne rendrait pas le même verdict là-bas serait un garde qui ne
    tourne pas en production."""
    import warnings as _w
    with _w.catch_warnings():
        _w.simplefilter("ignore", DeprecationWarning)
        import sre_parse
    monkeypatch.setattr(S, "_re_parser", lambda: sre_parse)
    assert S.pattern_refusal("^[a-z0-9_]+$", 60) is None
    assert S.pattern_refusal("(a+)+$", 250) is not None
    assert S.pattern_refusal(".*.*.*.*.*.*.*z", 250) is not None


def test_sans_parseur_aucun_motif_ne_passe(monkeypatch):
    """Fail-closed : ne plus savoir majorer un coût n'autorise pas à l'ignorer."""
    monkeypatch.setattr(S, "_re_parser", lambda: None)
    assert S.pattern_refusal("^a$", 60) is not None
