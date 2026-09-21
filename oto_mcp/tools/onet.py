"""O*NET — le référentiel des métiers des États-Unis (O*NET Web Services v2).

Wrappe `oto.tools.onet.client.ONetClient`. keyed `api_key` (en-tête `X-API-Key`),
byo-only : la clé est gratuite mais nominative (inscription développeur sur
services.onetcenter.org), aucune clé plateforme.

Un seul outil, `onet_occupation` — un objet métier, deux gestes (ADR 0047) :
- `op="search"` : trouver un métier et son code O*NET-SOC par mot-clé ou par code ;
- `op="get"` : la fiche d'un métier — titre, description, intitulés de poste, tâches.

**Aucun paramètre n'est ignoré en silence** : une op qui n'utilise pas un argument
fourni REFUSE (patron `_refuse_ignored`).

⚠️ **Écrit d'après le manuel de référence v2.0, jamais exercé en live** : aucune clé
n'était disponible à l'écriture. Chemins, en-tête d'auth et formes de réponse sont
ceux du manuel ; le premier appel réel reste à faire.

Les appels au client sont écrits en clair (`_client().get_occupation(…)`) : c'est ce
qui les rend vérifiables par la sonde version-skew.
"""
from __future__ import annotations

from typing import Literal, Optional

from fastmcp import FastMCP
from mcp.types import ErrorData, INVALID_PARAMS

from .. import access, output_projection
from ..connectors import verify as connector_verify
from ..mcp_errors import McpError

# Liens de navigation de l'API (une URL par rapport et par résultat) : ils ne servent
# qu'à un client HTTP authentifié, pas à un agent. Rendus sur `full=True`.
_SEARCH_ITEM_DROP = ("href",)
_GET_DROP = ("summary_contents", "details_contents", "custom_contents", "updated")
_MAX_LIMIT = 100


def _bad(msg: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=msg))


def _refuse_ignored(op: str, hint: str, **provided) -> None:
    for name, value in provided.items():
        if value is not None:
            raise _bad(f"op={op!r} n'utilise pas `{name}` — {hint}")


def _upstream_message(e) -> str:
    status = e.status_code
    if status in (401, 403):
        return (f"O*NET a rejeté la clé API (HTTP {status}) — vérifie la clé posée sur "
                "ce connecteur (services.onetcenter.org → My Account).")
    if status == 404:
        return f"O*NET : ressource introuvable (HTTP 404) — {e.body}"
    if status == 422:
        return ("O*NET : requête refusée (HTTP 422) — paramètre invalide, code "
                f"O*NET-SOC inexistant ou obsolète, ou donnée absente pour ce métier : {e.body}")
    if status == 429:
        return "O*NET : service saturé (429) — réessaie dans un instant."
    if status in (500, 502, 503, 504):
        return f"O*NET est momentanément indisponible (HTTP {status}) — réessaie plus tard."
    return f"O*NET a refusé la requête (HTTP {status}) : {e.body}"


def _verify(fields: dict, config: dict | None = None) -> None:  # noqa: ARG001
    """« Tester la connexion » : une recherche à 1 résultat, l'appel le plus léger."""
    from oto.tools.onet.client import ONetClient
    ONetClient(api_key=fields["key"]).search_occupations("engineer", end=1)


def register(mcp: FastMCP) -> None:
    from oto.tools.onet.client import ONetClient
    from oto.tools.common.errors import UpstreamHTTPError

    connector_verify.register("onet", _verify)

    def _client() -> ONetClient:
        key, _ = access.resolve_api_key("onet")
        return ONetClient(api_key=key)

    def _run(fn):
        try:
            return fn()
        except ValueError as e:
            raise _bad(str(e))
        except UpstreamHTTPError as e:
            raise _bad(_upstream_message(e))

    @mcp.tool()
    def onet_occupation(
        op: Literal["search", "get"] = "search",
        keyword: Optional[str] = None,
        code: Optional[str] = None,
        limit: int = 20,
        full: bool = False,
    ) -> dict:
        """A US occupation in the O*NET taxonomy — find it, or read its profile.

        op="search": occupations matching a word, phrase, job title or (partial)
        code, closest first. Returns — `{"start", "end", "total", "occupation":
        [{"code", "title", "tags"}]}`.
        op="get": one occupation by O*NET-SOC code. Returns — `{"code", "title",
        "description", "sample_of_reported_titles", "also_see", "tags",
        "bright_outlook", "tasks": [...], "tasks_total"}`; not every occupation
        carries every field, and one without task data returns `tasks: []`.

        An O*NET-SOC code is 8 digits ("15-1299.08"); its first 6 ("15-1299") are
        the SOC code that US wage statistics are published under.

        Args:
            op: "search" (default) | "get".
            keyword: REQUIRED by search — word, phrase, job title or code.
            code: REQUIRED by get — "15-1299.08"; a 6-digit SOC is read as ".00".
            limit: max results (search) or max tasks (get), 1-100, default 20.
            full: True also returns the API's navigation links (per-result `href`,
                report link lists, data-update history), dropped by default.
        """
        if not 1 <= limit <= _MAX_LIMIT:
            raise _bad(f"`limit` doit être entre 1 et {_MAX_LIMIT}.")
        if op == "search":
            _refuse_ignored(op, "passe `keyword` (un code s'y cherche aussi).", code=code)
            if not (keyword or "").strip():
                raise _bad("op='search' exige `keyword`.")
            found = _run(lambda: _client().search_occupations(keyword.strip(), end=limit))
            if full:
                return found
            return output_projection.project(found, items_path="occupation",
                                             item_drop=_SEARCH_ITEM_DROP)
        if op == "get":
            _refuse_ignored(op, "passe `code` (le code O*NET-SOC du métier).",
                            keyword=keyword)
            if not (code or "").strip():
                raise _bad("op='get' exige `code` (ex. '15-1299.08').")
            occupation = _run(lambda: _client().get_occupation(code))
            try:
                tasks = _client().get_occupation_tasks(code, end=limit)
            except UpstreamHTTPError as e:
                if e.status_code != 422:       # 422 = ce métier n'a pas de tâches
                    raise _bad(_upstream_message(e))
                tasks = {"task": [], "total": 0}
            occupation["tasks"] = [t.get("title") for t in tasks.get("task") or []]
            occupation["tasks_total"] = tasks.get("total", len(occupation["tasks"]))
            if full:
                return occupation
            return output_projection.project(occupation, drop=_GET_DROP, items_path="also_see",
                                             item_drop=_SEARCH_ITEM_DROP)
        raise _bad("op doit être 'search' ou 'get'.")
