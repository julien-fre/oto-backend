"""`oto_whoami` — visibilité du quota plateforme AVANT la dépense (oto-backend#710,
signaux #311/#312/#313) : « aucun moyen de connaître le quota restant AVANT
d'appeler ». `oto_whoami` dit déjà « appelle-la ... avant une action sensible
(... dépense de crédits) » — ce que `connectors.platform_quotas` tient enfin.

Ce qui est gardé :
1. `platform_quotas` réutilise TEL QUEL `quota_used_today`/`quota_daily` de
   `status_for` (déjà calculé pour ce même appel) — aucune deuxième marche de
   cascade sur ce chemin.
2. Absent pour un connecteur sans plafond (`quota_daily` None/0) : un connecteur
   `platform_ready` sans quota reste listé, juste sans entrée `platform_quotas`.
3. Un connecteur `over_quota` reste dans `platform_available` (il ne DISPARAÎT
   pas — avant ce lot, il n'apparaissait dans AUCUNE des deux listes une fois
   épuisé, ce qui se lisait « pas configuré »).
"""
from __future__ import annotations

import asyncio

import pytest


def _mount(monkeypatch, providers: dict):
    from fastmcp import FastMCP
    from oto_mcp import access
    from oto_mcp.tools import whoami as whoami_tool

    monkeypatch.setattr(whoami_tool, "current_user_sub_from_token", lambda: "u")
    monkeypatch.setattr(access, "status_for", lambda sub: {"providers": providers})

    m = FastMCP("t")
    whoami_tool.register(m)
    fn = asyncio.run(m.get_tool("oto_whoami")).fn
    return fn(ctx=None)


def test_platform_quota_surfaced_for_capped_connector(monkeypatch):
    out = _mount(monkeypatch, {
        "apollo": {"mode": "platform", "quota_used_today": 5, "quota_daily": 20},
    })
    assert out["connectors"]["platform_available"] == ["apollo"]
    assert out["connectors"]["platform_quotas"] == {
        "apollo": {"used": 5, "limit": 20, "remaining": 15},
    }


def test_no_quota_entry_for_uncapped_platform_connector(monkeypatch):
    """`quota_daily` None (illimité, ou org `unmetered`) : listé, sans chiffre
    inventé à côté."""
    out = _mount(monkeypatch, {
        "serper": {"mode": "platform", "quota_used_today": 0, "quota_daily": None},
    })
    assert out["connectors"]["platform_available"] == ["serper"]
    assert out["connectors"]["platform_quotas"] == {}


def test_over_quota_connector_stays_listed_instead_of_vanishing(monkeypatch):
    """Avant ce lot, `mode="over_quota"` ne tombait dans AUCUNE des deux listes —
    un agent qui vient d'épuiser Apollo le voyait disparaître de `oto_whoami`
    plutôt que d'y lire 0 restant."""
    out = _mount(monkeypatch, {
        "apollo": {"mode": "over_quota", "quota_used_today": 20, "quota_daily": 20},
    })
    assert out["connectors"]["configured"] == []
    assert out["connectors"]["platform_available"] == ["apollo"]
    assert out["connectors"]["platform_quotas"]["apollo"]["remaining"] == 0


def test_configured_connector_never_gets_a_quota_entry(monkeypatch):
    """Une clé BYO n'a pas de plafond plateforme — `platform_quotas` ne la cite
    jamais, même si `status_for` portait par erreur un `quota_daily`."""
    out = _mount(monkeypatch, {
        "folk": {"mode": "user", "quota_used_today": 0, "quota_daily": 999},
    })
    assert out["connectors"]["configured"] == ["folk"]
    assert out["connectors"]["platform_quotas"] == {}
