"""oto#150 — désinscription du DIGEST de signaux, exercée sur PostgreSQL réel.

Ce fichier prouve deux choses que rien d'autre ne peut prouver sans une vraie base :

1. `pending_signal_notices()` EXCLUT, en amont, les signaux d'un compte désinscrit
   du digest (`signal_digest_optouts`) — la garantie demandée par l'issue.
2. **Les deux canaux sont INDÉPENDANTS**, dans le MÊME test : se désinscrire du
   digest ne touche pas `outreach_optouts` (les relances restent dues), et se
   désinscrire des relances ne touche pas `signal_digest_optouts` (le digest reste
   dû). Une table commune ou un filtre partagé ferait glisser silencieusement l'un
   vers l'autre — c'est exactement ce que la décision d'Alexis (oto#150) refuse.

Patron de base éphémère : `test_outreach_audience_db.py::live`.
"""
from __future__ import annotations

import os
import uuid

import pytest

CAMPAGNE = "digest-optout-test"


@pytest.fixture(scope="module")
def live(pg_dsn):
    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    name = "oto_digest_optout_" + uuid.uuid4().hex[:8]
    root = psycopg.connect(pg_dsn, autocommit=True)
    root.execute(f'CREATE DATABASE "{name}"')
    dsn = pg_dsn.rsplit("/", 1)[0] + "/" + name

    avant_url, avant_pool = os.environ.get("DATABASE_URL"), dbconn._pool
    avant_key = os.environ.get("OTO_MCP_MASTER_KEY")
    os.environ["DATABASE_URL"] = dsn
    os.environ["OTO_MCP_MASTER_KEY"] = "4" * 64
    dbconn._pool = None
    try:
        from oto_mcp.db import init_db
        init_db()
        yield
    finally:
        if dbconn._pool is not None:
            dbconn._pool.close()
        dbconn._pool = avant_pool
        for cle, valeur in (("DATABASE_URL", avant_url), ("OTO_MCP_MASTER_KEY", avant_key)):
            if valeur is None:
                os.environ.pop(cle, None)
            else:
                os.environ[cle] = valeur
        root.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        root.close()


def _compte(conn, sub: str, email: str) -> None:
    conn.execute("INSERT INTO users (sub, email) VALUES (%s, %s)", (sub, email))


def _signal_termine(sub: str) -> int:
    from oto_mcp.db import usage as db_usage
    sid, _deja = db_usage.insert_usage_signal(
        sub=sub, org_id=None, signal="tool_feedback", kind="bug",
        target="x_tool", body="ça casse", session_id=None)
    db_usage.set_usage_signal_status(sid, status="resolved", by="op-1", note="corrigé")
    return sid


def test_digest_et_relance_sont_des_refus_INDEPENDANTS(live):
    """LE test du lot : deux comptes, deux désinscriptions, chacune sur SON canal
    seulement — vérifié dans les deux sens, dans le même test."""
    from oto_mcp.db._conn import _connect
    from oto_mcp.db import usage as db_usage
    from oto_mcp.db import outreach as db_outreach

    with _connect() as conn:
        _compte(conn, "sub-digest-only", "digest-only@exemple.test")
        _compte(conn, "sub-outreach-only", "outreach-only@exemple.test")
        _compte(conn, "sub-ni-l-un-ni-l-autre", "temoin@exemple.test")

    _signal_termine("sub-digest-only")
    _signal_termine("sub-outreach-only")
    _signal_termine("sub-ni-l-un-ni-l-autre")

    # Le témoin, non désinscrit de rien : sert d'ancre — s'il disparaissait lui
    # aussi, ce serait la preuve que le filtre mord trop large, pas qu'il marche.
    subs_avant = {r["sub"] for r in db_usage.pending_signal_notices()}
    assert subs_avant == {"sub-digest-only", "sub-outreach-only", "sub-ni-l-un-ni-l-autre"}

    # ── Compte A : désinscrit du DIGEST seulement ──────────────────────────────
    db_usage.opt_out_signal_digest("sub-digest-only", source="link")

    subs_apres_a = {r["sub"] for r in db_usage.pending_signal_notices()}
    assert "sub-digest-only" not in subs_apres_a, (
        "un compte désinscrit du digest ne doit plus y apparaître")
    assert {"sub-outreach-only", "sub-ni-l-un-ni-l-autre"} <= subs_apres_a, (
        "les deux autres comptes restent dus au digest")
    # INDÉPENDANCE (1/2) : la désinscription du digest n'a rien écrit côté relances.
    assert db_outreach.est_desinscrit("sub-digest-only") is False, (
        "se désinscrire du DIGEST ne désinscrit pas des RELANCES")

    # ── Compte B : désinscrit des RELANCES seulement ───────────────────────────
    db_outreach.desinscrire("sub-outreach-only", source="link")

    subs_apres_b = {r["sub"] for r in db_usage.pending_signal_notices()}
    # INDÉPENDANCE (2/2) : la désinscription des relances n'a RIEN retiré du digest.
    assert "sub-outreach-only" in subs_apres_b, (
        "se désinscrire des RELANCES ne désinscrit pas du DIGEST — le signal reste dû")
    assert "sub-digest-only" not in subs_apres_b, "le premier refus tient toujours"
    assert "sub-ni-l-un-ni-l-autre" in subs_apres_b, "le témoin n'a jamais bougé"

    # Le refus côté digest, lui, n'a jamais touché `outreach_optouts` du compte B.
    assert db_outreach.est_desinscrit("sub-ni-l-un-ni-l-autre") is False


def test_reinscrire_au_digest_fait_reapparaitre_les_signaux_dus(live):
    """Les signaux d'un compte désinscrit restent DUS (jamais marqués notifiés) —
    lever le refus doit donc les faire réapparaître, comme pour un compte sans
    adresse (`_notify_reporters`)."""
    from oto_mcp.db._conn import _connect
    from oto_mcp.db import usage as db_usage

    with _connect() as conn:
        _compte(conn, "sub-reinscription", "reinscription@exemple.test")
    _signal_termine("sub-reinscription")
    db_usage.opt_out_signal_digest("sub-reinscription", source="link")
    assert "sub-reinscription" not in {r["sub"] for r in db_usage.pending_signal_notices()}

    with _connect() as conn:
        conn.execute("DELETE FROM signal_digest_optouts WHERE sub = %s",
                     ("sub-reinscription",))
    assert "sub-reinscription" in {r["sub"] for r in db_usage.pending_signal_notices()}, (
        "le signal n'a jamais été marqué notifié : lever le refus le rend à nouveau dû")
