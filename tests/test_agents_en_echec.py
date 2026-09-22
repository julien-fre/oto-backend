"""La lentille qui NOMME un agent déclenché qui meurt en silence.

Un agent événementiel casse sans rien dire : sa file se vide (les travaux morts
n'attendent plus), son écran le dit « actif », et son dernier déroulé peut être
vert. Trois travaux d'un même agent de production sont morts sur quatre jours,
neuf tentatives, et la panne s'est découverte en regardant pour autre chose —
personne n'avait été prévenu, parce que rien n'était chargé de prévenir.

Ce que ces bancs figent :
- ① un agent qui perd plusieurs travaux remonte, avec ses comptes ;
- ② le groupement est par agent ET PAR MOTIF — « trois fois la même panne » et
  « trois pannes différentes » ne se réparent pas pareil ;
- ③ un identifiant dans le message ne sépare pas deux fois le même motif ;
- ④ un seul échec ne réveille personne (le seuil) ;
- ⑤ un travail qui a fini par réussir n'est pas un agent en échec ;
- ⑥ la fenêtre EXCLUT ce qui est vieux — une alerte parle de maintenant ;
- ⑦ `org_id` scope, et ne fuit pas l'org d'à côté.

Contre un PostgreSQL jetable (`OTO_TEST_PG_DSN`).
"""
from __future__ import annotations

import os
import uuid

import pytest

WORKER = "worker-alerte"


@pytest.fixture(scope="module")
def live(pg_dsn):
    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    name = "oto_alerte_" + uuid.uuid4().hex[:8]
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


def _sql(requete: str, *args) -> None:
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        conn.execute(requete, args)


def _travail_mort(org: int, trigger_id: int, motif: str, *, label="pipeline",
                  il_y_a_jours: float = 0) -> int:
    """Un travail conclu `failed` — par le vrai chemin, puis vieilli si besoin."""
    from oto_mcp import db
    j = db.enqueue_job(org, "start", max_attempts=1,
                       payload={"procedure": "p", "trigger_id": trigger_id,
                                "label": label})
    claime = db.claim_next_job(org, WORKER, lease_seconds=60)
    assert claime and claime["id"] == j["id"]
    out = db.complete_job(j["id"], WORKER, False, error=motif)
    assert out["status"] == "failed", out
    if il_y_a_jours:
        _sql("UPDATE runner_jobs SET finished_at = NOW() - make_interval(days => %s) "
             "WHERE id = %s", il_y_a_jours, j["id"])
    return int(j["id"])


# ── ① un agent qui perd plusieurs travaux remonte ─────────────────────────────

def test_un_agent_qui_perd_plusieurs_travaux_remonte_avec_ses_comptes(live):
    from oto_mcp import db
    org, trig = 9401, 7401
    for _ in range(3):
        _travail_mort(org, trig, "fin_anormale (max_tokens)")

    (row,) = db.agents_en_echec(1, org_id=org)
    assert row["trigger_id"] == trig and row["label"] == "pipeline", row
    assert row["travaux"] == 3, f"trois travaux perdus, comptés — {row!r}"
    assert row["tentatives"] == 3, row
    assert row["depuis"] and row["dernier"], (
        "« depuis quand » et « est-ce encore en cours » sont deux questions : "
        "une seule date les confondrait")


# ── ② groupé par MOTIF, pas seulement par agent ───────────────────────────────

def test_deux_motifs_font_deux_lignes_et_non_un_compte_de_cinq(live):
    """LE banc. « Trois fois la même panne » est un défaut à réparer ; « trois
    pannes différentes » est un agent mal réglé. Un compte unique les confondrait,
    et enverrait chercher un bug là où il n'y en a pas."""
    from oto_mcp import db
    org, trig = 9402, 7402
    for _ in range(3):
        _travail_mort(org, trig, "fin_anormale (max_tokens)")
    for _ in range(2):
        _travail_mort(org, trig, "invalid x-api-key")

    lignes = db.agents_en_echec(1, org_id=org)
    assert len(lignes) == 2, f"deux motifs, deux lignes — {lignes!r}"
    par_motif = {l["motif"]: l["travaux"] for l in lignes}
    assert par_motif == {"fin_anormale (max_tokens)": 3, "invalid x-api-key": 2}
    assert lignes[0]["travaux"] == 3, "le plus fréquent d'abord"


# ── ③ un identifiant ne sépare pas deux fois le même motif ────────────────────

def test_un_identifiant_dans_le_message_ne_scinde_pas_le_motif(live):
    """Deux messages qui ne diffèrent que par un numéro de travail sont LA MÊME
    panne. Groupés sur le message entier, ils feraient deux lignes de un — donc
    deux lignes sous le seuil, donc silence."""
    from oto_mcp import db
    org, trig = 9403, 7403
    for n in (23324, 23325, 23326):
        _travail_mort(org, trig,
                      f"le travail {n} est arrivé sans instruction de départ. "
                      f"Le worker en exécute une, il n'en compose pas.")

    lignes = db.agents_en_echec(1, org_id=org)
    assert len(lignes) == 1, f"une seule panne, une seule ligne — {lignes!r}"
    assert lignes[0]["travaux"] == 3
    assert "N" in lignes[0]["motif"] and "23324" not in lignes[0]["motif"], (
        f"le motif rendu est une CLÉ, l'identifiant y est normalisé — "
        f"{lignes[0]['motif']!r}")


def test_un_code_HTTP_n_est_PAS_normalise_et_garde_ses_pannes_distinctes(live):
    """⚠️ La frontière à quatre chiffres, et pourquoi elle n'est pas à trois.

    `429` (throttle, rien à réparer) et `400` (contrat cassé, tout à réparer)
    sont deux pannes. Les fondre en « HTTP N » ferait lire l'une pour l'autre —
    exactement l'erreur que le groupement par motif existe pour éviter."""
    from oto_mcp import db
    org, trig = 9409, 7409
    for _ in range(2):
        _travail_mort(org, trig, "Error code: 429 rate_limit_error")
    for _ in range(2):
        _travail_mort(org, trig, "Error code: 400 invalid_request_error")

    motifs = {l["motif"] for l in db.agents_en_echec(1, org_id=org)}
    assert motifs == {"Error code: 429 rate_limit_error",
                      "Error code: 400 invalid_request_error"}, motifs


# ── ④ un échec isolé ne réveille personne ─────────────────────────────────────

def test_un_seul_echec_reste_sous_le_seuil(live):
    """Un travail qui rate une fois est la vie normale d'un agent. Alerter dessus
    ferait de l'alerte un bruit, et le bruit se filtre — y compris le jour où il
    ne l'est plus."""
    from oto_mcp import db
    org, trig = 9404, 7404
    _travail_mort(org, trig, "fin_anormale (pause_turn)")
    assert db.agents_en_echec(1, org_id=org) == []
    # Le seuil se DIT, il ne se devine pas : à 1, la même donnée remonte.
    assert len(db.agents_en_echec(1, org_id=org, seuil=1)) == 1


# ── ⑤ un travail qui finit par réussir n'est pas un agent en échec ────────────

def test_un_travail_qui_a_fini_par_reussir_n_alerte_pas(live):
    """La lentille lit le STATUT final, pas le nombre de tentatives : un travail
    qui se rattrape au deuxième essai a fonctionné."""
    from oto_mcp import db
    org, trig = 9405, 7405
    j = db.enqueue_job(org, "start", max_attempts=3,
                       payload={"procedure": "p", "trigger_id": trig})
    db.claim_next_job(org, WORKER, lease_seconds=60)
    db.complete_job(j["id"], WORKER, False, error="fin_anormale (pause_turn)")
    _sql("UPDATE runner_jobs SET due_at = NOW() WHERE id = %s", j["id"])
    db.claim_next_job(org, WORKER, lease_seconds=60)
    db.complete_job(j["id"], WORKER, True)

    _travail_mort(org, trig, "fin_anormale (pause_turn)")
    assert db.agents_en_echec(1, org_id=org, seuil=2) == [], (
        "un seul travail est VRAIMENT mort ; le rattrapé ne compte pas")


# ── ⑥ la fenêtre exclut le vieux ──────────────────────────────────────────────

def test_une_panne_d_il_y_a_une_semaine_ne_remonte_pas_aujourd_hui(live):
    """Une alerte parle de MAINTENANT. Mélanger une panne réparée la semaine
    dernière avec celle de ce matin, c'est la faire lire comme non réparée."""
    from oto_mcp import db
    org, trig = 9406, 7406
    for _ in range(3):
        _travail_mort(org, trig, "invalid x-api-key", il_y_a_jours=7)

    assert db.agents_en_echec(1, org_id=org) == []
    assert len(db.agents_en_echec(30, org_id=org)) == 1, (
        "la fenêtre est un paramètre, pas un oubli : à 30 jours la panne est là")


# ── ⑦ le scope d'org ──────────────────────────────────────────────────────────

def test_la_lentille_d_une_org_ne_montre_pas_l_org_d_a_cote(live):
    from oto_mcp import db
    a, b = 9407, 9408
    for _ in range(2):
        _travail_mort(a, 7407, "fin_anormale (max_tokens)")
    for _ in range(2):
        _travail_mort(b, 7408, "fin_anormale (max_tokens)")

    (seule,) = db.agents_en_echec(1, org_id=a)
    assert seule["org_id"] == a and seule["trigger_id"] == 7407, seule
    # Sans `org_id`, c'est la lentille PLATEFORME : les deux orgs y sont.
    orgs = {l["org_id"] for l in db.agents_en_echec(1)}
    assert {a, b} <= orgs, orgs
