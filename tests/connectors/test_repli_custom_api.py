"""Un outil sans connecteur : le refus « nom inconnu » propose le Custom API (`http`).

Le fait : une cliente n'a trouvé ni comment brancher son outil de paie (absent du
catalogue, mais doté d'une API à clé), ni comment partager sa clé sans la coller dans
une conversation. Le refus de `oto_connector(name=…)` sait désormais dire l'issue — SI
l'org atteint `http` — avec le lien de la carte pré-remplie du nom cherché, jamais un
secret, construit sur la page connecteurs du produit de l'appelant (`links`).
"""
from __future__ import annotations

import pytest

from oto_mcp import links, providers
from oto_mcp.capabilities._types import AuthzDenied, ResolvedCtx
from oto_mcp.capabilities.connectors import selection as CS

_CTX = ResolvedCtx(sub="u-repli", org_id=7)


def _ligne(name: str) -> dict:
    return next(c for c in providers.public_catalog() if c["name"] == name)


def _refus(catalog, demande="Acme Paie") -> AuthzDenied:
    return CS._refus_nom_inconnu(catalog, demande, _CTX)


def test_http_atteignable_le_refus_donne_le_lien_de_la_carte(monkeypatch):
    vus = []

    def _link_for(kind, sub=None, **p):
        vus.append((kind, sub, p))
        return "https://app.example.test/connectors"
    monkeypatch.setattr(links, "link_for", _link_for)
    e = _refus([_ligne("http"), _ligne("folk")])
    assert e.code == "unknown_connector"
    url = "https://app.example.test/connectors/http?label=Acme+Paie"
    assert e.details["custom_api"] == {"connector": "http", "setup_url": url}
    assert url in e.message and "never ask for the key in the chat" in e.message
    # Le lien suit le produit de l'APPELANT : son sub et son org.
    assert vus == [("connectors", "u-repli", {"org": 7})]


def test_http_hors_de_portee_aucune_issue_promise(monkeypatch):
    monkeypatch.setattr(links, "link_for", lambda *a, **k: "https://x.test/connectors")
    e = _refus([_ligne("folk")])
    assert "custom_api" not in e.details and "Custom API" not in e.message


def test_sans_page_connecteurs_l_issue_reste_nommee_sans_lien(monkeypatch):
    monkeypatch.setattr(links, "link_for", lambda *a, **k: None)
    e = _refus([_ligne("http")])
    assert e.details["custom_api"]["setup_url"] is None
    assert "`http_setup_link`" in e.message


def test_le_connecteur_se_presente_en_custom_api():
    c = providers.REGISTRY["http"]
    assert c.label == "Custom API" and c.category == "Custom"
    assert "never in a chat" in c.help
    assert _ligne("http")["name"] == "http"


@pytest.mark.parametrize("cherche", ["custom api", "Custom API"])
def test_le_libelle_trouve_le_connecteur(cherche):
    assert [c["name"] for c in CS._resoudre_nom([_ligne("http"), _ligne("folk")],
                                                 cherche)] == ["http"]
