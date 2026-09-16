"""En-têtes de sécurité des pages PUBLIQUES qui portent un secret d'URL (oto-backend#565).

`/p/d/<jeton>` (doc partagé) et `<slug>.share.<D>` (projet partagé) sont servies SANS
authentification — le jeton dans l'URL EST la capacité. Trois surfaces, un même risque :
- **`Referer`** — un clic sur un lien sortant de la page envoie l'URL complète, jeton
  compris, au site tiers, si `Referrer-Policy` ne l'en empêche pas ;
- **cache partagé** — un proxy d'entreprise ou un CDN peut stocker et resservir une
  réponse qui porte un secret d'URL, si `Cache-Control` ne le dit pas `private` ;
- **sniffing / cadrage** — `X-Content-Type-Options: nosniff` et `X-Frame-Options: DENY`
  ferment les deux détours restants (aucun front ne cadre ces pages, vérifié).

Revue du 29/08/2026 : seule la variante HTML du doc partagé (et la page de projet)
posait ces en-têtes — markdown et JSON (les deux variantes que lit un AGENT via
`Accept`, le module l'annonce lui-même) sortaient nus. **Ce banc porte sur les TROIS
variantes de `public_doc_view`**, pas seulement celle qu'on regarde en premier : c'est
l'angle mort qui a laissé passer le trou.
"""
from __future__ import annotations

import asyncio

import pytest
from starlette.requests import Request

_ENTETES_ATTENDUES = {
    "referrer-policy": "no-referrer",
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
}


async def _receive():
    return {"type": "http.request", "body": b"", "more_body": False}


def _req(accept: str, token: str = "jeton") -> Request:
    return Request({"type": "http", "method": "GET", "path": f"/p/d/{token}",
                    "query_string": b"", "root_path": "", "scheme": "https",
                    "server": ("test", 443), "http_version": "1.1",
                    "headers": [(b"accept", accept.encode())],
                    "path_params": {"token": token}}, _receive)


@pytest.fixture
def doc(monkeypatch):
    from oto_mcp import db
    monkeypatch.setattr(db, "get_doc_by_public_token",
                        lambda t: {"title": "Notes", "body_md": "du texte",
                                   "updated_at": None, "owner_type": None, "owner_id": None})


@pytest.mark.parametrize("accept", ["text/html", "text/markdown", "application/json"])
def test_les_trois_variantes_portent_les_memes_entetes_de_securite(doc, accept):
    from oto_mcp.api import public as P

    resp = asyncio.run(P.public_doc_view(_req(accept)))
    for nom, valeur in _ENTETES_ATTENDUES.items():
        assert resp.headers.get(nom) == valeur, f"{accept} : {nom} manquant ou faux"
    cache = (resp.headers.get("cache-control") or "").lower()
    assert "private" in cache, f"{accept} : Cache-Control n'est pas private ({cache!r})"
    assert "public" not in cache, f"{accept} : Cache-Control reste public ({cache!r})"


def test_le_referer_ne_peut_pas_porter_le_jeton_hors_html(doc):
    """Cas concret qui a motivé #565 : la variante markdown (lue par un agent
    WebFetch) manquait justement de `Referrer-Policy` — reprécisé ici seul, pour
    qu'une régression future nomme la bonne variante dans son échec."""
    from oto_mcp.api import public as P

    resp = asyncio.run(P.public_doc_view(_req("text/markdown")))
    assert resp.headers.get("referrer-policy") == "no-referrer"


@pytest.mark.asyncio
async def test_la_page_html_du_projet_partage_porte_les_memes_gardes(monkeypatch):
    """`subdomain_project._send_html` (l'UI navigable d'un projet partagé) — même
    famille de secret d'URL, même garde."""
    from oto_mcp import subdomain_project as sp

    entetes: dict[str, str] = {}

    async def _send(msg):
        if msg["type"] == "http.response.start":
            for k, v in msg["headers"]:
                entetes[k.decode()] = v.decode()

    await sp._send_html(_send, "<html>ok</html>")
    for nom, valeur in _ENTETES_ATTENDUES.items():
        assert entetes.get(nom) == valeur, nom
    assert "private" in entetes.get("cache-control", "")
