"""Pennylane GED — la SESSION : login dans la Live View, et sonde de vérification.

Sœur de `pennylaneged.py`, qui porte les outils GED eux-mêmes. Ce module-ci ne
s'occupe que d'UNE chose : établir et vérifier le login de l'utilisateur sur
`app.pennylane.com`, puis persister sa session au coffre via `browser_session`. Les
deux modules sont montés ensemble (`providers/pennylaneged.py`, `modules=`) ; celui-ci
importe l'origine et les seams d'erreur de son aîné, jamais l'inverse.

⚠️ **La sonde de login ne tape JAMAIS une route métier.** Le 2026-09-03 elle sondait
la vue portefeuille (`/crm/flow_companies`) ; Pennylane l'a déplacée sous
`/portfolio/`, elle a répondu 404, la vérification a conclu « pas logué » et
`browser_session.finalize` est sorti avant de persister : plus aucune cliente ne
pouvait connecter sa GED, alors que trois des quatre outils marchaient. Une route de
session (`/users/me`) ne bouge pas avec le produit — c'est elle qu'on sonde.
"""
from __future__ import annotations

from fastmcp import Context, FastMCP
from mcp.types import INTERNAL_ERROR

from .. import browser_session, browserbase
from .pennylaneged import _ORIGIN, _err, _sub


# Route SONDÉE pour le login — de SESSION, jamais métier. `/users/me` est ce que la SPA
# appelle au chargement pour savoir QUI est logué (chunk `CurrentUserContext-*.js`) :
# elle répond **200 dans les deux cas** et porte le verdict dans son CORPS (`user: null`
# = anonyme, objet = logué). Elle ne suit donc ni le découpage des vues ni les
# renommages de namespace — au contraire de la route PORTEFEUILLE que sondait le code
# jusqu'au 2026-09-03 : déplacée sous `/portfolio/`, elle a répondu 404 et plus personne
# n'a pu connecter sa GED.
_PROBE_PATH = "/users/me"

_PROBE_JS = r"""async (path) => {
    try {
        const r = await fetch(path, {credentials: "include",
            headers: {"accept": "application/json",
                      "x-requested-with": "XMLHttpRequest"}});
        const txt = await r.text();
        let data = null;
        try { data = txt ? JSON.parse(txt) : null; } catch (e) { data = null; }
        return {status: r.status,
                login_page: /\/(auth\/)?login/.test(r.url || ""),
                json: data !== null && typeof data === "object",
                logged_in: !!(data && data.user && data.user.id)};
    } catch (e) { return {status: 0, error: String(e).slice(0, 200)}; }
}"""


def _read_probe(res: dict) -> browser_session.Verdict:
    """Verdict de la sonde, depuis sa réponse brute. Fonction PURE (testable sans
    navigateur) : c'est elle qui porte la règle. Quatre issues, et pas deux —

    - **logué** : 200 + `user` non nul ;
    - **pas encore logué** : 200 + `user: null` → l'humain n'a pas fini dans la fenêtre ;
    - **rejeté** : 401, 403, atterrissage sur la page de login → refaire le login ;
    - **sans verdict** : 404 (l'endpoint sondé a bougé) ou autre → `ProbeUnavailable`,
      qui n'est PAS un signal d'authentification (cf. `browser_session`).

    Le `fetch` en échec (status 0) est un « pas logué » retryable : il ne dit rien de
    l'authentification, mais relancer coûte un clic et peut suffire."""
    V, st = browser_session.Verdict, res.get("status")
    if st in (401, 403):
        return V(False, browser_session.AUTH_REJECTED,
                 f"Pennylane a refusé la session ({st}) : reconnecte-toi dans la Live "
                 "View, puis relance `pennylaneged_connect_status`.")
    if res.get("login_page"):
        return V(False, browser_session.AUTH_REJECTED,
                 "La session a atterri sur la page de login Pennylane : le login n'a "
                 "pas abouti (ou il a expiré). Refais-le dans la Live View.")
    if st == 0:
        return V(False, browser_session.NO_SESSION,
                 "La sonde n'a pas pu joindre Pennylane depuis le navigateur distant "
                 f"(GET {_PROBE_PATH} en échec réseau). Relance `pennylaneged_connect_status`.")
    if st == 200 and res.get("json"):
        if res.get("logged_in"):
            return V(True, browser_session.LOGGED_IN)
        return V(False, browser_session.NO_SESSION,
                 "Pennylane te voit encore anonyme : finis de te loguer dans la fenêtre "
                 "de la Live View (email, mot de passe, 2FA), PUIS relance "
                 "`pennylaneged_connect_status` avec les mêmes identifiants de session.")
    bouge = (" : cet endpoint n'existe plus (route déplacée par Pennylane)"
             if st == 404 else "")
    raise browser_session.ProbeUnavailable(
        f"la sonde de login Pennylane n'a pas pu se prononcer — GET {_PROBE_PATH} a "
        f"répondu {st}{bouge}. Ta session a été mémorisée quand même, SANS confirmation "
        "du login. Ne recommence pas la connexion : le problème est chez nous, pas chez "
        "toi. Essaie directement un appel GED — s'il répond, tout va bien.")


async def _verify_session(session_id: str) -> browser_session.Verdict:
    """Login Pennylane confirmé ? Sonde `/users/me` DEPUIS la session vivante
    (same-origin) et tranche sur le CORPS de la réponse, pas sur son code HTTP — cf.
    `_read_probe`. Partagé par les deux surfaces de connexion (dashboard REST + MCP)
    via `browser_session`."""
    from patchright.async_api import async_playwright
    async with async_playwright() as p:
        b = await p.chromium.connect_over_cdp(browserbase.connect_url(session_id))
        try:
            c = b.contexts[0] if b.contexts else await b.new_context()
            pg = c.pages[0] if c.pages else await c.new_page()
            await pg.goto(f"{_ORIGIN}/", wait_until="domcontentloaded", timeout=40000)
            res = await pg.evaluate(_PROBE_JS, _PROBE_PATH)
        finally:
            await b.close()
    return _read_probe(res)


# Déclare Pennylane GED comme connecteur à session navigateur (start générique + ce
# verify) — alimente le flux de connexion REST (dashboard) ET MCP. À l'import.
browser_session.register("pennylaneged", _verify_session, login_url=f"{_ORIGIN}/")


def register(mcp: FastMCP) -> None:

    # --- Onboarding (Live View) --------------------------------------------
    @mcp.tool()
    def pennylaneged_connect_start(ctx: Context) -> dict:
        """Démarre la connexion à la GED Pennylane. Ouvre un navigateur distant et
        renvoie une **`live_view_url`** : ouvre-la, connecte-toi à Pennylane normalement
        (email/mot de passe, SSO, 2FA — tu gères tout dans cette fenêtre). Puis appelle
        `pennylaneged_connect_status(context_id, session_id)` avec les valeurs renvoyées
        pour finaliser (ta session est mémorisée ; à refaire seulement quand elle expire).
        """
        sub = _sub()
        try:
            out = browser_session.start(sub, "pennylaneged")
        except browser_session.SessionError as e:
            raise _err(str(e), code=INTERNAL_ERROR)
        out["instructions"] = ("Ouvre `live_view_url`, connecte-toi à Pennylane, puis "
                               "appelle `pennylaneged_connect_status` avec context_id + session_id.")
        return out

    @mcp.tool()
    async def pennylaneged_connect_status(ctx: Context, context_id: str,
                                          session_id: str,
                                          force: bool = False) -> dict:
        """Finalise la connexion à la GED Pennylane. Vérifie que tu t'es bien logué dans
        la Live View (sonde `/users/me` depuis ta session) ; si oui, **mémorise** ta
        session (le Context) pour les prochains appels.

        Renvoie `{connected, reason, retry, hint}` — **lis `reason` avant de recommencer** :

        - `logged_in` → c'est fait, rien à refaire ;
        - `no_session` → tu n'as pas (encore) fini de te loguer dans la fenêtre : va au
          bout, PUIS rappelle ce tool avec les mêmes `context_id`/`session_id` ;
        - `auth_rejected` → Pennylane a refusé la session : refais le login ;
        - `probe_unavailable` → ta session EST mémorisée (`connected: true`) mais la
          sonde n'a pas pu le confirmer, parce qu'elle est cassée de NOTRE côté.
          `retry: false` : **ne recommence pas**, tente directement un appel GED.

        ⚠️ `retry: false` veut dire « recommencer ne peut pas aboutir » — le problème
        n'est pas chez l'utilisateur. Ne reboucle pas : dis-le et passe à la suite.

        Args:
            context_id: `context_id` rendu par `pennylaneged_connect_start`.
            session_id: `session_id` rendu par `pennylaneged_connect_start`.
            force: mémorise la session SANS vérifier le login. Échappatoire pour ne pas
                rester bloqué quand la sonde se trompe ou tombe. N'utilise-la que si tu
                t'es bien logué et que la vérification refuse quand même : elle peut
                poser au coffre une session morte, que tu ne découvriras qu'au premier
                appel GED (401 → « session expirée »).
        """
        sub = _sub()
        try:
            res = await browser_session.finalize(sub, "pennylaneged", context_id,
                                                 session_id, force=bool(force))
        except browser_session.SessionError as e:
            raise _err(str(e), code=INTERNAL_ERROR)
        if not res.connected:
            return {"connected": False, "reason": res.reason, "retry": res.retry,
                    "hint": res.detail or "Pas encore logué — connecte-toi dans la Live "
                                          "View puis relance."}
        out = {"connected": True, "context_id": context_id, "reason": res.reason,
               "login_verified": not res.warning}
        if res.warning:
            out["warning"] = res.warning
            out["retry"] = False
        return out
