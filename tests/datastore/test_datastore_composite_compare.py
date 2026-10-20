"""Une colonne composite ENTIÈRE ne se compare pas (oto#22, point b du 30/09).

Le SQL lit une case en texte : sur une colonne `list` ou `object`, c'est le texte du
JSON. `eq` ne matchait donc jamais, `ne` matchait tout, `gt` rangeait des blobs par
l'alphabet — des réponses rendues comme des résultats. La spec l'écrivait
(`datastore-colonne-tableau.md` §4) ; rien ne l'appliquait. Le refus consulte le type
DÉCLARÉ et oriente vers `contacts[].<attribut>`.

Contre un VRAI PostgreSQL pour ce qui reste permis : ce qu'on vérifie là, c'est ce que
la requête rend, pas le texte d'un fragment.
"""
from __future__ import annotations

import json

import psycopg
import pytest


_SCHEMA = {"fields": [
    {"key": "nom", "type": "text"},
    {"key": "contacts", "type": "list",
     "of": {"type": "object", "fields": [{"key": "fonction", "type": "text"},
                                         {"key": "email", "type": "email"}]}},
    {"key": "adresse", "type": "object",
     "fields": [{"key": "ville", "type": "text"}]},
    {"key": "tags", "type": "list", "of": {"type": "text"}},
]}


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
            ("avec", {"nom": "A", "contacts": [{"fonction": "DRH"}],
                      "adresse": {"ville": "Lyon"}, "tags": ["x"]}),
            ("sans", {"nom": "B"}),
        ]:
            c.execute("INSERT INTO datastore_rows (ns_id,row_id,data) "
                      "VALUES (1,%s,%s::jsonb)", (rid, json.dumps(data)))
        yield c
        c.execute("DROP TABLE IF EXISTS datastore_rows")


def _store(monkeypatch, schema=_SCHEMA):
    from oto_mcp.datastore.core import DatastorePg
    s = DatastorePg("u-1")
    monkeypatch.setattr(s, "_resolve", lambda ns, write=False: 1)
    monkeypatch.setattr(s, "_schema_of", lambda ns_id: schema)
    return s


def _ids(page) -> list:
    rows = page["rows"] if isinstance(page, dict) else page
    return sorted(r["_id"] for r in rows)


_VERBES = {
    "page_rows": lambda s, **kw: s.page_rows("t", **kw),
    "cursor_rows": lambda s, **kw: s.cursor_rows("t", **kw),
    "count_rows": lambda s, **kw: s.count_rows("t", **kw),
    "aggregate": lambda s, **kw: s.aggregate("t", **kw),
}


# --- le refus ------------------------------------------------------------------------

@pytest.mark.parametrize("verbe", sorted(_VERBES))
@pytest.mark.parametrize("op", ["eq", "ne", "in", "gt", "gte", "lt", "lte"])
def test_comparer_une_colonne_liste_entiere_est_refuse_sur_tout_verbe(
        pg, monkeypatch, verbe, op):
    s = _store(monkeypatch)
    with pytest.raises(ValueError) as e:
        _VERBES[verbe](s, filters=[{"field": "contacts", "op": op, "value": "DRH"}])
    msg = str(e.value)
    assert f"filter `{op}` on `contacts` refused" in msg
    assert "contacts[].fonction" in msg          # la destination qui existe
    assert "`empty`" in msg and "`not_empty`" in msg


def test_le_raccourci_filter_est_garde_aussi(pg, monkeypatch):
    """`filter={"contacts": "DRH"}` est un `eq` : il passe par la même garde."""
    with pytest.raises(ValueError, match="`list` column"):
        _store(monkeypatch).count_rows("t", filter={"contacts": "DRH"})


def test_une_cible_parmi_plusieurs_suffit_a_refuser(pg, monkeypatch):
    with pytest.raises(ValueError, match="`contacts`"):
        _store(monkeypatch).count_rows("t", filters=[
            {"fields": ["nom", "contacts"], "op": "eq", "value": "DRH"}])


def test_la_condition_d_une_metrique_est_gardee(pg, monkeypatch):
    with pytest.raises(ValueError, match="`contacts`"):
        _store(monkeypatch).aggregate("t", metrics=[
            {"op": "count", "where": [{"field": "contacts", "op": "eq",
                                       "value": "DRH"}]}])


def test_une_colonne_objet_entiere_est_refusee(pg, monkeypatch):
    with pytest.raises(ValueError) as e:
        _store(monkeypatch).count_rows("t", filters=[
            {"field": "adresse", "op": "eq", "value": "Lyon"}])
    assert "`object` column" in str(e.value)


def test_une_liste_de_scalaires_est_refusee_sans_promettre_un_chemin(pg, monkeypatch):
    with pytest.raises(ValueError) as e:
        _store(monkeypatch).count_rows("t", filters=[
            {"field": "tags", "op": "eq", "value": "x"}])
    assert "declare no attributes" in str(e.value)


def test_la_file_de_travail_est_gardee(monkeypatch):
    from oto_mcp.datastore.file_de_travail import perimetre_de_reservation
    with pytest.raises(ValueError, match="`contacts`"):
        perimetre_de_reservation(_SCHEMA, 1, filter={"contacts": "DRH"})


# --- ce qui reste permis, EXÉCUTÉ -----------------------------------------------------

def test_vide_et_rempli_restent_permis_sur_la_colonne_entiere(pg, monkeypatch):
    s = _store(monkeypatch)
    assert _ids(s.page_rows("t", filters=[{"field": "contacts",
                                           "op": "not_empty"}])) == ["avec"]
    assert _ids(s.page_rows("t", filters=[{"field": "contacts",
                                           "op": "empty"}])) == ["sans"]
    assert _ids(s.page_rows("t", filters=[{"field": "adresse",
                                           "op": "not_empty"}])) == ["avec"]


def test_contains_reste_une_recherche_de_texte(pg, monkeypatch):
    """Le filtre par défaut qu'un écran propose sur une colonne : on ne le casse pas."""
    s = _store(monkeypatch)
    assert _ids(s.page_rows("t", filters=[{"field": "contacts", "op": "contains",
                                           "value": "DRH"}])) == ["avec"]


def test_l_attribut_d_un_element_se_compare(pg, monkeypatch):
    s = _store(monkeypatch)
    assert _ids(s.page_rows("t", filters=[{"field": "contacts[].fonction",
                                           "op": "eq", "value": "DRH"}])) == ["avec"]


def test_une_colonne_scalaire_ou_non_declaree_n_est_pas_touchee(pg, monkeypatch):
    s = _store(monkeypatch)
    assert _ids(s.page_rows("t", filters=[{"field": "nom", "op": "eq",
                                           "value": "B"}])) == ["sans"]
    # Sans schéma, rien n'est déclaré composite : la garde ne devine pas.
    s = _store(monkeypatch, schema=None)
    assert s.count_rows("t", filters=[{"field": "contacts", "op": "ne",
                                       "value": "x"}]) == 2


def test_sans_comparaison_le_schema_n_est_pas_lu(pg, monkeypatch):
    def interdit(ns_id):
        raise AssertionError("schéma lu sans filtre de comparaison")
    s = _store(monkeypatch)
    monkeypatch.setattr(s, "_schema_of", interdit)
    assert s.count_rows("t") == 2
    assert s.count_rows("t", filters=[{"field": "contacts", "op": "empty"}]) == 1
