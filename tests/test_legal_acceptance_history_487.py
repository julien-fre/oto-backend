"""Le journal des acceptations côté BASE (#487) — le lot A additif, et sa réversibilité.

Trois choses ne peuvent PAS se prouver sur un store simulé, et ce sont exactement
celles qui touchent la production :

1. **l'ancien chemin d'écriture continue de marcher.** Prod et preprod partagent la
   base : le code servi en production (v1.157.0) fait son
   `INSERT … ON CONFLICT (sub, doc_slug)` sur `legal_acceptances`. Ce lot ne doit RIEN
   lui retirer — ni la table, ni sa PK. Ici on rejoue son SQL **verbatim** sur la base
   migrée : s'il tombe, l'acceptation des CGU casse en prod pendant la fenêtre ;
2. **la recopie au boot.** Pendant cette même fenêtre, la prod écrit dans la
   projection SEULE. Le journal — que le gate lit — doit rattraper ces lignes-là, à
   chaque boot, sinon on redemande ses CGU à quelqu'un qui vient de les accepter ;
3. **l'écriture double et la lecture unique.** Le nouveau chemin écrit les deux ; les
   lectures ne regardent QUE le journal.

D'où un vrai PostgreSQL (fixture `pg_dsn`, jetable), et la question posée à la
BASE — jamais au retour d'un appel.
"""
from __future__ import annotations

import uuid

import pytest

# La table telle qu'elle existe EN PRODUCTION avant ce lot. Recopiée à la main pour
# que le test parte de l'état d'AVANT, sans dépendre du DDL courant du dépôt.
DDL_AVANT = """
CREATE TABLE legal_acceptances (
    sub TEXT NOT NULL,
    doc_slug TEXT NOT NULL,
    version TEXT NOT NULL,
    accepted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (sub, doc_slug)
);
"""

# Le SQL du code SERVI EN PRODUCTION (oto-backend v1.157.0, `db/legal.py`), recopié
# mot pour mot. Il n'a pas à être joli : il a à passer tel quel sur la base migrée.
ECRITURE_V1_157_0 = (
    "INSERT INTO legal_acceptances (sub, doc_slug, version, accepted_at) "
    "VALUES (%s, %s, %s, NOW()) "
    "ON CONFLICT (sub, doc_slug) DO UPDATE SET "
    "version = EXCLUDED.version, accepted_at = EXCLUDED.accepted_at"
)
LECTURE_V1_157_0 = (
    "SELECT doc_slug, version, accepted_at FROM legal_acceptances WHERE sub = %s"
)

ANCIEN_SUB = "u-avant-le-journal"


@pytest.fixture(scope="module")
def live(pg_dsn):
    """Une base neuve où `legal_acceptances` existe DÉJÀ, avec une acceptation —
    puis `init_db()`, c'est-à-dire le vrai boot du lot."""
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


def _journal(sub: str) -> list[dict]:
    from oto_mcp.db._conn import _connect

    with _connect() as conn:
        return conn.execute(
            "SELECT id, org_id, doc_slug, version, context, ip, user_agent, accepted_at "
            "FROM legal_acceptance_events WHERE sub = %s ORDER BY id", (sub,)).fetchall()


def _projection(sub: str) -> list[dict]:
    from oto_mcp.db._conn import _connect

    with _connect() as conn:
        return conn.execute(
            "SELECT doc_slug, version, accepted_at FROM legal_acceptances "
            "WHERE sub = %s ORDER BY doc_slug", (sub,)).fetchall()


# ══ 1. rien n'est retiré à la production ═════════════════════════════════════

def test_la_pk_de_la_projection_est_intacte(live):
    """C'est L'ARBITRE du `ON CONFLICT` que la prod exécute. La retirer casserait son
    acceptation des CGU — donc l'inscription — pendant toute la fenêtre."""
    from oto_mcp.db._conn import _connect

    with _connect() as conn:
        defs = [c["def"] for c in conn.execute("""
            SELECT pg_get_constraintdef(c.oid) AS def
              FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid
             WHERE t.relname = 'legal_acceptances' AND c.contype IN ('p', 'u')
        """).fetchall()]
    assert "PRIMARY KEY (sub, doc_slug)" in defs, defs


def test_le_chemin_d_ecriture_de_la_v1_157_0_reste_vert_sur_la_base_migree(live):
    """Le SQL de la version en PRODUCTION, rejoué tel quel. Deux fois : la seconde
    exerce la branche `DO UPDATE`, celle qui a besoin de l'arbitre."""
    from oto_mcp.db._conn import _connect

    sub = "u-prod-" + uuid.uuid4().hex[:6]
    with _connect() as conn:
        conn.execute(ECRITURE_V1_157_0, (sub, "terms", "3.0"))
        conn.execute(ECRITURE_V1_157_0, (sub, "terms", "4.0"))
        lues = conn.execute(LECTURE_V1_157_0, (sub,)).fetchall()

    assert [(l["doc_slug"], l["version"]) for l in lues] == [("terms", "4.0")], (
        "la prod continue de voir sa dernière acceptation, exactement comme avant")


# ══ 2. la recopie au boot ════════════════════════════════════════════════════

def test_les_acceptations_deja_en_base_entrent_dans_le_journal(live):
    (ligne,) = _journal(ANCIEN_SUB)
    assert (ligne["doc_slug"], ligne["version"]) == ("terms", "2.0")
    assert ligne["accepted_at"].startswith("2026-07-01"), "la date d'origine, conservée"


def test_ce_qu_on_ne_sait_pas_de_l_ancien_reste_nul(live):
    """Aucun backfill de contexte ni d'IP : la projection ne les a jamais portés, et
    leur en inventer ferait mentir la trace là où elle sert de preuve."""
    (ligne,) = _journal(ANCIEN_SUB)
    assert (ligne["context"], ligne["org_id"], ligne["ip"], ligne["user_agent"]) == (
        None, None, None, None)


def test_le_boot_rattrape_ce_que_la_PROD_a_ecrit_pendant_la_fenetre(live):
    """Le cas qui décide de tout. Pendant la fenêtre preprod→tag, la prod (v1.157.0)
    n'écrit QUE dans la projection. Le journal étant ce que le gate lit, sans reprise
    au boot on redemanderait ses CGU à quelqu'un qui vient de les accepter."""
    from oto_mcp.db import init_db, legal as db_legal
    from oto_mcp.db._conn import _connect

    sub = "u-fenetre-" + uuid.uuid4().hex[:6]
    with _connect() as conn:                      # la prod, telle quelle
        conn.execute(ECRITURE_V1_157_0, (sub, "cgv", "2.0"))
    assert _journal(sub) == [], "avant le boot suivant, le journal ne sait rien"

    init_db()                                     # le boot du tag de prod
    assert [l["version"] for l in _journal(sub)] == ["2.0"]
    assert db_legal.get_legal_acceptances(sub)["cgv"]["version"] == "2.0"


def test_la_recopie_est_idempotente(live):
    """Rejouée à chaque boot : elle ne doit ni dupliquer, ni rien réécrire."""
    from oto_mcp.db import init_db

    avant = _journal(ANCIEN_SUB)
    init_db()
    init_db()
    assert _journal(ANCIEN_SUB) == avant


def test_une_reacceptation_par_la_prod_entre_bien_au_boot_suivant(live):
    """L'anti-jointure porte sur (sub, doc, version, accepted_at) : une ligne
    RÉÉCRITE par la prod a un nouvel horodatage, donc elle entre — et l'ancienne
    reste, puisque le journal n'écrase pas."""
    from oto_mcp.db import init_db
    from oto_mcp.db._conn import _connect

    sub = "u-rebump-" + uuid.uuid4().hex[:6]
    with _connect() as conn:
        conn.execute(ECRITURE_V1_157_0, (sub, "dpa", "1.0"))
    init_db()
    with _connect() as conn:
        conn.execute(ECRITURE_V1_157_0, (sub, "dpa", "2.0"))
    init_db()

    assert [l["version"] for l in _journal(sub)] == ["1.0", "2.0"]


# ══ 3. l'écriture double, la lecture unique ══════════════════════════════════

def test_le_nouveau_chemin_ecrit_les_DEUX(live):
    from oto_mcp.db import legal as db_legal

    sub = "u-neuf-" + uuid.uuid4().hex[:6]
    db_legal.record_legal_acceptances(
        sub, [("terms", "3.0"), ("cgv", "2.0"), ("dpa", "2.0")],
        context="purchase", org_id=219, ip="203.0.113.7",
        user_agent="Mozilla/5.0 (X11; Linux x86_64) Firefox/141.0")

    journal = _journal(sub)
    assert len(journal) == 3, "un achat = trois documents = trois lignes de journal"
    for ligne in journal:
        assert ligne["context"] == "purchase" and ligne["org_id"] == 219
        assert ligne["ip"] == "203.0.113.7" and "Firefox" in ligne["user_agent"]
    # …et la projection est à jour, pour que la PROD voie l'acceptation elle aussi.
    assert [(p["doc_slug"], p["version"]) for p in _projection(sub)] == [
        ("cgv", "2.0"), ("dpa", "2.0"), ("terms", "3.0")]


def test_deux_acceptations_font_deux_lignes_de_journal_et_UNE_de_projection(live):
    """La différence entre une preuve et un état, mesurée sur les deux tables."""
    from oto_mcp.db import legal as db_legal

    sub = "u-suite-" + uuid.uuid4().hex[:6]
    db_legal.record_legal_acceptances(sub, [("cgv", "1.0")], context="access")
    db_legal.record_legal_acceptances(sub, [("cgv", "2.0")], context="purchase")

    assert [l["version"] for l in _journal(sub)] == ["1.0", "2.0"], (
        "l'acceptation de la 1.0 reste : c'est une preuve, pas un état")
    assert [p["version"] for p in _projection(sub)] == ["2.0"], (
        "la projection, elle, écrase — c'est bien pour ça qu'elle ne prouve rien")
    assert db_legal.get_legal_acceptances(sub)["cgv"]["version"] == "2.0"


def test_la_lecture_ne_regarde_QUE_le_journal(live):
    """Une projection qui dirait autre chose que le journal ne doit changer aucune
    réponse : c'est le journal qui fait foi. (On triche ici sur la projection, ce que
    seule une divergence de fenêtre pourrait produire en vrai.)"""
    from oto_mcp.db import legal as db_legal
    from oto_mcp.db._conn import _connect

    sub = "u-divergent-" + uuid.uuid4().hex[:6]
    db_legal.record_legal_acceptances(sub, [("terms", "3.0")], context="access")
    with _connect() as conn:
        conn.execute("UPDATE legal_acceptances SET version = '99.0' WHERE sub = %s",
                     (sub,))
    assert db_legal.get_legal_acceptances(sub)["terms"]["version"] == "3.0"


def test_le_departage_ne_tient_pas_qu_a_la_date(live):
    """`accepted_at` vaut `NOW()`, l'horloge de la TRANSACTION : deux acceptations
    d'un même document écrites dans la même transaction portent la MÊME date. Sans
    départage par `id`, la lecture rendrait l'une ou l'autre au hasard."""
    from oto_mcp.db import legal as db_legal
    from oto_mcp.db._conn import _connect

    sub = "u-exaequo-" + uuid.uuid4().hex[:6]
    with _connect() as conn:
        for version in ("1.0", "2.0"):
            conn.execute("INSERT INTO legal_acceptance_events (sub, doc_slug, version) "
                         "VALUES (%s, 'dpa', %s)", (sub, version))
    lignes = _journal(sub)
    assert lignes[0]["accepted_at"] == lignes[1]["accepted_at"], "même transaction"
    assert db_legal.get_legal_acceptances(sub)["dpa"]["version"] == "2.0"
