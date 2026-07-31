"""Le geste « connecter » est DÉCLARÉ par le connecteur, dérivé partout ailleurs.

Ce que ces tests empêchent de revenir : le dashboard montait le widget de consentement
derrière `['zoho','zohodesk','zohoanalytics'].includes(name)`. Salesforce, qui a pourtant
la même forme côté backend, n'y était pas — donc aucun bouton, et un client ne pouvait
pas terminer sa connexion. La correction n'est pas « ajouter un nom » : c'est que plus
personne n'ait à en connaître.
"""
from __future__ import annotations

import pytest
from fastmcp import FastMCP

from oto_mcp import connector_flow, providers, status_hints
from oto_mcp.tools import register_all


@pytest.fixture(scope="module", autouse=True)
def _declarations():
    register_all(FastMCP("connector-flow-probe"))


# --- le contrat du descripteur -------------------------------------------------

def test_les_connecteurs_a_flux_sont_ceux_quon_attend():
    assert set(connector_flow.entries()) == {
        "zoho", "zohodesk", "zohoanalytics", "salesforce"}


def test_le_descripteur_ne_porte_ni_url_ni_nom_de_capacite():
    """Le catalogue `/api/connectors` est servi SANS authentification : y publier des
    chemins internes ferait de la surface d'attaque un effet de bord de la doc. Le
    chemin est fixe et connu du client, il n'a pas à voyager."""
    for name in connector_flow.entries():
        blob = repr(connector_flow.describe(name))
        for interdit in ("http", "/api/", "me.", "oto_"):
            assert interdit not in blob, f"{name} : « {interdit} » dans le descripteur"


def test_un_choix_ferme_a_toujours_des_options():
    """Un select vide est indémarrable. La déclaration le refuse déjà ; ce test fige
    la garantie pour les descripteurs réellement servis."""
    for name in connector_flow.entries():
        for p in connector_flow.describe(name)["params"]:
            if p["required"]:
                assert p["options"] or p["default"], f"{name}.{p['name']}"


def test_les_regions_zoho_ne_vivent_quici():
    """Les 6 data centers étaient recopiés à plusieurs endroits, dont un libellé de
    registre qui en annonçait un que le code REJETTE. Le descripteur est désormais leur
    domicile, et il est dérivé de la table de résolution elle-même."""
    from oto_mcp.tools.zoho import _DC_DOMAINS
    options = {o["value"] for o in connector_flow.describe("zoho")["params"][0]["options"]}
    assert options == set(_DC_DOMAINS), "le descripteur a divergé de la table de domaines"


def test_le_catalogue_expose_la_forme_et_rien_pour_les_autres():
    cat = {c["name"]: c for c in providers.public_catalog()}
    assert cat["salesforce"]["connect"]["label"]
    assert cat["serper"]["connect"] is None       # 56 connecteurs sur 70
    assert cat["unipile"]["connect"] is None      # flux hébergé, pas encore déclaré ici


# --- ce que le front n'a plus le droit de faire --------------------------------

def test_tout_connecteur_a_deux_temps_declare_son_flux():
    """TOTALITÉ, côté flux. Un connecteur qui annonce « mon credential se complète
    ailleurs » (`status_hints.register_state`) DOIT dire où — sinon la fiche affiche
    « il reste une étape » sans le moyen de la faire. C'est exactement l'état dans
    lequel Salesforce a passé la semaine."""
    manquants = sorted(
        n for n in providers.REGISTRY
        if status_hints.has_state(n) and not connector_flow.supports(n))
    assert not manquants, (
        f"{manquants} déclarent un credential qui se complète hors formulaire mais "
        "aucun flux : le front ne pourra pas proposer le geste.")


def test_le_demarrage_generique_partage_le_handler_des_capacites_nommees():
    """Il n'existe qu'UNE façon de démarrer un consentement. Les chemins par connecteur
    (`/api/zoho/oauth/start`) survivent le temps que le dashboard bascule sur le chemin
    fixe — ils appellent le MÊME `start_for`, donc les deux surfaces ne peuvent pas
    diverger. À retirer quand le front en prod n'appelle plus que `/connect`."""
    from oto_mcp.capabilities import salesforce_connect, zoho_connect
    import inspect
    assert "start_for" in inspect.getsource(zoho_connect._start)
    assert "start_for" in inspect.getsource(salesforce_connect._start)
    # et le flux générique passe par les mêmes
    from oto_mcp.tools import salesforce as sf_tools, zoho as zoho_tools
    assert "start_for" in inspect.getsource(sf_tools._start_flow)
    assert "start_for" in inspect.getsource(zoho_tools._start_flow)


def test_la_capacite_generique_est_montee_sur_un_chemin_fixe():
    from oto_mcp.capabilities import registry
    cap = next(c for c in registry.CAPABILITIES if c.key == "me.connector_connect")
    assert cap.rest is not None and cap.rest.path == "/api/me/connectors/{name}/connect"
    # le nom du connecteur voyage en PARAMÈTRE, il n'est pas dans le chemin
    for n in providers.REGISTRY:
        assert n not in cap.rest.path


# --- l'URL de retour : dérivée, et du bon côté ---------------------------------

def test_lurl_de_retour_est_derivee_de_lenvironnement(monkeypatch):
    """Elle vivait en PROSE dans la doc du connecteur, domaine de prod écrit à la main.
    Un utilisateur de preprod y lisait donc une URL que SON backend n'utilise pas, et le
    consentement échouait sur un `redirect_uri_mismatch` dont le message accusait sa
    Connected App. Désormais elle suit `OTO_MCP_PUBLIC_URL`."""
    monkeypatch.setenv("OTO_MCP_PUBLIC_URL", "https://mcp.example.test")
    assert connector_flow.callback_url("salesforce") == (
        "https://mcp.example.test/api/salesforce/oauth/callback")


def test_lurl_de_retour_nest_PAS_dans_le_catalogue_anonyme():
    """`/api/connectors` est servie sans authentification. Le descripteur public porte
    la FORME du geste, pas les adresses — c'est la projection authentifiée qui ajoute
    l'URL, parce que c'est là qu'elle sert à quelqu'un."""
    for name in connector_flow.entries():
        assert "callback_url" not in (connector_flow.describe(name) or {})
    cat = {c["name"]: c for c in providers.public_catalog()}
    assert "callback_url" not in (cat["salesforce"]["connect"] or {})


def test_aucune_url_de_retour_ecrite_en_dur_dans_la_prose_client():
    """TRIPWIRE — la doc et les messages d'erreur lus par un CLIENT ne doivent plus
    contenir de domaine en dur : ils sont servis aux deux environnements."""
    import pathlib as _p
    for f in (_p.Path("oto_mcp/connector_docs.py"),
              _p.Path("oto_mcp/tools/salesforce.py"),
              _p.Path("oto_mcp/tools/zoho.py")):
        src = f.read_text(encoding="utf-8")
        for ligne in src.splitlines():
            if "oauth/callback" in ligne and "mcp.oto." in ligne and not ligne.lstrip().startswith("#"):
                raise AssertionError(f"{f} code une URL de retour en dur : {ligne.strip()[:110]}")
