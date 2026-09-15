"""La purge rétroactive des jetons déjà écrits — contre un vrai PostgreSQL (#558).

Ce que le masquage à l'écriture ne peut pas faire : les lignes posées AVANT lui
portent leurs jetons en clair sur toute la fenêtre de rétention. Le seul instrument
qui prouve quoi que ce soit ici est la base : les prédicats de la passe sont du SQL
(`LIKE` avec exclusion des routes plus spécifiques).

⚠️ Jusqu'au 15/09/2026, ce banc prouvait le piège du préfixe générique/spécifique sur
`/api/invitations/{token}` VS `/api/invitations/code/{code}` — la passe générique
`/api/invitations/` aurait écrasé le travail de la passe spécifique
`/api/invitations/code/` et fait perdre son nom à la route. Cette paire a disparu avec
le code court d'invitation (oto-backend#560, retrait entier du mécanisme) : à date,
aucune autre paire de routes secrètes déclarées ne se chevauche dans la table servie
(vérifié : `upload`, `public/docs`, `invitations`, `p/d`, `o/u`, `o/d` ont chacune un
préfixe distinct). Le banc documente donc désormais la propriété plus simple qui
reste réellement exercée : chaque route déclarée est purgée sans toucher au nom d'une
AUTRE route."""
from __future__ import annotations

import pytest


@pytest.fixture(scope="module")
def base(pg_dsn):
    """Une base JETABLE À NOUS, bootée par le vrai chemin de démarrage.

    Pas celle du conteneur partagé : `pg_dsn` est session-scopé, et y laisser un
    boot complet (~67 tables et leurs FK) fait rougir des tests d'autres fichiers
    qui recréent deux tables autonomes. Même recette que `test_boot_order_replay`.
    """
    import os
    import uuid

    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    nom = "oto_purge_" + uuid.uuid4().hex[:8]
    root = psycopg.connect(pg_dsn, autocommit=True)
    root.execute(f'CREATE DATABASE "{nom}"')
    dsn = pg_dsn.rsplit("/", 1)[0] + "/" + nom
    url_avant, pool_avant = os.environ.get("DATABASE_URL"), dbconn._pool
    os.environ["DATABASE_URL"] = dsn
    dbconn._pool = None
    try:
        from oto_mcp.db import init_db
        init_db()
        yield dbconn
    finally:
        if dbconn._pool is not None:
            dbconn._pool.close()
        dbconn._pool = pool_avant
        if url_avant is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = url_avant
        root.execute(f'DROP DATABASE IF EXISTS "{nom}" WITH (FORCE)')
        root.close()


JETON = "eyJ0eXAiOiJ1cGxvYWQiLCJqdGkiOiJhYmMifQ.c2lnbmF0dXJl"
INVITE = "inv_Zm9vYmFyQmF6UXV1eA"


@pytest.fixture
def journal(base):
    """Le journal tel qu'il est aujourd'hui en base : des lignes d'AVANT le masquage."""
    with base._connect() as c:
        c.execute("TRUNCATE tool_calls")
        for kind, tool, args in [
            ("rest", f"PUT /api/upload/{JETON}", None),
            ("rest", f"GET /api/upload/{JETON}", None),
            ("rest", f"GET /api/public/docs/{INVITE}", None),
            ("rest", f"GET /api/invitations/{INVITE}", None),
            ("rest", "POST /api/orgs/:id/members", None),      # rien à réparer
            ("rest", "PUT /api/upload/:token", None),          # déjà réparée
            ("mcp", "oto_org", '{"op":"accept_invite","token":"%s"}' % INVITE),
            ("mcp", "oto_org", '{"op":"invite","email":"a@b.c"}'),  # rien à masquer
        ]:
            c.execute(
                "INSERT INTO tool_calls (kind, tool, args) VALUES (%s, %s, %s::jsonb)",
                (kind, tool, args))
    return base


def _tools(base):
    with base._connect() as c:
        return sorted(r["tool"] for r in
                      c.execute("SELECT tool FROM tool_calls").fetchall())


def _args_oto_org(base):
    with base._connect() as c:
        return [r["args"] for r in c.execute(
            "SELECT args FROM tool_calls WHERE tool = 'oto_org' ORDER BY id").fetchall()]


def test_a_blanc_la_purge_compte_et_n_ecrit_rien(journal):
    from oto_mcp import maintenance
    avant = _tools(journal)
    out = maintenance.journal_tokens(dry_run=True)
    assert out["applied"] is False
    assert out["routes"] == {
        "/api/upload/:token": 2,
        "/api/public/docs/:token": 1,
        "/api/invitations/:token": 1,
    }
    assert out["args"]["rows"] == 1
    assert _tools(journal) == avant, "une passe à blanc a écrit"


def test_la_purge_reduit_chaque_route_independamment_sans_les_confondre(journal):
    from oto_mcp import maintenance
    maintenance.journal_tokens(dry_run=False)
    tools = _tools(journal)
    assert JETON not in " ".join(tools)
    assert INVITE not in " ".join(tools)
    assert "GET /api/invitations/:token" in tools
    assert "GET /api/public/docs/:token" in tools
    assert tools.count("PUT /api/upload/:token") == 2
    assert "POST /api/orgs/:id/members" in tools     # intacte


def test_la_purge_masque_aussi_le_meme_secret_passe_en_argument(journal):
    from oto_mcp import maintenance
    maintenance.journal_tokens(dry_run=False)
    args = _args_oto_org(journal)
    assert args[0]["token"].startswith("#") and INVITE not in args[0]["token"]
    assert args[0]["op"] == "accept_invite"          # l'intention reste lisible
    assert args[1] == {"op": "invite", "email": "a@b.c"}   # rien touché


def test_la_purge_est_rejouable(journal):
    from oto_mcp import maintenance
    maintenance.journal_tokens(dry_run=False)
    apres_un = _tools(journal)
    out = maintenance.journal_tokens(dry_run=True)
    assert out["routes"] == {} and out["args"]["rows"] == 0
    maintenance.journal_tokens(dry_run=False)
    assert _tools(journal) == apres_un
