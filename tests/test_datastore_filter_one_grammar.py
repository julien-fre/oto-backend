"""Une seule grammaire de filtre, quel que soit le verbe.

La fiche de `data_rows` documente l'opérateur imbriqué — `{"posted_at": {"gte":
"2026-06-01"}}` — et c'est la forme qu'un agent apprend. Trois autres verbes
recopiaient la conversion en la SIMPLIFIANT en égalité : la colonne s'y comparait
alors au texte d'un dictionnaire Python, donc zéro ligne, sans erreur. La même
syntaxe répondait juste sur un verbe et faux sur les autres, et « aucune ligne » est
une réponse crédible à une question qu'on croit avoir posée.

D'où la forme de ces tests : ils posent la MÊME question aux quatre verbes et
comparent les réponses entre elles. Aucun n'affirme un nombre attendu de son côté —
c'est la COHÉRENCE qui est la propriété, et un test par verbe l'aurait manquée.
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
def store(pg_dsn, monkeypatch):
    """Le vrai store sur une vraie table — seule la résolution de namespace est
    court-circuitée : le sujet est la traduction du filtre, pas la propriété."""
    monkeypatch.setenv("DATABASE_URL", pg_dsn)
    from oto_mcp.db import _conn
    monkeypatch.setattr(_conn, "_database_url", lambda: pg_dsn)
    from oto_mcp.datastore import DatastorePg
    with psycopg.connect(pg_dsn, autocommit=True) as c:
        c.execute("DROP TABLE IF EXISTS datastore_rows")
        c.execute(_ddl())
        for i, jour in enumerate(["2026-05-01", "2026-06-15", "2026-07-20"]):
            c.execute(
                "INSERT INTO datastore_rows (ns_id, row_id, data) "
                "VALUES (1, %s, %s::jsonb)",
                (f"r{i}", json.dumps({"posted_at": jour, "statut": "ouvert"})))
        s = DatastorePg("u-1")
        monkeypatch.setattr(s, "_resolve", lambda ns, write=False: 1)
        monkeypatch.setattr(s, "_ns_of", lambda ns_id: {"schema": None, "namespace": "t"})
        monkeypatch.setattr(s, "_after_claim", lambda *a, **k: None)
        yield s
        c.execute("DROP TABLE IF EXISTS datastore_rows")


_APRES_JUIN = {"posted_at": {"gte": "2026-06-01"}}


def test_every_verb_answers_the_same_nested_operator(store):
    """LE défaut : `aggregate` rendait 0 là où `count_rows` rendait 2."""
    attendu = store.count_rows("t", filter=_APRES_JUIN)
    assert attendu == 2, "deux lignes sont postérieures au 1er juin"

    agrege = store.aggregate("t", filter=_APRES_JUIN)[0]["count"]
    page = store.page_rows("t", filter=_APRES_JUIN)["total"]
    curseur = len(store.cursor_rows("t", filter=_APRES_JUIN)["rows"])
    assert (agrege, page, curseur) == (attendu, attendu, attendu), (
        f"les verbes divergent sur le même filtre : count={attendu} "
        f"aggregate={agrege} page={page} cursor={curseur}")


def test_the_queue_reads_the_same_grammar(store):
    """`claim_next` filtre la file : y perdre l'opérateur ferait réserver une ligne
    hors périmètre — un worker traiterait ce qu'on avait exclu."""
    ligne = store.claim_next("t", worker="w-1", filter=_APRES_JUIN)
    assert ligne is not None and ligne["posted_at"] >= "2026-06-01"


def test_a_plain_value_is_still_an_equality(store):
    """La forme historique, sur les quatre verbes : rien ne bouge pour elle."""
    plat = {"statut": "ouvert"}
    assert store.count_rows("t", filter=plat) == 3
    assert store.aggregate("t", filter=plat)[0]["count"] == 3
    assert store.page_rows("t", filter=plat)["total"] == 3
    assert len(store.cursor_rows("t", filter=plat)["rows"]) == 3


def test_multi_field_filters_reach_every_verb(store):
    """Ce que le barreau 1 a ajouté au moteur doit être joignable par les verbes qui
    en portent la forme complète — sinon la capacité existe et personne ne l'atteint."""
    spec = [{"fields": ["posted_at", "statut"], "op": "contains", "value": "2026-07"}]
    assert store.count_rows("t", filters=spec) == 1
    assert store.aggregate("t", filters=spec)[0]["count"] == 1
    assert store.page_rows("t", filters=spec)["total"] == 1
    assert len(store.cursor_rows("t", filters=spec)["rows"]) == 1


def test_both_forms_combine(store):
    """`filter` et `filters` se cumulent en ET, sur le même appel."""
    n = store.count_rows("t", filter={"statut": "ouvert"},
                         filters=[{"fields": ["posted_at"], "op": "gte",
                                   "value": "2026-06-01"}])
    assert n == 2
