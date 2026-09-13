"""La valeur d'une case se lit pareil en Python et en SQL (oto#163).

`unwrap` rend `None` pour une case faite de couches seules, sans `valeur`. Le SQL
retombait sur le texte de l'enveloppe : `{"origine": ""}` était compté rempli, comparé,
trié et regroupé comme une valeur. Ce banc confronte les deux lecteurs sur la MÊME case,
contre un vrai PostgreSQL, pour chaque forme SQL que rend `db/paths.py` : le littéral
(contrôles de schéma), la forme paramétrée (filtres, tri, agrégats), la feuille d'un
item de rang précis, et la feuille d'un item quelconque (filtre d'existence).

Un témoin par entrée de `LAYER_KEYS`, généré : une couche ajoutée au vocabulaire sans
entrer dans la règle SQL fait rougir ce banc. Et deux cases que l'opérateur `?` prend
pour des enveloppes (la chaîne "valeur", la liste ["valeur"]) gardent la garde de type.
"""
from __future__ import annotations

import json

import psycopg
import pytest
from psycopg import sql

from oto_mcp.datastore.schema import LAYER_KEYS, unwrap
from oto_mcp.db.paths import field_read_sql, field_value_sql, leaf_read_sql

TEMOINS = [
    "abc", 42, 3.5, True, None, "",
    "valeur", ["valeur"],                              # `?` répond vrai sur eux
    [1, 2],
    {"a": 1},                                          # objet json métier
    {"a": 1, "origine": "x"},                          # métier, avec une clé homonyme
    {"comment": "x", "unite": "kg"},
    {"valeur": "abc", "comment": "x"},
    {"valeur": "", "origine": "x"},                    # "" reste une valeur
    {"valeur": None, "origine": "x"},                  # null reste NULL, pas l'enveloppe
    {"valeur": 42},
    {"valeur": {"a": 1}},
    {"origine": {"valeur": "X", "comment": "c"}},      # version d'origine (oto#140)
    {"origine": "", "comment": "c", "link": "L"},
    {"comment": "à vérifier"},
    {"valeur": "", "oto.vide_assume": True},           # vide assumé (oto#204)
    # Le marqueur interne SANS valeur : `unwrap` ne l'ignore pas (il n'est pas dans
    # `LAYER_KEYS`), donc la case reste un objet — le SQL doit rendre la même chose,
    # et rougir le jour où #204 change ce que `unwrap` ignore.
    {"oto.vide_assume": True, "origine": ""},
    {"oto.vide_assume": True},
    # `{}` (#165) : hors de ce lot. Les deux lecteurs le rendent aujourd'hui non nul ;
    # s'il change d'un côté seulement, ce témoin le dit.
    {},
] + [{couche: ""} for couche in LAYER_KEYS] + [{couche: "x"} for couche in LAYER_KEYS]


def _nom(v) -> str:
    return json.dumps(v, ensure_ascii=False)


@pytest.fixture(scope="module")
def conn(pg_module_dsn):
    with psycopg.connect(pg_module_dsn, autocommit=True) as c:
        c.execute("CREATE TABLE t_valeur (id INT, data JSONB)")
        yield c


def _poser(conn, data: dict) -> None:
    conn.execute("TRUNCATE t_valeur")
    conn.execute("INSERT INTO t_valeur VALUES (1, %s::jsonb)", (json.dumps(data),))


def _lire_litteral(conn):
    q = sql.SQL("SELECT {e} FROM t_valeur").format(e=field_value_sql("f"))
    return conn.execute(q).fetchone()[0]


def _lire_parametre(conn):
    expr, params = field_read_sql("f")
    return conn.execute(f"SELECT {expr} FROM t_valeur", params).fetchone()[0]


def _lire_item_de_rang(conn):
    expr, params = field_read_sql("l[0].f")
    return conn.execute(f"SELECT {expr} FROM t_valeur", params).fetchone()[0]


def _lire_item_quelconque(conn):
    expr, params = leaf_read_sql("_i.v", [], "f")
    return conn.execute(
        f"SELECT (SELECT {expr} FROM jsonb_array_elements(data->'l') AS _i(v)) "
        "FROM t_valeur", params).fetchone()[0]


LECTEURS = {
    "littéral": _lire_litteral,
    "paramétré": _lire_parametre,
    "item de rang": _lire_item_de_rang,
    "item quelconque": _lire_item_quelconque,
}


def _conforme(lu, attendu) -> bool:
    """Le texte que rend PostgreSQL correspond-il à la valeur qu'`unwrap` a déballée ?"""
    if attendu is None:
        return lu is None
    if lu is None:
        return False
    if isinstance(attendu, bool):
        return lu == ("true" if attendu else "false")
    if isinstance(attendu, str):
        return lu == attendu
    if isinstance(attendu, (int, float)):
        return lu == json.dumps(attendu)
    return json.loads(lu) == attendu


@pytest.mark.parametrize("lecteur", sorted(LECTEURS))
@pytest.mark.parametrize("case", TEMOINS, ids=_nom)
def test_le_sql_lit_la_valeur_qu_unwrap_lit(conn, case, lecteur):
    _poser(conn, {"f": case, "l": [{"f": case}]})
    lu = LECTEURS[lecteur](conn)
    attendu = unwrap(case)
    assert _conforme(lu, attendu), (
        f"{lecteur} : SQL rend {lu!r}, unwrap rend {attendu!r} pour {_nom(case)}")


@pytest.mark.parametrize("lecteur", sorted(LECTEURS))
def test_une_colonne_absente_est_nulle(conn, lecteur):
    _poser(conn, {"autre": "x", "l": [{"autre": "x"}]})
    assert LECTEURS[lecteur](conn) is None


@pytest.mark.parametrize("couche", LAYER_KEYS)
def test_chaque_couche_du_vocabulaire_est_dans_la_regle(couche):
    """Le témoin comportemental ci-dessus rougit si une couche manque ; celui-ci nomme
    laquelle, sans base : le tableau SQL vient de `LAYER_KEYS`."""
    expr, _ = field_read_sql("f")
    assert f"'{couche}'" in expr.split("ARRAY[", 1)[1].split("]", 1)[0]
