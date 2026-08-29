"""Le journal n'écrit jamais en clair un paramètre DÉCLARÉ porteur d'un secret.

Le défaut d'origine (#558) n'était pas une garde oubliée, c'était une allowlist de
FORMES : `_normalize_route` réduisait ce qui *ressemble* à un identifiant (numérique,
UUID) et laissait passer tout le reste — donc les quatre routes dont le secret est
DANS le chemin. Ces tests gardent la propriété qui remplace la forme : un segment lié
à un paramètre de route dont le NOM est déclaré secret est réduit, quelle que soit son
allure, et pour toute route présente ou future qui déclare ce nom.
"""
from __future__ import annotations

import pytest

from oto_mcp import journal_secrets as js


@pytest.fixture(autouse=True)
def _table_de_routes_reelle():
    """Déclare la VRAIE table de routes servie — pas une liste fabriquée ici."""
    from oto_mcp.api import routes as api_routes
    api_routes.make_routes(object(), mcp_instance=None)
    yield


# --- La propriété ------------------------------------------------------------

def test_la_table_de_routes_declare_ses_parametres_secrets():
    """Cliquet : si `make_routes` cesse de déclarer, tout le reste devient inerte
    EN SILENCE (le middleware masquerait zéro segment sans qu'un test rougisse)."""
    declares = js.declared_secret_routes()
    chemins = {shape for shape, _ in declares}
    assert len(declares) >= 4, (
        "La table de routes ne déclare plus aucun paramètre secret : le masquage du "
        "journal est devenu inerte. `make_routes` doit appeler `journal_secrets."
        "declare_routes` sur la table qu'elle sert.")
    # Les quatre routes de #558, nommées : leur retrait doit se voir dans un diff.
    attendues = {
        ("", "api", "upload", None),
        ("", "api", "public", "docs", None),
        ("", "api", "invitations", None),
        ("", "api", "invitations", "code", None),
    }
    assert attendues <= chemins, f"routes à secret manquantes : {attendues - chemins}"


@pytest.mark.parametrize("chemin,attendu", [
    ("/api/upload/eyJ0eXAiOiJ1cGxvYWQifQ.c2ln", "/api/upload/:token"),
    ("/api/public/docs/inv_Zm9vYmFy", "/api/public/docs/:token"),
    ("/api/invitations/inv_Zm9vYmFy", "/api/invitations/:token"),
    ("/p/d/inv_Zm9vYmFy", "/p/d/:token"),
])
def test_un_segment_lie_a_un_parametre_secret_est_reduit(chemin, attendu):
    route, secrets = js.route_and_secrets(chemin)
    assert route == attendu
    assert secrets and "token" in secrets


def test_la_route_la_plus_specifique_gagne():
    """`/api/invitations/{token}` et `/api/invitations/code/{code}` se chevauchent :
    sans arbitrage, `code` serait lu comme le jeton et la route perdrait son nom."""
    route, secrets = js.route_and_secrets("/api/invitations/code/ABC1234")
    assert route == "/api/invitations/code/:code"
    assert set(secrets) == {"code"}


def test_la_reduction_par_forme_est_conservee():
    """Ce que faisait déjà `_normalize_route` ne change pas : l'agrégation du
    monitoring lit `tool`, et un changement de vocabulaire couperait les séries."""
    assert js.route_and_secrets("/api/orgs/7/audit-log")[0] == "/api/orgs/:id/audit-log"
    assert js.route_and_secrets("/api/me")[0] == "/api/me"
    uuid = "/api/x/3f2504e0-4f89-41d3-9a0c-0305e82c3301/y"
    assert js.route_and_secrets(uuid)[0] == "/api/x/:id/y"


def test_un_chemin_plus_long_que_la_route_est_reduit_quand_meme():
    """Un 404 sur `/api/upload/<jeton>/x` n'atteint aucun handler — mais il est
    journalisé comme tout `/api/*`, donc son jeton doit tomber aussi."""
    route, secrets = js.route_and_secrets("/api/upload/inv_Zm9vYmFy/extra")
    assert "inv_Zm9vYmFy" not in route
    assert secrets and "token" in secrets


# --- Le masque ---------------------------------------------------------------

def test_le_masque_ne_contient_pas_la_valeur():
    m = js.mask("inv_Zm9vYmFyBAZ")
    assert "inv_" not in m and "Zm9vYmFy" not in m
    assert m.startswith("#") and len(m) <= 16


def test_le_masque_est_stable_donc_correlable():
    assert js.mask("ABC1234") == js.mask("ABC1234")
    assert js.mask("ABC1234") != js.mask("ABC1235")


def test_le_masque_est_CLE_donc_non_inversible(monkeypatch):
    """Un code d'invitation fait 7 caractères sur un alphabet de 30 (~34 bits) : un
    sha256 NU se retrouve par force brute en quelques secondes par quiconque lit le
    journal. Le masque doit donc dépendre d'un secret du serveur — c'est aussi la
    raison de ne PAS garder « les 8 derniers », qui exposeraient le code ENTIER."""
    import hashlib
    monkeypatch.setattr(js, "_KEY", None, raising=False)
    monkeypatch.setenv("OTO_MCP_OAUTH_STATE_SECRET", "cle-a")
    a = js.mask("ABC1234")
    monkeypatch.setattr(js, "_KEY", None, raising=False)
    monkeypatch.setenv("OTO_MCP_OAUTH_STATE_SECRET", "cle-b")
    b = js.mask("ABC1234")
    assert a != b, "le masque ne dépend pas de la clé du serveur → inversible"
    nu = "#" + hashlib.sha256(b"ABC1234").hexdigest()[:12]
    assert a != nu and b != nu, "le masque est un sha256 nu"


# --- Les arguments d'outil ---------------------------------------------------

def test_un_champ_de_capacite_declare_secret_est_connu_du_journal():
    """`oto_org op=accept_invite` porte le MÊME jeton d'invitation que la route —
    par l'autre face. La déclaration vit sur le champ, pas dans une liste d'outils."""
    assert js.secret_arg_names("oto_org") == frozenset({"token", "code"})


def test_un_argument_non_declare_reste_lisible():
    """Pas de masquage par NOM seul : `droit_article(code='CT')` n'est pas un secret,
    et un journal qui le cache coûte une lecture sans rien protéger."""
    assert js.secret_arg_names("droit_article") == frozenset()
    assert js.secret_arg_names(None) == frozenset()
