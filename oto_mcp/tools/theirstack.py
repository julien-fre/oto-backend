"""TheirStack — offres d'emploi par employeur + technologies utilisées (ERP…).

Wrappe `oto.tools.theirstack.client.TheirStackClient` (API v1, Bearer). keyed
`api_key`, byo-only (pas de clé plateforme) : chaque user/org connecte SON compte —
TheirStack se facture au crédit, au record rendu.

Deux gestes, lecture seule :
- `theirstack_jobs_search` : les offres publiées par une ou des entreprises (ou par
  pays / titre / techno) — le signal « qui recrute quoi, où, depuis quand ».
- `theirstack_companies_search` : la fiche firmographique + les technologies détectées
  (technographie : ERP, CRM, e-commerce…) d'entreprises nommées ou filtrées.

Le contrat côté agent est PROJETÉ par défaut (ce qu'un balayage de sourcing lit :
société, titre, date, url, lieu / nom, domaine, effectif, secteur, technologies) ;
`full=True` rend le record TheirStack entier (description, salaires, hiring_team,
company_object…). Les filtres typés couvrent le quotidien ; `extra` (fusionné EN
DERNIER, il prime) ouvre toute la DSL éditeur sans casser le schéma du tool.

Facturation : le crédit se compte au record ENTREPRISE rendu — un crédit entreprise
« déverrouille » toutes les offres + technologies + firmographie de cette entreprise.
Le spec OpenAPI (17/08/2026) le chiffre en crédits API : 1 par offre rendue sur
jobs/search, 3 par entreprise sur companies/search — dans les deux cas `limit` borne
la dépense, et `metadata.truncated_*` dit ce qui n'a PAS été rendu faute de crédits.
Couverture partielle sur les PME (≈ 8 % des petits grossistes français vus dans le
pilote) : `data: []` est un résultat NORMAL, pas une erreur — ne pas réessayer.

Les appels au client sont écrits en clair (`_client().search_jobs(…)`) : c'est ce qui
les rend vérifiables par la sonde version-skew (`test_tools_client_methods_exist`).
"""
from __future__ import annotations

from typing import Any, Optional

from fastmcp import FastMCP
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData, INVALID_PARAMS

from .. import access, output_projection
from ..connectors import verify as connector_verify

# Ce qu'un balayage de sourcing lit sur une offre / une entreprise (`full=True` rend tout).
_JOB_FIELDS = ("company", "job_title", "date_posted", "url", "location")
_COMPANY_FIELDS = ("name", "domain", "employee_count", "industry", "technology_names")


def _bad(msg: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=msg))


def _upstream_message(e) -> str:
    status = e.status_code
    if status in (401, 403):
        return (f"TheirStack a rejeté la clé API (HTTP {status}) — vérifie la clé "
                "configurée sur ce connecteur (TheirStack : Settings → API keys).")
    if status == 402:
        return ("TheirStack : crédits épuisés ou plan insuffisant (402) — recharge le "
                "compte, ou réduis `limit`.")
    if status == 422:
        return (f"TheirStack a refusé les filtres (422) : {e.body} — jobs_search exige au "
                "moins un de posted_at_max_age_days / posted_at_gte / posted_at_lte / "
                "company_names (company_name_or) / company_domain_or / company_linkedin_url_or ; "
                "les noms de champs de `extra` doivent être ceux de la DSL TheirStack.")
    if status == 429:
        return "TheirStack : trop de requêtes (429) — réessaie dans un instant."
    if status in (500, 502, 503, 504):
        return f"TheirStack est momentanément indisponible (HTTP {status}) — réessaie plus tard."
    return f"TheirStack a refusé la requête (HTTP {status}): {e.body}"


def _verify(fields: dict, config: dict | None = None) -> None:  # noqa: ARG001
    """Sonde « tester la connexion » : le solde de crédits — l'appel authentifié
    GRATUIT (une recherche, même `limit=1`, dépenserait des crédits)."""
    from oto.tools.theirstack.client import TheirStackClient
    TheirStackClient(api_key=fields["key"]).credit_balance()


def _clean_names(names: Optional[list[str]], what: str) -> list[str]:
    if names is None:
        return []
    if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
        raise _bad(f"`{what}` doit être une liste de chaînes.")
    return [n.strip() for n in names if n and n.strip()]


def _merge_extra(payload: dict, extra: Optional[dict]) -> dict:
    """`extra` = clés brutes de la DSL TheirStack, fusionnées EN DERNIER (elles priment
    sur les arguments typés — c'est l'échappatoire vers les ~110 filtres éditeur)."""
    if extra is None:
        return payload
    if not isinstance(extra, dict):
        raise _bad("`extra` doit être un dict de filtres TheirStack (DSL éditeur).")
    payload.update(extra)
    return payload


def _project(result: Any, fields: tuple, full: bool) -> Any:
    """`full=True` → payload INCHANGÉ ; sinon chaque item de `data` est resserré sur
    `fields`. L'enveloppe `metadata` (total, truncated_*) reste dans tous les cas."""
    if full:
        return result
    return output_projection.project(result, items_path="data", fields=fields)


def register(mcp: FastMCP) -> None:
    from oto.tools.common.errors import UpstreamHTTPError
    from oto.tools.theirstack.client import TheirStackClient

    connector_verify.register("theirstack", _verify)

    def _client() -> TheirStackClient:
        key, _ = access.resolve_api_key("theirstack")
        return TheirStackClient(api_key=key)

    def _run(fn):
        """Traduit un refus de TheirStack en erreur d'outil actionnable."""
        try:
            return fn()
        except ValueError as e:
            raise _bad(str(e))
        except UpstreamHTTPError as e:
            raise _bad(_upstream_message(e))

    @mcp.tool()
    def theirstack_jobs_search(
        company_names: Optional[list[str]] = None,
        posted_at_max_age_days: int = 90,
        job_country_code_or: Optional[list[str]] = None,
        limit: int = 25,
        page: int = 0,
        extra: Optional[dict] = None,
        full: bool = False,
    ) -> dict:
        """Job postings published by given companies (or by country / title / tech),
        from TheirStack — the "who is hiring what, where, since when" signal.

        Billing: credits are billed per company record returned — one company credit
        unlocks ALL the jobs + technologies + firmographics of that company (the API
        spec counts it as 1 API credit per job returned here; `limit` bounds the
        spend either way). Coverage is partial on small companies (~8% of small
        French wholesalers): an EMPTY `data` is a normal result, not an error — do
        not retry, move on.

        Returns `{metadata: {total_results?, truncated_results, truncated_companies,
        total_companies?}, data: [{company, job_title, date_posted, url, location}]}`
        (`full=True` → the raw records instead).

        Args:
            company_names: exact company names, CASE-SENSITIVE (`company_name_or`) —
                pass the name as TheirStack spells it. For looser matching put
                `company_name_case_insensitive_or` / `company_name_partial_match_or`
                / `company_domain_or` in `extra` instead.
            posted_at_max_age_days: only jobs posted in the last N days (default 90;
                0 = today only). TheirStack REQUIRES a date filter or a company
                identifier — this default satisfies it.
            job_country_code_or: ISO2 codes of the job location (e.g. ["FR"]).
            limit: results per page (default 25) — this is the cost bound.
            page: 0-based page number.
            extra: any other TheirStack filter, merged LAST (overrides the typed args):
                `job_title_or` (keywords), `job_title_pattern_or` (regex),
                `job_technology_slug_or`, `company_technology_slug_or`,
                `company_country_code_or`, `min_employee_count`, `industry_or`,
                `job_seniority_or`, `workplace_types_or`, `include_total_results`…
            full: return the raw TheirStack records (description, salary, hiring_team,
                company_object, technology_slugs…). Default: each item is projected to
                {company, job_title, date_posted, url, location}; the `metadata`
                envelope (total_results, truncated_results…) is always kept.
        """
        names = _clean_names(company_names, "company_names")
        if limit is not None and limit <= 0:
            raise _bad("`limit` doit être ≥ 1.")
        if page is not None and page < 0:
            raise _bad("`page` est 0-based (≥ 0).")
        payload: dict = {"page": page, "limit": limit}
        if posted_at_max_age_days is not None:
            payload["posted_at_max_age_days"] = posted_at_max_age_days
        if names:
            payload["company_name_or"] = names
        if job_country_code_or:
            payload["job_country_code_or"] = list(job_country_code_or)
        payload = _merge_extra(payload, extra)
        result = _run(lambda: _client().search_jobs(payload))
        return _project(result, _JOB_FIELDS, full)

    @mcp.tool()
    def theirstack_companies_search(
        company_names: Optional[list[str]] = None,
        company_country_code_or: Optional[list[str]] = None,
        limit: int = 25,
        page: int = 0,
        extra: Optional[dict] = None,
        full: bool = False,
    ) -> dict:
        """Company records from TheirStack — firmographics + the technologies detected
        in their job postings (ERP, CRM, e-commerce stack…): the technographic read
        on a named company or a filtered segment.

        Billing: credits are billed per company record returned — one company credit
        unlocks ALL the jobs + technologies + firmographics of that company (the API
        spec counts 3 API credits per company returned here; `limit` bounds the
        spend). Coverage is partial on small companies (~8% of small French
        wholesalers): an EMPTY `data` is a normal result, not an error — do not
        retry, move on. `metadata.truncated_companies` > 0 means the credit balance,
        not the filter, cut the list.

        Returns `{metadata: {…, truncated_companies, total_companies?}, data: [{name,
        domain, employee_count, industry, technology_names}]}` (`full=True` → the raw
        records instead).

        Args:
            company_names: exact company names, CASE-SENSITIVE (`company_name_or`).
                For looser matching put `company_name_case_insensitive_or` /
                `company_name_partial_match_or` / `company_domain_or` in `extra`.
            company_country_code_or: ISO2 codes of the HQ country (e.g. ["FR"]).
            limit: results per page (default 25) — the cost bound.
            page: 0-based page number.
            extra: any other TheirStack filter, merged LAST (overrides the typed args):
                `company_technology_slug_or` (companies using a tech),
                `min_employee_count` / `max_employee_count`, `industry_or`,
                `company_name_partial_match_or`, `job_filters` + `min_num_jobs_found`
                (hiring signals), `expand_technology_slugs`, `include_total_results`…
            full: return the raw records (technology_slugs, jobs_found,
                technologies_found, linkedin_url, revenue, funding…). Default: each
                item is projected to {name, domain, employee_count, industry,
                technology_names}; the `metadata` envelope is always kept.
        """
        names = _clean_names(company_names, "company_names")
        if limit is not None and limit <= 0:
            raise _bad("`limit` doit être ≥ 1.")
        if page is not None and page < 0:
            raise _bad("`page` est 0-based (≥ 0).")
        payload: dict = {"page": page, "limit": limit}
        if names:
            payload["company_name_or"] = names
        if company_country_code_or:
            payload["company_country_code_or"] = list(company_country_code_or)
        payload = _merge_extra(payload, extra)
        result = _run(lambda: _client().search_companies(payload))
        return _project(result, _COMPANY_FIELDS, full)
