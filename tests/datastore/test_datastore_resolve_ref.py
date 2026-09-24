"""resolve_datastore_ns accepte id OU nom (fix « Aperçu indisponible » : le picker projet
stocke le target_ref = id numérique, l'endpoint résolvait par nom → 404). On capture les
params passés à SQL — un ref tout-chiffres pose `nsid` (int), un nom laisse `nsid=None`.
La sémantique SQL réelle (anti-IDOR) est validée contre un vrai Postgres ; l'ambiguïté
chiffres = id d'un tableau ET nom d'un autre (#365) l'est ici et sur le montage réel
(`tests/test_homonymes_ponts_365.py`)."""
from __future__ import annotations

import contextlib

import pytest

from oto_mcp.db import datastore as DB


class _Cur:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row

    def fetchall(self):
        # Une liste de lignes rangées comme le SQL les range (le nom d'abord) ; une
        # ligne seule reste une ligne seule.
        if self._row is None:
            return []
        return list(self._row) if isinstance(self._row, list) else [self._row]


class _Conn:
    def __init__(self, cap, row):
        self.cap, self.row = cap, row

    def execute(self, sql, params=None):
        self.cap["sql"] = sql
        self.cap["params"] = params
        return _Cur(self.row)


def _patch(monkeypatch, cap, row=None):
    @contextlib.contextmanager
    def _fake():
        yield _Conn(cap, row)
    # `resolve_datastore_ns` vit dans le module du TABLEAU depuis le découpage (#325) :
    # c'est là que sa connexion se patche. Patcher l'ancien module rendait le test vert
    # sur un chemin qu'il n'exerçait plus.
    from oto_mcp.db import datastore_ns
    monkeypatch.setattr(datastore_ns, "_connect", _fake)


def test_digit_ref_sets_nsid_int(monkeypatch):
    cap = {}
    _patch(monkeypatch, cap, row={"id": 109})
    DB.resolve_datastore_ns("109", sub="u1", org_ids=[42], group_ids=[])
    assert cap["params"]["nsid"] == 109          # id numérique posé
    assert cap["params"]["ns"] == "109"          # nom conservé aussi (OR)
    assert "d.id = %(nsid)s" in cap["sql"]


def test_name_ref_leaves_nsid_none(monkeypatch):
    cap = {}
    _patch(monkeypatch, cap, row={"id": 5})
    DB.resolve_datastore_ns("vivier-pmi", sub="u1", org_ids=[42], group_ids=[])
    assert cap["params"]["nsid"] is None         # pas un id → NULL → jamais de match id
    assert cap["params"]["ns"] == "vivier-pmi"


def test_visibility_predicate_still_present(monkeypatch):
    # anti-IDOR : le prédicat de visibilité (owner/org/grant) reste dans le WHERE.
    cap = {}
    _patch(monkeypatch, cap, row=None)
    DB.resolve_datastore_ns("109", sub="u1", org_ids=[42], group_ids=[])
    sql = cap["sql"]
    assert "resource_grants" in sql and "d.owner_id = %(sub)s" in sql


def test_des_chiffres_qui_sont_AUSSI_le_nom_d_un_autre_tableau_sont_refuses(monkeypatch):
    """#365 : le nom gagnait en silence — un tableau NOMMÉ « 109 » captait tout ce qui
    visait le tableau 109, et les ponts adressent désormais les tableaux par numéro."""
    cap = {}
    _patch(monkeypatch, cap, row=[{"id": 7, "datastore": "109"}, {"id": 109, "datastore": "x"}])
    with pytest.raises(DB.AdresseAmbigue) as e:
        DB.resolve_datastore_ns("109", sub="u1", org_ids=[42], group_ids=[])
    assert (e.value.par_id, e.value.par_nom) == (109, 7)


def test_le_nom_qui_EST_l_identifiant_n_est_pas_ambigu(monkeypatch):
    cap = {}
    _patch(monkeypatch, cap, row=[{"id": 109, "datastore": "109"}])
    assert DB.resolve_datastore_ns("109", sub="u1", org_ids=[42], group_ids=[])["id"] == 109
