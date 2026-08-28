"""L'historique des acceptations côté BASE (#487) — la migration, et ce qu'elle garde.

Trois choses ne peuvent PAS se prouver sur un store simulé, et ce sont exactement
celles qui touchent la production :

1. **la migration vivante** — `legal_acceptances` portait une PK `(sub, doc_slug)`
   et des lignes de vrais consentements. Le boot doit les GARDER, leur donner un id,
   et retirer l'unicité qui interdisait l'historique. Un store en mémoire dirait
   « oui » à n'importe quel SQL ;
2. **l'append** — l'écriture d'avant était un upsert : elle passait aussi bien avec
   ou sans historique. Seule la base dit combien de lignes existent vraiment ;
3. **la lecture « la plus récente »** — `DISTINCT ON … ORDER BY accepted_at DESC,
   id DESC` est du SQL, pas de la logique Python.

D'où un vrai PostgreSQL (fixture `pg_dsn`, jetable), et la question posée à la
BASE — jamais au retour d'un appel.
"""
from __future__ import annotations

import uuid

import pytest

# La table telle qu'elle existe EN PRODUCTION avant ce lot. Recopiée à la main :
# une migration se teste contre l'état d'AVANT, et cet état-là n'existe plus dans le
# DDL du dépôt — le lire depuis `db/schema/legal.py` ne prouverait plus rien.
DDL_AVANT = """
CREATE TABLE legal_acceptances (
    sub TEXT NOT NULL,
    doc_slug TEXT NOT NULL,
    version TEXT NOT NULL,
    accepted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (sub, doc_slug)
);
"""

ANCIEN_SUB = "u-historique"


@pytest.fixture(scope="module")
def live(pg_dsn):
    """Une base neuve où `legal_acceptances` existe DÉJÀ, à l'ancienne forme et avec
    des lignes — puis `init_db()`, c'est-à-dire le vrai boot."""
    import os

    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    name = "oto_legal_" + uuid.uuid4().hex[:8]
    root = psycopg.connect(pg_dsn, autocommit=True)
    root.execute(f'CREATE DATABASE "{name}"')
    dsn = pg_dsn.rsplit("/", 1)[0] + "/" + name

    amont = psycopg.connect(dsn, autocommit=True)
    amont.execute(DDL_AVANT)
    amont.execute("INSERT INTO legal_acceptances (sub, doc_slug, version, accepted_at) "
                  "VALUES (%s, 'terms', '2.0', TIMESTAMPTZ '2026-07-01 08:00:00+00')",
                  (ANCIEN_SUB,))
    amont.close()

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


def _lignes(sub: str) -> list[dict]:
    from oto_mcp.db._conn import _connect

    with _connect() as conn:
        return conn.execute(
            "SELECT id, doc_slug, version, context, org_id, ip, user_agent, accepted_at "
            "FROM legal_acceptances WHERE sub = %s ORDER BY id", (sub,)).fetchall()


# ══ 1. la migration ══════════════════════════════════════════════════════════

def test_les_acceptations_deja_en_base_survivent_au_boot(live):
    (ligne,) = _lignes(ANCIEN_SUB)
    assert (ligne["doc_slug"], ligne["version"]) == ("terms", "2.0")
    assert ligne["accepted_at"].startswith("2026-07-01"), "la date d'origine, intacte"
    assert ligne["id"] is not None, "l'id surrogate est backfillé, pas laissé NULL"


def test_ce_qu_on_ne_sait_pas_de_l_ancien_reste_nul(live):
    """Aucun backfill de contexte ni d'IP : on ne sait pas d'où viennent ces lignes,
    et leur en inventer un ferait mentir la trace là où elle sert de preuve."""
    (ligne,) = _lignes(ANCIEN_SUB)
    assert (ligne["context"], ligne["org_id"], ligne["ip"], ligne["user_agent"]) == (
        None, None, None, None)


def test_l_unicite_sub_doc_a_disparu(live):
    """La PK `(sub, doc_slug)` est ce qui interdisait l'historique. Si elle survivait,
    la seconde acceptation d'un document échouerait — ici on le demande à la base."""
    from oto_mcp.db._conn import _connect

    with _connect() as conn:
        contraintes = conn.execute("""
            SELECT c.conname, pg_get_constraintdef(c.oid) AS def
              FROM pg_constraint c
              JOIN pg_class t ON t.oid = c.conrelid
             WHERE t.relname = 'legal_acceptances' AND c.contype IN ('p', 'u')
        """).fetchall()
    defs = [c["def"] for c in contraintes]
    assert "PRIMARY KEY (id)" in defs, "la nouvelle PK est l'id de l'ÉVÉNEMENT"
    assert not [d for d in defs if "sub" in d and "doc_slug" in d], (
        f"une unicité (sub, doc_slug) subsiste : {defs}")


def test_le_boot_est_rejouable(live):
    """Le one-shot est gardé sur l'absence de la colonne `id` : un second boot ne
    doit ni rejouer la bascule de PK, ni toucher aux lignes."""
    from oto_mcp.db import init_db

    avant = _lignes(ANCIEN_SUB)
    init_db()
    assert _lignes(ANCIEN_SUB) == avant


# ══ 2. l'écriture APPEND, et la lecture de la plus récente ═══════════════════

def test_deux_acceptations_font_deux_lignes_et_la_lecture_prend_la_derniere(live):
    from oto_mcp.db import legal as db_legal

    sub = "u-" + uuid.uuid4().hex[:8]
    db_legal.record_legal_acceptances(sub, [("cgv", "1.0")], context="access")
    db_legal.record_legal_acceptances(sub, [("cgv", "2.0")], context="purchase")

    lignes = _lignes(sub)
    assert [l["version"] for l in lignes] == ["1.0", "2.0"], (
        "l'acceptation de la 1.0 doit rester : c'est une preuve, pas un état")
    assert db_legal.get_legal_acceptances(sub)["cgv"]["version"] == "2.0"


def test_la_trace_situe_l_acte(live):
    from oto_mcp.db import legal as db_legal

    sub = "u-" + uuid.uuid4().hex[:8]
    db_legal.record_legal_acceptances(
        sub, [("terms", "3.0"), ("cgv", "2.0"), ("dpa", "2.0")],
        context="purchase", org_id=219, ip="203.0.113.7",
        user_agent="Mozilla/5.0 (X11; Linux x86_64) Firefox/141.0")

    lignes = _lignes(sub)
    assert len(lignes) == 3, "un achat = trois documents = trois lignes"
    for ligne in lignes:
        assert ligne["context"] == "purchase" and ligne["org_id"] == 219
        assert ligne["ip"] == "203.0.113.7"
        assert "Firefox" in ligne["user_agent"]


def test_le_departage_ne_tient_pas_qu_a_la_date(live):
    """`accepted_at` vaut `NOW()`, l'horloge de la TRANSACTION : deux acceptations
    d'un même document écrites dans la même transaction portent la MÊME date. Sans
    départage par `id`, la lecture rendrait l'une ou l'autre au hasard."""
    from oto_mcp.db._conn import _connect
    from oto_mcp.db import legal as db_legal

    sub = "u-" + uuid.uuid4().hex[:8]
    with _connect() as conn:
        for version in ("1.0", "2.0"):
            conn.execute("INSERT INTO legal_acceptances (sub, doc_slug, version) "
                         "VALUES (%s, 'dpa', %s)", (sub, version))
    lignes = _lignes(sub)
    assert lignes[0]["accepted_at"] == lignes[1]["accepted_at"], "même transaction"
    assert db_legal.get_legal_acceptances(sub)["dpa"]["version"] == "2.0"
