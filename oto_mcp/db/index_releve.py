"""`idx_tool_calls_org_tool_ok` : quand le construire soi-même, quand le laisser à la main.

oto-backend#1145. L'index des relevés d'org par outil (`tool_calls (org_id, tool,
created_at DESC) WHERE ok`) naît par deux chemins : la révision Alembic 0032
(CONCURRENTLY) et le démarrage d'une base neuve (`_init.py`, non concurrent). Les deux
appliquent le MÊME verdict, écrit ici une fois :

- l'index existe et il est valide → rien à faire ;
- il existe et il est INVALIDE (construction CONCURRENTLY interrompue) → `IndexInvalide` :
  `IF NOT EXISTS` le prendrait pour fait et il ne servirait jamais ;
- il est absent et `tool_calls` est PETITE (base neuve, vide, de test) → construire ;
- il est absent et `tool_calls` est GROSSE → `ConstructionManuelleRequise`. Mesuré en
  production le 04/10/2026 : 172 s pour environ 12 M lignes, au-delà des 120 s de la
  fenêtre de démarrage — et, au démarrage, non concurrent, la construction bloquerait
  les écritures du journal pendant tout ce temps.

Les deux refus renvoient à la procédure manuelle (`docs/migrations-versionnees.md`
§5.1). La taille se lit dans `pg_class.reltuples` (l'estimation de l'ANALYZE, sans
parcours) ; une table jamais analysée (`reltuples = -1`) est comptée, au plus jusqu'au
seuil plus une ligne.

Le verdict prend `scalaire(sql) -> valeur | None` : la révision passe par SQLAlchemy,
le démarrage par psycopg, le banc par un faux — une seule logique pour les trois.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

INDEX = "idx_tool_calls_org_tool_ok"
_FORME = "ON tool_calls (org_id, tool, created_at DESC) WHERE ok"
#: La construction de la révision : concurrente, hors transaction.
DDL_CONCURRENT = f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {INDEX} {_FORME}"
#: La construction du démarrage d'une base neuve : dans sa transaction.
DDL_DEMARRAGE = f"CREATE INDEX IF NOT EXISTS {INDEX} {_FORME}"

#: Au-delà, la construction sort de la fenêtre de démarrage ou de migration : elle se
#: fait à la main. 100 000 lignes se construisent en une fraction de seconde.
CONSTRUCTION_MAX_LIGNES = 100_000

#: `None` si l'index est absent, sinon sa validité.
SQL_VALIDITE = (
    "SELECT i.indisvalid FROM pg_index i "
    f"WHERE i.indexrelid = to_regclass('{INDEX}')"
)


def sql_table_trop_grosse(max_lignes: int) -> str:
    """`true` si `tool_calls` compte plus de `max_lignes` lignes (estimation)."""
    n = int(max_lignes)
    return (
        "SELECT CASE WHEN c.reltuples >= 0 THEN c.reltuples > " f"{n} "
        "ELSE (SELECT count(*) FROM (SELECT 1 FROM tool_calls "
        f"LIMIT {n + 1}) t) > {n} END "
        "FROM pg_class c WHERE c.oid = 'tool_calls'::regclass"
    )


_PROCEDURE = (
    "À la main, hors fenêtre de démarrage ou de migration "
    "(docs/migrations-versionnees.md §5.1) : SET statement_timeout = 0; "
    "SET lock_timeout = '5min'; "
    f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX}; {DDL_CONCURRENT}; "
    "puis vérifier `indisvalid` et rejouer la révision 0032."
)


class IndexInvalide(RuntimeError):
    """L'index existe mais n'est pas valide : il ne sert aucune lecture."""

    def __init__(self) -> None:
        super().__init__(
            f"{INDEX} existe mais est INVALIDE (construction CONCURRENTLY interrompue). "
            + _PROCEDURE)


class ConstructionManuelleRequise(RuntimeError):
    """L'index est absent d'une `tool_calls` trop grosse pour le construire ici."""

    def __init__(self, max_lignes: int) -> None:
        super().__init__(
            f"{INDEX} est absent et tool_calls dépasse {max_lignes} lignes : sa "
            "construction sortirait de la fenêtre de démarrage ou de migration. "
            + _PROCEDURE)


def scalaire_de(conn) -> Callable[[str], Optional[Any]]:
    """Le `scalaire` d'une connexion psycopg à lignes en dict (`_connect`) : la première
    colonne de la première ligne, `None` sans ligne."""
    def scalaire(sql: str) -> Optional[Any]:
        ligne = conn.execute(sql).fetchone()
        return None if ligne is None else next(iter(ligne.values()))
    return scalaire


def a_construire(scalaire: Callable[[str], Optional[Any]], *,
                 max_lignes: int = CONSTRUCTION_MAX_LIGNES) -> bool:
    """`True` s'il faut construire ICI, `False` si l'index est déjà là et valide.

    Lève `IndexInvalide` ou `ConstructionManuelleRequise` — jamais une construction
    longue en douce, jamais un index invalide pris pour fait."""
    valide = scalaire(SQL_VALIDITE)
    if valide is True:
        return False
    if valide is False:
        raise IndexInvalide()
    if scalaire(sql_table_trop_grosse(max_lignes)):
        raise ConstructionManuelleRequise(max_lignes)
    return True
