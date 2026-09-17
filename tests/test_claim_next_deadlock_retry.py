"""oto-backend — Sentry PYTHON-STARLETTE-8Z : `data_claim_next` remontait
`deadlock detected` au premier essai, sans jamais rejouer.

Mesuré (17/09/2026, journal d'appels) : le deadlock oppose une migration de boot
(`init_db`, `AccessExclusiveLock` sur des dizaines de tables dans une seule
transaction — cf. `db/_init.py::init_db`, incident du 2026-07-30) à un
`claim_next` en vol. Quand PG choisit ce dernier comme VICTIME, il n'a RIEN posé
— un rejeu est aussi sûr qu'un premier essai. `init_db` rejoue déjà sa propre
transaction ; ce lot fait le symétrique côté appelant.
"""
from __future__ import annotations

import psycopg
import pytest

from oto_mcp.db import rowlock


def test_un_deadlock_isole_est_rejoue_et_reussit(monkeypatch):
    appels = {"n": 0}

    def _once(ns_id, **kw):
        appels["n"] += 1
        if appels["n"] == 1:
            raise psycopg.errors.DeadlockDetected("deadlock detected")
        return {"row_id": "r1"}

    monkeypatch.setattr(rowlock, "_datastore_claim_next_once", _once)
    monkeypatch.setattr(rowlock.time, "sleep", lambda s: None)

    row = rowlock.datastore_claim_next(7, worker="w")

    assert row == {"row_id": "r1"}
    assert appels["n"] == 2, "un seul rejeu devait suffire"


def test_un_deadlock_persistant_leve_apres_le_dernier_essai(monkeypatch):
    appels = {"n": 0}

    def _once(ns_id, **kw):
        appels["n"] += 1
        raise psycopg.errors.DeadlockDetected("deadlock detected")

    monkeypatch.setattr(rowlock, "_datastore_claim_next_once", _once)
    monkeypatch.setattr(rowlock.time, "sleep", lambda s: None)
    monkeypatch.setenv(rowlock._CLAIM_DEADLOCK_ATTEMPTS, "3")

    with pytest.raises(psycopg.errors.DeadlockDetected):
        rowlock.datastore_claim_next(7, worker="w")

    assert appels["n"] == 3, "exactement le nombre d'essais déclaré, pas plus"


def test_aucun_deadlock_ne_rejoue_rien(monkeypatch):
    appels = {"n": 0}

    def _once(ns_id, **kw):
        appels["n"] += 1
        return None

    monkeypatch.setattr(rowlock, "_datastore_claim_next_once", _once)

    assert rowlock.datastore_claim_next(7, worker="w") is None
    assert appels["n"] == 1, "le chemin nominal ne coûte pas un essai de plus"
