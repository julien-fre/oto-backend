"""`apollo_match_person` — le quota plateforme échoué au refus SEC, sans dire ce
qui reste ni de délai (oto-backend#710, signaux #311/#312/#313). Ce que ces
tests figent :

1. Sur la clé PLATEFORME, une fois le crédit débité, la réponse porte
   `platform_quota` (`used`/`limit`/`remaining`) — un worker batch peut
   l'inspecter APRÈS chaque appel et s'arrêter avant que le suivant échoue.
2. `platform_quota` est ABSENT sur une clé BYO (pas de plafond) et quand le
   quota est illimité (`platform_quota_hint` rend `None`) — jamais un `null`
   qui se lirait comme « quota à zéro ».
3. Le champ vient de `access.platform_quota_hint`, jamais recalculé ici — un
   second calcul divergerait du refus (`_win_quota`, `resolve.py`).
4. Le docstring documente le champ ET les deux replis officiels (Hunter pour
   l'email, Kaspr/Fullenrich pour le téléphone) par leur nom RÉEL de tool —
   jamais un nom inventé (leçon #tool-description-is-instruction).

Mock la CLASSE client (jamais `requests`) — cf. `tests/test_apollo_location_filters.py`.
"""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest


def _mount(monkeypatch, *, is_platform: bool, hint):
    """Monte apollo.py sur un FastMCP nu, `match_person` mocké, résolution et
    `platform_quota_hint` stubbés — pas d'appel réseau, pas de DB."""
    import oto.tools.apollo.client as apollo_client
    from fastmcp import FastMCP
    from oto_mcp import access
    from oto_mcp.tools import apollo as apollo_tool

    client = MagicMock()
    client.match_person.return_value = {"person": {"id": "p1", "email": "a@b.com"}}
    usage: list[str] = []

    monkeypatch.setattr(access, "resolve_api_key", lambda *a, **k: ("k", is_platform))
    monkeypatch.setattr(access, "record_platform_usage", lambda p: usage.append(p))
    monkeypatch.setattr(access, "platform_quota_hint", lambda p: hint)
    monkeypatch.setattr(apollo_client, "ApolloClient", lambda **kw: client)

    m = FastMCP("t")
    apollo_tool.register(m)
    fn = asyncio.run(m.get_tool("apollo_match_person")).fn
    return fn, usage


def test_platform_quota_attached_on_platform_key(monkeypatch):
    hint = {"used": 5, "limit": 20, "remaining": 15}
    fn, usage = _mount(monkeypatch, is_platform=True, hint=hint)
    result = fn(person_id="p1")
    assert result["platform_quota"] == hint
    assert usage == ["apollo"], "le crédit doit être débité sur la clé plateforme"


def test_platform_quota_absent_when_hint_is_none(monkeypatch):
    """Quota illimité (org `unmetered`, ou registre sans plafond) : `hint` rend
    None — la réponse ne porte PAS un `platform_quota` à moitié rempli."""
    fn, _ = _mount(monkeypatch, is_platform=True, hint=None)
    result = fn(person_id="p1")
    assert "platform_quota" not in result


def test_platform_quota_absent_on_byo_key(monkeypatch):
    """Clé BYO : pas de plafond, pas de débit — le champ n'a rien à dire ici."""
    fn, usage = _mount(monkeypatch, is_platform=False,
                       hint={"used": 1, "limit": 2, "remaining": 1})
    result = fn(person_id="p1")
    assert "platform_quota" not in result
    assert usage == []


def test_docstring_documents_the_quota_field_and_real_fallback_tools():
    """Les noms cités doivent être de VRAIS tools — une description de tool est
    relue à chaque appel comme une instruction (leçon des 3 incidents du 29/08)."""
    from fastmcp import FastMCP
    from oto_mcp.tools import apollo as apollo_tool, hunter as hunter_tool
    from oto_mcp.tools import kaspr as kaspr_tool, fullenrich as fullenrich_tool

    m = FastMCP("t")
    apollo_tool.register(m)
    hunter_tool.register(m)
    kaspr_tool.register(m)
    fullenrich_tool.register(m)
    doc = asyncio.run(m.get_tool("apollo_match_person")).description or ""
    assert "platform_quota" in doc
    for real_tool in ("hunter_email_finder", "kaspr_enrich_linkedin",
                      "fullenrich_enrich_linkedin"):
        assert real_tool in doc
        assert asyncio.run(m.get_tool(real_tool)) is not None, (
            f"{real_tool} cité en repli doit exister réellement dans le registre")
