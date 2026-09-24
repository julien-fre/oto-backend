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


# ── Content-Security-Policy : la liste de ce que chaque page charge (#565) ──────
#
# Une CSP mal réglée CASSE la page qu'elle protège, en silence : le navigateur refuse
# la ressource et le dit dans sa console, que personne ne lit. Chaque page servie est
# donc rendue ici et recoupée avec SA politique — scripts en ligne (empreinte ou
# `'unsafe-inline'`), gestionnaires en attribut, feuilles, polices, images, `fetch`,
# formulaires. Une ressource ajoutée à une page sans que sa politique suive fait
# rougir ce banc, pas la page d'un lecteur.

import base64
import hashlib
import re


def _directives(csp: str) -> dict[str, list[str]]:
    out = {}
    for part in csp.split(";"):
        mots = part.split()
        if mots:
            out[mots[0]] = mots[1:]
    return out


def _source_admise(url: str, sources: list[str]) -> bool:
    if url.startswith("data:"):
        return "data:" in sources
    if url.startswith("https://"):
        hote = "https://" + url[len("https://"):].split("/")[0]
        return "https:" in sources or hote in sources
    if re.match(r"^[a-z][a-z0-9+.-]*:", url):
        return url.split(":", 1)[0] + ":" in sources   # autre schéma (http:…)
    return "'self'" in sources   # relative ⟹ même origine


def _ecarts(page: str, csp: str) -> list[str]:
    """Ce que `page` charge et que `csp` refuserait — vide si la page tient."""
    d = _directives(csp)
    assert d.get("default-src") == ["'none'"], "tout ce qui n'est pas nommé est refusé"
    scripts = d.get("script-src", [])
    styles = d.get("style-src", [])
    ecarts = []
    for corps in re.findall(r"<script>(.*?)</script>", page, re.S):
        emp = "'sha256-" + base64.b64encode(
            hashlib.sha256(corps.encode()).digest()).decode() + "'"
        if emp not in scripts and "'unsafe-inline'" not in scripts:
            ecarts.append("script en ligne non autorisé")
    if re.search(r"<script[^>]*\bsrc=", page):
        ecarts.append("script externe")
    if re.search(r"\son[a-z]+=", page) and "'unsafe-inline'" not in scripts:
        ecarts.append("gestionnaire en attribut sans 'unsafe-inline'")
    if ("<style" in page or " style=" in page) and "'unsafe-inline'" not in styles:
        ecarts.append("style en ligne")
    for href in re.findall(r'<link rel=stylesheet href="([^"]+)"', page):
        if not _source_admise(href, styles):
            ecarts.append(f"feuille {href}")
    if "fonts.googleapis.com" in page and "https://fonts.gstatic.com" not in d.get("font-src", []):
        ecarts.append("polices Google")
    for src in (re.findall(r'<img[^>]*\bsrc="([^"]+)"', page)
                + re.findall(r'<link rel=icon[^>]*href="([^"]+)"', page)):
        if not _source_admise(src, d.get("img-src", [])):
            ecarts.append(f"image {src[:40]}")
    if "fetch(" in page and "'self'" not in d.get("connect-src", []):
        ecarts.append("fetch")
    if "<form" in page and d.get("form-action", ["'none'"]) == ["'none'"]:
        ecarts.append("formulaire")
    return ecarts


_MD_RICHE = ("# Titre\n\nUn [lien](https://exemple.org) et une image "
             "![logo](https://exemple.org/l.png).\n\n| a | b |\n|---|---|\n| 1 | 2 |\n")


def test_la_page_de_doc_tient_sous_sa_politique():
    from oto_mcp import public_doc_page
    from oto_mcp.entetes_securite import CSP_DOC_PUBLIC
    for page in (public_doc_page.render(title="Notes", body_md=_MD_RICHE),
                 public_doc_page.render_missing()):
        assert _ecarts(page, CSP_DOC_PUBLIC) == []
    # Aucun script sur cette page : la politique n'en admet aucun.
    assert "script-src" not in CSP_DOC_PUBLIC


def test_les_pages_du_projet_publie_tiennent_sous_leur_politique():
    from oto_mcp import share_ui
    from oto_mcp.entetes_securite import CSP_PROJET_PUBLIE
    index = share_ui.render_index(
        name="P", brief_md=_MD_RICHE, procedures=[], tables=[], docs=[],
        connect_url="https://p.mcp.oto.cx",
        connectors=[{"name": "zoho", "label": "Zoho", "logo": "https://cdn.exemple/z.svg",
                     "href": "https://manage.oto.cx/c", "tool_count": 3}])
    data = share_ui.render_data(name="P", namespace="t", columns=["a", "b"],
                                rows=[{"a": 1, "b": "x"}], total=1, offset=0)
    prose = share_ui.render_prose(name="P", title="Doc", body_md=_MD_RICHE,
                                  kind_label="page")
    for page in (index, data, prose, share_ui.render_not_found(name="P")):
        assert _ecarts(page, CSP_PROJET_PUBLIE) == []


def test_le_formulaire_d_upload_tient_sous_sa_politique_et_son_script_passe():
    from oto_mcp.api import uploads as U
    from oto_mcp.entetes_securite import csp_upload
    csp = csp_upload(U._SCRIPT_UPLOAD)
    for page in (U._upload_page_html("Projet 59 / devis.pdf"), U._upload_page_html(None)):
        assert _ecarts(page, csp) == []
    # Le script est admis par son EMPREINTE, pas par `'unsafe-inline'`.
    assert "'unsafe-inline'" not in _directives(csp)["script-src"]


def test_une_ressource_ajoutee_sans_suivre_la_politique_est_vue():
    """Le témoin de l'instrument : il doit savoir refuser."""
    from oto_mcp.entetes_securite import CSP_DOC_PUBLIC
    assert _ecarts("<p><script>alert(1)</script></p>", CSP_DOC_PUBLIC)
    assert _ecarts('<img src="http://x/y.png">', CSP_DOC_PUBLIC)


@pytest.mark.parametrize("accept", ["text/html", "text/markdown", "application/json"])
def test_la_page_de_doc_SERVIE_porte_sa_politique(doc, accept):
    from oto_mcp.api import public as P
    from oto_mcp.entetes_securite import CSP_DOC_PUBLIC
    resp = asyncio.run(P.public_doc_view(_req(accept)))
    assert resp.headers.get("content-security-policy") == CSP_DOC_PUBLIC


def test_le_jeton_perime_est_servi_avec_les_memes_gardes(monkeypatch):
    """La page « introuvable » est celle qu'un jeton périmé sert : l'URL porte le
    jeton comme les autres. Jamais en cache (un jeton re-partagé ne doit pas rester
    « introuvable »)."""
    from oto_mcp import db
    from oto_mcp.api import public as P
    monkeypatch.setattr(db, "get_doc_by_public_token", lambda t: None)
    resp = asyncio.run(P.public_doc_view(_req("text/html")))
    assert resp.status_code == 404
    assert resp.headers.get("referrer-policy") == "no-referrer"
    assert "content-security-policy" in resp.headers
    assert resp.headers.get("cache-control") == "no-store"


@pytest.mark.asyncio
async def test_la_page_du_projet_publie_SERVIE_porte_sa_politique():
    from oto_mcp import subdomain_project as sp
    from oto_mcp.entetes_securite import CSP_PROJET_PUBLIE
    entetes: dict[str, str] = {}

    async def _send(msg):
        if msg["type"] == "http.response.start":
            entetes.update({k.decode(): v.decode() for k, v in msg["headers"]})

    await sp._send_html(_send, "<html>ok</html>")
    assert entetes.get("content-security-policy") == CSP_PROJET_PUBLIE


@pytest.mark.parametrize("valide", [True, False])
def test_le_formulaire_d_upload_SERVI_porte_sa_politique(monkeypatch, valide):
    from oto_mcp import upload_tokens
    from oto_mcp.api import uploads as U
    from oto_mcp.entetes_securite import csp_upload
    monkeypatch.setattr(upload_tokens, "verify",
                        lambda t: {"target": {"kind": "x"}} if valide else None)
    monkeypatch.setattr(upload_tokens, "target_label", lambda t: "cible")
    req = Request({"type": "http", "method": "GET", "path": "/u/jeton", "query_string": b"",
                   "root_path": "", "scheme": "https", "server": ("test", 443),
                   "http_version": "1.1", "headers": [], "path_params": {"token": "jeton"}},
                  _receive)
    resp = asyncio.run(U.upload_form(req))
    assert resp.headers.get("content-security-policy") == csp_upload(U._SCRIPT_UPLOAD)
    assert resp.headers.get("referrer-policy") == "no-referrer"
