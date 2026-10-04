"""Interroger une colonne-tableau : « il existe un contact dont… » (oto#22 §5.1).

La notation `[]` porte l'EXISTENCE intrinsèquement (§12) : `contacts[].fonction`
signifie toujours « il existe un contact dont la fonction… », et `match` ne descend
jamais dans les items — il joint les cibles déclarées, comme au premier niveau. Un
même mot qui changerait de sens selon la forme de sa cible serait de l'interprétation,
sur un paramètre au lieu d'un nom.

Un rang nommé (`contacts[0].email`) vise une fiche précise, là où `[]` balaie toute
la liste : deux questions distinctes, deux notations.

Contre un VRAI PostgreSQL : le sujet est ce que la requête REND. `jsonb_array_elements`
LÈVE sur une valeur qui n'est pas un tableau — et pendant une conversion, une partie
des lignes ne l'est pas encore. C'est l'état normal, pas un cas limite, et aucune
assertion sur du texte SQL ne l'aurait montré.
"""
from __future__ import annotations

import json

import psycopg
import pytest


def _ddl() -> str:
    from oto_mcp.db import _schema
    src = _schema._SCHEMA
    i = src.index("CREATE TABLE IF NOT EXISTS datastore_rows")
    j = src.index("\n);", i) + 3
    return src[i:j].replace("REFERENCES user_datastores(id) ON DELETE CASCADE", "")


@pytest.fixture()
def pg(pg_module_dsn, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", pg_module_dsn)
    from oto_mcp.db import _conn
    monkeypatch.setattr(_conn, "_database_url", lambda: pg_module_dsn)
    with psycopg.connect(pg_module_dsn, autocommit=True) as c:
        c.execute("DROP TABLE IF EXISTS datastore_rows")
        c.execute(_ddl())
        for rid, data in [
            ("avec_rh", {"contacts": [
                {"fonction": "Dirigeant"},
                {"fonction": "DRH", "email": {"valeur": "rh@x.fr",
                                              "origine": "socle"}}]}),
            ("sans_rh", {"contacts": [{"fonction": "Commercial"},
                                      {"fonction": "Dirigeant"}]}),
            ("vide", {"contacts": []}),
            ("absent", {"siren": "1"}),
            # Pendant une conversion, une partie des lignes n'est PAS encore une liste.
            ("pas_converti", {"contacts": "Dupont, Martin"}),
        ]:
            c.execute("INSERT INTO datastore_rows (ns_id,row_id,data) "
                      "VALUES (1,%s,%s::jsonb)", (rid, json.dumps(data)))
        yield c
        c.execute("DROP TABLE IF EXISTS datastore_rows")


def _ids(filters: list) -> list:
    from oto_mcp import db
    return sorted(r["row_id"] for r in db.datastore_list_rows(1, filters=filters))


# --- existence à travers les items -------------------------------------------------

def test_any_item_matching_is_enough(pg):
    """LA question : « les fiches dont UN contact est RH », quel que soit son rang."""
    assert _ids([{"field": "contacts[].fonction", "op": "eq", "value": "DRH"}]) == [
        "avec_rh"]


def test_a_row_whose_column_is_not_a_list_does_not_explode(pg):
    """`jsonb_array_elements` lève sur une non-liste. Sans garde de type, une seule
    ligne non convertie ferait échouer la requête ENTIÈRE — et pendant la fenêtre de
    migration, ces lignes-là sont la majorité."""
    assert _ids([{"field": "contacts[].fonction", "op": "not_empty"}]) == [
        "avec_rh", "sans_rh"]


def test_an_empty_or_absent_list_matches_nothing(pg):
    assert "vide" not in _ids([{"field": "contacts[].fonction", "op": "not_empty"}])
    assert "absent" not in _ids([{"field": "contacts[].fonction", "op": "not_empty"}])


def test_the_operators_work_through_the_items(pg):
    assert _ids([{"field": "contacts[].fonction", "op": "in",
                  "value": ["DRH", "DAF"]}]) == ["avec_rh"]
    assert _ids([{"field": "contacts[].fonction", "op": "contains",
                  "value": "commerc"}]) == ["sans_rh"]


def test_a_layer_of_an_item_is_reachable(pg):
    """La provenance d'un attribut d'item se filtre comme une valeur — sinon la
    question « quels contacts n'ont pas de source ? » reste hors de portée là où la
    donnée est la plus dense."""
    assert _ids([{"field": "contacts[].email.origine", "op": "eq",
                  "value": "socle"}]) == ["avec_rh"]


def test_several_declared_targets_still_join_at_the_target_level(pg):
    """`match` joint les CIBLES, jamais les items (§12) : ici « une des deux colonnes
    porte un DRH », l'existence restant intrinsèque à chaque `[]`."""
    assert _ids([{"fields": ["contacts[].fonction", "dirigeant_fonction"],
                  "op": "eq", "value": "DRH", "match": "any"}]) == ["avec_rh"]


# --- un rang précis ----------------------------------------------------------------

def test_a_named_rank_targets_one_item(pg):
    """Un rang nommé vise le rang, et lui seul — sans jamais balayer la liste."""
    assert _ids([{"field": "contacts[1].fonction", "op": "eq",
                  "value": "DRH"}]) == ["avec_rh"]
    assert _ids([{"field": "contacts[0].fonction", "op": "eq", "value": "DRH"}]) == []


def test_a_rank_beyond_the_list_matches_nothing(pg):
    assert _ids([{"field": "contacts[9].fonction", "op": "not_empty"}]) == []


def test_a_layer_at_a_named_rank(pg):
    assert _ids([{"field": "contacts[1].email.origine", "op": "eq",
                  "value": "socle"}]) == ["avec_rh"]


# --- ce qui ne bouge pas ------------------------------------------------------------

def test_ordinary_columns_are_unchanged(pg):
    """Un nom sans crochets reste une colonne — tout l'existant passe par là."""
    assert _ids([{"field": "siren", "op": "eq", "value": "1"}]) == ["absent"]


def test_a_column_name_containing_brackets_is_not_a_path(pg):
    """La forme est reconnue par sa SYNTAXE complète (`col[n].reste`, ou l'élément nu
    `col[n]` d'une liste de valeurs, oto#102), pas par la présence d'un crochet : une
    colonne bizarrement nommée reste un nom."""
    from oto_mcp.db import datastore as dsdb
    assert dsdb.split_list_path("contacts[]") == ("contacts", None, None)
    assert dsdb.split_list_path("Note [1]") is None
    assert dsdb.split_list_path("note[a]") is None
    assert dsdb.split_list_path("contacts.nom") is None
    assert dsdb.split_list_path("[0].nom") is None


# --- les TROIS verbes, et le cas d'acceptation du consommateur ---------------------

def _store(monkeypatch, schema):
    from oto_mcp.datastore.core import DatastorePg
    s = DatastorePg("u-1")
    monkeypatch.setattr(s, "_resolve", lambda ns, write=False: 1)
    monkeypatch.setattr(s, "_schema_of", lambda ns_id: schema)
    monkeypatch.setattr(s, "_ns_of", lambda ns_id: {"schema": schema, "datastore": "t"})
    return s


_LISTE = {"fields": [
    {"key": "contacts", "type": "list",
     "of": {"type": "object", "fields": [{"key": "fonction", "type": "text"}]}}]}


def test_sorting_across_all_items_is_refused_by_name(pg, monkeypatch):
    """Même famille que l'égalité sur la colonne entière : N valeurs ne se trient pas.
    Rendre le premier item donnerait un ordre reproductible et faux."""
    s = _store(monkeypatch, _LISTE)
    with pytest.raises(ValueError) as e:
        s.page_rows("t", order_by="contacts[].fonction")
    assert "contacts[0].fonction" in str(e.value)
