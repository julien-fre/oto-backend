"""oto-backend — `status_for` N+1, suite (17/09/2026).

#997 (mergée, en prod v1.305.0) a bien réduit les LECTURES d'instances de 106 à
~53 — mais mesuré ensuite en prod, la DURÉE n'a pas bougé (0,43-0,58s) : chaque
emprunt de connexion coûte 3 allers-retours (`BEGIN` + la requête + `COMMIT` posés
par le pool en mode non-autocommit), pas 1. Le banc de #997 comptait le nombre de
LECTURES d'instances — la mauvaise grandeur : il était vert et le temps n'a pas
bougé. Celui-ci compte les emprunts de connexion (chaque emprunt = un `BEGIN`/
`COMMIT` payé, donc l'unité qu'oto cd a mesurée en prod), pas les lectures.

Correctif : `db.reuse_connection()` (`oto_mcp/db/_conn.py`) emprunte UNE connexion
pour toute la portée du bloc — les `_connect()` imbriqués dedans la réutilisent au
lieu d'en emprunter une chacun. `status_for` (`oto_mcp/access/status.py`) enveloppe
tout son corps dedans.

⚠️ Banc STRUCTUREL, pas de durée : on compte des emprunts au pool, pas des
millisecondes.
"""
from __future__ import annotations

from contextlib import contextmanager

from oto_mcp import access
from oto_mcp.db import _conn


class _FauxPool:
    """Remplace `_get_pool()` : chaque entrée dans `.connection()` est un emprunt
    compté — pas de vraie base, la connexion rendue est un objet inerte."""

    def __init__(self):
        self.emprunts = 0

    @contextmanager
    def connection(self):
        self.emprunts += 1
        yield object()


def test_hors_reuse_connection_chaque_connect_emprunte_le_sien(monkeypatch):
    """Le comportement D'AVANT #998-suite, gardé comme référence : sans
    `reuse_connection()`, N appels à `_connect()` empruntent N fois."""
    faux_pool = _FauxPool()
    monkeypatch.setattr(_conn, "_get_pool", lambda: faux_pool)
    for _ in range(5):
        with _conn._connect():
            pass
    assert faux_pool.emprunts == 5


def test_dans_reuse_connection_un_seul_emprunt_quel_que_soit_N(monkeypatch):
    """Le cœur du correctif : à l'intérieur de `reuse_connection()`, le nombre
    d'emprunts reste à 1 — vérifié pour N=3 ET N=10, la preuve que ça ne dépend
    pas de N (même patron que le banc de #997, sur la bonne grandeur cette fois)."""
    for n in (3, 10):
        faux_pool = _FauxPool()
        monkeypatch.setattr(_conn, "_get_pool", lambda fp=faux_pool: fp)
        with _conn.reuse_connection():
            for _ in range(n):
                with _conn._connect():
                    pass
        assert faux_pool.emprunts == 1, (
            f"N={n} : {faux_pool.emprunts} emprunt(s) — devrait rester à 1 quel "
            "que soit le nombre de _connect() imbriqués.")


def test_reuse_connection_est_reentrant(monkeypatch):
    """Un `reuse_connection()` imbriqué dans un autre ne réemprunte pas — la
    connexion du bloc englobant sert aux deux (utile si un futur appelant de
    `status_for` a lui-même déjà ouvert une portée réutilisée)."""
    faux_pool = _FauxPool()
    monkeypatch.setattr(_conn, "_get_pool", lambda: faux_pool)
    with _conn.reuse_connection():
        with _conn.reuse_connection():
            with _conn._connect():
                pass
    assert faux_pool.emprunts == 1


def test_status_for_enveloppe_bien_son_appel_dans_reuse_connection(monkeypatch):
    """Preuve de câblage : `status_for` (la façade publique) ouvre bien la portée
    réutilisée AUTOUR de la projection — pas seulement la mécanique testée en
    isolation ci-dessus. `_status_for_projection` est stubbée : ce banc ne juge
    QUE l'enveloppe, la projection elle-même reste couverte par les bancs
    existants (`test_status_batch_platform.py`, `test_presence_batch.py`…)."""
    appels: list = []
    entrees_reuse: list = []

    @contextmanager
    def _reuse_espionne():
        entrees_reuse.append(True)
        yield

    def _projection_stub(sub, *, org, group):
        # Vérifie qu'on est bien DANS la portée réutilisée au moment de l'appel.
        appels.append((sub, org, group, len(entrees_reuse)))
        return {"role": "member", "providers": {}}

    monkeypatch.setattr(access.status.db, "reuse_connection", _reuse_espionne)
    monkeypatch.setattr(access.status, "_status_for_projection", _projection_stub)

    out = access.status_for("sub-banc")

    assert out == {"role": "member", "providers": {}}
    assert len(entrees_reuse) == 1, "reuse_connection() doit être ouvert exactement une fois"
    assert appels == [("sub-banc", access.status.scope._UNSET,
                       access.status.scope._UNSET, 1)], (
        "la projection doit s'exécuter APRÈS l'entrée dans reuse_connection()")
