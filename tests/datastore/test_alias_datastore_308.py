"""Les anciens chemins du datastore répondent en 308 — et le navigateur les suit.

**Pourquoi ces alias existent, et ce n'est pas la douceur.** Le renommage
`namespace` → `datastore` (08/09/2026) devait être une bascule sèche. Un consommateur
a mesuré qu'il ne POUVAIT PAS porter les deux noms : une garde de son dépôt exige que
tout chemin appelé existe **verbatim** dans le contrat servi, donc déclarer un chemin
que le back ne sert pas échoue — dans les deux sens. Sans alias, sa bascule et celle du
back devaient coïncider à la seconde, sans marge et sans retour arrière possible.

⚠️ **Ces alias sont DÉRIVÉS des routes montées, jamais listés à la main.** À l'écriture
de ce banc, la dérivation en rendait **24** là où la liste manuelle envisagée en
comptait six : dix-huit chemins auraient été oubliés, et un chemin oublié ne lève rien
— il rend 404 chez quelqu'un qui croyait la redirection posée.

**Ce que ce banc éprouve est ce qu'un `curl` ne montre pas.** Les appels partent d'un
navigateur avec un en-tête `Authorization`, donc en CORS, et une redirection après
préflight a longtemps été fragile. Un alias qui marche en ligne de commande et casse
dans le navigateur serait le pire des trois mondes : il aurait l'air de marcher.
"""
from __future__ import annotations

import pytest
from starlette.applications import Starlette
from starlette.responses import Response
from starlette.testclient import TestClient

from oto_mcp import deprecations
from oto_mcp.api import alias_routes
from oto_mcp.api.base import _cors_headers

ORIGINE = "https://app.oto.ninja"
ANCIEN_ROW = "/api/datastore/namespaces/leads/rows/42"


@pytest.fixture()
def client():
    async def options_handler(request):
        return Response(status_code=204,
                        headers=_cors_headers(request.headers.get("origin")))

    return TestClient(Starlette(routes=alias_routes.make_routes(options_handler)))


def test_la_derivation_couvre_toutes_les_routes_montees():
    """Aucun chemin `/api/datastores` ne doit être sans alias — et c'est la dérivation
    qui le garantit, pas la vigilance de qui ajoute une route."""
    from oto_mcp.capabilities.registry import CAPABILITIES

    montes = {(c.rest.verb, c.rest.path) for c in CAPABILITIES
              if getattr(c, "rest", None) and getattr(c.rest, "path", "")
              and c.rest.path.startswith("/api/datastores")}
    alias = {(a.verbe, a.nouveau) for a in deprecations._alias_datastore()}
    assert montes and montes == alias, montes ^ alias


def test_le_preflight_passe_avec_authorization(client):
    """⚠️ Le préflight n'est JAMAIS redirigé : un navigateur qui reçoit un 308 sur son
    `OPTIONS` abandonne avant d'essayer la vraie requête."""
    r = client.options(ANCIEN_ROW, headers={
        "Origin": ORIGINE,
        "Access-Control-Request-Method": "PATCH",
        "Access-Control-Request-Headers": "authorization,content-type"})
    assert r.status_code == 204
    assert r.headers["access-control-allow-origin"] == ORIGINE
    assert "PATCH" in r.headers["access-control-allow-methods"]
    assert "Authorization" in r.headers["access-control-allow-headers"]


def test_le_308_preserve_la_methode_et_porte_le_CORS(client):
    """308 et pas 301/302 : un 30x plus faible autorise le client à retomber en GET —
    un `PATCH` deviendrait une lecture, donc un no-op silencieux sur une écriture."""
    r = client.patch(ANCIEN_ROW + "?fields=a,b", json={"patch": {"x": 1}},
                     headers={"Origin": ORIGINE}, follow_redirects=False)
    assert r.status_code == 308
    assert r.headers["location"] == "/api/datastores/leads/rows/42?fields=a,b"
    # ⚠️ Le navigateur vérifie CORS sur CHAQUE réponse de la chaîne : une 308 nue
    # ferait échouer le `fetch`, et l'erreur ne nommerait pas la redirection.
    assert r.headers["access-control-allow-origin"] == ORIGINE


def test_la_valeur_voyage_meme_si_le_placeholder_change_de_nom(client):
    """`{namespace}` devient `{datastore}` : seule la VALEUR capturée traverse."""
    r = client.get("/api/datastore/namespaces/mon-tableau/schema",
                   headers={"Origin": ORIGINE}, follow_redirects=False)
    assert r.status_code == 308
    assert r.headers["location"] == "/api/datastores/mon-tableau/schema"


def test_l_alias_annonce_SA_date_de_retrait_pas_celle_d_un_autre_lot(client):
    """⚠️ Défaut mesuré le 08/09/2026 : ces alias servaient la date du renommage #519,
    annoncée dix jours plus tôt. Un intégrateur qui lit ses en-têtes aurait daté son
    travail sur une échéance qui n'était pas la sienne."""
    r = client.get("/api/datastore/namespaces", headers={"Origin": ORIGINE},
                   follow_redirects=False)
    assert r.headers["Deprecation"] == "true"
    assert r.headers["Sunset"] == deprecations.RETRAIT_DATASTORE.strftime("%d/%m/%Y")
    assert r.headers["Sunset"] != deprecations.date_de_retrait()
