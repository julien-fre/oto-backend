"""Suivi des tenants (console plateforme) — `capabilities/tenants_admin.py`.

Ce que ces tests tiennent, dans l'ordre de ce qui coûterait le plus cher à rater :

1. **L'autz est PLATFORM_ADMIN sur les TROIS surfaces.** Le suivi expose la
   configuration d'annuaire des partenaires et la volumétrie de toute la plateforme :
   une surface qui retomberait en `SUB_ONLY` la donnerait à n'importe quel compte.
2. **Le suivi ne peut RIEN écrire.** Déclarer un tenant est un runbook (instance
   Logto dédiée, client OAuth, hosts) et le registre est bâti au boot : une capacité
   d'écriture ferait croire qu'une ligne en base suffit. Le test le vérifie sur le
   registre, pas sur une intention — n'importe quel futur `admin.tenant.*` porteur
   d'un verbe mutant tombe.
3. **L'écart base ↔ process est RENDU** (`loaded` / `pending_restart`) : un tenant
   déclaré mais non redémarré voit ses jetons rejetés tout en paraissant prêt. C'est
   le diagnostic pour lequel cet écran existe.
4. Le dispatch de la console (`op=get` sans slug, 404 d'un slug inconnu, écrêtage de
   la fenêtre) — un 500 sur `days=-1` était le mode de panne d'#300.

Le SQL des compteurs, lui, ne se stube pas : il est exercé contre un vrai PostgreSQL
dans `test_tenants_overview_pg.py` (sauté sans base).
"""
from __future__ import annotations

import pytest

from oto_mcp import tenancy
from oto_mcp.capabilities import tenants_admin as ta
from oto_mcp.capabilities._authz import PLATFORM_ADMIN
from oto_mcp.capabilities._types import AuthzDenied, ResolvedCtx
from oto_mcp.capabilities.registry import CAPABILITIES
from oto_mcp.db import tenants as tenants_db

CTX = ResolvedCtx(sub="operateur", role="super_admin")

# Les lignes stubbées passent par `_shape_tenant` — la même dérivation que la vraie
# lecture (état d'émetteur, compteurs entiers). Stuber la forme SERVIE plutôt que la
# forme BRUTE ferait tester une réponse que le code ne produit jamais.
_OTO = tenants_db._shape_tenant(
    {"id": 1, "slug": "oto", "name": "Oto", "issuer": None, "jwks_uri": None,
     "hosts": [], "oauth_client_id": None, "dashboard_url": None,
     "link_paths": {}, "created_at": "2026-01-01 00:00:00", "orgs": 12,
     "orgs_archivees": 1, "comptes": 40, "comptes_actifs": 9, "appels": 300,
     "dernier_compte_at": None, "last_seen_at": None, "orgs_desalignees": 0})
_TIERS = tenants_db._shape_tenant(
    dict(_OTO, id=2, slug="tulina", name="Tulina",
         issuer="https://auth.tulina.ai/oidc", hosts=["mcp.tulina.ai"],
         orgs=3, comptes=10, comptes_actifs=2, appels=44, orgs_desalignees=2))


def test_the_shape_derives_what_authenticates_from_the_row():
    """Le primaire tient son émetteur de l'ENV (une ligne le redéclarant est ignorée
    par le registre) ; un tenant tiers n'authentifie que s'il en porte un."""
    assert _OTO["primary"] is True
    assert _OTO["issuer_source"] == "env" and _OTO["authenticates"] is True
    assert _TIERS["primary"] is False
    assert _TIERS["issuer_source"] == "db" and _TIERS["authenticates"] is True
    muet = tenants_db._shape_tenant(dict(_TIERS, slug="acme", issuer=None))
    assert muet["issuer_source"] is None and muet["authenticates"] is False


def _caps():
    """Toutes les capacités du domaine tenant, prises AU REGISTRE — pas la liste
    déclarée par ce module : une surface tenant ajoutée ailleurs doit tomber ici."""
    return [c for c in CAPABILITIES if c.key.startswith("admin.tenant")]


# ── 1. autz ─────────────────────────────────────────────────────────────────

def test_every_tenant_surface_is_platform_admin():
    caps = _caps()
    assert {c.key for c in caps} == {"admin.tenants", "admin.tenant",
                                     "admin.tenant_console"}
    for c in caps:
        assert c.authz is PLATFORM_ADMIN, (
            f"{c.key} doit rester PLATFORM_ADMIN : le suivi rend la configuration "
            "d'annuaire des partenaires et la volumétrie de toute la plateforme.")


# ── 2. lecture seule ────────────────────────────────────────────────────────

def test_the_tracking_surface_cannot_write():
    """Aucune face du suivi n'est un verbe d'écriture — ni en REST, ni en MCP.

    Se prouve sur le REGISTRE (ce qui est monté), pas sur les noms de ce module :
    une capacité tenant ajoutée ailleurs, avec un POST, tombe ici aussi.
    """
    for c in _caps():
        verbe = c.rest.verb if c.rest else "GET"
        assert verbe == "GET", (
            f"{c.key} expose {verbe} : déclarer un tenant est un runbook de "
            "provisioning (instance Logto + client OAuth + hosts) et le registre "
            "d'émetteurs est construit AU BOOT — une écriture ici laisserait croire "
            "qu'une ligne en base suffit.")


# ── 3. l'écart base ↔ registre du process ───────────────────────────────────

def _registry(*entries):
    return tenancy.IssuerRegistry(tenancy.build("https://auth.oto.ninja/oidc",
                                                tenants=entries))


def test_a_declared_tenant_absent_from_the_registry_is_flagged(monkeypatch):
    """Le cas qui motive l'écran : la ligne existe, le process ne l'a pas chargée
    (déclarée après le dernier boot) — donc ses jetons sont rejetés."""
    monkeypatch.setattr(ta.db, "list_tenants_overview", lambda **kw: [dict(_OTO), dict(_TIERS)])
    monkeypatch.setattr(tenancy, "_INSTALLED", _registry(), raising=False)

    par_slug = {t["slug"]: t for t in ta._tenants(CTX, ta.TenantsInput())["tenants"]}
    assert par_slug["tulina"]["pending_restart"] is True
    assert par_slug["tulina"]["loaded"] is False
    # Le primaire tient son émetteur de l'env : il est chargé, jamais « en attente ».
    assert par_slug["oto"]["loaded"] is True
    assert par_slug["oto"]["pending_restart"] is False


def test_a_loaded_tenant_reports_the_hosts_the_process_serves(monkeypatch):
    monkeypatch.setattr(ta.db, "list_tenants_overview", lambda **kw: [dict(_TIERS)])
    monkeypatch.setattr(tenancy, "_INSTALLED", _registry(
        {"slug": "tulina", "issuer": "https://auth.tulina.ai/oidc",
         "hosts": ["mcp.tulina.ai"]}), raising=False)

    t = ta._tenants(CTX, ta.TenantsInput())["tenants"][0]
    assert t["loaded"] is True and t["pending_restart"] is False
    assert t["live_hosts"] == ["mcp.tulina.ai"]


def test_a_tenant_without_an_issuer_does_not_authenticate(monkeypatch):
    """Une ligne sans émetteur n'authentifie personne — et ce n'est PAS un
    « redémarrage en attente » : il n'y a rien à charger."""
    orphelin = tenants_db._shape_tenant(dict(_TIERS, slug="acme", issuer=None, hosts=[]))
    monkeypatch.setattr(ta.db, "list_tenants_overview", lambda **kw: [orphelin])
    monkeypatch.setattr(tenancy, "_INSTALLED", _registry(), raising=False)

    t = ta._tenants(CTX, ta.TenantsInput())["tenants"][0]
    assert t["authenticates"] is False
    assert t["pending_restart"] is False


def test_totals_are_summed_from_the_rows(monkeypatch):
    monkeypatch.setattr(ta.db, "list_tenants_overview", lambda **kw: [dict(_OTO), dict(_TIERS)])
    out = ta._tenants(CTX, ta.TenantsInput())
    assert out["totals"] == {"tenants": 2, "orgs": 15, "comptes": 50,
                             "comptes_actifs": 11, "appels": 344}


# ── 4. dispatch de la console ───────────────────────────────────────────────

def test_get_requires_a_slug():
    with pytest.raises(AuthzDenied) as e:
        ta._console(CTX, ta.TenantConsoleInput(op="get"))
    assert e.value.code == "missing_slug"


def test_an_unknown_slug_is_a_404_not_an_empty_sheet(monkeypatch):
    """Une fiche vide se lirait « ce tenant existe et n'a rien » — le contraire du
    vrai diagnostic (« ce tenant n'existe pas »)."""
    monkeypatch.setattr(ta.db, "get_tenant_overview", lambda slug, **kw: None)
    with pytest.raises(AuthzDenied) as e:
        ta._console(CTX, ta.TenantConsoleInput(op="get", slug="fantome"))
    assert e.value.status == 404 and e.value.code == "unknown_tenant"


def test_the_window_is_clamped_never_a_500(monkeypatch):
    """`days` part au SQL : une valeur négative faisait échouer la requête (#300)."""
    vues = []
    monkeypatch.setattr(ta.db, "list_tenants_overview",
                        lambda **kw: vues.append(kw["days"]) or [])
    ta._console(CTX, ta.TenantConsoleInput(op="list", days=-5))
    ta._console(CTX, ta.TenantConsoleInput(op="list", days=99999))
    ta._console(CTX, ta.TenantConsoleInput(op="list"))
    assert vues == [1, 365, 30]


def test_get_passes_the_slug_and_window_through(monkeypatch):
    vu = {}
    monkeypatch.setattr(ta.db, "get_tenant_overview",
                        lambda slug, **kw: vu.update(slug=slug, **kw) or dict(_TIERS))
    monkeypatch.setattr(tenancy, "_INSTALLED", _registry(), raising=False)
    out = ta._console(CTX, ta.TenantConsoleInput(op="get", slug="tulina", days=7))
    assert vu == {"slug": "tulina", "days": 7}
    assert out["tenant"]["slug"] == "tulina" and out["days"] == 7
