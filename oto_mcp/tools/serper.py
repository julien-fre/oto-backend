"""Serper — recherche Google (web, images, vidéos, news, places, maps, reviews,
shopping, scholar, patents, lens, autocomplete) + scraping de page.

Clé résolue par appel via `access.resolve_api_key("serper")` : user key
(`/account`) si posée, sinon platform key + quota daily pour les members.
Guests doivent obligatoirement poser leur propre clé.
"""
from __future__ import annotations

import re
from typing import Literal, Optional

from fastmcp import FastMCP
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData, INVALID_REQUEST

from .. import access, output_projection

# Ce que le défaut retire d'une page de résultats Google (rendu par `full=True`). Aucune
# de ces clés n'est du bruit dans l'absolu — knowledge graph et sitelinks servent parfois
# — mais aucune ne sert la boucle courante d'un agent (titre + lien + extrait), et
# ensemble elles pèsent ~34 % d'une réponse (mesuré : 3 105 c. pour 5 résultats, dont 542
# de knowledgeGraph, 328 de relatedSearches, 635 de sitelinks).
#
# C'ÉTAIT un opt-in `compact=True`, par prudence héritée de `fr_get` projeté par allowlist
# qui avait perdu `liste_idcc` en silence (oto-core#37). Mesuré depuis : un agent branché
# en direct sur le MCP ne le passe JAMAIS — six `serper_search` avec `query` seul sur une
# fiche, six réponses entières —, et il n'a aucune raison de le faire : il ne sait pas que
# le paramètre existe avant d'avoir lu le schéma, et rien ne lui dit que c'est important.
# Une doctrine ne peut pas le lui apprendre non plus, celle qui pilote ces agents ne nomme
# aucun outil (par choix : c'est ce qui la protège des renommages). **Un paramètre
# d'économie qu'il faut connaître pour en bénéficier ne bénéficie à personne** — sur une
# conversation d'enrichissement réelle, 6 800 tokens de sorties d'outils pour 784 de
# prompt. Le défaut servait le cas rare et faisait payer le cas général : inversé.
# La prudence de #37 ne s'applique pas ici — ces listes sont une DENYLIST de clés nommées,
# pas une allowlist : elles ne peuvent pas faire disparaître un champ imprévu.
_SEARCH_DROP = ("knowledgeGraph", "peopleAlsoAsk", "relatedSearches", "searchParameters")
_RESULT_DROP = ("sitelinks", "attributes", "imageUrl", "thumbnailUrl")

# Serper renvoie `Serper <method> <status>: <msg>` (RuntimeError nu). Deux classes
# d'échec sont des ENTRÉES invalides, pas des bugs backend — on les convertit en
# McpError GÉRÉE (message actionnable pour l'agent + non reporté à Sentry, la
# taxonomie droppe les McpError d'entrée) :
#  - **400** (générique, dans `_run`) : requête/URL invalide — param de lieu manquant
#    (`Missing fid/cid/placeId`), URL non scrapable (`Content-Type application/json`)… ;
#  - **404 et 5xx** du scrape (dans `serper_scrape`) : l'URL ne mène à rien (page
#    morte) ou la page a bloqué le robot. Le 404 était le 1ᵉʳ contributeur de bruit
#    Sentry du backend — 37 événements en 5 semaines pour « l'URL que l'agent a
#    trouvée est morte », ce qui est une entrée invalide, pas une panne.
# Les 401/402/403/429 (clé/crédits/rate) restent propagés : vrais problèmes de config.
_SERPER_STATUS = re.compile(r"Serper \w+ (\d{3}):")


def register(mcp: FastMCP) -> None:
    # Import au register pour fail-fast si le package n'est pas installé.
    from oto.tools.serper import SerperClient

    def _client() -> tuple[SerperClient, bool]:
        key, is_platform = access.resolve_api_key("serper")
        return SerperClient(api_key=key), is_platform

    def _run(method: str, **kwargs) -> dict:
        """Résout la clé, appelle la méthode du client, compte l'usage plateforme.
        Un 400 Serper (entrée invalide) → McpError gérée (actionnable, hors Sentry)."""
        client, is_platform = _client()
        try:
            result = getattr(client, method)(**kwargs)
        except RuntimeError as e:
            m = _SERPER_STATUS.search(str(e))
            if m and int(m.group(1)) == 400:
                raise McpError(ErrorData(code=INVALID_REQUEST, message=str(e))) from None
            raise
        if is_platform:
            access.record_platform_usage("serper")
        return result

    def _project(result: dict, items: str, full: bool, fields) -> dict:
        """Applique la projection à une page de résultats. `full=True` rend le payload
        INCHANGÉ (l'échappatoire pour qui veut le knowledge graph) ; sinon on retire ce
        qu'un balayage ne lit pas, `fields` restant un resserrement supplémentaire."""
        if full and not fields:
            return result
        return output_projection.project(
            result,
            drop=() if full else _SEARCH_DROP,
            items_path=items,
            item_drop=() if full else _RESULT_DROP,
            fields=fields)

    def _bad(msg: str) -> McpError:
        return McpError(ErrorData(code=INVALID_REQUEST, message=msg))

    # Verticale → (méthode du client, chemin des items, params acceptés en plus du socle).
    # Le socle commun est `query` + `num` + `page` + `country` + `language` : c'est ce
    # recouvrement qui justifie la fusion (ADR 0047 §Amendement — le critère est
    # l'homogénéité des params, pas le comptage).
    _KINDS = {
        "web":      ("search",          "organic"),
        "news":     ("search_news",     "news"),
        "images":   ("search_images",   "images"),
        "videos":   ("search_videos",   "videos"),
        "places":   ("search_places",   "places"),
        "shopping": ("search_shopping", "shopping"),
        "scholar":  ("search_scholar",  "organic"),
        "patents":  ("search_patents",  "organic"),
    }
    _KIND_LIST = ", ".join(sorted(_KINDS) + ["autocomplete"])

    @mcp.tool()
    def serper_search(
        query: str,
        kind: Literal["web", "news", "images", "videos", "places", "shopping",
                      "scholar", "patents", "autocomplete"] = "web",
        num: int = 10,
        page: int = 1,
        country: Optional[str] = "fr",
        language: Optional[str] = "fr",
        tbs: Optional[str] = None,
        location: Optional[str] = None,
        site_filter: Optional[str] = None,
        autocorrect: Optional[bool] = None,
        full: bool = False,
        fields: Optional[list[str]] = None,
    ) -> dict:
        """Google search via Serper — une verticale par `kind`.

        `kind` :
        - **"web"** (défaut) : résultats organiques. Accepte `site_filter`
          (ex. "linkedin.com/in"), `autocorrect`, `location`, `tbs`.
        - **"news"** : Google News — utile pour surveiller les signaux d'une cible
          (communiqués, recrutements, levées). Accepte `tbs`.
        - **"images"** / **"videos"** : renvoient un tableau `images` / `videos`
          (titre, lien, source, dimensions ou durée). Acceptent `tbs`.
        - **"places"** : Google Local — les établissements d'une requête. Excellent
          pour de la prospection B2B locale : titre, adresse, téléphone, site, note,
          nombre d'avis et **`cid`** (à passer à `serper_reviews`). Accepte `location`.
        - **"shopping"** : titre, prix, marchand, note, livraison. Accepte `location`.
        - **"scholar"** : publications académiques (titre, revue, année, citations, pdf).
        - **"patents"** : brevets (titre, inventeur, déposant, numéro, dates).
        - **"autocomplete"** : les suggestions Google pour `query` — pour élargir un
          champ lexical ou trouver des idées de mots-clés. Ignore la pagination.

        Args:
            query: la requête.
            kind: la verticale (défaut "web") : web | news | images | videos |
                places | shopping | scholar | patents | autocomplete.
            num: nombre de résultats (max 100). Ignoré par "autocomplete".
            page: page de résultats (1-based). Ignoré par "autocomplete".
            country: code pays (défaut "fr").
            language: code langue (défaut "fr").
            tbs: filtre temporel Google — "qdr:d" (24 h), "qdr:w" (7 j), "qdr:m"…
                Accepté par web / news / images / videos.
            location: biais géographique (ex. "Paris, France"). Accepté par
                web / places / shopping.
            site_filter: kind="web" — restreint à un domaine (ex. "linkedin.com/in").
            autocorrect: kind="web" — bascule la correction orthographique Google.
            full: rend la réponse Google ENTIÈRE. Par défaut (recommandé) le retour
                est resserré sur ce qu'un balayage lit — titre, lien, extrait — et
                laisse tomber knowledge graph, people-also-ask, recherches associées
                et sitelinks par résultat (~un tiers du payload, jamais lu). Ne passe
                `full=True` que si tu veux précisément l'une de ces sections.
                Accepté par web / news.
            fields: ne garde QUE ces clés sur chaque résultat (ex. ["title","link",
                "snippet"]). L'enveloppe (crédits, pagination) est conservée dans tous
                les cas. Accepté par web / news.
        """
        if kind == "autocomplete":
            return _run("autocomplete", query=query, country=country, language=language)

        entry = _KINDS.get(kind)
        if entry is None:
            raise _bad(f"`kind` invalide : {kind!r} (attendu : {_KIND_LIST}).")
        method, items = entry

        args = {"query": query, "num": num, "page": page,
                "country": country, "language": language}
        if kind in ("web", "news", "images", "videos"):
            args["tbs"] = tbs
        if kind in ("web", "places", "shopping"):
            args["location"] = location
        if kind == "web":
            args["site_filter"] = site_filter
            args["autocorrect"] = autocorrect

        result = _run(method, **args)
        return _project(result, items, full, fields) if kind in ("web", "news") else result


    @mcp.tool(meta={"census_via": "serper_maps_census"})
    def serper_maps_sample(
        query: Optional[str] = None,
        ll: Optional[str] = None,
        place_id: Optional[str] = None,
        cid: Optional[str] = None,
        num: int = 10,
        page: int = 1,
        country: Optional[str] = "fr",
        language: Optional[str] = "fr",
    ) -> dict:
        """Google Maps — un ÉCHANTILLON de lieux, ancré géographiquement.

        ⚠️ Le nom dit ce que fait ce tool : un échantillon, pas un inventaire. Il
        plafonne à ~20 résultats par appel et biaise vers `ll` → il **sous-compte
        silencieusement** (20 trouvés là où 60 existent, sans lever d'erreur ni
        annoncer de total). Pour un comptage ou une liste EXHAUSTIVE d'un type de
        commerce sur une zone (« combien de X à Y »), utilise **`serper_maps_census`**,
        qui pave la zone, pagine chaque ancre et déduplique côté serveur.
        Règle : total exact → census ; quelques hits en tête → ce tool.

        Args:
            query: la requête (ex. "coffee shops").
            ll: ancre lat/long + zoom "@lat,lng,zoom" (ex. "@45.76,4.83,12z").
            place_id: id Google d'un lieu, à consulter directement.
            cid: customer id Google d'un lieu.
            num: nombre de résultats (max 100).
            page: page de résultats (1-based).
            country: code pays (défaut "fr").
            language: code langue (défaut "fr").
        """
        return _run(
            "search_maps", query=query, ll=ll, place_id=place_id, cid=cid,
            num=num, page=page, country=country, language=language,
        )

    @mcp.tool(meta={"technique": "local-census"})
    def serper_maps_census(
        query: str,
        center: Optional[str] = None,
        radius_km: float = 5.0,
        grid: int = 3,
        zoom: int = 14,
        ll_anchors: Optional[list[str]] = None,
        max_pages: int = 3,
        country: Optional[str] = "fr",
        language: Optional[str] = "fr",
    ) -> dict:
        """Recensement EXHAUSTIF d'un type de commerce sur une zone (Google Maps).

        À utiliser — PAS `serper_maps_sample` — dès qu'il faut un **comptage ou une
        liste exhaustive** d'un type de commerce sur une zone. Un échantillon Maps
        plafonne à ~20 résultats et biaise vers son point d'ancrage : il **sous-compte
        silencieusement**. Ce tool corrige les deux côté serveur — il **pave** la zone
        en une grille d'ancres géo, **pagine** chacune et **déduplique** par id de lieu
        → résultat complet.

        Fournir soit `center` "lat,lng" (+ radius_km, grid), soit `ll_anchors`.
        Coût : ~grid² × max_pages appels Serper (throttlés) — c'est le prix de
        l'exhaustivité ; commencer modeste et resserrer la grille si besoin.

        Args:
            query: Ce qu'on énumère (e.g. "laverie automatique").
            center: Centre de zone "lat,lng" (e.g. "48.8566,2.3522"). Requis sauf ll_anchors.
            radius_km: Demi-largeur de la zone carrée autour du centre (défaut 5).
            grid: Densité du pavage grid×grid ; + fin = + de couverture et d'appels (défaut 3 → 9 ancres).
            zoom: Niveau de zoom Maps par ancre (défaut 14).
            ll_anchors: Ancres "@lat,lng,zoomz" explicites, priment sur center/radius/grid.
            max_pages: Pages maxi paginées par ancre (défaut 3).
            country: Country code (default "fr").
            language: Language code (default "fr").

        Returns {query, count, places[], anchors_used, pages_fetched}. `count` =
        total dédupliqué — à préférer à tout comptage d'un échantillon seul.
        """
        return _run(
            "census_maps", query=query, center=center, radius_km=radius_km,
            grid=grid, zoom=zoom, ll_anchors=ll_anchors, max_pages=max_pages,
            country=country, language=language,
        )

    @mcp.tool(meta={"technique": "reviews-census"})
    def serper_reviews(
        op: Literal["all", "page"] = "all",
        cid: Optional[str] = None,
        fid: Optional[str] = None,
        place_id: Optional[str] = None,
        query: Optional[str] = None,
        sort_by: Optional[str] = None,
        topic_id: Optional[str] = None,
        max_reviews: int = 200,
        next_page_token: Optional[str] = None,
        country: Optional[str] = "fr",
        language: Optional[str] = "fr",
    ) -> dict:
        """Avis Google d'un lieu.

        ⚠️ **Le défaut rend TOUS les avis, et c'est voulu.** Une page seule
        (~10 avis, triés `mostRelevant`) **sous-représente silencieusement** un lieu
        qui peut en avoir des milliers : une analyse de sentiment faite dessus est
        biaisée sans que rien ne le signale. Le chemin par défaut est donc le chemin
        complet ; l'échantillon se demande explicitement.

        `op` :
        - **"all"** (défaut) : suit le curseur `nextPageToken` côté serveur jusqu'à
          épuisement, ou jusqu'au plafond `max_reviews` (borne le coût ;
          `truncated=True` signale la coupe). Renvoie {count, reviews[],
          pages_fetched, truncated}. C'est ce qu'il faut pour un sentiment global,
          des thèmes récurrents, une réputation.
        - **"page"** : UNE page (~10 avis) — échantillon rapide, ou pagination à la
          main via `next_page_token`. Ne conclus rien de global dessus.

        Identifier le lieu par `cid` / `fid` / `place_id` (issus d'un
        `serper_search(kind="places")` ou d'un `serper_maps_sample`) ou par `query` libre.

        Args:
            op: "all" (défaut) | "page".
            cid: customer id Google du lieu.
            fid: feature id Google du lieu.
            place_id: place id Google.
            query: recherche libre du lieu (alternative aux ids).
            sort_by: 'mostRelevant' | 'newest' | 'highestRating' | 'lowestRating'.
            topic_id: filtre les avis par thème.
            max_reviews: op="all" — plafond d'avis récupérés (défaut 200).
            next_page_token: op="page" — curseur d'une réponse précédente.
            country: code pays (défaut "fr").
            language: code langue (défaut "fr").
        """
        if op == "all":
            return _run(
                "reviews_all", cid=cid, fid=fid, place_id=place_id, query=query,
                sort_by=sort_by, topic_id=topic_id, max_reviews=max_reviews,
                country=country, language=language,
            )
        if op == "page":
            return _run(
                "search_reviews", cid=cid, fid=fid, place_id=place_id, query=query,
                sort_by=sort_by, topic_id=topic_id, next_page_token=next_page_token,
                country=country, language=language,
            )
        raise _bad(f"`op` invalide : {op!r} (attendu : all | page).")

    @mcp.tool()
    def serper_lens(
        url: str,
        country: Optional[str] = "fr",
        language: Optional[str] = "fr",
    ) -> dict:
        """Google Lens via Serper — recherche inversée à partir d'une image.

        Args:
            url: URL publique de l'image à analyser.
            country: code pays (défaut "fr").
            language: code langue (défaut "fr").
        """
        return _run("search_lens", url=url, country=country, language=language)

    @mcp.tool()
    def serper_scrape(
        url: str,
        format: Literal["markdown", "text", "both"] = "markdown",
    ) -> dict:
        """Récupère une page web via le scraper de Serper.

        Renvoie le contenu en UNE représentation (markdown par défaut) + JSON-LD +
        métadonnées. Préférable à un fetch brut : Serper gère le rendu JS et les
        anti-bot rudimentaires.

        Args:
            url: URL de la page à récupérer.
            format: "markdown" (défaut, la représentation lisible par un LLM) |
                "text" (brut) | "both" (seulement si tu as vraiment besoin de comparer).
        """
        if format not in ("markdown", "text", "both"):
            raise _bad(f"`format` invalide : {format!r} (markdown | text | both).")
        try:
            res = _run("scrape_page", url=url, include_markdown=format != "text")
            # Serper renvoyait `text` ET `markdown` : deux représentations du MÊME
            # contenu (mesuré, 97 % de mots communs), pour 37 % du payload en pure
            # duplication. On n'en sert qu'une — retirer un doublon ne perd rien. Le
            # JSON-LD et les métadonnées RESTENT : ce ne sont pas des représentations
            # du contenu mais des données structurées (date, auteur) qu'on ne saurait
            # pas reconstituer, donc les retirer perdrait quelque chose.
            if format == "markdown" and res.get("markdown"):
                res.pop("text", None)
            return res
        except RuntimeError as e:
            m = _SERPER_STATUS.search(str(e))
            code = int(m.group(1)) if m else None
            if code == 404 or (code is not None and 500 <= code < 600):
                detail = ("cette page n'existe pas (ou plus)" if code == 404 else
                          "la page a bloqué le robot ou n'a pas pu être récupérée")
                raise McpError(ErrorData(
                    code=INVALID_REQUEST,
                    message=(f"Scrape impossible pour cette URL ({url}) : {detail}. "
                             "Essaie une autre source ou serper_search."),
                )) from None
            raise
