"""Un chemin REST renommé répond encore — en **308**, et il annonce sa date (#519).

Lot B2 de #519. Renommer un chemin, c'est casser tout appelant qui vit hors de ce
dépôt : le build de la vitrine, un `fetch` de navigateur, une intégration tierce, un
`curl` dans un script. Alors l'ancien chemin reste monté, ne fait rien d'autre que
rediriger, et s'en va à une date écrite (lot D, #526).

Ce que ces tests gardent, dans l'ordre de ce qui coûterait le plus cher :

1. **Un 308, pas un 301.** Un 301/302 autorise le client à retomber en GET : un
   `POST …/publish` deviendrait un GET sur la nouvelle route — 405, ou pire, un
   no-op silencieux. Le 308 conserve la méthode et le corps.
2. **La query string survit.** La vitrine appelle `…/library?limit=200` ; un
   `Location` qui la perdrait rendrait 100 entrées au lieu de 200, sans qu'aucun
   code d'erreur ne le dise. C'est le genre de régression qu'on découvre au trafic.
3. **La cible EXISTE.** Un alias qui pointe un chemin mort est pire qu'un retrait
   sec : le client suit la redirection et reçoit un 404 qui ne nomme rien.
4. **Les en-têtes CORS sont sur la REDIRECTION.** Un navigateur vérifie CORS sur
   chaque réponse d'une chaîne : une 308 nue fait échouer le `fetch` cross-origin de
   la vitrine. Le préflight `OPTIONS`, lui, n'est jamais redirigé — sinon le
   navigateur abandonne avant d'essayer.
5. **Les alias sont montés EN DERNIER, et aucun n'éclipse une vraie route.** Un
   placeholder trop gourmand posé trop haut avalerait un chemin littéral servi.
6. **Le document OpenAPI les dit dépréciés**, avec le chemin de remplacement et la
   date — et sans voler l'`operationId` d'une autre entrée.
"""
from __future__ import annotations

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from oto_mcp import deprecations, openapi
from oto_mcp.api import routes as api_routes

_ORIGINE = "https://oto.cx"          # une origine réellement autorisée (`_allowed_origins`)


class _FauxVerifieur:
    """`make_routes` ne fait que CAPTURER le verifier — jamais appelé au montage."""


def _table():
    return api_routes.make_routes(_FauxVerifieur(), mcp_instance=None)


@pytest.fixture(scope="module")
def client():
    return TestClient(Starlette(routes=_table()))


def _resout(table, methode: str, chemin: str):
    """La route que Starlette servirait pour (méthode, chemin) — la PREMIÈRE qui
    matche entièrement, exactement comme le routeur."""
    scope = {"type": "http", "method": methode, "path": chemin, "headers": [],
             "path_params": {}, "root_path": "", "query_string": b""}
    for r in table:
        m, child = r.matches(scope)
        if m.name == "FULL":
            return r, child.get("path_params", {})
    return None, {}


def _exemple(chemin: str) -> str:
    """Un chemin concret : chaque placeholder reçoit une valeur plausible."""
    return chemin.replace("{slug}", "un-slug").replace("{id}", "42") \
                 .replace("{doctrine_id}", "42").replace("{guide_id}", "42") \
                 .replace("{scope}", "org")


# ── 1 & 2. 308, méthode conservée, query string reportée ────────────────────

@pytest.mark.parametrize("alias", deprecations.REST, ids=lambda a: f"{a.verbe} {a.ancien}")
def test_lancien_chemin_redirige_en_308_vers_le_nouveau(client, alias):
    r = client.request(alias.verbe, _exemple(alias.ancien), follow_redirects=False)
    assert r.status_code == 308, (
        f"{alias.verbe} {alias.ancien} répond {r.status_code}. Un 301/302 autorise le "
        "client à retomber en GET — sur un POST, c'est un no-op silencieux.")
    assert r.headers["location"] == _exemple(alias.nouveau)


def test_la_query_string_est_reportee(client):
    """Le build de la vitrine appelle `…/library?limit=200` : la perdre rendrait 100
    entrées au lieu de 200, sans le moindre code d'erreur."""
    r = client.get("/api/doctrines/library?limit=200&q=x", follow_redirects=False)
    assert r.headers["location"] == "/api/guide-library?limit=200&q=x"


def test_lavis_de_retrait_est_dans_les_en_tetes(client):
    """Un intégrateur qui lit ses logs voit la date sans ouvrir la doc."""
    r = client.get("/api/doctrines/library", follow_redirects=False)
    assert r.headers["deprecation"] == "true"
    assert r.headers["sunset"] == deprecations.date_de_retrait()


# ── 3. La cible existe et n'est pas, elle-même, un alias ────────────────────

@pytest.mark.parametrize("alias", deprecations.REST, ids=lambda a: a.nouveau)
def test_la_cible_de_lalias_est_une_route_servie(alias):
    table = _table()
    route, _ = _resout(table, alias.verbe, _exemple(alias.nouveau))
    assert route is not None, (
        f"`{alias.nouveau}` n'est servi par aucune route : la redirection mène à un "
        "404 qui ne nomme rien. Pire qu'un retrait sec.")
    assert getattr(route.endpoint, "__name__", "") != "alias_deprecie", (
        f"`{alias.nouveau}` est lui-même un alias : une chaîne de redirections, dont "
        "un maillon partira au lot D en cassant l'autre.")


# ── 4. Le préflight n'est jamais redirigé ───────────────────────────────────

@pytest.mark.parametrize("alias", deprecations.REST, ids=lambda a: a.ancien)
def test_le_preflight_nest_pas_redirige(alias):
    """Un `OPTIONS` qui répondrait 308 ferait abandonner le navigateur avant même
    d'essayer la requête réelle."""
    route, _ = _resout(_table(), "OPTIONS", _exemple(alias.ancien))
    assert route is not None
    assert getattr(route.endpoint, "__name__", "") == "options_handler"


def test_la_redirection_porte_les_en_tetes_cors(client):
    """Le navigateur vérifie CORS sur CHAQUE réponse d'une chaîne de redirections."""
    r = client.get("/api/doctrines/library", follow_redirects=False,
                   headers={"origin": _ORIGINE})
    assert r.headers.get("access-control-allow-origin") == _ORIGINE


def test_sans_origine_pas_den_tete_cors(client):
    """Inertie : un appel serveur-à-serveur (le build de la vitrine) reçoit
    exactement ce qu'il recevait — pas d'en-tête inventé."""
    r = client.get("/api/doctrines/library", follow_redirects=False)
    assert "access-control-allow-origin" not in r.headers


# ── 5. Les alias sont derniers, et n'éclipsent rien ─────────────────────────

def test_les_alias_sont_montes_en_dernier():
    """Un alias ne doit pouvoir capturer que ce que rien d'autre ne sert. Monté plus
    haut, un de ses placeholders éclipserait une vraie route en silence."""
    table = _table()
    positions = [i for i, r in enumerate(table)
                 if getattr(r.endpoint, "__name__", "") == "alias_deprecie"]
    assert positions, "aucun alias monté — table vide ou renommage du handler"
    premier = min(positions)
    apres = [r.path for r in table[premier:]
             if getattr(r.endpoint, "__name__", "") not in
             ("alias_deprecie", "options_handler")]
    assert not apres, (
        f"{apres} sont montées APRÈS le premier alias déprécié. Les alias se montent "
        "en dernier (cf. `routes.make_routes`) — sinon un placeholder d'alias peut "
        "avaler un chemin littéral servi juste en dessous.")


@pytest.mark.parametrize("alias", deprecations.REST, ids=lambda a: f"{a.verbe} {a.ancien}")
def test_chaque_ancien_chemin_atteint_bien_son_alias(alias):
    """Le cas qui mord : `…/doctrines/library` DOIT atteindre son alias, et non être
    avalé par `…/doctrines/{doctrine_id}`, qui capture un segment. C'était le cas
    AVANT ce lot — le chemin `/api/me/doctrines/library` était inatteignable (400
    `invalid_input` sur un id qui vaut « library »). L'ordre de `deprecations.REST`
    est ce qui le règle, et ce test est ce qui le garde."""
    route, _ = _resout(_table(), alias.verbe, _exemple(alias.ancien))
    assert route is not None and route.path == alias.ancien, (
        f"{alias.verbe} {alias.ancien} est servi par `{route.path if route else None}` : "
        "un chemin littéral doit précéder le placeholder qui l'engloberait.")


# ── 6. Le document le dit, avec le remplaçant et la date ────────────────────

@pytest.mark.parametrize("alias", deprecations.REST, ids=lambda a: a.ancien)
def test_lopenapi_marque_lancien_chemin_deprecie(alias):
    doc = openapi.build()
    op = doc["paths"][alias.ancien][alias.verbe.lower()]
    assert op["deprecated"] is True
    assert alias.nouveau in op["summary"], (
        "« déprécié » sans chemin de remplacement n'est qu'un reproche")
    assert deprecations.date_de_retrait() in op["summary"]
    assert "308" in op["responses"]


def test_les_nouveaux_chemins_sont_documentes():
    doc = openapi.build()
    manquants = [a.nouveau for a in deprecations.REST
                 if a.nouveau.startswith("/api/me/") and a.nouveau not in doc["paths"]]
    assert not manquants, f"chemins d'aujourd'hui absents du document : {manquants}"


def test_un_alias_ne_vole_pas_loperationid_dune_autre_entree():
    """Un `operationId` est unique dans un document OpenAPI : deux entrées qui le
    partagent font disparaître une méthode d'un client généré, en silence.

    ⚠️ Portée VOLONTAIREMENT réduite aux alias. Le document porte des doublons
    ANTÉRIEURS à ce lot (une capacité à plusieurs bindings donne le même id à ses
    trois chemins) : les corriger renommerait des méthodes chez des clients déjà
    générés, ce qui est une décision, pas un nettoyage."""
    doc = openapi.build()
    anciens = {a.ancien for a in deprecations.REST}
    ids_alias = {op["operationId"] for p, item in doc["paths"].items() if p in anciens
                 for op in item.values()}
    ids_autres = {op["operationId"] for p, item in doc["paths"].items()
                  if p not in anciens for op in item.values()}
    assert not (ids_alias & ids_autres), (
        f"{sorted(ids_alias & ids_autres)} : un alias déprécié porte l'operationId "
        "d'une entrée vivante. L'id suit la CAPACITÉ — quand la clé n'a pas changé, "
        "c'est le NOUVEAU chemin qui en hérite, et l'alias en reçoit un dérivé de son "
        "chemin (cf. `deprecations.AliasRest.operation_id`).")


# ── 7. Les clés de capacité ont bien basculé ────────────────────────────────

def test_le_registre_ne_porte_plus_les_anciennes_cles():
    from oto_mcp.capabilities import registry
    cles = {c.key for c in registry.CAPABILITIES}
    restantes = sorted(a for a in deprecations.CAPACITES if a in cles)
    assert not restantes, f"clés de capacité non renommées : {restantes}"
    manquantes = sorted(n for n in deprecations.CAPACITES.values() if n not in cles)
    assert not manquantes, f"clés d'aujourd'hui absentes du registre : {manquantes}"
