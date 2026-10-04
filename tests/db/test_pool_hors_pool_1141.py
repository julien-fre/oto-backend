"""Le pool rend ce qui ne sert plus, et les connexions HORS pool ont un plafond (#1141).

Deux bornes, sans base :

- `max_idle` : une connexion inactive au-delà de `min_size` est rendue (60 s par
  défaut, `OTO_MCP_DB_POOL_MAX_IDLE`) — le défaut de psycopg_pool (600 s) laissait le
  pool tenir ce qu'un pic avait ouvert ;
- `_connect_autocommit` prend une PLACE avant d'ouvrir sa connexion : le chemin servi
  (`bornee=True`) n'attend qu'un délai fini puis refuse (`HorsPoolSature`), traduit par
  le datastore en ses refus d'index déjà nommés ; le travail de fond attend son tour.
"""
from __future__ import annotations

import threading
from contextlib import contextmanager

import pytest

from oto_mcp.db import _conn
from oto_mcp.db import datastore as dsdb


# ── max_idle ─────────────────────────────────────────────────────────────────

@pytest.fixture
def pool_espion(monkeypatch):
    vus: dict = {}

    class _Pool:
        def __init__(self, **kwargs):
            vus.update(kwargs)

    monkeypatch.setattr(_conn, "ConnectionPool", _Pool)
    monkeypatch.setattr(_conn, "_pool", None)
    monkeypatch.setattr(_conn, "_database_url", lambda: "postgresql://factice")
    return vus


def test_le_pool_rend_une_connexion_inactive_apres_60_s_par_defaut(pool_espion, monkeypatch):
    monkeypatch.delenv("OTO_MCP_DB_POOL_MAX_IDLE", raising=False)
    _conn._get_pool()
    assert pool_espion["max_idle"] == 60.0


def test_max_idle_se_regle_par_l_environnement(pool_espion, monkeypatch):
    monkeypatch.setenv("OTO_MCP_DB_POOL_MAX_IDLE", "15")
    _conn._get_pool()
    assert pool_espion["max_idle"] == 15.0


def test_max_idle_zero_rend_la_borne_de_psycopg_pool(pool_espion, monkeypatch):
    monkeypatch.setenv("OTO_MCP_DB_POOL_MAX_IDLE", "0")
    _conn._get_pool()
    assert pool_espion["max_idle"] == 600.0


# ── plafond hors pool ────────────────────────────────────────────────────────

@pytest.fixture
def hors_pool(monkeypatch):
    """Deux places, un délai d'attente court, une connexion factice qui compte les
    ouvertures simultanées."""
    monkeypatch.setenv("OTO_MCP_DB_HORS_POOL_MAX", "2")
    monkeypatch.setenv("OTO_MCP_DB_POOL_TIMEOUT", "0.05")
    monkeypatch.setattr(_conn, "_places_hors_pool", None)
    monkeypatch.setattr(_conn, "_database_url", lambda: "postgresql://factice")
    etat = {"ouvertes": 0, "max": 0}

    @contextmanager
    def _connexion(*_a, **_k):
        etat["ouvertes"] += 1
        etat["max"] = max(etat["max"], etat["ouvertes"])
        try:
            yield object()
        finally:
            etat["ouvertes"] -= 1

    monkeypatch.setattr(_conn.psycopg, "connect", _connexion)
    return etat


def test_au_dela_du_plafond_le_chemin_servi_refuse_sans_attendre_sans_fin(hors_pool):
    with _conn._connect_autocommit(), _conn._connect_autocommit():
        with pytest.raises(_conn.HorsPoolSature):
            with _conn._connect_autocommit():
                pass
    assert hors_pool["max"] == 2, "la troisième connexion ne doit jamais s'ouvrir"


def test_la_place_est_rendue_en_sortie_meme_sur_exception(hors_pool):
    for _ in range(3):
        with pytest.raises(ZeroDivisionError):
            with _conn._connect_autocommit():
                1 / 0
    with _conn._connect_autocommit(), _conn._connect_autocommit():
        pass
    assert hors_pool["ouvertes"] == 0


def test_le_travail_de_fond_attend_son_tour_au_lieu_de_refuser(hors_pool):
    """`bornee=False` (maintenance, migration) : pas de refus, une file."""
    passe = threading.Event()

    def _fond():
        with _conn._connect_autocommit(bornee=False):
            passe.set()

    with _conn._connect_autocommit(), _conn._connect_autocommit():
        t = threading.Thread(target=_fond)
        t.start()
        assert not passe.wait(0.2), "le fond ne doit pas dépasser le plafond"
    t.join(2)
    assert passe.is_set(), "une place rendue doit laisser passer le fond"
    assert hors_pool["max"] == 2


# ── traduction par le datastore ──────────────────────────────────────────────

@pytest.fixture
def sature(monkeypatch):
    @contextmanager
    def _plein(**_k):
        raise _conn.HorsPoolSature("plein")
        yield  # pragma: no cover

    monkeypatch.setattr(dsdb, "_connect_autocommit", _plein)


def test_pose_d_index_sans_place_est_le_refus_nomme_de_la_pose(sature):
    with pytest.raises(dsdb.KeyIndexUnavailable) as e:
        dsdb.datastore_ensure_key_index(1, "siren")
    assert "Le schéma EST écrit" in str(e.value)
    assert "aucune connexion de DDL n'était libre" in str(e.value)


def test_retrait_d_index_sans_place_est_le_refus_nomme_du_retrait(sature):
    with pytest.raises(dsdb.KeyIndexStillEnforced) as e:
        dsdb.datastore_drop_key_index(1)
    assert "TOUJOURS en place" in str(e.value)
