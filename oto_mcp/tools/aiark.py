"""AI Ark — recherche société/personne B2B + enrichissement contact (LinkedIn).

Connecteur classique (kind="tools", ex-mount #152→#160) sur l'API REST synchrone
d'AI Ark (docs.ai-ark.com). Contrat LLM curé ici ; le client HTTP vit dans oto-core
(`oto.tools.aiark.client.AiArkClient`). Cascade de clé standard
(`resolve_api_key("aiark")` : BYO user/org > grant plateforme + quota) → mode
plateforme possible via `record_platform_usage`.

v1 = endpoints SYNCHRONES seulement. Les exports/find-emails EN LOT d'AI Ark
répondent par webhook (async) → hors périmètre (itération suivante).
"""
from __future__ import annotations

from typing import Literal, Optional

from fastmcp import FastMCP
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData, INVALID_PARAMS

from .. import access, connector_verify


def _verify(fields: dict, config: dict | None = None) -> None:  # noqa: ARG001 (config: contrat de sonde, non utilisé ici)
    """Sonde « tester la connexion » : la clé authentifie-t-elle vraiment ?

    `verify_key()` (oto-core) fait un GET crédits sans effet de bord — 401 sur
    clé invalide. Lève — le message remonte tel quel à l'UI.
    """
    from oto.tools.aiark.client import AiArkClient
    AiArkClient(api_key=fields["key"]).verify_key()


def register(mcp: FastMCP) -> None:
    from oto.tools.aiark.client import AiArkClient

    connector_verify.register("aiark", _verify)

    def _client() -> tuple[AiArkClient, bool]:
        key, is_platform = access.resolve_api_key("aiark")
        return AiArkClient(api_key=key), is_platform

    def _run(fn):
        """Exécute un appel AI Ark : traduit une erreur HTTP en McpError
        actionnable (5xx amont = réessayer ; sinon entrée invalide) et compte
        l'usage plateforme sur succès."""
        client, is_platform = _client()
        try:
            result = fn(client)
        except McpError:
            raise
        except Exception as e:
            resp = getattr(e, "response", None)
            status = getattr(resp, "status_code", None)
            if status and status >= 500:
                msg = ("AI Ark est momentanément indisponible (erreur serveur "
                       f"{status}). Réessaie dans un moment — ce n'est pas ton entrée.")
            elif status == 401:
                msg = "Clé AI Ark invalide ou révoquée (401). Vérifie la clé posée."
            else:
                msg = f"AI Ark n'a pas pu traiter la requête ({e})."
            raise McpError(ErrorData(code=INVALID_PARAMS, message=msg))
        if is_platform:
            access.record_platform_usage("aiark")
        return result

    @mcp.tool()
    def linkedin_aiark_credits() -> dict:
        """Remaining AI Ark credits for the resolved account (`{"total": <int>}`).

        ⚠️ On the PLATFORM key (credits paid by oto), the balance is not yours to
        read — the call is refused rather than showing someone else's pool. Bring
        your own AI Ark key to see a balance.
        """
        _, is_platform = _client()
        if is_platform:
            raise McpError(ErrorData(code=INVALID_PARAMS, message=(
                "Ces crédits AI Ark sont fournis par oto : leur solde ne t'est pas "
                "exposé. Pose ta propre clé AI Ark pour suivre un solde.")))
        return _run(lambda c: c.credits())

    @mcp.tool()
    def linkedin_aiark_search(
        op: Literal["people", "companies"] = "people",
        account: Optional[dict] = None,
        contact: Optional[dict] = None,
        lookalike_domains: Optional[list[str]] = None,
        lists: Optional[dict] = None,
        page: int = 0,
        size: int = 10,
    ) -> dict:
        """Search LinkedIn-sourced B2B data through AI Ark (bought data, per-credit).

        Not interchangeable with `linkedin_unipile_search`: that one drives YOUR
        connected LinkedIn session (and is rate-limited by LinkedIn); this one
        queries AI Ark's index and BILLS CREDITS per returned record.

        `op`:
        - **"people"** (default): people by company + contact filters. Results do
          NOT include emails — use `linkedin_aiark_person(op="export")` for one.
        - **"companies"**: companies by firmographics.

        Args:
            op: "people" (default) | "companies".
            account: filters on the company. AI Ark nested DSL — each field takes an
                include/exclude matcher. Examples:
                - name: {"name": {"any": {"include": {"mode": "SMART", "content": ["Amazon"]}}}}
                - location: {"location": {"any": {"include": ["United States"]}}}
                - employee size: {"employeeSize": {"type": "RANGE", "range": [{"start": 1000, "end": 5000}]}}
                Combine keys in one object. Supports domain, website, industries,
                revenue, foundedYear, technologies, keywords, funding, naics…
            contact: op="people" — filters on the person, e.g.
                {"seniority": {"any": {"include": ["founder"]}}}. Supports title,
                department, seniority, location…
            lookalike_domains: op="companies" — up to 5 company URLs to find similar ones.
            lists: exclude records already in saved lists.
            page: zero-based page number. size: 0-100 (default 10).

        Returns the raw AI Ark page: `content[]`, `totalElements`, `totalPages`.
        """
        if op == "people":
            return _run(lambda c: c.search_people(
                account=account, contact=contact, lists=lists, page=page, size=size))
        if op == "companies":
            return _run(lambda c: c.search_companies(
                account=account, lists=lists,
                lookalike_domains=lookalike_domains, page=page, size=size))
        raise McpError(ErrorData(code=INVALID_PARAMS,
                                 message="op doit être 'people' ou 'companies'"))

    @mcp.tool()
    def linkedin_aiark_person(
        op: Literal["export", "reverse", "mobile"] = "export",
        id: Optional[str] = None,
        url: Optional[str] = None,
        search: Optional[str] = None,
        linkedin: Optional[str] = None,
        domain: Optional[str] = None,
        name: Optional[str] = None,
    ) -> dict:
        """Resolve ONE person through AI Ark (bought data, per-credit).

        `op`:
        - **"export"** (default): the person WITH their email (synchronous email
          finder). Give `id` (from `linkedin_aiark_search(op="people")`) OR `url`
          (a LinkedIn profile URL).
        - **"reverse"**: find the person FROM a contact detail (`search` = an email,
          a phone number…).
        - **"mobile"**: their mobile phone number(s). Give `linkedin` (profile URL)
          alone, OR `domain` AND `name` together.

        Every op returns `{"found": false}` rather than an error when nothing
        matches — an absence is a result, not a failure.

        Args:
            op: export (default) | reverse | mobile.
            id: op="export" — an AI Ark person id from a prior search.
            url: op="export" — a LinkedIn profile URL.
            search: op="reverse" — the contact detail to resolve.
            linkedin: op="mobile" — the person's LinkedIn profile URL.
            domain: op="mobile" — the company domain (with `name`).
            name: op="mobile" — the person's name (with `domain`).
        """
        def _need(cond: bool, msg: str) -> None:
            if not cond:
                raise McpError(ErrorData(code=INVALID_PARAMS, message=msg))

        if op == "export":
            _need(bool(id or url), "op='export' exige `id` ou `url`.")
            result = _run(lambda c: c.export_person(id=id, url=url))
        elif op == "reverse":
            _need(bool(search), "op='reverse' exige `search`.")
            result = _run(lambda c: c.reverse_lookup(search))
        elif op == "mobile":
            _need(bool(linkedin) or bool(domain and name),
                  "op='mobile' exige `linkedin` OU (`domain` ET `name`).")
            result = _run(lambda c: c.mobile_phone(
                linkedin=linkedin, domain=domain, name=name))
        else:
            raise McpError(ErrorData(
                code=INVALID_PARAMS,
                message="op doit être 'export', 'reverse' ou 'mobile'"))

        if result is None:
            return {"found": False}
        return {"found": True, **result}
