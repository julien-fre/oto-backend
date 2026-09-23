"""#1058 — la boîte d'une session de FLOTTE suit l'org de la MISSION, pas la maison.

Incident du 22/09 : la maison du compte porteur des workers de flotte a basculé
(2 → 226, sans déploiement, écriture `org_members.is_active`). À l'`initialize` MCP,
aucun jeton d'appel n'existe encore (`_org=`/`_run_id=` n'arrivent qu'au premier
`call_tool`) : `session_visibility` dérivait donc TOUJOURS la boîte de la maison —
tous les connecteurs de toutes les flottes du compte ont disparu pendant ~2h, alors
que les APPELS de ces mêmes flottes continuaient de s'exécuter sous l'org réelle de
la mission. Seule la VISIBILITÉ suivait la maison ; jamais l'exécution.

⚠️ Premier essai (#1060, retiré) : porter `X-Oto-Run` et résoudre l'org via le run.
Faux — le client oto-runner ouvre sa session `initialize` AVANT `run_start` (`mcp.py`
appelle `_ouvrir()` dans `__init__`, le run n'existe qu'ensuite). Ce banc rejoue donc
l'ORDRE RÉEL : `initialize` (avec `X-Oto-Org`, connue dès la construction du client,
cf. `self.org` dans oto-runner) **PUIS** un éventuel `run_start` — jamais l'inverse.

Trois niveaux, du plus bas au plus haut :
1. `disabled_tools._org_de_mission` — pure, fail-open (jamais de refus au handshake).
2. `session_visibility.compute_hidden_tools(..., org=...)` — déjà couvert ailleurs
   (`test_agent_toolbox.py`, `test_kit_*`) ; pas reproduit ici.
3. `UserDisabledToolsMiddleware.on_initialize` — le banc demandé par oto cd : la boîte
   d'une session de flotte reste IDENTIQUE quand la maison du compte bascule pendant
   que la flotte tourne, ET une session SANS l'en-tête n'est pas affectée.

Logique pure : aucune base, aucun réseau (convention CLAUDE.md §Tests).
"""
from __future__ import annotations

import types

import mcp.types as mt
import pytest

from oto_mcp import org_store, roles
from oto_mcp.middleware import disabled_tools as DT


# ── 1. `_org_de_mission` — pure, fail-open ──────────────────────────────────────

@pytest.mark.asyncio
async def test_sans_en_tete_rien_nest_resolu(monkeypatch):
    monkeypatch.setattr(DT, "get_http_headers", lambda **_kw: {})
    assert await DT._org_de_mission("sub-flotte") is None


@pytest.mark.asyncio
async def test_avec_en_tete_et_appartenance_lorg_est_rendue(monkeypatch):
    monkeypatch.setattr(DT, "get_http_headers", lambda **_kw: {"x-oto-org": "226"})
    monkeypatch.setattr(roles, "is_org_member", lambda sub, org: org == 226)
    assert await DT._org_de_mission("sub-flotte") == 226


@pytest.mark.asyncio
@pytest.mark.parametrize("brut", ["", "  ", "abc", "0", "-1", "perso"])
async def test_en_tete_mal_forme_ou_sentinelle_rend_none(monkeypatch, brut):
    monkeypatch.setattr(DT, "get_http_headers", lambda **_kw: {"x-oto-org": brut})
    called = []
    monkeypatch.setattr(roles, "is_org_member", lambda sub, org: called.append(org) or True)
    assert await DT._org_de_mission("sub-flotte") is None
    assert called == [], "aucun appel d'appartenance sur un en-tête déjà mal formé"


@pytest.mark.asyncio
async def test_non_membre_ne_leve_pas_rend_none(monkeypatch):
    """Fail-open, à la différence de `pin_for_call` (call-time, #639) : au handshake,
    on ne refuse jamais — on retombe sur la dérivation historique (maison), jamais une
    fuite de la boîte d'une org à laquelle le sub n'appartient pas."""
    monkeypatch.setattr(DT, "get_http_headers", lambda **_kw: {"x-oto-org": "226"})
    monkeypatch.setattr(roles, "is_org_member", lambda sub, org: False)
    assert await DT._org_de_mission("sub-flotte") is None


@pytest.mark.asyncio
async def test_appartenance_qui_tousse_ne_leve_pas_rend_none(monkeypatch):
    def _boom(sub, org):
        raise RuntimeError("pool timeout")
    monkeypatch.setattr(DT, "get_http_headers", lambda **_kw: {"x-oto-org": "226"})
    monkeypatch.setattr(roles, "is_org_member", _boom)
    assert await DT._org_de_mission("sub-flotte") is None


# ── 2. Le banc demandé par oto cd : boîte de flotte INCHANGÉE quand la maison bascule ──

def _ctx_init():
    return types.SimpleNamespace(message=mt.InitializeRequest(
        method="initialize",
        params=mt.InitializeRequestParams(
            protocolVersion="2025-11-25",
            capabilities=mt.ClientCapabilities(),
            clientInfo=mt.Implementation(name="oto-runner", version="0.1"))))


async def _call_next(_ctx):
    return None


@pytest.mark.asyncio
async def test_la_boite_dune_session_de_flotte_ne_bouge_pas_quand_la_maison_bascule(monkeypatch):
    """Le banc demandé par oto cd (mesuré ensuite en préprod). Deux handshakes
    successifs de la MÊME mission (même `X-Oto-Org`) — l'ordre réel du runner,
    `initialize` avant tout `run_start` : entre les deux, la maison du compte
    porteur bascule (2 → 999, comme le 22/09) — la boîte posée doit être identique
    aux deux passages."""
    monkeypatch.setattr(DT, "current_user_sub_from_token", lambda: "sub-flotte")
    monkeypatch.setattr(DT, "get_http_headers", lambda **_kw: {"x-oto-org": "226"})
    monkeypatch.setattr(roles, "is_org_member", lambda sub, org: True)

    poses = []

    async def _capture(ctx, sub, **kwargs):
        poses.append(kwargs.get("org"))

    monkeypatch.setattr(DT, "apply_session_visibility", _capture)

    maison = {"org": 2}
    monkeypatch.setattr(org_store, "get_active_org", lambda sub: maison["org"])

    mw = DT.UserDisabledToolsMiddleware()
    context = types.SimpleNamespace(message=_ctx_init().message, fastmcp_context=object())
    await mw.on_initialize(context, _call_next)

    maison["org"] = 999  # bascule de la maison EN COURS DE VOL, comme le 22/09
    await mw.on_initialize(context, _call_next)

    assert poses == [226, 226], (
        f"la boîte de la flotte a suivi la maison au lieu de l'org de la mission : {poses}")


@pytest.mark.asyncio
async def test_session_sans_en_tete_derive_la_maison_comme_avant(monkeypatch):
    """Non-régression humaine/dashboard : aucun `X-Oto-Org` sur la requête
    `initialize` → `org` n'est PAS passé à `apply_session_visibility` (dérive de la
    maison, comportement identique à avant #1058)."""
    monkeypatch.setattr(DT, "current_user_sub_from_token", lambda: "sub-humain")
    monkeypatch.setattr(DT, "get_http_headers", lambda **_kw: {})

    poses = []

    async def _capture(ctx, sub, **kwargs):
        poses.append(kwargs)

    monkeypatch.setattr(DT, "apply_session_visibility", _capture)

    mw = DT.UserDisabledToolsMiddleware()
    context = types.SimpleNamespace(message=_ctx_init().message, fastmcp_context=object())
    await mw.on_initialize(context, _call_next)

    assert poses == [{}], f"un en-tête absent ne doit poser aucun override d'org : {poses}"


@pytest.mark.asyncio
async def test_en_tete_pose_mais_sub_non_membre_derive_aussi_la_maison(monkeypatch):
    """Fail-open bout en bout au niveau middleware : `X-Oto-Org` pointe une org dont
    le sub n'est pas membre → pas de fuite, dérive de la maison."""
    monkeypatch.setattr(DT, "current_user_sub_from_token", lambda: "sub-flotte")
    monkeypatch.setattr(DT, "get_http_headers", lambda **_kw: {"x-oto-org": "226"})
    monkeypatch.setattr(roles, "is_org_member", lambda sub, org: False)

    poses = []

    async def _capture(ctx, sub, **kwargs):
        poses.append(kwargs)

    monkeypatch.setattr(DT, "apply_session_visibility", _capture)

    mw = DT.UserDisabledToolsMiddleware()
    context = types.SimpleNamespace(message=_ctx_init().message, fastmcp_context=object())
    await mw.on_initialize(context, _call_next)

    assert poses == [{}]
