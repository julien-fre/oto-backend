"""Le retrait du résidu de la recopie, contre un vrai PostgreSQL.

La recopie est arrêtée (`tests/test_recopie_arretee.py`) ; reste ce qu'elle a
déposé : au 2026-09-01, 70 876 nœuds sur 70 927 et 29 174 blocs, marqués
`props.legacy`. Ce fichier éprouve le geste qui les retire.

Trois choses s'y vérifient, et aucune ne se déduit du code :

1. **le retrait ne prend QUE le résidu** — les nœuds natifs (les couches de
   contexte, seules écritures directes de `db/guides.py`) et leurs blocs survivent ;
2. **les blocs partent avec leur nœud** — `blocks.node_id` n'a aucune clé étrangère,
   donc rien ne cascade : un nœud supprimé sans ses blocs laisse des orphelins qu'aucune
   requête ne relie plus à rien. C'est le mode d'échec qui ne se voit pas ;
3. **le compte est un DIFFÉRENTIEL d'inventaire, pas la réponse du geste.** Un
   `DELETE` qui ne trouve rien répond « zéro ligne » exactement comme un `DELETE` qui
   vient de tout prendre. Le troisième test neutralise le retrait et exige que le
   compte le DISE — c'est la seule forme qui distingue « fait » de « rien à faire ».
"""
from __future__ import annotations

import uuid

import pytest


@pytest.fixture(scope="module")
def live(pg_dsn):
    """Une base JETABLE, le VRAI `init_db()`, le vrai pool."""
    import os

    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    name = "oto_residu_" + uuid.uuid4().hex[:8]
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


def _sql(sql, params=()):
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        cur = conn.execute(sql, params)
        return [dict(r) for r in cur.fetchall()] if cur.description else []


def _pose(marque: str | None, titre: str) -> int:
    """Un nœud, recopié (`marque`) ou natif (`None`), avec un bloc de corps."""
    import json
    props = {"title": titre}
    if marque is not None:
        props["legacy"] = marque
        props["legacy_id"] = "1"
    node = _sql(
        "INSERT INTO nodes (public_id, kind, owner_type, owner_id, props) "
        "VALUES (%s, 'page', 'user', 'u1', %s::jsonb) RETURNING id",
        ("pid_" + uuid.uuid4().hex[:10], json.dumps(props)))[0]["id"]
    _sql("INSERT INTO blocks (public_id, node_id, position, type, props) "
         "VALUES (%s, %s, 0, 'text', %s::jsonb)",
         ("blk_" + uuid.uuid4().hex[:12], node,
          json.dumps({"md": "corps de " + titre})))
    return node


def test_le_retrait_ne_prend_que_le_residu(live):
    from oto_mcp.db import nodes as db_nodes

    recopies = [_pose("doc", "page recopiée"), _pose("row", "ligne recopiée")]
    natifs = [_pose(None, "couche de contexte")]

    assert db_nodes.count_projected_nodes() >= 2
    db_nodes.delete_projected_nodes(batch_size=1)

    restants = {r["id"] for r in _sql("SELECT id FROM nodes")}
    assert not (set(recopies) & restants), "du résidu a survécu au retrait"
    assert set(natifs) <= restants, "le retrait a emporté un nœud natif"
    assert db_nodes.count_projected_nodes() == 0


def test_les_blocs_partent_avec_leur_noeud(live):
    from oto_mcp.db import nodes as db_nodes

    recopie, natif = _pose("doc", "recopiée"), _pose(None, "native")
    db_nodes.delete_projected_nodes()

    blocs = {r["node_id"] for r in _sql("SELECT node_id FROM blocks")}
    assert recopie not in blocs, (
        "le nœud est parti, son bloc est resté : un orphelin que plus rien ne "
        "relie — `blocks.node_id` n'a pas de clé étrangère, rien ne cascade")
    assert natif in blocs, "le corps d'un nœud natif a disparu"
    assert db_nodes.count_orphan_blocks() == 0


def test_le_compte_est_un_differentiel_pas_la_reponse_du_geste(live, monkeypatch):
    """Si le retrait ne retire RIEN, le rapport doit le dire.

    Sans ce test, un travail qui répondrait « retirés : 70 876 » en lisant le
    `rowcount` d'un `DELETE` joué sur une colonne déjà vide passerait pour un
    succès. On neutralise le geste et on exige que le compte reste honnête.
    """
    from oto_mcp import maintenance
    from oto_mcp.db import nodes as db_nodes

    _pose("doc", "résidu que personne ne retirera")
    avant = db_nodes.count_projected_nodes()
    assert avant > 0

    monkeypatch.setattr(db_nodes, "delete_projected_nodes",
                        lambda **kw: None)
    rapport = maintenance.residu_projete(dry_run=False)

    assert rapport["retires"] == 0, "un retrait qui n'a rien fait s'est déclaré fait"
    assert rapport["restants"] == avant


def test_a_blanc_par_defaut(live):
    """C'est un ACTE : sans `--apply`, il inventorie et ne touche à rien."""
    from oto_mcp import maintenance
    from oto_mcp.db import nodes as db_nodes

    _pose("doc", "résidu")
    avant = db_nodes.count_projected_nodes()

    rapport = maintenance.residu_projete()
    assert rapport["projetes"] == avant
    assert db_nodes.count_projected_nodes() == avant, "le mode à blanc a écrit"
    assert maintenance._TRAVAUX["residu-projete"] is maintenance.residu_projete
    assert "residu-projete" in maintenance._ACTES
    assert "residu-projete" not in maintenance._ALL, (
        "un acte destructif est entré dans la routine quotidienne")
