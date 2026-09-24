"""`key=` sur une écriture UNITAIRE : accepté quand il nomme la clé métier DÉCLARÉE.

Signaux 986, 1125, 1135, 1154 (même procédure, quatre matins). L'idiome « upsert sur la
clé métier » s'écrit naturellement `data_write(key="<clé déclarée>", row={…})`, et le
refus (voulu depuis le 09/09 pour tout `key` inopérant) coûtait un aller-retour par
écriture — une ligne de journal de plusieurs milliers de caractères renvoyée entière.

Or dans ce cas précis le paramètre n'est PAS inopérant : l'écriture unitaire rapproche
déjà sur la clé déclarée (`append_row`). Il est donc réglé, et accepté. Tout le reste —
autre colonne, `id=` présent, valeur absente, tableau sans clé — reste refusé : c'est la
classe qui a coûté 172 500 jetons le 09/09.
"""
from __future__ import annotations

import asyncio

import pytest
from oto_mcp.mcp_errors import McpError

from oto_mcp.datastore import jetons

CLE = "covers_date"
NOM = "journal-des-runs"


def _tool():
    from fastmcp import FastMCP
    from oto_mcp.tools import datastore as D
    m = FastMCP("t")
    D.register(m)
    return asyncio.run(m.get_tool("data_write")).fn


class _Store:
    dernier_tableau = {"ns_id": 7, "datastore": NOM}

    def __init__(self, schema):
        self.schema = schema
        self.appends: list = []
        self.updates: list = []

    def get_schema(self, datastore):
        return self.schema

    def append_row(self, datastore, row, **k):
        self.appends.append(row)
        return {"_id": "r1", **row}

    def update_row(self, datastore, id, row, **k):
        self.updates.append((id, row))
        return {"_id": id, **row}

    def off_schema_report(self):
        return {}


@pytest.fixture
def store(monkeypatch):
    from oto_mcp.tools import datastore as D
    s = _Store({"key": CLE, "fields": [{"key": CLE}, {"key": "synthese"}]})
    monkeypatch.setattr(D, "_acting_store", lambda: s)
    monkeypatch.setattr(D, "_project_hint", lambda ns: None)
    return s


def test_la_cle_declaree_avec_sa_valeur_est_ACCEPTEE(store):
    """Le geste des quatre signaux : il écrit, en un seul appel, par le rapprochement
    que l'écriture unitaire fait déjà sur la clé déclarée."""
    out = _tool()(datastore=NOM, key=CLE, row={CLE: "2026-09-24", "synthese": "…"})
    assert store.appends == [{CLE: "2026-09-24", "synthese": "…"}]
    assert out["_id"] == "r1"


@pytest.mark.parametrize("appel", [
    {"key": "synthese", "row": {CLE: "2026-09-24", "synthese": "…"}},   # autre colonne
    {"key": CLE, "row": {"synthese": "…"}},                              # valeur absente
    {"key": CLE, "row": {CLE: "2026-09-24"}, "id": "r1"},                # id= vise déjà
    {"key": "@claimed", "row": {CLE: "2026-09-24"}},                     # jeton retiré
])
def test_tout_le_reste_reste_REFUSE_et_rien_n_est_ecrit(store, appel):
    with pytest.raises(McpError):
        _tool()(datastore=NOM, **appel)
    assert store.appends == [] and store.updates == []


def test_sans_cle_declaree_le_refus_le_dit(monkeypatch):
    from oto_mcp.tools import datastore as D
    s = _Store({"fields": [{"key": CLE}]})
    monkeypatch.setattr(D, "_acting_store", lambda: s)
    with pytest.raises(McpError) as e:
        _tool()(datastore=NOM, key=CLE, row={CLE: "x"})
    assert "ne déclare pas de clé métier" in e.value.error.message
    assert s.appends == []


def test_le_refus_nomme_la_cle_declaree_et_la_forme_en_lot():
    """Refusé, l'appelant repart avec les deux corrections d'une ligne : la clé qui
    serait acceptée, et la même ligne enveloppée dans `rows=[…]`."""
    msg = jetons.refus_de_key_sans_lot("synthese", CLE)
    assert f"`{CLE}`" in msg
    assert "même une seule ligne" in msg and "rows=[" in msg


def test_la_regle_pure():
    assert jetons.key_unitaire_redondant(CLE, CLE, {CLE: 0}, None)
    assert not jetons.key_unitaire_redondant(CLE, None, {CLE: 1}, None)
    assert not jetons.key_unitaire_redondant(CLE, CLE, {CLE: None}, None)
    assert not jetons.key_unitaire_redondant(CLE, CLE, {CLE: 1}, "r1")
    assert not jetons.key_unitaire_redondant("x", CLE, {"x": 1}, None)
