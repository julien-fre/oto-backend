"""Ce qu'un écran de surveillance lit d'un travail : le BAIL, et les POSTES DE GARDE.

Deux manques, la même famille — une donnée que la plateforme détient déjà et qui ne
sort pas :

**Le bail.** `lease_until` n'était rendu que par `op=claim`, c'est-à-dire au seul
worker qui vient de prendre le job. `op=list` et `op=get` — les deux verbes de
surveillance — ne le sélectionnaient pas. Un écran ne pouvait donc pas dire « ce bail
a expiré » : il devait le DEVINER à un seuil sur l'ancienneté, et un seuil dérivé
range dans la même case un travail lent et un travail mort.

**Les postes de garde.** Le harnais du runner écrit dans `result` ce qu'il a dû
réparer sur la ligne travaillée. `extra=allow` les laissait passer jusqu'au client —
mais *servi* n'est pas *déclaré* : leur forme n'était garantie nulle part, et un
client typé (l'OpenAPI du dashboard) ne les voyait pas du tout. Le cas qui MORD est
`valeurs_cliente_detruites` : il a **trois** états, et son `null` veut dire « la
ligne n'a pas pu être identifiée, la garde n'a pas tourné » — le lire comme « aucune
destruction » afficherait un travail propre là où personne n'a regardé.

Prouvé sur le chemin réel : la capacité `runner.jobs` telle que la route REST
l'appelle, contre un PostgreSQL jetable, plus la forme au contrat servi (l'OpenAPI,
puisque `runner.jobs` est REST-only et n'a pas de face MCP).
"""
from __future__ import annotations

import uuid

import pytest

from oto_mcp.capabilities import runner_jobs as RJ
from oto_mcp.capabilities._types import ResolvedCtx

SUB = "worker-travail-servi"
ORG = 6642


# ── le contrat servi : ce que l'OpenAPI nomme ─────────────────────────────────

def test_le_bail_est_declare_sur_le_job():
    champ = RJ.Job.model_fields["lease_until"]
    assert champ.description, \
        "un client qui lit une date de bail doit savoir contre quoi la comparer"
    assert "status" in champ.description, \
        "le sens de `lease_until` dépend du statut : le contrat doit le dire"


@pytest.mark.parametrize("nom", ["valeurs_cliente_reparees",
                                 "contacts_fabriques_retires",
                                 "valeurs_cliente_detruites"])
def test_les_postes_de_garde_sont_declares(nom):
    """`extra=allow` sert, il ne déclare pas : sans ces trois lignes, l'OpenAPI ne
    nomme aucun d'eux et les types générés du dashboard ne les voient pas."""
    assert nom in RJ.JobResult.model_fields
    assert RJ.JobResult.model_fields[nom].description


def test_le_null_de_la_destruction_est_ecrit_au_contrat():
    """Le seul des trois dont `null` n'est pas « rien » : c'est « pas mesuré ».

    La confusion est l'incident qu'on prévient, pas une subtilité de doc — un `[]`
    et un `null` s'affichent pareil sur un écran qui compte, et l'un des deux dit
    que la garde n'a jamais tourné."""
    d = RJ.JobResult.model_fields["valeurs_cliente_detruites"].description
    assert "NOT MEASURED" in d, "le contrat doit nommer le troisième état"


def test_le_schema_reste_ouvert():
    """Le worker déclare plus que le socle (coût d'entrée/sortie, hors-schéma, faux
    départ…). Nommer trois champs ne doit pas fermer la porte aux autres."""
    assert RJ.JobResult.model_config.get("extra") == "allow"


def test_les_trois_champs_sortent_dans_l_openapi():
    """La preuve au bout de la chaîne : `runner.jobs` n'a pas de face MCP, donc son
    seul contrat publié est l'OpenAPI. `JobResult` y est un modèle IMBRIQUÉ, donc un
    `$defs` → `#/components/schemas/JobResult`."""
    from oto_mcp import openapi
    doc = openapi.build()
    props = doc["components"]["schemas"]["JobResult"]["properties"]
    for nom in ("valeurs_cliente_reparees", "contacts_fabriques_retires",
                "valeurs_cliente_detruites"):
        assert nom in props, f"{nom} servi mais absent du contrat publié"
    assert "lease_until" in doc["components"]["schemas"]["Job"]["properties"]


# ── la couture servie, contre un vrai PostgreSQL ──────────────────────────────

@pytest.fixture(scope="module")
def live(pg_dsn):
    import os

    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    name = "oto_travail_" + uuid.uuid4().hex[:8]
    root = psycopg.connect(pg_dsn, autocommit=True)
    root.execute(f'CREATE DATABASE "{name}"')
    dsn = pg_dsn.rsplit("/", 1)[0] + "/" + name

    prev_url, prev_pool = os.environ.get("DATABASE_URL"), dbconn._pool
    os.environ["DATABASE_URL"] = dsn
    dbconn._pool = None
    try:
        from oto_mcp.db import init_db
        init_db()
        # ⚠️ Le porteur d'un travail doit EXISTER et être membre de son org — c'est
        # le cas en production (l'enfilage est réservé aux membres), et depuis la
        # délégation le serveur le VÉRIFIE à la réservation. Un harnais qui ne le
        # modélisait pas décrivait un état impossible, et il a rougi le jour où
        # quelque chose l'a enfin lu.
        from oto_mcp.db._conn import _connect
        with _connect() as _c:
            _c.execute("INSERT INTO users (sub) VALUES (%s) ON CONFLICT DO NOTHING",
                       (SUB,))
            _c.execute("INSERT INTO orgs (id, name) VALUES (%s, %s) "
                       "ON CONFLICT DO NOTHING", (ORG, "org du banc"))
            _c.execute("INSERT INTO org_members (org_id, sub, org_role) "
                       "VALUES (%s, %s, 'org_admin') ON CONFLICT DO NOTHING",
                       (ORG, SUB))
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


def _jobs(**kw) -> dict:
    """La capacité telle que la route l'appelle — le worker porte un jeton d'org."""
    return RJ._jobs(ResolvedCtx(sub=SUB, org_id=ORG), RJ.JobsInput(**kw))


def _job_claime() -> dict:
    _jobs(op="enqueue", kind="start", payload={"procedure": "p-garde"})
    job = _jobs(op="claim")["job"]
    assert job, "la file portait un job : le claim le rend"
    return job


def _dans_la_liste(job_id: int) -> dict:
    return next(j for j in _jobs(op="list")["jobs"] if j["id"] == job_id)


def test_la_surveillance_voit_le_bail_du_travail_en_cours(live):
    """Le claim le rendait déjà ; list et get le rendent désormais, et c'est LE MÊME.

    Deux projections qui divergeraient sur la date du bail ne se rattraperaient
    jamais : l'écran croirait un travail mort que le worker tient encore."""
    job = _job_claime()
    bail = job["lease_until"]
    assert bail, "op=claim pose un bail"

    assert _jobs(op="get", job_id=job["id"])["job"]["lease_until"] == bail
    assert _dans_la_liste(job["id"])["lease_until"] == bail


def test_un_job_jamais_pris_na_pas_de_bail(live):
    """`null`, et il le DIT : sans le champ, l'écran ne distinguait pas « en attente »
    de « pris par un worker qui ne répond plus »."""
    _jobs(op="enqueue", kind="start", payload={"procedure": "p-attente"})
    en_attente = [j for j in _jobs(op="list", status="pending")["jobs"]]
    assert en_attente and all(j["lease_until"] is None for j in en_attente)


def test_prolonger_le_bail_se_voit_a_la_surveillance(live):
    """Le heartbeat du worker doit être LISIBLE : c'est le signe qu'un travail long
    est vivant. Un bail figé pendant qu'il tourne se lirait « mort »."""
    job = _job_claime()
    avant = _dans_la_liste(job["id"])["lease_until"]
    _jobs(op="extend", job_id=job["id"], lease_seconds=3600)
    apres = _dans_la_liste(job["id"])["lease_until"]
    assert apres > avant, "le heartbeat repousse la date, la surveillance la voit"


def test_les_postes_de_garde_traversent_jusqua_la_surveillance(live):
    """Ce que le harnais déclare à la conclusion arrive intact à list et à get —
    `[]` reste `[]`, `null` reste `null`. Le contrat vaut ce que la traversée vaut."""
    job = _job_claime()
    _jobs(op="complete", job_id=job["id"], ok=True,
          result={"usage_tokens": 12_000, "stopped": "end_turn",
                  "valeurs_cliente_reparees": ["effectif", "ca"],
                  "contacts_fabriques_retires": ["Jean Dupont"],
                  "valeurs_cliente_detruites": None})

    for source, j in (("get", _jobs(op="get", job_id=job["id"])["job"]),
                      ("list", _dans_la_liste(job["id"]))):
        r = j["result"]
        assert r["valeurs_cliente_reparees"] == ["effectif", "ca"], source
        assert r["contacts_fabriques_retires"] == ["Jean Dupont"], source
        assert "valeurs_cliente_detruites" in r, \
            f"{source} : la clé qui disparaît se lit « aucune destruction »"
        assert r["valeurs_cliente_detruites"] is None, source


def test_non_mesure_et_aucune_destruction_ne_se_confondent_pas(live):
    """Le test qui justifie tout le lot n°3 : deux travaux, deux verdicts opposés,
    et la seule chose qui les sépare est `[]` contre `null`."""
    mesure = _job_claime()
    _jobs(op="complete", job_id=mesure["id"], ok=True,
          result={"valeurs_cliente_detruites": []})
    aveugle = _job_claime()
    _jobs(op="complete", job_id=aveugle["id"], ok=True,
          result={"valeurs_cliente_detruites": None})

    vu = _jobs(op="get", job_id=mesure["id"])["job"]["result"]
    pas_vu = _jobs(op="get", job_id=aveugle["id"])["job"]["result"]
    assert vu["valeurs_cliente_detruites"] == [], "la garde a tourné, rien à signaler"
    assert pas_vu["valeurs_cliente_detruites"] is None, \
        "la garde n'a PAS tourné — la ligne n'a pas pu être identifiée"
