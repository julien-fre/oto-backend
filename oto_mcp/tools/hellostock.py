"""HelloStock administration — the READS of the marketplace (requests, offers,
members, positionings).

Wraps `oto.tools.hellostock.HelloStockAdminClient` (admin API `/api/admin`,
OpenAPI 3.1 contract). Personal `hs_…` token per user, administrator account
required; the translation of refusals lives in `hellostock_socle.py`. The three
actions that write are in `hellostock_ecritures.py`.

**One tool per object, verb in `op`** (ADR 0047): `list` (default) and `get`.
Positionings only have a read, hence a tool without `op`.

**Lists projected by default** (ratchet `tests/test_sorties_listes_projetees.py`):
each object declares the columns its default removes, the response NAMES them, and
`full=True` returns the whole page. What goes: the free fields of the original
form (`data`, no size bound), the advertising attribution
(`provenance`), photo URLs, and for a member the company record that
`company`/`sector`/`location` already summarize. A record (`op="get"`) is never
projected: it is the step where everything is read.

**The cursor is exposed as-is**: `nextCursor` of the previous page, null on the
last. `total` counts everything matching the filters.
"""
from __future__ import annotations

from typing import Annotated, Literal, Optional

from fastmcp import FastMCP
from oto.tools.hellostock import CERTIFICATS, MATIERES, SECTORS, STATUSES
from pydantic import Field

from ..connectors import verify as connector_verify
from .hellostock_socle import _bad, _client, _hors_op, _run, projeter, refus

_DROP_DEMANDE = ("data", "provenance")
_DROP_OFFRE = ("data", "provenance", "photos")
_DROP_MEMBRE = ("entreprise", "phone", "companyRole", "createdAt")
_DROP_POSITIONNEMENT = ("entreprise",)

_Limit = Annotated[int, Field(ge=1, le=200, description=(
    "Page size, 1-200. Continue with `cursor` = the previous page's `nextCursor`."))]


def _verify(fields: dict, config: dict | None = None) -> None:  # noqa: ARG001
    """« Test the connection » probe: one request read, `limit=1`, no side effect.

    It tests BOTH conditions of use at once — a token HelloStock
    knows (otherwise 401) AND an administrator account (otherwise 403) —, since all
    the routes of this API require both. The `hs_` prefix is checked before the call:
    a secret of any other shape would be refused with a 401 without anything saying why.
    """
    from oto.tools.common.errors import UpstreamHTTPError
    from oto.tools.hellostock import HelloStockAdminClient

    token = (fields.get("key") or "").strip()
    if not token.startswith("hs_"):
        raise ValueError(
            "This is not a HelloStock API token: they start with « hs_ ». "
            "Create one from hellostock.fr → Mon espace → Réglages → « Jetons d'API ».")
    try:
        HelloStockAdminClient(token=token).list_demandes(limit=1)
    except UpstreamHTTPError as e:
        raise ValueError(refus(e, carte=False)) from None


def register(mcp: FastMCP) -> None:
    connector_verify.register("hellostock", _verify)

    @mcp.tool()
    def hellostock_demande(
        op: Literal["list", "get"] = "list",
        demande_id: Optional[int] = None,
        status: Optional[Literal[STATUSES]] = None,
        since: Optional[str] = None,
        until: Optional[str] = None,
        departement: Optional[str] = None,
        matiere: Optional[Literal[MATIERES]] = None,
        service: Optional[str] = None,
        q: Optional[str] = None,
        limit: _Limit = 20,
        cursor: Optional[str] = None,
        full: bool = False,
    ) -> dict:
        """HelloStock demandes (buyers' requests for metal) — list or read one.

        - **"list"** (default): newest first, `{items, nextCursor, total}`. Filters
          are validated by HelloStock: a value outside its referential is REFUSED
          (400 with the reason), never an empty list. Projected: `data` and
          `provenance` are dropped (named in `projection`); `full=True` keeps them.
        - **"get"**: one demande by `demande_id`, whole — plus its positionnements
          (suppliers who answered) and its envois (members it was already sent to).

        To suggest suppliers for a demande: read it, then look for members whose
        `services`/`sector` fit (`hellostock_membre`) and offers of the same
        `matiere` (`hellostock_offre`).

        Args:
            op: "list" | "get".
            demande_id: op="get" — the demande's numeric id.
            status: lifecycle status; only `published` is visible on the public marketplace.
            since: createdAt >= since, `YYYY-MM-DD` or ISO datetime.
            until: createdAt < until, same format.
            departement: French département code (`69`, `2A`, `971`…), read from the buyer company's postal code.
            matiere: metal family.
            service: a service code as it appears in records' `services` lists; matches `services` or `customServices`.
            q: free text over specs, comment and the contact's / company's identity.
            cursor: `nextCursor` of the previous page.
            full: op="list" — whole records instead of projected ones.
        """
        if op == "get":
            if demande_id is None:
                raise _bad("op='get': `demande_id` required.")
            _hors_op("get", status=status, since=since, until=until,
                     departement=departement, matiere=matiere, service=service, q=q,
                     cursor=cursor)
            return _run(lambda: _client().get_demande(demande_id))
        _hors_op("list", demande_id=demande_id)
        page = _run(lambda: _client().list_demandes(
            status=status, since=since, until=until, departement=departement,
            matiere=matiere, service=service, q=q, limit=limit, cursor=cursor))
        return projeter(page, _DROP_DEMANDE, full)

    @mcp.tool()
    def hellostock_offre(
        op: Literal["list", "get"] = "list",
        offre_id: Optional[int] = None,
        status: Optional[Literal[STATUSES]] = None,
        since: Optional[str] = None,
        until: Optional[str] = None,
        departement: Optional[str] = None,
        matiere: Optional[Literal[MATIERES]] = None,
        certificat: Optional[Literal[CERTIFICATS]] = None,
        q: Optional[str] = None,
        limit: _Limit = 20,
        cursor: Optional[str] = None,
        full: bool = False,
    ) -> dict:
        """HelloStock offres (sellers' stock for sale) — list or read one.

        - **"list"** (default): newest first, `{items, nextCursor, total}`, filters
          refused (400) when outside HelloStock's referential. Projected: `data`,
          `provenance` and `photos` are dropped (named in `projection`);
          `full=True` keeps them.
        - **"get"**: one offre by `offre_id`, whole — including the certificate
          check (`certificat.verdictDetail`: the values read on the certificate).

        The keyword queue is `certificat="sans-mots-cles"` (certificate attached, no
        keyword yet): read each offre, then write its keywords with
        `hellostock_offre_update`.

        Args:
            op: "list" | "get".
            offre_id: op="get" — the offre's numeric id.
            status: lifecycle status; only `published` is visible on the public marketplace.
            since: createdAt >= since, `YYYY-MM-DD` or ISO datetime.
            until: createdAt < until, same format.
            departement: French département code as indexed on the offre (`69`, `2A`, `971`…).
            matiere: metal family.
            certificat: `dispo` = certificate declared or attached; `verifie` = automatic check passed; `sans-mots-cles` = attached and no keyword yet.
            q: free text over grade, dimensions, comment, keywords (exact) and the contact's / company's identity.
            cursor: `nextCursor` of the previous page.
            full: op="list" — whole records instead of projected ones.
        """
        if op == "get":
            if offre_id is None:
                raise _bad("op='get': `offre_id` required.")
            _hors_op("get", status=status, since=since, until=until,
                     departement=departement, matiere=matiere, certificat=certificat,
                     q=q, cursor=cursor)
            return _run(lambda: _client().get_offre(offre_id))
        _hors_op("list", offre_id=offre_id)
        page = _run(lambda: _client().list_offres(
            status=status, since=since, until=until, departement=departement,
            matiere=matiere, certificat=certificat, q=q, limit=limit, cursor=cursor))
        return projeter(page, _DROP_OFFRE, full)

    @mcp.tool()
    def hellostock_membre(
        op: Literal["list", "get"] = "list",
        membre_id: Optional[int] = None,
        q: Optional[str] = None,
        sector: Optional[Literal[SECTORS]] = None,
        service: Optional[str] = None,
        is_admin: Optional[bool] = None,
        has_offres: Optional[bool] = None,
        has_demandes: Optional[bool] = None,
        limit: _Limit = 20,
        cursor: Optional[str] = None,
        full: bool = False,
    ) -> dict:
        """HelloStock members (buyer and supplier accounts) — list or read one.

        - **"list"** (default): by increasing id (sort by name yourself),
          `{items, nextCursor, total}`. Projected: `entreprise` (summarised by
          `company`/`sector`/`location`), `phone`, `companyRole`, `createdAt` are
          dropped (named in `projection`); `full=True` keeps them.
        - **"get"**: one member by `membre_id`, whole — company details (SIRET,
          address), and their demandes, offres and message threads.

        A member's `id` is what `hellostock_demande_send` takes as a recipient.

        Args:
            op: "list" | "get".
            membre_id: op="get" — the member's numeric id.
            q: free text over company name, name and email.
            sector: the company's declared sector.
            service: a service code as it appears in members' `services` lists.
            is_admin: true = administrators only, false = never.
            has_offres: true = has posted at least one offre, false = none.
            has_demandes: true = has posted at least one demande, false = none.
            cursor: `nextCursor` of the previous page.
            full: op="list" — whole records instead of projected ones.
        """
        if op == "get":
            if membre_id is None:
                raise _bad("op='get': `membre_id` required.")
            _hors_op("get", q=q, sector=sector, service=service, is_admin=is_admin,
                     has_offres=has_offres, has_demandes=has_demandes, cursor=cursor)
            return _run(lambda: _client().get_user(membre_id))
        _hors_op("list", membre_id=membre_id)
        page = _run(lambda: _client().list_users(
            q=q, sector=sector, service=service, is_admin=is_admin,
            has_offres=has_offres, has_demandes=has_demandes, limit=limit,
            cursor=cursor))
        return projeter(page, _DROP_MEMBRE, full)

    @mcp.tool()
    def hellostock_positionnements(
        demande_id: Optional[int] = None,
        membre_id: Optional[int] = None,
        since: Optional[str] = None,
        limit: _Limit = 20,
        cursor: Optional[str] = None,
        full: bool = False,
    ) -> dict:
        """HelloStock positionnements: the suppliers who offered to serve a demande,
        with their message and whether a quote is attached (`devisUrl`).

        By increasing id, `{items, nextCursor, total}`. Projected: `entreprise` is
        dropped (the supplier is in `seller`), named in `projection`; `full=True`
        keeps it. The quote PDF itself is not readable here.

        Args:
            demande_id: positionnements on this demande.
            membre_id: positionnements by this supplier (a member id).
            since: createdAt >= since, `YYYY-MM-DD` or ISO datetime.
            cursor: `nextCursor` of the previous page.
            full: whole records instead of projected ones.
        """
        page = _run(lambda: _client().list_positionnements(
            demande_id=demande_id, user_id=membre_id, since=since, limit=limit,
            cursor=cursor))
        return projeter(page, _DROP_POSITIONNEMENT, full)
