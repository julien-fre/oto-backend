"""Le nœud a une colonne de DONNÉE et les cinq colonnes du bail, sur un vrai PG.

Deux natures se disputaient `props` : ce que le nœud EST pour la plateforme (titre,
épingle, livraison, schéma d'enfants — des clés qu'oto interprète) et ce que
l'utilisateur y a MIS (les valeurs des colonnes de son tableau — des clés dont oto ne
sait rien). Mêlées, une donnée nommée `title` écrase le sens du nœud, et toute lecture
doit connaître la liste des clés réservées pour trier.

Le bail, lui, avait deux colonnes ici et cinq sur la table historique. Un verrou qui
ignore sous quel run une ligne est réservée, combien de fois elle a été reprise et
pourquoi elle a été abandonnée n'est pas le même verrou : c'est celui d'avant les deux
corrections qui l'ont rendu sûr.

⚠️ **Ce fichier vérifie la FORME, pas un usage** — les surfaces d'écriture des
tableaux et des lignes viennent ensuite, et ce sont elles qui rempliront `data`. Une
colonne posée sans lecteur est un mécanisme inerte tant que rien ne s'en sert ; on le
dit ici plutôt que de laisser croire le contraire.
"""
from __future__ import annotations

import uuid

import pytest


@pytest.fixture(scope="module")
def live(pg_dsn):
    import os

    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    name = "oto_forme_" + uuid.uuid4().hex[:8]
    root = psycopg.connect(pg_dsn, autocommit=True)
    root.execute(f'CREATE DATABASE "{name}"')
    dsn = pg_dsn.rsplit("/", 1)[0] + "/" + name

    previous_url, previous_pool = os.environ.get("DATABASE_URL"), dbconn._pool
    os.environ["DATABASE_URL"] = dsn
    dbconn._pool = None
    try:
        from oto_mcp.db import init_db
        init_db()
        yield dsn
    finally:
        if dbconn._pool is not None:
            dbconn._pool.close()
        dbconn._pool = previous_pool
        if previous_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous_url
        root.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        root.close()


def _colonnes(table: str) -> dict:
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        return {r["column_name"]: (r["data_type"], r["is_nullable"])
                for r in conn.execute(
                    "SELECT column_name, data_type, is_nullable "
                    "FROM information_schema.columns WHERE table_name = %s",
                    (table,)).fetchall()}


def test_le_noeud_a_sa_colonne_de_donnee(live):
    cols = _colonnes("nodes")
    assert "data" in cols, "la donnée métier n'a pas de colonne à elle"
    assert cols["data"] == ("jsonb", "NO")


def test_le_bail_a_ses_cinq_colonnes_des_deux_cotes(live):
    """Le même verrou doit pouvoir servir les deux tables : mêmes colonnes, mêmes
    types. Une divergence ici, et c'est deux mécaniques qui s'écartent au premier
    correctif appliqué d'un seul côté."""
    bail = ("claimed_by", "claimed_until", "claimed_run", "claims", "abandon_reason")
    noeuds, lignes = _colonnes("nodes"), _colonnes("datastore_rows")
    manquantes = [c for c in bail if c not in noeuds]
    assert not manquantes, manquantes
    for c in bail:
        assert noeuds[c][0] == lignes[c][0], (
            f"`{c}` n'a pas le même type des deux côtés : "
            f"{noeuds[c][0]} sur nodes, {lignes[c][0]} sur datastore_rows")


def test_la_donnee_et_les_proprietes_ne_se_melangent_pas(live):
    """Une clé métier nommée comme une propriété du nœud ne doit rien écraser."""
    import json
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        conn.execute(
            "INSERT INTO nodes (public_id, kind, owner_type, owner_id, props, data) "
            "VALUES (%s, 'ligne', 'org', '1', %s::jsonb, %s::jsonb)",
            ("nod_" + uuid.uuid4().hex[:10],
             json.dumps({"title": "Prospects Q3"}),
             json.dumps({"title": "Société Dupont", "position": "gérant"})))
        r = conn.execute(
            "SELECT props->>'title' AS p, data->>'title' AS d, "
            "       data->>'position' AS pos FROM nodes WHERE kind = 'ligne'"
        ).fetchone()
    assert r["p"] == "Prospects Q3", "la donnée métier a écrasé le titre du nœud"
    assert r["d"] == "Société Dupont"
    assert r["pos"] == "gérant", (
        "une valeur métier nommée `position` doit rester une valeur, pas devenir "
        "l'ordre du nœud dans sa fratrie")


def test_l_index_du_bail_n_est_PAS_pose(live):
    """Le contre-test : les colonnes sont là, l'index ne l'est pas.

    Le chemin de réservation lit encore la table historique. Un index sur un prédicat
    que personne n'interroge est un coût d'écriture pur, et sa forme utile dépend d'un
    arbitrage de contrat — toute forme indexable en partiel change l'ordre observable
    de la file. Poser l'index « pour la symétrie » est le geste que ce test refuse.
    """
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        idx = {r["indexname"] for r in conn.execute(
            "SELECT indexname FROM pg_indexes WHERE tablename = 'nodes'").fetchall()}
    assert not {i for i in idx if "claim" in i}, sorted(idx)
