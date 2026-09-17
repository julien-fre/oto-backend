"""PayFit — logiciel de paie et RH : l'entreprise, l'annuaire des collaborateurs,
leurs contrats et leurs absences, en LECTURE seule.

Wrappe `oto.tools.payfit.PayfitClient` (Bearer, « Partner API » v1). keyed
`api_key`, BYO (membre ou org) : une clé API PayFit est créée par un admin de
l'entreprise et ne donne accès qu'à cette entreprise — il n'y a pas de clé
plateforme. L'id d'entreprise n'est jamais demandé : le client le lit par
introspection de la clé. Hôtes fixes (`partner-api.payfit.com`,
`oauth.payfit.com`) : aucun champ ne désigne une destination, donc pas de garde
d'egress (`oto_mcp/egress.py`) à poser ici.

## Données de paie : ce que ce connecteur ne sert PAS

Bulletins de paie, journal comptable, fichiers de virement, mutuelle et
prévoyance, titres-restaurant, documents fiscaux : le client oto-core n'a aucune
méthode vers ces endpoints, et ce module n'en ajoute pas. Aucune écriture
(création de collaborateur, de contrat ou d'absence) n'est servie.

⚠️ **Les ressources du périmètre EMBARQUENT quand même des données personnelles
lourdes**, selon les scopes que porte la clé : un collaborateur peut arriver avec
son NIR, son IBAN/BIC, sa date et son pays de naissance, sa nationalité, son sexe,
ses adresses, téléphones et e-mails personnels ; un contrat FR avec le NIR, le
motif de rupture (dont les codes d'inaptitude), la mutuelle et la prévoyance ; une
absence avec un type qui nomme un arrêt maladie, un accident du travail ou une
maternité — donnée de santé (RGPD art. 9). Tout passe donc par une **projection en
LISTE BLANCHE** (`payfit_socle`) : seuls les champs nommés sortent, un champ que
l'API ajouterait demain reste dehors. Le type d'une absence n'est servi que s'il
est un congé ordinaire (congés payés, RTT, repos, sans solde, télétravail, école) ;
tout autre sort en `absence`. **Il n'existe aucune échappatoire vers le brut** :
`fields=["*"]` rend la vue par défaut, jamais plus.

## Surface (ADR 0047), verbe en `op`, lecture seule

- `payfit_company` — l'entreprise de la clé (seul, sans op).
- `payfit_collaborator` — list | get.
- `payfit_contract` — list | get ; `fr=True` → variante FR (nature du contrat,
  statut conventionnel, IDCC).
- `payfit_absence` — la liste des absences (l'API n'a pas de lecture unitaire).

**Aucun argument n'est retenu au silence** : un argument qu'un `op` n'utilise pas
est REFUSÉ, et « fourni » se lit `is not None` — un `fr=False` passé à un op qui
l'ignore est refusé comme les autres.

Dérivé de la documentation et de la spec OpenAPI publiques (lues le 2026-09-17).
**Aucun appel réel** : pas de clé disponible — la forme exacte des réponses n'est
pas vérifiée.
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from fastmcp import FastMCP
from mcp.types import ErrorData, INVALID_PARAMS

from .. import access
from ..connectors import verify as connector_verify
from ..mcp_errors import McpError
from . import payfit_socle as S

_NAME = "payfit"
_DEFAULT_LIMIT = 50


def _bad(msg: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=msg))


def _need(op: str, **required: Any) -> None:
    missing = [n for n, v in required.items() if v is None or v == ""]
    if missing:
        raise _bad(f"op={op!r} exige {', '.join('`' + m + '`' for m in missing)}.")


def _refuse_ignored(op: str, **provided: Any) -> None:
    """Un argument fourni que CET op n'utilise pas est une erreur d'intention.
    `is not None`, jamais la vérité : `False` et `0` sont des valeurs fournies."""
    for name, value in provided.items():
        if value is not None:
            raise _bad(f"op={op!r} n'utilise pas `{name}`.")


def _limit(limit: Optional[int]) -> int:
    return _DEFAULT_LIMIT if limit is None else limit


# ---------------------------------------------------------------------------
# Erreurs amont & sonde
# ---------------------------------------------------------------------------

def _upstream_message(e: Any) -> str:
    status = e.status_code
    if status == 401:
        return "PayFit : clé API refusée (HTTP 401) — invalide, révoquée ou inactive."
    if status == 403:
        return ("PayFit : accès refusé (HTTP 403) — la clé ne porte pas le scope requis "
                "(collaborators:read, contracts:read ou time:read selon la ressource).")
    if status == 404:
        return "PayFit : ressource introuvable (HTTP 404)."
    if status == 429:
        return "PayFit : trop de requêtes (HTTP 429) — réessaie dans un instant."
    if status >= 500:
        return f"PayFit est momentanément indisponible (HTTP {status})."
    return f"PayFit a refusé la requête (HTTP {status}) : {e.body}"


def _verify(fields: dict, config: dict | None = None) -> None:  # noqa: ARG001
    """Sonde « tester la connexion » : introspection de la clé puis
    `GET /companies/{id}` (aucun scope requis), sans effet.

    Une clé vide est refusée AVANT le client : passée vide, `PayfitClient`
    résoudrait `PAYFIT_API_KEY` dans l'environnement du serveur et testerait une
    autre clé que celle posée."""
    from oto.tools.common.errors import UpstreamHTTPError
    from oto.tools.payfit import PayfitClient

    key = (fields or {}).get("key")
    if not isinstance(key, str) or not key.strip():
        raise connector_verify.NonAutorise("PayFit : clé API vide.")
    try:
        PayfitClient(api_key=key.strip()).get_company()
    except UpstreamHTTPError as e:
        if e.status_code in (401, 403):
            raise connector_verify.NonAutorise(_upstream_message(e)) from e
        raise


def register(mcp: FastMCP) -> None:
    from oto.tools.common.errors import UpstreamHTTPError
    from oto.tools.payfit import PayfitClient

    connector_verify.register(_NAME, _verify)

    def _client() -> PayfitClient:
        key, _ = access.resolve_api_key(_NAME)
        if not isinstance(key, str) or not key.strip():
            # Vide, le client irait chercher une clé dans l'environnement du serveur.
            raise _bad("PayFit : aucune clé API posée pour ce connecteur.")
        return PayfitClient(api_key=key.strip())

    def _run(fn):
        try:
            return fn()
        except ValueError as e:
            raise _bad(str(e))
        except UpstreamHTTPError as e:
            raise _bad(_upstream_message(e))

    @mcp.tool()
    def payfit_company() -> dict:
        """The PayFit company the API key belongs to — name, country, registration
        number (SIRET in France), address, number of active contracts. Its
        `country` tells whether `payfit_contract(fr=True)` applies (FR only)."""
        c = _client()
        return S.one(_run(c.get_company), "company", S.company)

    @mcp.tool()
    def payfit_collaborator(
        op: Literal["list", "get"] = "list",
        collaborator_id: Optional[str] = None,
        email: Optional[str] = None,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
        fields: Optional[list] = None,
    ) -> dict:
        """The PayFit employee directory, reduced to what is professional: id, first
        and last names, professional emails, manager id, team, termination date,
        and each contract's id, dates and status. Job title and contract type are
        on `payfit_contract`.

        Personal data is withheld and cannot be requested: social security number,
        bank details, birth date and place, nationality, gender, personal
        addresses, phones and emails.

        `op`:
        - **"list"** (default): the collaborators, optionally the one whose contract
          carries `email`. Paginated: pass back `next_cursor` as `cursor`.
        - **"get"**: one collaborator (`collaborator_id`).

        Args:
            op: list (default) | get.
            collaborator_id: op="get".
            email: op="list" — exact contract email (not the login email).
            limit: op="list" — 1..50 (default 50).
            cursor: op="list" — `next_cursor` of the previous page.
            fields: op="list" — keep only these keys per row (`id` always kept);
                omitted or `["*"]` = the default view, never more.
        """
        c = _client()
        if op == "list":
            _refuse_ignored(op, collaborator_id=collaborator_id)
            return S.page(_run(lambda: c.list_collaborators(
                limit=_limit(limit), cursor=cursor, email=email)),
                "collaborators", S.collaborator, "id", fields=fields)
        if op == "get":
            _need(op, collaborator_id=collaborator_id)
            _refuse_ignored(op, email=email, limit=limit, cursor=cursor, fields=fields)
            return S.one(_run(lambda: c.get_collaborator(collaborator_id)),
                         "collaborator", S.collaborator)
        raise _bad("op doit être 'list' ou 'get'.")

    @mcp.tool()
    def payfit_contract(
        op: Literal["list", "get"] = "list",
        contract_id: Optional[str] = None,
        fr: Optional[bool] = None,
        include_in_progress: Optional[bool] = None,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
        fields: Optional[list] = None,
    ) -> dict:
        """Employment contracts in PayFit (active, pending, and last year's
        archived): job title, status, start and end dates, collaborator id.

        `fr=True` (French companies only) adds the contract nature
        (`natureContratDsn`: 01 CDI, 02 CDD, …), the conventional status
        (`statutConventionnelDsn`) and the collective agreement (`idcc`).
        Withheld: social security number, working time, termination reason,
        health insurance and provident fund, pay.

        `op`:
        - **"list"** (default): paginated; pass back `next_cursor` as `cursor`.
        - **"get"**: one contract (`contract_id`).

        Args:
            op: list (default) | get.
            contract_id: op="get".
            fr: French variant (default False).
            include_in_progress: op="list" — also contracts still being created.
            limit: op="list" — 1..50 (default 50).
            cursor: op="list" — `next_cursor` of the previous page.
            fields: op="list" — keep only these keys per row (`contractId` always
                kept); omitted or `["*"]` = the default view, never more.
        """
        c = _client()
        french = bool(fr)
        shape = S.contract_fr if french else S.contract
        if op == "list":
            _refuse_ignored(op, contract_id=contract_id)
            return S.page(_run(lambda: c.list_contracts(
                limit=_limit(limit), cursor=cursor,
                include_in_progress=include_in_progress, fr=french)),
                "contracts", shape, "contractId", fields=fields)
        if op == "get":
            _need(op, contract_id=contract_id)
            _refuse_ignored(op, include_in_progress=include_in_progress, limit=limit,
                            cursor=cursor, fields=fields)
            return S.one(_run(lambda: c.get_contract(contract_id, fr=french)),
                         "contract", shape)
        raise _bad("op doit être 'list' ou 'get'.")

    @mcp.tool()
    def payfit_absence(
        begin_date: Optional[str] = None,
        end_date: Optional[str] = None,
        contract_id: Optional[str] = None,
        status: Optional[list] = None,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
        fields: Optional[list] = None,
    ) -> dict:
        """Absences in PayFit: contract id, start and end (date + moment of day),
        status, type. Paginated: pass back `next_cursor` as `cursor`.

        ⚠️ The type is served only for ordinary leave (paid leave, RTT, rest,
        unpaid leave, remote work, school); every other type — sick leave, work
        accident, maternity, sick child, bereavement… — is served as `absence`.
        Do not try to infer the reason of an `absence`.

        Args:
            begin_date / end_date: YYYY-MM-DD — absences overlapping the window.
            contract_id: only this contract's absences (ids from `payfit_contract`
                or a collaborator's `contracts`).
            status: approved (PayFit's default) | pending_approval | declined |
                cancelled | pending_cancellation | all — one or several.
            limit: 1..50 (default 50).
            cursor: `next_cursor` of the previous page.
            fields: keep only these keys per row (`id` always kept); omitted or
                `["*"]` = the default view, never more.
        """
        c = _client()
        return S.page(_run(lambda: c.list_absences(
            limit=_limit(limit), cursor=cursor, contract_id=contract_id, status=status,
            begin_date=begin_date, end_date=end_date)),
            "absences", S.absence, "id", fields=fields)
