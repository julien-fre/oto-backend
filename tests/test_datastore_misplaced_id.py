"""`_id` posé dans `row` au lieu du paramètre `id` (oto-backend#390, signal).

`_id` est géré par le datastore : il vit dans la colonne `row_id`, jamais dans le
blob. Il était donc filtré des données écrites — en silence. Un agent qui avait
enrichi une fiche correctement (dix-sept appels, trois contacts, deux actualités
sourcées) a écrit `row={"_id": "019f…", "statut": "enrichi", …}` sans `id=` : la
ligne visée est restée vide, une 501ᵉ ligne sans SIREN a été INSÉRÉE avec tout le
travail, aucune erreur. 28 champs repris à la main.

`hors_schema` ne pouvait rien dire : tous les noms de champs étaient bons, c'est la
DESTINATION qui était fausse. D'où une garde distincte, et au même endroit — le
store, pour qu'aucune face ne puisse l'oublier.
"""
from __future__ import annotations

import pytest

import oto_mcp.datastore as dsm
from oto_mcp.datastore import DatastorePg

ROW = "019fdba0-dd7b-7be2-ec51-37adae1cbfa4"


@pytest.fixture()
def store(monkeypatch):
    st = DatastorePg("u", acting_org=35)
    monkeypatch.setattr(st, "_resolve", lambda ns, write=False: 7)
    calls = {"insert": [], "update": []}
    monkeypatch.setattr(dsm.db, "get_datastore_namespace_by_id",
                        lambda ns_id: {"id": ns_id, "schema": None})
    monkeypatch.setattr(dsm.db, "datastore_insert_row",
                        lambda ns_id, rid, data, *a, **k: (
                            calls["insert"].append(data) or
                            {"row_id": rid, "created_at": "t", "updated_at": "t",
                             "data": data}))
    monkeypatch.setattr(dsm.db, "datastore_get_row",
                        lambda ns_id, rid: {"row_id": rid, "created_at": "t",
                                            "updated_at": "t",
                                            "data": {"siren": "1"}})
    monkeypatch.setattr(dsm.db, "datastore_update_row",
                        lambda ns_id, rid, data, ts: (
                            calls["update"].append((rid, data)) or
                            {"row_id": rid, "created_at": "t", "updated_at": ts,
                             "data": data}))
    return st, calls


def test_append_with_id_in_the_body_is_refused(store):
    """LE cas vécu : sans `id=`, l'écriture insérait une ligne neuve."""
    st, calls = store
    with pytest.raises(ValueError, match="INSÉRERAIT"):
        st.append_row("v", {"_id": ROW, "statut": "enrichi"})
    assert calls["insert"] == []          # surtout : rien d'inséré


def test_the_message_hands_back_the_right_call(store):
    st, _ = store
    with pytest.raises(ValueError) as e:
        st.append_row("v", {"_id": ROW, "statut": "enrichi"})
    # l'identifiant ET la forme correcte, pour que la reprise soit mécanique
    assert ROW in str(e.value) and "data_write(id=" in str(e.value)


def test_patch_with_a_coherent_id_passes(store):
    """Round-trip normal : relire une ligne entière, la modifier, la repousser avec
    son `id`. Le refuser n'apprendrait rien à personne."""
    st, calls = store
    out = st.update_row("v", ROW, {"_id": ROW, "statut": "enrichi"})
    assert calls["update"] and out["statut"] == "enrichi"
    assert "_id" not in calls["update"][0][1]   # jamais écrit dans le blob


def test_patch_with_a_divergent_id_is_refused(store):
    """Deux cibles pour une écriture : on ne devine pas laquelle."""
    st, calls = store
    with pytest.raises(ValueError, match="ne correspond pas"):
        st.update_row("v", ROW, {"_id": "019f-autre", "statut": "enrichi"})
    assert calls["update"] == []


def test_batch_row_carrying_an_id_is_refused(store):
    """Un lot dédouble par clé métier ; il ne cible pas une ligne par `_id`."""
    st, calls = store
    with pytest.raises(ValueError, match="dédouble par clé"):
        st.write_rows("v", [{"siren": "1"}, {"_id": ROW, "siren": "2"}])
    assert len(calls["insert"]) == 1       # la 1ʳᵉ passe, le lot s'arrête net


def test_other_platform_columns_stay_silently_ignored(store):
    """`_created_at`/`_claimed_by` dans un round-trip sont bénins : ils ne DÉSIGNENT
    pas la cible de l'écriture. Les refuser casserait le relire-modifier-réécrire."""
    st, calls = store
    st.append_row("v", {"siren": "1", "_created_at": "t", "_claimed_by": "agent-A"})
    assert calls["insert"][0] == {"siren": "1"}
