"""Nextmotion — les gardes d'arguments, la traduction des erreurs amont et la sonde.

Séparées de `nextmotion.py` (outils) pour tenir sous 500 lignes. Deux règles vivent ici :
- **aucun argument n'est retenu au silence** : « fourni » se lit `is not None`, jamais
  la vérité — `dry_run=False` et `offset=0` sont des valeurs fournies ;
- **une erreur amont se classe sur `status_code`**, jamais sur le texte du message.
"""
from __future__ import annotations

from typing import Any, Optional

from mcp.types import ErrorData, INVALID_PARAMS

from ..connectors import verify as connector_verify
from ..mcp_errors import McpError


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


def _paging(limit: Optional[int], offset: Optional[int]) -> dict:
    return {"limit": 50 if limit is None else limit, "offset": 0 if offset is None else offset}


def _error_codes(body: Any) -> set:
    if not isinstance(body, dict):
        return set()
    return {e.get("code") for e in body.get("errors") or [] if isinstance(e, dict)}


def _upstream_message(e: Any) -> str:
    status = e.status_code
    if status == 401:
        return "Nextmotion : clé API refusée (HTTP 401) — invalide, régénérée ou supprimée."
    if status == 403:
        if "non_employee_access_denied" in _error_codes(e.body):
            return ("Nextmotion : accès refusé (HTTP 403) — l'utilisateur de la clé "
                    "n'est pas employé de cette clinique.")
        return "Nextmotion : accès refusé (HTTP 403)."
    if status == 404:
        return "Nextmotion : ressource introuvable (HTTP 404)."
    if status == 429:
        return "Nextmotion : trop de requêtes (HTTP 429) — réessaie dans un instant."
    if status >= 500:
        return f"Nextmotion est momentanément indisponible (HTTP {status})."
    return f"Nextmotion a refusé la requête (HTTP {status}) : {e.body}"


def _verify(fields: dict, config: dict | None = None) -> None:  # noqa: ARG001
    """Sonde « tester la connexion » : `GET /v4/users/me`, sans paramètre ni effet.

    Une clé vide est refusée AVANT le client : passée vide, `NextmotionClient`
    résoudrait `NEXTMOTION_API_KEY` dans l'environnement du serveur et testerait
    une autre clé que celle posée."""
    from oto.tools.common.errors import UpstreamHTTPError
    from oto.tools.nextmotion import NextmotionClient

    key = (fields or {}).get("key")
    if not isinstance(key, str) or not key.strip():
        raise connector_verify.NonAutorise("Nextmotion : clé API vide.")
    try:
        NextmotionClient(api_key=key.strip()).get_me()
    except UpstreamHTTPError as e:
        if e.status_code in (401, 403):
            raise connector_verify.NonAutorise(_upstream_message(e)) from e
        raise
