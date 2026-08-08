"""Le texte d'un accord se demande sur place (signal #346).

`fr_accords_get` ne rendait que des métadonnées, alors que la doctrine et
l'usage attendu (« fr_accords_get pour lire l'accord ») laissaient croire au
contraire : quatre agents sur quatre, pendant un même run, ont dû découvrir
seuls que le texte vivait dans un autre outil. Ça compte au-delà du confort —
les codes de thème sont déclaratifs et incomplets (#357), donc c'est la LECTURE
qui tranche, et il ne faut pas qu'elle dépende d'une trouvaille.

Le texte n'est pas dans l'index local : il est proxy-fetché à l'acte. D'où un
paramètre, pas un champ toujours servi.
"""
from __future__ import annotations

import asyncio

import pytest

_META = {"id": "ACCOTEXT000052404634", "numero": "T03425063599",
         "raison_sociale": "ACME", "theme_codes": ["081", "084"]}
_TEXT = {"texte": "Article 5 - Régime de remboursement complémentaire…",
         "texte_chars": 17194, "tronque": False, "next_offset": None,
         "source_url": "https://www.legifrance.gouv.fr/acco/id/ACCOTEXT000052404634"}


def _call(monkeypatch, **kwargs):
    from fastmcp import FastMCP
    from oto_mcp import fod_ccn, fod_fr
    from oto_mcp.tools import fr as fr_tool

    seen: dict = {}
    monkeypatch.setattr(fod_fr, "get_acco", lambda ref: dict(_META))

    def fake_text(acco_id, offset=0):
        seen["acco_id"] = acco_id
        return dict(_TEXT)

    monkeypatch.setattr(fod_ccn, "accords_text", fake_text)
    m = FastMCP("t")
    fr_tool.register(m)
    fn = asyncio.run(m.get_tool("fr_accords_get")).fn
    return fn(**kwargs), seen


def test_metadata_only_by_default(monkeypatch):
    """Le défaut ne paie pas un appel Légifrance que l'appelant n'a pas demandé."""
    out, seen = _call(monkeypatch, id_or_numero="ACCOTEXT000052404634")
    assert "texte" not in out and seen == {}
    assert out["raison_sociale"] == "ACME"


def test_include_text_returns_the_body(monkeypatch):
    out, seen = _call(monkeypatch, id_or_numero="ACCOTEXT000052404634", include_text=True)
    assert out["texte"].startswith("Article 5")
    assert out["texte_tronque"] is False
    assert out["raison_sociale"] == "ACME"          # les métadonnées restent


def test_text_is_fetched_by_dila_id_not_by_deposit_number(monkeypatch):
    """L'appelant peut nommer l'acte par son numéro de dépôt (T…) — que
    Légifrance ne connaît pas. C'est l'id résolu qui part."""
    _, seen = _call(monkeypatch, id_or_numero="T03425063599", include_text=True)
    assert seen["acco_id"] == "ACCOTEXT000052404634"


def test_unknown_agreement_is_not_a_text_call(monkeypatch):
    from fastmcp import FastMCP
    from oto_mcp import fod_ccn, fod_fr
    from oto_mcp.tools import fr as fr_tool

    called = []
    monkeypatch.setattr(fod_fr, "get_acco", lambda ref: None)
    monkeypatch.setattr(fod_ccn, "accords_text", lambda *a, **k: called.append(a))
    m = FastMCP("t")
    fr_tool.register(m)
    out = asyncio.run(m.get_tool("fr_accords_get")).fn(id_or_numero="ACCOTEXT000", include_text=True)
    assert out["error"] == "not_found" and called == []


@pytest.mark.parametrize("tool,needle", [
    ("fr_accords_search", "NEVER ABSENCE"),
    ("fr_accords_get", "include_text"),
    ("fr_accords_themes", "declarative"),
])
def test_the_contract_says_what_the_codes_cannot_prove(tool, needle):
    """Le contrat LLM porte l'avertissement : les codes de thème prouvent une
    PRÉSENCE, jamais une absence (#357). C'est ce texte que l'agent lit avant de
    conclure « régime dormant » — la recette qui promettait l'inverse a été
    retirée."""
    from fastmcp import FastMCP
    from oto_mcp.tools import fr as fr_tool

    m = FastMCP("t")
    fr_tool.register(m)
    doc = asyncio.run(m.get_tool(tool)).description or ""
    assert needle in doc
    assert "STALE" not in doc          # l'ancienne recette de dormance ne revient pas
