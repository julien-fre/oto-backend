"""Désactiver un tenant suspend aussi SES ORGS — contre un vrai PostgreSQL (#1165, suite).

Le scénario de la décision : un tenant avec deux orgs, un membre venu d'un autre tenant
dans l'une d'elles, un projet publié sans login dans l'autre, un déclencheur cron et un
webhook ; plus une troisième org du tenant DÉJÀ suspendue pour une autre raison (un essai
fini). Après `disable`, tout est refusé ou n'est plus enfilé ; après `enable`, les orgs
que le geste a suspendues rouvrent, pas celle qu'une autre raison suspendait.

Chaque refus a son contrefactuel : l'org d'un tenant voisin, au même chemin, passe.
"""
from __future__ import annotations

import asyncio

import psycopg
import pytest
from psycopg.rows import dict_row

from oto_mcp.capabilities._types import AuthzDenied, ResolvedCtx


@pytest.fixture()
def base(live, pg_module_dsn, monkeypatch):
    # `live` (portée module) pose `DATABASE_URL` AVANT la fixture automatique qui, sans
    # base, rend toute org active (`conftest._org_active_sans_base`) : la garde lit ici la
    # vraie colonne.
    monkeypatch.setenv("DATABASE_URL", pg_module_dsn)
    monkeypatch.setenv("OTO_MCP_MASTER_KEY", "4" * 64)
    from oto_mcp.db import _conn
    monkeypatch.setattr(_conn, "_database_url", lambda: pg_module_dsn)
    _conn._pool = None
    from oto_mcp import db, org_suspension, server
    db.init_db()
    # Le registre d'émetteurs du processus n'est pas l'objet de ce banc.
    monkeypatch.setattr(server, "reload_tenant_registry", lambda: {})
    org_suspension._cache.update(ids=frozenset(), lu_a=None)
    with psycopg.connect(pg_module_dsn, row_factory=dict_row, autocommit=True) as c:
        for t in ("runner_jobs", "runner_hook_deliveries", "runner_triggers",
                  "projects", "org_members", "user_api_tokens", "sub_aliases", "users"):
            c.execute(f"DELETE FROM {t}")
        c.execute("DELETE FROM orgs")
        c.execute("DELETE FROM tenants WHERE slug <> 'oto'")
        ids = {r["slug"]: r["id"] for r in c.execute(
            "INSERT INTO tenants (slug, name, issuer) VALUES "
            "('acme', 'Acme', 'https://auth.acme.test/oidc'), "
            "('beta', 'Beta', 'https://auth.beta.test/oidc') RETURNING id, slug")}
        orgs = {}
        for nom, tenant in (("a1", "acme"), ("a2", "acme"), ("a3", "acme"),
                            ("b1", "beta")):
            orgs[nom] = c.execute("INSERT INTO orgs (name, tenant_id) VALUES (%s, %s) "
                                  "RETURNING id", (nom, ids[tenant])).fetchone()["id"]
        for sub in ("acme:carla", "beta:fred"):
            c.execute("INSERT INTO users (sub, email) VALUES (%s, %s)",
                      (sub, f"{sub.replace(':', '.')}@ex.test"))
        # Le membre venu d'un AUTRE tenant, dans une org du tenant ET dans la sienne.
        c.execute("INSERT INTO org_members (org_id, sub, org_role) VALUES "
                  "(%s, 'acme:carla', 'org_admin'), (%s, 'beta:fred', 'org_member'), "
                  "(%s, 'beta:fred', 'org_admin')", (orgs["a1"], orgs["a1"], orgs["b1"]))
        # Le projet publié SANS login, dans la seconde org du tenant ; un voisin chez beta.
        for slug, org in (("projet-acme", orgs["a2"]), ("projet-beta", orgs["b1"])):
            c.execute("INSERT INTO projects (owner_type, owner_id, name, mcp_slug, "
                      "mcp_access) VALUES ('org', %s, %s, %s, 'anonymous')",
                      (str(org), slug, slug))
    # La troisième org du tenant, suspendue pour une AUTRE raison, avant le geste.
    from oto_mcp import org_store
    org_store.suspend_org(orgs["a3"], by="svc", reason="trial_ended")
    yield _Scene(db=db, orgs=orgs, tenants=ids)
    _conn._pool = None


class _Scene(dict):
    __getattr__ = dict.__getitem__


def _ctx():
    return ResolvedCtx(sub="op-1", role="super_admin")


def _geste(op, reason=None):
    from oto_mcp.capabilities import tenant_desactivation as td
    return td._disablement(_ctx(), td.TenantDisablementInput(slug="acme", op=op,
                                                             reason=reason))


def _refuse_dans(org_id) -> bool:
    """Les deux gardes d'appel : capacité (adaptateurs) et outil (seam des outils)."""
    from oto_mcp import org_suspension
    from oto_mcp.connectors import activation_gate
    outil = activation_gate._refus_suspendue(org_id, tool="data_write")
    try:
        org_suspension.garde_capacite("me.tools.list",
                                      ResolvedCtx(sub="beta:fred", org_id=org_id))
    except AuthzDenied as e:
        assert e.code == "org_suspended" and outil is not None
        return True
    assert outil is None
    return False


def _anonyme(slug: str) -> tuple[int, str]:
    """Une requête sur l'endpoint publié, par la vraie app ASGI racine (`HostDispatch`)."""
    from oto_mcp.subdomain_project import HostDispatch, _BUCKETS
    _BUCKETS.clear()
    servi = []

    async def app(scope, receive, send):
        servi.append(scope["path"])
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"servi"})

    sortie = []

    async def send(m):
        sortie.append(m)

    async def receive():
        return {"type": "http.request", "body": b""}

    scope = {"type": "http", "method": "POST", "path": "/mcp", "query_string": b"",
             "headers": [(b"host", f"{slug}.mcp.oto.cx".encode())],
             "client": ("203.0.113.9", 1)}
    asyncio.run(HostDispatch(authed_app=app, anon_app=app)(scope, receive, send))
    corps = b"".join(m.get("body", b"") for m in sortie if m["type"] == "http.response.body")
    return sortie[0]["status"], corps.decode()


def _cron(db, org):
    t = db.create_trigger(org, "acme:carla", procedure=f"veille-{org}", tz="UTC",
                          tools=["a"], cron="0 * * * *",
                          next_due="2026-01-01T00:00:00+00:00")
    return t


def _jobs(org) -> int:
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        return conn.execute("SELECT COUNT(*) AS n FROM runner_jobs WHERE org_id = %s",
                            (org,)).fetchone()["n"]


def test_desactiver_ferme_les_orgs_du_tenant_a_tout_le_monde(base):
    from oto_mcp import db, garde_identite, runner_hook, runner_tick
    orgs = base.orgs

    # AVANT : l'org du tenant sert son membre venu d'ailleurs, son projet publié, son cron.
    assert not _refuse_dans(orgs["a1"])
    assert _anonyme("projet-acme") == (200, "servi")
    _cron(db, orgs["a1"]), _cron(db, orgs["b1"])
    assert runner_tick._tick() == 2 and _jobs(orgs["a1"]) == 1
    slugs = {p["mcp_slug"] for p in db.list_published_mcp_projects()}
    assert slugs == {"projet-acme", "projet-beta"}

    out = _geste("disable", "contrat terminé")
    # Le nombre d'orgs suspendues PAR CE GESTE : a1 et a2 — pas a3, déjà suspendue.
    assert out["orgs_suspended"] == 2
    assert sorted(out["orgs_suspended_ids"]) == sorted([orgs["a1"], orgs["a2"]])
    assert out["orgs_already_suspended"] == 1

    # Le membre venu de beta n'est pas coupé comme COMPTE (son tenant est vivant)…
    assert garde_identite.refus("beta:fred") is None
    # …mais l'org du tenant le refuse, aux capacités comme aux outils ; la sienne non.
    assert _refuse_dans(orgs["a1"]) and _refuse_dans(orgs["a2"])
    assert not _refuse_dans(orgs["b1"])

    # Le projet publié n'est plus servi, ni annoncé ; celui du voisin l'est.
    status, corps = _anonyme("projet-acme")
    assert status == 403 and "org_suspended" in corps
    assert _anonyme("projet-beta") == (200, "servi")
    assert {p["mcp_slug"] for p in db.list_published_mcp_projects()} == {"projet-beta"}

    # Le cron n'enfile plus rien pour l'org du tenant ; le voisin, si.
    with psycopg.connect(db._conn._database_url(), autocommit=True) as c:
        c.execute("UPDATE runner_triggers SET next_due = NOW() - interval '1 minute'")
    assert runner_tick._tick() == 1
    assert _jobs(orgs["a1"]) == 1 and _jobs(orgs["b1"]) == 2

    # Le webhook entrant est refusé, nommément.
    hook = db.create_trigger(orgs["a1"], "acme:carla", procedure="hook", tz="UTC",
                             tools=["a"], kind="webhook")
    secret, hache = runner_hook.nouveau_secret()
    db.poser_secret_de_hook(hook["id"], orgs["a1"], hache)
    with pytest.raises(runner_hook.HookRefus) as e:
        runner_hook.declencher(hook["id"], secret, None)
    assert e.value.code == "org_suspended"

    # Le travail déjà en file n'est pas réservé.
    assert db.claim_next_job(None, "w", lease_seconds=60, org_ids=[orgs["a1"]]) is None

    # La trace : qui, et pourquoi — l'origine est le tenant.
    with psycopg.connect(db._conn._database_url(), row_factory=dict_row) as c:
        lignes = c.execute("SELECT id, suspended_by, suspended_reason, suspended_tenant_id "
                           "FROM orgs WHERE tenant_id = %s ORDER BY id",
                           (base.tenants["acme"],)).fetchall()
    par_id = {r["id"]: r for r in lignes}
    assert par_id[orgs["a1"]]["suspended_by"] == "op-1"
    assert par_id[orgs["a1"]]["suspended_reason"] == "tenant acme désactivé : contrat terminé"
    assert par_id[orgs["a1"]]["suspended_tenant_id"] == base.tenants["acme"]
    assert par_id[orgs["a3"]]["suspended_tenant_id"] is None   # l'autre raison, intacte
    assert par_id[orgs["a3"]]["suspended_reason"] == "trial_ended"


def test_reactiver_rouvre_ses_orgs_et_pas_celle_suspendue_pour_autre_chose(base):
    from oto_mcp import db
    orgs = base.orgs
    _geste("disable", "contrat terminé")
    _cron(db, orgs["a1"])
    db.enqueue_job(orgs["a1"], "start", payload={"input": "go", "tools": []})

    out = _geste("enable")
    assert out["changed"] is True and out["orgs_resumed"] == 2
    assert sorted(out["orgs_resumed_ids"]) == sorted([orgs["a1"], orgs["a2"]])

    assert not _refuse_dans(orgs["a1"]) and not _refuse_dans(orgs["a2"])
    assert _refuse_dans(orgs["a3"]), "une suspension d'une autre raison ne se lève pas"
    assert _anonyme("projet-acme") == (200, "servi")
    # Le travail resté en file repart.
    assert db.claim_next_job(None, "w", lease_seconds=60, org_ids=[orgs["a1"]])
    # Réactiver de nouveau ne rouvre rien de plus.
    encore = _geste("enable")
    assert encore["changed"] is False and encore["orgs_resumed"] == 0


def test_rejoue_sur_un_tenant_deja_desactive_rattrape_ses_orgs(base):
    """Le tenant coupé par la version précédente : désactivé, ses orgs ouvertes. Le geste
    rejoué ne réécrit ni l'état ni l'auteur, ne révoque rien de plus — et suspend les orgs
    qui ne le sont pas encore, en le disant."""
    from oto_mcp import db
    orgs = base.orgs
    with psycopg.connect(db._conn._database_url(), autocommit=True) as c:
        c.execute("UPDATE tenants SET disabled_at = NOW(), disabled_by = 'op-0', "
                  "disabled_reason = 'avant le lot' WHERE slug = 'acme'")
    assert not _refuse_dans(orgs["a1"]), "l'état d'avant : orgs ouvertes"

    out = _geste("disable", "rattrapage")
    assert out["changed"] is False
    assert out["disabled_by"] == "op-0" and out["disabled_reason"] == "avant le lot"
    assert out["revoked"] == {}
    assert out["orgs_suspended"] == 2 and out["orgs_already_suspended"] == 1
    assert _refuse_dans(orgs["a1"]) and _refuse_dans(orgs["a2"])

    # Rejoué encore : rien à rattraper, les orgs déjà suspendues par lui ne se recomptent
    # ni comme suspendues par ce geste, ni comme suspendues « pour une autre raison ».
    encore = _geste("disable", "rattrapage")
    assert encore["orgs_suspended"] == 0 and encore["orgs_already_suspended"] == 1

    # Une org NÉE depuis sous ce tenant est rattrapée au geste suivant.
    with psycopg.connect(db._conn._database_url(), row_factory=dict_row,
                         autocommit=True) as c:
        neuve = c.execute("INSERT INTO orgs (name, tenant_id) VALUES ('a4', %s) "
                          "RETURNING id", (base.tenants["acme"],)).fetchone()["id"]
    assert _geste("disable", "rattrapage")["orgs_suspended_ids"] == [neuve]

    # Et `enable` les rouvre toutes, rattrapées comprises.
    assert _geste("enable")["orgs_resumed"] == 3


def test_le_geste_dorg_ne_contourne_pas_le_tenant(base):
    """Tant que le tenant est désactivé, ni le commerce ni un super admin ne rouvrent une
    de ses orgs par le geste d'org ; et une suspension reprise par le geste d'org ne
    tombe pas à la réactivation du tenant."""
    from oto_mcp.capabilities import org_suspension as cap
    orgs = base.orgs
    _geste("disable", "contrat terminé")
    ctx = ResolvedCtx(sub="service:m2m")

    with pytest.raises(AuthzDenied) as e:
        cap._org_suspension(ctx, cap.OrgSuspensionInput(op="resume", org_id=orgs["a1"]))
    assert (e.value.status, e.value.code) == (409, "tenant_disabled")
    # …ni celle qu'une autre raison suspendait (elle rouvrirait sous un tenant coupé).
    with pytest.raises(AuthzDenied):
        cap._org_suspension(ctx, cap.OrgSuspensionInput(op="resume", org_id=orgs["a3"]))
    assert _refuse_dans(orgs["a1"]) and _refuse_dans(orgs["a3"])

    # L'essai de a2 finit PENDANT la désactivation : le commerce la suspend, il la reprend.
    cap._org_suspension(ctx, cap.OrgSuspensionInput(op="suspend", org_id=orgs["a2"],
                                                    reason="trial_ended"))
    out = _geste("enable")
    assert out["orgs_resumed_ids"] == [orgs["a1"]]
    assert not _refuse_dans(orgs["a1"])
    assert _refuse_dans(orgs["a2"]) and _refuse_dans(orgs["a3"])
    # Le tenant réactivé, le geste d'org reprend la main.
    assert cap._org_suspension(ctx, cap.OrgSuspensionInput(
        op="resume", org_id=orgs["a3"]))["changed"] is True
    assert not _refuse_dans(orgs["a3"])
