"""Un bail expiré n'est pas une réservation — la lecture doit le savoir comme la garde.

**Le fait, mesuré le 01/09/2026 sur un fichier de production.** 495 lignes sur 8 910
portaient `_claimed_by` ; **les 495 étaient expirées**, la plus ancienne depuis dix-huit
jours, au nom de travailleurs d'une campagne close.

⚠️ **Et la plateforme le savait déjà.** `datastore_active_lease` filtre sur
`claimed_until > NOW()`, et sa docstring dit « expiré compte pour libre » — sinon le
zombie de dix-huit jours aurait bloqué sa ligne pendant dix-huit jours. *La lecture,
elle, servait le nom sans regarder la date.*

> **Deux lectures voisines de la même donnée, et une seule connaissait la règle.**
> **Un champ servi affirmait ce que le système lui-même tenait pour faux.**

Ce que ça coûtait, et ce n'est pas théorique : un relevé qui compte les lignes réservées
en trouvait 495 sans qu'aucun travail ne tourne, et l'export destiné au client montrait
le nom d'un worker à côté de chaque ligne. *Le compteur de reprises porte déjà la trace
des tentatives : rien ne se perd à taire un bail que plus personne ne détient.*
"""
from __future__ import annotations

import uuid

import pytest


@pytest.fixture(scope="module")
def live(pg_dsn):
    import os

    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    name = "oto_bail_" + uuid.uuid4().hex[:8]
    root = psycopg.connect(pg_dsn, autocommit=True)
    root.execute(f'CREATE DATABASE "{name}"')
    prev_url, prev_pool = os.environ.get("DATABASE_URL"), dbconn._pool
    os.environ["DATABASE_URL"] = pg_dsn.rsplit("/", 1)[0] + "/" + name
    dbconn._pool = None
    try:
        from oto_mcp.db import init_db
        init_db()
        yield
    finally:
        if dbconn._pool is not None:
            dbconn._pool.close()
        dbconn._pool = prev_pool
        if prev_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = prev_url
        root.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        root.close()


@pytest.fixture
def table(live):
    from oto_mcp import db
    from oto_mcp.datastore.core import make_store
    ns = "t-" + uuid.uuid4().hex[:6]
    ns_id = db.create_datastore_namespace("user", "sub-test", ns)
    st = make_store("sub-test")
    st.set_schema(ns, {"key": "siren", "fields": [{"key": "siren", "type": "text"}]})
    row = st.append_row(ns, {"siren": "552032534"})
    return st, ns, ns_id, row["_id"]


def _poser_bail(ns_id: int, row_id: str, jusqua: str, qui: str = "mistral-2"):
    """Écrit un bail directement, pour fabriquer un expiré que rien ne pose plus."""
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        conn.execute(
            "UPDATE datastore_rows SET claimed_by = %s, claimed_until = %s::timestamptz "
            "WHERE ns_id = %s AND row_id = %s", (qui, jusqua, ns_id, row_id))


def test_un_bail_EXPIRE_nest_pas_servi(table):
    """⚠️ LE cas : 495 lignes de production le portaient, toutes mortes."""
    st, ns, ns_id, rid = table
    _poser_bail(ns_id, rid, "2026-08-13 14:14:18")      # dix-huit jours

    servi = st.get_row(ns, rid) or {}
    assert "_claimed_by" not in servi, (
        f"un bail expiré ne se sert pas comme une réservation : {servi.get('_claimed_by')!r}")
    assert "_claimed_until" not in servi


def test_un_bail_ACTIF_est_servi(table):
    """Le témoin négatif : on ne casse pas ce qui sert à la file."""
    st, ns, ns_id, rid = table
    _poser_bail(ns_id, rid, "2099-01-01 00:00:00")

    servi = st.get_row(ns, rid) or {}
    assert servi.get("_claimed_by") == "mistral-2"
    assert servi.get("_claimed_until") is not None


def test_la_LECTURE_et_la_GARDE_disent_la_meme_chose(table):
    """⚠️ La règle, et c'est elle qui manquait : deux lectures voisines de la même
    donnée doivent la lire pareil. La garde savait ; la lecture non."""
    from oto_mcp.db.rowlock import datastore_active_lease
    st, ns, ns_id, rid = table

    for jusqua, actif in (("2026-08-13 14:14:18", False), ("2099-01-01 00:00:00", True)):
        _poser_bail(ns_id, rid, jusqua)
        garde = datastore_active_lease(ns_id, rid) is not None
        lecture = "_claimed_by" in (st.get_row(ns, rid) or {})
        assert garde is actif, f"la garde se trompe sur {jusqua}"
        assert lecture is garde, (
            f"la lecture dit {lecture} là où la garde dit {garde} — {jusqua}")


def test_une_ligne_JAMAIS_reservee_ne_porte_rien(table):
    st, ns, ns_id, rid = table
    servi = st.get_row(ns, rid) or {}
    assert "_claimed_by" not in servi and "_claimed_until" not in servi
