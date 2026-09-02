"""Une bascule de compte doit emporter les relances reçues ET le refus de recevoir —
exercé sur le merge réel, en SQL réel.

`test_migrate_sub_cascade` et `test_migrate_sub_inventory` dérivent leur verdict du
DDL : ils prouvent que la colonne est TRIAGÉE, jamais que le repointage a lieu. Or
c'est le repointage qui compte ici, et pour deux raisons différentes :

- **le refus** (`outreach_optouts`) : sans repointage, la ligne part en CASCADE avec
  l'ancien compte, et la personne se retrouve RÉ-ABONNÉE par la fusion — un opt-out
  annulé par une opération technique qu'elle n'a pas demandée ;
- **les relances déjà reçues** (`outreach_sends`) : sans repointage, le compte
  fusionné ressort « jamais relancé » et reçoit une seconde fois le même mail.

Et surtout, les DEUX cas de collision — c'est eux qui justifient le pré-traitement.
Un `UPDATE` nu y lèverait `UniqueViolation`, et cette exception ferait échouer **tout**
le merge, pas seulement ces tables (mode d'échec vécu en prod le 2026-07-28 sur
`org_members` : merge en échec à CHAQUE requête de l'user).

⚠️ `outreach_optouts` est le premier cas où la colonne de sub est **à elle seule** la
clé : le « reste de PK » est vide, et sans le repli `or "TRUE"` de `migrate_sub` le SQL
généré est invalide (`… AND )`). Déplacer l'une ou l'autre entrée vers `_SUB_COLUMNS`
rend ce fichier rouge.

Patron de base éphémère : `test_migrate_sub_group_grants_db.py::live`.
"""
from __future__ import annotations

import os
import uuid

import pytest

VIEUX, NEUF = "sub-vieux", "sub-neuf"


@pytest.fixture()
def live(pg_dsn):
    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    name = "oto_migroutreach_" + uuid.uuid4().hex[:8]
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


def _envoi(sub, campagne="c1", kind="send"):
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        conn.execute(
            "INSERT INTO outreach_sends (campaign, sub, to_email, locale, kind, "
            "fingerprint, sent_by) VALUES (%s, %s, 'x@exemple.test', 'fr', %s, 'fp', %s)",
            (campagne, sub, kind, sub))


def _optout(sub):
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        conn.execute("INSERT INTO outreach_optouts (sub) VALUES (%s)", (sub,))


def _migre():
    from oto_mcp.db.users import migrate_sub
    migrate_sub(VIEUX, NEUF, operator_source="test")


# ── le refus suit la personne ────────────────────────────────────────────────

def test_le_refus_de_recevoir_SUIT_la_bascule(live):
    _optout(VIEUX)
    _migre()
    assert [r["sub"] for r in _sql("SELECT sub FROM outreach_optouts")] == [NEUF], (
        "sans repointage, la fusion RÉ-ABONNE quelqu'un qui s'était désinscrit")


def test_les_DEUX_comptes_desinscrits_ne_font_pas_echouer_le_merge(live):
    """La collision sur une PK réduite à la seule colonne de sub — le cas qui exige le
    pré-traitement, et le seul qui exerce le « reste de PK » VIDE."""
    _optout(VIEUX)
    _optout(NEUF)
    _migre()   # ne doit PAS lever
    assert [r["sub"] for r in _sql("SELECT sub FROM outreach_optouts")] == [NEUF]


# ── les relances reçues suivent aussi ────────────────────────────────────────

def test_les_relances_recues_SUIVENT_la_bascule(live):
    _envoi(VIEUX)
    _migre()
    lignes = _sql("SELECT sub, sent_by FROM outreach_sends")
    assert [l["sub"] for l in lignes] == [NEUF], (
        "sans repointage, le compte fusionné ressort « jamais relancé » et reçoit une "
        "seconde fois le même mail")
    assert lignes[0]["sent_by"] == NEUF, "l'auteur aussi, sinon il désigne un mort"


def test_la_MEME_campagne_recue_par_les_deux_comptes_ne_casse_pas_le_merge(live):
    """L'index unique partiel `(campaign, sub) WHERE kind='send'` : c'est lui, et non
    une PK, qui impose le pré-traitement de cette table."""
    _envoi(VIEUX)
    _envoi(NEUF)
    _migre()   # ne doit PAS lever
    assert len(_sql("SELECT 1 FROM outreach_sends WHERE campaign = 'c1' "
                    "AND kind = 'send'")) == 1


def test_un_ESSAI_n_est_pas_dedoublonne_avec_un_ENVOI(live):
    """`kind` fait partie de la clé de dédoublonnage. Sans lui, on jetterait l'essai
    de l'ancien compte en croyant dédupliquer un envoi — et l'envoi correspondant
    redeviendrait bloqué faute d'essai."""
    _envoi(VIEUX, kind="test")
    _envoi(NEUF, kind="send")
    _migre()
    kinds = sorted(r["kind"] for r in _sql("SELECT kind FROM outreach_sends"))
    assert kinds == ["send", "test"]


def test_deux_campagnes_DISTINCTES_survivent_toutes_les_deux(live):
    _envoi(VIEUX, campagne="c1")
    _envoi(NEUF, campagne="c2")
    _migre()
    assert sorted(r["campaign"] for r in _sql("SELECT campaign FROM outreach_sends")) \
        == ["c1", "c2"]


# ── la preuve que le triage est ce qui tient ─────────────────────────────────
#
# Les quatre tests ci-dessus seraient verts si `migrate_sub` traitait ces tables par
# hasard, ou si la collision ne se produisait pas dans ce jeu de données. On DÉPLACE
# donc les deux entrées vers `_SUB_COLUMNS` (l'UPDATE nu) et on exige que le merge
# échoue — c'est le mode d'échec réel qu'on prétend éviter.

def _en_update_nu(monkeypatch, table: str):
    from oto_mcp.db import users
    monkeypatch.setattr(users, "_PK_SUB_TABLES",
                        tuple(e for e in users._PK_SUB_TABLES if e[0] != table))
    colonne = "sub"
    monkeypatch.setattr(users, "_SUB_COLUMNS", users._SUB_COLUMNS + [(table, colonne)])


def test_en_UPDATE_nu_le_refus_double_ferait_echouer_TOUT_le_merge(live, monkeypatch):
    import psycopg
    _en_update_nu(monkeypatch, "outreach_optouts")
    _optout(VIEUX)
    _optout(NEUF)
    with pytest.raises(psycopg.errors.UniqueViolation):
        _migre()


def test_en_UPDATE_nu_la_meme_campagne_ferait_echouer_TOUT_le_merge(live, monkeypatch):
    import psycopg
    _en_update_nu(monkeypatch, "outreach_sends")
    _envoi(VIEUX)
    _envoi(NEUF)
    with pytest.raises(psycopg.errors.UniqueViolation):
        _migre()
