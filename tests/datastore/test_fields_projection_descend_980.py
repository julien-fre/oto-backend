"""`fields` doit projeter DÈS `_row_to_dict`, pas après coup (oto-backend#980, lot 2).

Mesuré (17/09/2026, oto cd) : un ramassage complet du vivier par `GET
/api/datastores/{datastore}/rows` coûtait 10,3 à 10,8 s de calcul Python et
70-76 Mo de JSON. Sur une page de 500 lignes, `_row_to_dict` faisait 0,56 s
cumulé, dont `flat_layers` appelée ~38 000 fois — chaque colonne DÉCLARÉE de
chaque ligne était aplatie et servie, `null` compris, avant d'être jetée par le
filtrage a posteriori (`_project_row`) pour les colonnes non demandées.

⚠️ Banc de PROPORTION, jamais de durée : une assertion en millisecondes mesure
la machine qui l'exécute, pas le code. Ce fichier compare des COMPTES d'appels
à `flat_layers`, avec et sans projection, sur la MÊME page."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from oto_mcp.datastore import core as D
from oto_mcp.datastore import schema as dsv2

_SCHEMA = {"fields": [{"key": f"c{i}"} for i in range(20)]}
_ROW = {
    "row_id": "r01", "created_at": "t", "updated_at": "t",
    "data": {f"c{i}": f"v{i}" for i in range(20)},
}


def test_projection_reduit_les_appels_a_flat_layers():
    """Demander 2 colonnes sur 20 doit appeler `flat_layers` ~2 fois, pas ~20."""
    reel = dsv2.flat_layers
    with patch.object(dsv2, "flat_layers", wraps=reel) as espion:
        D.DatastorePg._row_to_dict(_ROW, _SCHEMA, fields=frozenset({"c0", "c1"}))
        appels_projetes = espion.call_count
    espion.reset_mock()
    with patch.object(dsv2, "flat_layers", wraps=reel) as espion:
        D.DatastorePg._row_to_dict(_ROW, _SCHEMA, fields=None)
        appels_complets = espion.call_count
    assert appels_projetes <= 2, appels_projetes
    assert appels_complets >= 20, appels_complets
    assert appels_projetes < appels_complets / 5


def test_sans_fields_le_payload_est_identique_a_avant_ce_lot():
    """`fields=None` (l'écrasante majorité des appelants existants) ne doit RIEN
    changer — même colonnes, mêmes clés méta."""
    out = D.DatastorePg._row_to_dict(_ROW, _SCHEMA, fields=None)
    assert set(out) >= {"_id", "_created_at", "_updated_at"} | {f"c{i}" for i in range(20)}


def test_projection_garde_toujours_id_meme_non_demande():
    out = D.DatastorePg._row_to_dict(_ROW, _SCHEMA, fields=frozenset({"c0"}))
    assert out == {"_id": "r01", "c0": "v0"}


def test_projection_TOUT_jamais_passee_ici_reste_hors_scope():
    """Le jeton `*` se résout en amont (`cursor_rows`/`page_rows`, `fields=None`
    passé au noyau) — `_row_to_dict` ne connaît que `None` ou un set concret."""
    out = D.DatastorePg._row_to_dict(_ROW, _SCHEMA, fields=frozenset({"c0", "c1"}))
    assert set(out) == {"_id", "c0", "c1"}


def test_une_couche_ne_survit_que_nommee_elle_meme():
    """Demander `c0` ne fait pas apparaître `c0.origine` — même règle que l'ancien
    filtrage a posteriori (`_project_row`)."""
    row = {"row_id": "r02", "created_at": "t", "updated_at": "t",
           "data": {"c0": {"valeur": "v", "origine": "import"}}}
    out = D.DatastorePg._row_to_dict(row, _SCHEMA, fields=frozenset({"c0"}))
    assert "c0.origine" not in out
    out2 = D.DatastorePg._row_to_dict(row, _SCHEMA, fields=frozenset({"c0", "c0.origine"}))
    assert out2.get("c0.origine") == "import"


# ── l'ordre, pas seulement le résultat : rougit si la projection repasse après ──

def test_ordre_la_projection_doit_operer_AVANT_l_aplatissement():
    """Un correctif qui calculerait la ligne complète puis filtrerait ferait le
    MÊME nombre d'appels à `flat_layers` qu'une lecture non projetée — c'est
    précisément le défaut que ce lot corrige. Ce banc rougit si quelqu'un
    réintroduit ce chemin."""
    reel = dsv2.flat_layers
    with patch.object(dsv2, "flat_layers", wraps=reel) as espion:
        D.DatastorePg._row_to_dict(_ROW, _SCHEMA, fields=frozenset({"c0"}))
    assert espion.call_count == 1, (
        "flat_layers a été appelée pour des colonnes non demandées : "
        "la projection est appliquée APRÈS l'aplatissement, pas avant")


@pytest.fixture
def store(monkeypatch):
    rows = [{"row_id": f"r{i:02d}", "created_at": "t", "updated_at": "t",
             "data": {f"c{i}": f"v{i}" for i in range(20)}} for i in range(1, 4)]

    def _fake_page(ns_id, *, offset=0, limit=50, order_by=None, order_dir="desc",
                   q=None, filters=None, order_type=None, order_options=None):
        return rows[offset:offset + limit]

    def _fake_count(ns_id, *, q=None, filters=None):
        return len(rows)

    monkeypatch.setattr(D.db, "datastore_list_rows", _fake_page)
    monkeypatch.setattr(D.db, "datastore_count_rows", _fake_count)
    s = D.DatastorePg("u1")
    monkeypatch.setattr(s, "_resolve", lambda ns, write=False: 1)
    monkeypatch.setattr(s, "_schema_of", lambda ns_id: _SCHEMA)
    return s


def test_page_rows_fields_absent_meme_payload_qu_avant(store):
    """Contrat REST inchangé sans `fields` (#980 lot 2 : additif, jamais cassant)."""
    page = store.page_rows("ns", limit=10)
    assert set(page["rows"][0]) == {"_id", "_created_at", "_updated_at"} | {
        f"c{i}" for i in range(20)}


def test_page_rows_fields_projette_chaque_ligne(store):
    page = store.page_rows("ns", limit=10, fields=["c0", "c1"])
    assert all(set(r) == {"_id", "c0", "c1"} for r in page["rows"])


def test_page_rows_fields_etoile_egale_absence(store):
    page = store.page_rows("ns", limit=10, fields=["*"])
    assert set(page["rows"][0]) == {"_id", "_created_at", "_updated_at"} | {
        f"c{i}" for i in range(20)}
