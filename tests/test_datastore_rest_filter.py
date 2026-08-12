"""La face REST du verbe « lister des lignes » honore `filter` (#303).

L'histoire : `GET …/rows` acceptait `filter` et l'IGNORAIT — la CLI (`oto data list
--filter k:v`) recevait toutes les lignes en croyant sa liste filtrée. Un mensonge
silencieux. La migration en capacité l'aurait transformé en refus (`unknown_fields`),
honnête mais cassant pour un consommateur existant.

La sortie retenue : déclarer `filter` ET le brancher. Le piège nommé dans l'issue
était de le déclarer sans l'implémenter — ce qui aurait recréé le mensonge avec un
contrat qui l'endosse. **D'où le test central : le filtre doit MORDRE**, pas
seulement être accepté.

Logique pure : store stubbé, aucun accès DB.
"""
from __future__ import annotations

import json

import pytest

from oto_mcp.capabilities import datastore_rows as dr
from oto_mcp.capabilities._types import AuthzDenied


class _Ctx:
    sub = "user-1"
    org_id = 2


class _Store:
    """Capture ce que le handler transmet — c'est tout l'objet du test."""

    def __init__(self):
        self.seen = {}

    def page_rows(self, namespace, **kw):
        self.seen = {"namespace": namespace, **kw}
        return {"rows": [], "total": 0, "offset": 0, "limit": 50}


@pytest.fixture
def store(monkeypatch):
    s = _Store()
    monkeypatch.setattr(dr, "make_store", lambda sub: s)
    return s


def test_le_filtre_atteint_le_store(store):
    """Le cœur de #303 : déclaré ET transmis. Un test qui vérifierait seulement
    l'absence de 400 laisserait passer exactement le bug d'origine."""
    dr._list_rows(_Ctx(), dr.ListRowsInput(
        namespace="leads", filter=json.dumps({"status": "pending"})))
    assert store.seen["filter"] == {"status": "pending"}


def test_les_deux_formes_de_filtre_cohabitent(store):
    """`filter` (égalité, chemin MCP et CLI) et `filters` (clauses riches, dashboard)
    doivent se cumuler : elles viennent de deux surfaces qui peuvent se croiser."""
    dr._list_rows(_Ctx(), dr.ListRowsInput(
        namespace="leads",
        filter=json.dumps({"status": "pending"}),
        filters=json.dumps([{"field": "score", "op": "gte", "value": 10}])))
    assert store.seen["filter"] == {"status": "pending"}
    assert store.seen["filters"] == [{"field": "score", "op": "gte", "value": 10}]


def test_sans_filtre_rien_n_est_invente(store):
    """Le cas nominal ne doit pas fabriquer de clause vide qui filtrerait tout."""
    dr._list_rows(_Ctx(), dr.ListRowsInput(namespace="leads"))
    assert store.seen["filter"] is None
    assert store.seen["filters"] is None


def test_un_filtre_illisible_est_refuse_et_nomme(store):
    """Refus distinct de `invalid_filters` : deux paramètres, deux diagnostics —
    sinon l'utilisateur corrige le mauvais."""
    with pytest.raises(AuthzDenied) as e:
        dr._list_rows(_Ctx(), dr.ListRowsInput(namespace="leads", filter="{pas du json"))
    assert e.value.code == "invalid_filter"


def test_un_filtre_de_la_mauvaise_forme_est_refuse(store):
    """Une LISTE passée là où un objet est attendu doit être refusée, pas silencieusement
    ignorée — c'est la classe de bug qu'on est en train de fermer."""
    with pytest.raises(AuthzDenied) as e:
        dr._list_rows(_Ctx(), dr.ListRowsInput(
            namespace="leads", filter=json.dumps([{"field": "status"}])))
    assert e.value.code == "invalid_filter"


def test_le_parametre_est_bien_declare_sur_la_capacite():
    """Garde-fou de contrat : la validation des capacités REFUSE les champs inconnus.
    Si `filter` disparaissait de l'Input, la CLI ne serait pas ignorée — elle serait
    REJETÉE en 400. Ce test fige la présence du champ, pas son comportement."""
    assert "filter" in dr.ListRowsInput.model_fields
    assert "q" in dr.ListRowsInput.model_fields


def test_le_store_convertit_le_filtre_en_clauses():
    """L'égalité `{col: val}` doit devenir une clause `eq` — le même motif que
    `aggregate`/`claim_next`/`cursor_rows`, pour qu'il n'existe qu'une façon de
    filtrer côté SQL."""
    from oto_mcp import datastore as ds

    captured = {}

    class _FakeDb:
        @staticmethod
        def datastore_list_rows(ns_id, **kw):
            captured["list"] = kw
            return []

        @staticmethod
        def datastore_count_rows(ns_id, **kw):
            captured["count"] = kw
            return 0

    store = ds.DatastorePg.__new__(ds.DatastorePg)
    store._resolve = lambda ns, write=False: 1
    store._row_to_dict = lambda r: r
    orig_db = ds.db
    ds.db = _FakeDb
    try:
        store.page_rows("leads", filter={"status": "pending"})
    finally:
        ds.db = orig_db

    assert captured["list"]["filters"] == [{"field": "status", "op": "eq", "value": "pending"}]
    # Le total doit décrire le même jeu que la page, sinon la pagination ment.
    assert captured["count"]["filters"] == captured["list"]["filters"]
