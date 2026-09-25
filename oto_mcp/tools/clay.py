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
import re
import time
from typing import Any, Optional

from fastmcp import FastMCP
from mcp.types import INVALID_PARAMS, ErrorData

from .. import access, egress, output_projection
from ..connectors import verify as connector_verify
from ..mcp_errors import McpError

log = logging.getLogger("oto_mcp.tools.clay")

logger = logging.getLogger(__name__)

CONNECTOR = "clay"

# Lignes par appel : un POST webhook par ligne, sous le plafond dur de 45 s du
# chemin REST (`capabilities/tools_me.py`, `asyncio.wait_for(..., timeout=45)`).
# ~0,3-0,6 s par POST + la pause ci-dessous → 50 lignes tiennent avec de la marge.
MAX_ROWS = 50
PACE_S = 0.1
# Plafond dur d'un appel sur le chemin REST (`capabilities/tools_me.py`,
# `asyncio.wait_for(..., timeout=45)`), et délai (connexion, lecture) d'UN POST webhook.
REST_CALL_LIMIT_S = 45.0
WEBHOOK_TIMEOUT = (5, 10)
# Budget d'horloge d'un lot : passé ce délai on rend le reçu PARTIEL (le reste marqué
# non envoyé) plutôt que de laisser le délai d'appel couper sans reçu — l'agent
# réessaierait tout et Clay recevrait des doublons. Le budget n'est vérifié qu'AVANT
# chaque POST : un POST lancé juste avant l'échéance peut encore durer tout son délai.
# D'où : plafond REST − le pire POST − une marge pour ce qui précède la boucle
# (résolution de l'entrée, coffre). Vérifié par les tests : jamais au-delà de 45 s.
BATCH_MARGIN_S = 5.0
BATCH_BUDGET_S = REST_CALL_LIMIT_S - sum(WEBHOOK_TIMEOUT) - BATCH_MARGIN_S
MAX_ROUTINE_ITEMS = 100
#: Une valeur texte plus longue devient sa TAILLE dans la vue par défaut d'une page
#: (`<champ>_length`) : on retire une colonne, on ne tronque jamais un texte
#: (`output_projection.summarize`). `fields=["*"]` rend la page brute.
LONG_TEXT = 280

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


REFERENCE_CHUNK = 30_000
_HEADING = re.compile(r"^(#{1,4}) +(.+?)\s*$")


def _sections(text: str) -> list[dict]:
    """Découpe un Markdown en sections `{level, title, start, end}` — `end` = début
    du prochain titre de niveau ≤, donc une section INCLUT ses sous-sections."""
    lines = text.split("\n")
    heads, pos = [], 0
    for line in lines:
        m = _HEADING.match(line)
        if m:
            heads.append({"level": len(m.group(1)), "title": m.group(2), "start": pos})
        pos += len(line) + 1
    for i, h in enumerate(heads):
        h["end"] = next((k["start"] for k in heads[i + 1:] if k["level"] <= h["level"]),
                        len(text))
    return heads


def _shape_page(page: Any, fields: Optional[list[str]]) -> Any:
    """Page Clay resserrée : dans `data`, une colonne de TEXTE LONG devient sa taille
    (`<champ>_length`) ; `fields=[…]` ne garde que ces colonnes (+ `id`) ;
    `fields=["*"]` rend la page brute. L'enveloppe (curseur, `has_more`, `status`,
    quota…) reste intacte : sans elle l'agent croit avoir tout vu. Une page d'une autre
    forme passe telle quelle — une API tierce change de forme sans prévenir."""
    if not isinstance(page, dict):
        return page
    rows = page.get("data")
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        return page
    longs = sorted({k for r in rows for k, v in r.items()
                    if isinstance(v, str) and len(v) > LONG_TEXT})
    data, notice = output_projection.summarize(
        rows, body_fields=longs, fields=fields, always=("id",),
        hint=f'Vue par défaut : textes longs réduits à leur taille. `fields=["{output_projection.RAW}"]` '
             "rend la page brute, `fields=[…]` choisit les colonnes.")
    out = {**page, "data": data}
    if notice:
        out["projection"] = notice
    return out


def _slim_reference(text: str, section: Optional[str], offset: int) -> dict:
    """La référence de requête Clay (un Markdown de ~180 Ko) servie par morceaux :
    sommaire + l'essentiel sans `section`, sinon la section demandée, paginée."""
    heads = _sections(text)
    toc = [{"title": h["title"], "level": h["level"], "chars": h["end"] - h["start"]}
           for h in heads]
    if not section:
        core = [h for h in heads if h["title"].lower() in ("grammar", "operators")]
        return {"sections": toc,
                "content": "\n\n".join(text[h["start"]:h["end"]] for h in core),
                "hint": "Pass `section` (a title above) for the rest."}
    want = section.strip().lower()
    hit = (next((h for h in heads if h["title"].lower() == want), None)
           or next((h for h in heads if want in h["title"].lower()), None))
    if hit is None:
        raise _bad(f"Section « {section} » introuvable. Sections : "
                   + ", ".join(h["title"] for h in heads))
    body = text[hit["start"]:hit["end"]]
    offset = max(0, offset)
    chunk = body[offset:offset + REFERENCE_CHUNK]
    out: dict[str, Any] = {"section": hit["title"], "content": chunk,
                           "chars": len(body), "offset": offset}
    if offset + REFERENCE_CHUNK < len(body):
        out["next_offset"] = offset + REFERENCE_CHUNK
    return out


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
            hook = ClayTableWebhook(url, auth_token=token, timeout=WEBHOOK_TIMEOUT)
        except ValueError as e:
            raise _bad(f"Table `{name}` : {e} — recolle le webhook sur la carte Clay.")
        return hook, entry, _url_mark(url)

    def _sent_so_far(entry: dict, mark: str) -> int:
        meta = entry["meta"]
        return int(meta.get("submissions") or 0) if meta.get("submissions_url") == mark else 0

    def _mark_of(name: str) -> Optional[str]:
        """Marque de l'URL du webhook ACTUELLEMENT collé sur l'entrée `name`, ou None
        si elle ne se lit pas. Le compteur ne vaut que pour l'URL qu'il a comptée : un
        webhook recollé repart de zéro (même règle que `clay_push_rows`)."""
        try:
            url, _ = _webhook_of(access.resolve_credential_fields(CONNECTOR, account=name))
            return _url_mark(url)
        except Exception:  # noqa: SILENT — la liste reste servie ; le compteur de cette table est dit inconnu
            logger.warning("clay : webhook de la table illisible pour le compteur",
                           exc_info=True)
            return None

    def _upstream(e: UpstreamHTTPError) -> dict:
        return {"status": e.status_code, "error": str(e.body)[:300]}

    # --- tables (écriture) ----------------------------------------------------

    @mcp.tool()
    def clay_list_tables() -> dict:
        """List the Clay tables registered on the Clay connector card.

        Each table is a named entry holding that table's inbound webhook; pass its
        `name` to clay_push_rows. Returns name, level (member/group/org) and how many
        rows oto already sent to the webhook CURRENTLY pasted on it (Clay caps a
        webhook at 50,000 submissions, ever; a re-pasted webhook starts again at 0;
        `rows_sent` is null when the webhook cannot be read). Also says whether a Clay
        API key is set."""
        entries = _entries(_sub())
        tables = []
        for name, e in sorted(entries.items()):
            if e["kind"] != "table":
                continue
            mark = _mark_of(name)
            sent = _sent_so_far(e, mark) if mark else None
            tables.append({"name": name, "level": e["scope"], "rows_sent": sent,
                           "remaining": None if sent is None
                           else max(0, WEBHOOK_LIMIT - sent)})
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

        One row = one JSON object = one POST (a JSON array is NOT split: it makes a
        single row). The whole object lands in the table's Webhook column; its keys
        are mapped to columns once, in Clay. Clay then runs the table's enrichments
        on each new row, spending the table owner's Clay credits.

        Pass exactly one of `row` / `rows`. A 401/403 (webhook token refused) or a
        network error stops the batch at once; any other per-row error is recorded
        and the batch continues. A timeout while READING Clay's answer may still have
        written the row: it is reported in `failed`, and sending it again can create
        a duplicate in the table.

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
                     limit: int = 20, fields: Optional[list[str]] = None) -> dict:
        """Progress and results of a routine run started with clay_run_routine.

        Returns {status, finished, total, data, cursor}. Call again until
        `status` is `complete` (wait a few seconds between calls). A complete run can
        still hold `failed` items. If `cursor` is present, pass it back to read the
        next page (limit 1-100). By default a long text value in `data` comes back as
        its size (`<field>_length`, listed under `projection`).

        Args:
            routine_run_id: the id returned by clay_run_routine.
            cursor: from the previous page, for the next one.
            limit: items per page, 1-100.
            fields: keep only these item keys (plus `id`); ["*"] returns the raw page."""
        return _shape_page(_client().get_run_results(
            routine_run_id, cursor=cursor, limit=max(1, min(limit, 100))), fields)

    @mcp.tool()
    def clay_search_fields(source_type: str) -> dict:
        """Filter fields available to clay_search in filters mode, for `people` or
        `companies`, with Clay's usage guidance. Read this before building
        `filters`."""
        if source_type not in SOURCE_TYPES:
            raise _bad(f"source_type : {' | '.join(SOURCE_TYPES)}.")
        return _client().list_search_fields(source_type)

    @mcp.tool()
    def clay_search_reference(section: Optional[str] = None, offset: int = 0) -> dict:
        """Clay's search-query language, for clay_search in query mode — served by
        section (the full reference is ~180 KB).

        Without `section`: the table of contents plus the Grammar and Operators
        sections. With `section`: that section and its subsections (title match,
        case-insensitive — e.g. "People fields", "Location filtering", "Examples"),
        at most 30,000 characters per call; pass `next_offset` back as `offset` for
        the rest."""
        ref = _client().get_query_reference().get("reference") or ""
        return _slim_reference(ref, section, offset)

    @mcp.tool()
    def clay_search(
        source_type: Optional[str] = None,
        filters: Optional[dict] = None,
        query: Optional[str] = None,
        limit: int = 20,
        fields: Optional[list[str]] = None,
    ) -> dict:
        """Search Clay's people/companies database and return the first page.

        Two modes, pass one:
        - filters mode: `source_type` (people | companies) + `filters` (fields from
          clay_search_fields);
        - query mode: `query` (a Clay search query, see clay_search_reference).

        Returns {search_id, mode, data, has_more}. More pages: clay_search_next with
        the same search_id and mode. limit 1-500. Results count against the Clay
        plan's search quota (returned as period_quota). By default a long text value
        in `data` comes back as its size (`<field>_length`, listed under
        `projection`); `fields=["*"]` returns the raw page, `fields=[…]` picks keys
        (plus `id`)."""
        limit = max(1, min(limit, 500))
        c = _client()
        if query and (filters or source_type):
            raise _bad("Passe `query` SEUL, ou `source_type` + `filters` — pas les deux.")
        if query:
            created = c.create_query_search(query)
            page = c.run_query_search(created["search_id"], limit=limit)
            return _shape_page({"search_id": created["search_id"], "mode": "query",
                                "source_type": created.get("source_type"), **page},
                               fields)
        if source_type not in SOURCE_TYPES or not isinstance(filters, dict):
            raise _bad("Mode filtres : `source_type` (people | companies) + `filters` "
                       "(cf. clay_search_fields). Mode requête : `query`.")
        created = c.create_filters_search(source_type, filters)
        page = c.run_filters_search(created["search_id"], limit=limit)
        return _shape_page({"search_id": created["search_id"], "mode": "filters",
                            **page}, fields)

    @mcp.tool()
    def clay_search_next(search_id: str, mode: str, limit: int = 20,
                         fields: Optional[list[str]] = None) -> dict:
        """Next page of a search started with clay_search (pass back its
        `search_id` and `mode`: filters | query). Each call advances the iterator.
        Same page view as clay_search: long text values come back as their size by
        default; `fields=["*"]` returns the raw page, `fields=[…]` picks keys (plus
        `id`)."""
        limit = max(1, min(limit, 500))
        c = _client()
        if mode == "query":
            return _shape_page(c.run_query_search(search_id, limit=limit), fields)
        if mode == "filters":
            return _shape_page(c.run_filters_search(search_id, limit=limit), fields)
        raise _bad("mode : filters | query (celui rendu par clay_search).")

    @mcp.tool()
    def clay_tables_query(query: dict, cursor: Optional[str] = None,
                          limit: int = 50, fields: Optional[list[str]] = None) -> dict:
        """Read rows from existing Clay tables with a structured query (read-only).

        Clay Enterprise only: the table must have ClayQL sync enabled. Pages come least-recently-updated
        first; a row updated mid-scan can come back, so deduplicate by id. Pass the
        returned `cursor` for the next page (limit 1-100). To WRITE rows, use
        clay_push_rows. By default a long text value in `data` comes back as its size
        (`<field>_length`, listed under `projection`); `fields=["*"]` returns the raw
        page, `fields=[…]` picks keys (plus `id`)."""
        try:
            return _shape_page(_client().query_tables(
                query, cursor=cursor, limit=max(1, min(limit, 100))), fields)
        except UpstreamHTTPError as e:
            detail = str(e.body)[:300]
            # Constaté en live : une table sans sync ClayQL répond 400, pas 403.
            if e.status_code == 403 or "ClayQL sync" in detail:
                raise _bad(
                    "Clay refuse la lecture de cette table : la synchronisation API "
                    "(« ClayQL sync », plan Enterprise) n'y est pas activée. L'écriture "
                    f"par clay_push_rows reste possible. Détail : {detail}")
            raise
