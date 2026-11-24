"""CLIQUET : tout retour de consentement passe par `api/oauth_returns.py` — y compris
celui d'un connecteur qui n'existe pas encore.

Pourquoi. Jusqu'à #1914, chaque connecteur écrivait SON callback, et chacun rangeait le
jeton lui-même, sur la seule foi du `sub` porté par le state. Un lien de consentement
lancé par A et ouvert par B rangeait le compte fournisseur de B chez A (ADA 3.2.2,
CASA). #1914 ramène les douze retours à une seule mécanique (`oauth_flow.callback_url`
ne range rien ; `oauth_flow.complete` vérifie que le porteur est celui qui a lancé le
lien, consomme le state, puis échange).

Ce qui manquait : rien n'empêchait le TREIZIÈME connecteur de réécrire un callback à la
main, qui range au retour comme avant. Le gel de la table de routes ne l'arrête pas
(on régénère le fichier, une ligne de plus). Ce banc, si : toute route servie dont le
chemin dit « callback » doit être produite par `api/oauth_returns.py`, donc déclarée
par `oauth_flow.declare_return`, donc terminée par `oauth_flow.complete`.

Ajouter un connecteur OAuth = `oauth_flow.declare_return(OAuthReturn(...))` dans son
`auth/<x>.py`. Jamais une `Route(".../callback", ...)` écrite à la main.
"""
from __future__ import annotations

import pathlib
import re

from oto_mcp.auth import flow as oauth_flow

_MODULE_DES_RETOURS = "oto_mcp.api.oauth_returns"


class _FakeVerifier:
    """`make_routes` capture le verifier sans l'appeler (cf. test_api_routes_table_frozen)."""


def _routes():
    from oto_mcp.api import routes as api_routes
    return api_routes.make_routes(_FakeVerifier(), mcp_instance=None)


def _est_un_retour(path: str) -> bool:
    return "callback" in path.lower()


def test_tout_callback_servi_vient_de_oauth_returns():
    a_la_main = sorted(
        f"{r.path} -> {getattr(r.endpoint, '__module__', '?')}"
        for r in _routes()
        if _est_un_retour(r.path)
        and getattr(r.endpoint, "__module__", None) != _MODULE_DES_RETOURS)
    assert not a_la_main, (
        f"Callback(s) OAuth écrit(s) à la main : {a_la_main}\n"
        "Un callback hors de `api/oauth_returns.py` peut ranger un jeton pour le `sub` "
        "du state, sans vérifier QUI termine la connexion (ADA 3.2.2). Déclare le "
        "retour avec `oauth_flow.declare_return` dans `auth/<connecteur>.py` : le "
        "callback commun renvoie au front (`connect=confirm`), et "
        "`POST /api/me/connectors/{name}/complete` termine avec le jeton du porteur.")


def test_chaque_retour_declare_est_servi_et_reciproquement():
    servis = {r.path for r in _routes() if _est_un_retour(r.path)}
    declares = {r.callback_path for r in oauth_flow.load_returns().values()}
    assert servis == declares, (
        f"servis sans déclaration : {sorted(servis - declares)} ; "
        f"déclarés sans route : {sorted(declares - servis)}")



# --- au-delà du mot « callback » ------------------------------------------------
#
# Un retour peut s'appeler `/oauth/return` ou `/connect/done` : le banc du chemin ne le
# verrait pas. Le signe qui ne trompe pas, c'est le STATE : un module qui en signe ou en
# lit un mène une danse OAuth, et doit donc la déclarer (`declare_return`), ce qui la
# fait terminer par `oauth_flow.complete`.

_ROOT = pathlib.Path(__file__).resolve().parents[2] / "oto_mcp"

#: Les primitives de state : celles de `auth/flow.py`, et l'ancien signeur de `auth/pkce.py`.
_STATE = re.compile(
    r"\b[a-z_]*flow\.(?:sign_state|read_state)\(|\bpkce\.(?:make_state|verify_state)\("
    r"|\bimport\b[^\n]*\b(?:sign_state|read_state)\b")

#: Modules qui touchent un state sans être un retour, et pourquoi.
_SANS_RETOUR: dict[str, str] = {
    "auth/flow.py": "définit les primitives et le cœur de complétion",
    "auth/pkce.py": "définit l'ancien signeur PKCE",
}


def test_tout_module_qui_signe_un_state_declare_son_retour():
    fautifs = []
    for p in sorted(_ROOT.rglob("*.py")):
        rel = p.relative_to(_ROOT).as_posix()
        if rel in _SANS_RETOUR:
            continue
        src = p.read_text(encoding="utf-8")
        if _STATE.search(src) and "declare_return(" not in src:
            fautifs.append(rel)
    assert not fautifs, (
        f"Module(s) qui signent ou lisent un state OAuth sans déclarer de retour : "
        f"{fautifs}\nUne danse OAuth hors de `oauth_flow.declare_return` peut ranger un "
        "jeton au retour sans vérifier qui termine (ADA 3.2.2). Déclare le retour ; si "
        "le module ne range vraiment rien, ajoute-le à `_SANS_RETOUR` avec sa raison.")


# --- les deux crochets qui s'exécutent AU CALLBACK, sans identité ---------------
#
# `side_return` (trancher au callback) et `code` (remettre autre chose que le code reçu)
# tournent avant toute vérification du porteur. Légitimes aujourd'hui : Microsoft
# (approbation d'un administrateur, rien n'est rangé) et WordPress (le credential reçu
# est SCELLÉ, pas rangé). Un nouveau venu qui s'en servirait pour ranger contournerait
# le cœur : il doit passer par la revue, donc par cette liste.

def test_side_return_reserve_a_microsoft():
    assert {a for a, r in oauth_flow.load_returns().items()
            if r.side_return is not None} == {"microsoft"}


def test_code_au_callback_reserve_a_wordpress():
    assert {a for a, r in oauth_flow.load_returns().items()
            if r.code is not None} == {"wordpress"}
