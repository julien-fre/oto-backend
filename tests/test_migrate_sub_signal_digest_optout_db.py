"""Une bascule de compte doit emporter le refus de recevoir le DIGEST de signaux —
exercé sur le merge réel, en SQL réel. Même patron que
`test_migrate_sub_outreach_db.py`, pour la table sœur trouvée non triagée par la
garde d'inventaire (`test_migrate_sub_cascade.py`,
`test_migrate_sub_inventory.py`) à la revue du lot oto#150.

`test_migrate_sub_cascade` et `test_migrate_sub_inventory` dérivent leur verdict du
DDL : ils prouvent que la colonne est TRIAGÉE, jamais que le repointage a lieu. Ici,
c'est le repointage qui compte : `signal_digest_optouts.sub` porte `ON DELETE
CASCADE` vers `users(sub)`, donc sans pré-traitement la ligne part en CASCADE avec
l'ancien compte à l'étape 4 (`DELETE FROM users`) — la personne désinscrite se
retrouve RÉ-ABONNÉE par une fusion qu'elle n'a pas demandée, sans erreur, sans trace.

⚠️ Même famille qu'`outreach_optouts` : PK `sub` SEUL (reste de PK vide). Un
`UPDATE` nu y lèverait `UniqueViolation` dès que les deux comptes de la personne se
sont désinscrits du digest — et ferait échouer TOUT le merge, pas seulement cette
table.

Patron de base éphémère : `test_migrate_sub_group_grants_db.py::live`.
"""
from __future__ import annotations

import os
import uuid

import pytest

VIEUX, NEUF = "sub-vieux-digest", "sub-neuf-digest"


@pytest.fixture()
def live(pg_dsn):
    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    name = "oto_migdigest_" + uuid.uuid4().hex[:8]
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
        with dbconn._connect() as conn:
            for sub in (VIEUX, NEUF):
                conn.execute("INSERT INTO users (sub, email) VALUES (%s, %s)",
                             (sub, f"{sub}@exemple.test"))
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


def _sql(requete, params=()):
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        return [dict(r) for r in conn.execute(requete, params).fetchall()]


def _optout(sub):
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        conn.execute("INSERT INTO signal_digest_optouts (sub) VALUES (%s)", (sub,))


def _migre():
    from oto_mcp.db.users import migrate_sub
    migrate_sub(VIEUX, NEUF, operator_source="test")


def test_le_refus_de_recevoir_le_digest_SUIT_la_bascule(live):
    _optout(VIEUX)
    _migre()
    assert [r["sub"] for r in _sql("SELECT sub FROM signal_digest_optouts")] == [NEUF], (
        "sans repointage, la fusion RÉ-ABONNE au digest quelqu'un qui s'en était "
        "désinscrit")


def test_les_DEUX_comptes_desinscrits_du_digest_ne_font_pas_echouer_le_merge(live):
    """La collision sur une PK réduite à la seule colonne de sub — le cas qui exige
    le pré-traitement, et le seul qui exerce le « reste de PK » VIDE."""
    _optout(VIEUX)
    _optout(NEUF)
    _migre()   # ne doit PAS lever
    assert [r["sub"] for r in _sql("SELECT sub FROM signal_digest_optouts")] == [NEUF]


def test_desinscription_relance_et_desinscription_digest_restent_INDEPENDANTES(live):
    """Non-régression du lot oto#150 : repointer les deux tables lors d'une fusion ne
    doit pas les faire fusionner l'une dans l'autre — chacune suit la personne, sur
    SON canal, jamais sur l'autre."""
    _optout(VIEUX)  # désinscrit du digest SEUL
    _migre()
    assert [r["sub"] for r in _sql("SELECT sub FROM signal_digest_optouts")] == [NEUF]
    assert _sql("SELECT sub FROM outreach_optouts") == [], (
        "une désinscription du digest ne doit jamais écrire dans outreach_optouts")


# ── la preuve que le triage est ce qui tient ─────────────────────────────────

def test_en_UPDATE_nu_le_refus_double_ferait_echouer_TOUT_le_merge(live, monkeypatch):
    """Rangée un temps dans `_SUB_COLUMNS` (l'UPDATE nu), la collision des deux
    désinscriptions ferait échouer tout le merge — c'est le mode d'échec réel que
    `_PK_SUB_TABLES` existe pour éviter."""
    import psycopg
    from oto_mcp.db import users
    avant = {e[0] for e in users._PK_SUB_TABLES}
    assert "signal_digest_optouts" in avant, (
        "signal_digest_optouts n'est pré-traitée par AUCUNE famille : la mutation "
        "ne retirerait rien et ce test passerait pour la mauvaise raison.")
    monkeypatch.setattr(
        users, "_PK_SUB_TABLES",
        tuple(e for e in users._PK_SUB_TABLES if e[0] != "signal_digest_optouts"))
    monkeypatch.setattr(users, "_SUB_COLUMNS",
                        users._SUB_COLUMNS + [("signal_digest_optouts", "sub")])
    _optout(VIEUX)
    _optout(NEUF)
    with pytest.raises(psycopg.errors.UniqueViolation):
        _migre()
