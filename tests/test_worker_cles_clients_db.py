"""Le filtre « famille seule » EN BASE — ce qu'une doublure ne prouve pas.

Un worker qui ne tient aucune clé à lui ne doit JAMAIS réserver le travail d'un
agent posé sans modèle : les workers existants le servent sur leur modèle, et le
lui voler le ferait échouer faute de clé. La clause vit en SQL.

Patron de base éphémère repris de `test_modele_de_l_agent_db.py`. Tous les claims
sont scopés à leur org : un claim de plateforme prendrait le travail des autres bancs.
"""
from __future__ import annotations

import os
import uuid

import pytest


@pytest.fixture(scope="module")
def live(pg_dsn):
    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    name = "oto_cles_clients_" + uuid.uuid4().hex[:8]
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


def _travail(org, famille=None):
    from oto_mcp import db
    charge = {"procedure": "p"}
    if famille:
        charge.update(model=f"un-modele-{famille}", model_family=famille)
    return db.enqueue_job(org, "start", payload=charge)["id"]


def test_famille_seule_ne_prend_JAMAIS_un_travail_sans_famille(live):
    """⚠️ LE banc du lot côté file. Le travail sans famille est le plus ancien :
    sans le filtre, le worker Anthropic « clés clients » le prendrait en premier."""
    from oto_mcp import db
    libre = _travail(9301)
    anthropic = _travail(9301, "anthropic")

    pris = db.claim_next_job(9301, "w-cles-clients", lease_seconds=60,
                             depot="anthropic", famille_seule=True)
    assert pris and pris["id"] == anthropic, "il prend SA famille"
    assert db.claim_next_job(9301, "w-cles-clients", lease_seconds=60,
                             depot="anthropic", famille_seule=True) is None, (
        "le travail sans famille reste aux workers existants")

    pris = db.claim_next_job(9301, "w-mistral", lease_seconds=60, depot="mistral")
    assert pris and pris["id"] == libre, "et un worker ordinaire le sert toujours"


def test_famille_seule_ne_prend_pas_non_plus_une_AUTRE_famille(live):
    from oto_mcp import db
    _travail(9302, "mistral")
    assert db.claim_next_job(9302, "w-cles-clients", lease_seconds=60,
                             depot="anthropic", famille_seule=True) is None


def test_famille_seule_SANS_depot_ne_prend_rien(live):
    """Sans famille nommée et sans travaux sans famille, il n'y a rien à servir."""
    from oto_mcp import db
    _travail(9303)
    _travail(9303, "anthropic")
    assert db.claim_next_job(9303, "w-muet", lease_seconds=60, depot="",
                             famille_seule=True) is None


def test_sans_famille_seule_le_filtre_d_avant_est_intact(live):
    """Le bord opposé : les workers existants gardent leur comportement, à l'octet."""
    from oto_mcp import db
    libre = _travail(9304)
    pris = db.claim_next_job(9304, "w-anthropic", lease_seconds=60, depot="anthropic")
    assert pris and pris["id"] == libre
