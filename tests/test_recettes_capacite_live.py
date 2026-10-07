"""`oto_recipe` de bout en bout sur un vrai store : écrire, proposer, publier (une page
d'épreuve), exécuter, et les refus nommés — dont l'agent hébergé, borné à la liste
d'outils de son travail."""
from __future__ import annotations

import asyncio
import uuid

import pytest

SUB = "sub-recettes-capa"
SCHEMA = {"key": "contact_key", "fields": [
    {"key": "contact_key", "type": "text"}, {"key": "title", "type": "text"}]}
PERSONNES = [{"link": {"linkedin": f"https://www.linkedin.com/in/q{i}"},
              "profile": {"title": f"Role {i}"}} for i in range(4)]
CORPS = {"tool": "acme_people", "arguments": {}, "source": {
    "items": "content", "pagination": {"type": "page", "param": "page", "size": 2,
                                       "size_param": "size", "last": "last"}},
         "map": {"linkedin_url": "link.linkedin", "title": "profile.title"},
         "key": {"column": "contact_key", "template": "{{item.link.linkedin}}"},
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
    monkeypatch.setattr(access, "current_user_sub_or_raise", lambda: SUB)
    monkeypatch.setattr(hooks, "current_user_sub_from_token", lambda: SUB)
    m = FastMCP("t-recettes-capa")

    @m.tool(annotations=LECTURE)
    def acme_people(page: int = 0, size: int = 2) -> dict:
        return {"content": PERSONNES[page * size:(page + 1) * size],
                "last": (page + 1) * size >= len(PERSONNES)}
    monkeypatch.setattr(tool_registry, "bound_instance", lambda: m)
    ctx = ResolvedCtx(sub=SUB, org_id=None)

    def appeler(**kw):
        kw.setdefault("scope", "user")
        return asyncio.run(recipes._recipe(ctx, recipes.RecipeInput(**kw)))
    return appeler, monkeypatch


def _table() -> str:
    from oto_mcp import db
    from oto_mcp.datastore.core import make_store
    ns = f"recettes-capa-{uuid.uuid4().hex[:6]}"
    db.create_datastore("user", SUB, ns)
    make_store(SUB).set_schema(ns, SCHEMA)
    return ns


def _code(exc) -> str:
    return getattr(exc.value, "code", None) or exc.value.args[1]


def test_ecrire_publier_executer(capa):
    from oto_mcp.capabilities._types import AuthzDenied
    appeler, _ = capa
    slug = f"acme-{uuid.uuid4().hex[:6]}"
    out = appeler(op="create", slug=slug, title="Acme people", recipe=CORPS)
    assert out["version"] == {"version": 1, "status": "proposee"}
    with pytest.raises(AuthzDenied) as e:
        appeler(op="run", slug=slug, datastore=_table())
    assert _code(e) == "not_published"
    pub = appeler(op="publish", slug=slug, version=1)
    assert pub["version"]["status"] == "publiee" and pub["receipt"]["rows_built"] == 2
    ns = _table()
    run = appeler(op="run", slug=slug, datastore=ns)
    assert run["receipt"]["done"] and run["receipt"]["written"] == 4
    fiche = appeler(op="get", slug=slug)
    assert fiche["recipe"]["published_version"] == 1
    assert fiche["version"]["test_report"]["fill"] == {"linkedin_url": 1.0, "title": 1.0}


def test_proposer_exige_la_derniere_version_lue(capa):
    from oto_mcp.capabilities._types import AuthzDenied
    appeler, _ = capa
    slug = f"acme-{uuid.uuid4().hex[:6]}"
    appeler(op="create", slug=slug, title="t", recipe=CORPS)
    assert appeler(op="propose", slug=slug, expected_version=1,
                   recipe=CORPS)["version"]["version"] == 2
    with pytest.raises(AuthzDenied) as e:
        appeler(op="propose", slug=slug, expected_version=1, recipe=CORPS)
    assert _code(e) == "version_conflict"
    assert [v["version"] for v in appeler(op="versions", slug=slug)["versions"]] == [2, 1]


def test_une_recette_fautive_est_refusee_avec_ses_defauts(capa):
    from oto_mcp.capabilities._types import AuthzDenied
    appeler, _ = capa
    with pytest.raises(AuthzDenied) as e:
        appeler(op="create", slug="acme-bad", title="t", recipe={**CORPS, "limits": {}})
    assert _code(e) == "invalid_recipe"


def test_une_recette_en_ligne_s_eprouve_mais_n_ecrit_jamais(capa):
    """Seule une version publiée écrit : une recette en ligne ne sert qu'à `test`."""
    from oto_mcp.capabilities._types import AuthzDenied
    from oto_mcp.datastore.core import make_store
    appeler, _ = capa
    ns = _table()
    with pytest.raises(AuthzDenied) as e:
        appeler(op="run", recipe=CORPS, datastore=ns)
    assert _code(e) == "inline_recipe_cannot_write"
    recu = appeler(op="test", recipe=CORPS, datastore=ns)["receipt"]
    assert recu["rows_built"] == 2 and recu["written"] == 0
    assert make_store(SUB).cursor_rows(ns, limit=10)["rows"] == []


def _travail(outils) -> tuple[int, int]:
    """Un travail du runner dont la charge porte `outils`, dans une org neuve dont le
    compte est membre. Rend `(job_id, org_id)`."""
    from oto_mcp import db, org_store
    org = org_store.create_org(f"Acme jobs {uuid.uuid4().hex[:4]}", created_by=SUB)
    org_store.add_org_member(org, SUB)
    job = db.enqueue_job(org, "start", payload={"procedure": "p", "tools": outils}, sub=SUB)
    return job["id"], org


def _sous_le_travail(monkeypatch, job_id):
    from oto_mcp.capabilities import recipes
    monkeypatch.setattr(recipes, "current_token_axes", lambda: {"token_kind": "delegation"})
    monkeypatch.setattr(recipes, "current_delegation_job", lambda: job_id)


def _appels(outil: str) -> int:
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        return conn.execute("SELECT count(*) AS n FROM tool_calls WHERE sub = %s AND tool = %s",
                            (SUB, outil)).fetchone()["n"]


def test_un_jeton_de_delegation_sans_travail_est_refuse(capa):
    """Un jeton émis avant qu'il porte son travail : la liste est introuvable, refus."""
    from oto_mcp.capabilities._types import AuthzDenied
    appeler, monkeypatch = capa
    _sous_le_travail(monkeypatch, None)
    for kw in ({"op": "test", "recipe": CORPS}, {"op": "sample", "tool": "acme_people"}):
        with pytest.raises(AuthzDenied) as e:
            appeler(**kw)
        assert _code(e) == "hosted_runs_not_supported"
    # Les ops de stockage restent ouvertes : elles n'appellent aucun outil.
    assert "recipes" in appeler(op="list")


def test_un_agent_heberge_execute_une_recette_de_sa_liste(capa):
    appeler, monkeypatch = capa
    job, _ = _travail(["oto_recipe", "acme_people"])
    slug = f"acme-{uuid.uuid4().hex[:6]}"
    appeler(op="create", slug=slug, title="Acme people", recipe=CORPS)
    _sous_le_travail(monkeypatch, job)
    appeler(op="publish", slug=slug, version=1)
    assert appeler(op="run", slug=slug, datastore=_table())["receipt"]["written"] == 4
    assert appeler(op="sample", tool="acme_people", items="content")["shape"]["items_found"]


def test_un_outil_hors_de_la_liste_est_refuse_avant_tout_appel(capa):
    """`oto_call` dans la liste n'ouvre rien : la recette n'élargit pas l'agent."""
    from oto_mcp.capabilities._types import AuthzDenied
    appeler, monkeypatch = capa
    job, _ = _travail(["oto_recipe", "oto_call"])
    _sous_le_travail(monkeypatch, job)
    avant = _appels("acme_people")
    for kw in ({"op": "test", "recipe": CORPS}, {"op": "sample", "tool": "acme_people"}):
        with pytest.raises(AuthzDenied) as e:
            appeler(**kw)
        assert _code(e) == "recipe_tool_not_in_agent_tools"
    assert _appels("acme_people") == avant


def test_un_agent_heberge_ne_sort_pas_de_l_org_de_son_travail(capa):
    """Le verrou du jeton tient l'org : une recette appelée pour une autre org du
    porteur est refusée avant tout appel."""
    from oto_mcp import org_store, verrou_org
    from oto_mcp.capabilities import recipes
    from oto_mcp.capabilities._types import ResolvedCtx
    from oto_mcp.mcp_errors import McpError
    _, monkeypatch = capa
    job, org = _travail(["oto_recipe", "acme_people"])
    autre = org_store.create_org(f"Acme other {uuid.uuid4().hex[:4]}", created_by=SUB)
    org_store.add_org_member(autre, SUB)
    _sous_le_travail(monkeypatch, job)
    avant = _appels("acme_people")
    jeton = verrou_org.poser(verrou_org.Verrou(sub=SUB, org_id=org, job_id=job))
    try:
        with pytest.raises(McpError) as e:
            asyncio.run(recipes._recipe(ResolvedCtx(sub=SUB, org_id=autre),
                                        recipes.RecipeInput(op="test", recipe=CORPS)))
    finally:
        verrou_org.lever(jeton)
    assert e.value.error.data["code"] == verrou_org.CODE
    assert _appels("acme_people") == avant


def test_les_appels_de_la_recette_suivent_l_org_de_la_recette(capa):
    """La session vit dans une org ; la recette est appelée pour une AUTRE org dont le
    compte est membre. La clé, la facturation et le journal de l'outil doivent suivre
    l'org de la recette — pas l'org maison."""
    from oto_mcp import db, org_store
    from oto_mcp.capabilities import recipes
    from oto_mcp.capabilities._types import ResolvedCtx
    from oto_mcp.db._conn import _connect
    maison = org_store.create_org(f"Acme home {uuid.uuid4().hex[:4]}", created_by=SUB)
    cible = org_store.create_org(f"Acme target {uuid.uuid4().hex[:4]}", created_by=SUB)
    for o in (maison, cible):
        org_store.add_org_member(o, SUB)
    org_store.set_active_org(SUB, maison)
    nom = f"recettes-org-{uuid.uuid4().hex[:6]}"
    ns_id = db.create_datastore("org", str(cible), nom)
    from oto_mcp.datastore.core import make_store
    from oto_mcp import session_org
    jeton = session_org.set_call_org(cible)
    try:
        make_store(SUB).set_schema(nom, SCHEMA)
    finally:
        session_org.reset_call_org(jeton)
    ctx = ResolvedCtx(sub=SUB, org_id=cible)
    slug = f"acme-org-{uuid.uuid4().hex[:6]}"

    def appeler(**kw):
        return asyncio.run(recipes._recipe(ctx, recipes.RecipeInput(slug=slug, **kw)))
    appeler(op="create", title="Acme people", recipe=CORPS)
    appeler(op="publish", version=1)
    recu = appeler(op="run", datastore=ns_id)["receipt"]
    assert recu["written"] == 4
    with _connect() as conn:
        orgs = {r["org_id"] for r in conn.execute(
            "SELECT org_id FROM tool_calls WHERE sub = %s AND tool = 'acme_people' "
            "AND created_at > NOW() - INTERVAL '1 minute'", (SUB,)).fetchall()}
    assert cible in orgs and maison not in orgs
