"""Les capacités du datastore résolvent `slot:<nom>` comme les tools `data_*`.

Vécu : `data_drop_column` (capacité) a refusé seize purges d'affilée avec
`namespace_not_found` parce qu'il recevait `slot:vivier` comme un nom littéral. Le
tool `@mcp.tool()` qu'il remplace résolvait la référence ; la conversion en capacité
l'a perdue, le helper vivant dans `tools/datastore.py`. `data_get_schema`, converti
plus tôt, portait le même trou.

Sur un verbe destructif l'échec est heureux — mais la même lacune sur une LECTURE
répond « tableau inconnu » à un tableau qui existe, et sur un verbe qui écrirait,
elle viserait le mauvais tableau. D'où la source unique
`access.resolve_namespace_ref` et ce test : toute capacité datastore qui prend un
`namespace` doit passer par elle.
"""
from __future__ import annotations

import pytest

from oto_mcp import access
from oto_mcp.capabilities.datastore import columns as cols
from oto_mcp.capabilities.datastore import schema as sch


class _Ctx:
    sub = "u"


@pytest.fixture()
def resolved(monkeypatch):
    """`slot:vivier` → le nom réel, comme le ferait un binding de projet."""
    seen = []
    monkeypatch.setattr(access, "resolve_slot_tableau",
                        lambda name: (seen.append(name) or "edition-echantillon-500"))
    return seen


def test_bare_name_passes_through_untouched():
    assert access.resolve_namespace_ref("mon-tableau") == "mon-tableau"


def test_slot_is_resolved_to_the_real_namespace(resolved):
    assert access.resolve_namespace_ref("slot:vivier") == "edition-echantillon-500"
    assert resolved == ["vivier"]


def test_drop_column_resolves_the_slot(monkeypatch, resolved):
    """LE cas des seize refus."""
    called = {}

    class _Store:
        def drop_column(self, namespace, key, *, confirm):
            called.update(namespace=namespace, key=key, confirm=confirm)
            return {"namespace": namespace, "key": key, "rows": 3}

    monkeypatch.setattr(cols, "make_store", lambda sub: _Store())
    out = cols._drop_column(_Ctx(), cols.DropColumnInput(
        namespace="slot:vivier", key="actualite_sociale", confirm=True))
    assert called["namespace"] == "edition-echantillon-500"
    assert out["rows"] == 3


def test_get_schema_resolves_the_slot_and_answers_with_the_real_name(monkeypatch, resolved):
    class _Store:
        def get_schema(self, namespace):
            assert namespace == "edition-echantillon-500"
            return {"strict": True, "fields": []}

    monkeypatch.setattr(sch, "make_store", lambda sub: _Store())
    out = sch._get_schema(_Ctx(), sch.GetSchemaInput(namespace="slot:vivier"))
    # le nom RÉSOLU revient : l'appelant doit voir sur quel tableau il a lu
    assert out["namespace"] == "edition-echantillon-500"
    assert out["schema"]["strict"] is True
