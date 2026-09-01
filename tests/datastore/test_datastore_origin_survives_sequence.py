"""L'origine survit à une RÉÉCRITURE — la propriété vit dans la séquence (#322).

Le défaut que ces tests figent n'était pas dans `_merge_column` : il était dans le
fait que `update_row` avait sa PROPRE fusion. Mes tests d'alors exerçaient la
fonction que j'avais écrite, pas le chemin qu'un agent emprunte — donc « l'origine
survit » était vrai de ce que je testais et faux de ce qu'on utilise.

D'où la forme de ces tests : ils passent par `DatastorePg.update_row` et
`append_row`, ils écrivent DEUX FOIS, et ils vérifient l'état APRÈS la seconde.
Une écriture seule ne prouve rien de la survie.

Les quatre cas sont ceux de la session de campagne, rejoués ici après l'avoir été sur la
vraie surface (`data_write`, préprod) — le store est ce que la surface appelle.
"""
from __future__ import annotations

import pytest

from oto_mcp.datastore.core import DatastorePg


class _Db:
    """Base en mémoire : le round-trip suffit, et c'est LUI qu'on veut exercer."""

    def __init__(self):
        self.rows: dict = {}

    # --- ce que le store appelle
    def datastore_get_row(self, ns_id, row_id):
        return self.rows.get(row_id)

    def datastore_merge_row_locked(self, ns_id, row_id, apply, now, **kw):
        cur = self.rows.get(row_id)
        if cur is None:
            return None
        merged = apply(dict(cur["data"]))
        cur["data"] = merged
        return cur, merged

    def datastore_update_row(self, ns_id, row_id, data, now, **kw):
        self.rows[row_id]["data"] = data
        return self.rows[row_id]


@pytest.fixture()
def store(monkeypatch):
    from oto_mcp.datastore import core as ds
    db = _Db()
    s = DatastorePg("u-1")
    monkeypatch.setattr(s, "_resolve", lambda ns, write=False: 1)
    monkeypatch.setattr(s, "_ns_of", lambda ns_id: {"schema": None, "namespace": "t"})
    monkeypatch.setattr(s, "_schema_of", lambda ns_id: None)
    monkeypatch.setattr(s, "_assert_writable", lambda *a, **k: None)
    monkeypatch.setattr(s, "_trace", lambda *a, **k: None)
    for name in ("datastore_get_row", "datastore_merge_row_locked",
                 "datastore_update_row"):
        monkeypatch.setattr(ds.db, name, getattr(db, name))
    db.rows["r1"] = {"row_id": "r1", "created_at": "t", "updated_at": "t", "data": {}}
    return s, db


def _val(db, key="contact1_nom"):
    return DatastorePg._row_to_dict(db.rows["r1"]).get(key)


def _origine(db, key="contact1_nom"):
    return DatastorePg._row_to_dict(db.rows["r1"]).get(f"{key}.origine")


# --- les quatre cas de la campagne, en SÉQUENCE ------------------------------------

def test_a_flat_rewrite_keeps_the_origin(store):
    """LE cas qui échouait. `update_row` est le patch par `id` — le geste le plus
    courant d'un agent, et celui que ma correction initiale avait manqué."""
    s, db = store
    s.update_row("t", "r1", {"contact1_nom": {"valeur": "DUPONT Jean",
                                              "origine": "DUPONT Jean"}})
    s.update_row("t", "r1", {"contact1_nom": "MARTIN Claire"})
    assert _val(db) == "MARTIN Claire"
    assert _origine(db) == "DUPONT Jean", "l'origine ne doit pas suivre la valeur"


def test_a_layered_rewrite_without_origin_keeps_it(store):
    s, db = store
    s.update_row("t", "r1", {"contact1_nom": {"valeur": "DUPONT Jean",
                                              "origine": "DUPONT Jean"}})
    s.update_row("t", "r1", {"contact1_nom": {"valeur": "DURAND Paul",
                                              "comment": "registre"}})
    assert _val(db) == "DURAND Paul"
    assert _origine(db) == "DUPONT Jean"


def test_an_explicit_origin_replaces_it(store):
    """Pas de verrou : un ré-import repose une nouvelle valeur de départ."""
    s, db = store
    s.update_row("t", "r1", {"contact1_nom": {"valeur": "x", "origine": "vieux"}})
    s.update_row("t", "r1", {"contact1_nom": {"valeur": "y", "origine": "neuf"}})
    assert _origine(db) == "neuf"


def test_the_socle_import_then_the_agent(store):
    """Le flux de la campagne de bout en bout : le socle client pose l'origine sur un
    champ qu'aucun agent n'a renseigné, puis l'agent le renseigne. C'est le cas
    NOMINAL, et c'est celui où la lecture rendait l'enveloppe."""
    s, db = store
    s.update_row("t", "r1", {"contact1_nom": {"origine": "SOCLE Client"}})
    assert _val(db) is None, "pas d'objet rendu : la valeur n'est pas encore posée"
    assert _origine(db) == "SOCLE Client"
    s.update_row("t", "r1", {"contact1_nom": "Renseigné par agent"})
    assert _val(db) == "Renseigné par agent"
    assert _origine(db) == "SOCLE Client"


# --- le croisement que personne ne couvrait -----------------------------------

def test_the_write_protection_still_applies_to_a_layered_column(monkeypatch, store):
    """Les tests du verrou portent sur le bail, les miens sur les couches — le
    CROISEMENT n'était couvert par personne. Une colonne à couches ne doit pas
    échapper à la protection d'écriture parce qu'elle passe par une autre fusion."""
    from oto_mcp.datastore import core as ds
    s, db = store
    appels = []
    monkeypatch.setattr(s, "_assert_writable",
                        lambda ns_id, row_id: appels.append(row_id))
    s.update_row("t", "r1", {"contact1_nom": {"valeur": "x", "origine": "o"}})
    assert appels == ["r1"], "la garde de bail doit s'appliquer AUSSI sur une couche"
