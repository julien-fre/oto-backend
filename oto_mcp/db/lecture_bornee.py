"""Lecture d'agrégat BORNÉE : un `statement_timeout` court, posé pour sa transaction.

oto-backend#1145. Le 04/10/2026, la base partagée a basculé six fois : quelques
lectures d'agrégat sur `tool_calls` (relevés d'org, usage d'une procédure, activité,
supervision) tenaient chacune une connexion de 25 s à plus de 7 min, et une rafale
suffisait à prendre toute la réserve du pool. Le pool applicatif ne pose aucun
`statement_timeout` (`_conn._connect_options` : opt-in, à cause des migrations de
démarrage) ; ces lectures-là en reçoivent un, court, à LEUR niveau.

`lecture_d_agregat()` remplace `_connect()` dans une lecture d'agrégat : même connexion
du pool, dans une transaction explicite où `SET LOCAL statement_timeout` vaut
`DUREE_MAX_MS` — `LOCAL`, donc rendu à la fin de la transaction, jamais laissé sur une
connexion qui repart au pool. Une lecture qui dépasse lève `LectureTropLongue`, NOMMÉE :
jamais un résultat partiel, jamais un repli silencieux. La face (REST ou MCP) en fait
un refus explicite (`capabilities/_lecture_bornee.py`).

`lectures_bornees()` borne de même une portée de lectures ORDINAIRES (le store d'un
tableau, `cursor_rows`) qu'on ne peut pas réécrire en une requête : chaque connexion
empruntée dedans reçoit le même `SET LOCAL statement_timeout`.

Ce qu'elle ne fait pas : borner l'ATTENTE d'une connexion (c'est le pool, 5 s) ni
limiter le nombre de lectures simultanées (c'est le budget des routes lourdes).
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator, Optional

import psycopg

from . import _conn
from ._conn import _connect

#: La durée maximale d'une lecture d'agrégat. Dix secondes : au-delà, la lecture tient
#: une connexion d'une réserve partagée par toute la plateforme, pour une réponse que
#: personne n'attend plus (le front abandonne avant).
DUREE_MAX_MS = 10_000


class LectureTropLongue(RuntimeError):
    """Une lecture d'agrégat a dépassé `DUREE_MAX_MS` et PostgreSQL l'a annulée."""

    def __init__(self, objet: str, duree_ms: int) -> None:
        super().__init__(
            f"{objet} : lecture interrompue après {duree_ms // 1000} s (borne des "
            "lectures d'agrégat). Resserrer la fenêtre ou le périmètre.")
        self.objet = objet
        self.duree_ms = duree_ms


@contextmanager
def lecture_d_agregat(objet: str, *, isolation: Optional[str] = None
                      ) -> Iterator[psycopg.Connection]:
    """La connexion d'une lecture d'agrégat, sous `statement_timeout` court.

    `objet` nomme la lecture dans l'erreur (« relevé par outil », « usage d'une
    procédure »…). `isolation="REPEATABLE READ"` pour une lecture en plusieurs requêtes
    qui doivent partager un snapshot : posé en PREMIER, comme PostgreSQL l'exige."""
    duree = DUREE_MAX_MS
    with _connect() as conn, conn.transaction():
        if isolation is not None:
            if isolation not in ("REPEATABLE READ", "SERIALIZABLE"):
                raise ValueError(f"isolation non supportée : {isolation!r}")
            conn.execute(f"SET TRANSACTION ISOLATION LEVEL {isolation}")
        conn.execute(f"SET LOCAL statement_timeout = {int(duree)}")
        try:
            yield conn
        except psycopg.errors.QueryCanceled as e:
            raise LectureTropLongue(objet, duree) from e


@contextmanager
def lectures_bornees(objet: str, duree_ms: int = DUREE_MAX_MS) -> Iterator[None]:
    """Une portée où chaque requête du pool est bornée à `duree_ms` : les lectures d'un
    tableau par le store (`cursor_rows`), qu'aucune requête unique ne remplace.

    ⚠️ LECTURES seulement : chaque `_connect()` dedans ouvre une transaction explicite
    (le `SET LOCAL` l'exige) ; un chemin qui y validerait lui-même (`commit()`)
    serait refusé par psycopg. Une requête annulée lève `LectureTropLongue`, nommée."""
    jeton = _conn._duree_bornee_ms.set(int(duree_ms))
    try:
        yield
    except psycopg.errors.QueryCanceled as e:
        raise LectureTropLongue(objet, duree_ms) from e
    finally:
        _conn._duree_bornee_ms.reset(jeton)
