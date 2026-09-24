"""Plomberie de connexion PG : pool psycopg, row factory, bornes serveur.

Extrait de l'ex-monolithe `db.py` (barreau 2). Aucune logique métier ici —
juste le pool, le `_connect()` context manager et les helpers de normalisation
de row. Importé par tous les modules de domaine du package `db`.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date, datetime
from typing import Any, Iterator, Optional

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .. import providers
from ..config import require_env
from . import _hors_boucle, journal_revisions

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
    return require_env("DATABASE_URL")


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
    # L'interrupteur du journal des révisions (oto#273) : lu par le déclencheur, par
    # connexion, donc pour les seules écritures de CE processus. Posé seulement coupé.
    if journal_revisions.journal_coupe():
        parts.append(f"-c {journal_revisions.REGLAGE_PG}=off")
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


class _EmpruntParesseux:
    """Emprunte au pool à la PREMIÈRE utilisation réelle, jamais avant — un bloc
    `reuse_connection()` dont le chemin ne fait finalement aucune requête (chemin
    entièrement mocké en test, ou tous les préchargements retombent en cache) ne
    touche ni le pool ni `DATABASE_URL`."""

    def __init__(self):
        self._cm = None
        self._conn: Optional[psycopg.Connection] = None

    def obtenir(self) -> psycopg.Connection:
        if self._conn is None:
            pool = _get_pool()
            self._cm = pool.connection()
            self._conn = self._cm.__enter__()
            # AUTOCOMMIT le temps du prêt (oto cd, 17/09/2026, revue de ce
            # lot) : sans lui, tout `status_for` tenait dans UNE SEULE
            # transaction PostgreSQL — une requête en erreur (attrapée par
            # l'appelant, ex. une clé illisible sur UN connecteur) mettait
            # TOUTE la transaction en échec, et chaque lecture suivante
            # levait `InFailedSqlTransaction` au lieu de simplement échouer,
            # elle. `status_for` ne fait QUE lire (garanti par
            # `reuse_connection`, ci-dessous) : chaque requête devient son
            # propre commit implicite, une erreur reste locale à elle, et le
            # `BEGIN`/`COMMIT` explicite disparaît (un aller-retour de moins
            # par emprunt, en plus du gain déjà obtenu en n'empruntant
            # qu'une fois). Remis à `False` dans `fermer()` avant que la
            # connexion ne reparte au pool — jamais une connexion en
            # autocommit ne doit atteindre un autre appelant.
            self._conn.autocommit = True
        return self._conn

    def fermer(self) -> None:
        if self._conn is not None:
            self._conn.autocommit = False
        if self._cm is not None:
            self._cm.__exit__(None, None, None)


# Emprunt partagé par `reuse_connection()` pour toute une portée — vide en temps
# normal, chaque `_connect()` emprunte alors la sienne au pool comme avant.
_emprunt_partage: ContextVar[Optional[_EmpruntParesseux]] = ContextVar(
    "_emprunt_partage", default=None)


@contextmanager
def reuse_connection() -> Iterator[None]:
    """Emprunte AU PLUS UNE connexion au pool pour toute la portée du bloc — à la
    première requête réelle, pas à l'entrée du bloc (cf. `_EmpruntParesseux`) — et
    les `_connect()` imbriqués dedans la RÉUTILISENT au lieu d'en emprunter une
    chacun (oto-backend, lot `status_for` N+1, 17/09/2026 — 53 emprunts mesurés sur
    un appel, chacun payant son `BEGIN`/`COMMIT` propre au pool : ~3 allers-retours
    par emprunt plutôt qu'1).

    ⚠️ **LECTURE SEULE, et désormais en AUTOCOMMIT** (revue oto cd, 17/09/2026) :
    chaque requête empruntée valide seule, immédiatement — jamais toutes ensemble
    à la sortie du bloc. Deux conséquences, dans le même sens : une écriture posée
    dedans serait visible des AUTRES connexions immédiatement, pas seulement à la
    sortie — n'enveloppe jamais un chemin qui écrit puis relit sa propre écriture
    en supposant l'isolation d'une transaction commune ; et une requête en ERREUR,
    attrapée par l'appelant, ne met en échec qu'ELLE-MÊME — les requêtes suivantes
    dans le même bloc restent utilisables (sans autocommit, PostgreSQL aurait mis
    TOUTE la transaction en échec, `InFailedSqlTransaction` sur la première lecture
    suivante). `status_for` est une PROJECTION (aucune écriture sur son chemin,
    vérifié : `journal_resolution` — la seule comparaison ADR 0053 posée sur ce
    chemin — ne fait qu'un `logger.warning`, jamais une écriture) — c'est le seul
    appelant prévu, et le seul pour lequel ces deux propriétés sont sûres.

    Ré-entrant : un `reuse_connection()` imbriqué dans un autre ne fait rien (la
    connexion déjà empruntée par le bloc englobant sert aux deux)."""
    if _emprunt_partage.get() is not None:
        yield
        return
    emprunt = _EmpruntParesseux()
    jeton = _emprunt_partage.set(emprunt)
    try:
        yield
    finally:
        _emprunt_partage.reset(jeton)
        emprunt.fermer()


@contextmanager
def _connect() -> Iterator[psycopg.Connection]:
    # Avant TOUT (y compris le prêt partagé de `reuse_connection`) : chaque requête
    # s'exécute dans le thread appelant, c'est donc chaque `_connect()` qui peut geler.
    _hors_boucle.verifier()
    emprunt = _emprunt_partage.get()
    if emprunt is not None:
        yield emprunt.obtenir()
        return
    pool = _get_pool()
    with pool.connection() as conn:
        yield conn


@contextmanager
def _connect_autocommit(*, bornee: bool = True) -> Iterator[psycopg.Connection]:
    """Connexion HORS pool, en autocommit — pour le DDL qui l'exige, et pour tenir un
    verrou de session.

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
    sur une table très occupée ne se pose jamais.

    Second usage, lui aussi strictement local : TENIR un verrou consultatif de session
    le temps d'un appel réseau (`billing_reservation`, 10/09/2026). Sur une connexion du
    pool, la transaction ouverte serait coupée par `idle_in_transaction_session_timeout`
    en plein appel, et un verrou de session oublié survivrait au retour de la connexion
    dans le pool ; ici, la fermeture de la connexion le rend."""
    _hors_boucle.verifier()
    options = _ddl_options() if bornee else _connect_options()
    with psycopg.connect(_database_url(), options=options,
                         row_factory=_str_dict_row, autocommit=True) as conn:
        yield conn
