"""La pose d'index de clé métier a une BORNE, et ce qu'elle fait quand elle coupe.

Second volet du correctif de l'incident du 2026-09-01 (le premier est
`test_capacites_hors_boucle.py`). Sortir le travail de la boucle ne suffisait pas : hors
boucle, le même `CREATE INDEX CONCURRENTLY` aurait tenu un thread du threadpool pendant
les 47 minutes de la requête qui le retenait, et laissé son appelant pendu. Il fallait
donc aussi qu'il RENONCE.

Ce qui se vérifie ici, dans l'ordre où ça compte :

1. **le mécanisme est réel** — une simple LECTURE qui tourne retient la pose d'index,
   sans poser le moindre verrou gênant. C'est le fait contre-intuitif de l'incident ;
2. **la borne coupe** — au lieu d'attendre la fin de la lecture ;
3. **ce qui reste est cohérent** — le schéma EST écrit, l'appel REND un résultat (pas un
   500 sur un schéma pourtant posé), et il DIT que la contrainte manque ;
4. **la borne ne coûte rien** quand rien n'est devant.

Ces tests exigent un vrai PostgreSQL : une borne de verrou ne s'observe pas sur un stub,
et c'est précisément l'attente côté serveur qu'on mesure.

⚠️ Ce banc ouvre une transaction qui TOURNE (`pg_sleep`) sur SA base jetable. Jamais
ailleurs : c'est exactement le geste qui a coupé la production.
"""
from __future__ import annotations

import os
import threading
import time
import uuid

import pytest


@pytest.fixture(scope="module")
def base_jetable(pg_dsn):
    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    nom = "oto_borne_" + uuid.uuid4().hex[:8]
    root = psycopg.connect(pg_dsn, autocommit=True)
    root.execute(f'CREATE DATABASE "{nom}"')
    dsn = pg_dsn.rsplit("/", 1)[0] + "/" + nom

    url_prec, pool_prec = os.environ.get("DATABASE_URL"), dbconn._pool
    os.environ["DATABASE_URL"] = dsn
    dbconn._pool = None
    try:
        from oto_mcp.db import init_db
        init_db()
        yield dsn
    finally:
        if dbconn._pool is not None:
            dbconn._pool.close()
        dbconn._pool = pool_prec
        if url_prec is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = url_prec
        root.execute(f'DROP DATABASE IF EXISTS "{nom}" WITH (FORCE)')
        root.close()


@pytest.fixture
def tableau(base_jetable):
    """Un tableau avec quelques lignes — l'index doit avoir quelque chose à balayer."""
    from oto_mcp import db
    ns = "t-" + uuid.uuid4().hex[:6]
    ns_id = db.create_datastore_namespace("user", "sub-borne", ns)
    for i in range(50):
        db.datastore_insert_row(ns_id, f"r{i}", {"siren": f"{552032534 + i}"})
    return ns, ns_id


class _LectureQuiTourne:
    """Une LECTURE, rien d'autre : `SELECT … pg_sleep(n)`.

    Elle ne pose aucun verrou qui gênerait une écriture. Elle tient seulement un
    snapshot — et c'est ce snapshot que `CREATE INDEX CONCURRENTLY` attend avant sa
    passe de validation. Le cœur du piège de l'incident tient dans cette phrase.

    ⚠️ Une transaction simplement `idle in transaction` en READ COMMITTED ne le
    retiendrait PAS (plus de snapshot tenu entre deux ordres) : vérifié, et c'est
    pourquoi ce banc fait tourner une requête au lieu d'en laisser une ouverte."""

    def __init__(self, dsn: str, secondes: float):
        self.dsn, self.secondes = dsn, secondes
        self.demarree = threading.Event()
        self._t = threading.Thread(target=self._go, daemon=True)

    def _go(self):
        import psycopg
        with psycopg.connect(self.dsn) as c:
            c.execute("SELECT 1").fetchone()          # la transaction s'ouvre
            self.demarree.set()
            c.execute("SELECT count(*), pg_sleep(%s) FROM datastore_rows",
                      (self.secondes,)).fetchone()
            c.rollback()

    def __enter__(self):
        self._t.start()
        assert self.demarree.wait(10), "la lecture témoin n'a pas démarré"
        time.sleep(0.3)                                # qu'elle soit bien dans pg_sleep
        return self

    def __exit__(self, *a):
        self._t.join(self.secondes + 10)


_SCHEMA = {"key": "siren", "fields": [{"key": "siren", "type": "text"}]}


def test_une_lecture_qui_tourne_retient_vraiment_la_pose_dindex(tableau, base_jetable,
                                                                monkeypatch):
    """Le fait de l'incident, établi AVANT de mesurer le remède.

    Sans ce test, « la borne coupe » ne prouverait rien : elle pourrait couper une
    attente qui n'aurait jamais eu lieu."""
    from oto_mcp import db
    _, ns_id = tableau
    monkeypatch.setenv("OTO_MCP_DDL_LOCK_TIMEOUT_MS", "0")       # bornes désarmées
    monkeypatch.setenv("OTO_MCP_DDL_STATEMENT_TIMEOUT_MS", "0")

    with _LectureQuiTourne(base_jetable, 3.0):
        t0 = time.monotonic()
        db.datastore_ensure_key_index(ns_id, "siren")
        attendu = time.monotonic() - t0

    assert attendu > 1.5, (
        f"la pose n'a attendu que {attendu:.2f}s : la lecture témoin ne retient plus "
        "l'index, donc le reste de ce banc mesure autre chose que l'incident")


def test_la_borne_coupe_au_lieu_dattendre(tableau, base_jetable, monkeypatch):
    from oto_mcp import db
    _, ns_id = tableau
    monkeypatch.setenv("OTO_MCP_DDL_LOCK_TIMEOUT_MS", "300")

    with _LectureQuiTourne(base_jetable, 4.0):
        t0 = time.monotonic()
        with pytest.raises(db.KeyIndexUnavailable):
            db.datastore_ensure_key_index(ns_id, "siren")
        attendu = time.monotonic() - t0

    assert attendu < 2.5, (
        f"la borne a mis {attendu:.2f}s à rendre la main sur une lecture de 4s — elle "
        "ne coupe pas, elle accompagne")


def test_le_schema_est_ecrit_et_le_manque_est_DIT(tableau, base_jetable, monkeypatch):
    """Le comportement d'ensemble : ni 500 opaque, ni succès muet.

    Le schéma est déjà écrit quand l'index échoue. Rendre une erreur ferait chercher un
    dégât qui n'existe pas ; taire l'échec ferait croire à une contrainte absente. La
    réponse porte donc le schéma ET l'avertissement."""
    from oto_mcp.datastore.core import make_store
    ns, ns_id = tableau
    monkeypatch.setenv("OTO_MCP_DDL_LOCK_TIMEOUT_MS", "300")
    store = make_store("sub-borne")

    with _LectureQuiTourne(base_jetable, 4.0):
        out = store.set_schema(ns, _SCHEMA)

    assert out["schema"] == _SCHEMA, "le schéma posé doit être rendu tel quel"
    assert store.get_schema(ns) == _SCHEMA, (
        "le schéma n'est pas en base : l'échec de l'index a emporté l'écriture avec lui")
    assert "warning" in out and "siren" in out["warning"], (
        f"l'appel a réussi sans dire que la contrainte manque : {out!r}")
    from oto_mcp import db
    assert not db.datastore_has_key_index(ns_id), (
        "l'avertissement annonce un index manquant alors qu'il est là — message faux")


def test_la_borne_ne_coute_rien_quand_rien_ne_bloque(tableau, monkeypatch):
    """Le contrepoids : une borne qui gênerait le cas nominal serait une régression
    déguisée en correctif."""
    from oto_mcp import db
    _, ns_id = tableau
    monkeypatch.setenv("OTO_MCP_DDL_LOCK_TIMEOUT_MS", "300")

    t0 = time.monotonic()
    db.datastore_ensure_key_index(ns_id, "siren")
    assert time.monotonic() - t0 < 2.0
    assert db.datastore_has_key_index(ns_id)


def test_le_travail_de_fond_nest_PAS_borne(tableau, base_jetable, monkeypatch):
    """`bornee=False` : le timer de maintenance a le droit d'attendre son tour.

    Le borner garantirait qu'un index sur une table très occupée ne se pose jamais —
    or c'est justement lui qui rattrape ce que le chemin de requête a renoncé à faire."""
    from oto_mcp import db
    _, ns_id = tableau
    monkeypatch.setenv("OTO_MCP_DDL_LOCK_TIMEOUT_MS", "300")

    with _LectureQuiTourne(base_jetable, 3.0):
        db.datastore_ensure_key_index(ns_id, "siren", bornee=False)   # attend, et pose

    assert db.datastore_has_key_index(ns_id)
