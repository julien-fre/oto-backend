"""La garde qui manquait : le fragment `usage` DOIT s'appliquer SEUL sur une base
vierge — c'est exactement ce que fait `test_runner_trigger_sans_worker.py`
(`fragment_usage.USAGE` + `fragment_runs.RUNS`, sans `users`), et c'est ce qui a
rougi en CI le 15/09/2026 : `signal_digest_optouts` (oto#150) avait été posée dans
`db/schema/usage.py` avec `REFERENCES users(sub)`, alors que `users` vit dans un
autre fragment — `UndefinedTable: relation "users" does not exist`.

`outreach_optouts` (même forme, même `REFERENCES users(sub)`) n'a jamais fait
rougir aucun banc : rien ne mounte le fragment `outreach` seul. Ce test ne prouve
pas que `outreach` est sûr — il prouve que `usage`, LUI, l'est, parce que c'est le
seul fragment que la suite mounte isolément. Le correctif a déplacé la table dans
`outreach.py`, « auprès de sa sœur » `outreach_optouts` ; ce test est le filet qui
aurait rougi AVANT la CI si un futur ajout au fragment `usage` répétait l'erreur.
"""
from __future__ import annotations

import uuid

import pytest


def test_le_fragment_usage_s_applique_seul_sur_une_base_vierge(pg_dsn):
    """Aucune FK du fragment `usage` ne doit sortir de lui-même : le jouer seul,
    sur une base qui ne porte STRICTEMENT que lui, doit réussir sans erreur."""
    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db.schema import usage as fragment_usage

    nom = "oto_usage_seul_" + uuid.uuid4().hex[:8]
    root = psycopg.connect(pg_dsn, autocommit=True)
    root.execute(f'CREATE DATABASE "{nom}"')
    dsn = pg_dsn.rsplit("/", 1)[0] + "/" + nom
    try:
        conn = psycopg.connect(dsn, autocommit=True)
        try:
            conn.execute(fragment_usage.USAGE)
        finally:
            conn.close()
    finally:
        root.execute(f'DROP DATABASE IF EXISTS "{nom}" WITH (FORCE)')
        root.close()
