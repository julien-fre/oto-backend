"""Le modèle de l'agent EN BASE : le filtre du claim, la présence par famille.

Les deux vivent en SQL, et une doublure ne prouve ni la clause, ni la table, ni la
fenêtre. Patron de base éphémère repris de `test_runner_workers_db.py`.

⚠️ L'ORDRE des bancs compte : un claim de PLATEFORME (`org_id=None`) prend le
travail de n'importe quelle org. Les bancs de présence passent donc d'abord, sur
une file vide ; ceux du filtre ensuite, avec des claims scopés à leur org.
"""
from __future__ import annotations

import datetime
import os
import uuid

import pytest


@pytest.fixture(scope="module")
def live(pg_dsn):
    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    name = "oto_modele_" + uuid.uuid4().hex[:8]
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
        for cle, valeur in (("DATABASE_URL", avant_url),
                            ("OTO_MCP_MASTER_KEY", avant_key)):
            if valeur is None:
                os.environ.pop(cle, None)
            else:
                os.environ[cle] = valeur
        root.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        root.close()


# ── la présence par famille ───────────────────────────────────────────────────

def test_un_worker_de_plateforme_note_CHAQUE_famille_qu_il_sert(live):
    """Plusieurs processus partagent UN secret, donc une ligne de déclaration, et ne
    servent pas forcément la même famille. Une colonne garderait la dernière vue et
    effacerait l'autre à chaque sondage — d'où la table à part."""
    from oto_mcp import db
    assert db.runner_arme(9000)["families"] == [], "aucun sondage, aucune famille"

    db.claim_next_job(None, "worker:banc", lease_seconds=60, depot="anthropic")
    db.claim_next_job(None, "worker:banc", lease_seconds=60, depot="mistral")

    etat = db.runner_arme(9000)
    assert etat["armed"] is True
    assert etat["families"] == ["anthropic", "mistral"]


def test_un_depot_hors_catalogue_ne_se_note_pas(live):
    """`provider` est une chaîne libre : un dépôt que rien ne route n'a rien à
    promettre, et un écran qui l'afficherait comme « servi » mentirait."""
    from oto_mcp import db
    from oto_mcp.db._conn import _connect
    db.claim_next_job(None, "worker:banc-libre", lease_seconds=60, depot="folk")
    db.claim_next_job(None, "worker:banc-libre", lease_seconds=60)
    with _connect() as c:
        n = c.execute("SELECT count(*) AS n FROM runner_platform_depots "
                      "WHERE worker_sub = 'worker:banc-libre'").fetchone()["n"]
    assert n == 0


def test_une_famille_tue_depuis_la_fenetre_ne_se_sert_plus(live):
    from oto_mcp import db
    from oto_mcp.db._conn import _connect
    with _connect() as c:
        c.execute("UPDATE runner_platform_depots SET last_seen_at = NOW() - "
                  "make_interval(secs => %s) WHERE depot = 'mistral'",
                  (db.ARME_FENETRE_S + 60,))
        c.commit()
    assert db.runner_arme(9000)["families"] == ["anthropic"]


# ── le filtre du claim ────────────────────────────────────────────────────────

def _travail(org, famille=None):
    from oto_mcp import db
    charge = {"procedure": "p"}
    if famille:
        charge.update(model=f"un-modele-{famille}", model_family=famille)
    return db.enqueue_job(org, "start", payload=charge)["id"]


def test_un_worker_ne_prend_que_SA_famille_et_les_travaux_sans_famille(live):
    """LE banc du lot côté file. Le travail Mistral est le plus ancien : sans le
    filtre, le worker Anthropic le prendrait et l'appellerait chez Anthropic."""
    from oto_mcp import db
    mistral = _travail(9101, "mistral")
    libre = _travail(9101)

    pris = db.claim_next_job(9101, "w-anthropic", lease_seconds=60, depot="anthropic")
    assert pris and pris["id"] == libre, "sans famille = servi par n'importe qui"
    assert db.claim_next_job(9101, "w-anthropic", lease_seconds=60,
                             depot="anthropic") is None, "le Mistral reste à Mistral"

    pris = db.claim_next_job(9101, "w-mistral", lease_seconds=60, depot="mistral")
    assert pris and pris["id"] == mistral


def test_un_worker_qui_ne_nomme_AUCUN_depot_ne_prend_aucune_famille(live):
    """Un worker qui ne dit pas ce qu'il sert ne reçoit jamais un modèle qu'il ne
    saurait pas appeler — mais il sert toujours tout ce qui n'en porte pas."""
    from oto_mcp import db
    anthropic = _travail(9102, "anthropic")
    assert db.claim_next_job(9102, "w-muet", lease_seconds=60) is None
    assert db.claim_next_job(9102, "w-muet", lease_seconds=60, depot="") is None
    libre = _travail(9102)
    pris = db.claim_next_job(9102, "w-muet", lease_seconds=60)
    assert pris and pris["id"] == libre
    pris = db.claim_next_job(9102, "w-anthropic", lease_seconds=60, depot="anthropic")
    assert pris and pris["id"] == anthropic


# ── ce que la base garde du modèle ────────────────────────────────────────────

def test_un_declencheur_garde_son_modele_et_le_rend(live):
    from oto_mcp import db
    t = db.create_trigger(9103, "alexis", procedure="p", cron="5 6 * * *", tz="UTC",
                          next_due=datetime.datetime.now(datetime.timezone.utc),
                          tools=["a"], model="claude-opus-5")
    assert db.get_trigger(t["id"], 9103)["model"] == "claude-opus-5"
    assert db.due_triggers()[0]["model"] == "claude-opus-5", (
        "le tick lit `due_triggers` : sans la colonne, le modèle ne partirait jamais")
    db.update_trigger(t["id"], 9103, {"model": None})
    assert db.get_trigger(t["id"], 9103)["model"] is None


def test_un_continue_relit_le_modele_du_START_de_son_run(live):
    from oto_mcp import db
    from oto_mcp.db._conn import _connect
    # `runner_jobs.run_id` référence `runs` : un travail ne se lie qu'à un run qui existe.
    with _connect() as c:
        for run_id in ("run-banc", "run-sans-modele"):
            c.execute("INSERT INTO runs (run_id, sub, org_id, label) VALUES (%s, %s, %s, %s)",
                      (run_id, "alexis", 9104, "banc du modèle"))
        c.commit()
    db.enqueue_job(9104, "start", run_id="run-banc",
                   payload={"procedure": "p", "model": "claude-opus-5",
                            "model_family": "anthropic"})
    db.enqueue_job(9104, "start", run_id="run-sans-modele", payload={"procedure": "p"})
    assert db.modele_du_run("run-banc", 9104) == {
        "model": "claude-opus-5", "model_family": "anthropic"}
    assert db.modele_du_run("run-banc", 9105) == {}, "le run d'une AUTRE org ne se lit pas"
    assert db.modele_du_run("run-sans-modele", 9104) == {}


def test_un_continue_relit_l_effort_ET_le_plafond_du_START(live):
    """Relevé en revue le 14/09/2026 : la charge du `start` porte `max_output_tokens`, et
    la reprise ne relisait que `effort`. Le worker lève sur un effort qui raisonne sans
    plafond : chaque reprise d'un run Medium aurait échoué. Les deux voyagent ensemble,
    et le plafond revient ENTIER (`->>` rend du texte)."""
    from oto_mcp import db, runner_models
    from oto_mcp.db._conn import _connect
    with _connect() as c:
        c.execute("INSERT INTO runs (run_id, sub, org_id, label) VALUES (%s, %s, %s, %s)",
                  ("run-medium", "banc", 9106, "banc du plafond"))
        c.commit()
    db.enqueue_job(9106, "start", run_id="run-medium",
                   payload={"procedure": "p", **runner_models.charge("mistral-medium-2604")})
    assert db.modele_du_run("run-medium", 9106) == {
        "model": "mistral-medium-2604", "model_family": "mistral", "effort": "high",
        "max_output_tokens": 16000}
