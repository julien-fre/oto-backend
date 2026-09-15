"""Les surfaces servies SANS en-tête d'auth — ce qu'elles ne laissent pas sortir.

`oto_mcp/api/public.py` était à **49 %** le 15/09/2026. C'est le module dont le
moindre trou est le plus cher : ses handlers répondent à **n'importe qui**, sans
jeton, et deux d'entre eux sont lus par un PROGRAMME (le build de oto.cx, celui de
docs.oto.cx) qui republie ce qu'ils rendent sur le web ouvert. Un champ de trop ici
n'est pas une fuite vers un utilisateur : c'est une publication.

Trois régimes de deny-by-default y cohabitent, et ce banc tient les trois :

1. **Par ALLOWLIST de champs** — `_VITRINE_META` / `_VITRINE_ENTREE`. Le commentaire
   du module dit pourquoi : une liste de champs à RETIRER ne protège que du passé,
   elle est muette sur la colonne qui n'existe pas encore. On l'éprouve donc avec une
   entrée qui porte une colonne inventée : si elle sort, l'allowlist a été retournée
   en denylist par quelqu'un, et la prochaine colonne de `guide_library` fuitera.
2. **Par CONSTRUCTION** — `guides_library_public` appelle `list_guides_for()` sans
   `sub` ni `org_id`, et `guides_library_public_get` passe `scope="platform"`
   EXPLICITEMENT. Sans ce mot, `read_guide_scoped` cherche aussi org puis user : une
   route anonyme servirait le guide privé d'une org qui aurait choisi le même slug.
3. **Par FILTRE** — `connectors_catalog`, la seule surface mixte. Anonyme : activation
   plus exclusion des `platform_granted` (les bridges client-sensibles d'ADR 0003).
   Authentifié non-opérateur : activation de SON org. Opérateur : tout le registre,
   parce que sa vue sert justement à activer et désactiver.

⚠️ `outreach_unsubscribe` et `digest_unsubscribe` ne sont PAS ici : ils sont déjà
tenus au niveau route par `tests/test_outreach_optout.py` (jeton trafiqué qui n'écrit
rien, croisement des deux `typ`, page autoportée). Les redoubler n'ajouterait pas une
garde, seulement un second endroit à corriger.
"""
from __future__ import annotations

import pytest
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

from oto_mcp import access, db, guide_store, org_store, providers
from oto_mcp.api import base, public
from oto_mcp.connectors import activation as connector_activation
from oto_mcp.connectors import cardinality as connector_cardinality

# Une entrée de bibliothèque telle que la ligne SQL la porte : le contenu publiable
# ET les identifiants qui rattachent l'entrée à une org et à un compte.
ENTREE = {
    "id": 501, "slug": "prospection-b2b", "title": "Prospection B2B",
    "description": "Un guide", "author_kind": "org", "author_display": "Une org",
    "category": "vente", "tags": ["vente"], "visibility": "public", "version": 3,
    "created_at": "2026-09-01T10:00:00Z", "updated_at": "2026-09-02T10:00:00Z",
    "snippet": "…", "body_md": "# Titre\n\ncorps", "slots": {}, "source_slug": "src",
    # Ce qui ne doit JAMAIS sortir sur la vitrine :
    "published_by": "acme:u-42", "author_org_id": 168, "source_org_id": 12,
    "forked_from": 490,
    # La colonne de demain : personne ne la relira, l'allowlist doit la retenir seule.
    "colonne_ajoutee_plus_tard": "valeur-interne",
}


class _Claims:
    def __init__(self, sub: str):
        self.claims = {"sub": sub, "email": f"{sub}@public.invalid", "name": sub}


class _Verifier:
    async def verify_token(self, token: str):
        return _Claims(token)


@pytest.fixture
def journal():
    return {"list_library": [], "get_entry": [], "guides_for": [], "read_scoped": [],
            "exposed": [], "overlay": []}


@pytest.fixture
def client(monkeypatch, journal):
    monkeypatch.setattr("oto_mcp.account_suspension.refus", lambda sub: None)
    monkeypatch.setattr(base, "alias_drain_armed", lambda: False)
    monkeypatch.setattr(db, "upsert_user", lambda *a, **k: None)

    def _list_library(**kw):
        journal["list_library"].append(kw)
        return [dict(ENTREE)]

    def _get_entry(**kw):
        journal["get_entry"].append(kw)
        return dict(ENTREE)

    def _guides_for(sub=None, org_id=None):
        journal["guides_for"].append((sub, org_id))
        return [{"slug": "notice", "title": "Notice"}]

    def _read_scoped(slug, **kw):
        journal["read_scoped"].append((slug, kw))
        return {"slug": slug, "body_md": "# guide plateforme"}

    monkeypatch.setattr(org_store, "list_library", _list_library)
    monkeypatch.setattr(org_store, "get_library_entry", _get_entry)
    monkeypatch.setattr(guide_store, "list_guides_for", _guides_for)
    monkeypatch.setattr(guide_store, "read_guide_scoped", _read_scoped)
    monkeypatch.setattr(org_store, "preview_invitation",
                        lambda tok: {"email": "invite@exemple.invalid"} if tok == "bon-jeton" else None)
    monkeypatch.setattr(org_store, "preview_invitation_by_code",
                        lambda code: {"email": "invite@exemple.invalid"} if code == "ABC123" else None)
    monkeypatch.setattr(db, "get_doc_by_public_token",
                        lambda tok: {"title": "Partagé", "body_md": "# corps",
                                     "updated_at": "2026-09-01T10:00:00Z"}
                        if tok == "bon-jeton" else None)

    verifier = _Verifier()
    return TestClient(Starlette(routes=[
        Route("/api/version", public.version, methods=["GET"]),
        Route("/favicon.svg", public.favicon, methods=["GET"]),
        Route("/api/mcp/catalog", base.bind(public.mcp_catalog, mcp_instance=None),
              methods=["GET"]),
        Route("/api/connectors",
              base.bind(public.connectors_catalog, verifier=verifier), methods=["GET"]),
        Route("/api/guide-library", public.guide_library_public, methods=["GET"]),
        Route("/api/guide-library/{slug}", public.guide_library_public_get,
              methods=["GET"]),
        Route("/api/guides/library", public.guides_library_public, methods=["GET"]),
        Route("/api/guides/library/{slug}", public.guides_library_public_get,
              methods=["GET"]),
        Route("/api/invitations/code/{code}", public.invite_preview_by_code,
              methods=["GET"]),
        Route("/api/invitations/{token}", public.invite_preview, methods=["GET"]),
        Route("/api/public/docs/{token}", public.public_doc, methods=["GET"]),
        Route("/p/d/{token}", public.public_doc_view, methods=["GET"]),
    ]))


# ── 1. l'allowlist de champs de la vitrine ───────────────────────────────────

_INTERDITS = ("published_by", "author_org_id", "source_org_id", "id", "forked_from",
              "colonne_ajoutee_plus_tard")


def test_la_liste_publique_ne_sert_aucun_identifiant(client):
    """Les identifiants faisaient de la vitrine un annuaire des orgs et des comptes
    qui publient — servi sans jeton, donc indexable."""
    entree = client.get("/api/guide-library").json()["guides"][0]
    for champ in _INTERDITS:
        assert champ not in entree, f"« {champ} » ne doit pas sortir sur la vitrine"


def test_une_colonne_ajoutee_plus_tard_ne_fuit_pas(client):
    """LE test de l'allowlist. Une denylist laisserait passer `colonne_ajoutee_plus_tard`
    parce que personne n'aura pensé à l'y inscrire — c'est le seul cas qu'on ne relira
    pas, et donc le seul contre lequel la forme de la garde doit protéger."""
    entree = client.get("/api/guide-library").json()["guides"][0]
    assert set(entree) <= set(public._VITRINE_META)
    assert "colonne_ajoutee_plus_tard" not in entree


def test_le_detail_publie_le_corps_mais_toujours_pas_les_identifiants(client):
    """La fiche complète sert le markdown — c'est son objet — sans pour autant
    élargir l'allowlist aux identifiants."""
    fiche = client.get("/api/guide-library/prospection-b2b").json()
    assert fiche["body_md"] == "# Titre\n\ncorps"
    assert set(fiche) <= set(public._VITRINE_ENTREE)
    for champ in _INTERDITS:
        assert champ not in fiche


def test_les_deux_noms_servis_portent_la_MEME_projection(client):
    """Le préavis (#519) republie la liste sous l'ancien nom. Projeter APRÈS le
    doublage servirait des entrées réduites sous un nom et complètes sous l'autre —
    la fuite reviendrait par la porte de compatibilité."""
    corps = client.get("/api/guide-library").json()
    autres = [v for k, v in corps.items() if k != "guides" and isinstance(v, list)]
    assert autres, "le doublage de nom a disparu : ce test doit être revu, pas supprimé"
    for liste in autres:
        assert liste == corps["guides"]


def test_la_vitrine_ne_demande_JAMAIS_les_entrees_non_listees(client, journal):
    """`unlisted` = partageable par lien, pas publiable. Le passer à True ici mettrait
    dans le cliché du build des entrées que leur auteur a choisi de ne pas exposer."""
    client.get("/api/guide-library")
    client.get("/api/guide-library/prospection-b2b")
    assert journal["list_library"][0]["include_unlisted"] is False
    assert journal["get_entry"][0]["include_unlisted"] is False


def test_un_slug_inconnu_rend_404_nomme(client, monkeypatch):
    monkeypatch.setattr(org_store, "get_library_entry", lambda **kw: None)
    r = client.get("/api/guide-library/inexistant")
    assert r.status_code == 404 and r.json()["error"] == "unknown_entry"


@pytest.mark.parametrize("brut, attendu", [
    ("50", 50),
    ("9999", 200),          # borné : une vitrine ne sert pas la table entière
    ("abc", 100),           # illisible → défaut, pas 500
    ("", 100),
])
def test_la_borne_de_liste_est_appliquee_sans_jamais_lever(client, journal, brut,
                                                            attendu):
    """La borne est la seule protection d'une route anonyme contre une lecture de
    table complète — `?limit=` est choisi par l'appelant."""
    client.get("/api/guide-library", params={"limit": brut})
    assert journal["list_library"][0]["limit"] == attendu


def test_les_filtres_de_la_vitrine_sont_transmis_tels_quels(client, journal):
    client.get("/api/guide-library",
               params={"q": "prospection", "category": "vente", "author": "org"})
    passe = journal["list_library"][0]
    assert (passe["query"], passe["category"], passe["author_kind"]) == (
        "prospection", "vente", "org")


# ── 2. les guides PLATEFORME : deny-by-default par construction ──────────────

def test_la_bibliotheque_plateforme_n_interroge_ni_compte_ni_org(client, journal):
    """`list_guides_for()` sans argument ne rend que le scope plateforme. Passer un
    `sub` ou un `org_id` ici — même celui d'un requérant — servirait du contenu d'org
    sur une route anonyme."""
    assert client.get("/api/guides/library").status_code == 200
    assert journal["guides_for"] == [(None, None)]


def test_un_guide_est_lu_avec_un_scope_plateforme_EXPLICITE(client, journal):
    """Sans `scope="platform"`, `read_guide_scoped` cherche aussi org puis user : une
    org qui aurait choisi le même slug verrait son guide privé servi au public."""
    client.get("/api/guides/library/notice")
    assert journal["read_scoped"] == [("notice", {"scope": "platform"})]


def test_un_guide_plateforme_inconnu_rend_404_nomme(client, monkeypatch):
    monkeypatch.setattr(guide_store, "read_guide_scoped", lambda slug, **kw: None)
    r = client.get("/api/guides/library/inexistant")
    assert r.status_code == 404 and r.json()["error"] == "unknown_guide"


# ── 3. le catalogue de connecteurs : la seule surface mixte ──────────────────

@pytest.fixture
def catalogue(monkeypatch, journal):
    lignes = [
        {"name": "apollo", "availability": "self_serve"},
        {"name": "eteint", "availability": "self_serve"},
        {"name": "bridge", "availability": "platform_granted"},
        {"name": "scaleway", "availability": "platform_granted"},
    ]
    monkeypatch.setattr(providers, "public_catalog", lambda: [dict(c) for c in lignes])

    def _exposed(org_id=None):
        journal["exposed"].append(org_id)
        # `eteint` est éteint partout ; `scaleway` n'est ouvert qu'à l'org 5.
        return {"apollo", "bridge"} | ({"scaleway"} if org_id == 5 else set())

    def _overlay(rows, org):
        journal["overlay"].append(org)
        return rows

    monkeypatch.setattr(connector_activation, "exposed_connectors", _exposed)
    monkeypatch.setattr(connector_cardinality, "overlay_for_org", _overlay)
    monkeypatch.setattr(access, "current_org", lambda sub: 5)
    monkeypatch.setattr(access, "is_platform_operator", lambda sub: sub == "operateur")
    return lignes


def _noms(reponse):
    return {c["name"] for c in reponse.json()["connectors"]}


def test_la_vitrine_anonyme_ne_montre_aucun_connecteur_a_cle_plateforme(client,
                                                                        catalogue):
    """Les `platform_granted` sont deny-by-default : ce sont les bridges
    client-sensibles d'ADR 0003. Leur seul NOM dans un catalogue public dirait à qui
    lit la vitrine avec quels systèmes la plateforme s'intègre pour ses clients."""
    r = client.get("/api/connectors")
    assert r.status_code == 200
    assert _noms(r) == {"apollo"}


def test_la_vitrine_anonyme_n_interroge_l_activation_d_AUCUNE_org(client, catalogue,
                                                                  journal):
    """Sans requérant il n'y a pas d'org de contexte : passer autre chose que `None`
    ferait suivre à la vitrine publique la surcharge d'une org particulière."""
    client.get("/api/connectors")
    assert journal["exposed"] == [None] and journal["overlay"] == [None]


def test_un_connecteur_eteint_n_apparait_pas_meme_pour_un_membre(client, catalogue):
    """Le cran d'activation (ADR 0010) filtre EN AMONT de la visibilité : un
    connecteur master OFF sans override n'existe pas pour la vue produit."""
    r = client.get("/api/connectors", headers={"Authorization": "Bearer u-membre"})
    assert "eteint" not in _noms(r)


def test_un_membre_voit_ce_que_SON_org_a_ouvert(client, catalogue):
    r = client.get("/api/connectors", headers={"Authorization": "Bearer u-membre"})
    assert _noms(r) == {"apollo", "bridge", "scaleway"}


def test_l_operateur_voit_TOUT_le_registre_y_compris_l_eteint(client, catalogue):
    """Sa vue est celle de gouvernance : elle sert à activer et désactiver, donc elle
    doit montrer ce qui est éteint. C'est la seule exception au filtre, et elle est
    conditionnée au rôle, pas à la présence d'un en-tête."""
    r = client.get("/api/connectors", headers={"Authorization": "Bearer operateur"})
    assert _noms(r) == {"apollo", "eteint", "bridge", "scaleway"}


def test_un_bearer_invalide_ne_retombe_PAS_sur_la_vitrine_anonyme(client, catalogue,
                                                                  monkeypatch):
    """Le pire scénario de cette route mixte : un jeton refusé traité comme « pas de
    jeton ». L'appelant croirait voir le catalogue de son org et lirait celui de la
    vitrine — ou, selon le sens de l'erreur, l'inverse. Le refus doit être un refus."""
    class _Refuse:
        async def verify_token(self, token):
            return None

    r = TestClient(Starlette(routes=[
        Route("/api/connectors",
              base.bind(public.connectors_catalog, verifier=_Refuse()), methods=["GET"]),
    ])).get("/api/connectors", headers={"Authorization": "Bearer jeton-pourri"})
    assert r.status_code == 401 and r.json()["error"] == "invalid_token"


def test_l_activation_et_la_cardinalite_parlent_de_la_MEME_org(client, catalogue,
                                                               journal):
    """L'org est lue UNE fois et sert les deux. Deux lectures pourraient diverger (le
    seam dépend d'un en-tête et d'un contexte), et l'écran servirait alors la
    visibilité d'une org avec la cardinalité d'une autre."""
    client.get("/api/connectors", headers={"Authorization": "Bearer u-membre"})
    assert journal["exposed"] == [5] and journal["overlay"] == [5]


# ── 4. les jetons qui SONT le secret : invitation, doc partagé ───────────────

@pytest.mark.parametrize("chemin", ["/api/invitations/inconnu",
                                    "/api/invitations/code/ZZZZZZ"])
def test_une_invitation_inconnue_rend_404_sans_dire_pourquoi(client, chemin):
    """Ni « jeton inconnu » ni « invitation expirée » : le même refus pour les deux,
    sinon la route devient un oracle qui distingue un jeton qui a existé d'un jeton
    inventé."""
    r = client.get(chemin)
    assert r.status_code == 404 and r.json()["error"] == "invalid_or_expired"


def test_un_jeton_valide_rend_l_apercu_sans_rien_demander_de_plus(client):
    """L'aperçu existe pour accompagner quelqu'un qui n'a pas encore de compte : exiger
    une session ici rendrait l'invitation illisible avant l'inscription, c'est-à-dire
    à l'exact moment où elle sert."""
    r = client.get("/api/invitations/bon-jeton")
    assert r.status_code == 200 and r.json() == {"email": "invite@exemple.invalid"}


def test_un_code_court_n_est_pas_accepte_comme_jeton_long(client):
    """Deux chemins, deux stores : le code d'org (court, devinable) et le jeton long
    n'ouvrent pas la même porte. Les confondre rendrait le jeton aussi faible que le
    code."""
    assert client.get("/api/invitations/ABC123").status_code == 404
    assert client.get("/api/invitations/code/ABC123").status_code == 200


def test_un_jeton_de_doc_inconnu_rend_404_sur_les_deux_faces(client):
    assert client.get("/api/public/docs/inconnu").status_code == 404
    assert client.get("/p/d/inconnu").status_code == 404


def test_un_jeton_vide_ne_descend_meme_pas_jusqu_a_la_base(client, monkeypatch):
    """`if token else None` : sans ce court-circuit, une requête sur un chemin vide
    interrogerait la base avec `''` — et c'est exactement le genre de valeur qu'une
    colonne mal contrainte finit par contenir."""
    vus = []
    monkeypatch.setattr(db, "get_doc_by_public_token",
                        lambda tok: vus.append(tok) or None)
    assert client.get("/p/d/%20").status_code == 404
    client_vide = TestClient(Starlette(routes=[
        Route("/p/d{token}", public.public_doc_view, methods=["GET"])]))
    assert client_vide.get("/p/d").status_code == 404
    assert "" not in vus, "un jeton vide ne doit pas être présenté à la base"


def test_la_page_partagee_ne_fuit_ni_par_le_cache_ni_par_le_referer(client):
    """Le jeton EST le secret et il vit dans l'URL : un cache partagé le publierait,
    un `Referer` vers un lien du document le transmettrait au site visité."""
    r = client.get("/p/d/bon-jeton")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    assert r.headers["cache-control"] == "private, max-age=300"
    assert r.headers["referrer-policy"] == "no-referrer"


@pytest.mark.parametrize("accept, attendu", [
    ("application/json", "application/json"),
    ("text/markdown", "text/markdown"),
    ("text/html", "text/html"),
    ("*/*", "text/html"),
    ("", "text/html"),
])
def test_la_page_partagee_negocie_sur_Accept(client, accept, attendu):
    """La route est faite pour être lue par un agent (WebFetch, sans JS) autant que
    par un navigateur : c'est `Accept` qui départage, et le défaut reste l'HTML."""
    r = client.get("/p/d/bon-jeton", headers={"accept": accept} if accept else {})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith(attendu)


def test_le_markdown_servi_sort_avec_son_CORS(client):
    """Une `Response` nue sort sans CORS et le navigateur la jette (incident du
    09/09/2026). Le markdown passe par `base._file`, comme le ZIP et le PDF."""
    r = client.get("/p/d/bon-jeton", headers={"accept": "text/markdown",
                                              "origin": "https://manage.oto.cx"})
    assert r.status_code == 200 and r.text.startswith("# Partagé")
    assert r.headers["access-control-allow-origin"] == "https://manage.oto.cx"


def test_un_404_en_JSON_reste_du_JSON_et_pas_une_page_HTML(client):
    """Un agent qui demande du JSON doit pouvoir LIRE le refus. Lui rendre la page
    HTML « document introuvable » lui ferait parser du balisage pour comprendre un
    404."""
    r = client.get("/p/d/inconnu", headers={"accept": "application/json"})
    assert r.status_code == 404 and r.json()["error"] == "not_found"


def test_les_deux_faces_du_doc_servent_le_meme_contenu(client):
    par_json = client.get("/api/public/docs/bon-jeton").json()
    par_page = client.get("/p/d/bon-jeton",
                          headers={"accept": "application/json"}).json()
    assert par_json == par_page == {"title": "Partagé", "body_md": "# corps",
                                    "updated_at": "2026-09-01T10:00:00Z"}


# ── 5. les descriptifs publics : aucune valeur, jamais de 500 ────────────────

def test_le_catalogue_MCP_sans_instance_rend_une_liste_vide_pas_une_erreur(client):
    """La route est consommée par le build de l'autodoc : un 500 casserait la
    publication du site, là où une liste vide ne casse qu'une page."""
    r = client.get("/api/mcp/catalog")
    assert r.status_code == 200 and r.json() == {"tools": []}


def test_un_catalogue_MCP_qui_leve_rend_un_refus_nomme(client):
    class _QuiLeve:
        async def list_tools(self, run_middleware=False):
            raise RuntimeError("registre vide")

    r = TestClient(Starlette(routes=[
        Route("/api/mcp/catalog", base.bind(public.mcp_catalog, mcp_instance=_QuiLeve()),
              methods=["GET"])])).get("/api/mcp/catalog")
    assert r.status_code == 500
    assert r.json()["error"].startswith("list_tools_failed:")


def test_la_version_servie_ne_depend_d_AUCUNE_identite(client):
    """Sans auth, et c'est le point (oto#33) : un contrôle externe (Uptime Kuma, un
    script de déploiement, un agent) n'a pas de jeton, et doit pouvoir dater un
    changement de comportement AVANT d'avoir résolu quoi que ce soit d'identité. Si
    la réponse variait avec le porteur, la coordonnée cesserait d'être une coordonnée."""
    anonyme = client.get("/api/version")
    porteur = client.get("/api/version", headers={"Authorization": "Bearer u-1"})
    invalide = client.get("/api/version", headers={"Authorization": "Bearer pourri"})
    assert anonyme.status_code == porteur.status_code == invalide.status_code == 200
    assert anonyme.json() == porteur.json() == invalide.json()


def test_le_favicon_sort_avec_son_CORS_et_son_cache(client):
    r = client.get("/favicon.svg", headers={"origin": "https://oto.cx"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/svg+xml")
    assert r.headers["cache-control"] == "public, max-age=86400"
    assert r.headers["access-control-allow-origin"] == "https://oto.cx"


def test_le_catalogue_MCP_sert_les_quatre_champs_de_l_autodoc(client):
    """Le site vitrine reconstruit sa page « outils » depuis ce document. Un champ
    renommé ou absent ne casse rien ici et casse la page là-bas, au build suivant —
    à un endroit où plus personne ne regarde ce module."""
    class _Outil:
        """Ce que `list_tools` rend : nom, description, schémas — certains à None."""

        def __init__(self, name, description, parameters=None, output_schema=None):
            self.name = name
            self.description = description
            self.parameters = parameters
            self.output_schema = output_schema

    class _Instance:
        async def list_tools(self, run_middleware=False):
            return [_Outil("oto_doc", "  Écrire une page.  ", {"type": "object"}),
                    _Outil("oto_search", None)]

    r = TestClient(Starlette(routes=[
        Route("/api/mcp/catalog",
              base.bind(public.mcp_catalog, mcp_instance=_Instance()), methods=["GET"]),
    ])).get("/api/mcp/catalog")
    assert r.status_code == 200
    corps = r.json()
    assert corps["count"] == 2
    assert corps["tools"][0] == {"name": "oto_doc", "description": "Écrire une page.",
                                 "input_schema": {"type": "object"},
                                 "output_schema": None}
    assert corps["tools"][1]["description"] == "", (
        "une description absente devient une chaîne vide, jamais `null` : le build de "
        "la vitrine concatène ce champ")


def test_le_descriptif_openapi_est_DERIVE_de_la_table_de_routes_vivante(client):
    """Dérivé, donc jamais désynchronisé (c'est tout l'argument du module). On le
    vérifie en montant une table minuscule : ce qui y est monté doit s'y retrouver, et
    le serveur annoncé doit être celui par lequel on est entré."""
    app = Starlette(routes=[
        Route("/openapi.json", public.openapi_doc, methods=["GET"]),
        Route("/api/version", public.version, methods=["GET"]),
    ])
    doc = TestClient(app, base_url="https://mcp.exemple.test").get(
        "/openapi.json").json()
    assert doc["openapi"].startswith("3.")
    assert "/api/version" in doc["paths"]
    assert doc["servers"][0]["url"] == "https://mcp.exemple.test"


def test_le_descriptif_openapi_ne_publie_AUCUNE_route_admin():
    """`/api/admin/*` décrit la gouvernance de la plateforme : la surface d'un
    opérateur n'a pas à être publiée sur un document que le build de docs.oto.cx
    republie."""
    app = Starlette(routes=[
        Route("/openapi.json", public.openapi_doc, methods=["GET"]),
        Route("/api/admin/orgs", public.version, methods=["GET"]),
        Route("/api/version", public.version, methods=["GET"]),
    ])
    doc = TestClient(app).get("/openapi.json").json()
    assert "/api/version" in doc["paths"]
    assert not [c for c in doc["paths"] if c.startswith("/api/admin")]
