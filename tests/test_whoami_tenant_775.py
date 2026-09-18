"""`oto_whoami` sert désormais le tenant de l'appelant (oto-backend#775, point 3).

Avant ce lot, aucune surface servie ne disait DE QUEL tenant relève l'appelant —
la forme de l'identifiant (`tenant:{slug}:…` sur une instance) était bien servie,
mais pas l'appartenance elle-même. `rung_tenant` est la seule fonction qui sait
répondre (registre en process, aucune lecture DB) : `oto_whoami` la rejoue.

⚠️ Et la rejoue SANS filet, contrairement aux blocs DB voisins. Le premier jet
enveloppait l'appel d'un `try/except` qui rendait `tenant: None` sur échec — or la
description servie donne `None` pour un FAIT (« compte oto ordinaire »). Le filet
faisait donc AFFIRMER à `oto_whoami` quelque chose qu'il n'avait pas résolu, sans que
l'appelant puisse distinguer les deux cas. Il n'amortissait rien au passage :
`rung_tenant` ne fait aucune I/O (classification par préfixe dans le registre du
process). Le banc du bas fige ce choix.
"""
from __future__ import annotations

import asyncio

from oto_mcp import tenancy


def _mount(monkeypatch):
    from fastmcp import FastMCP
    from oto_mcp.tools import whoami as whoami_tool

    monkeypatch.setattr(whoami_tool, "current_user_sub_from_token", lambda: "pilote:u1")

    m = FastMCP("t")
    whoami_tool.register(m)
    fn = asyncio.run(m.get_tool("oto_whoami")).fn
    return fn(ctx=None)


def test_un_compte_d_un_tenant_tiers_voit_son_slug(monkeypatch):
    monkeypatch.setattr(tenancy, "_INSTALLED", tenancy.IssuerRegistry(tenancy.build(
        "https://auth.oto.ninja/oidc",
        tenants=[{"slug": "pilote", "issuer": "https://auth.pilote.test/oidc"}])),
        raising=False)
    out = _mount(monkeypatch)
    assert out["tenant"] == "pilote"


def test_un_compte_nu_ne_relevant_du_tenant_primaire_rend_none(monkeypatch):
    from fastmcp import FastMCP
    from oto_mcp.tools import whoami as whoami_tool

    monkeypatch.setattr(whoami_tool, "current_user_sub_from_token", lambda: "u1")
    m = FastMCP("t")
    whoami_tool.register(m)
    fn = asyncio.run(m.get_tool("oto_whoami")).fn
    out = fn(ctx=None)
    assert out["tenant"] is None


def test_une_resolution_qui_echoue_REMONTE_au_lieu_de_mentir(monkeypatch):
    """`tenant: None` est servi comme un FAIT (« compte oto ordinaire ») : le rendre
    sur une résolution ratée, c'est répondre « tu n'es hébergé par personne » à un
    compte hébergé, sans que rien ne le signale. L'erreur remonte donc — et comme
    `rung_tenant` ne touche ni la base ni le réseau, elle ne peut venir que d'un
    registre cassé, qui doit se voir."""
    import pytest
    from fastmcp import FastMCP
    from oto_mcp import tenant_vault
    from oto_mcp.tools import whoami as whoami_tool

    monkeypatch.setattr(whoami_tool, "current_user_sub_from_token", lambda: "u1")
    def _boom(sub):
        raise RuntimeError("registre indisponible")
    monkeypatch.setattr(tenant_vault, "rung_tenant", _boom)
    m = FastMCP("t")
    whoami_tool.register(m)
    fn = asyncio.run(m.get_tool("oto_whoami")).fn
    with pytest.raises(RuntimeError, match="registre indisponible"):
        fn(ctx=None)
