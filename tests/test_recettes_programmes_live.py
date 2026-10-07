"""Les exécutions programmées : poser, lister, suspendre ; un passage de la boucle au
nom de qui l'a posé, revérifié à chaque fois ; suspendu après des échecs."""
from __future__ import annotations

import asyncio
import uuid

import pytest

SUB = "sub-recettes-prog"
PERSONNES = [{"id": f"p{i}", "title": f"Role {i}"} for i in range(3)]
CORPS = {"tool": "acme_people", "arguments": {}, "source": {"items": "content"},
         "map": {"title": "title"}, "key": {"column": "contact_key", "template": "{{item.id}}"},
         "limits": {"max_units": 50}}


@pytest.fixture(scope="module")
def compte(live):
    from oto_mcp import db
    db.upsert_user(SUB, email=f"{SUB}@acme.test", name=SUB)
    return SUB


@pytest.fixture
def capa(compte, monkeypatch):
    from fastmcp import FastMCP

    from oto_mcp import access, tool_registry
    from oto_mcp.auth import hooks
    from oto_mcp.capabilities import recipes
    from oto_mcp.capabilities._types import ResolvedCtx
    from oto_mcp.tools.lecture import LECTURE
    monkeypatch.setattr(access, "current_user_sub_or_raise",
                        lambda: hooks.current_user_sub_from_token())
    m = FastMCP("t-recettes-prog")
    etat = {"panne": False}

    @m.tool(annotations=LECTURE)
    def acme_people() -> dict:
        if etat["panne"]:
            raise RuntimeError("down")
        return {"content": PERSONNES}
    monkeypatch.setattr(tool_registry, "bound_instance", lambda: m)
    ctx = ResolvedCtx(sub=SUB, org_id=None)

    def appeler(**kw):
        kw.setdefault("scope", "user")
        with hooks.sub_override(SUB):
            return asyncio.run(recipes._recipe(ctx, recipes.RecipeInput(**kw)))
    return appeler, monkeypatch, etat


def _publiee(appeler) -> str:
    slug = f"prog-{uuid.uuid4().hex[:6]}"
    appeler(op="create", slug=slug, title="t", recipe=CORPS)
    appeler(op="publish", slug=slug, version=1)
    return slug


def _table() -> str:
    from oto_mcp import db
    from oto_mcp.datastore.core import make_store
    ns = f"prog-{uuid.uuid4().hex[:6]}"
    db.create_datastore("user", SUB, ns)
    make_store(SUB).set_schema(ns, {"key": "contact_key", "fields": [
        {"key": "contact_key", "type": "text"}, {"key": "title", "type": "text"}]})
    return ns


def _code(e) -> str:
    return e.value.code


def test_poser_lister_suspendre_retirer(capa):
    from oto_mcp.capabilities._types import AuthzDenied
    appeler, _, _ = capa
    slug = _publiee(appeler)
    with pytest.raises(AuthzDenied) as e:
        appeler(op="schedule", slug=slug, every_minutes=5, datastore=_table())
    assert _code(e) == "schedule_too_frequent"
    out = appeler(op="schedule", slug=slug, every_minutes=60, datastore=_table())
    sid = out["schedules"][0]["id"]
    assert out["schedules"][0]["enabled"]
    assert not appeler(op="set_schedule", slug=slug, schedule_id=sid,
                       enabled=False)["schedules"][0]["enabled"]
    assert appeler(op="unschedule", slug=slug, schedule_id=sid)["schedules"] == []


def test_une_recette_non_publiee_ou_un_agent_heberge_ne_programment_pas(capa):
    from oto_mcp.capabilities import recipes
    from oto_mcp.capabilities._types import AuthzDenied
    appeler, monkeypatch, _ = capa
    slug = f"prog-{uuid.uuid4().hex[:6]}"
    appeler(op="create", slug=slug, title="t", recipe=CORPS)
    with pytest.raises(AuthzDenied) as e:
        appeler(op="schedule", slug=slug, every_minutes=60, datastore=_table())
    assert _code(e) == "not_published"
    monkeypatch.setattr(recipes, "current_token_axes", lambda: {"token_kind": "delegation"})
    with pytest.raises(AuthzDenied) as e:
        appeler(op="schedules", slug=slug)
    assert _code(e) == "schedules_not_in_hosted_agents"


def _du(sid: int) -> None:
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        conn.execute("UPDATE recipe_schedules SET next_run_at = NOW() - INTERVAL '1 minute' "
                     "WHERE id = %s", (sid,))


def _programme(sid: int) -> dict:
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        return dict(conn.execute("SELECT * FROM recipe_schedules WHERE id = %s",
                                 (sid,)).fetchone())


def test_un_passage_ecrit_au_nom_de_qui_l_a_pose_et_garde_son_recu(capa):
    from oto_mcp.datastore.core import make_store
    from oto_mcp.recipes import programmes
    appeler, _, _ = capa
    slug, ns = _publiee(appeler), _table()
    sid = appeler(op="schedule", slug=slug, every_minutes=60, datastore=ns)["schedules"][0]["id"]
    _du(sid)
    assert asyncio.run(programmes._un_tour()) == 1
    assert len(make_store(SUB).cursor_rows(ns, limit=10)["rows"]) == 3
    prog = _programme(sid)
    assert prog["last_receipt"]["written"] == 3 and prog["failures"] == 0
    # Avancé d'un pas : un deuxième tour ne le reprend pas.
    assert asyncio.run(programmes._un_tour()) == 0


def test_trois_echecs_d_affilee_le_suspendent(capa):
    from oto_mcp.recipes import programmes
    appeler, _, etat = capa
    slug = _publiee(appeler)
    sid = appeler(op="schedule", slug=slug, every_minutes=60,
                  datastore=_table())["schedules"][0]["id"]
    etat["panne"] = True
    for _ in range(programmes.ECHECS_MAX):
        _du(sid)
        asyncio.run(programmes._un_tour())
    prog = _programme(sid)
    assert not prog["enabled"] and prog["failures"] == programmes.ECHECS_MAX


def test_un_compte_sorti_de_l_org_suspend_le_programme_sans_appel(capa):
    from oto_mcp import org_store
    from oto_mcp.db import recipes as db_recipes
    from oto_mcp.recipes import programmes
    appeler, _, etat = capa
    slug = _publiee(appeler)
    org = org_store.create_org(f"Acme {uuid.uuid4().hex[:4]}", created_by="someone-else")
    fiche = db_recipes.get_recipe("user", SUB, slug)
    prog = db_recipes.creer_programme(recipe_id=fiche["id"], version=1, sub=SUB, org_id=org,
                                      params={}, datastore=_table(), every_minutes=60)
    _du(prog["id"])
    etat["panne"] = True  # un appel lèverait : aucun ne doit partir
    asyncio.run(programmes._un_tour())
    apres = _programme(prog["id"])
    assert not apres["enabled"]
    assert apres["last_receipt"]["reason"] == "no_longer_member"


def test_la_boucle_est_eteinte_par_defaut(monkeypatch):
    from oto_mcp.recipes import programmes
    monkeypatch.delenv("OTO_RECIPE_SCHEDULER_ENABLED", raising=False)
    assert not programmes.armee()


def test_une_nouvelle_version_publiee_suspend_le_programme(capa):
    from oto_mcp.recipes import programmes
    appeler, _, etat = capa
    slug = _publiee(appeler)
    sid = appeler(op="schedule", slug=slug, every_minutes=60,
                  datastore=_table())["schedules"][0]["id"]
    appeler(op="propose", slug=slug, expected_version=1, recipe=CORPS)
    appeler(op="publish", slug=slug, version=2)
    etat["panne"] = True  # aucun appel ne doit partir
    _du(sid)
    asyncio.run(programmes._un_tour())
    prog = _programme(sid)
    assert not prog["enabled"] and prog["last_receipt"]["stopped"] == "version_changed"


def test_seul_le_createur_reprend_son_programme(capa, monkeypatch):
    from oto_mcp import db, org_store
    from oto_mcp.capabilities import recipes
    from oto_mcp.capabilities._types import AuthzDenied, ResolvedCtx
    from oto_mcp.auth import hooks
    appeler, _, _ = capa
    autre = f"sub-autre-{uuid.uuid4().hex[:4]}"
    db.upsert_user(autre, email=f"{autre}@acme.test", name=autre)
    org = org_store.create_org(f"Acme {uuid.uuid4().hex[:4]}", created_by=SUB)
    org_store.add_org_member(org, SUB)
    org_store.add_org_member(org, autre)
    ctx_a, ctx_b = ResolvedCtx(sub=SUB, org_id=org), ResolvedCtx(sub=autre, org_id=org)
    slug = f"prog-{uuid.uuid4().hex[:6]}"

    def en(ctx, **kw):
        with hooks.sub_override(ctx.sub):
            return asyncio.run(recipes._recipe(ctx, recipes.RecipeInput(slug=slug, **kw)))
    en(ctx_a, op="create", title="t", recipe=CORPS)
    en(ctx_a, op="publish", version=1)
    sid = en(ctx_a, op="schedule", every_minutes=60, datastore=_table())["schedules"][0]["id"]
    en(ctx_a, op="set_schedule", schedule_id=sid, enabled=False)
    with pytest.raises(AuthzDenied) as e:
        en(ctx_b, op="set_schedule", schedule_id=sid, enabled=True)
    assert e.value.code == "not_schedule_owner"
    with pytest.raises(AuthzDenied):
        en(ctx_b, op="unschedule", schedule_id=sid)
