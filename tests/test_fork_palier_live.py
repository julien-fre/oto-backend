"""Le fork d'une entrée de bibliothèque atterrit au palier du RÔLE (décision du 06/10/2026).

Un simple membre qui ajoute un process de la communauté obtenait une procédure d'ORG :
vue de tous les membres et de leurs agents, pour qui une procédure existante fait
autorité, et qu'il ne pouvait ensuite ni corriger ni supprimer (réservé à org_admin).
Ce qu'on crée appartient à son créateur : la copie d'un membre est désormais une
procédure PERSONNELLE ; celle d'un org_admin reste une procédure d'org.

Contre un vrai PostgreSQL, sur le CHEMIN SERVI (autz déclarée puis handler), par les
deux faces : `library.fork` (REST) et `oto_procedure op=fork` (MCP).
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid

import pytest

from oto_mcp.capabilities._types import AuthzDenied, RawCtx

_CORPS = "# Veille\n\nUne procédure de la communauté."


@pytest.fixture(scope="module")
def monde(pg_module_dsn):
    pytest.importorskip("psycopg")
    url_avant = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = pg_module_dsn
    try:
        from oto_mcp import org_store
        from oto_mcp.db import init_db
        init_db()
        org = org_store.create_org("Acme", created_by="u-admin")
        org_store.add_org_member(org, "u-admin", "org_admin")
        for sub in ("u-membre", "u-autre"):
            org_store.add_org_member(org, sub, "org_member")
        for sub in ("u-admin", "u-membre", "u-autre"):
            org_store.set_active_org(sub, org)
        yield {"org": org}
    finally:
        if url_avant is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = url_avant


def _cap(key: str, sub: str, **args):
    from oto_mcp.capabilities.registry import CAPABILITIES
    cap = next(c for c in CAPABILITIES if c.key == key)
    inp = cap.Input(**args)
    out = cap.handler(cap.authz(RawCtx(sub=sub), inp), inp)
    return asyncio.run(out) if asyncio.iscoroutine(out) else out


def _entree() -> str:
    from oto_mcp import org_store
    slug = f"veille-{uuid.uuid4().hex[:6]}"
    org_store.publish_guide(slug=slug, title="Veille", body_md=_CORPS,
                            author_kind="otomata", published_by="u-admin")
    return slug


def _liste(sub: str) -> str:
    return json.dumps(_cap("org.procedure.console", sub, op="list"), default=str)


def _fork_rest(sub: str, **args):
    return _cap("library.fork", sub, **args)


def _fork_mcp(sub: str, **args):
    return _cap("org.procedure.console", sub, op="fork", **args)


@pytest.mark.parametrize("fork", [_fork_rest, _fork_mcp], ids=["rest", "mcp"])
def test_un_membre_obtient_une_procedure_perso_invisible_des_autres(monde, fork):
    from oto_mcp import org_store
    slug = _entree()
    out = fork("u-membre", slug=slug)
    assert out["scope"] == "user" and out["org_id"] == monde["org"]
    assert org_store.get_instruction("user", "u-membre", out["slug"]) is not None
    assert org_store.get_instruction("org", monde["org"], out["slug"]) is None
    assert out["slug"] in _liste("u-membre")
    assert out["slug"] not in _liste("u-autre"), "la copie d'un membre est à lui seul"


def test_un_admin_obtient_une_procedure_d_org(monde):
    from oto_mcp import org_store
    slug = _entree()
    out = _fork_rest("u-admin", slug=slug)
    assert out["scope"] == "org"
    assert org_store.get_instruction("org", monde["org"], out["slug"]) is not None
    assert out["slug"] in _liste("u-autre")


def test_un_admin_peut_forker_pour_lui_seul(monde):
    from oto_mcp import org_store
    slug = _entree()
    out = _fork_mcp("u-admin", slug=slug, scope="user")
    assert out["scope"] == "user"
    assert org_store.get_instruction("user", "u-admin", out["slug"]) is not None


@pytest.mark.parametrize("fork", [_fork_rest, _fork_mcp], ids=["rest", "mcp"])
def test_scope_org_sans_etre_admin_est_refuse_sans_rien_ecrire(monde, fork):
    from oto_mcp import org_store
    slug = _entree()
    with pytest.raises(AuthzDenied) as e:
        fork("u-membre", slug=slug, scope="org")
    assert e.value.status == 403 and e.value.details["scope"] == "org"
    assert "scope='user'" in e.value.message, "le refus nomme le palier ouvert"
    assert org_store.get_instruction("org", monde["org"], slug) is None
    assert org_store.get_instruction("user", "u-membre", slug) is None


def test_un_scope_inconnu_est_refuse_cote_mcp(monde):
    with pytest.raises(AuthzDenied) as e:
        _fork_mcp("u-membre", slug=_entree(), scope="group")
    assert (e.value.status, e.value.code) == (400, "bad_scope")


def test_sans_org_active_le_fork_est_refuse(monde):
    with pytest.raises(AuthzDenied) as e:
        _fork_rest("u-sans-org", slug=_entree())
    assert (e.value.status, e.value.code) == (400, "no_active_org")


def test_la_copie_se_rattache_au_projet_demande(monde):
    from oto_mcp import db
    projet = _cap("me.project", "u-membre", op="create", name="Mon projet",
                  owner_type="user")
    out = _fork_rest("u-membre", slug=_entree(), project_id=int(projet["id"]))
    assert out["project_id"] == int(projet["id"])
    liens = db.list_project_links(int(projet["id"]))
    assert [(l["target_type"], l["target_ref"]) for l in liens] == [
        ("procedure", str(out["guide_id"]))]


def test_un_projet_non_modifiable_refuse_avant_toute_copie(monde):
    from oto_mcp import org_store
    projet = _cap("me.project", "u-autre", op="create", name="Projet d'un autre",
                  owner_type="user")
    slug = _entree()
    with pytest.raises(AuthzDenied) as e:
        _fork_rest("u-membre", slug=slug, project_id=int(projet["id"]))
    assert (e.value.status, e.value.code) == (403, "project_not_writable")
    assert org_store.get_instruction("user", "u-membre", slug) is None, "rien copié"
