"""Un index posé CONCURRENTLY sur une table servie : quand le construire soi-même, quand
le laisser à la main.

Né pour `idx_tool_calls_org_tool_ok` (oto-backend#1145, révision 0032), généralisé pour
les index de la recherche dans les valeurs servies (#307, révision 0041) : chaque index
de ce régime naît par deux chemins — sa révision Alembic (CONCURRENTLY, hors
transaction) et le démarrage d'une base neuve (`_init.py`, non concurrent, dans sa
transaction) — et les deux appliquent le MÊME verdict, écrit ici une fois :

- l'index existe et il est valide → rien à faire ;
- il existe et il est INVALIDE (construction CONCURRENTLY interrompue) → `IndexInvalide` :
  `IF NOT EXISTS` le prendrait pour fait et il ne servirait jamais ;
- il est absent et sa table est PETITE (base neuve, vide, de test) → construire ;
- il est absent et sa table est GROSSE → `ConstructionManuelleRequise`. Mesuré en
  production le 04/10/2026 : 172 s pour environ 12 M lignes de `tool_calls`, au-delà des
  120 s de la fenêtre de démarrage — et, au démarrage, non concurrent, la construction
  bloquerait les écritures de la table pendant tout ce temps.

Les deux refus renvoient à la procédure manuelle (`docs/migrations-versionnees.md`
§5.1). La taille se lit dans `pg_class.reltuples` (l'estimation de l'ANALYZE, sans
parcours) ; une table jamais analysée (`reltuples = -1`) est comptée, au plus jusqu'au
seuil plus une ligne.

Le verdict prend `scalaire(sql) -> valeur | None` : la révision passe par SQLAlchemy,
le démarrage par psycopg, le banc par un faux — une seule logique pour les trois.

**Verrous de la révision.** `ShareUpdateExclusiveLock` sur la table seulement : ni
lectures ni écritures bloquées. La phase concurrente ATTEND la fin de toute transaction
ouverte avant elle, par des attentes de verrou sur leur `virtualxid`, et `lock_timeout`
coupe CES attentes aussi : à 2 s, une construction a échoué en production
(`LockNotAvailable`, index laissé invalide). Il vaut donc 5 min.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

#: Au-delà, la construction sort de la fenêtre de démarrage ou de migration : elle se
#: fait à la main. 100 000 lignes de `tool_calls` se construisent en une fraction de
#: seconde ; un index d'expression coûteux déclare un seuil plus bas.
CONSTRUCTION_MAX_LIGNES = 100_000

#: Hors transaction, un `SET` vaut pour la SESSION : il est remis à zéro en sortie.
ATTENTE_MAX = "SET lock_timeout = '5min'"
ATTENTE_RENDUE = "RESET lock_timeout"


@dataclass(frozen=True)
class IndexConcurrent:
    """Un index de ce régime : son nom, sa table, sa forme et la révision qui le pose."""

    nom: str
    table: str
    #: Tout ce qui suit `ON <table>` : colonnes ou `USING GIN (…)`, prédicat partiel.
    forme: str
    #: L'identifiant de la révision qui le pose — cité par la procédure manuelle.
    revision: str
    max_lignes: int = CONSTRUCTION_MAX_LIGNES

    @property
    def ddl_concurrent(self) -> str:
        """La construction de la révision : concurrente, hors transaction."""
        return f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {self.nom} ON {self.table} {self.forme}"

    @property
    def ddl_demarrage(self) -> str:
        """La construction du démarrage d'une base neuve : dans sa transaction."""
        return f"CREATE INDEX IF NOT EXISTS {self.nom} ON {self.table} {self.forme}"

    @property
    def sql_validite(self) -> str:
        """`None` si l'index est absent, sinon sa validité."""
        return ("SELECT i.indisvalid FROM pg_index i "
                f"WHERE i.indexrelid = to_regclass('{self.nom}')")

    def sql_table_trop_grosse(self, max_lignes: int) -> str:
        """`true` si la table compte plus de `max_lignes` lignes (estimation)."""
        n = int(max_lignes)
        return (
            "SELECT CASE WHEN c.reltuples >= 0 THEN c.reltuples > " f"{n} "
            f"ELSE (SELECT count(*) FROM (SELECT 1 FROM {self.table} "
            f"LIMIT {n + 1}) t) > {n} END "
            f"FROM pg_class c WHERE c.oid = '{self.table}'::regclass"
        )

    @property
    def procedure(self) -> str:
        return (
            "À la main, hors fenêtre de démarrage ou de migration "
            "(docs/migrations-versionnees.md §5.1) : SET statement_timeout = 0; "
            "SET lock_timeout = '5min'; "
            f"DROP INDEX CONCURRENTLY IF EXISTS {self.nom}; {self.ddl_concurrent}; "
            f"puis vérifier `indisvalid` et rejouer la révision {self.revision}."
        )


class IndexInvalide(RuntimeError):
    """L'index existe mais n'est pas valide : il ne sert aucune lecture."""

    def __init__(self, index: IndexConcurrent) -> None:
        self.index = index
        super().__init__(
            f"{index.nom} existe mais est INVALIDE (construction CONCURRENTLY "
            "interrompue). " + index.procedure)


class ConstructionManuelleRequise(RuntimeError):
    """L'index est absent d'une table trop grosse pour le construire ici."""

    def __init__(self, index: IndexConcurrent, max_lignes: int) -> None:
        self.index = index
        super().__init__(
            f"{index.nom} est absent et {index.table} dépasse {max_lignes} lignes : sa "
            "construction sortirait de la fenêtre de démarrage ou de migration. "
            + index.procedure)


def scalaire_de(conn) -> Callable[[str], Optional[Any]]:
    """Le `scalaire` d'une connexion psycopg à lignes en dict (`_connect`) : la première
    colonne de la première ligne, `None` sans ligne."""
    def scalaire(sql: str) -> Optional[Any]:
        ligne = conn.execute(sql).fetchone()
        return None if ligne is None else next(iter(ligne.values()))
    return scalaire


def a_construire(index: IndexConcurrent, scalaire: Callable[[str], Optional[Any]], *,
                 max_lignes: Optional[int] = None) -> bool:
    """`True` s'il faut construire ICI, `False` si l'index est déjà là et valide.

    Lève `IndexInvalide` ou `ConstructionManuelleRequise` — jamais une construction
    longue en douce, jamais un index invalide pris pour fait."""
    seuil = index.max_lignes if max_lignes is None else max_lignes
    valide = scalaire(index.sql_validite)
    if valide is True:
        return False
    if valide is False:
        raise IndexInvalide(index)
    if scalaire(index.sql_table_trop_grosse(seuil)):
        raise ConstructionManuelleRequise(index, seuil)
    return True


def poser_au_demarrage(conn, index: IndexConcurrent) -> None:
    """La forme d'une base NEUVE, dans la transaction du démarrage. Une base servie
    le reçoit à la main ou de sa révision, CONCURRENTLY. Index invalide ou table trop
    grosse : le démarrage continue et le DIT, le geste est manuel (§5.1)."""
    try:
        if a_construire(index, scalaire_de(conn)):
            conn.execute(index.ddl_demarrage)
    except (IndexInvalide, ConstructionManuelleRequise) as e:
        logger.error("démarrage : %s", e)


def poser_par_revision(op, index: IndexConcurrent) -> None:
    """Le corps d'`upgrade()` d'une révision de ce régime, sous `autocommit_block` :
    construire si le verdict le permet, puis VÉRIFIER la validité — un index laissé
    invalide lève au lieu d'être estampillé fait."""
    with op.get_context().autocommit_block():
        bind = op.get_bind()

        def scalaire(sql: str):
            return bind.exec_driver_sql(sql).scalar()

        if not a_construire(index, scalaire):
            return
        op.execute(ATTENTE_MAX)
        try:
            op.execute(index.ddl_concurrent)
        finally:
            op.execute(ATTENTE_RENDUE)
        if scalaire(index.sql_validite) is not True:
            raise IndexInvalide(index)


def retirer_par_revision(op, index: IndexConcurrent) -> None:
    """Le retour arrière : `DROP INDEX CONCURRENTLY`, même attente bornée."""
    with op.get_context().autocommit_block():
        op.execute(ATTENTE_MAX)
        try:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {index.nom}")
        finally:
            op.execute(ATTENTE_RENDUE)
