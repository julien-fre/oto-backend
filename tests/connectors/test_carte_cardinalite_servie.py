"""La carte SERVIE porte la cardinalité de l'org, pas celle du code (oto-backend#732).

Le fichier voisin `test_connector_cardinality_override.py` prouve que la source unique
répond juste, et qu'aucune fonction ne sert le catalogue sans repasser par elle (sonde
AST). Celui-ci fait l'autre moitié, et c'est la moitié qui manquait à #732 : il exerce
les **deux surfaces réelles**, celles qu'un navigateur appelle, et lit la valeur qui
sort du fil.

Pourquoi les deux séparément. `auth.cardinality` est produit UNE fois
(`providers.public_catalog()`) et servi par DEUX chemins qui ne se croisent jamais —
la route écrite à la main `GET /api/connectors` et la capacité `connectors.me`
(`GET /api/me/connectors?verbose=true`). C'est exactement la topologie qui fabrique un
demi-correctif : réparer celui qu'on regarde, laisser l'autre. Le dashboard les
consomme tous les deux (`getConnectors` pour la grille, `getMyConnectors` pour le
panneau de connexion qui décide s'il propose un second compte).

⚠️ Ces tests posent une surcharge dans le dictionnaire de PROCESS, jamais en base : ce
qu'ils mesurent est la traversée du seam par la surface, pas le chargement — qui a son
test à base dans le fichier voisin.
"""
from __future__ import annotations

import asyncio
import json
import types

import pytest

from oto_mcp import providers
from oto_mcp.api import public as api_public
from oto_mcp.capabilities._types import ResolvedCtx
from oto_mcp.capabilities.connectors import selection as connectors_selection
from oto_mcp.connectors import activation as connector_activation
from oto_mcp.connectors import cardinality

SUB = "usr_carte"
ORG, AUTRE_ORG = 41, 42

# Le code dit MONO (session par personne) : le candidat exact d'un élargissement par
# surcharge — un connecteur que le registre refuse et qu'une org ouvre sans déploiement.
MONO_PAR_DEFAUT = "crunchbase"


@pytest.fixture(autouse=True)
def _surcharge_org_elargie(monkeypatch):
    """`MONO_PAR_DEFAUT` élargi pour ORG seulement. Le cache de surcharges est un état
    de PROCESS : posé et retiré ici, sinon un test verrait les lignes d'un autre."""
    cardinality._reset_for_tests()
    monkeypatch.setattr(cardinality, "_LOADED", True)
    monkeypatch.setattr(cardinality, "_OVERRIDES",
                        {("org", str(ORG), MONO_PAR_DEFAUT): "multi"})
    yield
    cardinality._reset_for_tests()


def _cardinalite(lignes: list, nom: str = MONO_PAR_DEFAUT) -> str:
    ligne = next((r for r in lignes if r["name"] == nom), None)
    assert ligne is not None, f"`{nom}` absent de la réponse — le banc ne mesure rien"
    return ligne["auth"]["cardinality"]


def test_le_banc_voit_bien_un_connecteur_mono_au_registre():
    """Témoin. Si `MONO_PAR_DEFAUT` devenait multi au code, toute la mesure passerait
    sans rien prouver : on lirait « multi_account » sans qu'aucune surcharge n'ait été
    lue. Un contrôle qui ne peut pas échouer n'est pas un contrôle."""
    brut = {r["name"]: r for r in providers.public_catalog()}
    assert brut[MONO_PAR_DEFAUT]["auth"]["cardinality"] == cardinality.SERVI_SINGLE


# ─── Surface 1 : `GET /api/connectors` (route écrite à la main, auth optionnelle) ──

def _requete(entete_auth: bool):
    from starlette.requests import Request
    entetes = [(b"authorization", b"Bearer jeton-de-test")] if entete_auth else []
    return Request({
        "type": "http", "method": "GET", "path": "/api/connectors",
        "query_string": b"", "root_path": "", "scheme": "http",
        "server": ("test", 80), "http_version": "1.1", "headers": entetes,
        "path_params": {},
    })


def _corps(reponse) -> list:
    return json.loads(bytes(reponse.body).decode())["connectors"]


def _catalogue_rest(monkeypatch, *, org, anonyme=False) -> list:
    """Appelle le VRAI handler, avec l'authentification et l'activation stubées."""
    async def _auth(request, verifier, **kw):
        return SUB, None
    monkeypatch.setattr(api_public, "_authenticate", _auth)
    monkeypatch.setattr(api_public.access, "is_platform_operator", lambda sub: False)
    monkeypatch.setattr(api_public.access, "current_org", lambda sub: org)
    monkeypatch.setattr(connector_activation, "exposed_connectors",
                        lambda org_id=None: {c.name for c in providers.REGISTRY.values()})
    reponse = asyncio.run(api_public.connectors_catalog(
        _requete(not anonyme), verifier=types.SimpleNamespace()))
    return _corps(reponse)


def test_la_route_publique_sert_la_cardinalite_de_l_ORG_du_requerant(monkeypatch):
    """⚠️ LE test de #732 sur cette surface. Avant le correctif, la ligne sortait du
    registre PUR : « single », quelle que soit l'org, alors que la garde d'écriture
    acceptait déjà un second compte pour ORG."""
    assert _cardinalite(_catalogue_rest(monkeypatch, org=ORG)) \
        == cardinality.SERVI_MULTI


def test_la_route_publique_ne_CONTAMINE_pas_l_org_voisine(monkeypatch):
    """La surcharge est scopée : une autre org lit ce que dit le code."""
    assert _cardinalite(_catalogue_rest(monkeypatch, org=AUTRE_ORG)) \
        == cardinality.SERVI_SINGLE


def test_la_vitrine_ANONYME_ignore_la_surcharge_d_une_ORG(monkeypatch):
    """Sans requérant il n'y a pas d'org de contexte. Le build du site vitrine appelle
    cette route sans en-tête : sa sortie ne doit pas se mettre à dépendre du réglage
    d'une org particulière — ce serait servir à tout le monde la carte de la première
    org venue."""
    assert _cardinalite(_catalogue_rest(monkeypatch, org=None, anonyme=True)) \
        == cardinality.SERVI_SINGLE


def test_la_vitrine_ANONYME_suit_en_revanche_la_surcharge_PLATEFORME(monkeypatch):
    """L'autre moitié de la règle, et elle n'est pas symétrique : une surcharge
    plateforme EST la réponse de la plateforme pour tout le monde, y compris pour qui
    n'est pas connecté. La laisser hors de la vitrine y afficherait un connecteur
    mono-compte que le serveur traite en multi partout ailleurs."""
    monkeypatch.setattr(cardinality, "_OVERRIDES",
                        {("platform", "platform", MONO_PAR_DEFAUT): "multi"})
    assert _cardinalite(_catalogue_rest(monkeypatch, org=None, anonyme=True)) \
        == cardinality.SERVI_MULTI


# ─── Surface 2 : `connectors.me` (le seam du panneau de connexion du dashboard) ────

def _catalogue_me(monkeypatch, org) -> list:
    """Appelle le VRAI `_visible_catalog`, avec activation et RBAC stubés."""
    monkeypatch.setattr(connectors_selection.connector_activation, "exposed_connectors",
                        lambda org_id=None: {c.name for c in providers.REGISTRY.values()})
    monkeypatch.setattr(connectors_selection.access, "rbac_denied_connectors",
                        lambda sub, org_id: set())
    monkeypatch.setattr(connectors_selection.access, "is_platform_operator",
                        lambda sub: False)
    return connectors_selection._visible_catalog(ResolvedCtx(sub=SUB, org_id=org))


def test_la_carte_de_mes_connecteurs_suit_l_org_active(monkeypatch):
    """C'est CETTE ligne que le panneau de connexion lit pour décider s'il propose un
    second compte (`ConnectorConnectionPanel`, oto-dashboard#121). Le geste offert par
    l'écran et celui accepté par le serveur doivent être le même."""
    assert _cardinalite(_catalogue_me(monkeypatch, ORG)) == cardinality.SERVI_MULTI
    assert _cardinalite(_catalogue_me(monkeypatch, AUTRE_ORG)) \
        == cardinality.SERVI_SINGLE


def test_les_deux_surfaces_disent_la_MEME_chose_a_la_meme_org(monkeypatch):
    """Le vrai risque d'un correctif à deux endroits n'est pas d'en rater un : c'est
    de les faire diverger. Une grille qui promet « multi » à côté d'un panneau qui ne
    propose rien est pire que les deux muets."""
    grille = {r["name"]: r["auth"]["cardinality"]
              for r in _catalogue_rest(monkeypatch, org=ORG)}
    panneau = {r["name"]: r["auth"]["cardinality"]
               for r in _catalogue_me(monkeypatch, ORG)}
    assert grille.keys() == panneau.keys(), \
        "les deux surfaces ne servent pas le même jeu — la comparaison ne mesure rien"
    ecarts = {n: (grille[n], panneau[n]) for n in grille
              if grille[n] != panneau[n]}
    assert not ecarts, f"les deux surfaces se contredisent : {ecarts}"
