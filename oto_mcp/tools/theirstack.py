"""TheirStack — offres d'emploi par employeur + technologies utilisées (ERP…).

Wrappe `oto.tools.theirstack.client.TheirStackClient` (API v1, Bearer). keyed
`api_key`, **BYO ou clé plateforme** : `auth_modes = {byo_user, byo_org, platform}`
depuis oto-backend#405 — TheirStack est une donnée-marchandise, donc revendable,
contrairement aux CRM/ATS qui restent byo-only. TheirStack se facture au crédit,
au record rendu.
⚠️ Ce docstring a dit « byo-only » jusqu'au 2026-09-09, plusieurs semaines APRÈS
que #405 ait ouvert le mode plateforme. Le registre (`providers.REGISTRY`) fait
foi, pas ce texte — la contradiction a fait conclure à tort que les deux lignes
TheirStack du site (« Get hiring signals », « tech stack ») étaient invendables.
Le palier plateforme reste **grant-only** (`default_quota=0,
platform_key_open=False`) : une org n'y accède que par un grant explicite.

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
TheirStack ne rend AUCUN compteur de crédits consommés : chaque réponse porte
`credits_estimes`, notre estimation d'après ce barème (oto#174), étiquetée comme telle.
Couverture partielle sur les PME (≈ 8 % des petits grossistes français vus dans le
pilote) : `data: []` est un résultat NORMAL, pas une erreur — ne pas réessayer.

Les appels au client sont écrits en clair (`_client().search_jobs(…)`) : c'est ce qui
les rend vérifiables par la sonde version-skew (`test_tools_client_methods_exist`).
"""
from __future__ import annotations

from typing import Any, Optional

from fastmcp import FastMCP
from ..mcp_errors import McpError
from mcp.types import ErrorData, INVALID_PARAMS

from .. import access, output_projection, session_org
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


def _verify(fields: dict, config: dict | None = None) -> dict:  # noqa: ARG001
    """Sonde « tester la connexion » — couvre `auth+quota` : le solde de crédits,
    l'appel authentifié GRATUIT (une recherche, même `limit=1`, dépenserait des
    crédits). Le solde était lu puis JETÉ : un compte à sec gardait une sonde verte.

    Réponse documentée (`GET /v0/billing/credit-balance`) : `api_credits`,
    `used_api_credits`, `ui_credits`, `used_ui_credits`, `earliest_expiration`. La doc
    ne dit pas si `api_credits` est le RESTANT ou l'ALLOCATION : on ne conclut donc
    « à sec » que sur `api_credits <= 0`, vrai sous les deux lectures, et on rend les
    deux chiffres tels quels plutôt qu'un restant calculé qui pourrait mentir.
    """
    from oto.tools.theirstack.client import TheirStackClient

    solde = TheirStackClient(api_key=fields["key"]).credit_balance()
    api = solde.get("api_credits") if isinstance(solde, dict) else None
    if not isinstance(api, int):
        raise RuntimeError(
            f"TheirStack a répondu sans solde de crédits API lisible : {str(solde)[:200]}")
    if api <= 0:
        raise connector_verify.QuotaEpuise(
            "La clé TheirStack est bonne, mais le compte n'a plus de crédits API. "
            "Recharge le compte chez TheirStack — reconnecter n'y changerait rien.")
    return {"quota": {"api_credits": api,
                      "used_api_credits": solde.get("used_api_credits"),
                      "unite": "crédits API"}}


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


#: Le barème publié (spec OpenAPI, 17/08/2026), en crédits API par record rendu.
_CREDITS_PAR_OFFRE = 1
_CREDITS_PAR_ENTREPRISE = 3


def _with_credit_estimate(result: Any, par_record: int, unite: str) -> Any:
    """Ajoute `credits_estimes` à l'enveloppe (oto#174).

    TheirStack ne rend AUCUN compteur de crédits — ni dans `metadata`, ni en
    en-tête ; seul un appel de solde à part le donne. Des procédures disaient
    pourtant « compte les crédits depuis `metadata` » : impossible, et l'appelant
    dépassait son budget sans le savoir. Le backend connaît le barème et le nombre
    de records rendus (c'est déjà le métrage, `_trace_quantity`) : on le dit, en
    l'étiquetant ESTIMÉ — ce n'est pas un relevé du fournisseur."""
    if not (isinstance(result, dict) and isinstance(result.get("data"), list)):
        return result
    out = dict(result)
    out["credits_estimes"] = par_record * len(result["data"])
    out["credits_estimes_source"] = (
        f"Estimé d'après le barème publié ({par_record} crédit(s) par {unite} "
        "rendue), pas un compteur : TheirStack n'en renvoie aucun. Le solde réel "
        "se lit dans le tableau de bord TheirStack (API : "
        "GET /v0/billing/credit-balance).")
    return out


def _trace_quantity(result: Any) -> None:
    """Métrage par unité (facturation du partenaire, 21/08) — le nombre de records RENDUS
    dans `data`, avant projection (`_project` ne change jamais la longueur de
    la liste, seulement les clés de chaque item). C'est ce que TheirStack
    facture réellement : 1 crédit API/offre sur jobs/search, 3/entreprise sur
    companies/search — voir le docstring du module. Les deux tools résolvent
    au MÊME connecteur (`namespace_of` = premier token, "theirstack" pour les
    deux : aucun préfixe multi-token "theirstack_jobs"/"theirstack_companies"
    n'est déclaré au registre) — c'est la grille de prix du consommateur de
    facturation du partenaire (dépôt externe) qui doit donc distinguer les deux
    TAUX par nom de TOOL, pas par connecteur."""
    if isinstance(result, dict) and isinstance(result.get("data"), list):
        session_org.note_call_trace(quantity=len(result["data"]))


def _record_platform_usage(result, is_platform: bool) -> None:
    """Débite le quota interne oto quand c'est NOTRE clé qui a servi.

    Mêmes deux gestes que `aiark`/`fullenrich`, et la même séparation nette :
    - `record_platform_usage` ne compte QUE le mode plateforme — c'est le quota
      d'oto sur sa propre clé, sans objet quand le client apporte la sienne ;
    - `note_call_trace(quantity=…)` ci-dessus est INCONDITIONNEL — c'est le
      métrage, et `tool_calls.key_mode` dit séparément sous quelle clé l'appel
      est passé, ce que le consommateur de facturation lit pour ne facturer que
      la clé du partenaire.
    Compté au nombre de records RENDUS, pas au nombre d'appels : TheirStack nous
    facture au record (1 crédit/offre, 3/entreprise), donc un appel qui rend 50
    offres coûte 50, et une page vide coûte 0."""
    if not is_platform:
        return
    if isinstance(result, dict) and isinstance(result.get("data"), list):
        access.record_platform_usage("theirstack", len(result["data"]))


def register(mcp: FastMCP) -> None:
    from oto.tools.common.errors import UpstreamHTTPError
    from oto.tools.theirstack.client import TheirStackClient

    connector_verify.register("theirstack", _verify, couvre=connector_verify.AUTH_QUOTA)

    def _client() -> tuple[TheirStackClient, bool]:
        key, is_platform = access.resolve_api_key("theirstack")
        return TheirStackClient(api_key=key), is_platform

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
        total_companies?}, data: [{company, job_title, date_posted, url, location}],
        credits_estimes, credits_estimes_source}` (`full=True` → the raw records
        instead). TheirStack returns NO credit counter (not in `metadata`, not in a
        header): `credits_estimes` is OUR estimate from the published rate (1 per
        job returned) — sum it to track a budget; the real balance is on the
        TheirStack dashboard.

        ⚠️ The company DOMAIN is NULLABLE (raw records, `full=True`): the same
        company can come back without it, from one day to the next. An exclusion or
        a dedup table keyed on the domain then lets that record through SILENTLY —
        key on the company name too, never on the domain alone.

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
        client, is_platform = _client()
        result = _run(lambda: client.search_jobs(payload))
        _record_platform_usage(result, is_platform)
        _trace_quantity(result)
        return _with_credit_estimate(_project(result, _JOB_FIELDS, full),
                                     _CREDITS_PAR_OFFRE, "offre")

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
        domain, employee_count, industry, technology_names}], credits_estimes,
        credits_estimes_source}` (`full=True` → the raw records instead).
        TheirStack returns NO credit counter (not in `metadata`, not in a header):
        `credits_estimes` is OUR estimate from the published rate (3 per company
        returned) — sum it to track a budget; the real balance is on the
        TheirStack dashboard.

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
        client, is_platform = _client()
        result = _run(lambda: client.search_companies(payload))
        _record_platform_usage(result, is_platform)
        _trace_quantity(result)
        return _with_credit_estimate(_project(result, _COMPANY_FIELDS, full),
                                     _CREDITS_PAR_ENTREPRISE, "entreprise")
