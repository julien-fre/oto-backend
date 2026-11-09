"""Lier à un projet PRIVÉ ce qui appartient à l'org le rend privé (`_liens_prives`).

Le défaut constaté : un projet possédé par une personne et partagé à deux collègues
liait des procédures et des tableaux possédés par l'org. Un troisième membre de l'org,
sans aucun partage du projet, les lisait tous par le catalogue de l'org. Le projet
était privé ; ce qu'il liait ne l'était pas, et rien ne le disait.

Contre un vrai PostgreSQL (boot `init_db`) et par le chemin SERVI (`me.project`, autz
déclarée puis handler) : le transfert, les partages et la sonde de slug sont du SQL.
Chaque test pose ses propres cibles.
"""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest

from oto_mcp.capabilities._types import RawCtx

ADMIN, COLLEGUE, TIERS, AUTRE = "u-admin", "u-collegue", "u-tiers", "u-autre"


@pytest.fixture(scope="module")
def monde(pg_dsn):
    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    nom = "oto_prive_" + uuid.uuid4().hex[:8]
    root = psycopg.connect(pg_dsn, autocommit=True)
    root.execute(f'CREATE DATABASE "{nom}"')
    url_avant, pool_avant = os.environ.get("DATABASE_URL"), dbconn._pool
    os.environ["DATABASE_URL"] = pg_dsn.rsplit("/", 1)[0] + "/" + nom
    dbconn._pool = None
    try:
        from oto_mcp import db, org_store
        from oto_mcp.db import init_db
        init_db()
        org = org_store.create_org("Acme", created_by=ADMIN)
        org_store.add_org_member(org, ADMIN, "org_admin")
        for m in (COLLEGUE, TIERS, AUTRE):
            org_store.add_org_member(org, m, "org_member")
        org_store.set_active_org(ADMIN, org)
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


def _projet(monde, *, partage_org: bool = False) -> int:
    from oto_mcp import db, ownership
    pid = int(db.create_project("user", ADMIN, "Ventes " + uuid.uuid4().hex[:6],
                                created_by=ADMIN, context_org_id=monde["org"]))
    ownership.grant("project", str(pid), "user", COLLEGUE, role="editor", granted_by=ADMIN)
    if partage_org:
        ownership.grant("project", str(pid), "org", str(monde["org"]), role="viewer",
                        granted_by=ADMIN)
    return pid


def _procedure(monde) -> tuple[str, str]:
    from oto_mcp import org_store
    slug = "proc-" + uuid.uuid4().hex[:8]
    org_store.set_instruction("org", monde["org"], slug, "1. Une étape",
                              title=slug, set_by=ADMIN)
    return str(org_store.get_instruction("org", monde["org"], slug)["id"]), slug


def _tableau(monde) -> str:
    from oto_mcp import db
    return str(db.create_datastore("org", str(monde["org"]),
                                   "t-" + uuid.uuid4().hex[:8]))


def _link(pid: int, target_type: str, ref: str, sub: str = ADMIN, **args) -> dict:
    from oto_mcp.capabilities.registry import CAPABILITIES
    cap = next(c for c in CAPABILITIES if c.key == "me.project")
    inp = cap.Input(op="link", project_id=pid, target_type=target_type,
                    target_ref=ref, **args)
    return cap.handler(cap.authz(RawCtx(sub=sub), inp), inp)


def _lit(sub: str, rtype: str, ref: str) -> bool:
    from oto_mcp import ownership
    return ownership.can_access(sub, rtype, ref, "read")


PROC, TAB = "doctrine", "datastore_namespace"


# ── Le défaut, tel qu'il se vivait ───────────────────────────────────────────

def test_une_procedure_d_org_liee_a_un_projet_prive_devient_privee(monde):
    from oto_mcp import ownership
    pid = _projet(monde)
    ref, _ = _procedure(monde)
    assert _lit(TIERS, PROC, ref), "prémisse : un membre lit une procédure d'org"

    out = _link(pid, "procedure", ref)

    assert out["privacy"]["made_private"] is True
    assert ownership.owner_of(PROC, ref) == ("user", ADMIN)
    assert not _lit(TIERS, PROC, ref), "un membre hors du projet la lit encore"
    assert _lit(COLLEGUE, PROC, ref), "le bénéficiaire du projet l'a perdue"
    assert out["privacy"]["shared_with"] == [
        {"principal_type": "user", "principal_id": COLLEGUE, "role": "viewer"}]


def test_le_beneficiaire_lit_la_procedure_par_le_chemin_servi(monde):
    """Pas seulement le seam : `oto_procedure op=get guide_id=…`, comme un agent ou le
    front, sert le corps au bénéficiaire du projet et le refuse au membre hors projet."""
    from oto_mcp.capabilities.registry import CAPABILITIES
    from oto_mcp import org_store
    pid = _projet(monde)
    ref, slug = _procedure(monde)
    _link(pid, "procedure", ref)
    cap = next(c for c in CAPABILITIES if c.key == "org.guide.get")
    for m in (COLLEGUE, TIERS):
        org_store.set_active_org(m, monde["org"])
    inp = cap.Input(guide_id=int(ref))
    lu = asyncio.run(cap.handler(cap.authz(RawCtx(sub=COLLEGUE), inp), inp))
    assert "1. Une étape" in str(lu)
    with pytest.raises(Exception, match=f"No guide #{ref}"):
        asyncio.run(cap.handler(cap.authz(RawCtx(sub=TIERS), inp), inp))


def test_un_tableau_d_org_lie_a_un_projet_prive_devient_prive(monde):
    from oto_mcp import db, ownership
    pid = _projet(monde)
    ref = _tableau(monde)

    out = _link(pid, "tableau", ref)

    assert out["privacy"]["made_private"] is True
    assert ownership.owner_of(TAB, ref) == ("user", ADMIN)
    assert db.get_datastore_by_id(int(ref))["context_org_id"] == monde["org"]
    assert not _lit(TIERS, TAB, ref)
    assert ownership.can_access(COLLEGUE, TAB, ref, "write"), "l'éditeur du projet n'écrit plus"


def test_relier_un_lien_existant_applique_la_regle(monde):
    """Le chemin de rattrapage d'un projet d'avant la règle : le même geste, rejoué."""
    from oto_mcp import db, ownership
    pid = _projet(monde)
    ref, _ = _procedure(monde)
    db.add_project_link(pid, "procedure", ref)   # posé avant la règle

    out = _link(pid, "procedure", ref)

    assert out["link_status"] == "unchanged"
    assert out["privacy"]["made_private"] is True
    assert ownership.owner_of(PROC, ref) == ("user", ADMIN)


# ── Ce qui reste à l'org, et le dit ──────────────────────────────────────────

def test_org_wide_true_garde_la_cible_a_l_org(monde):
    from oto_mcp import ownership
    pid = _projet(monde)
    ref, _ = _procedure(monde)
    out = _link(pid, "procedure", ref, org_wide=True)
    assert out["privacy"] == {"kept_org_wide": True, "reason": "org_wide_requested"}
    assert ownership.owner_of(PROC, ref)[0] == "org"


def test_un_projet_partage_a_l_org_n_est_pas_prive(monde):
    from oto_mcp import ownership
    pid = _projet(monde, partage_org=True)
    ref, _ = _procedure(monde)
    out = _link(pid, "procedure", ref)
    assert "privacy" not in out
    assert ownership.owner_of(PROC, ref)[0] == "org"


def test_un_projet_d_org_ne_touche_a_rien(monde):
    from oto_mcp import db, ownership
    pid = int(db.create_project("org", str(monde["org"]), "Base", created_by=ADMIN))
    ref, _ = _procedure(monde)
    out = _link(pid, "procedure", ref)
    assert "privacy" not in out
    assert ownership.owner_of(PROC, ref)[0] == "org"


def test_une_cible_liee_par_un_autre_projet_reste_a_l_org(monde):
    from oto_mcp import db, ownership
    autre = int(db.create_project("org", str(monde["org"]), "Autre", created_by=ADMIN))
    ref, _ = _procedure(monde)
    db.add_project_link(autre, "procedure", ref)
    out = _link(_projet(monde), "procedure", ref)
    assert out["privacy"]["reason"] == "linked_by_other_projects"
    assert ownership.owner_of(PROC, ref)[0] == "org"


def test_sans_etre_admin_de_l_org_la_cible_reste_a_l_org(monde):
    """Le collègue ÉCRIT le projet, mais sortir une ressource de l'org est un transfert."""
    from oto_mcp import ownership
    pid = _projet(monde)
    ref, _ = _procedure(monde)
    out = _link(pid, "procedure", ref, sub=COLLEGUE)
    assert out["privacy"]["reason"] == "needs_org_admin"
    assert ownership.owner_of(PROC, ref)[0] == "org"


def test_un_agent_d_un_autre_porteur_garde_la_procedure_a_l_org(monde):
    """Un agent lit sa procédure chez SON porteur puis à l'org : déplacée chez le
    propriétaire du projet, celle-ci ne se résoudrait plus pour l'agent d'un autre."""
    from oto_mcp import db, ownership
    pid = _projet(monde)
    ref, slug = _procedure(monde)
    db.create_trigger(monde["org"], AUTRE, procedure=slug, tz="Europe/Paris", tools=[])
    out = _link(pid, "procedure", ref)
    assert out["privacy"]["reason"] == "agents_depend_on_it"
    assert ownership.owner_of(PROC, ref)[0] == "org"


def test_un_agent_du_proprietaire_ne_bloque_pas(monde):
    from oto_mcp import db, ownership
    pid = _projet(monde)
    ref, slug = _procedure(monde)
    db.create_trigger(monde["org"], ADMIN, procedure=slug, tz="Europe/Paris", tools=[])
    out = _link(pid, "procedure", ref)
    assert out["privacy"]["made_private"] is True
    assert ownership.owner_of(PROC, ref) == ("user", ADMIN)


def test_un_slug_deja_pris_chez_le_proprietaire_garde_la_procedure(monde):
    from oto_mcp import org_store, ownership
    pid = _projet(monde)
    ref, slug = _procedure(monde)
    org_store.set_instruction("user", ADMIN, slug, "ma copie", title=slug, set_by=ADMIN)
    out = _link(pid, "procedure", ref)
    assert out["privacy"]["reason"] == "slug_taken_at_owner"
    assert ownership.owner_of(PROC, ref)[0] == "org"
