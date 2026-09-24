"""Crunchbase : une session expirée se VOIT sur la fiche, pas seulement dans les runs.

Signaux #1070, #1076, #1149, #1163 (org cliente, 18→24/09/2026) : la session
navigateur Crunchbase avait expiré ; chaque run d'agent recevait le refus, le
signalait, continuait sans la source — et personne d'autre ne le voyait, pendant six
jours. La reconnexion est HUMAINE (login dans la Live View) : rien ne peut la faire
à sa place. Ce qui manquait, c'est que la personne qui peut agir l'apprenne : un
401/403 marque désormais la ligne de coffre RÉELLEMENT servie (`health_ko`), que la
fiche du connecteur lit (« à reconnecter »).

Aucun appel réel : Browserbase et la résolution du coffre sont stubbés.
"""
from __future__ import annotations

import asyncio

import pytest

from oto_mcp.access.resolved_credential import ResolvedCredential
from oto_mcp.mcp_errors import McpError
from oto_mcp.tools import crunchbase as cb


@pytest.fixture
def banc(monkeypatch):
    marques: list = []
    rc = ResolvedCredential(provider="crunchbase", secret="ctx-123", is_platform=False,
                            mode="user", entity_type="member", entity_id="7:sub-x",
                            account="")
    monkeypatch.setattr(cb.browserbase, "is_configured", lambda: True)
    monkeypatch.setattr(cb.access, "resolve_credential", lambda *a, **k: rc)
    monkeypatch.setattr(cb.connector_health, "mark_rejected",
                        lambda *a: marques.append(a))
    reponse: dict = {}

    async def _fetch(ctx_id, method, path, body, base, app):
        assert ctx_id == "ctx-123"
        return reponse

    monkeypatch.setattr(cb.browserbase, "run_fetch", _fetch)
    return reponse, marques


@pytest.mark.parametrize("statut", [401, 403])
def test_une_session_expiree_marque_la_ligne_servie_et_le_dit(banc, statut):
    reponse, marques = banc
    reponse.update(status=statut, data=None)
    with pytest.raises(McpError) as e:
        asyncio.run(cb._api("GET", "/autocompletes?query=x"))
    assert marques == [("member", "7:sub-x", "crunchbase", "",
                        f"session expirée (HTTP {statut})")]
    msg = str(e.value)
    assert "crunchbase_connect_start" in msg
    assert "aucune reconnexion automatique" in msg
    assert "continue sans cette source" in msg


def test_une_reponse_normale_ne_marque_rien(banc):
    reponse, marques = banc
    reponse.update(status=200, data={"ok": True})
    assert asyncio.run(cb._api("GET", "/autocompletes?query=x")) == {"ok": True}
    assert marques == []


def test_une_panne_serveur_n_est_pas_une_session_morte(banc):
    """Un 500 ne dit rien de la session : la marquer peindrait en rouge une session
    vivante sur un hoquet de Crunchbase."""
    reponse, marques = banc
    reponse.update(status=500, data="boom")
    with pytest.raises(McpError):
        asyncio.run(cb._api("GET", "/autocompletes?query=x"))
    assert marques == []
