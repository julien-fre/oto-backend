"""L'expression polymorphe qui lit une colonne à couches OU un scalaire (#318).

Une colonne pourra porter `{"valeur": …, "source": …, "origine": …}` au lieu d'un
scalaire. Personne ne réécrira les 43 782 lignes existantes : **la table restera
mixte pour toujours**. Chaque lecteur SQL doit donc rendre la valeur dans les deux
cas, par UNE expression, jamais recopiée.

Ces tests s'exercent contre un VRAI PostgreSQL parce que le sujet EST la sémantique
de l'opérateur : `('"abc"'::jsonb)->>'valeur'` sur un scalaire, sur un objet sans la
clé, sur `null` — trois comportements qu'un stub ne saurait pas imiter, et dont
dépend le `COALESCE` de tout le chantier.
"""
from __future__ import annotations

import psycopg
import pytest

from oto_mcp.db import datastore as dsdb


@pytest.fixture()
def conn(pg_dsn):
    with psycopg.connect(pg_dsn) as c:
        c.execute("DROP TABLE IF EXISTS t_layers")
        c.execute("CREATE TABLE t_layers (id INT, data JSONB)")
        c.commit()
        yield c


def _read(conn, data: str) -> object:
    """Valeur rendue par l'expression polymorphe pour ce blob."""
    expr = dsdb.field_value_sql("f").as_string(None)
    conn.execute("DELETE FROM t_layers")
    conn.execute("INSERT INTO t_layers VALUES (1, %s::jsonb)", (data,))
    # Connexion NUE : pas le row factory dict de `db._conn`, donc un tuple.
    return conn.execute(f"SELECT {expr} AS v FROM t_layers").fetchone()[0]


def test_a_flat_scalar_reads_as_itself(conn):
    """Le cas des 43 782 lignes existantes — il doit rester exact, pour toujours."""
    assert _read(conn, '{"f": "abc"}') == "abc"


def test_a_layered_column_reads_its_value(conn):
    assert _read(conn, '{"f": {"valeur": "abc", "source": "hunter"}}') == "abc"


def test_a_number_survives_both_forms(conn):
    assert _read(conn, '{"f": 42}') == "42"
    assert _read(conn, '{"f": {"valeur": 42, "source": "insee"}}') == "42"


def test_an_absent_column_is_null(conn):
    assert _read(conn, '{"autre": "x"}') is None


def test_a_json_null_is_null(conn):
    """Une valeur `null` JSON ne doit pas devenir la chaîne « null »."""
    assert _read(conn, '{"f": null}') is None


def test_an_object_without_valeur_falls_back_to_its_text(conn):
    """Un champ `json` LÉGITIME qui se trouve être un objet reste opaque : on rend
    son texte, comme avant. C'est ce qui permet au critère de reconnaissance d'être
    le TYPE DÉCLARÉ et non la forme observée — l'expression, elle, ne devine pas."""
    out = _read(conn, '{"f": {"a": 1}}')
    assert out is not None and "a" in out


def test_an_empty_string_is_not_swallowed_by_the_coalesce(conn):
    """Piège du COALESCE : une chaîne vide est une VALEUR, pas une absence. Si le
    premier terme rendait `''` et qu'on le confondait avec NULL, on retomberait sur
    le second et on lirait l'objet entier."""
    assert _read(conn, '{"f": {"valeur": "", "source": "s"}}') == ""


# --- l'identité textuelle index ↔ lookup ---------------------------------------

def test_the_index_and_the_lookup_share_one_expression():
    """Exigence ③ de la revue : pour que le planner serve le lookup d'upsert PAR
    l'index, l'expression du WHERE doit être TEXTUELLEMENT celle de l'index.

    Le mode d'échec d'un écart ne casse rien de visible — la déduplication
    continuerait de marcher, chaque lookup passerait simplement en seq scan. C'est
    la panne silencieuse type : on ne la voit qu'au moment où le namespace est assez
    gros pour que ça coûte. D'où ce test, qui compare les deux chaînes plutôt que
    leurs effets."""
    assert (dsdb.field_value_sql("siren").as_string(None)
            == dsdb.bkey_index_expr("siren").as_string(None))


def test_a_hostile_field_name_stays_inside_a_literal():
    """Le nom de champ vient d'un schéma utilisateur : il est ÉCHAPPÉ, pas nettoyé.

    On ne restreint pas le jeu de caractères d'une clé — ce serait un changement de
    comportement sur des schémas déjà posés, donc un risque réel échangé contre un
    risque hypothétique. Ce qui protège est l'échappement, et il est vérifié ici sur
    la charge qu'un attaquant écrirait."""
    out = dsdb.field_value_sql("x'; DROP TABLE datastore_rows; --").as_string(None)
    assert "''; DROP TABLE" in out, "l'apostrophe doit être DOUBLÉE"
    assert out.count("COALESCE") == 1 and out.endswith(")")
