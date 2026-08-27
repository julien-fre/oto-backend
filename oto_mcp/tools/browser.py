"""Navigateur connecté — lire N sites derrière login SANS écrire un connecteur par site.

Connecteur **générique** sur le substrat Browserbase (ADR 0026) : là où `crunchbase`,
`brevoauto` et `pennylaneged` sont trois connecteurs écrits en dur pour trois API privées
qu'on exploite en profondeur, celui-ci sert le besoin inverse — **lire** une page
authentifiée sur un site quelconque (média payant, intranet, back-office sans API), où
écrire un connecteur dédié coûterait un cycle de dev complet pour un `GET`.

Modèle (oto-private#79) :
- **un site = un compte du coffre** (`account` = le host, cf. multi-compte ADR 0011/0024) :
  chaque site a SON Context Browserbase, donc sa session isolée — jamais un profil
  fourre-tout qui mélangerait les credentials de N sites dans un seul secret. Les sites
  connectés se listent (`browser_sites`) et apparaissent dans le picker d'identités du
  dashboard (backend keyed générique de `connector_identities`).
- **connexion** = Live View interactive sur l'URL demandée (`browser_connect_start`) :
  l'utilisateur se logue à la main (SSO/2FA/captcha), la session persiste dans le Context.
- **lecture** = `browser_fetch(url)` charge la page dans une session éphémère du Context du
  site et renvoie son contenu **complet** (pas le repli tronqué à 400 caractères de
  `run_fetch`, qui vise des API JSON).

⚠️ **Vérification du login : générique, donc faillible.** Un connecteur dédié sonde une
route authentifiée qu'il connaît (`/crm/flow_companies` chez Pennylane) ; ici on ne sait
rien du site. Le seul signal lisible partout = « la session porte-t-elle des cookies sur ce
host ? ». 0 cookie ⇒ presque sûrement pas logué ; >0 ne PROUVE rien (un cookie anonyme
suffit). D'où `force=True` sur `browser_connect_status` pour les sites dont l'état de login
vit ailleurs (localStorage) — assumé et documenté, pas un fallback silencieux.

Coût : **1 session navigateur par appel** (héritée du substrat). Adapté au delta de veille
(quelques pages), pas à un backfill de centaines d'articles — la réutilisation d'une session
pour N appels d'un même run reste à faire si le volume le justifie.
"""
from __future__ import annotations

from urllib.parse import urlparse

from fastmcp import Context, FastMCP
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData, INVALID_PARAMS, INTERNAL_ERROR

from .. import access, browser_session, browserbase, connector_identities
from ..auth_hooks import current_user_sub_from_token

_CONNECTOR = "browser"

# Plafond de contenu rendu à l'agent. Une page entière peut peser des centaines de
# milliers de caractères ; on tronque en le DISANT (`truncated`), jamais en silence.
_MAX_CHARS = 100_000


def _err(msg: str, code: int = INVALID_PARAMS) -> McpError:
    return McpError(ErrorData(code=code, message=msg))


def _sub() -> str:
    sub = None
    try:
        sub = current_user_sub_from_token()
    # noqa: SILENT — dette déclarée : sub avalé (#424, verdict C — seam commun)
    except Exception:
        pass
    if not sub:
        raise _err("Auth requise — ce tool ne marche que sur le transport HTTP authentifié.")
    return sub


def _site_of(url: str) -> str:
    """Host normalisé d'une URL = l'identité du site au coffre. `www.` retiré (mêmes
    cookies, même login : `www.exemple.fr` et `exemple.fr` ne doivent pas produire deux
    sessions à maintenir), port et casse normalisés."""
    p = urlparse((url or "").strip())
    if p.scheme not in ("http", "https") or not p.hostname:
        raise _err(f"URL invalide : {url!r} — attendu une URL absolue (https://…).")
    host = p.hostname.lower()
    return host[4:] if host.startswith("www.") else host


def _context_id(site: str) -> str:
    """Context Browserbase de l'utilisateur POUR CE SITE, résolu du coffre. Lève une
    McpError actionnable si le site n'est pas connecté. Un compte explicite introuvable
    lève côté `access` (jamais de repli muet sur le Context d'un AUTRE site — lire la
    mauvaise session serait une fuite entre sites)."""
    try:
        return access.resolve_credential(_CONNECTOR, want="byo", account=site).key
    except McpError:
        raise _err(f"`{site}` n'est pas connecté. Lance `browser_connect_start(\"https://{site}/\")` "
                   "pour t'y loguer une fois (session mémorisée ensuite).")


async def _verify_site(session_id: str, account: str) -> bool:
    """Sonde de login GÉNÉRIQUE : la session vivante porte-t-elle des cookies sur le
    host ? (cf. l'avertissement en tête de module — signal, pas preuve.)"""
    if not account:
        return False
    return await browserbase.host_cookies(session_id, f"https://{account}/") > 0


# Connecteur à session navigateur, variante GÉNÉRIQUE : `login_url` fournie à l'appel
# (le site vient de l'utilisateur) et `verify` account-aware. À l'import, comme les autres.
browser_session.register(_CONNECTOR, _verify_site, account_aware=True)


def register(mcp: FastMCP) -> None:

    # --- Connexion d'un site (Live View) ------------------------------------
    @mcp.tool()
    def browser_connect_start(ctx: Context, url: str) -> dict:
        """Connecte un site derrière login (une fois par site). Ouvre un navigateur
        distant sur `url` et renvoie une **`live_view_url`** : ouvre-la, connecte-toi au
        site normalement (email/mot de passe, SSO, 2FA — tout se passe dans cette
        fenêtre). Puis appelle `browser_connect_status(context_id, session_id, site)`
        avec les valeurs renvoyées pour mémoriser la session.

        Ensuite `browser_fetch(url)` lit n'importe quelle page de ce site en étant logué.

        Args:
            url: URL de la page de connexion du site (ou sa page d'accueil).
        """
        sub = _sub()
        site = _site_of(url)
        try:
            out = browser_session.start(sub, _CONNECTOR, login_url=url)
        except browser_session.SessionError as e:
            raise _err(str(e), code=INTERNAL_ERROR)
        out["site"] = site
        out["instructions"] = (
            f"Ouvre `live_view_url`, connecte-toi à {site}, puis appelle "
            f"`browser_connect_status` avec context_id + session_id + site='{site}'.")
        return out

    @mcp.tool()
    async def browser_connect_status(ctx: Context, context_id: str, session_id: str,
                                     site: str, force: bool = False) -> dict:
        """Finalise la connexion d'un site. Vérifie que tu t'es bien logué dans la Live
        View ; si oui, **mémorise** la session pour ce site. Renvoie `{connected, site}`.
        Rappelle-le si `connected=false` (pas encore logué).

        Args:
            context_id: valeur renvoyée par `browser_connect_start`.
            session_id: valeur renvoyée par `browser_connect_start`.
            site: host renvoyé par `browser_connect_start` (ex. `le-ticket.fr`).
            force: mémoriser SANS vérification. La vérification est générique (présence
                de cookies sur le host) : certains sites gardent leur session ailleurs
                (localStorage) et répondent « pas logué » à tort. N'utilise `force` que
                si tu t'es bien logué et que la vérification échoue quand même.
        """
        sub = _sub()
        try:
            connected = await browser_session.finalize(
                sub, _CONNECTOR, context_id, session_id, account=site, force=force)
        except browser_session.SessionError as e:
            raise _err(str(e), code=INTERNAL_ERROR)
        if not connected:
            return {"connected": False, "site": site,
                    "hint": ("Pas encore logué (aucun cookie sur ce site) — connecte-toi "
                             "dans la Live View puis relance. Si tu ES logué, relance avec "
                             "force=true (le site garde peut-être sa session hors cookies).")}
        return {"connected": True, "site": site}

    @mcp.tool()
    def browser_sites() -> dict:
        """Liste les sites que tu as connectés dans ce navigateur (un par login mémorisé).
        Renvoie `{sites: [{id, label, is_default}]}` — `id` = le host à passer aux autres
        tools. Un site absent d'ici doit d'abord passer par `browser_connect_start`."""
        sub = _sub()
        return {"sites": connector_identities.list_identities(sub, _CONNECTOR)}

    # --- Lecture -------------------------------------------------------------
    @mcp.tool()
    async def browser_fetch(url: str, as_html: bool = False,
                            max_chars: int = _MAX_CHARS) -> dict:
        """Lit une page **en étant logué** sur le site (session mémorisée, cf.
        `browser_connect_start`). Charge l'URL dans le navigateur distant et renvoie le
        contenu rendu — texte lisible par défaut, DOM sérialisé si `as_html`.

        Renvoie `{site, status, final_url, title, content, truncated}`. `status` = code
        HTTP de la navigation (une page de login renvoyée à la place du contenu signale
        une session expirée → reconnecte le site).

        Args:
            url: URL absolue de la page à lire.
            as_html: True = HTML rendu (pour extraire des attributs/liens précis) ;
                False (défaut) = texte lisible, bien plus compact.
            max_chars: plafond de caractères renvoyés (troncature signalée par
                `truncated=true`).
        """
        site = _site_of(url)
        if not browserbase.is_configured():
            raise _err("Browserbase non configuré côté plateforme "
                       "(BROWSERBASE_API_KEY / BROWSERBASE_PROJECT_ID).", code=INTERNAL_ERROR)
        ctx_id = _context_id(site)
        try:
            res = await browserbase.fetch_page(ctx_id, url, as_html=as_html)
        except browserbase.BrowserbaseError as e:
            raise _err(f"Exécution Browserbase échouée : {e}", code=INTERNAL_ERROR)
        content = res.get("content") or ""
        cap = max(1, int(max_chars))
        return {"site": site, "status": res.get("status"),
                "final_url": res.get("final_url"), "title": res.get("title"),
                "content": content[:cap], "truncated": len(content) > cap}

    @mcp.tool()
    async def browser_eval(url: str, js: str) -> dict:
        """Exécute du JavaScript **dans la page**, sur ta session loguée — échappatoire
        pour ce que `browser_fetch` ne couvre pas (appeler une API interne du site avec
        son CSRF tournant, cliquer/dérouler avant de lire, extraire une structure précise).

        `js` = source d'une fonction async **sans argument**, ex.
        `async () => (await fetch("/api/items", {credentials:"include"})).json()`.
        Sa valeur de retour est renvoyée telle quelle sous `result` (une liste est
        enveloppée sous `items` — MCP exige un objet). Le `fetch` est same-origin avec
        `url`, donc il porte les cookies de session.

        Args:
            url: page à charger avant d'exécuter (donne l'origine et les cookies).
            js: source de la fonction async à exécuter dans la page.
        """
        site = _site_of(url)
        if not browserbase.is_configured():
            raise _err("Browserbase non configuré côté plateforme "
                       "(BROWSERBASE_API_KEY / BROWSERBASE_PROJECT_ID).", code=INTERNAL_ERROR)
        ctx_id = _context_id(site)
        try:
            res = await browserbase.run_page_eval(ctx_id, url, js)
        except browserbase.BrowserbaseError as e:
            raise _err(f"Exécution Browserbase échouée : {e}", code=INTERNAL_ERROR)
        if isinstance(res, list):
            return {"site": site, "items": res}
        return {"site": site, "result": res}
