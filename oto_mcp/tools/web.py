"""web_read — lire une page publique qui se défend, en ESCALADANT (#348).

Mesuré en campagne réelle : sur les sites de petites structures, deux sur
trois ne se laissent pas lire par un fetch nu (timeouts, 403 anti-robot) — et
les coordonnées qu'on y cherche sont la priorité du client final. Trois crans,
un seul verbe, et la réponse DIT toujours son chemin :

  ① fetch HTTP nu       — gratuit ; suffit pour la majorité des sites ;
  ② scraper hébergé     — `serper` (1 crédit) : rendu JS + anti-bot rudimentaire ;
  ③ navigateur hébergé  — session Chrome JETABLE (Browserbase, sans compte ni
                          coffre), OPT-IN `browser=True` : jamais un défaut
                          silencieux qui multiplie la facture.

Un cran indisponible (pas de clé serper, Browserbase non configuré) est SAUTÉ
ET DIT — le repli silencieux est exclu, dans les deux sens.

⚠️ SÉCURITÉ (cran ① seulement — ② et ③ s'exécutent hors de notre réseau) :
le fetch tourne sur la box, qui vit dans un VPC avec des services PRIVÉS. La
garde SSRF : schémas http(s) seuls, résolution DNS puis refus de toute IP non
publique (loopback, RFC1918, link-local, metadata…), redirections marchées À
LA MAIN et re-vérifiées à chaque saut (un 302 public→privé ne contourne rien).
Limite assumée : un DNS à TTL nul qui répond différemment entre la
vérification et la connexion (rebinding pur) n'est pas couvert — les cibles
internes exigent de toute façon des chemins/headers qu'une lecture de page ne
fournit pas.

Bornes de lecture : le cap se compte sur les bytes DÉCOMPRESSÉS, PENDANT la
lecture (jamais accumuler-puis-tronquer — la leçon de la bombe de
décompression).
"""
from __future__ import annotations

import asyncio
import ipaddress
import socket
import time
from html.parser import HTMLParser
from typing import Optional
from urllib.parse import urljoin, urlsplit

import requests
from fastmcp import FastMCP
from mcp.shared.exceptions import McpError
from mcp.types import INVALID_REQUEST, ErrorData

from .. import access, browserbase

_TIMEOUT = (10, 30)              # borne CHAQUE socket — pas la lecture entière
_DEADLINE_S = 45                 # budget GLOBAL du cran ① (cf. `_fetch_http`)
_MAX_FETCH_BYTES = 3_000_000     # bytes décompressés lus au maximum (cran ①)
_EMPTY_TEXT_CHARS = 200          # texte extrait plus court = coquille vide
_MAX_REDIRECTS = 5
_DEFAULT_MAX_CHARS = 12_000
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


def _bad(msg: str) -> McpError:
    return McpError(ErrorData(code=INVALID_REQUEST, message=msg))


def _now() -> float:
    """Indirection d'horloge — le budget global se teste sans dormir."""
    return time.monotonic()


def _meme_site(demande: str, servi: str) -> bool:
    """`demande` et `servi` désignent-ils le MÊME site ?

    Un `www.` en tête et un sous-domaine ne sont pas des écarts (`acme.fr` →
    `www.acme.fr` → `shop.acme.fr` : même maison) ; deux domaines distincts en
    sont un. Volontairement sans liste de suffixes publics : la règle « l'un est
    suffixe de l'autre sur une frontière de label » n'a pas le trou du `.co.uk`
    qu'aurait une comparaison des deux derniers labels — `acme.co.uk` et
    `evil.co.uk` ne sont suffixes ni l'un ni l'autre, donc l'écart est ANNONCÉ."""
    a = (demande or "").lower().removeprefix("www.")
    b = (servi or "").lower().removeprefix("www.")
    if not a or not b:
        return False
    return a == b or a.endswith("." + b) or b.endswith("." + a)


# ── garde SSRF (cran ① seulement) ────────────────────────────────────────────
def _resolved_ips(host: str) -> list:
    infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    return [ipaddress.ip_address(i[4][0]) for i in infos]


def check_url_public(url: str) -> None:
    """Lève (en nommant la raison) si `url` ne désigne pas une cible PUBLIQUE.

    Fail-closed sur l'ENSEMBLE des IPs résolues : un host qui résout à la fois
    public et privé est refusé — c'est le montage type du contournement."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise _bad(f"schéma `{parts.scheme or '∅'}` refusé — web_read lit du "
                   "http(s), rien d'autre")
    host = parts.hostname
    if not host:
        raise _bad("URL sans hôte")
    try:
        ips = _resolved_ips(host)
    except OSError as e:
        raise _bad(f"`{host}` ne résout pas ({e})") from None
    for ip in ips:
        if not ip.is_global:
            raise _bad(
                f"`{host}` résout vers {ip}, une adresse non publique "
                "(réseau interne, loopback ou lien-local) — web_read ne lit "
                "que l'internet public")


# ── extraction texte (stdlib — pas de dépendance pour retirer des balises) ───
class _TextExtractor(HTMLParser):
    _SKIP = {"script", "style", "noscript", "template", "svg", "head"}
    _BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6",
              "section", "article", "header", "footer", "ul", "ol", "table"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list = []
        self.title = ""
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip += 1
        if tag == "title":
            self._in_title = True
        if tag in self._BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip:
            self._skip -= 1
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self._skip and data.strip():
            self.parts.append(data)


def extract_text(html_str: str) -> tuple:
    """(texte lisible, titre) — les lignes vides répétées repliées."""
    p = _TextExtractor()
    try:
        p.feed(html_str)
    # noqa: SILENT — extraction de texte optionnelle : le HTML brut reste rendu
    except Exception:  # noqa: BLE001 — un HTML monstrueux ne casse pas la lecture
        pass
    brut = "".join(p.parts)
    lignes = [l.strip() for l in brut.splitlines()]
    texte = "\n".join(l for i, l in enumerate(lignes)
                      if l or (i > 0 and lignes[i - 1]))
    return texte.strip(), p.title.strip()


# ── cran ① : fetch HTTP nu, streamé, gardé ───────────────────────────────────
def _fetch_http(url: str) -> dict:
    """{verdict, ok, status?, html?, final_url?} — les redirections sont
    marchées À LA MAIN : chaque saut repasse la garde SSRF.

    ⚠️ `_TIMEOUT` borne chaque SOCKET, jamais la lecture entière : six sauts de
    redirection valent six fois ce budget, et la boucle de streaming n'est
    bornée par rien du tout (un serveur qui distille un octet à la fois tient la
    connexion indéfiniment). Mesuré au journal de prod du 17/08 : 11 lectures
    au-delà de 30 s, une à **57,5 s**. D'où un budget GLOBAL (`_DEADLINE_S`),
    vérifié avant chaque saut ET pendant la lecture, qui rabote au passage le
    timeout de socket sur ce qu'il reste. Le verdict DIT ce qu'il a tenté
    (combien de sauts, où il en était) — un « timeout » nu n'apprend rien à
    l'agent qui doit décider s'il réessaie (#491)."""
    t0 = _now()
    courante = url
    sauts = 0

    def _delai(ou: str) -> dict:
        ecoule = _now() - t0
        return {"ok": False,
                "verdict": ("délai global dépassé ({:.0f} s) {} — {} redirection(s) "
                            "suivie(s), dernière cible : {}"
                            .format(ecoule, ou, sauts, courante))}

    for _ in range(_MAX_REDIRECTS + 1):
        reste = _DEADLINE_S - (_now() - t0)
        if reste <= 0:
            return _delai("avant le saut suivant")
        check_url_public(courante)
        try:
            r = requests.get(courante, stream=True, allow_redirects=False,
                             timeout=(min(_TIMEOUT[0], reste), min(_TIMEOUT[1], reste)),
                             headers={"User-Agent": _UA})
        except requests.Timeout:
            return {"ok": False,
                    "verdict": "timeout après {} redirection(s), sur {}".format(
                        sauts, courante)}
        except requests.RequestException as e:
            return {"ok": False, "verdict": f"réseau : {type(e).__name__}"}
        try:
            if r.is_redirect or r.is_permanent_redirect:
                cible = r.headers.get("Location")
                if not cible:
                    return {"ok": False, "verdict": "redirection sans cible"}
                courante = urljoin(courante, cible)
                sauts += 1
                continue
            if r.status_code >= 400:
                return {"ok": False, "verdict": f"HTTP {r.status_code}",
                        "status": r.status_code}
            # Lecture STREAMÉE, cap sur les bytes DÉCOMPRESSÉS, arrêt PENDANT.
            # Le budget se revérifie à chaque morceau : c'est ici qu'un serveur
            # lent tenait la lecture 57 s (#491).
            morceaux, total = [], 0
            for chunk in r.iter_content(chunk_size=65536, decode_unicode=False):
                morceaux.append(chunk)
                total += len(chunk)
                if total >= _MAX_FETCH_BYTES:
                    break
                if _now() - t0 >= _DEADLINE_S:
                    return _delai("pendant la lecture du corps")
            brut = b"".join(morceaux)
            html_str = brut.decode(r.encoding or "utf-8", errors="replace")
            return {"ok": True, "status": r.status_code, "html": html_str,
                    "final_url": courante, "verdict": "lu"}
        finally:
            r.close()
    return {"ok": False, "verdict": f"plus de {_MAX_REDIRECTS} redirections"}


def register(mcp: FastMCP) -> None:

    def _serper_scrape(url: str) -> Optional[dict]:
        """Cran ② — None si la clé serper n'est pas résolvable (cran sauté)."""
        from oto.tools.serper import SerperClient

        try:
            key, is_platform = access.resolve_api_key("serper")
        # noqa: SILENT — dette déclarée : erreur de coffre lue comme « pas de clé serper » (#424, verdict C)
        except Exception:  # noqa: BLE001 — pas de clé = cran indisponible, pas une panne
            return None
        res = SerperClient(api_key=key).scrape_page(url, include_markdown=True)
        if is_platform:
            access.record_platform_usage("serper")
        return res

    @mcp.tool()
    async def web_read(url: str, browser: bool = False, as_html: bool = False,
                       max_chars: int = _DEFAULT_MAX_CHARS) -> dict:
        """Read a PUBLIC web page, escalating until it yields — and always
        saying which path was used.

        Escalation: ① plain HTTP fetch (free) → on 403/timeout/empty shell
        ② hosted scraper (serper, 1 credit — JS rendering, basic anti-bot) →
        ③ ONLY IF `browser=true`: a disposable hosted Chrome session
        (real fingerprint, patience — costs a browser session). Without
        `browser=true` the answer stops at ② and tells you what to do.

        Returns `{content, title, final_url, hote, chemin, tentatives, cout,
        truncated}` — `chemin` = which path actually produced the content
        (`http` | `serper` | `browser`), `tentatives` = every path tried and
        why it moved on, `cout` = what the read cost (serper credits, browser
        session). A skipped path (no serper key, Browserbase not configured)
        is REPORTED, never silent.

        ⚠️ `final_url` is the OBSERVED landing URL, or `null` when the path
        used cannot report one (the hosted scraper follows redirects silently).
        `hote` = `{demande, servi, conforme}` says whether the page actually
        served belongs to the domain you asked for: `conforme` is `true`
        (same site), `false` (a redirect took you elsewhere — the content is
        that OTHER site's) or `null` (unknowable on this path). Anything but
        `true` also sets `avertissement`. Never assume the body came from the
        host you requested — read `hote`.

        Args:
            url: absolute public URL (http/https). Internal/private addresses
                are refused by design.
            browser: opt-in for the costly last resort (real Chrome session).
            as_html: True = raw HTML/DOM instead of readable text (①/③ only —
                ② returns markdown).
            max_chars: cap on returned content (truncation is flagged).
        """
        cap = max(1, int(max_chars))
        tentatives: list = []
        cout = {"serper_credits": 0, "browser_session": False}

        demande = urlsplit(url).hostname or ""

        def _sortie(chemin: str, content: str, title: str = "",
                    final_url: Optional[str] = None) -> dict:
            """Assemble la réponse — et n'AFFIRME jamais l'URL finale.

            Signal #491 : ce champ recopiait l'URL DEMANDÉE quand le cran n'en
            observait aucune (`final_url or url`). Or serper suit les
            redirections en silence et ne rend AUCUNE URL finale : le tool
            jurait donc que la page venait de l'hôte demandé, sans rien en
            savoir — et la seule parade de l'appelant (comparer `final_url` à
            l'hôte demandé) était structurellement aveugle sur ce cran.

            Désormais : `final_url` est OBSERVÉE ou `None`, `hote` porte le
            verdict (`conforme` vaut `None` quand on ne sait pas), et tout ce
            qui n'est pas un `True` franc se dit dans `avertissement`. C'est le
            tool qui annonce l'écart, pas l'appelant qui doit y penser."""
            servi = urlsplit(final_url).hostname if final_url else None
            conforme = _meme_site(demande, servi) if servi else None
            out = {"chemin": chemin, "content": content[:cap],
                   "truncated": len(content) > cap, "title": title,
                   "final_url": final_url,
                   "hote": {"demande": demande, "servi": servi,
                            "conforme": conforme},
                   "tentatives": tentatives, "cout": cout}
            if conforme is None:
                out["avertissement"] = (
                    "Impossible de confirmer quel site a répondu : le cran "
                    "`{}` ne rend pas l'URL finale et suit les redirections "
                    "sans le dire. Le contenu peut venir d'un autre domaine "
                    "que `{}` — recoupe avant d'en tirer un fait.".format(
                        chemin, demande))
            elif conforme is False:
                out["avertissement"] = (
                    "Tu as demandé `{}` ; la page servie vient de `{}` "
                    "(redirection suivie). Le contenu ci-dessus est celui de "
                    "`{}` — vérifie que c'est bien le site voulu avant d'en "
                    "tirer un fait.".format(demande, servi, servi))
            return out

        # ── ① le fetch nu ────────────────────────────────────────────────────
        # `requests` est SYNCHRONE et ce handler est `async def` (il `await` le
        # cran ③) : exécuté tel quel, il gèle la boucle — donc TOUS les
        # utilisateurs — le temps de la lecture. Mesuré au journal du 17/08 :
        # 11 lectures > 30 s, une à 57,5 s (docs/event-loop-perf.md, mode n°1).
        # Le garde-fou AST ne peut pas le voir : le handler `await` bien
        # quelque chose, plus bas. D'où `to_thread` ici (#491).
        res = await asyncio.to_thread(_fetch_http, url)
        if res.get("ok"):
            texte, title = extract_text(res["html"])
            contenu = res["html"] if as_html else texte
            if len(texte) >= _EMPTY_TEXT_CHARS:
                tentatives.append({"cran": "http", "verdict": "lu"})
                return _sortie("http", contenu, title, res.get("final_url") or None)
            tentatives.append({"cran": "http",
                               "verdict": f"coquille vide ({len(texte)} car. utiles)"})
        else:
            tentatives.append({"cran": "http", "verdict": res["verdict"]})

        # ── ② le scraper hébergé ─────────────────────────────────────────────
        # Même raison qu'au cran ① : `SerperClient` est synchrone (et s'auto-
        # limite par un `time.sleep`), il n'a rien à faire dans la boucle.
        scrape = await asyncio.to_thread(_serper_scrape, url)
        if scrape is None:
            tentatives.append({"cran": "serper",
                               "verdict": "sauté — aucune clé serper résolvable"})
        else:
            cout["serper_credits"] = 1
            md = scrape.get("markdown") or scrape.get("text") or ""
            meta = scrape.get("metadata") or {}
            if len(md.strip()) >= _EMPTY_TEXT_CHARS:
                tentatives.append({"cran": "serper", "verdict": "lu"})
                return _sortie("serper", md, str(meta.get("title") or ""))
            tentatives.append({"cran": "serper",
                               "verdict": f"coquille vide ({len(md.strip())} car.)"})

        # ── ③ le navigateur jetable — OPT-IN strict ──────────────────────────
        if not browser:
            raise _bad(
                "Page illisible par fetch et scraper "
                f"({'; '.join(t['cran'] + ': ' + t['verdict'] for t in tentatives)}). "
                "Dernier recours : repasse avec browser=true — une session Chrome "
                "hébergée (coût réel, quelques secondes de navigateur).")
        if not browserbase.is_configured():
            raise _bad("browser=true demandé mais Browserbase n'est pas configuré "
                       "côté plateforme — signale-le (feedback signal=gap).")
        try:
            page = await browserbase.fetch_page_ephemeral(url, as_html=as_html)
        except browserbase.BrowserbaseError as e:
            tentatives.append({"cran": "browser", "verdict": str(e)[:200]})
            raise _bad("La session navigateur a échoué aussi — la page est "
                       f"illisible par les trois crans. Tentatives : {tentatives}")
        cout["browser_session"] = True
        contenu = page.get("content") or ""
        if not as_html:
            # innerText déjà « texte » — pas de seconde extraction.
            pass
        tentatives.append({"cran": "browser", "verdict": "lu"})
        return _sortie("browser", contenu, page.get("title") or "",
                       page.get("final_url") or None)
