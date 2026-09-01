"""Datastore — un champ dont la VALEUR est posée par la plateforme (#607).

Le constat : une colonne `modele` que l'agent remplit de mémoire dérive —
`…2407` sur une fiche, `…2511` sur une autre le lendemain, quand les 102 travaux
enregistrés du run disent tous `…2512`. **Une valeur recopiée de mémoire est une
déclaration, pas une trace.**

Le cran est le frère de `origine: "system"` (#586) : là, la plateforme pose une
COUCHE ; ici, elle pose la VALEUR, depuis une source fermée.

⚠️ **Ce que la plateforme peut honnêtement poser** est plus étroit que ce que la
demande listait. `run.model` n'est servi par AUCUNE source : la table `runs` ne
porte pas de colonne modèle, `run_start` n'en reçoit pas, et le handshake ne
connaît qu'un nom de CLIENT (`claude.ai`, `Claude Code`), jamais un modèle. La
source est donc REFUSÉE à la déclaration en disant pourquoi — poser un champ que
la plateforme remplirait de vide, ou pire d'une devinette, referait le défaut
qu'on corrige. Ce que le cran sert à la place est le POINTEUR : `run.id` sur la
ligne, et le modèle se lit au run, jamais recopié.

⚠️ **Une valeur identique n'est pas une écriture** — même règle que `readonly`
(#625) et `origine` (#623). Le geste dominant du terrain réémet la fiche ENTIÈRE,
colonnes posées par la plateforme comprises : les refuser arrêterait la flotte.
"""
from __future__ import annotations

import pytest

from oto_mcp.datastore import core as dsm
from oto_mcp.datastore import schema as dsv2
from oto_mcp.datastore.core import DatastorePg
from oto_mcp.datastore.errors import RowValidationError


SCHEMA = {"key": "siren",
          "fields": [{"key": "siren", "type": "text"},
                     {"key": "run", "type": "text", "system": "run.id"},
                     {"key": "ecrit_le", "type": "datetime", "system": "write.at"},
                     {"key": "libre", "type": "text"}]}


# ── la déclaration ───────────────────────────────────────────────────────────

def test_les_sources_servies_sont_fermees():
    """Un ensemble fermé, dérivé de ce que le code SAIT poser — pas une liste
    parallèle qui se met à mentir le jour où une source disparaît."""
    assert set(dsv2.SYSTEM_SOURCES) == {"run.id", "run.started_at", "write.at"}


def test_une_source_inconnue_est_refusee_en_nommant_les_sources():
    errs = dsv2.validate_schema_def(
        {"fields": [{"key": "x", "type": "text", "system": "run.temperature"}]})
    assert any("run.id" in e and "write.at" in e for e in errs), errs


def test_run_model_est_refuse_en_disant_POURQUOI():
    """La source que la demande visait, et que rien ne sert. Le refus doit dire
    que la plateforme ne l'enregistre pas — pas juste « valeur invalide » —, et
    nommer le pointeur qui la remplace."""
    errs = dsv2.validate_schema_def(
        {"fields": [{"key": "modele", "type": "text", "system": "run.model"}]})
    assert len(errs) == 1
    msg = errs[0]
    assert "run.model" in msg
    assert "run.id" in msg                    # le remplaçant est nommé
    assert "n'enregistre" in msg or "ne connaît" in msg


def test_le_cran_ne_se_pose_qu_au_premier_niveau():
    """Sous un sous-record la garde ne le lit pas — et une déclaration que rien
    ne lit n'est pas inerte, elle ment."""
    errs = dsv2.validate_schema_def(
        {"fields": [{"key": "c", "type": "object",
                     "fields": [{"key": "q", "type": "text", "system": "run.id"}]}]})
    assert any("premier niveau" in e for e in errs), errs


def test_le_cran_ne_se_pose_pas_sur_un_composite():
    errs = dsv2.validate_schema_def(
        {"fields": [{"key": "c", "type": "list", "system": "run.id",
                     "of": {"fields": [{"key": "q", "type": "text"}]}}]})
    assert any("scalaire" in e for e in errs), errs


def test_le_cran_lit_se_declare_sur_la_bonne_colonne():
    assert dsv2.validate_schema_def(SCHEMA) == []
    assert dsv2.system_value_fields(SCHEMA) == {"run": "run.id",
                                                "ecrit_le": "write.at"}


def test_le_cran_s_annonce_dans_enforced():
    dsv2.reset_enforced_keys()
    try:
        assert "system" in dsv2.enforced_keys()
    finally:
        dsv2.reset_enforced_keys()


# ── la COMPOSITION avec les crans voisins (#606, #586, #516) ─────────────────

def test_readonly_et_system_sur_la_meme_colonne_sont_contradictoires():
    """`readonly` dit « la valeur ne change jamais », `system` dit « la plateforme
    la repose à chaque écriture ». Les deux ensemble, l'un des deux ment — et on
    ne sait pas lequel en lisant le schéma. Refusé à la POSE."""
    errs = dsv2.validate_schema_def(
        {"fields": [{"key": "x", "type": "text",
                     "readonly": True, "system": "run.id"}]})
    assert any("readonly" in e and "system" in e for e in errs), errs


def test_la_cle_metier_ne_se_pose_pas_en_system():
    """Même raison que `readonly` sur la clé (#606) : la clé identifie la ligne.
    La plateforme qui la repose déciderait de l'identité des lignes, et toute
    écriture viserait une ligne neuve."""
    errs = dsv2.validate_schema_def(
        {"key": "siren",
         "fields": [{"key": "siren", "type": "text", "system": "run.id"}]})
    assert any("clé métier" in e for e in errs), errs


# ── le geste ─────────────────────────────────────────────────────────────────

def _fake_merge_locked(rows):
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
    st = DatastorePg("u", acting_org=35)
    etat = {"schema": SCHEMA, "lignes": {"r1": {"siren": "552081317"}},
            "creees": [], "maj": []}
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


@pytest.fixture()
def sous_run(monkeypatch):
    monkeypatch.setattr(dsm, "_current_run", lambda: "run-abc")
    return "run-abc"


def test_la_plateforme_pose_la_valeur_a_la_creation(banc, sous_run):
    store, etat = banc
    store.append_row("viviers", {"siren": "999", "libre": "x"})
    creee = etat["creees"][0]
    assert dsv2.unwrap(creee["run"]) == "run-abc"
    assert dsv2.unwrap(creee["ecrit_le"])            # l'horodatage est posé


def test_elle_la_repose_a_chaque_ecriture_sans_que_l_appelant_la_nomme(banc, sous_run):
    """Le point du cran : l'estampille ne dépend pas de la mémoire de l'agent,
    donc elle n'a pas à figurer dans son corps."""
    store, etat = banc
    store.update_row("viviers", "r1", {"libre": "y"})
    assert dsv2.unwrap(etat["lignes"]["r1"]["run"]) == "run-abc"


def test_l_appelant_qui_ecrit_une_AUTRE_valeur_est_refuse(banc, sous_run):
    """Le geste exact du constat : l'agent grave une valeur de mémoire."""
    store, etat = banc
    with pytest.raises(RowValidationError) as e:
        store.update_row("viviers", "r1", {"run": "run-invente"})
    msg = str(e.value)
    assert "`run`" in msg and "run.id" in msg
    assert etat["maj"] == []


def test_la_valeur_identique_est_un_no_op(banc, sous_run):
    """#623/#625 : la fiche ENTIÈRE réémise porte la colonne posée par la
    plateforme. La refuser arrêterait la flotte — c'est déjà arrivé une fois."""
    store, etat = banc
    etat["lignes"]["r1"]["run"] = "run-abc"
    store.update_row("viviers", "r1", {"siren": "552081317", "run": "run-abc",
                                       "libre": "z"})
    assert etat["lignes"]["r1"]["libre"] == "z"


def test_la_valeur_DEJA_EN_PLACE_est_un_no_op(banc, sous_run):
    """Une fiche relue sous le run A puis réémise sous le run B porte encore la
    valeur de A. C'est notre propre lecture qui revient : la refuser punirait
    l'aller-retour, pas l'invention. La plateforme repose la sienne par-dessus."""
    store, etat = banc
    etat["lignes"]["r1"]["run"] = "run-precedent"
    store.update_row("viviers", "r1", {"run": "run-precedent", "libre": "z"})
    assert dsv2.unwrap(etat["lignes"]["r1"]["run"]) == "run-abc"


def test_sans_run_actif_rien_n_est_devine(banc, monkeypatch):
    """« Sans run actif, le champ reste vide et le refus reste » — jamais une
    valeur devinée, jamais un repli silencieux."""
    monkeypatch.setattr(dsm, "_current_run", lambda: None)
    store, etat = banc
    store.append_row("viviers", {"siren": "999"})
    assert "run" not in etat["creees"][0] or etat["creees"][0].get("run") in (None, "")
    with pytest.raises(RowValidationError):
        store.update_row("viviers", "r1", {"run": "run-invente"})


def test_le_lot_estampille_aussi(banc, sous_run):
    store, etat = banc
    store.write_rows("viviers", [{"siren": "111"}])
    assert dsv2.unwrap(etat["creees"][0]["run"]) == "run-abc"
