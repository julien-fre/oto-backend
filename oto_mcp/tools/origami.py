"""Origami — campagnes email + LinkedIn : tables de leads, campagnes, lancement, stats.

Wrappe `oto.tools.origami.client.OrigamiClient` (API v2 `origami.chat/api/v2`, Bearer
`og_live_…`). keyed `api_key`, byo-only (pas de clé plateforme) : l'org connecte SON
compte Origami — les crédits d'enrichissement et les envois sont les siens.

⚠️ **NOTE DE CONCEPTION — premier montage tiers dont l'écriture ENVOIE.** Les montages
HTTP « génériques » d'oto sont pensés lecture-seule (le connecteur `http` s'annonce
GET-only au catalogue) ; les connecteurs qui écrivent (folk, lemlist, notion…) écrivent
dans un CRM ou un outil, pas vers des tiers. La valeur d'Origami est dans le POST :
créer une table depuis un CSV, upserter des lignes, faire rédiger une campagne par
l'agent Origami, et **la lancer** — ce qui envoie des emails et des messages LinkedIn
à des personnes réelles, à l'échelle, sans retour arrière. C'est donc le premier
connecteur tiers d'oto dont l'écriture sort de la plateforme vers des inconnus ; le
mainteneur décide si c'est acceptable. Tout est implémenté proprement et CHAQUE tool
mutant est gaté par la convention `dry_run` oto-wide (la validation tourne, l'appel
final est sauté, la réponse porte `dry_run: true` + un aperçu) ; le lancement est
`dry_run=True` PAR DÉFAUT — il faut passer `dry_run=False` pour envoyer.

Faits d'API vérifiés en live les 16–17/08/2026 et portés par les docstrings des
tools (l'agent doit les lire, il n'y a pas d'autre endroit) :
- listes en enveloppe `{items[], nextCursor}` (50/page) — `origami_rows` suit
  `nextCursor` côté serveur jusqu'à `max_pages` ;
- l'upsert n'accepte que des colonnes `input`, adressées par leur SLUG (tirets) ;
  slug inconnu → 400 `UNKNOWN_FIELDS` — on lit les colonnes AVANT d'écrire ;
- l'upload est du JSON (base64), jamais du multipart ; un CSV `mode: "table"` CRÉE
  une table ;
- la campagne se crée par un run AGENTIQUE (202 `{agent, run}`, à suivre via
  `origami_run_get`) ; pas de `GET /campaigns` global (lister par table) et pas de
  `GET /runs/{id}` (le run se lit sous son agent) ; `GET /sequences?workspaceId=`
  est la seule vue qui voit toutes les séquences d'un workspace ;
- `blockPriorContacts=True` supprime de la campagne toute personne DÉJÀ enrôlée
  auparavant, MÊME dans un brouillon supprimé jamais envoyé ;
- le lancement peut répondre 200 avec `launch.blocked.missingChannels` : aucun compte
  émetteur pour ces canaux, RIEN n'est parti ;
- la suppression est en deux temps, et le 2e temps peut répondre 200 sans supprimer :
  on re-GET et on n'affirme « supprimée » que sur un 404.

Les appels au client sont écrits en clair (`c.list_tables(…)`) : c'est ce qui les rend
vérifiables par la sonde version-skew (`test_tools_client_methods_exist`).
"""
from __future__ import annotations

import base64
import csv
import io
from typing import Any, Literal, Optional

from fastmcp import FastMCP
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData, INVALID_PARAMS

from .. import access, connector_verify

# Plafond de l'API par appel d'upsert (au-delà : 400).
_UPSERT_MAX_ROWS = 100
# Pages suivies au maximum par `origami_rows(op="list")` — 50 lignes/page ⇒ 20 pages =
# 1 000 lignes, la taille raisonnable d'un retour d'outil ; au-delà, l'agent relance
# avec le `cursor` rendu.
_ROWS_MAX_PAGES_CAP = 20
# Lignes montrées dans l'aperçu d'un upload CSV en dry_run.
_CSV_PREVIEW_ROWS = 5


def _bad(msg: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=msg))


def _upstream_message(e) -> str:
    status, body = e.status_code, e.body
    code = body.get("code") if isinstance(body, dict) else None
    detail = body.get("error") if isinstance(body, dict) else body
    if status in (401, 403):
        return (f"Origami a rejeté la clé API (HTTP {status}, {code}) — vérifie la clé "
                "`og_live_…` configurée sur ce connecteur (Origami : Settings → API keys).")
    if status == 402:
        return (f"Origami : crédits ou plan insuffisant (402, {code}) — {detail}. "
                "Recharge le compte, ou réduis la portée (enrich=False, moins de lignes).")
    if status == 404:
        return f"Origami : ressource introuvable (404, {code}) — vérifie l'id. {detail}"
    if status == 400 and code == "UNKNOWN_FIELDS":
        return (f"Origami a refusé des clés de ligne (400 UNKNOWN_FIELDS) : {detail} — "
                "les clés d'`rows` et `match_columns` sont les SLUGS des colonnes d'entrée "
                "(`origami_tables(op='columns')` → items[].slug, avec des tirets), jamais "
                f"les noms affichés. Détails : {body.get('details') if isinstance(body, dict) else ''}")
    if status == 409:
        return f"Origami : conflit (409, {code}) — {detail}"
    if status == 429:
        return (f"Origami : limite atteinte (429, {code}) — {detail}. Réessaie dans un "
                "instant (100 req/min par org ; les runs d'agent concurrents sont plafonnés "
                "par le plan).")
    if status in (500, 502, 503, 504):
        return f"Origami est momentanément indisponible (HTTP {status}) — réessaie plus tard."
    return f"Origami a refusé la requête (HTTP {status}, {code}): {detail}"


def _verify(fields: dict, config: dict | None = None) -> None:  # noqa: ARG001
    """Sonde « tester la connexion » : lister les workspaces (lecture, sans effet)."""
    from oto.tools.origami.client import OrigamiClient
    OrigamiClient(api_key=fields["key"]).list_workspaces(limit=1)


def _items(envelope: Any) -> list:
    """Les `items` d'une enveloppe liste v2 (tolérant : une liste nue passe aussi)."""
    if isinstance(envelope, dict):
        items = envelope.get("items")
        return items if isinstance(items, list) else []
    return envelope if isinstance(envelope, list) else []


def _column_slugs(columns_envelope: Any) -> tuple[set[str], set[str]]:
    """(slugs d'ENTRÉE, tous les slugs) lus d'un `GET /tables/{id}/columns`."""
    inputs, all_slugs = set(), set()
    for col in _items(columns_envelope):
        if not isinstance(col, dict) or not col.get("slug"):
            continue
        all_slugs.add(col["slug"])
        if str(col.get("kind", "input")).lower() == "input":
            inputs.add(col["slug"])
    return inputs, all_slugs


def _parse_csv_preview(csv_text: str) -> dict:
    """En-tête + N premières lignes + compte total, pour l'aperçu d'un upload."""
    reader = csv.reader(io.StringIO(csv_text))
    header = next(reader, None)
    if not header or not any(h.strip() for h in header):
        raise _bad("`csv_text` n'a pas de ligne d'en-tête : la première ligne doit porter "
                   "les noms de colonnes.")
    preview, count = [], 0
    for row in reader:
        if not any(cell.strip() for cell in row):
            continue
        count += 1
        if len(preview) < _CSV_PREVIEW_ROWS:
            preview.append(dict(zip(header, row)))
    return {"columns": header, "rows": count, "preview": preview}


def register(mcp: FastMCP) -> None:
    from oto.tools.common.errors import UpstreamHTTPError
    from oto.tools.origami.client import OrigamiClient

    connector_verify.register("origami", _verify)

    def _client() -> OrigamiClient:
        key, _ = access.resolve_api_key("origami")
        return OrigamiClient(api_key=key)

    def _run(fn):
        """Traduit un refus d'Origami en erreur d'outil actionnable."""
        try:
            return fn()
        except ValueError as e:
            raise _bad(str(e))
        except UpstreamHTTPError as e:
            raise _bad(_upstream_message(e))

    # --- workspaces ---------------------------------------------------------

    @mcp.tool()
    def origami_workspaces(
        op: Literal["list", "create"] = "list",
        name: Optional[str] = None,
        search: Optional[str] = None,
        cursor: Optional[str] = None,
        dry_run: bool = False,
    ) -> dict:
        """Origami workspaces — the containers of tables, documents and campaigns.

        `op`:
        - **"list"** (default): `{items: [{id, name, url, createdAt}], nextCursor}`.
          `search` filters by name substring; pass `nextCursor` back as `cursor`.
        - **"create"**: creates a workspace named `name` (WRITE — needed before an
          upload-first flow). `dry_run=True` validates and returns
          `{dry_run: true, would_create: {name}}` without creating.

        Args:
            op: "list" | "create".
            name: op="create" — workspace name (≤ 80 chars).
            search: op="list" — case-insensitive substring on the name.
            cursor: op="list" — `nextCursor` of the previous page.
            dry_run: op="create" — preview only, nothing written.
        """
        if op == "list":
            return _run(lambda: _client().list_workspaces(cursor=cursor, search=search))
        if op == "create":
            if not name or not name.strip():
                raise _bad("op='create' : `name` requis.")
            if len(name) > 80:
                raise _bad("op='create' : `name` ≤ 80 caractères.")
            if dry_run:
                return {"dry_run": True, "would_create": {"name": name.strip()}}
            return _run(lambda: _client().create_workspace(name.strip()))
        raise _bad(f"`op` invalide : {op!r} (attendu : list | create).")

    # --- tables -------------------------------------------------------------

    @mcp.tool()
    def origami_tables(
        op: Literal["list", "get", "columns"] = "list",
        workspace_id: Optional[str] = None,
        table_id: Optional[str] = None,
        cursor: Optional[str] = None,
        include_stats: bool = False,
    ) -> dict:
        """Origami tables (lead lists) — list them, read one, or read its columns.

        `op`:
        - **"list"** (default): every table of the org, or of `workspace_id` if given
          — `{items: [{id, workspaceId, name, leadCount, columns, credits, url}],
          nextCursor}` (50 per page; pass `nextCursor` back as `cursor`).
        - **"get"**: one table by `table_id` — name, leadCount, columns, credits
          consumed (`credits.lifetimeUsed`); `include_stats=True` adds the economics
          block (creditsPerLead, qualification, funnel).
        - **"columns"**: `{items: [{id, name, slug, kind, autoTrigger}]}` — READ THIS
          BEFORE ANY UPSERT: row keys and `match_columns` are the `slug` values
          (hyphenated, e.g. `first-name`) of `kind == "input"` columns, never the
          display names. Enrichment / score / sequence columns are not writable.

        Args:
            op: "list" | "get" | "columns".
            workspace_id: op="list" — scope to one workspace.
            table_id: op="get" / op="columns" — the table.
            cursor: op="list" — `nextCursor` of the previous page.
            include_stats: op="get" — attach the economics block.
        """
        if op == "list":
            return _run(lambda: _client().list_tables(workspace_id=workspace_id, cursor=cursor))
        if op not in ("get", "columns"):
            raise _bad(f"`op` invalide : {op!r} (attendu : list | get | columns).")
        if not table_id:
            raise _bad(f"op='{op}' : `table_id` requis.")
        if op == "get":
            return _run(lambda: _client().get_table(
                table_id, include="stats" if include_stats else None))
        return _run(lambda: _client().list_columns(table_id))

    # --- rows ---------------------------------------------------------------

    def _list_rows(c: OrigamiClient, table_id: str, cursor: Optional[str],
                   max_pages: int, limit: Optional[int]) -> dict:
        """Suit `nextCursor` côté serveur jusqu'à `max_pages` ; rend `cursor` (le
        prochain à passer) quand il reste des pages — l'agent sait qu'il n'a pas tout vu."""
        items: list = []
        total = None
        pages = 0
        next_cursor = cursor
        while pages < max_pages:
            page = c.list_rows(table_id, cursor=next_cursor, cells="flat", limit=limit)
            pages += 1
            items.extend(_items(page))
            if isinstance(page, dict) and page.get("total") is not None:
                total = page.get("total")
            next_cursor = page.get("nextCursor") if isinstance(page, dict) else None
            if not next_cursor:
                break
        return {"table_id": table_id, "count": len(items), "total": total,
                "pages_fetched": pages, "cursor": next_cursor,
                "truncated": bool(next_cursor), "items": items}

    def _upsert_rows(c: OrigamiClient, table_id: str, rows: list, match_columns: list,
                     enrich: bool, dry_run: bool) -> dict:
        # Validation IDENTIQUE avec et sans dry_run ; seul l'appel final est sauté.
        if not isinstance(rows, list) or not rows or not all(isinstance(r, dict) for r in rows):
            raise _bad("op='upsert' : `rows` = liste non vide de dicts {slug: valeur}.")
        if len(rows) > _UPSERT_MAX_ROWS:
            raise _bad(f"op='upsert' : {len(rows)} lignes > {_UPSERT_MAX_ROWS} par appel — "
                       "découpe en plusieurs appels.")
        if not match_columns or not all(isinstance(m, str) and m for m in match_columns):
            raise _bad("op='upsert' : `match_columns` requis (slugs de colonnes d'entrée, "
                       "ex. ['email']) — c'est la clé de correspondance insert/update.")
        missing = [i for i, r in enumerate(rows)
                   if any(r.get(m) in (None, "") for m in match_columns)]
        if missing:
            raise _bad(f"op='upsert' : valeur de match vide sur les lignes {missing[:20]} "
                       f"(colonnes {match_columns}) — l'API refuse (MISSING_MATCH_VALUE).")
        keys = set()
        for r in rows:
            keys |= set(r)
        # Vérification des slugs contre les colonnes RÉELLES : un slug inconnu ou une
        # colonne non-input est refusé ici (message qui nomme les slugs valides) au
        # lieu d'un 400 UNKNOWN_FIELDS amont. Dégradé explicite si la lecture échoue.
        inputs, all_slugs = _column_slugs(c.list_columns(table_id))
        check: dict = {"columns_available": bool(all_slugs)}
        if all_slugs:
            unknown = sorted(k for k in keys if k not in all_slugs)
            non_input = sorted(k for k in keys if k in all_slugs and k not in inputs)
            bad_match = sorted(m for m in match_columns if m not in inputs)
            if unknown or non_input or bad_match:
                raise _bad(
                    "op='upsert' : clés refusées — "
                    + (f"slugs inconnus {unknown} ; " if unknown else "")
                    + (f"colonnes non-input {non_input} ; " if non_input else "")
                    + (f"match_columns hors colonnes d'entrée {bad_match} ; " if bad_match else "")
                    + f"slugs d'entrée valides : {sorted(inputs)}.")
            check.update(input_slugs_used=sorted(keys), match_columns=list(match_columns))
        if dry_run:
            return {"dry_run": True, "table_id": table_id,
                    "would_upsert": {"rows": len(rows), "match_columns": list(match_columns),
                                     "enrich": bool(enrich), "keys": sorted(keys),
                                     "sample": rows[:3]},
                    "check": check}
        receipt = c.upsert_rows(table_id, rows, match_columns, enrich=enrich)
        return {"table_id": table_id, "sent": len(rows), "receipt": receipt}

    @mcp.tool()
    def origami_rows(
        op: Literal["list", "upsert"],
        table_id: str,
        cursor: Optional[str] = None,
        max_pages: int = 1,
        limit: Optional[int] = None,
        rows: Optional[list[dict]] = None,
        match_columns: Optional[list[str]] = None,
        enrich: bool = False,
        dry_run: bool = False,
    ) -> dict:
        """Rows of an Origami table — read them (flat `{slug: value}` rows, following
        the cursor) or upsert them (the ONLY write path for rows).

        `op`:
        - **"list"**: `{count, total, pages_fetched, cursor, truncated, items}`.
          Pages are 50 rows (`limit` ≤ 200); the server follows `nextCursor` up to
          `max_pages` (cap 20). `truncated: true` + `cursor` = there is more — call
          again with that `cursor`.
        - **"upsert"** (WRITE): `rows` (1–100 dicts keyed by input-column SLUG) matched
          on `match_columns` — a row whose match values all equal an existing row
          UPDATES it, otherwise it is INSERTED. Slugs come from
          `origami_tables(op="columns")` (`items[].slug`, hyphenated); only
          `kind == "input"` columns are writable. The tool reads the columns first
          and refuses unknown / non-input slugs with the list of valid ones (the API
          would answer 400 UNKNOWN_FIELDS). `dry_run=True` runs the same validation
          and returns `{dry_run: true, would_upsert: {rows, match_columns, enrich,
          keys, sample}, check}` — nothing written. The real call returns the
          `enrichment_run` receipt `{id, counts: {inserted, updated, skipped}}`.

        Args:
            op: "list" | "upsert".
            table_id: the table.
            cursor: op="list" — `cursor` returned by a previous call (resume).
            max_pages: op="list" — pages to follow server-side (default 1, cap 20).
            limit: op="list" — rows per page (default 50, max 200).
            rows: op="upsert" — the rows, `{slug: value}` (max 100 per call).
            match_columns: op="upsert" — input-column slugs used as the match key
                (e.g. ["email"]); every row must carry a non-empty value for each.
            enrich: op="upsert" — enrich freshly INSERTED rows (spends Origami
                credits). Default False (the API default is true — here you opt in).
            dry_run: op="upsert" — validate + preview, write nothing.
        """
        if not table_id:
            raise _bad("`table_id` requis.")
        if op == "list":
            if max_pages < 1:
                raise _bad("`max_pages` ≥ 1.")
            if limit is not None and not (1 <= limit <= 200):
                raise _bad("`limit` entre 1 et 200.")
            pages = min(max_pages, _ROWS_MAX_PAGES_CAP)
            return _run(lambda: _list_rows(_client(), table_id, cursor, pages, limit))
        if op == "upsert":
            return _run(lambda: _upsert_rows(_client(), table_id, rows or [],
                                             match_columns or [], enrich, dry_run))
        raise _bad(f"`op` invalide : {op!r} (attendu : list | upsert).")

    # --- upload CSV → table -------------------------------------------------

    @mcp.tool()
    def origami_upload_csv(
        workspace_id: str,
        filename: str,
        csv_text: str,
        mode: Literal["table", "append"] = "table",
        table_id: Optional[str] = None,
        dry_run: bool = False,
    ) -> dict:
        """Create an Origami TABLE from a CSV (or append CSV rows to an existing
        table) — the ingest verb `POST /workspaces/{id}/documents`, JSON with the
        file base64-encoded (never multipart). WRITE.

        `mode="table"` (default): the CSV becomes a NEW table in `workspace_id`,
        its header row = the input columns. `mode="append"`: the rows join
        `table_id` (header = input-column slugs). `dry_run=True` parses the CSV and
        returns `{dry_run: true, would_upload: {filename, mode, columns, rows,
        preview}}` (first rows) without uploading. The real call returns the per-file
        `results[]` (an entry may be `kind: "error"`); the new table then appears in
        `origami_tables(op="list", workspace_id=…)`.

        Args:
            workspace_id: target workspace (`origami_workspaces`).
            filename: name ending in `.csv` (e.g. "grossistes-fr.csv").
            csv_text: the CSV content, header row first, UTF-8.
            mode: "table" (new table, default) | "append" (into `table_id`).
            table_id: required when mode="append".
            dry_run: preview the parsed CSV, upload nothing.
        """
        if not workspace_id:
            raise _bad("`workspace_id` requis.")
        if not filename or not filename.lower().endswith(".csv"):
            raise _bad("`filename` doit se terminer par .csv (un CSV en mode table/append).")
        if not csv_text or not csv_text.strip():
            raise _bad("`csv_text` vide.")
        if mode not in ("table", "append"):
            raise _bad(f"`mode` invalide : {mode!r} (table | append).")
        if mode == "append" and not table_id:
            raise _bad("mode='append' : `table_id` requis.")
        parsed = _parse_csv_preview(csv_text)
        if parsed["rows"] == 0:
            raise _bad("`csv_text` n'a que l'en-tête : aucune ligne de données.")
        spec: dict = {"filename": filename, "mode": mode,
                      "content": base64.b64encode(csv_text.encode("utf-8")).decode("ascii")}
        if mode == "append":
            spec["tableId"] = table_id
        if dry_run:
            return {"dry_run": True, "workspace_id": workspace_id,
                    "would_upload": {"filename": filename, "mode": mode,
                                     "table_id": table_id, **parsed}}
        result = _run(lambda: _client().upload_documents(workspace_id, [spec]))
        # L'identifiant de la table créée est CE que l'appelant veut : il conditionne
        # l'appel suivant (upsert, campagne). Remonté au premier niveau plutôt que laissé
        # à `result.results[0].table.id` — mesuré : un harnais l'a raté à cette profondeur.
        # Une entrée `kind: "error"` est remontée aussi, au lieu d'un succès muet.
        first = ((result or {}).get("results") or [{}])[0] if isinstance(result, dict) else {}
        first = first if isinstance(first, dict) else {}
        created = first.get("table") if isinstance(first.get("table"), dict) else {}
        # Forme mesurée le 17/08/2026 : `results[0].table.{id,slug}` ; `tableId` à plat
        # accepté aussi, au cas où l'API le renverrait comme pour le mode append.
        new_id = created.get("id") or first.get("tableId")
        out = {"workspace_id": workspace_id, "filename": filename, "mode": mode,
               "rows_sent": parsed["rows"], "columns": parsed["columns"],
               "table_id": new_id or (table_id if mode == "append" else None),
               "table_slug": created.get("slug"), "result": result}
        if isinstance(first, dict) and first.get("kind") == "error":
            out["error"] = first.get("error") or first.get("message") or "upload refusé (kind=error)"
        return out

    # --- campaigns (lecture) ------------------------------------------------

    @mcp.tool()
    def origami_campaigns(
        op: Literal["list_for_table", "get", "stats", "people"],
        table_id: Optional[str] = None,
        campaign_id: Optional[str] = None,
        cursor: Optional[str] = None,
        status: Optional[str] = None,
        search: Optional[str] = None,
    ) -> dict:
        """Read Origami campaigns — an email + LinkedIn campaign is a set of
        sequences (one per enrolled person) that send from a table.

        `op`:
        - **"list_for_table"**: the campaigns sending from `table_id` —
          `{items: [{id, slug, name, status, peopleCount}]}`. There is NO global
          campaign list: list per table, or see every sequence of a workspace with
          `origami_sequences(workspace_id=…)`.
        - **"get"**: one campaign — `{id, name, status: draft|active|paused,
          workspaceId, tableId, channels: {email, linkedin}, settings:
          {blockPriorContacts, blockActiveDuplicates, autoTopUpEnabled}, brief}`.
        - **"stats"**: `{found, contacted, connectSent, connectAccepted,
          connectionRate, replied, replyRate, hasEmail, hasLinkedin}`.
        - **"people"**: the enrolled people — `{items: [{sequenceId, rowId, recipient,
          sendStatus, stopReason, fitScore, fitExplanation, profile, addedAt}],
          total, nextCursor}`; `status` = CSV of send-status buckets, `search` =
          substring; pass `nextCursor` back as `cursor`.

        Args:
            op: "list_for_table" | "get" | "stats" | "people".
            table_id: op="list_for_table".
            campaign_id: op="get" / "stats" / "people".
            cursor: op="people" — pagination.
            status: op="people" — CSV of send statuses to keep.
            search: op="people" — substring over recipient / identity.
        """
        if op == "list_for_table":
            if not table_id:
                raise _bad("op='list_for_table' : `table_id` requis.")
            return _run(lambda: _client().list_campaigns(table_id))
        if op not in ("get", "stats", "people"):
            raise _bad(f"`op` invalide : {op!r} (attendu : list_for_table | get | stats | people).")
        if not campaign_id:
            raise _bad(f"op='{op}' : `campaign_id` requis.")
        if op == "get":
            return _run(lambda: _client().get_campaign(campaign_id))
        if op == "stats":
            return _run(lambda: _client().campaign_stats(campaign_id))
        return _run(lambda: _client().campaign_people(
            campaign_id, cursor=cursor, status=status, search=search))

    # --- campaigns (écriture) -----------------------------------------------

    @mcp.tool()
    def origami_campaign_create(
        table_id: str,
        instructions: str,
        block_prior_contacts: bool = True,
        block_active_duplicates: bool = True,
        dry_run: bool = False,
    ) -> dict:
        """Ask Origami's agent to DRAFT a campaign on a table (WRITE — creates the
        campaign and its per-person sequences; sends NOTHING — launching is a
        separate, explicit tool).

        The call is agentic: `instructions` (1–10 000 chars: audience, channels,
        tone, offer, follow-ups…) is handed to the Origami agent, which answers
        202 `{agent: {id}, run: {id}, table}`. Poll `origami_run_get(agent_id,
        run_id)` until `status != "running"`; the drafted campaign then appears in
        `origami_campaigns(op="list_for_table", table_id=…)`. Review it (`get`,
        `people`) BEFORE `origami_campaign_launch`.

        Settings (persisted on the campaign, read back in `settings`):
        - `block_prior_contacts=True` (default): auto-cancels every person who was
          EVER enrolled in a previous campaign — INCLUDING people who sat in a
          deleted, never-sent draft. Pass False ONLY when the prior enrolments were
          drafts that never actually sent to anyone; keep True to protect real
          past recipients from a second cold approach.
        - `block_active_duplicates=True` (default): auto-cancels people who are
          currently active in another campaign.

        `dry_run=True` validates and returns `{dry_run: true, would_create: {...}}`
        without calling Origami. Errors: 402 INSUFFICIENT_CREDITS, 409 AGENT_BUSY
        (an agent run is already going on that table — poll it), 429
        CONCURRENT_LIMIT_EXCEEDED (plan cap on concurrent agent runs).

        Args:
            table_id: the table the campaign sends from.
            instructions: the brief for the Origami agent.
            block_prior_contacts: suppress anyone previously enrolled (even in
                deleted unsent drafts). Default True.
            block_active_duplicates: suppress people active in another campaign.
                Default True.
            dry_run: preview only.
        """
        if not table_id:
            raise _bad("`table_id` requis.")
        if not instructions or not instructions.strip():
            raise _bad("`instructions` requis (le brief de la campagne).")
        if len(instructions) > 10_000:
            raise _bad("`instructions` ≤ 10 000 caractères.")
        settings = {"blockPriorContacts": bool(block_prior_contacts),
                    "blockActiveDuplicates": bool(block_active_duplicates)}
        if dry_run:
            return {"dry_run": True, "table_id": table_id,
                    "would_create": {"instructions": instructions, "settings": settings},
                    "next": "origami_run_get(agent_id, run_id) puis origami_campaigns(op='list_for_table')"}
        result = _run(lambda: _client().create_campaign(table_id, instructions, settings=settings))
        agent_id = ((result or {}).get("agent") or {}).get("id")
        run_id = ((result or {}).get("run") or {}).get("id")
        return {"table_id": table_id, "agent_id": agent_id, "run_id": run_id,
                "settings": settings, "response": result,
                "next": ("poll origami_run_get(agent_id, run_id) until status != 'running', "
                         "then origami_campaigns(op='list_for_table', table_id) — nothing "
                         "is sent until origami_campaign_launch(dry_run=False)")}

    @mcp.tool()
    def origami_run_get(agent_id: str, run_id: str, include: Optional[str] = None) -> dict:
        """Poll an Origami agent run — the follow-up of `origami_campaign_create`
        (which returns `agent_id` + `run_id`).

        `GET /agents/{agent_id}/runs/{run_id}` — there is NO `GET /runs/{id}`, the
        run lives under its agent. Returns the run object: `status` ("running" until
        terminal), `steps`, `response` (tables touched, transcript with
        `include="transcript"`, economics with `include="stats"`). Poll until
        `status != "running"`, then read the drafted campaign with
        `origami_campaigns`.

        Args:
            agent_id: from `origami_campaign_create` → `agent_id`.
            run_id: from `origami_campaign_create` → `run_id`.
            include: optional CSV of "stats", "transcript".
        """
        if not agent_id or not run_id:
            raise _bad("`agent_id` et `run_id` requis (rendus par origami_campaign_create).")
        return _run(lambda: _client().get_run(agent_id, run_id, include=include))

    def _launch(c: OrigamiClient, campaign_id: str, dry_run: bool) -> dict:
        campaign = c.get_campaign(campaign_id)
        summary = {k: campaign.get(k) for k in ("id", "name", "status", "channels",
                                                 "settings", "tableId", "outOfLeads")}
        if dry_run:
            preview = c.launch_campaign(campaign_id, dry_run=True)
            return {"dry_run": True, "campaign": summary, "preview": preview,
                    "note": ("nothing was sent — pass dry_run=False to launch; that call "
                             "sends emails / LinkedIn messages to real people")}
        result = c.launch_campaign(campaign_id, dry_run=False)
        blocked = (result.get("launch") or {}).get("blocked") if isinstance(result, dict) else None
        out = {"campaign": summary, "result": result,
               "launched": result.get("launched") if isinstance(result, dict) else None}
        if blocked:
            out["blocked_missing_channels"] = blocked.get("missingChannels")
            out["note"] = ("LAUNCH BLOCKED — no connected sending account for these "
                           f"channels: {blocked.get('missingChannels')}. {blocked.get('message')} "
                           "Nothing was sent; connect the account in Origami, then relaunch.")
        return out

    @mcp.tool()
    def origami_campaign_launch(campaign_id: str, dry_run: bool = True) -> dict:
        """LAUNCH an Origami campaign — this SENDS emails and LinkedIn messages to
        the enrolled people. IRREVERSIBLE once messages leave.

        `dry_run` is **True by default**: the tool then reads the campaign (status,
        channels, settings) and asks Origami's own `?dryRun=true` preview
        (`{dryRun: true, campaignId, wouldLaunch}`) — nothing is sent. To actually
        launch you MUST pass `dry_run=False`, after reviewing the people
        (`origami_campaigns(op="people")`) and the drafted copy.

        The real launch marks the campaign `active` and runs the launch pipeline
        (sender gate, duplicate auto-cancel, per-account scheduling); it is
        idempotent on an already-active campaign. Read the result:
        `launched` = drafts scheduled; `result.launch.{scheduled, firstScheduledAt,
        missingRecipientCount, duplicateActiveCancelledCount,
        duplicatePriorCancelledCount}`. If `result.launch.blocked` is present
        (`blocked.missingChannels`, echoed as `blocked_missing_channels`), NO
        sending account is connected for those channels and NOTHING was sent —
        connect the email / LinkedIn account in Origami, then relaunch. There are
        no override knobs: send windows, daily caps and spacing are the campaign's
        own settings, set through the agent.

        Args:
            campaign_id: the campaign (`origami_campaigns(op="list_for_table")`).
            dry_run: default True = preview only. Pass False to send.
        """
        if not campaign_id:
            raise _bad("`campaign_id` requis.")
        return _run(lambda: _launch(_client(), campaign_id, dry_run))

    @mcp.tool()
    def origami_campaign_pause(campaign_id: str, dry_run: bool = False) -> dict:
        """Pause an active Origami campaign — halts further sends (idempotent:
        pausing a paused campaign is a no-op). WRITE.

        `dry_run=True` asks Origami's `?dryRun=true` → `{dryRun: true, campaignId,
        wouldPause}`, no writes. The real call returns the transition with
        `pause: {stoppedSequences, haltedSteps, inFlightSending, alreadyPaused}` —
        `inFlightSending` messages already handed to the provider still go out.
        """
        if not campaign_id:
            raise _bad("`campaign_id` requis.")
        result = _run(lambda: _client().pause_campaign(campaign_id, dry_run=dry_run))
        return {"dry_run": True, "preview": result} if dry_run else result

    @mcp.tool()
    def origami_campaign_resume(campaign_id: str, dry_run: bool = False) -> dict:
        """Resume a paused Origami campaign from where its sequences stopped
        (idempotent) — this SENDS again. WRITE.

        `dry_run=True` asks Origami's `?dryRun=true` → `{dryRun: true, campaignId,
        wouldResume}`, no writes. The real call returns the transition with
        `resume: {resumedSequences, noAccountSequences, missingChannels}` —
        `missingChannels` non-empty means those channels have no connected sending
        account and their sequences did not resume.
        """
        if not campaign_id:
            raise _bad("`campaign_id` requis.")
        result = _run(lambda: _client().resume_campaign(campaign_id, dry_run=dry_run))
        return {"dry_run": True, "preview": result} if dry_run else result

    def _delete(c: OrigamiClient, campaign_id: str, confirm: bool, dry_run: bool) -> dict:
        if not confirm or dry_run:
            # Étape 1 (ou aperçu forcé) : DELETE sans confirm = aperçu d'impact, rien
            # n'est retiré. `dryRun=true` force l'aperçu même si confirm est posé.
            preview = c.delete_campaign(campaign_id, confirm=confirm, dry_run=dry_run)
            return {"dry_run": True, "campaign_id": campaign_id, "deleted": False,
                    "preview": preview,
                    "note": ("nothing removed — pass confirm=True (and dry_run=False) to "
                             "delete; the tool then re-reads the campaign and reports "
                             "whether it is really gone")}
        # Étape 2 : suppression réelle, puis re-GET — le 2e temps peut répondre 200
        # sans supprimer ; seule un 404 prouve la disparition.
        result = c.delete_campaign(campaign_id, confirm=True)
        really_gone: Optional[bool]
        after: Any = None
        try:
            after = c.get_campaign(campaign_id)
            really_gone = False
        except UpstreamHTTPError as e:
            if e.status_code == 404:
                really_gone = True
            else:
                raise
        out = {"campaign_id": campaign_id, "result": result, "really_deleted": really_gone}
        if not really_gone:
            out["after"] = {k: (after or {}).get(k) for k in ("id", "name", "status")}
            out["note"] = ("Origami answered the delete but the campaign is STILL readable "
                           "(no 404 on re-read) — treat it as NOT deleted; check its status "
                           "and retry or delete it in the Origami UI.")
        return out

    @mcp.tool()
    def origami_campaign_delete(
        campaign_id: str,
        confirm: bool = False,
        dry_run: bool = False,
    ) -> dict:
        """Delete an Origami campaign — TWO-STEP, and verified. WRITE.

        Step 1 (`confirm=False`, default): Origami returns the impact preview
        `{id, name, confirmationRequired: true, status}`; nothing is removed.
        Step 2 (`confirm=True`, `dry_run=False`): soft-deletes the campaign
        (its picker halts instantly) and cancels its orphaned sequences. Because
        the second step can answer 200 WITHOUT deleting, the tool re-reads the
        campaign afterwards: `really_deleted: true` only if the re-read is a 404;
        otherwise `really_deleted: false` + the current status — do not report it
        as deleted. `dry_run=True` forces the preview even with `confirm=True`.

        Args:
            campaign_id: the campaign.
            confirm: True to actually delete (step 2).
            dry_run: preview only, even if confirm=True.
        """
        if not campaign_id:
            raise _bad("`campaign_id` requis.")
        return _run(lambda: _delete(_client(), campaign_id, confirm, dry_run))

    # --- sequences ----------------------------------------------------------

    def _list_sequences(c: OrigamiClient, workspace_id: str, cursor: Optional[str],
                        max_pages: int, status: Optional[str], channel: Optional[str],
                        recipient: Optional[str]) -> dict:
        """Suit `nextCursor` côté serveur jusqu'à `max_pages` (50 séquences/page), comme
        `_list_rows`. Rend `cursor` quand il reste des pages, `truncated: true` — l'agent
        sait qu'il n'a pas tout vu. Ajoute `campaign_ids`, la liste DISTINCTE des
        campagnes rencontrées : c'est le seul moyen d'énumérer les campagnes d'un
        workspace, et une première page seule en fait croire une là où il y en a quatre
        (mesuré le 17/08/2026 : 50 séquences / 1 campagne sur une page, 369 / 4 en tout)."""
        items: list = []
        pages = 0
        next_cursor = cursor
        while pages < max_pages:
            page = c.list_sequences(workspace_id, cursor=next_cursor, status=status,
                                    channel=channel, recipient=recipient)
            pages += 1
            items.extend(_items(page))
            next_cursor = page.get("nextCursor") if isinstance(page, dict) else None
            if not next_cursor:
                break
        campaign_ids = sorted({s.get("campaignId") for s in items
                               if isinstance(s, dict) and s.get("campaignId")})
        return {"workspace_id": workspace_id, "count": len(items),
                "campaign_ids": campaign_ids, "pages_fetched": pages,
                "cursor": next_cursor, "truncated": bool(next_cursor), "items": items}

    @mcp.tool()
    def origami_sequences(
        workspace_id: Optional[str] = None,
        sequence_id: Optional[str] = None,
        cursor: Optional[str] = None,
        max_pages: int = 10,
        status: Optional[str] = None,
        channel: Optional[str] = None,
        recipient: Optional[str] = None,
    ) -> dict:
        """Origami sequences — one sequence = one enrolled person in a campaign.

        Pass exactly one of:
        - `workspace_id` → `GET /sequences?workspaceId=`: EVERY sequence of the
          workspace, each with its `campaignId`, `status`, `sendStatus`,
          `stopReason`, `tableId`, `rowId` — the only view that sees all campaigns
          of a workspace at once (there is no global campaign list). Pages are 50;
          the tool follows `nextCursor` server-side up to `max_pages` (default 10 =
          500 sequences) and returns `campaign_ids`, the DISTINCT campaigns seen,
          plus `truncated`/`cursor` when more remain — pass `cursor` back to
          continue. Filters `status` / `channel` (email|linkedin) / `recipient`.
        - `sequence_id` → the sequence with its steps inline (message copy per
          step; provider internals redacted).

        Args:
            workspace_id: list mode — the workspace.
            sequence_id: get mode — one sequence.
            cursor: list mode — resume from a previous `cursor`.
            max_pages: list mode — pages of 50 to fetch server-side (default 10).
            status: list mode — filter on sequence status.
            channel: list mode — "email" | "linkedin".
            recipient: list mode — filter on recipient.
        """
        if bool(workspace_id) == bool(sequence_id):
            raise _bad("Passe exactement un de `workspace_id` (liste) ou `sequence_id` (détail).")
        if sequence_id:
            return _run(lambda: _client().get_sequence(sequence_id))
        if max_pages < 1:
            raise _bad("`max_pages` doit être ≥ 1.")
        return _run(lambda: _list_sequences(_client(), workspace_id, cursor, max_pages,
                                            status, channel, recipient))
