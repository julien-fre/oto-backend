"""Le verrou de ligne devient natif (#317) — libération par run + protection en écriture.

Deux manques mesurés sur le terrain, que ce lot comble :

**① L'agent qui meurt garde sa ligne.** La pile de run est session-scopée (aucune
table) : elle ne survit ni au redémarrage ni à l'agent disparu — or c'est précisément
lui qu'il faut ramasser. Le lien run→ligne est donc durable, porté par le bail
lui-même. Mesure qui l'a rendu nécessaire : **une** ligne portait un bail sur toute la
production, tenue depuis **18 jours** par un worker disparu, invisible de tous.

**② Le bail protégeait l'attribution, pas la donnée.** Deux agents ne prenaient pas la
même ligne, mais rien n'empêchait le second d'écrire dessus.

⚠️ Le titulaire s'identifie de DEUX façons qui se recouvrent, parce qu'une écriture
ordinaire ne dit pas qui écrit et que `claimed_by` est un libellé libre, jamais un
compte : par le RUN (transparent, cas nominal) ou par le WORKER rejoué (la sortie
explicite hors run, déjà la garde du release).
"""
from __future__ import annotations

import uuid

import pytest


@pytest.fixture(scope="module")
def live(pg_dsn):
    import os

    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    name = "oto_lock_" + uuid.uuid4().hex[:8]
    root = psycopg.connect(pg_dsn, autocommit=True)
    root.execute(f'CREATE DATABASE "{name}"')
    dsn = pg_dsn.rsplit("/", 1)[0] + "/" + name

    prev_url, prev_pool = os.environ.get("DATABASE_URL"), dbconn._pool
    os.environ["DATABASE_URL"] = dsn
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
    ns = "q-" + uuid.uuid4().hex[:6]
    ns_id = db.create_datastore_namespace("user", "sub-test", ns)
    for i in range(3):
        db.datastore_insert_row(ns_id, f"r{i}", {"societe": f"Boîte {i}"})
    return ns, ns_id


def _store():
    from oto_mcp.datastore import make_store
    return make_store("sub-test")


def _bail(ns_id: int, row_id: str) -> dict:
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        r = conn.execute(
            "SELECT claimed_by, claimed_until, claimed_run FROM datastore_rows "
            "WHERE ns_id = %s AND row_id = %s", (ns_id, row_id)).fetchone()
    return dict(r or {})


# ── ① la libération par run ──────────────────────────────────────────────────

def test_a_claim_records_the_run_that_holds_it(table):
    """Le lien est DURABLE, porté par le bail — pas par la pile de session, qui ne
    survivrait pas à l'agent mort qu'on cherche justement à ramasser."""
    from oto_mcp import db, session_org
    ns, ns_id = table

    token = session_org.set_call_run("run-abc")
    try:
        row = _store().claim_next(ns, worker="w1")
    finally:
        session_org.reset_call_run(token) if hasattr(session_org, "reset_call_run") else None

    assert row is not None
    assert _bail(ns_id, row["_id"])["claimed_run"] == "run-abc"
    assert db.datastore_release_by_run("run-abc") == 1
    assert _bail(ns_id, row["_id"])["claimed_by"] is None


def test_closing_a_run_frees_everything_it_held(table):
    """⚠️ Le cas mesuré : l'agent meurt sans relâcher. La fermeture du run rend TOUT
    ce qu'il tenait, quel que soit son issue — c'est ce qui manquait pendant 18 jours
    sur la seule ligne réservée qu'ait portée la production."""
    from oto_mcp import db, session_org
    ns, ns_id = table

    token = session_org.set_call_run("run-mort")
    try:
        a = _store().claim_next(ns, worker="w1")
        b = _store().claim_next(ns, worker="w1")
    finally:
        session_org.reset_call_run(token) if hasattr(session_org, "reset_call_run") else None
    assert a and b

    assert db.datastore_release_by_run("run-mort") == 2
    for r in (a, b):
        assert _bail(ns_id, r["_id"])["claimed_by"] is None


def test_a_run_only_frees_what_it_held(table):
    """Un run ne libère que SES lignes — sinon fermer un run relâcherait le travail
    d'un collègue, ce qui serait pire que le défaut d'origine."""
    from oto_mcp import db, session_org
    ns, ns_id = table

    t1 = session_org.set_call_run("run-1")
    a = _store().claim_next(ns, worker="w1")
    session_org.reset_call_run(t1) if hasattr(session_org, "reset_call_run") else None
    t2 = session_org.set_call_run("run-2")
    b = _store().claim_next(ns, worker="w2")
    session_org.reset_call_run(t2) if hasattr(session_org, "reset_call_run") else None

    assert db.datastore_release_by_run("run-1") == 1
    assert _bail(ns_id, a["_id"])["claimed_by"] is None
    assert _bail(ns_id, b["_id"])["claimed_by"] == "w2", "l'autre run garde la sienne"


def test_releasing_an_unknown_run_is_a_cheap_no_op():
    from oto_mcp import db
    assert db.datastore_release_by_run("run-jamais-vu") == 0
    assert db.datastore_release_by_run("") == 0


# ── ② la protection en écriture ──────────────────────────────────────────────

def test_writing_on_a_row_held_by_another_is_refused(table):
    """Le bail protège désormais la DONNÉE, pas seulement l'attribution."""
    from oto_mcp import session_org
    from oto_mcp.datastore import RowLocked
    ns, ns_id = table

    t = session_org.set_call_run("run-titulaire")
    row = _store().claim_next(ns, worker="w1")
    session_org.reset_call_run(t) if hasattr(session_org, "reset_call_run") else None

    with pytest.raises(RowLocked) as e:
        _store().append_row(ns, {"_id": row["_id"], "societe": "Écrit par un autre"}) \
            if False else _store().upsert_row(ns, row["_id"], {"societe": "Par un autre"})

    # L'erreur donne la SORTIE, pas seulement le constat.
    msg = str(e.value)
    assert "w1" in msg and "data_release" in msg


def test_the_holder_writes_freely_through_its_run(table):
    """La première des deux identifications : écrire sous le run qui tient la ligne,
    c'est être le titulaire — rien à déclarer, le cas nominal est transparent."""
    from oto_mcp import session_org
    ns, ns_id = table

    t = session_org.set_call_run("run-x")
    try:
        row = _store().claim_next(ns, worker="w1")
        _store().upsert_row(ns, row["_id"], {"societe": "Par son titulaire"})
    finally:
        session_org.reset_call_run(t) if hasattr(session_org, "reset_call_run") else None

    from oto_mcp import db
    assert db.datastore_get_row(ns_id, row["_id"])["data"]["societe"] == "Par son titulaire"


def test_the_holder_writes_freely_through_its_worker(table):
    """La seconde : la sortie explicite hors run — un agent qui reprend son travail
    dans une autre session doit pouvoir écrire SA ligne."""
    from oto_mcp import db, session_org
    from oto_mcp.datastore import writing_as
    ns, ns_id = table

    t = session_org.set_call_run("run-y")
    row = _store().claim_next(ns, worker="w1")
    session_org.reset_call_run(t) if hasattr(session_org, "reset_call_run") else None

    with writing_as("w1"):                      # hors du run, mais je suis w1
        _store().upsert_row(ns, row["_id"], {"societe": "Reprise"})

    assert db.datastore_get_row(ns_id, row["_id"])["data"]["societe"] == "Reprise"


def test_an_expired_lease_protects_nothing(table):
    """⚠️ La nuance qui empêche la protection de devenir un mur : seul un bail ACTIF
    protège. Sans elle, le zombie de 18 jours mesuré en production aurait bloqué sa
    ligne pendant 18 jours."""
    from oto_mcp import db
    from oto_mcp.db._conn import _connect
    ns, ns_id = table

    row = _store().claim_next(ns, worker="w1")
    with _connect() as conn:                    # on fait expirer le bail
        conn.execute("UPDATE datastore_rows SET claimed_until = NOW() - interval '1 day' "
                     "WHERE ns_id = %s AND row_id = %s", (ns_id, row["_id"]))

    _store().upsert_row(ns, row["_id"], {"societe": "Le zombie ne bloque rien"})

    assert db.datastore_get_row(
        ns_id, row["_id"])["data"]["societe"] == "Le zombie ne bloque rien"


def test_a_free_row_is_written_as_before(table):
    """Hors bail actif, tout le monde écrit comme avant — le lot ne durcit que ce
    qui est réservé."""
    from oto_mcp import db
    ns, ns_id = table
    _store().upsert_row(ns, "r2", {"societe": "Libre"})
    assert db.datastore_get_row(ns_id, "r2")["data"]["societe"] == "Libre"


def test_releasing_then_writing_is_the_documented_way_out(table):
    """La sortie officielle, celle que l'erreur indique : lever le bail, puis écrire.
    Deux gestes délibérés — il n'y a pas de « forcer » en un clic, un bouton force
    devenant un réflexe qui rendrait le verrou décoratif."""
    from oto_mcp import db, session_org
    from oto_mcp.datastore import RowLocked
    ns, ns_id = table

    t = session_org.set_call_run("run-z")
    row = _store().claim_next(ns, worker="w1")
    session_org.reset_call_run(t) if hasattr(session_org, "reset_call_run") else None

    with pytest.raises(RowLocked):
        _store().upsert_row(ns, row["_id"], {"societe": "Non"})

    _store().force_release(ns, row["_id"])      # le geste d'un humain qui a le droit
    _store().upsert_row(ns, row["_id"], {"societe": "Oui"})

    assert db.datastore_get_row(ns_id, row["_id"])["data"]["societe"] == "Oui"


def test_every_write_path_is_covered_not_just_the_merge(table):
    """⚠️ **Le trou que les tests ont trouvé.** Le seam de FUSION n'est pas le seul
    chemin d'écriture : le remplacement, la mise à jour et la suppression n'y passent
    pas. Une protection posée sur le seul merge aurait été un verrou troué — et le
    trou aurait été invisible, puisque le cas le plus courant, lui, était protégé."""
    from oto_mcp import session_org
    from oto_mcp.datastore import RowLocked
    ns, ns_id = table

    t = session_org.set_call_run("run-couverture")
    row = _store().claim_next(ns, worker="w1")
    session_org.reset_call_run(t)
    rid = row["_id"]

    # remplacement intégral
    with pytest.raises(RowLocked):
        _store().upsert_row(ns, rid, {"societe": "non"})
    # suppression — plus destructrice qu'une écriture, donc gardée aussi
    with pytest.raises(RowLocked):
        _store().delete_row(ns, rid)


def test_a_brand_new_row_is_never_blocked(table):
    """Une ligne qui n'existe pas encore ne peut pas être réservée : la garde ne doit
    pas coûter un refus (ni même une lecture inutile) sur le chemin de création."""
    ns, ns_id = table
    from oto_mcp import db
    st = _store()
    st.upsert_row(ns, "toute-neuve", {"societe": "Créée"})
    assert db.datastore_get_row(ns_id, "toute-neuve")["data"]["societe"] == "Créée"
