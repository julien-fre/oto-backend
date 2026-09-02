"""`_UNIQUE_INDEX_SUB_TABLES` décrit un index unique QUI EXISTE, et qui repointe.

Troisième famille de colonnes à sub, née le 2026-09-02 d'un mauvais rangement : une
table dont la contrainte est un index unique PARTIEL avait été mise dans
`_PK_SUB_TABLES`, dont la clé de dédoublonnage est la PK. Le test voisin
(`test_pk_sub_tables_reste_matches_the_real_primary_key`) l'a refusée à juste titre —
mais rien n'aurait attrapé l'erreur SYMÉTRIQUE dans le nouveau bac : une entrée qui
nomme un index inexistant, des colonnes qui ne sont pas les siennes, ou un prédicat
qui ne cadre pas. Le DELETE de l'étape 2 quinquies supprimerait alors des lignes
qu'aucune contrainte ne menace, au milieu de la transaction censée les sauver.

Deux gardes, toutes deux dérivées du DDL — jamais d'une liste tenue à la main :

1. l'index existe, avec EXACTEMENT ces colonnes et ce prédicat ;
2. l'entrée a son `(table, colonne)` dans `_SUB_COLUMNS`. Cette liste-ci ne fait que
   RETIRER la ligne en trop ; sans l'UPDATE nu qui suit, on supprimerait sans jamais
   repointer — une perte silencieuse, et exactement l'inverse du but.
"""
from __future__ import annotations

import re

from oto_mcp.db._schema import _SCHEMA
from oto_mcp.db.users import _SUB_COLUMNS, _UNIQUE_INDEX_SUB_TABLES

# `CREATE UNIQUE INDEX [IF NOT EXISTS] nom ON table(cols) [WHERE pred];`
_INDEX = re.compile(
    r"CREATE\s+UNIQUE\s+INDEX\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)\s+ON\s+(\w+)\s*"
    r"\(([^)]*)\)\s*(?:WHERE\s+([^;]+?))?\s*;", re.I | re.S)


def _index_du_ddl() -> list[tuple]:
    trouves = []
    for m in _INDEX.finditer(_SCHEMA):
        cols = frozenset(c.strip() for c in m.group(3).split(","))
        pred = " ".join((m.group(4) or "").split())
        trouves.append((m.group(2), cols, pred))
    return trouves


def test_il_y_a_bien_quelque_chose_a_garder():
    """Un cliquet sur une liste vide est une décoration."""
    assert _UNIQUE_INDEX_SUB_TABLES, (
        "la famille est vide : soit la retirer avec son étape 2 quinquies, soit "
        "elle a été vidée par erreur.")
    assert _index_du_ddl(), "le parse du DDL ne trouve plus aucun index unique"


def test_chaque_entree_decrit_un_index_unique_QUI_EXISTE():
    index = _index_du_ddl()
    problemes = []
    for table, col, autres, predicat in _UNIQUE_INDEX_SUB_TABLES:
        attendu_cols = frozenset({col, *autres})
        # Le prédicat est un gabarit (`{a}.kind = 'send'`) : côté DDL il n'y a pas
        # d'alias, donc on compare la forme sans préfixe.
        attendu_pred = " ".join(predicat.format(a="").replace(".", "", 1).split()) \
            if predicat else ""
        candidats = [i for i in index if i[0] == table and i[1] == attendu_cols]
        if not candidats:
            memes_tables = sorted({tuple(sorted(i[1])) for i in index if i[0] == table})
            problemes.append(
                f"{table}({sorted(attendu_cols)}) : aucun index unique de ces colonnes "
                f"dans le DDL. Index uniques déclarés sur cette table : {memes_tables}")
            continue
        if attendu_pred and not any(c[2] == attendu_pred for c in candidats):
            problemes.append(
                f"{table}({sorted(attendu_cols)}) : prédicat {attendu_pred!r} ≠ "
                f"celui du DDL {[c[2] for c in candidats]!r}")
    assert not problemes, (
        "entrées `_UNIQUE_INDEX_SUB_TABLES` qui ne décrivent pas un index réel :\n  "
        + "\n  ".join(problemes)
        + "\nLe DELETE de l'étape 2 quinquies supprimerait alors des lignes qu'aucune "
          "contrainte ne menace — au milieu de la transaction censée les sauver.")


def test_chaque_entree_a_bien_son_REPOINTAGE():
    """Cette famille RETIRE ; elle ne repointe pas. Sans l'entrée jumelle dans
    `_SUB_COLUMNS`, le merge supprimerait la ligne en trop puis laisserait l'autre
    sur un identifiant mort — que le `DELETE FROM users` de l'étape 4 emporterait
    en CASCADE. On aurait fait la moitié dangereuse du geste."""
    repointees = {(t, c) for t, c in _SUB_COLUMNS}
    manquantes = [(t, c) for t, c, _a, _p in _UNIQUE_INDEX_SUB_TABLES
                  if (t, c) not in repointees]
    assert not manquantes, (
        f"retirées mais jamais repointées : {manquantes}. Ajoute `(table, colonne)` "
        "à `_SUB_COLUMNS` — c'est lui qui fait l'UPDATE.")


def test_aucune_entree_n_est_AUSSI_dans_le_bac_de_la_cle_primaire():
    """Les deux gestes se cumuleraient : deux DELETE sur des clés différentes."""
    from oto_mcp.db.users import _PK_SUB_TABLES
    doubles = {(t, c) for t, c, _a, _p in _UNIQUE_INDEX_SUB_TABLES} \
        & {(t, c) for t, c, _r in _PK_SUB_TABLES}
    assert not doubles, f"rangée dans les deux bacs à la fois : {sorted(doubles)}"
