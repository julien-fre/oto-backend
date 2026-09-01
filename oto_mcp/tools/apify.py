"""Apify — louer un scraper déjà écrit plutôt que d'en écrire un (apify.com).

Wrappe `oto.tools.apify.client.ApifyClient` (API v2). keyed `api_key` (Bearer),
byo-only : chaque user/org connecte SON compte — un actor se facture à l'usage.

Apify n'est pas un scraper mais un **catalogue de scrapers** (les *actors*) : Google
Maps, LinkedIn, Instagram, Amazon, Booking, TikTok… Chaque actor a son propre JSON
d'entrée, décrit sur sa fiche du Store. D'où le parcours :

1. `apify_store_search("google maps")` → trouver l'actor et son identifiant.
2. `apify_actor(id)` → lire sa fiche (options par défaut, mémoire, timeout).
3. `apify_run_sync(id, input)` → lancer et récupérer les résultats (≤ 300 s),
   ou `apify_run` + `apify_run_status` + `apify_dataset_items` pour un job long.

Un actor qui tourne coûte : poser `max_items` / `timeout_secs` /
`max_total_charge_usd` AU LANCEMENT est la seule protection — après, c'est facturé.

Les appels au client sont écrits en clair (`_client().run(…)`) et non dispatchés par
nom : c'est ce qui les rend vérifiables par la sonde version-skew.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Optional

from fastmcp import FastMCP
from ..mcp_errors import McpError
from mcp.types import ErrorData, INVALID_PARAMS

from .. import access
from ..connectors import verify as connector_verify


def _bad(msg: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=msg))


def _upstream_message(e) -> str:
    status = e.status_code
    if status in (401, 403):
        return (f"Apify a rejeté le token (HTTP {status}) — vérifie la clé configurée "
                "sur ce connecteur (Apify : Settings → API & Integrations).")
    if status == 402:
        return ("Apify : crédits/plan insuffisants (402) — recharge le compte, ou "
                "réduis la portée du run (max_items).")
    if status == 404:
        return (f"Apify : introuvable (404) — vérifie l'identifiant. Un actor s'écrit "
                f"`username/actor-name` (ou son id), un run/dataset est un id opaque. {e.body}")
    if status == 408:
        return ("Apify : le run a dépassé les 300 s du mode synchrone — relance avec "
                "`apify_run`, puis `apify_run_status` et `apify_dataset_items`.")
    if status == 429:
        return "Apify : trop de requêtes (429) — réessaie dans un instant."
    if status in (500, 502, 503, 504):
        return f"Apify est momentanément indisponible (HTTP {status}) — réessaie plus tard."
    return f"Apify a refusé la requête (HTTP {status}): {e.body}"


def _verify(fields: dict, config: dict | None = None) -> None:  # noqa: ARG001
    """Sonde « tester la connexion » : liste les actors du compte — appel
    authentifié, gratuit, qui échoue si le token est invalide."""
    from oto.tools.apify.client import ApifyClient
    ApifyClient(api_key=fields["key"]).actors(limit=1)


def register(mcp: FastMCP) -> None:
    from oto.tools.apify.client import ApifyClient
    from oto.tools.common.errors import UpstreamHTTPError

    connector_verify.register("apify", _verify)

    def _client() -> ApifyClient:
        key, _ = access.resolve_api_key("apify")
        return ApifyClient(api_key=key)

    @contextmanager
    def _upstream():
        """Traduit un refus d'Apify en erreur d'outil actionnable."""
        try:
            yield
        except ValueError as e:
            raise _bad(str(e))
        except UpstreamHTTPError as e:
            raise _bad(_upstream_message(e))

    # --- trouver l'actor ----------------------------------------------------

    @mcp.tool()
    def apify_store_search(
        search: Optional[str] = None,
        limit: int = 20,
        category: Optional[str] = None,
        sort_by: Optional[str] = None,
    ) -> dict:
        """Search Apify's public Store for an actor that already scrapes your target.

        Start here: naming a target ("google maps reviews", "linkedin company",
        "amazon products") is how you find the actor to run, along with its pricing
        and its identifier.

        Returns — `{data: {items: [{id, username, name, title, description, stats,
            pricingInfos, …}], total}}`. Run it with `username/name`.

        Args:
            search: free text describing the site or data you want.
            category: Store category (e.g. `"SOCIAL_MEDIA"`, `"ECOMMERCE"`).
            sort_by: `"relevance"` | `"popularity"` | `"newest"` | `"lastUpdate"`.
        """
        with _upstream():
            return _client().store_search(search=search, limit=limit,
                                          category=category, sort_by=sort_by)

    @mcp.tool()
    def apify_actors(limit: int = 50, offset: Optional[int] = None) -> dict:
        """List the actors of THIS account (not the public Store)."""
        with _upstream():
            return _client().actors(limit=limit, offset=offset)

    @mcp.tool()
    def apify_actor(actor_id: str) -> dict:
        """Fetch one actor's card: builds, versions and `defaultRunOptions`
        (default memory and timeout).

        Read it before a first run to size `memory_mbytes`/`timeout_secs`. The
        actor's INPUT fields are documented on its Store page, not here.

        Args:
            actor_id: `username/actor-name` (as shown in the Store) or its id.
        """
        with _upstream():
            return _client().actor(actor_id)

    # --- lancer -------------------------------------------------------------

    @mcp.tool()
    def apify_run_sync(
        actor_id: str,
        run_input: Optional[dict] = None,
        max_items: Optional[int] = None,
        limit: Optional[int] = None,
        fields: Optional[list[str]] = None,
        timeout_secs: Optional[int] = None,
        memory_mbytes: Optional[int] = None,
        max_total_charge_usd: Optional[float] = None,
    ) -> Any:
        """Run an actor and return its results in one call. Waits up to 300 s.

        The normal path for a bounded scrape. Beyond 300 s Apify answers 408 — use
        `apify_run` then `apify_run_status`/`apify_dataset_items` instead.

        Returns: the LIST of dataset items.

        Args:
            actor_id: `username/actor-name` or id.
            run_input: the actor's own input JSON — its fields are specific to each
                actor (e.g. `{"searchStringsArray": ["bakery Marseille"],
                "maxCrawledPlaces": 20}` for the Google Maps scraper). See the
                actor's Store page.
            max_items: cap on BILLED items (pay-per-result actors) — set it.
            limit / fields: paginate and project the returned items (some actors
                return very wide objects).
            timeout_secs / memory_mbytes: run budget on Apify's side.
            max_total_charge_usd: hard cost ceiling for this run.
        """
        with _upstream():
            return _client().run_sync_dataset_items(
                actor_id, run_input=run_input, max_items=max_items, limit=limit,
                fields=fields, timeout_secs=timeout_secs,
                memory_mbytes=memory_mbytes,
                max_total_charge_usd=max_total_charge_usd)

    @mcp.tool()
    def apify_run(
        actor_id: str,
        run_input: Optional[dict] = None,
        max_items: Optional[int] = None,
        timeout_secs: Optional[int] = None,
        memory_mbytes: Optional[int] = None,
        max_total_charge_usd: Optional[float] = None,
        wait_for_finish: Optional[int] = None,
    ) -> dict:
        """Start an actor WITHOUT waiting for it — for scrapes longer than 300 s.

        Returns — `{data: {id, status, defaultDatasetId, …}}` — keep `id` for
            `apify_run_status` and `defaultDatasetId` for `apify_dataset_items`.

        Args:
            wait_for_finish: seconds to wait before returning (max 60) — enough to
                catch a short run without polling.
        """
        with _upstream():
            return _client().run(
                actor_id, run_input=run_input, max_items=max_items,
                timeout_secs=timeout_secs, memory_mbytes=memory_mbytes,
                max_total_charge_usd=max_total_charge_usd,
                wait_for_finish=wait_for_finish)

    @mcp.tool()
    def apify_run_status(run_id: str, wait_for_finish: Optional[int] = None) -> dict:
        """Check a run: `status` (READY, RUNNING, SUCCEEDED, FAILED, TIMED-OUT,
        ABORTED), `defaultDatasetId` (where the output is) and `usageTotalUsd`
        (what it cost so far)."""
        with _upstream():
            return _client().run_status(run_id, wait_for_finish=wait_for_finish)

    @mcp.tool()
    def apify_abort_run(run_id: str, gracefully: Optional[bool] = None) -> dict:
        """Abort a running actor — stops the billing.

        Args:
            gracefully: let the actor finish its current item and flush its results
                (a hard abort may lose what wasn't pushed yet).
        """
        with _upstream():
            return _client().abort_run(run_id, gracefully=gracefully)

    # --- lire la sortie -----------------------------------------------------

    @mcp.tool()
    def apify_dataset_items(
        dataset_id: str,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        fields: Optional[list[str]] = None,
        omit: Optional[list[str]] = None,
        clean: Optional[bool] = None,
    ) -> Any:
        """Read the results a run produced.

        Returns: the LIST of items.

        Args:
            dataset_id: the run's `defaultDatasetId`.
            fields / omit: keep or drop keys — worth using, several actors return
                objects with dozens of fields per item.
            clean: skip empty/hidden items.
        """
        with _upstream():
            return _client().dataset_items(
                dataset_id, limit=limit, offset=offset, fields=fields,
                omit=omit, clean=clean)
