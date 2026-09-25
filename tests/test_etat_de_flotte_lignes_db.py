"""L'état d'une automatisation dit ce que sont devenues ses LIGNES, en SQL RÉEL (oto#77).

Le cas mesuré, rejoué : une automatisation d'essai à trois lignes a rendu onze
travaux `done`, zéro `failed`, zéro `abandoned` — et, relues au tableau, une ligne
enrichie et deux lignes abandonnées après trois réservations sans écriture. Les
compteurs de travaux ne mentaient pas sur les travaux : ils ne parlaient pas des
lignes, et rien d'autre n'en parlait.

Base éphémère, vraies réservations (`datastore_claim_next`), vraie libération par run
(`datastore_release_by_run`, qui verse la ligne à bout dans l'état d'abandon), vrais
faits `run_start`/`run_finish` au journal, et la capacité `op=state` elle-même.
"""
from __future__ import annotations

import json
import os
import uuid

import pytest

WORKER = "worker-lignes"
EN_FILE = [{"field": "statut", "op": "eq", "value": "a_enrichir"}]
SCHEMA = {"fields": [
    {"key": "siren", "role": "key"},
    {"key": "lot"},
    {"key": "statut", "role": "status",
     "lifecycle": {"states": ["a_enrichir", "enrichi", "echec", "ecarte"],
                   "transitions": {"a_enrichir": ["enrichi", "echec", "ecarte"]},
                   "terminal": ["enrichi", "echec", "ecarte"],
                   "max_claims": 3, "abandon_state": "echec"}},
]}
MOTIF = "abandonnée après 3 réservations sans écriture, plafond 3"


@pytest.fixture(scope="module")
def live(pg_dsn):
    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    name = "oto_lignes_flotte_" + uuid.uuid4().hex[:8]
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


@pytest.fixture(autouse=True)
def _socle(monkeypatch):
    from oto_mcp.capabilities import runner_fleets as RF
    monkeypatch.setattr("oto_mcp.db.connector_settings.get_connector_setting",
                        lambda *a, **k: None)


def _tableau(sub, *, schema=SCHEMA) -> tuple[int, str]:
    """Trois lignes du lot d'essai à enrichir, et une ligne d'un AUTRE lot, déjà en
    échec — hors du périmètre de l'automatisation."""
    from oto_mcp import db
    from oto_mcp.db._conn import _connect
    nom = "lot-" + uuid.uuid4().hex[:6]
    ns_id = db.create_datastore("user", sub, nom)
    if schema is not None:
        db.set_datastore_schema(ns_id, schema)
    with _connect() as conn:
        for i, (lot, statut) in enumerate((("banc", "a_enrichir"), ("banc", "a_enrichir"),
                                           ("banc", "a_enrichir"), ("autre", "echec"))):
            conn.execute("INSERT INTO datastore_rows (ns_id, row_id, data) VALUES (%s, %s, %s)",
                         (ns_id, f"ligne-{i}",
                          json.dumps({"siren": str(i), "lot": lot, "statut": statut})))
    return ns_id, nom


@pytest.fixture
def campagne(live):
    from oto_mcp import db, org_store
    sub = "lignes_" + uuid.uuid4().hex[:8]
    db.upsert_user(sub)
    org = org_store.create_org("org_" + uuid.uuid4().hex[:8], created_by=sub)
    ns_id, nom = _tableau(sub)
    f = db.create_fleet(org, sub, label="enrichissement", procedure="p",
                        tools=["data_claim_next", "data_write"], namespace=nom,
                        row_filter={"statut": "a_enrichir", "lot": "banc"})
    return {"org": org, "sub": sub, "id": int(f["id"]), "ns_id": ns_id, "nom": nom}


def _run(c) -> str:
    from oto_mcp import db
    run_id = uuid.uuid4().hex
    db.insert_run(run_id, sub=c["sub"], org_id=c["org"], label="exécution")
    db.insert_tool_call({"tool": "run_start", "sub": c["sub"], "org_id": c["org"],
                         "run_id": run_id, "args": {"label": "exécution"}, "ok": True})
    return run_id


def _clore(c, run_id: str, issue: str) -> None:
    from oto_mcp import db
    db.finish_run(run_id, issue, sub=c["sub"])
    db.insert_tool_call({"tool": "run_finish", "sub": c["sub"], "org_id": c["org"],
                         "run_id": run_id, "args": {"run_id": run_id, "outcome": issue},
                         "ok": True})


def _travail(c, *, ecrit: bool, issue: str) -> None:
    """Un travail de l'automatisation, joué comme le poste le joue : un run, une
    réservation, l'écriture ou non, `run_finish`, la libération par run, et la
    conclusion du travail — `done`, car il s'est arrêté de lui-même."""
    from oto_mcp import db
    from oto_mcp.db._conn import _connect
    run_id = _run(c)
    ligne = db.datastore_claim_next(c["ns_id"], worker="w", run_id=run_id, filters=EN_FILE)
    if ligne is not None and ecrit:
        with _connect() as conn:
            conn.execute("UPDATE datastore_rows SET claims = 0, "
                         "data = jsonb_set(data, '{statut}', '\"enrichi\"') "
                         "WHERE ns_id = %s AND row_id = %s", (c["ns_id"], ligne["row_id"]))
    _clore(c, run_id, issue)
    db.datastore_release_by_run(run_id)
    j = db.enqueue_job(c["org"], "start", payload={"procedure": "p"},
                       max_attempts=1, fleet_id=c["id"])
    pris = db.claim_next_job(c["org"], WORKER, lease_seconds=60)
    assert pris and pris["id"] == j["id"]
    assert db.complete_job(int(j["id"]), WORKER, True, run_id=run_id,
                           result={"tool_counts": {"data_claim_next": 1}})


def _etat(c) -> dict:
    from oto_mcp.capabilities import runner_fleets as RF
    from oto_mcp.capabilities._types import ResolvedCtx
    rendu = RF._fleets(ResolvedCtx(sub=c["sub"], org_id=c["org"]),
                       RF.FleetInput(op="state", fleet_id=c["id"]))
    # Le contrat servi, pas seulement le dict : un champ hors modèle ne se lirait pas.
    return RF.FleetOut.model_validate(rendu).state.model_dump()


def _le_cas_mesure(c) -> None:
    """Onze travaux : une ligne enrichie, deux abandonnées à la troisième réservation
    sans écriture, puis la file vide."""
    _travail(c, ecrit=True, issue="done")
    for _ in range(6):
        _travail(c, ecrit=False, issue="failed")
    for _ in range(4):
        _travail(c, ecrit=False, issue="done")


# ══ le cas mesuré ══════════════════════════════════════════════════════════════

def test_onze_travaux_done_ne_cachent_plus_deux_lignes_abandonnees_sur_trois(campagne):
    _le_cas_mesure(campagne)

    e = _etat(campagne)

    assert (e["jobs_total"], e["done"], e["failed"], e["abandoned"]) == (11, 11, 0, 0), (
        "les compteurs de TRAVAUX restent ce qu'ils sont — c'est ce qui trompait")
    lignes = e["rows"]
    assert e["rows_unavailable"] is None
    assert (lignes["concluded"], lignes["abandoned"], lignes["open"]) == (1, 2, 0)
    assert lignes["by_status"] == {"enrichi": 1, "echec": 2}, (
        "la ligne d'un autre lot, déjà en échec, n'est pas celle de l'automatisation")
    assert lignes["abandon_reasons"] == [{"reason": MOTIF, "rows": 2}]
    assert (lignes["scope"], lignes["perimeter"], lignes["status_column"]) == (
        "perimeter", {"lot": "banc"}, "statut")
    assert (lignes["total"], lignes["abandon_state"], lignes["terminal_states"]) == (
        3, "echec", ["ecarte", "echec", "enrichi"])


def test_l_issue_de_chaque_execution_se_lit_au_journal(campagne):
    _le_cas_mesure(campagne)

    assert _etat(campagne)["runs_by_outcome"] == {"done": 5, "failed": 6}


def test_une_execution_ouverte_ou_partielle_se_dit(campagne):
    from oto_mcp import db
    ouvert, partiel, sans_ouverture = _run(campagne), _run(campagne), uuid.uuid4().hex
    _clore(campagne, partiel, "partial")
    db.insert_run(sans_ouverture, sub=campagne["sub"], org_id=campagne["org"], label="x")
    for run_id in (ouvert, partiel, sans_ouverture):
        j = db.enqueue_job(campagne["org"], "start", payload={"procedure": "p"},
                           max_attempts=1, fleet_id=campagne["id"])
        assert db.claim_next_job(campagne["org"], WORKER, lease_seconds=60)
        assert db.complete_job(int(j["id"]), WORKER, True, run_id=run_id, result={})

    assert _etat(campagne)["runs_by_outcome"] == {"open": 1, "partial": 1, "unknown": 1}


# ══ ce que la ventilation ne sait pas attribuer, elle le dit ═══════════════════

def test_un_filtre_qui_ne_borne_que_le_statut_ventile_tout_le_tableau_et_le_DIT(live):
    from oto_mcp import db, org_store
    sub = "lignes_" + uuid.uuid4().hex[:8]
    db.upsert_user(sub)
    org = org_store.create_org("org_" + uuid.uuid4().hex[:8], created_by=sub)
    _, nom = _tableau(sub)
    f = db.create_fleet(org, sub, label="tout", procedure="p", tools=["data_claim_next"],
                        namespace=nom, row_filter={"statut": "a_enrichir"})

    lignes = _etat({"sub": sub, "org": org, "id": int(f["id"])})["rows"]

    assert (lignes["scope"], lignes["perimeter"]) == ("table", {})
    assert lignes["by_status"] == {"a_enrichir": 3, "echec": 1}
    assert (lignes["abandoned"], lignes["open"]) == (1, 3)


def test_une_ligne_versee_en_echec_par_une_ecriture_n_a_pas_de_motif_de_plateforme(campagne):
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        conn.execute("UPDATE datastore_rows SET data = jsonb_set(data, '{statut}', "
                     "'\"echec\"') WHERE ns_id = %s AND row_id = 'ligne-0'",
                     (campagne["ns_id"],))

    lignes = _etat(campagne)["rows"]

    assert (lignes["abandoned"], lignes["open"]) == (1, 2)
    assert lignes["abandon_reasons"] == [{"reason": None, "rows": 1}]


def test_une_valeur_en_couches_se_ventile_par_sa_valeur(campagne):
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        conn.execute("UPDATE datastore_rows SET data = jsonb_set(data, '{statut}', "
                     "'{\"valeur\": \"enrichi\", \"origine\": \"system\"}') "
                     "WHERE ns_id = %s AND row_id = 'ligne-0'", (campagne["ns_id"],))

    assert _etat(campagne)["rows"]["by_status"] == {"enrichi": 1, "a_enrichir": 2}


@pytest.mark.parametrize("cas,attendu", [
    ("sans_tableau", "no_table"),
    ("tableau_introuvable", "table_not_found"),
    ("sans_colonne_de_statut", "no_status_column"),
])
def test_sans_ventilation_possible_l_etat_dit_pourquoi(live, cas, attendu):
    from oto_mcp import db, org_store
    sub = "lignes_" + uuid.uuid4().hex[:8]
    db.upsert_user(sub)
    org = org_store.create_org("org_" + uuid.uuid4().hex[:8], created_by=sub)
    nom = {"sans_tableau": None, "tableau_introuvable": "nulle-part"}.get(cas)
    if cas == "sans_colonne_de_statut":
        _, nom = _tableau(sub, schema={"fields": [{"key": "statut"}]})
    f = db.create_fleet(org, sub, label="x", procedure="p", tools=["data_claim_next"],
                        namespace=nom, row_filter={"lot": "banc"} if nom else None)

    e = _etat({"sub": sub, "org": org, "id": int(f["id"])})

    assert (e["rows"], e["rows_unavailable"]) == (None, attendu)
