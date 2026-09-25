"""Clay — tables (écriture par webhook) + Public API (routines, recherche, tables).

Wrappe `oto.tools.clay` (`ClayClient`, `ClayTableWebhook`). UNE carte, deux sortes
d'entrées nommées sur le credential `clay` (cf. `providers/clay.py`) :

- `kind=table` — le webhook entrant d'une table Clay. Le NOM de l'entrée est le nom
  de la table côté oto : `clay_push_rows(table="<nom>")`. Seul chemin d'écriture
  dans Clay (l'API publique est en lecture/exécution).
- `kind=api` — la clé Public API : `clay_account`, routines, recherche, tables.

`kind` vit dans `meta` : le listing des entrées ne déchiffre rien. Une entrée
précise se résout par la cascade habituelle (`resolve_credential_fields(account=)`,
membre > équipe > org).

Plafond Clay : 50 000 envois par webhook, jamais remis à zéro (même en supprimant
des lignes). On compte les envois réussis dans `meta` (clé liée à l'URL : coller un
nouveau webhook repart de zéro) et on prévient avant le mur.
"""
from __future__ import annotations

import hashlib
import logging
import time
from typing import Any, Optional

from fastmcp import FastMCP
from mcp.types import INVALID_PARAMS, ErrorData

from .. import access, egress
from ..connectors import verify as connector_verify
from ..mcp_errors import McpError

log = logging.getLogger("oto_mcp.tools.clay")

CONNECTOR = "clay"

# Lignes par appel : un POST webhook par ligne, sous le plafond dur de 45 s du
# chemin REST (`capabilities/tools_me.py`, `asyncio.wait_for(..., timeout=45)`).
# ~0,3-0,6 s par POST + la pause ci-dessous → 50 lignes tiennent avec de la marge.
MAX_ROWS = 50
PACE_S = 0.1
# Budget d'horloge d'un lot, bien sous les 45 s : passé ce délai on rend le reçu
# PARTIEL (le reste marqué non envoyé) plutôt que de laisser le délai d'appel
# couper sans reçu — l'agent réessaierait tout et Clay recevrait des doublons.
BATCH_BUDGET_S = 30.0
MAX_ROUTINE_ITEMS = 100

WEBHOOK_LIMIT = 50_000
WEBHOOK_WARN_AT = 45_000

_SCOPES = ("member", "group", "org")


def _bad(message: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=message))


def _webhook_of(fields: dict) -> tuple[str, Optional[str]]:
    """(url, jeton) d'une entrée table : le champ `webhook` accepte l'URL nue ou la
    commande cURL copiée depuis Clay ; `auth_token` explicite prime sur celui du cURL."""
    from oto.tools.clay import parse_curl

    parsed = parse_curl(fields.get("webhook") or "")
    return parsed["webhook_url"], (fields.get("auth_token") or parsed["auth_token"])


def _url_mark(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]


def _verify(fields: dict, config: dict | None = None) -> None:
    """Sonde « tester la connexion » — otomata-tech/oto#69. Couvre `auth` SEUL.

    Selon la sorte d'entrée :
    - `api` : `GET /me` (déjà dans le client — `get_me`), le plus petit appel
      authentifié de l'API publique ; un 401 = clé refusée.
    - `table` : forme seule (URL https sur un hôte clay.com, cURL lisible). Un POST
      de test écrirait une ligne parasite dans la table du client : on ne le fait pas.
    """
    from oto.tools.clay import ClayClient, is_clay_webhook_url

    kind = (fields.get("kind") or (config or {}).get("kind") or "").strip().lower()
    if kind == "api" or (not kind and fields.get("api_key")):
        ClayClient(api_key=fields["api_key"]).get_me()
        return
    url, _ = _webhook_of(fields)
    if not is_clay_webhook_url(url):
        raise ValueError("webhook : URL https://…clay.com/… attendue")
    egress.check_url(url, connector=CONNECTOR, field="webhook")


def _entries(sub: str) -> dict[str, dict]:
    """Entrées `clay` VISIBLES du sub, par nom : {kind, entity}. Le palier le plus
    proche gagne (membre > équipe > org), comme la cascade de résolution."""
    from .. import credentials_store
    from ..connectors.identities import keyed_entity

    out: dict[str, dict] = {}
    for scope in _SCOPES:
        ent = keyed_entity(sub, scope)
        if ent is None:
            continue
        for row in credentials_store.list_accounts(ent[0], ent[1], CONNECTOR):
            name = row["account"]
            if name in out:
                continue
            meta = row.get("meta") or {}
            out[name] = {
                "kind": (meta.get("kind") or "").lower(),
                "scope": scope,
                "entity": ent,
                "meta": meta,
            }
    return out


def register(mcp: FastMCP) -> None:
    from oto.tools.clay import ClayClient, ClayTableWebhook, SOURCE_TYPES
    from oto.tools.common import UpstreamHTTPError

    from .. import credentials_store

    connector_verify.register(CONNECTOR, _verify)

    def _sub() -> str:
        return access.current_user_sub_or_raise()

    def _client() -> ClayClient:
        apis = [n for n, e in _entries(_sub()).items() if e["kind"] == "api"]
        if not apis:
            raise _bad(
                "Aucune clé API Clay posée : sur la carte Clay, ajoute une entrée de "
                "type `api` (Clay → Settings → Account → API keys). Les webhooks de "
                "table ne suffisent pas pour ce tool.")
        fields = access.resolve_credential_fields(CONNECTOR, account=apis[0])
        return ClayClient(api_key=fields.get("api_key"))

    def _table(name: str) -> tuple[ClayTableWebhook, dict, str]:
        entries = _entries(_sub())
        tables = sorted(n for n, e in entries.items() if e["kind"] == "table")
        entry = entries.get(name)
        if entry is None or entry["kind"] != "table":
            raise _bad(
                f"Table Clay `{name}` inconnue. Tables enregistrées : "
                f"{', '.join(tables) or 'aucune'}. Pour en ajouter une : dans Clay, "
                "+ Add → Monitor webhook, copie la commande cURL, puis colle-la sur la "
                "carte Clay (type `table`, nom = le nom à passer ici).")
        fields = access.resolve_credential_fields(CONNECTOR, account=name)
        url, token = _webhook_of(fields)
        egress.check_url(url, connector=CONNECTOR, field="webhook")
        try:
            hook = ClayTableWebhook(url, auth_token=token)
        except ValueError as e:
            raise _bad(f"Table `{name}` : {e} — recolle le webhook sur la carte Clay.")
        return hook, entry, _url_mark(url)

    def _sent_so_far(entry: dict, mark: str) -> int:
        meta = entry["meta"]
        return int(meta.get("submissions") or 0) if meta.get("submissions_url") == mark else 0

    def _upstream(e: UpstreamHTTPError) -> dict:
        return {"status": e.status_code, "error": str(e.body)[:300]}

    # --- tables (écriture) ----------------------------------------------------

    @mcp.tool()
    def clay_list_tables() -> dict:
        """List the Clay tables registered on the Clay connector card.

        Each table is a named entry holding that table's inbound webhook; pass its
        `name` to clay_push_rows. Returns name, level (member/group/org) and how many
        rows oto already sent to that webhook (Clay caps a webhook at 50,000
        submissions, ever). Also says whether a Clay API key is set."""
        entries = _entries(_sub())
        tables = []
        for name, e in sorted(entries.items()):
            if e["kind"] != "table":
                continue
            sent = int(e["meta"].get("submissions") or 0)
            tables.append({"name": name, "level": e["scope"], "rows_sent": sent,
                           "remaining": max(0, WEBHOOK_LIMIT - sent)})
        return {
            "tables": tables,
            "api_key_set": any(e["kind"] == "api" for e in entries.values()),
            "how_to_add": "Clay → + Add → Monitor webhook → copy the cURL command → "
                          "paste it on the Clay connector card as a `table` entry.",
        }

    @mcp.tool()
    def clay_push_rows(
        table: str,
        row: Optional[dict] = None,
        rows: Optional[list[dict]] = None,
        dry_run: bool = False,
    ) -> dict:
        """Write rows into a Clay table through its inbound webhook.

        One row = one flat JSON object whose keys become the table's columns (Clay
        creates missing columns). Clay then runs the table's enrichments on each new
        row, spending the table owner's Clay credits.

        Pass exactly one of `row` / `rows`. A 401/403 (webhook token refused) or a
        network error stops the batch at once; any other per-row error is recorded
        and the batch continues.

        Args:
            table: name of a table entry on the Clay card (see clay_list_tables).
            row: a single row. Returns Clay's direct response.
            rows: several rows (max 50 per call — call again for more). Returns a
                receipt {total, succeeded, failed: [{index, error}]}.
            dry_run: validate and show what would be sent, send nothing."""
        if (row is None) == (rows is None):
            raise _bad("Passe exactement un de `row` (une ligne) ou `rows` (plusieurs).")
        batch = [row] if row is not None else list(rows or [])
        if not batch:
            raise _bad("`rows` est vide.")
        if len(batch) > MAX_ROWS:
            raise _bad(f"{len(batch)} lignes : maximum {MAX_ROWS} par appel "
                       "(un POST par ligne, sous le délai d'un appel). Découpe en lots.")
        for i, r in enumerate(batch):
            if not isinstance(r, dict) or not r:
                raise _bad(f"Ligne {i} : un objet JSON non vide est attendu.")

        hook, entry, mark = _table(table)
        sent = _sent_so_far(entry, mark)
        if sent + len(batch) > WEBHOOK_LIMIT:
            raise _bad(
                f"Le webhook de `{table}` a déjà reçu {sent} lignes ; Clay plafonne un "
                f"webhook à {WEBHOOK_LIMIT} envois. Crée un nouveau webhook dans la "
                "table (+ Add → Monitor webhook) et recolle-le sur cette entrée.")

        if dry_run:
            return {"dry_run": True, "table": table, "would_send": len(batch),
                    "rows": batch[:5], "rows_sent_so_far": sent}

        if row is not None:
            result = hook.push(row)
            _count(entry, table, mark, sent + 1)
            return {"table": table, "sent": True, "response": result}

        ok, failed = 0, []
        deadline = time.monotonic() + BATCH_BUDGET_S

        def _rest(start: int, why: str) -> None:
            failed.extend({"index": j, "error": f"not sent ({why})"}
                          for j in range(start, len(batch)))

        try:
            for i, r in enumerate(batch):
                if i:
                    if time.monotonic() >= deadline:
                        _rest(i, "time budget — send these again")
                        break
                    time.sleep(PACE_S)
                try:
                    hook.push(r)
                    ok += 1
                except UpstreamHTTPError as e:
                    failed.append({"index": i, **_upstream(e)})
                    if e.status_code in (401, 403):
                        _rest(i + 1, "batch aborted")
                        break
                except Exception as e:  # noqa: SILENT — rendu dans le reçu (failed[]) ; réseau : on arrête le lot
                    failed.append({"index": i, "error": f"{type(e).__name__}: {e}"[:300]})
                    _rest(i + 1, "batch aborted")
                    break
        finally:
            # Même si l'appel est coupé en plein lot : ce qui est parti chez Clay compte.
            if ok:
                _count(entry, table, mark, sent + ok)
        out: dict[str, Any] = {"table": table, "total": len(batch),
                               "succeeded": ok, "failed": failed}
        if sent + ok >= WEBHOOK_WARN_AT:
            out["warning"] = (f"{sent + ok}/{WEBHOOK_LIMIT} envois sur ce webhook : "
                              "prévois un nouveau webhook pour cette table.")
        return out

    def _count(entry: dict, table: str, mark: str, total: int) -> None:
        ent = entry["entity"]
        try:
            credentials_store.update_meta(
                ent[0], ent[1], CONNECTOR, table,
                {"submissions": total, "submissions_url": mark})
        except Exception:
            # Le compteur est un garde-fou, pas la vérité : jamais au prix de
            # l'écriture déjà faite chez Clay — mais on le dit.
            log.warning("clay: compteur d'envois non mis à jour (table %s)", table,
                        exc_info=True)

    # --- API publique -------------------------------------------------------------

    @mcp.tool()
    def clay_account() -> dict:
        """Who the Clay API key belongs to (user, workspace) and the workspace's
        credit balances. Requires an `api` entry on the Clay card."""
        c = _client()
        return {"me": c.get_me(), "credits": c.get_credit_balance()}

    @mcp.tool()
    def clay_run_routine(routine_id: str, items: list[dict]) -> dict:
        """Run a Clay routine (Clay-managed function, custom function or Workflow)
        on 1-100 items. Asynchronous: returns {routine_run_id, status: in_progress}
        — read results with clay_get_run. Spends the key owner's Clay credits, like
        the same work in the Clay app.

        Args:
            routine_id: e.g. `function:t_abc123`. Clay has no endpoint listing
                routines: copy the id from the Clay app, or ask the user for it.
            items: [{"id": "<your id, ≤64 chars>", "inputs": {...}}] — `id` comes
                back with each result so you can match them."""
        if not 1 <= len(items or []) <= MAX_ROUTINE_ITEMS:
            raise _bad(f"`items` : 1 à {MAX_ROUTINE_ITEMS} éléments (limite Clay).")
        for i, it in enumerate(items):
            if not isinstance(it, dict) or not it.get("id") or not isinstance(
                    it.get("inputs"), dict):
                raise _bad(f"items[{i}] : forme attendue {{\"id\": \"…\", \"inputs\": {{…}}}}.")
        return _client().run_routine(routine_id, items)

    @mcp.tool()
    def clay_get_run(routine_run_id: str, cursor: Optional[str] = None,
                     limit: int = 20) -> dict:
        """Progress and results of a routine run started with clay_run_routine.

        Returns {status, finished, total, data, cursor}. Call again until
        `status` is `complete` (wait a few seconds between calls). A complete run can
        still hold `failed` items. If `cursor` is present, pass it back to read the
        next page (limit 1-100)."""
        return _client().get_run_results(routine_run_id, cursor=cursor,
                                             limit=max(1, min(limit, 100)))

    @mcp.tool()
    def clay_search_fields(source_type: str) -> dict:
        """Filter fields available to clay_search in filters mode, for `people` or
        `companies`, with Clay's usage guidance. Read this before building
        `filters`."""
        if source_type not in SOURCE_TYPES:
            raise _bad(f"source_type : {' | '.join(SOURCE_TYPES)}.")
        return _client().list_search_fields(source_type)

    @mcp.tool()
    def clay_search_reference() -> dict:
        """Grammar of Clay search queries, for clay_search in query mode."""
        return _client().get_query_reference()

    @mcp.tool()
    def clay_search(
        source_type: Optional[str] = None,
        filters: Optional[dict] = None,
        query: Optional[str] = None,
        limit: int = 20,
    ) -> dict:
        """Search Clay's people/companies database and return the first page.

        Two modes, pass one:
        - filters mode: `source_type` (people | companies) + `filters` (fields from
          clay_search_fields);
        - query mode: `query` (a Clay search query, see clay_search_reference).

        Returns {search_id, mode, data, has_more}. More pages: clay_search_next with
        the same search_id and mode. limit 1-500. Results count against the Clay
        plan's search quota (returned as period_quota)."""
        limit = max(1, min(limit, 500))
        c = _client()
        if query and (filters or source_type):
            raise _bad("Passe `query` SEUL, ou `source_type` + `filters` — pas les deux.")
        if query:
            created = c.create_query_search(query)
            page = c.run_query_search(created["search_id"], limit=limit)
            return {"search_id": created["search_id"], "mode": "query",
                    "source_type": created.get("source_type"), **page}
        if source_type not in SOURCE_TYPES or not isinstance(filters, dict):
            raise _bad("Mode filtres : `source_type` (people | companies) + `filters` "
                       "(cf. clay_search_fields). Mode requête : `query`.")
        created = c.create_filters_search(source_type, filters)
        page = c.run_filters_search(created["search_id"], limit=limit)
        return {"search_id": created["search_id"], "mode": "filters", **page}

    @mcp.tool()
    def clay_search_next(search_id: str, mode: str, limit: int = 20) -> dict:
        """Next page of a search started with clay_search (pass back its
        `search_id` and `mode`: filters | query). Each call advances the iterator."""
        limit = max(1, min(limit, 500))
        c = _client()
        if mode == "query":
            return c.run_query_search(search_id, limit=limit)
        if mode == "filters":
            return c.run_filters_search(search_id, limit=limit)
        raise _bad("mode : filters | query (celui rendu par clay_search).")

    @mcp.tool()
    def clay_tables_query(query: dict, cursor: Optional[str] = None,
                          limit: int = 50) -> dict:
        """Read rows from existing Clay tables with a structured query (read-only).

        Clay Enterprise only (API table sync). Pages come least-recently-updated
        first; a row updated mid-scan can come back, so deduplicate by id. Pass the
        returned `cursor` for the next page (limit 1-100). To WRITE rows, use
        clay_push_rows."""
        try:
            return _client().query_tables(query, cursor=cursor,
                                              limit=max(1, min(limit, 100)))
        except UpstreamHTTPError as e:
            if e.status_code == 403:
                raise _bad(
                    "Clay refuse la lecture de tables pour cette clé (403) : "
                    "`/tables/query` est réservé au plan Enterprise de Clay. "
                    f"Détail : {str(e.body)[:200]}")
            raise
