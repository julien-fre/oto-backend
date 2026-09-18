"""Nextmotion — le client, les gardes d'arguments, la traduction des erreurs amont et
la sonde, partagés par tous les modules d'outils du connecteur.

Séparés des modules d'outils pour tenir sous 500 lignes. Deux règles vivent ici :
- **aucun argument n'est retenu au silence** : « fourni » se lit `is not None`, jamais
  la vérité — `dry_run=False` et `offset=0` sont des valeurs fournies ;
- **une erreur amont se classe sur `status_code`**, jamais sur le texte du message.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Optional

from mcp.types import ErrorData, INVALID_PARAMS

from .. import access
from ..connectors import verify as connector_verify
from ..mcp_errors import McpError

if TYPE_CHECKING:
    from oto.tools.nextmotion import NextmotionClient

_NAME = "nextmotion"


def _bad(msg: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=msg))


def _client() -> NextmotionClient:
    """Le client pour la clé de CET appelant. L'import réel est dans le corps : les
    tests remplacent la classe du package."""
    from oto.tools.nextmotion import NextmotionClient

    key, _ = access.resolve_api_key(_NAME)
    if not isinstance(key, str) or not key.strip():
        # Vide, le client irait chercher une clé dans l'environnement du serveur.
        raise _bad("Nextmotion : aucune clé API posée pour ce connecteur.")
    return NextmotionClient(api_key=key.strip())


def _run(fn: Callable[[], Any]) -> Any:
    """Exécute un appel au client ; une erreur de validation ou amont devient une
    consigne `INVALID_PARAMS`."""
    from oto.tools.common.errors import UpstreamHTTPError

    try:
        return fn()
    except ValueError as e:
        raise _bad(str(e)) from None
    except UpstreamHTTPError as e:
        raise _bad(_upstream_message(e)) from None


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


@dataclass(frozen=True)
class Kind:
    """Une ressource servie sous un `kind` d'outil : sa liste blanche, ses appels, ses
    filtres propres. `lister(c, clinic_id, **filtres, limit, offset)` et `lire(c, id)`
    sont `None` quand l'API n'a pas l'endpoint (l'op est alors refusée, nommément)."""
    plural: str
    shape: Callable[[Any], Any]
    lister: Optional[Callable[..., Any]] = None
    lire: Optional[Callable[..., Any]] = None
    filters: tuple = ()
    withheld: Optional[str] = None


def _serve_kind(kinds: dict, kind: str, op: str, *, client: Callable[[], Any],
                clinic_id: Optional[str],
                item_id: Optional[str], filters: dict, limit: Optional[int],
                offset: Optional[int], fields: Optional[list]) -> dict:
    """`op` list | get d'un outil à `kind` : un filtre d'un autre kind est refusé, un
    argument que l'op n'utilise pas aussi (`is not None`), l'appel part ensuite.

    `client` est la fabrique (`_client`), passée par le module d'outils : c'est chez lui
    que la sonde de version-skew lit les méthodes appelées."""
    from .nextmotion_socle import _one, _page

    if kind not in kinds:
        raise _bad(f"kind inconnu : {kind!r}.")
    k = kinds[kind]
    for name, value in filters.items():
        if name not in k.filters and value is not None:
            raise _bad(f"kind={kind!r} n'utilise pas `{name}`.")
    own = {name: filters[name] for name in k.filters}
    if op == "list":
        if k.lister is None:
            raise _bad(f"kind={kind!r} : l'API Nextmotion n'a pas de liste — op='get'.")
        _need(op, clinic_id=clinic_id)
        _refuse_ignored(op, item_id=item_id)
        c = client()
        return _page(_run(lambda: k.lister(c, clinic_id, **own, **_paging(limit, offset))),
                     k.plural, k.shape, fields=fields, withheld=k.withheld)
    if op == "get":
        if k.lire is None:
            raise _bad(f"kind={kind!r} : l'API Nextmotion n'a pas de lecture unitaire — "
                       "op='list'.")
        _need(op, item_id=item_id)
        _refuse_ignored(op, clinic_id=clinic_id, limit=limit, offset=offset, fields=fields,
                        **own)
        c = client()
        return _one(_run(lambda: k.lire(c, item_id)), kind, k.shape, withheld=k.withheld)
    raise _bad("op doit être 'list' ou 'get'.")
