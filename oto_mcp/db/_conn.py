"""Plomberie de connexion PG : pool psycopg, row factory, bornes serveur.

Extrait de l'ex-monolithe `db.py` (barreau 2). Aucune logique métier ici —
juste le pool, le `_connect()` context manager et les helpers de normalisation
de row. Importé par tous les modules de domaine du package `db`.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import date, datetime
from typing import Any, Iterator, Optional

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .. import providers

def _normalize_value(v: Any) -> Any:
    # Match the string shape SQLite returned ("YYYY-MM-DD HH:MM:SS") so downstream
    # JSONResponse + frontends keep working unchanged.
    if isinstance(v, datetime):
        return v.replace(tzinfo=None, microsecond=0).isoformat(sep=" ")
    if isinstance(v, date):
        return v.isoformat()
    return v


def _str_dict_row(cursor):
    inner = dict_row(cursor)

    def make_row(values):
        d = inner(values)
        if d:
            for k, v in d.items():
                if isinstance(v, (datetime, date)):
                    d[k] = _normalize_value(v)
        return d

    return make_row


# Providers supportés pour les user keys. DÉRIVÉ du registre source unique
# (`providers/`) — ne plus éditer ici, déclarer le connecteur dans le registre.
KEY_PROVIDERS = providers.KEY_PROVIDERS
# Ensemble plus large des providers pouvant détenir un credential (keyed + sessions
# cookie + byo multi-champs) — garde-fou d'écriture `keys._check_provider`.
CREDENTIAL_PROVIDERS = providers.CREDENTIAL_PROVIDERS


_pool: Optional[ConnectionPool] = None


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL not set (managed PG connection string)")
    return url


def _connect_options() -> str:
    """Bornes serveur posées par connexion via l'option libpq `options` (issue #70).

    `idle_in_transaction_session_timeout` (défaut 60 s) tue une transaction laissée
    IDLE → empêche qu'un process hangé laisse une connexion zombie tenant un lock
    qui bloquerait le boot suivant (`init_db`, incident 2026-06-25). Sans effet sur
    une requête EN COURS (seules les txns inactives sont coupées).

    `statement_timeout` est **opt-in** (défaut 0 = off) : on ne l'active pas par
    défaut car un `CREATE INDEX` de migration sur une grosse table (tool_calls,
    datastore_rows) pourrait dépasser le seuil au boot. Le borné cold-S3 du scan
    SIRENE est déjà porté par le service FOD (watchdog 90 s), pas par ce pool.
    """
    idle = os.environ.get("OTO_MCP_DB_IDLE_TX_TIMEOUT_MS", "60000")
    stmt = os.environ.get("OTO_MCP_DB_STATEMENT_TIMEOUT_MS", "0")
    parts = [f"-c idle_in_transaction_session_timeout={idle}"]
    if stmt and stmt != "0":
        parts.append(f"-c statement_timeout={stmt}")
    return " ".join(parts)


# Bornes du DDL À CHAUD (incident du 2026-09-01, `docs/event-loop-perf.md` mode n°4).
#
# `CREATE INDEX CONCURRENTLY` n'a pas de durée propre : avant de construire, il ATTEND
# la fin de toute transaction ouverte avant lui — y compris une simple LECTURE, qui ne
# pose pourtant aucun verrou gênant. Une requête d'analyse lancée à la main et laissée
# 47 min l'a donc retenu 47 min. Ces attentes sont des attentes de VERROU (le waiter
# prend un `ShareLock` sur le VXID de chaque transaction plus ancienne) : `lock_timeout`
# les coupe. `statement_timeout` borne en plus la construction elle-même, pour le cas où
# la table aurait grossi hors de toute mesure — mesuré 40 ms pour 50 000 lignes, donc
# 60 s laissent trois ordres de grandeur de marge.
#
# ⚠️ Ces bornes ne valent QUE pour le DDL à chaud (`_connect_autocommit`). Le pool
# ordinaire garde `statement_timeout=0` : c'est lui qui porte les migrations de boot, où
# un index sur une grosse table a le droit de prendre son temps (personne ne sert encore).
_DDL_LOCK_TIMEOUT_MS = "OTO_MCP_DDL_LOCK_TIMEOUT_MS"
_DDL_STATEMENT_TIMEOUT_MS = "OTO_MCP_DDL_STATEMENT_TIMEOUT_MS"


def _ddl_options() -> str:
    """`_connect_options()` + les deux bornes du DDL à chaud. `0` désarme une borne."""
    parts = [_connect_options()]
    lock = os.environ.get(_DDL_LOCK_TIMEOUT_MS, "5000")
    stmt = os.environ.get(_DDL_STATEMENT_TIMEOUT_MS, "60000")
    if lock and lock != "0":
        parts.append(f"-c lock_timeout={lock}")
    if stmt and stmt != "0":
        parts.append(f"-c statement_timeout={stmt}")
    return " ".join(parts)


def _get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            conninfo=_database_url(),
            min_size=1,
            # 24 et non 40 (taille du threadpool anyio) : un pool PLUS PETIT que le
            # threadpool est ce qui fait qu'une rafale échoue proprement en 5 s
            # au lieu de saturer la base. Les aligner supprimerait le signal.
            # Marge PG : 3 process (prod, canari, preprod) x 24 = 72 sur 150,
            # base MANAGÉE PARTAGÉE — le reste va aux migrations, sondes et
            # opérations manuelles. Monté de 8 le 2026-09-03 : le seam des
            # capacités ayant sorti 285 handlers de la boucle, jusqu'à 40
            # traitements se disputent désormais le pool (mesuré : 7/8 en
            # heure CREUSE, avant tout pic).
            max_size=int(os.environ.get("OTO_MCP_DB_POOL_MAX", "24")),
            kwargs={"row_factory": _str_dict_row, "options": _connect_options()},
            open=True,
            # Attente MAX d'une connexion (défaut psycopg_pool : 30s !). Pendant un
            # blip DB (SSL eof, saturation), le pool se vide et `getconn` ATTEND —
            # depuis un chemin sync dans l'event loop (ex. _authenticate), c'est le
            # serveur ENTIER qui gèle. 5s ⇒ PoolTimeout → 500 propre, pas un down.
            # Vécu 2026-07-02 (2 gels, py-spy : getconn wait sous _authenticate).
            timeout=float(os.environ.get("OTO_MCP_DB_POOL_TIMEOUT", "5") or "5"),
        )
    return _pool


@contextmanager
def _connect() -> Iterator[psycopg.Connection]:
    pool = _get_pool()
    with pool.connection() as conn:
        yield conn


@contextmanager
def _connect_autocommit(*, bornee: bool = True) -> Iterator[psycopg.Connection]:
    """Connexion HORS pool, en autocommit — pour le seul DDL qui l'exige.

    `CREATE INDEX CONCURRENTLY` est REFUSÉ dans un bloc transactionnel (« cannot run
    inside a transaction block », vérifié), et le pool en ouvre un. Or c'est
    précisément la forme qui ne bloque pas les écritures pendant la construction :
    sans elle, poser un index d'unicité sur une grosse table gèlerait les écritures
    de tout le monde le temps du scan.

    Hors pool et à usage strictement local : on n'expose pas une connexion sans
    transaction à du code métier, qui perdrait l'atomicité sans le voir.

    `bornee=True` (défaut) pose `lock_timeout`/`statement_timeout` : un DDL à chaud qui
    attend sans fin est un gel de production, pas une lenteur. Cf. l'incident du
    2026-09-01 — 12 min 48 s sans une seule réponse, derrière une requête d'analyse
    lancée à la main qui tournait depuis 47 min.

    `bornee=False` pour un travail de FOND (timer de maintenance, migration de boot) :
    là, attendre ne dessert personne, et une borne ne ferait que garantir qu'un index
    sur une table très occupée ne se pose jamais."""
    options = _ddl_options() if bornee else _connect_options()
    with psycopg.connect(_database_url(), options=options,
                         row_factory=_str_dict_row, autocommit=True) as conn:
        yield conn
