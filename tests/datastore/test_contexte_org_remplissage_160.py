"""oto#160, phase 2 : la révision `0018` remplit `context_org_id` des personnels d'avant.

Sur base réelle, un tableau fabriqué par source : le journal (MCP au même nom ±2 min,
REST ±10 s), un projet lié (d'org, ou personnel rangé), et les cas qui doivent rester
NULL — sans trace, trace hors fenêtre, nom différent, journal ambigu, org supprimée.
Plus : un tableau déjà rempli n'est pas réécrit, un tableau d'org n'est pas touché, la
révision rejouée ne change rien, et son retour arrière ne défait rien.
"""
from __future__ import annotations

import itertools
import uuid
from pathlib import Path

import pytest

SUB = "usr_remplissage_160"
RACINE = Path(__file__).resolve().parent.parent.parent
AVANT, APRES = "0017_tableaux_contexte_org", "0018_contexte_org_rempli"


def _conn():
    from oto_mcp.db._conn import _connect
    return _connect()


def _contexte(ns_id: int):
    with _conn() as conn:
        return conn.execute("SELECT context_org_id FROM user_datastores WHERE id = %s",
                            (ns_id,)).fetchone()["context_org_id"]


_HEURES = itertools.count(1)


def _tableau(owner_type: str = "user", owner_id: str = SUB, *,
             contexte: int | None = None) -> tuple[int, str]:
    """Chaque tableau naît une heure avant le précédent : la fenêtre REST (±10 s) ne
    reconnaît pas le nom, elle attraperait sinon tous les tableaux du banc."""
    from oto_mcp import db
    nom = "r160-" + uuid.uuid4().hex[:6]
    ns_id = db.create_datastore(owner_type, owner_id, nom, context_org_id=contexte)
    with _conn() as conn:
        conn.execute("UPDATE user_datastores SET created_at = now() - %s * interval "
                     "'1 hour' WHERE id = %s", (next(_HEURES), ns_id))
    return ns_id, nom


def _appel(ns_id: int, *, kind: str, tool: str, org_id: int | None,
           decalage: str, nom: str | None = None, sub: str = SUB,
           effective_sub: str | None = None, ok: bool = True) -> None:
    """Une ligne de journal posée à `created_at du tableau + décalage`."""
    from psycopg.types.json import Jsonb
    with _conn() as conn:
        conn.execute(
            "INSERT INTO tool_calls (kind, sub, effective_sub, tool, args, ok, org_id, "
            "created_at) SELECT %s, %s, %s, %s, %s, %s, %s, "
            "d.created_at + %s::interval FROM user_datastores d WHERE d.id = %s",
            (kind, sub, effective_sub, tool,
             Jsonb({"datastore": nom} if nom is not None else {}), ok, org_id,
             decalage, ns_id))


def _alembic():
    from alembic.config import Config
    cfg = Config(str(RACINE / "alembic.ini"))
    cfg.set_main_option("script_location", str(RACINE / "oto_mcp" / "db" / "migrations"))
    return cfg


@pytest.fixture(scope="module")
def orgs(live):
    from oto_mcp import db, org_store
    db.upsert_user(SUB, email=f"{SUB}@t.invalid", name=SUB)
    a = org_store.create_org("A 160", created_by=SUB)
    b = org_store.create_org("B 160", created_by=SUB)
    for o in (a, b):
        org_store.add_org_member(o, SUB)
    return a, b


def test_la_revision_0018_remplit_par_journal_puis_projet_et_rien_d_autre(orgs):
    from alembic import command

    from oto_mcp import db, org_store
    a, b = orgs

    # --- décidables ---------------------------------------------------------
    mcp, nom = _tableau()
    _appel(mcp, kind="mcp", tool="data_create_datastore", org_id=a, decalage="30 s",
           nom=nom)
    rest, _ = _tableau()
    _appel(rest, kind="rest", tool="POST /api/datastores", org_id=b, decalage="-5 s")
    mandataire, nom_m = _tableau()      # le compte relu après exécution fait foi
    _appel(mandataire, kind="mcp", tool="data_create_datastore", org_id=b,
           decalage="1 min", nom=nom_m, sub="usr_autre_160", effective_sub=SUB)
    par_projet_org, _ = _tableau()
    p_org = db.create_project("org", str(b), "P org 160", created_by=SUB)
    db.add_project_link(p_org, "tableau", str(par_projet_org))
    par_projet_perso, _ = _tableau()
    p_perso = db.create_project("user", SUB, "P perso 160", created_by=SUB,
                                context_org_id=a)
    db.add_project_link(p_perso, "tableau", str(par_projet_perso))
    journal_avant_projet, nom_jp = _tableau()     # le journal dit A, le projet dit B
    _appel(journal_avant_projet, kind="mcp", tool="data_create_datastore", org_id=a,
           decalage="0 s", nom=nom_jp)
    db.add_project_link(p_org, "tableau", str(journal_avant_projet))

    # --- indécidables, ou à ne pas toucher -----------------------------------
    sans_trace, _ = _tableau()
    hors_fenetre, nom_hf = _tableau()
    _appel(hors_fenetre, kind="mcp", tool="data_create_datastore", org_id=a,
           decalage="3 min", nom=nom_hf)
    rest_hors_fenetre, _ = _tableau()
    _appel(rest_hors_fenetre, kind="rest", tool="POST /api/datastores", org_id=a,
           decalage="15 s")
    autre_nom, _ = _tableau()
    _appel(autre_nom, kind="mcp", tool="data_create_datastore", org_id=a,
           decalage="0 s", nom="pas-ce-nom")
    autre_compte, nom_ac = _tableau()
    _appel(autre_compte, kind="mcp", tool="data_create_datastore", org_id=a,
           decalage="0 s", nom=nom_ac, sub="usr_autre_160")
    en_echec, nom_ee = _tableau()
    _appel(en_echec, kind="mcp", tool="data_create_datastore", org_id=a,
           decalage="0 s", nom=nom_ee, ok=False)
    ambigu, nom_amb = _tableau()
    _appel(ambigu, kind="mcp", tool="data_create_datastore", org_id=a, decalage="0 s",
           nom=nom_amb)
    _appel(ambigu, kind="mcp", tool="data_create_datastore", org_id=b, decalage="1 s",
           nom=nom_amb)
    projets_ambigus, _ = _tableau()
    db.add_project_link(p_org, "tableau", str(projets_ambigus))
    db.add_project_link(p_perso, "tableau", str(projets_ambigus))
    ephemere = org_store.create_org("Éphémère 160", created_by=SUB)
    org_disparue, nom_od = _tableau()
    _appel(org_disparue, kind="mcp", tool="data_create_datastore", org_id=ephemere,
           decalage="0 s", nom=nom_od)
    with _conn() as conn:
        conn.execute("DELETE FROM orgs WHERE id = %s", (ephemere,))
    deja_rempli, nom_dr = _tableau(contexte=b)
    _appel(deja_rempli, kind="mcp", tool="data_create_datastore", org_id=a,
           decalage="0 s", nom=nom_dr)
    d_org, nom_do = _tableau("org", str(a))
    _appel(d_org, kind="mcp", tool="data_create_datastore", org_id=b, decalage="0 s",
           nom=nom_do)

    cfg = _alembic()
    command.stamp(cfg, AVANT)
    command.upgrade(cfg, APRES)

    attendu = {
        mcp: a, rest: b, mandataire: b, par_projet_org: b, par_projet_perso: a,
        journal_avant_projet: a,
        sans_trace: None, hors_fenetre: None, rest_hors_fenetre: None, autre_nom: None,
        autre_compte: None, en_echec: None, ambigu: None, projets_ambigus: None,
        org_disparue: None, deja_rempli: b, d_org: None,
    }
    assert {i: _contexte(i) for i in attendu} == attendu

    # Rejouée : rien ne bouge. Défaite : rien ne bouge non plus.
    command.downgrade(cfg, AVANT)
    assert {i: _contexte(i) for i in attendu} == attendu
    command.upgrade(cfg, APRES)
    assert {i: _contexte(i) for i in attendu} == attendu
    command.stamp(cfg, "head")


def test_le_demarrage_ne_remplit_pas(orgs):
    """Le remplissage est une révision, pas un geste de boot."""
    from oto_mcp.db import init_db
    a, _ = orgs
    ns, nom = _tableau()
    _appel(ns, kind="mcp", tool="data_create_datastore", org_id=a, decalage="0 s",
           nom=nom)
    init_db()
    assert _contexte(ns) is None
