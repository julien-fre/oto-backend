"""Capacité `oto_apps` (`me.app`) — apps rangées dans un projet, pas des pages.

L'accès est celui du PROJET ; le manifeste se juge à l'écriture contre l'AUTEUR. On
monkeypatche `db.apps`, `ownership` et les sources du jugement (store, déclencheurs).
Les assertions portent sur le CODE des refus, jamais sur leur phrase.
"""
import pytest

from oto_mcp import db, ownership
from oto_mcp.capabilities import apps as A
from oto_mcp.capabilities import apps_manifeste as M
from oto_mcp.capabilities._types import AuthzDenied, ResolvedCtx

CTX = ResolvedCtx(sub="u1", org_id=42)
ROW = {"id": 5, "project_id": 7, "title": "Lead review", "manifest": {}, "html": "<p>x</p>",
       "thumbnail": None, "created_by": "u1", "created_at": "t", "updated_by": "u1",
       "updated_at": "t", "archived_at": None, "rev": "abc"}
GOOD = {"title": "Leads", "tables": {"leads": {"table": "12", "fields": ["name", "status"],
                                              "set": {"status": ["ok", "no"]}}},
        "agents": {"enrich": {"id": 9, "run": True, "input": True}, "watch": 10}}


class _Store:
    """Un store qui voit le tableau 12 (champs name, status) et rien d'autre."""

    def _resolve(self, ref, write=False):
        if str(ref) != "12":
            from oto_mcp.datastore.core import DatastoreNotFound
            raise DatastoreNotFound(ref)
        return 12

    def get_schema(self, ref):
        return {"fields": [{"key": "name"}, {"key": "status"}]}


@pytest.fixture
def seams(monkeypatch):
    rec = {"create": [], "update": [], "access": {}}
    rights = {"read": True, "write": True}
    monkeypatch.setattr(ownership, "can_access",
                        lambda sub, t, rid, want="read": rights[want])
    monkeypatch.setattr(ownership, "can_access_many",
                        lambda sub, t, owners, want="read": {k: k != "8" for k in owners})
    monkeypatch.setattr(db.apps, "get", lambda i: dict(ROW, id=i) if i == 5 else None)
    monkeypatch.setattr(db.apps, "create",
                        lambda pid, **k: rec["create"].append((pid, k)) or dict(ROW, project_id=pid, **{
                            x: k[x] for x in ("title", "manifest", "html", "thumbnail")}))

    def _update(app_id, *, by, expected_rev=None, **fields):
        if expected_rev not in (None, "abc"):
            raise db.apps.AppConflict("abc")
        rec["update"].append((app_id, by, fields))
        return dict(ROW, **{k: v for k, v in fields.items() if v is not None and v is not ...})

    monkeypatch.setattr(db.apps, "update", _update)
    monkeypatch.setattr(db.apps, "project_owners",
                        lambda ids: {str(i): ("org", "42") for i in ids})
    monkeypatch.setattr(db.apps, "list_for_projects",
                        lambda ids, archived=False: [dict(ROW, project_id=i) for i in ids])
    monkeypatch.setattr(db, "log_project_activity", lambda *a, **k: None)
    monkeypatch.setattr(db, "get_trigger", lambda tid, org: {"id": tid} if tid in (9, 10) else None)
    import oto_mcp.datastore.core as core
    monkeypatch.setattr(core, "make_store", lambda sub: _Store())
    rec["rights"] = rights
    return rec


def _refus(fn) -> AuthzDenied:
    with pytest.raises(AuthzDenied) as e:
        fn()
    return e.value


# ── La forme ────────────────────────────────────────────────────────────────

def test_forme_normalise_agents_et_bornes():
    clean, problems = M.forme(GOOD)
    assert problems == []
    assert clean["agents"] == {"enrich": {"id": 9, "run": True, "input": True},
                               "watch": {"id": 10, "run": False, "input": False}}
    assert clean["tables"]["leads"]["set"] == {"status": ["ok", "no"]}
    assert clean["height"] == M.DEFAULT_HEIGHT


def test_forme_input_sans_run_ne_vaut_rien():
    clean, _ = M.forme({"agents": {"a": {"id": 3, "input": True}}})
    assert clean["agents"]["a"] == {"id": 3, "run": False, "input": False}


@pytest.mark.parametrize("raw", [
    [], {"tables": {"bad name": {"table": "1"}}}, {"tables": {"t": {}}},
    {"tables": {"t": {"table": "1", "fields": "name"}}}, {"agents": {"a": 0}},
])
def test_forme_refuse(raw):
    _, problems = M.forme(raw)
    assert problems


# ── L'auteur ────────────────────────────────────────────────────────────────

def test_auteur_voit_tout(seams):
    clean, _ = M.forme(GOOD)
    assert M.auteur("u1", 42, clean) == []


def test_auteur_tableau_invisible_champ_inconnu_agent_absent(seams):
    clean, _ = M.forme({"tables": {"a": {"table": "99"}, "b": {"table": "12", "fields": ["secret"]}},
                        "agents": {"x": 77}})
    problems = M.auteur("u1", 42, clean)
    assert len(problems) == 3


# ── La capacité ─────────────────────────────────────────────────────────────

def test_create_ecrit_le_manifeste_normalise(seams):
    out = A._app(CTX, A.AppInput(op="create", project_id=7, title=" Leads ", manifest=GOOD,
                                 html="<p>hi</p>"))
    (pid, k), = seams["create"]
    assert pid == 7 and k["title"] == "Leads" and k["by"] == "u1"
    assert k["manifest"]["agents"]["watch"]["run"] is False
    assert out["app"]["project_id"] == 7


def test_create_sans_ecriture_refuse(seams):
    seams["rights"]["write"] = False
    e = _refus(lambda: A._app(CTX, A.AppInput(op="create", project_id=7, title="x", manifest={})))
    assert (e.status, e.code) == (403, "forbidden")
    assert seams["create"] == []


def test_create_tableau_hors_de_portee_de_l_auteur(seams):
    e = _refus(lambda: A._app(CTX, A.AppInput(op="create", project_id=7, title="x",
                                              manifest={"tables": {"t": {"table": "99"}}})))
    assert (e.status, e.code) == (400, "invalid_manifest")
    assert seams["create"] == []


def test_validate_n_ecrit_rien(seams):
    out = A._app(CTX, A.AppInput(op="validate", manifest={"tables": {"t": {"table": "99"}}}))
    assert out["ok"] is False and out["problems"]
    assert seams["create"] == [] and seams["update"] == []


def test_get_inconnue_et_illisible_meme_404(seams):
    e1 = _refus(lambda: A._app(CTX, A.AppInput(op="get", app_id=404)))
    seams["rights"]["read"] = False
    e2 = _refus(lambda: A._app(CTX, A.AppInput(op="get", app_id=5)))
    assert (e1.status, e1.code) == (e2.status, e2.code) == (404, "unknown_app")


def test_update_lecteur_seul_refuse(seams):
    seams["rights"]["write"] = False
    e = _refus(lambda: A._app(CTX, A.AppInput(op="update", app_id=5, title="y")))
    assert (e.status, e.code) == (403, "forbidden")


def test_update_conflit(seams):
    e = _refus(lambda: A._app(CTX, A.AppInput(op="update", app_id=5, title="y", expected_rev="old")))
    assert (e.status, e.code) == (409, "conflict")


def test_update_vignette_effacee_ou_gardee(seams):
    A._app(CTX, A.AppInput(op="update", app_id=5, title="y"))
    A._app(CTX, A.AppInput(op="update", app_id=5, clear_thumbnail=True))
    assert seams["update"][0][2]["thumbnail"] is ...
    assert seams["update"][1][2]["thumbnail"] is None


def test_vignette_cles_inconnues_refusees(seams):
    e = _refus(lambda: A._app(CTX, A.AppInput(op="update", app_id=5, thumbnail={"script": "x"})))
    assert e.code == "invalid_thumbnail"


def test_html_trop_lourd(seams):
    e = _refus(lambda: A._app(CTX, A.AppInput(op="update", app_id=5, html="x" * (A.MAX_HTML + 1))))
    assert e.code == "html_too_large"


def test_list_ne_rend_que_les_projets_lisibles(seams):
    out = A._app(CTX, A.AppInput(op="list", project_ids=[7, 8]))
    assert [a["project_id"] for a in out["apps"]] == [7]


def test_list_exige_des_projets(seams):
    e = _refus(lambda: A._app(CTX, A.AppInput(op="list")))
    assert e.code == "missing_project"


def test_vignette_image_seulement_sur_notre_stockage(seams, monkeypatch):
    from oto_mcp import media_store
    monkeypatch.setattr(media_store, "public_base", lambda: "https://media.example")
    A._app(CTX, A.AppInput(op="update", app_id=5, thumbnail={"image": "https://media.example/a.png"}))
    e = _refus(lambda: A._app(CTX, A.AppInput(op="update", app_id=5,
                                              thumbnail={"image": "https://tiers.example/a.png"})))
    assert e.code == "invalid_thumbnail"
    e = _refus(lambda: A._app(CTX, A.AppInput(op="update", app_id=5,
                                              thumbnail={"image": "https://media.example.tiers.example/a.png"})))
    assert e.code == "invalid_thumbnail"


def test_vignette_image_refusee_sans_stockage_declare(seams, monkeypatch):
    from oto_mcp import media_store

    def _absente():
        raise RuntimeError("OTO_MCP_S3_ENDPOINT")
    monkeypatch.setattr(media_store, "public_base", _absente)
    e = _refus(lambda: A._app(CTX, A.AppInput(op="update", app_id=5,
                                              thumbnail={"image": "https://media.example/a.png"})))
    assert e.code == "invalid_thumbnail"
