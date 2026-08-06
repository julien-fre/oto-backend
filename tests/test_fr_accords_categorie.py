"""Filtre de catégorie d'entreprise sur fr_accords_search (signal #359).

ACCO ne porte pas la taille de l'entreprise, et le stock ÉTABLISSEMENT ne porte
pas son appartenance à un groupe : une filiale peut être petite par l'effectif de
son établissement et rester une GE au sens INSEE (catégorie calculée sur le
périmètre groupe). Le filtre traverse donc le backend jusqu'au service FOD, qui
croise le stock UNITÉ LÉGALE.

Ce qui est gardé ici : que les deux paramètres atteignent VRAIMENT le service.
Un paramètre avalé en route rendrait un résultat non filtré qui passerait pour
filtré — le mode de panne qu'on ne voit pas (cf. `aiark_company_search(account=)`).
"""
from __future__ import annotations

from oto_mcp import fod_fr


def _capture(monkeypatch) -> dict:
    """Intercepte le corps POST envoyé au service FOD."""
    sent: dict = {}

    def fake_post(path, body):
        sent["path"] = path
        sent["body"] = body
        return {"results": [], "total_count": 0}

    monkeypatch.setattr(fod_fr, "_post", fake_post)
    return sent


def test_exclude_categories_reaches_the_service(monkeypatch):
    sent = _capture(monkeypatch)
    fod_fr.search_acco(themes=["111", "112"], exclude_categories=["GE"])
    assert sent["path"] == "/api/fr/accords/search"
    assert sent["body"]["exclude_categories"] == ["GE"]
    assert sent["body"]["themes"] == ["111", "112"]


def test_categories_entreprise_reaches_the_service(monkeypatch):
    sent = _capture(monkeypatch)
    fod_fr.search_acco(categories_entreprise=["PME", "ETI"])
    assert sent["body"]["categories_entreprise"] == ["PME", "ETI"]


def test_both_filters_default_to_none(monkeypatch):
    """Absents = None, pas [] : le service distingue « pas de filtre » d'une
    liste vide, et un [] qui deviendrait un filtre vide ne renverrait rien."""
    sent = _capture(monkeypatch)
    fod_fr.search_acco(siren="443975933")
    assert sent["body"]["categories_entreprise"] is None
    assert sent["body"]["exclude_categories"] is None


def test_tool_signature_exposes_both_filters():
    """Le tool MCP doit exposer les deux paramètres — le service peut bien les
    servir, si la signature du tool ne les porte pas l'agent ne peut pas s'en
    servir, et c'est exactement le gap remonté."""
    import inspect

    from oto_mcp.tools import fr as fr_tools

    src = inspect.getsource(fr_tools.register)
    sig_start = src.index("def fr_accords_search(")
    sig_end = src.index(") -> dict:", sig_start)
    signature = src[sig_start:sig_end]
    assert "exclude_categories" in signature
    assert "categories_entreprise" in signature
