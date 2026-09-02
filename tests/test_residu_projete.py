"""Le retrait du résidu de la recopie, contre un vrai PostgreSQL.

La recopie est arrêtée (`tests/test_recopie_arretee.py`) ; reste ce qu'elle a
déposé : au 2026-09-01, 70 876 nœuds sur 70 927 et 29 174 blocs, marqués
`props.legacy`. Ce fichier éprouve le geste qui les retire.

Trois choses s'y vérifient, et aucune ne se déduit du code :

1. **le retrait ne prend QUE le résidu** — les nœuds natifs (les couches de
   contexte, seules écritures directes de `db/guides.py`) et leurs blocs survivent ;
2. **les blocs partent avec leur nœud** — un nœud supprimé sans ses blocs laisse des
   orphelins qu'aucune requête ne relie plus à rien. C'est le mode d'échec qui ne se
   voit pas. ⚠️ Depuis #800 (2026-09-01) il ne dépend plus de ce que fait l'appelant :
   `blocks.node_id` porte une clé étrangère `ON DELETE CASCADE`, gardée par
   `tests/test_blocs_cascade.py`. Ce test-ci reste, et il vérifie autre chose de ce
   même geste — que le retrait n'emporte QUE le résidu ;
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
        "le nœud est parti, son bloc est resté : un orphelin que plus rien ne relie")
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
    assert set(rapport) == {"projetes", "blocs_attaches", "blocs_orphelins"}, (
        "l'inventaire à blanc doit nommer TOUTE la surface qu'`--apply` emporterait, "
        f"blocs attachés compris (#800) — {rapport}")
    assert db_nodes.count_projected_nodes() == avant, "le mode à blanc a écrit"
    assert maintenance._TRAVAUX["residu-projete"] is maintenance.residu_projete
    assert "residu-projete" in maintenance._ACTES
    assert "residu-projete" not in maintenance._ALL, (
        "un acte destructif est entré dans la routine quotidienne")


# --- Ce que le retrait ANNONCE, et ce qu'il ne doit plus emporter (#800) ------

def test_le_mode_a_blanc_annonce_les_blocs_attaches(live):
    """Point ② : l'inventaire taisait la plus grosse part de ce qu'il détruirait.

    ~34 000 blocs pendaient aux nœuds recopiés ; le mode à blanc n'annonçait que les
    nœuds et les orphelins. Un inventaire dont le rôle est de dire ce qu'on s'apprête
    à détruire, et qui en tait l'essentiel, donne confiance à tort — il vaut moins que
    pas d'inventaire du tout, puisqu'il rassure.
    """
    from oto_mcp import maintenance

    recopies = [_pose("doc", "recopiée A"), _pose("row", "recopiée B")]
    natif = _pose(None, "couche de contexte")

    rapport = maintenance.residu_projete()
    attendus = _sql("SELECT count(*) AS n FROM blocks b JOIN nodes n ON n.id = b.node_id "
                    "WHERE n.props ? 'legacy'")[0]["n"]
    assert rapport["blocs_attaches"] == attendus >= 2, (
        f"le mode à blanc ne dit pas les blocs qu'`--apply` emporterait — {rapport}")

    _sql("DELETE FROM nodes WHERE id = ANY(%s)", (recopies + [natif],))


def test_le_retrait_ne_balaie_plus_un_bloc_NATIF(live):
    """Point ③ : `delete_orphan_blocks()` n'avait aucun prédicat de provenance.

    Il supprimait TOUT bloc sans nœud — donc le corps d'une page **native** dont le
    nœud venait d'être supprimé par la fuite que #800 corrige par ailleurs, sous un nom
    (« résidu projeté ») qui promettait de ne toucher qu'à la copie. On le met dans la
    position exacte où il débordait : un orphelin natif présent quand le retrait joue.

    ⚠️ Le borner « au résidu marqué » est **impossible** : un orphelin n'a plus de
    nœud, donc plus de marque — rien ne distingue en base le corps d'une copie de celui
    d'une page native. Il est donc retiré, et c'est la clé étrangère
    (`tests/test_blocs_cascade.py`) qui empêche de naître ce qu'un balai aurait dû
    borner. Pour reconstituer un orphelin, ce test doit d'abord la déposer.
    """
    import json
    from oto_mcp import maintenance
    from oto_mcp.db import init_db, nodes as db_nodes

    assert not hasattr(db_nodes, "delete_orphan_blocks"), (
        "un balai sans prédicat de provenance a été remis dans `db/nodes.py`")

    _sql("ALTER TABLE blocks DROP CONSTRAINT IF EXISTS blocks_node_fk")
    natif = _pose(None, "page native supprimée par erreur")
    orphelin = _sql("SELECT public_id FROM blocks WHERE node_id = %s",
                    (natif,))[0]["public_id"]
    _sql("DELETE FROM nodes WHERE id = %s", (natif,))
    recopie = _pose("doc", "résidu à retirer")

    rapport = maintenance.residu_projete(dry_run=False)

    assert _sql("SELECT count(*) AS n FROM blocks WHERE public_id = %s",
                (orphelin,))[0]["n"] == 1, (
        "le retrait du résidu a emporté le corps d'une page NATIVE — le débordement "
        "du point ③")
    assert rapport["retires"] >= 1, rapport
    assert rapport["blocs_orphelins"] == 1, (
        "le témoin doit RENDRE l'orphelin qu'il ne retire pas : c'est ce qui dit que "
        f"la contrainte manque sur cette base, pas qu'il reste du ménage — {rapport}")

    _sql("DELETE FROM blocks WHERE public_id = %s", (orphelin,))
    _sql("DELETE FROM nodes WHERE id = %s", (recopie,))
    init_db()
