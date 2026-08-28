"""Un refus SÉMANTIQUE de filtre doit dire pourquoi, pas rendre `invalid_filters` nu.

Trouvé au smoke prod de v1.87.0, invisible aux tests du store : la garde `null` de
#306 nomme l'opérateur `empty` dans son message, et la capacité écrasait ce message
par le seul code `invalid_filters` — celui-là même que rend un JSON MALFORMÉ.

L'appelant reçoit donc le même refus pour deux causes opposées : « ta syntaxe JSON est
cassée » et « ton filtre est syntaxiquement bon mais ne veut rien dire ». Il relit sa
syntaxe, qui est correcte, et n'a aucun moyen d'arriver à `empty`.

Même famille que le 500→400 de #390 : le store sait dire pourquoi il refuse, la
surface le jette.
"""
from __future__ import annotations

import pytest

from oto_mcp.capabilities import datastore_rows as dr
from oto_mcp.capabilities._types import AuthzDenied


class _Ctx:
    sub = "u-1"


class _Store:
    """Store qui refuse comme le vrai : `ValueError` porteuse du remède."""

    MSG = ("valeur de filtre `null` : `data ->> champ` ne distingue pas un JSON "
           "`null` d'une clé absente — utiliser l'opérateur `empty`.")

    def page_rows(self, *a, **k):
        raise ValueError(self.MSG)

    def aggregate(self, *a, **k):
        raise ValueError("métrique inconnue `median`")


@pytest.fixture()
def store(monkeypatch):
    monkeypatch.setattr(dr, "make_store", lambda sub: _Store())


def test_list_rows_passes_the_reason_through(store):
    inp = dr.ListRowsInput(namespace="160",
                           filters='[{"field":"x","op":"eq","value":null}]')
    with pytest.raises(AuthzDenied) as e:
        dr._list_rows(_Ctx(), inp)
    assert e.value.status == 400
    assert e.value.code == "invalid_filters"
    # Le remède doit ARRIVER : c'est lui qui rend la reprise mécanique.
    assert "empty" in e.value.message


def test_aggregate_passes_the_reason_through(store):
    inp = dr.AggregateInput(namespace="160", metrics='["median:ca"]')
    with pytest.raises(AuthzDenied) as e:
        dr._aggregate(_Ctx(), inp)
    assert e.value.status == 400
    assert "median" in e.value.message


def test_malformed_json_stays_distinguishable(store):
    """Le refus de PARSING garde son code nu — c'est voulu : il n'a pas de remède
    métier à donner, et le distinguer du refus sémantique est tout l'intérêt."""
    inp = dr.ListRowsInput(namespace="160", filters="pas du json")
    with pytest.raises(AuthzDenied) as e:
        dr._list_rows(_Ctx(), inp)
    assert e.value.code == "invalid_filters"
    assert not e.value.message, "un JSON cassé n'a pas de message métier à porter"
