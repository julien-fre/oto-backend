"""Un projet archivé se désarchive, et l'archivage dit ce qu'il range (issue `oto`#38).

**Le défaut.** `op=archive` posait `archived_at`, `op=list` masquait le projet, et rien
ne le remettait à NULL — ni `update`, ni REST, ni store : le seul retour était un UPDATE
SQL par un admin. Cas vécu : un projet archivé par erreur (pris pour un doublon) portait
dans son brief les règles opérationnelles d'une mission. Ne pas désarchiver était un
choix assumé (oto-backend#929), rompu sur décision d'Alexis du 23/09/2026.

**Ce que ce fichier fige :**

1. `op=unarchive` remet le projet dans la liste, dit ce qu'il a annulé
   (`was_archived_at`) et se journalise (`project.unarchive`) ; sur un projet vivant,
   c'est un non-geste (`unarchived: false`), pas une erreur ;
2. même garde que l'archivage (`can_govern`) ;
3. `op=archive` rend ce qu'il range, et REFUSE (409 `confirm_required`) un projet qui
   porte un brief ou une procédure liée sans `confirm=true` — sans rien archiver ;
4. `op=list archived=true` rend les projets archivés, et eux seuls.

Contre un vrai PostgreSQL, sur le CHEMIN SERVI (autz déclarée puis handler).
"""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest

from oto_mcp.capabilities._types import AuthzDenied, RawCtx


@pytest.fixture(scope="module")
def monde(pg_dsn):
    """Base JETABLE bootée par le vrai `init_db` — même recette que #27/#662."""
    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    nom = "oto_38_" + uuid.uuid4().hex[:8]
    root = psycopg.connect(pg_dsn, autocommit=True)
    root.execute(f'CREATE DATABASE "{nom}"')
    url_avant, pool_avant = os.environ.get("DATABASE_URL"), dbconn._pool
    os.environ["DATABASE_URL"] = pg_dsn.rsplit("/", 1)[0] + "/" + nom
    dbconn._pool = None
    try:
        from oto_mcp import org_store
        from oto_mcp.db import init_db
        init_db()
        org = org_store.create_org("Acme", created_by="u-admin")
        org_store.add_org_member(org, "u-admin", "org_admin")
        org_store.add_org_member(org, "u-membre", "org_member")
        for sub in ("u-admin", "u-membre"):
            org_store.set_active_org(sub, org)
        yield {"org": org}
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


def _appel(sub: str, **args):
    """UN appel d'`oto_project` par le chemin servi : autz DÉCLARÉE puis handler."""
    from oto_mcp.capabilities.registry import CAPABILITIES
    cap = next(c for c in CAPABILITIES if c.key == "me.project")
    inp = cap.Input(**args)
    out = cap.handler(cap.authz(RawCtx(sub=sub), inp), inp)
    return asyncio.run(out) if asyncio.iscoroutine(out) else out


def _projet(monde, brief: str = "") -> int:
    out = _appel("u-admin", op="create", name=f"P-{uuid.uuid4().hex[:6]}",
                 brief_md=brief, owner_type="org", owner_id=str(monde["org"]))
    return int(out["id"])


def _ids(sub: str, **args) -> set:
    return {int(p["id"]) for p in _appel(sub, op="list", **args)["projects"]}


# ── 1. Le désarchivage ──────────────────────────────────────────────────────

def test_un_projet_archive_revient_et_le_geste_se_journalise(monde):
    from oto_mcp import db
    pid = _projet(monde)
    archive = _appel("u-admin", op="archive", project_id=pid)
    assert archive["archived"] is True
    assert pid not in _ids("u-admin")

    out = _appel("u-admin", op="unarchive", project_id=pid)
    assert out["unarchived"] is True and out["was_archived_at"]
    assert pid in _ids("u-admin")
    assert db.get_project_by_id(pid)["archived_at"] is None
    actions = [a["action"] for a in db.list_project_activity(pid)]
    assert "project.unarchive" in actions


def test_desarchiver_un_projet_vivant_est_un_non_geste(monde):
    pid = _projet(monde)
    out = _appel("u-admin", op="unarchive", project_id=pid)
    assert (out["unarchived"], out["was_archived_at"]) == (False, None)


def test_desarchiver_demande_de_gouverner_le_projet(monde):
    pid = _projet(monde)
    _appel("u-admin", op="archive", project_id=pid)
    with pytest.raises(AuthzDenied) as e:
        _appel("u-membre", op="unarchive", project_id=pid)
    assert e.value.status == 403


# ── 2. L'archivage dit ce qu'il range ────────────────────────────────────────

def test_un_brief_non_vide_exige_confirm(monde):
    from oto_mcp import db
    pid = _projet(monde, brief="La règle de localisation des données.")
    with pytest.raises(AuthzDenied) as e:
        _appel("u-admin", op="archive", project_id=pid)
    assert (e.value.status, e.value.code) == (409, "confirm_required")
    assert e.value.details["unreachable"]["brief"] is True
    assert db.get_project_by_id(pid)["archived_at"] is None, "rien ne doit être archivé"

    out = _appel("u-admin", op="archive", project_id=pid, confirm=True)
    assert out["archived"] is True and out["unreachable"]["brief"] is True


def test_une_procedure_liee_exige_confirm(monde):
    from oto_mcp import db, org_store
    pid = _projet(monde)
    org_store.set_instruction("org", monde["org"], "cloture", "Corps.")
    guide = org_store.get_instruction("org", monde["org"], "cloture")
    db.add_project_link(pid, "procedure", str(guide["id"]))
    with pytest.raises(AuthzDenied) as e:
        _appel("u-admin", op="archive", project_id=pid)
    assert e.value.code == "confirm_required"
    assert e.value.details["unreachable"]["procedures"] == 1


def test_un_projet_sans_brief_ni_procedure_s_archive_et_dit_ce_qu_il_range(monde):
    pid = _projet(monde)
    out = _appel("u-admin", op="archive", project_id=pid)
    assert out["unreachable"] == {"pages": 0, "procedures": 0, "links": 0,
                                  "brief": False}


# ── 3. Retrouver ce qu'on a rangé ───────────────────────────────────────────

def test_list_archived_rend_les_archives_et_elles_seules(monde):
    vivant, range_ = _projet(monde), _projet(monde)
    _appel("u-admin", op="archive", project_id=range_)
    archives = _ids("u-admin", archived=True)
    assert range_ in archives and vivant not in archives


def test_archived_et_confirm_hors_de_leur_op_sont_refuses(monde):
    pid = _projet(monde)
    with pytest.raises(AuthzDenied) as e:
        _appel("u-admin", op="get", project_id=pid, archived=True)
    assert e.value.code == "unsupported_archived"
    with pytest.raises(AuthzDenied) as e:
        _appel("u-admin", op="get", project_id=pid, confirm=True)
    assert e.value.code == "unsupported_confirm"
