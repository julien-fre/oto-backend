"""Sponsored conversations (`access/parrainage`): the operator pays the Post studio.

Unit first — the rules, the payer, the resolver's sponsored key, the model call's
reservation, the toolbox —, then ON THE SERVED ROUTES against a real PostgreSQL (only the
model is scripted): a sponsored turn is decided by the server and stored on the thread,
runs through a daily spend limit, books its model call to the operator's sponsor payer
(never the org), refuses a tool outside its list, and the monthly quota is refused by
name."""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from oto_mcp.access import parrainage


@pytest.fixture(autouse=True)
def _sans_parrain():
    parrainage.activer(None)
    yield
    parrainage.activer(None)


# ── the rules ────────────────────────────────────────────────────────────────

def test_seul_le_post_studio_parraine():
    parrainage.activer("autre")
    assert parrainage.courant() is None
    parrainage.activer(parrainage.POST_STUDIO)
    assert parrainage.courant() == parrainage.POST_STUDIO
    assert parrainage.paie("apify") and parrainage.paie("anthropic")
    assert not parrainage.paie("hunter")           # not a provider the studio needs


def test_outils_et_operations_permis():
    assert parrainage.outil_permis("apify_linkedin_posts", {"author_urls": []})
    assert parrainage.outil_permis("oto_trigger", {"op": "list"})
    assert not parrainage.outil_permis("oto_trigger", {"op": "run_now"})
    assert not parrainage.outil_permis("oto_trigger", {"op": "delete"})
    assert parrainage.outil_permis("oto_profile", {"op": "update"})
    assert not parrainage.outil_permis("data_write", {})
    assert not parrainage.outil_permis("oto_call", {"name": "data_write"})
    assert not parrainage.outil_permis("higgsfield_act", {"op": "delete"})


def test_reglages_stricts(monkeypatch):
    monkeypatch.delenv("OTO_POST_STUDIO_SPONSOR", raising=False)
    assert not parrainage.ouvert()
    monkeypatch.setenv("OTO_POST_STUDIO_SPONSOR", "1")
    assert parrainage.ouvert()
    monkeypatch.delenv("OTO_POST_STUDIO_THREADS_PER_MONTH", raising=False)
    assert parrainage.threads_par_mois() == 3
    monkeypatch.setenv("OTO_POST_STUDIO_THREADS_PER_MONTH", "trois")
    with pytest.raises(ValueError):
        parrainage.threads_par_mois()
    monkeypatch.setenv("OTO_POST_STUDIO_MAX_MODEL_CALLS", "0")
    with pytest.raises(ValueError):
        parrainage.appels_max()


def test_le_payeur_d_une_etape_parrainee_est_l_operateur():
    from oto_mcp.access import credits, credits_tours
    assert credits_tours._payeur("usr_x", 42, frozenset()) == "org:42"
    parrainage.activer(parrainage.POST_STUDIO)
    assert credits_tours._payeur("usr_x", 42, frozenset()) == parrainage.PAYEUR
    assert credits_tours._payeur("usr_x", None, frozenset()) == parrainage.PAYEUR
    assert credits.payeur("usr_x", 42, frozenset()) == "org:42"   # tools: untouched


# ── the resolver ─────────────────────────────────────────────────────────────

def _impl(mode):
    from oto_mcp.access.resolved_credential import ResolvedCredential

    def impl(provider, want, sub, account=None, check_usage=True, units=1):
        if mode is None:
            from oto_mcp.mcp_errors import McpError
            from mcp.types import ErrorData, INVALID_PARAMS
            raise McpError(ErrorData(code=INVALID_PARAMS, message="grant withdrawn"))
        return ResolvedCredential(provider, "own-or-billed", mode == "platform", mode,
                                  "user", "x")
    return impl


@pytest.mark.parametrize("mode,attendu", [
    ("user", None),                     # their own key wins: used as always, unbilled
    ("org", None),
    ("platform", "sponsor"),            # a re-billed key gives way to the operator's
    ("tenant", "sponsor"),
    (None, "sponsor"),                  # no key at all: the grant was withdrawn
])
def test_cle_parrainee(monkeypatch, mode, attendu):
    from oto_mcp import access, credentials_store
    from oto_mcp.access import resolve_parraine
    monkeypatch.setattr(access, "_resolve_credential_impl", _impl(mode))
    monkeypatch.setattr(credentials_store, "list_platform_instances",
                        lambda p: [{"label": "plat-1"}])
    monkeypatch.setattr(credentials_store, "get_credential",
                        lambda et, label, p: "sk-platform" if label == "plat-1" else None)
    rc = resolve_parraine.resolu("apify", "auto", "usr_x")
    if attendu is None:
        assert rc is None
    else:
        assert rc.mode == "sponsor" and rc.secret == "sk-platform"
        assert rc.is_platform is False         # no platform quota debited after it


def test_sans_cle_de_plateforme_le_chemin_ordinaire_dit_pourquoi(monkeypatch):
    from oto_mcp import access, credentials_store
    from oto_mcp.access import resolve_parraine
    monkeypatch.setattr(access, "_resolve_credential_impl", _impl(None))
    monkeypatch.setattr(credentials_store, "list_platform_instances", lambda p: [])
    assert resolve_parraine.resolu("apify", "auto", "usr_x") is None


def test_le_resolveur_sert_la_cle_parrainee_dans_une_etape_parrainee(monkeypatch):
    from oto_mcp import access
    from oto_mcp.access import resolve_parraine
    from oto_mcp.access.resolved_credential import ResolvedCredential
    servie = ResolvedCredential("apify", "sk-platform", False, "sponsor", "platform", "p")
    monkeypatch.setattr(resolve_parraine, "resolu", lambda p, w, s: servie)
    monkeypatch.setattr(access, "_note_resolved_instance", lambda rc: rc)
    monkeypatch.setattr(access, "_resolve_credential_impl",
                        lambda *a, **k: pytest.fail("the ordinary path must not run"))
    parrainage.activer(parrainage.POST_STUDIO)
    assert access.resolve_credential("apify", sub="usr_x") is servie
    # outside a sponsored leg, the ordinary path runs
    parrainage.activer(None)
    with pytest.raises(BaseException):
        access.resolve_credential("apify", sub="usr_x")


# ── the model call ───────────────────────────────────────────────────────────

def test_la_reservation_parrainee_ignore_la_limite_du_jour(monkeypatch):
    from oto_mcp.capabilities.copilot import cle
    bloc = {"scope": "member", "until": datetime.now(timezone.utc) + timedelta(hours=1)}
    monkeypatch.setattr(cle.limite_du_jour, "refus", lambda sub, org: bloc)
    vus = []
    monkeypatch.setattr(cle.tours, "reserver_tour",
                        lambda *a: vus.append(a) or {"held": True, "reserved": 1})
    k = cle.Cle("sk", None, "platform")
    with pytest.raises(cle.SansCle) as e:
        cle.reserver(k, "copilot:thr_x:000001", "usr_x", 42, "m", 10)
    assert e.value.code == "daily_spend_limit" and not vus
    parrainage.activer(parrainage.POST_STUDIO)
    cle.reserver(k, "copilot:thr_x:000001", "usr_x", 42, "m", 10)
    assert len(vus) == 1


# ── the toolbox ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_hors_de_sa_liste_un_outil_est_refuse(monkeypatch):
    from oto_mcp.capabilities.copilot import outils
    b = outils.Boite(frozenset(), {}, [])
    parrainage.activer(parrainage.POST_STUDIO)
    for name, args in (("data_write", {}), ("oto_trigger", {"op": "run_now"}),
                       ("oto_call", {"name": "data_aggregate", "arguments": {}})):
        issue = await outils.executer(b, "usr_x", name, args, org=1, projet=None,
                                      run_id=None)
        assert not issue.ok and "Post studio" in issue.texte, name


def test_restreindre_garde_la_liste_et_le_point_de_cache():
    from oto_mcp.capabilities.copilot import outils
    b = outils.Boite(frozenset({"a", "data_write", "data_aggregate"}),
                     {"data_write": 1, "data_aggregate": 2, "apify_linkedin_posts": 3},
                     [{"name": "data_aggregate"}, {"name": "apify_linkedin_posts"},
                      {"name": "data_write", "cache_control": {"type": "ephemeral"}}])
    r = outils.restreindre(b, parrainage.OUTILS)
    assert set(r.outils) == {"data_aggregate", "apify_linkedin_posts"}
    assert [o["name"] for o in r.offerts] == ["data_aggregate", "apify_linkedin_posts"]
    assert r.offerts[-1]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in r.offerts[0]
    assert "cache_control" in b.offerts[-1]         # the shared box is untouched


# ── on the served routes ─────────────────────────────────────────────────────

A, B = "usr_parrain_a", "usr_parrain_b"


class _Claims:
    def __init__(self, sub: str):
        self.claims = {"sub": sub, "email": f"{sub}@copilot.invalid", "name": sub}


class _Verifier:
    async def verify_token(self, token: str):
        return _Claims(token)


@pytest.fixture(scope="module")
def monde(pg_dsn):
    psycopg = pytest.importorskip("psycopg")
    from starlette.applications import Starlette
    from starlette.testclient import TestClient
    from oto_mcp import tool_registry
    from oto_mcp.db import _conn as dbconn

    nom = "oto_parrain_" + uuid.uuid4().hex[:8]
    root = psycopg.connect(pg_dsn, autocommit=True)
    root.execute(f'CREATE DATABASE "{nom}"')
    url_avant, pool_avant = os.environ.get("DATABASE_URL"), dbconn._pool
    inst_avant = tool_registry.bound_instance()
    os.environ["DATABASE_URL"] = pg_dsn.rsplit("/", 1)[0] + "/" + nom
    dbconn._pool = None
    try:
        from oto_mcp import db, org_store
        from oto_mcp.db import init_db
        from oto_mcp.server import _build_mcp
        init_db()
        oid = org_store.create_org("Org parrainée", created_by=A)
        for sub in (A, B):
            db.upsert_user(sub, email=f"{sub}@copilot.invalid", name=sub)
            org_store.add_org_member(oid, sub, "org_admin")
            org_store.set_active_org(sub, oid)
        _build_mcp("noauth")
        from oto_mcp.api import routes as api_routes
        client = TestClient(Starlette(routes=api_routes.make_routes(
            _Verifier(), mcp_instance=None)))
        yield {"org": oid, "client": client}
    finally:
        tool_registry.bind(inst_avant)
        if dbconn._pool is not None:
            dbconn._pool.close()
        dbconn._pool = pool_avant
        if url_avant is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = url_avant
        root.execute(f'DROP DATABASE IF EXISTS "{nom}" WITH (FORCE)')
        root.close()


class _Modele:
    def __init__(self):
        self.script: list[dict] = []
        self.requetes: list[dict] = []

    async def flux(self, cle, workspace, corps, releve=None):
        self.requetes.append(corps)
        usage = {"input_tokens": 1, "output_tokens": 1}
        if releve is not None:
            releve["usage"] = usage
        msg = self.script.pop(0)
        for b in msg["content"]:
            if b.get("type") == "text":
                yield ("text", b["text"])
        yield ("end", {**msg, "usage": usage})


@pytest.fixture
def modele(monde, monkeypatch):
    """The model scripted; the platform key the sponsored leg is served, stubbed (the
    real `cle.resoudre` runs: no org key here, so it takes the sponsored branch)."""
    from oto_mcp.access import resolve_parraine
    from oto_mcp.access.resolved_credential import ResolvedCredential
    from oto_mcp.capabilities.copilot import modele as m
    faux = _Modele()
    monkeypatch.setattr(m, "flux", faux.flux)
    monkeypatch.setattr(resolve_parraine, "resolu_traces", lambda p, s: ResolvedCredential(
        p, "sk-platform", False, "sponsor", "platform", "plat-1"))
    monkeypatch.setenv("OTO_COPILOT_SUBS", f"{A},{B}")
    monkeypatch.setenv("OTO_POST_STUDIO_SPONSOR", "1")
    # The model's four lines in the scale, as an instance has them: a sponsored call is
    # booked like any other — to the operator's payer.
    from oto_mcp.access import credits, credits_tours
    from oto_mcp.capabilities.copilot import palier
    from oto_mcp.db import credits as db_credits
    fonction = credits_tours.fonction_du_modele(palier.defaut())
    db_credits.poser_bareme(1, [{"connector": credits_tours.MODELE_CONNECTOR,
                                 "function": fonction, "unit": u, "budget": 1, "per_n": n}
                                for u, n in (("input_tokens", 100), ("output_tokens", 10),
                                             ("cache_creation_input_tokens", 100),
                                             ("cache_read_input_tokens", 1000))])
    credits.oublier()
    return faux


def _evs(r) -> list[dict]:
    assert r.status_code == 200, r.text
    return [json.loads(l[5:]) for l in r.text.split("\n") if l.startswith("data:")]


def _tour(monde, sub, texte, surface="post_studio", thread_id=None):
    page = {"path": "/post-studio", "orgId": monde["org"], "page": "post-studio",
            "title": "Post studio", "params": {}, "surfaces": {}}
    corps = {"thread_id": thread_id, "text": texte, "input": "text", "mode": "act",
             "page_context": page, "client_tools": []}
    if surface:
        corps["surface"] = surface
    return monde["client"].post("/api/copilot/turns", json=corps, headers={
        "Authorization": f"Bearer {sub}", "X-Oto-Org": str(monde["org"])})


def _fin(texte):
    return {"stop_reason": "end_turn", "content": [{"type": "text", "text": texte}]}


def _outil(id_, name, args):
    return {"stop_reason": "tool_use", "content": [
        {"type": "tool_use", "id": id_, "name": name, "input": args}]}


def test_un_tour_parraine_passe_la_limite_du_jour_et_paie_l_operateur(monde, modele,
                                                                     monkeypatch):
    from oto_mcp.access import limite_du_jour
    from oto_mcp.db import copilot as store, credits as db_credits
    bloc = {"scope": "member", "until": datetime.now(timezone.utc) + timedelta(hours=1),
            "group_id": None}
    monkeypatch.setattr(limite_du_jour, "blocage", lambda sub, org: bloc)
    monkeypatch.setattr(limite_du_jour, "refus", lambda sub, org: bloc)
    modele.script = [_outil("tu_1", "data_write", {"namespace": "x", "rows": []}),
                     _fin("Done.")]
    evs = _evs(_tour(monde, A, "Write my post"))
    thread = next(e["thread_id"] for e in evs if e["type"] == "thread")
    assert evs[-1]["type"] == "done"
    assert not any(e["type"] == "error" for e in evs), [e for e in evs if e["type"] == "error"]
    ligne = store.lire(thread, A)
    assert ligne["sponsor"] == parrainage.POST_STUDIO
    # the tool outside its list was refused, by name, to the model
    resultat = json.dumps(modele.requetes[1]["messages"][-1], ensure_ascii=False)
    assert "Post studio" in resultat
    # both model calls booked to the operator, never to the org
    for rang in (1, 2):
        hold = db_credits.reservation(f"copilot:{thread}:{rang:06d}")
        assert hold is not None and hold["payer"] == parrainage.PAYEUR, hold


def test_sans_surface_la_conversation_reste_celle_de_l_org(monde, modele):
    from oto_mcp.db import copilot as store
    modele.script = [_fin("Hi.")]
    evs = _evs(_tour(monde, B, "hello", surface=None))
    thread = next(e["thread_id"] for e in evs if e["type"] == "thread")
    assert store.lire(thread, B)["sponsor"] is None


def test_le_quota_du_mois_est_refuse_par_son_nom(monde, modele, monkeypatch):
    monkeypatch.setenv("OTO_POST_STUDIO_THREADS_PER_MONTH", "1")
    # A already opened one this month (test above)
    r = _tour(monde, A, "once more")
    assert r.status_code == 429 and r.json()["error"] == parrainage.CODE_QUOTA


def test_eteint_aucune_conversation_neuve_n_est_parrainee(monde, modele, monkeypatch):
    from oto_mcp.db import copilot as store
    monkeypatch.setenv("OTO_POST_STUDIO_SPONSOR", "")
    modele.script = [_fin("Hi.")]
    evs = _evs(_tour(monde, B, "hello"))
    thread = next(e["thread_id"] for e in evs if e["type"] == "thread")
    assert store.lire(thread, B)["sponsor"] is None


# ── what one sponsored conversation may buy (`parrainage.QUOTAS`) ────────────────────


def test_les_familles_payantes():
    assert parrainage.famille("higgsfield_act", {"op": "create_generation"}) == "image"
    assert parrainage.famille("higgsfield_act", {"op": "cancel"}) is None
    assert parrainage.famille("apify_linkedin_posts", {}) == "linkedin"
    assert parrainage.famille("waterfall_company", {}) == "company"
    assert parrainage.famille("oto_trigger", {"op": "list"}) is None


@pytest.mark.parametrize("args,refuse", [
    ({"model": "higgsfield-ai/soul/v2/standard", "max_usd": 1}, False),
    ({"model": "higgsfield-ai/soul/v2/standard", "max_usd": 0.4}, False),
    ({"model": "higgsfield-ai/soul/v2/standard", "max_usd": 1.01}, True),
    ({"model": "higgsfield-ai/soul/v2/standard", "max_usd": 0}, True),
    ({"model": "higgsfield-ai/soul/v2/standard"}, True),
    ({"model": "higgsfield-ai/soul/v2/standard", "max_usd": "nan"}, True),
    ({"model": "kling-video/v3.0/pro/image-to-video", "max_usd": 0.5}, True),
    ({"model": "bytedance/seedance-2.5/video-edit", "max_usd": 0.5}, True),
])
def test_l_image_parrainee_est_une_image_d_au_plus_un_dollar(monkeypatch, args, refuse):
    monkeypatch.delenv("OTO_POST_STUDIO_IMAGE_MAX_USD", raising=False)
    assert (parrainage.refus_d_image(args) is not None) is refuse


def test_le_plafond_de_l_image_est_strict(monkeypatch):
    monkeypatch.setenv("OTO_POST_STUDIO_IMAGE_MAX_USD", "0.5")
    assert parrainage.refus_d_image({"model": "soul", "max_usd": 0.6}) is not None
    for brut in ("abc", "0", "-1", "inf"):
        monkeypatch.setenv("OTO_POST_STUDIO_IMAGE_MAX_USD", brut)
        with pytest.raises(ValueError):
            parrainage.image_max_usd()


def test_le_quota_d_une_conversation_se_compte_et_se_rend(monde):
    from oto_mcp.capabilities.copilot import boucle
    from oto_mcp.db import copilot as store
    thread = "th_" + uuid.uuid4().hex
    store.creer(thread, A, monde["org"], None, "sys", None, parrainage.POST_STUDIO)
    fil = {"id": thread}
    image = {"model": "higgsfield-ai/soul/v2/standard", "max_usd": 1}
    assert boucle._quota_parraine(fil, "image", image) is None
    # one image per conversation
    assert boucle._quota_parraine(fil, "image", image) == parrainage.refus_de_quota("image")
    # a failed call gives its count back
    store.rendre_parraine(thread, "image")
    assert boucle._quota_parraine(fil, "image", image) is None
    # a clip is refused before it is counted
    assert "Nothing was sent" in boucle._quota_parraine(
        fil, "image", {"model": "kling-video/v3.0/pro/image-to-video", "max_usd": 1})
    for _ in range(parrainage.QUOTAS["linkedin"]):
        assert boucle._quota_parraine(fil, "linkedin", {}) is None
    assert boucle._quota_parraine(fil, "linkedin", {}) is not None
    assert store.lire(thread, A)["sponsor_uses"] == {
        "image": 1, "linkedin": parrainage.QUOTAS["linkedin"]}
    # giving back what was never counted does nothing
    store.rendre_parraine(thread, "company")
    assert "company" not in store.lire(thread, A)["sponsor_uses"]
