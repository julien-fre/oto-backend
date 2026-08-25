"""Airtable — bases, tables, champs, lignes, commentaires, pièces jointes, sync CSV.

Couvre TOUTE la section « Base data » de la Web API Airtable (records CRUD + upsert,
commentaires CRUD, upload de pièce jointe, sync CSV) et les compagnons sans lesquels un
agent ne peut rien faire : schéma d'une base (tables + champs), création/renommage de
tables et de champs, liste des bases accordées, identité du token.

Clé résolue par appel via `access.resolve_api_key("airtable")` — **BYO only** (user ou
org) : un PAT Airtable est attaché à des bases nommément accordées dans un workspace
donné, une clé plateforme partagée n'aurait aucun sens (elle exposerait les bases
d'Otomata à toutes les orgs).

**Surface consolidée (ADR 0047 §Amendement)** : un tool par OBJET, le verbe en paramètre
`op` — 22 endpoints → 7 tools. La découpe suit l'homogénéité des PARAMÈTRES :

- `airtable_record` (8 → 1) — tout est keyé par `base_id` + `table` ; l'upsert n'est
  qu'un `performUpsert` sur le même PATCH que l'update, donc pas un tool à part.
- `airtable_comment` (4 → 1) — ancrage plus profond (`+ record_id`, `+ comment_id`) et
  `text`/`parent_comment_id` ne recouvrent aucun paramètre de record.
- `airtable_table` (3 → 1) — le CONTENEUR : `name`, `description`, `fields[]`.
- `airtable_field` (3 → 1) — reste séparé de `airtable_table` pour la même raison
  qu'`attio_object` / `attio_attribute` : `type` + `options` (une forme d'objet PAR type
  de champ Airtable) n'existent nulle part ailleurs, les fusionner ferait porter à
  `name`/`description` deux sens selon l'op. `op="list"` = le schéma de base filtré sur
  la table : une vraie lecture, qui donne les `fld…` stables.
- `airtable_base` (3 → 1) — le seul tool dont l'entrée n'est pas keyée par une base.
- `airtable_attachment` (1) et `airtable_sync` (1) restent seuls : autre HÔTE
  (`content.airtable.com`, corps base64) pour l'un, corps **`text/csv` brut** et limites
  de débit propres (20 req/5 min) pour l'autre. Mettre un chemin non-JSON dans
  `airtable_record` polluerait sa signature pour tous les autres verbes.

⚠️ Ce module ÉCRIT dans une base Airtable RÉELLE. Le défaut de chaque tool à `op` est une
LECTURE (`"list"` ou `"schema"`) : un appel sans `op` ne peut ni écrire ni supprimer, et
une op inconnue est refusée AVANT même la résolution de la clé. Les deux tools qui n'ont
AUCUNE lecture possible (`airtable_attachment`, `airtable_sync`) n'ont délibérément pas
de paramètre `op` : un verbe unique n'a pas de verbe à choisir, et leurs paramètres de
charge utile (le fichier, le CSV) sont tous obligatoires — aucun appel nu ne peut muter.

⚠️ **`typecast` vaut `False` par défaut, et c'est un choix.** Chez Airtable ce n'est pas
une conversion de confort : c'est une **mutation de schéma déclenchée par une écriture de
donnée** (il crée l'option manquante d'un select, voire un enregistrement dans la table
liée d'un champ *linked record*), et il ne demande que le scope `data.records:write`. Un
`op="create"` avec une valeur mal orthographiée élargirait donc en silence le schéma
d'une base client. Ici l'écriture échoue franchement ; `typecast=True` est un geste
explicite de l'appelant.

⚠️ **Lots et débit.** L'API refuse plus de **10 records par requête** (create / update /
delete) et plafonne à **5 requêtes/seconde par base** ; un 429 impose **30 secondes**
d'attente. Le client oto-core ne boucle pas : c'est ce module qui découpe en lots de 10,
espace les requêtes de 200 ms et plafonne à `_MAX_ITEMS` records par appel — plafond
DÉRIVÉ du budget d'invoke de 45 s (`api_routes.py`), pas deviné. Sur 429 on **n'attend
pas** les 30 s (l'appel mourrait en timeout sans dire ce qui a été écrit) : on s'arrête et
on rend un reçu partiel qui NOMME ce qui est passé et ce qui ne l'est pas.
"""
from __future__ import annotations

import time
from typing import Any, Literal, Optional, get_args

from fastmcp import FastMCP
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData, INVALID_PARAMS

from .. import access, connector_verify

# Ops de chaque objet, lectures → écritures. Source unique : le SCHÉMA MCP
# (`Literal` → `enum` JSON), la validation d'entrée ET le message de refus en dérivent.
_RecordOp = Literal["list", "get", "create", "update", "upsert", "delete"]
_CommentOp = Literal["list", "create", "update", "delete"]
_TableOp = Literal["schema", "create", "update"]
_FieldOp = Literal["list", "create", "update"]
_BaseOp = Literal["list", "whoami", "create"]

_RECORD_OPS = get_args(_RecordOp)
_COMMENT_OPS = get_args(_CommentOp)
_TABLE_OPS = get_args(_TableOp)
_FIELD_OPS = get_args(_FieldOp)
_BASE_OPS = get_args(_BaseOp)

# Plafond DUR d'Airtable sur create/update/delete multiples. Recopié ici plutôt que
# lu sur `AirtableClient` : le module ne doit pas dépendre d'un attribut de classe pour
# une valeur qui gouverne le découpage. `test_batch_size_matches_the_core_client` casse
# si oto-core change d'avis.
_BATCH_SIZE = 10
# Plafond d'items par appel, DÉRIVÉ du budget d'invoke (45 s, `api_routes.py`) :
# 200 records = 20 requêtes de 10 × (200 ms de courtoisie + ~300 ms de latence) ≈ 10 s.
_MAX_ITEMS = 200
# 5 requêtes/seconde par base côté Airtable → 200 ms entre deux requêtes.
_RATE_DELAY = 0.2
# Plafond de pages lues en une fois — même raison (une base peut avoir 100 000 lignes).
_MAX_PAGES = 25
# Au-delà, la `filterByFormula` ne tient plus dans une query string : Airtable expose
# `POST …/listRecords`, qui prend les MÊMES critères dans le corps. Le basculement est
# automatique et déterministe — un agent n'a pas à connaître cette limite d'URL.
_FORMULA_URL_LIMIT = 8000


def _bad(msg: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=msg))


def _one_of(name: str, values: tuple[str, ...]) -> str:
    """Message de refus DÉRIVÉ des valeurs admises — une op ajoutée s'annonce seule."""
    quoted = [f"'{v}'" for v in values]
    return f"{name} doit être " + ", ".join(quoted[:-1]) + " ou " + quoted[-1]


def _need(value, name: str, op: str):
    """Argument obligatoire pour CET op. Une valeur VIDE compte comme absente : un
    `fields={}` sur `op='update'` serait un PATCH qui ne change rien et passerait pour
    un succès."""
    if value is None or (isinstance(value, (str, list, dict)) and not value):
        raise _bad(f"op='{op}' requiert {name}")
    return value


def _exactly_one(solo, plural, solo_name: str, plural_name: str, op: str):
    """Paire singulier/pluriel mutuellement exclusive — jamais de « liste de 1 » à
    interpréter. Le singulier rend le résultat direct, le pluriel un reçu de lot."""
    if (solo is None) == (plural is None):
        raise _bad(
            f"op='{op}' requiert EXACTEMENT un de `{solo_name}` (un seul) ou "
            f"`{plural_name}` (plusieurs), pas les deux ni aucun."
        )


def _upstream_message(e) -> str:
    """Traduit un refus Airtable en message actionnable — le code seul ne dit rien."""
    hints = {
        401: "token Airtable invalide ou révoqué (PAT `pat…`, airtable.com/create/tokens).",
        403: "le token n'a pas le scope requis, OU cette base ne lui a pas été accordée "
             "— les deux se règlent dans les réglages du PAT.",
        404: "base, table, champ ou record introuvable — vérifier `base_id` (app…), le "
             "nom/id de table, et que la base est bien accordée au token.",
        422: "Airtable a refusé la donnée : nom de champ inconnu, type incompatible, ou "
             "option de select absente (dans ce dernier cas, `typecast=True` la créerait).",
        429: "limite de débit Airtable atteinte (5 req/s par base) — Airtable exige "
             "30 secondes avant de réessayer.",
    }
    hint = hints.get(getattr(e, "status_code", None), "")
    return f"{e}" + (f" — {hint}" if hint else "")


def _verify(fields: dict, config: dict | None = None) -> None:  # noqa: ARG001
    """Sonde « tester la connexion » — DEUX étages, parce que l'auth seule ne prouve rien.

    Le mode d'échec dominant d'Airtable n'est pas un mauvais token : c'est un PAT valide,
    tous scopes cochés, auquel on a oublié d'accorder la moindre base. `GET /meta/bases`
    répond alors **200 avec une liste vide**, pas une erreur — une sonde qui s'arrête à
    l'auth validerait donc un credential incapable de lire quoi que ce soit.
    """
    from oto.tools.airtable.client import AirtableClient

    client = AirtableClient(api_key=fields["key"])
    client.whoami()  # auth
    bases = (client.list_bases() or {}).get("bases") or []  # scope + octroi
    if not bases:
        raise RuntimeError(
            "token Airtable valide, mais AUCUNE base ne lui est accordée : ouvrir "
            "airtable.com/create/tokens, éditer le token et ajouter la ou les bases "
            "dans « Access » (les scopes seuls ne suffisent pas)."
        )


def _chunks(items: list, size: int) -> list[list]:
    return [items[i:i + size] for i in range(0, len(items), size)]


def _check_items(items: list, name: str) -> list:
    if not isinstance(items, list):
        raise _bad(f"`{name}` doit être une liste.")
    if len(items) > _MAX_ITEMS:
        raise _bad(
            f"`{name}` : {len(items)} items pour un maximum de {_MAX_ITEMS} par appel "
            f"(Airtable plafonne à 10 par requête et 5 requêtes/s par base ; au-delà "
            f"l'appel dépasserait son budget de temps). Découper l'appel."
        )
    return items


def _norm_records(records: list, *, need_id: bool, op: str) -> list[dict]:
    """Normalise vers la forme Airtable `{"id"?: …, "fields": {…}}`.

    Un item SANS clé `fields` est pris pour la carte de champs elle-même
    (`{"Name": "Ada"}` ⟹ `{"fields": {"Name": "Ada"}}`) : c'est la forme qu'un appelant
    écrit spontanément. Corollaire assumé : une table dont une colonne s'appellerait
    littéralement « fields » doit utiliser la forme explicite.
    """
    out: list[dict] = []
    for i, item in enumerate(records):
        if not isinstance(item, dict):
            raise _bad(f"`records[{i}]` doit être un objet, reçu {type(item).__name__}.")
        if "fields" in item and isinstance(item["fields"], dict):
            rec = {"fields": item["fields"]}
            if item.get("id"):
                rec["id"] = item["id"]
        else:
            rec = {"fields": {k: v for k, v in item.items() if k != "id"}}
            if item.get("id"):
                rec["id"] = item["id"]
        if not rec["fields"]:
            raise _bad(f"`records[{i}]` n'a aucun champ à écrire.")
        if need_id and "id" not in rec:
            raise _bad(
                f"op='{op}' : `records[{i}]` n'a pas d'`id`. Pour rapprocher des lignes "
                f"sur une valeur métier plutôt que sur leur id, utiliser op='upsert'."
            )
        out.append(rec)
    return out


def register(mcp: FastMCP) -> None:
    from oto.tools.common.errors import UpstreamHTTPError
    from oto.tools.airtable.client import AirtableClient

    connector_verify.register("airtable", _verify)

    def _client() -> AirtableClient:
        key, _ = access.resolve_api_key("airtable")
        return AirtableClient(api_key=key)

    def _run(fn):
        """Traduit un refus d'Airtable (ou une garde du client) en erreur actionnable."""
        try:
            return fn()
        except ValueError as e:
            raise _bad(str(e))
        except UpstreamHTTPError as e:
            raise _bad(_upstream_message(e))

    def _batched(items: list, call, *, key: str) -> dict:
        """Découpe en lots de 10, espace les requêtes, rend un reçu HONNÊTE.

        `call(chunk)` fait UNE requête. Trois régimes d'échec, délibérément distincts :
        - **401/403** → la clé est mauvaise pour toute la suite : on lève tout de suite
          plutôt que de répéter N fois le même refus.
        - **429** → on s'ARRÊTE (Airtable veut 30 s, le budget d'invoke est de 45 s) et
          on rend le reçu partiel : l'appelant sait exactement où reprendre.
        - **autre 4xx** → l'échec est propre à ce lot : on l'enregistre et on continue.
        """
        done: list = []
        failed: list[dict] = []
        aborted: Optional[str] = None
        chunks = _chunks(items, _BATCH_SIZE)
        for n, chunk in enumerate(chunks):
            if n:
                time.sleep(_RATE_DELAY)
            first = n * _BATCH_SIZE
            try:
                result = call(chunk) or {}
            except UpstreamHTTPError as e:
                if e.status_code in (401, 403):
                    raise _bad(_upstream_message(e))
                if e.status_code == 429:
                    aborted = "rate_limit"
                    failed.append({"items": [first, first + len(chunk) - 1],
                                   "error": _upstream_message(e)})
                    break
                failed.append({"items": [first, first + len(chunk) - 1],
                               "error": _upstream_message(e)})
                continue
            except ValueError as e:
                raise _bad(str(e))
            done.extend(result.get(key) or [])
        receipt: dict[str, Any] = {
            "total": len(items),
            "succeeded": len(done),
            "failed": failed,
            key: done,
        }
        if aborted:
            receipt["aborted"] = aborted
            receipt["resume_hint"] = (
                f"{len(done)} item(s) traités avant la limite de débit. Attendre 30 s et "
                f"relancer avec les items restants."
            )
        return receipt

    def _paginate(fetch, key: str, limit: int) -> dict:
        """Suit l'`offset` opaque d'Airtable jusqu'à `limit` items ou `_MAX_PAGES`.

        Rend TOUJOURS l'`offset` restant s'il en reste : une liste tronquée le DIT, elle
        ne fait pas croire qu'on a tout lu.
        """
        items: list = []
        offset: Optional[str] = None
        pages = 0
        while True:
            page = fetch(offset) or {}
            items.extend(page.get(key) or [])
            offset = page.get("offset")
            pages += 1
            if not offset or len(items) >= limit or pages >= _MAX_PAGES:
                break
            time.sleep(_RATE_DELAY)
        out: dict[str, Any] = {key: items[:limit], "count": min(len(items), limit)}
        if offset or len(items) > limit:
            out["offset"] = offset
            out["more"] = True
        return out

    def _tables_of(client: AirtableClient, base_id: str) -> list[dict]:
        return (client.get_base_schema(base_id) or {}).get("tables") or []

    # ==================================================================
    # Records
    # ==================================================================

    @mcp.tool()
    def airtable_record(
        base_id: str,
        table: str,
        op: _RecordOp = "list",
        record_id: Optional[str] = None,
        record_ids: Optional[list[str]] = None,
        fields: Optional[dict] = None,
        records: Optional[list[dict]] = None,
        select_fields: Optional[list[str]] = None,
        filter_by_formula: Optional[str] = None,
        view: Optional[str] = None,
        sort: Optional[list[dict]] = None,
        max_records: int = 100,
        merge_on: Optional[list[str]] = None,
        typecast: bool = False,
        replace: bool = False,
        cell_format: Optional[str] = None,
        time_zone: Optional[str] = None,
        user_locale: Optional[str] = None,
        return_fields_by_field_id: bool = False,
    ) -> dict:
        """A row in an Airtable table: list, read, create, update, upsert, delete.

        `base_id` is the base (`appXXXXXXXX`, from `airtable_base`), `table` is a table
        name or id (`tblXXXXXXXX`, from `airtable_table`). Ids are the stable path —
        names break silently when someone renames a column or a table.

        `op`:
        - **"list"** (default): rows of the table, newest page first. Narrow with
          `filter_by_formula`, `view`, `sort`, `select_fields`. Follows Airtable's
          pagination up to `max_records`; if rows remain, the reply carries
          `more: true` and the `offset` to resume from.
        - **"get"**: one row by `record_id`.
        - **"create"** — ⚠️ WRITES: one row (`fields`) or many (`records`, up to 200).
        - **"update"** — ⚠️ WRITES: one row (`record_id` + `fields`) or many (`records`,
          each carrying its `id`). PATCH by default: untouched fields keep their value.
        - **"upsert"** — ⚠️ WRITES: `records` matched against existing rows on
          `merge_on` (1-3 field names) — matched rows are updated, unmatched ones
          created. The reply separates `createdRecords` from `updatedRecords`.
        - **"delete"** — ⚠️ WRITES: one row (`record_id`) or many (`record_ids`).
          Irreversible.

        Writes go 10 rows per request with a courtesy delay, capped at 200 rows per
        call. If Airtable rate-limits mid-batch the call stops and returns a receipt
        naming what was written — it never silently half-succeeds.

        Args:
            base_id: the base, `appXXXXXXXX`.
            table: table name or id (`tblXXXXXXXX` — the stable one).
            op: list (default) | get | create | update | upsert | delete.
            record_id: op="get"/"update"/"delete" — a single row `recXXXXXXXX`.
            record_ids: op="delete" — several rows at once.
            fields: op="create"/"update" — one row's cells, `{"Name": "Ada", "Age": 36}`.
            records: op="create"/"update"/"upsert" — several rows. Either plain cell
                maps (`[{"Name": "Ada"}]`) or the explicit Airtable shape
                (`[{"id": "rec…", "fields": {…}}]`); `id` is required for "update".
            select_fields: op="list"/"get" — only return these columns. The first lever
                against a huge reply.
            filter_by_formula: op="list" — Airtable formula evaluated per row, e.g.
                `{Status}='Done'` or `AND({Score}>10, {Owner}='Ada')`. A very long
                formula switches to Airtable's POST form on its own.
            view: op="list" — restrict to a view's rows and order.
            sort: op="list" — `[{"field": "Name", "direction": "asc"|"desc"}]`.
            max_records: op="list" — how many rows to return (default 100).
            merge_on: op="upsert" — 1 to 3 field names used to match existing rows.
            typecast: op="create"/"update"/"upsert" — let Airtable coerce values AND
                create missing select options / linked records. Off by default: it
                mutates the base's schema from a data write.
            replace: op="update" — ⚠️ PUT instead of PATCH: every field NOT sent is
                CLEARED. Off by default.
            cell_format: op="list"/"get" — "json" (default) or "string" (formatted as
                the UI shows them; then `time_zone` and `user_locale` are required).
            time_zone: with cell_format="string", e.g. "Europe/Paris".
            user_locale: with cell_format="string", e.g. "fr".
            return_fields_by_field_id: key returned cells by `fld…` id instead of name.
        """
        # Refus AVANT toute résolution de credential : une op inconnue n'atteint jamais
        # le client, donc jamais, par un chemin dérivé, une écriture sur la base.
        if op not in _RECORD_OPS:
            raise _bad(_one_of("op", _RECORD_OPS))
        client = _client()

        if op == "list":
            long_formula = len(filter_by_formula or "") > _FORMULA_URL_LIMIT

            def _page(off):
                if long_formula:
                    # Mêmes critères, dans le CORPS : une formule de cette taille ferait
                    # dépasser la longueur d'URL admise et Airtable la rejetterait.
                    body = {k: v for k, v in {
                        "fields": select_fields,
                        "filterByFormula": filter_by_formula,
                        "view": view,
                        "sort": sort,
                        "pageSize": min(100, max_records),
                        "cellFormat": cell_format,
                        "timeZone": time_zone,
                        "userLocale": user_locale,
                        "returnFieldsByFieldId": return_fields_by_field_id or None,
                        "offset": off,
                    }.items() if v is not None}
                    return client.list_records_post(base_id, table, body)
                return client.list_records(
                    base_id, table,
                    fields=select_fields,
                    filter_by_formula=filter_by_formula,
                    view=view,
                    sort=sort,
                    page_size=min(100, max_records),
                    cell_format=cell_format,
                    time_zone=time_zone,
                    user_locale=user_locale,
                    return_fields_by_field_id=return_fields_by_field_id or None,
                    offset=off,
                )

            return _run(lambda: _paginate(_page, "records", max_records))

        if op == "get":
            return _run(lambda: client.get_record(
                base_id, table, _need(record_id, "record_id", op),
                cell_format=cell_format, time_zone=time_zone, user_locale=user_locale,
                return_fields_by_field_id=return_fields_by_field_id or None,
            ))

        if op == "create":
            _exactly_one(fields, records, "fields", "records", op)
            if fields is not None:
                return _run(lambda: client.create_records(
                    base_id, table, [{"fields": fields}], typecast=typecast or None,
                    return_fields_by_field_id=return_fields_by_field_id or None,
                ))
            items = _norm_records(
                _check_items(records, "records"), need_id=False, op=op)
            return _batched(items, lambda chunk: client.create_records(
                base_id, table, chunk, typecast=typecast or None,
                return_fields_by_field_id=return_fields_by_field_id or None,
            ), key="records")

        if op == "update":
            _exactly_one(record_id, records, "record_id", "records", op)
            if record_id is not None:
                return _run(lambda: client.update_record(
                    base_id, table, record_id, _need(fields, "fields", op),
                    replace=replace, typecast=typecast or None,
                    return_fields_by_field_id=return_fields_by_field_id or None,
                ))
            items = _norm_records(
                _check_items(records, "records"), need_id=True, op=op)
            return _batched(items, lambda chunk: client.update_records(
                base_id, table, chunk, replace=replace, typecast=typecast or None,
                return_fields_by_field_id=return_fields_by_field_id or None,
            ), key="records")

        if op == "upsert":
            merge = _need(merge_on, "merge_on (1 à 3 noms de champs)", op)
            if not isinstance(merge, list) or not 1 <= len(merge) <= 3:
                raise _bad("`merge_on` doit être une liste de 1 à 3 noms de champs.")
            items = _norm_records(
                _check_items(_need(records, "records", op), "records"),
                need_id=False, op=op)
            upsert = {"fieldsToMergeOn": merge}
            receipt = _batched(items, lambda chunk: client.update_records(
                base_id, table, chunk, replace=replace, typecast=typecast or None,
                perform_upsert=upsert,
                return_fields_by_field_id=return_fields_by_field_id or None,
            ), key="records")
            receipt["merged_on"] = merge
            return receipt

        # delete
        _exactly_one(record_id, record_ids, "record_id", "record_ids", op)
        if record_id is not None:
            return _run(lambda: client.delete_record(base_id, table, record_id))
        ids = _check_items(record_ids, "record_ids")
        return _batched(ids, lambda chunk: client.delete_records(base_id, table, chunk),
                        key="records")

    # ==================================================================
    # Commentaires
    # ==================================================================

    @mcp.tool()
    def airtable_comment(
        base_id: str,
        table: str,
        record_id: str,
        op: _CommentOp = "list",
        comment_id: Optional[str] = None,
        text: Optional[str] = None,
        parent_comment_id: Optional[str] = None,
        max_comments: int = 100,
    ) -> dict:
        """A comment on an Airtable row: list, create, update, delete.

        Comments live on a single row, so `base_id` + `table` + `record_id` are always
        required.

        `op`:
        - **"list"** (default): comments newest first, with author, reactions and
          `parentCommentId` for threaded replies.
        - **"create"** — ⚠️ WRITES: post `text`. Mention someone with `@[usrXXXXXXX]`;
          `parent_comment_id` replies inside an existing thread.
        - **"update"** — ⚠️ WRITES: rewrite a comment's `text`. A token can only edit
          comments written by its own user.
        - **"delete"** — ⚠️ WRITES: remove a comment. Deleting a thread's first comment
          deletes the whole thread.

        Args:
            base_id: the base, `appXXXXXXXX`.
            table: table name or id.
            record_id: the row the comments hang off, `recXXXXXXXX`.
            op: list (default) | create | update | delete.
            comment_id: op="update"/"delete" — `comXXXXXXXX`.
            text: op="create"/"update" — the comment body.
            parent_comment_id: op="create" — reply inside that thread.
            max_comments: op="list" — how many to return (default 100).
        """
        if op not in _COMMENT_OPS:
            raise _bad(_one_of("op", _COMMENT_OPS))
        client = _client()

        if op == "list":
            return _run(lambda: _paginate(
                lambda off: client.list_comments(
                    base_id, table, record_id,
                    page_size=min(100, max_comments), offset=off,
                ),
                "comments", max_comments,
            ))
        if op == "create":
            return _run(lambda: client.create_comment(
                base_id, table, record_id, _need(text, "text", op),
                parent_comment_id=parent_comment_id,
            ))
        if op == "update":
            return _run(lambda: client.update_comment(
                base_id, table, record_id,
                _need(comment_id, "comment_id", op), _need(text, "text", op),
            ))
        return _run(lambda: client.delete_comment(
            base_id, table, record_id, _need(comment_id, "comment_id", op)))

    # ==================================================================
    # Tables
    # ==================================================================

    @mcp.tool()
    def airtable_table(
        base_id: str,
        op: _TableOp = "schema",
        table_id: Optional[str] = None,
        name: Optional[str] = None,
        description: Optional[str] = None,
        fields: Optional[list[dict]] = None,
    ) -> dict:
        """A table inside a base: read the schema, create a table, rename one.

        `op`:
        - **"schema"** (default): every table of the base with its fields (id, name,
          type, options) and views. Pass `table_id` to narrow to one table. This is the
          only way to discover field names and types before writing rows — there is no
          "get one table" endpoint in the API.
        - **"create"** — ⚠️ WRITES: a new table from `name` + `fields`. The FIRST field
          becomes the primary field and must be a type allowed as primary (text, number,
          date, formula — not attachment or checkbox).
        - **"update"** — ⚠️ WRITES: rename / redescribe a table. Structure changes go
          through `airtable_field`.

        Args:
            base_id: the base, `appXXXXXXXX`.
            op: schema (default) | create | update.
            table_id: op="schema" — narrow to one table; op="update" — which table.
            name: op="create"/"update" — the table name.
            description: op="create"/"update" — up to 20 000 characters.
            fields: op="create" — `[{"name": …, "type": …, "options": {…}}]`. See
                `airtable_field` for the per-type `options` shapes.
        """
        if op not in _TABLE_OPS:
            raise _bad(_one_of("op", _TABLE_OPS))
        client = _client()

        if op == "schema":
            tables = _run(lambda: _tables_of(client, base_id))
            if table_id:
                tables = [t for t in tables
                          if t.get("id") == table_id or t.get("name") == table_id]
                if not tables:
                    raise _bad(
                        f"aucune table `{table_id}` dans la base {base_id} — appeler "
                        f"op='schema' sans `table_id` pour voir celles qui existent."
                    )
            return {"tables": tables, "count": len(tables)}

        if op == "create":
            return _run(lambda: client.create_table(
                base_id, _need(name, "name", op), _need(fields, "fields", op),
                description=description,
            ))

        if name is None and description is None:
            raise _bad("op='update' requiert `name` et/ou `description`")
        return _run(lambda: client.update_table(
            base_id, _need(table_id, "table_id", op),
            name=name, description=description,
        ))

    # ==================================================================
    # Champs
    # ==================================================================

    @mcp.tool()
    def airtable_field(
        base_id: str,
        table_id: str,
        op: _FieldOp = "list",
        field_id: Optional[str] = None,
        name: Optional[str] = None,
        type: Optional[str] = None,
        description: Optional[str] = None,
        options: Optional[dict] = None,
    ) -> dict:
        """A column of a table: list the columns, add one, rename one.

        `op`:
        - **"list"** (default): the table's fields — `fld…` id, name, type and options.
          Read this before writing rows: it is what tells you the exact column names and
          which select options already exist.
        - **"create"** — ⚠️ WRITES: add a column. `options` is required by most types
          and its shape depends on `type`.
        - **"update"** — ⚠️ WRITES: rename / redescribe a column. Airtable does NOT let
          the API change an existing field's `type` or `options` — create a new field.

        Common `type` values and the `options` they need:
        `singleLineText`, `multilineText`, `email`, `url`, `phoneNumber` (no options) ·
        `number` `{"precision": 0}` · `percent`, `currency` `{"precision": 2,
        "symbol": "€"}` · `checkbox` `{"color": "greenBright", "icon": "check"}` ·
        `singleSelect`, `multipleSelects` `{"choices": [{"name": "Done"}]}` ·
        `date` `{"dateFormat": {"name": "iso"}}` · `dateTime` `{"dateFormat":
        {"name": "iso"}, "timeFormat": {"name": "24hour"}, "timeZone": "Europe/Paris"}` ·
        `multipleRecordLinks` `{"linkedTableId": "tbl…"}` · `multipleAttachments`,
        `rating` `{"max": 5, "icon": "star", "color": "yellowBright"}`.

        Args:
            base_id: the base, `appXXXXXXXX`.
            table_id: the table, `tblXXXXXXXX` (a name works too).
            op: list (default) | create | update.
            field_id: op="update" — `fldXXXXXXXX`.
            name: op="create"/"update" — the column name.
            type: op="create" — one of the Airtable field types above.
            description: op="create"/"update" — up to 20 000 characters.
            options: op="create" — the type-specific configuration above.
        """
        if op not in _FIELD_OPS:
            raise _bad(_one_of("op", _FIELD_OPS))
        client = _client()

        if op == "list":
            tables = _run(lambda: _tables_of(client, base_id))
            match = next((t for t in tables
                          if t.get("id") == table_id or t.get("name") == table_id), None)
            if match is None:
                known = ", ".join(f"{t.get('name')} ({t.get('id')})" for t in tables[:20])
                raise _bad(
                    f"aucune table `{table_id}` dans la base {base_id}. "
                    f"Tables existantes : {known or '(aucune)'}"
                )
            fields = match.get("fields") or []
            return {
                "table": {"id": match.get("id"), "name": match.get("name"),
                          "primaryFieldId": match.get("primaryFieldId")},
                "fields": fields,
                "count": len(fields),
            }

        if op == "create":
            return _run(lambda: client.create_field(
                base_id, table_id, _need(name, "name", op), _need(type, "type", op),
                description=description, options=options,
            ))

        if name is None and description is None:
            raise _bad(
                "op='update' requiert `name` et/ou `description` — l'API Airtable ne "
                "permet PAS de changer le `type` ni les `options` d'un champ existant."
            )
        return _run(lambda: client.update_field(
            base_id, table_id, _need(field_id, "field_id", op),
            name=name, description=description,
        ))

    # ==================================================================
    # Bases et identité du token
    # ==================================================================

    @mcp.tool()
    def airtable_base(
        op: _BaseOp = "list",
        max_bases: int = 200,
        name: Optional[str] = None,
        workspace_id: Optional[str] = None,
        tables: Optional[list[dict]] = None,
    ) -> dict:
        """The Airtable bases this token can reach, and who the token is.

        `op`:
        - **"list"** (default): every base granted to the token, with its
          `permissionLevel`. Start here — a `base_id` (`appXXXXXXXX`) is what every
          other Airtable tool needs. An EMPTY list means the token is valid but no base
          was granted to it (fix that in the token's settings, not in the scopes).
        - **"whoami"**: the user behind the token, and its scopes. Says nothing about
          which bases are reachable — that is "list".
        - **"create"** — ⚠️ WRITES: a new base in a workspace (`wspXXXXXXXX`) with at
          least one table. Airtable has no delete-base endpoint: this cannot be undone
          from the API.

        Args:
            op: list (default) | whoami | create.
            max_bases: op="list" — how many bases to return (default 200).
            name: op="create" — the base name.
            workspace_id: op="create" — `wspXXXXXXXX`, from the Airtable URL.
            tables: op="create" — `[{"name": …, "fields": [{"name": …, "type": …}]}]`.
                The first field of each table becomes its primary field.
        """
        if op not in _BASE_OPS:
            raise _bad(_one_of("op", _BASE_OPS))
        client = _client()

        if op == "list":
            out = _run(lambda: _paginate(
                lambda off: client.list_bases(offset=off), "bases", max_bases))
            if not out["bases"]:
                out["hint"] = (
                    "Aucune base accordée à ce token. Ouvrir airtable.com/create/tokens, "
                    "éditer le token, et ajouter la ou les bases dans « Access » — les "
                    "scopes seuls ne donnent accès à rien."
                )
            return out

        if op == "whoami":
            return _run(client.whoami)

        return _run(lambda: client.create_base(
            _need(name, "name", op),
            _need(workspace_id, "workspace_id", op),
            _need(tables, "tables", op),
        ))

    # ==================================================================
    # Pièces jointes — pas de paramètre `op` : un seul verbe, tout obligatoire
    # ==================================================================

    @mcp.tool()
    def airtable_attachment(
        base_id: str,
        record_id: str,
        field: str,
        filename: str,
        content_type: str,
        file_base64: str,
    ) -> dict:
        """⚠️ WRITES: attach a file to an attachment column of a row.

        ADDS to the column — existing attachments are kept. Returns the updated row.

        The file travels base64-encoded and Airtable caps it at **5 MB**. For anything
        bigger, host the file somewhere public and write its URL into the column with
        `airtable_record(op="update", fields={"<column>": [{"url": "https://…"}]})` —
        Airtable fetches it itself, with no size limit of this kind.

        Args:
            base_id: the base, `appXXXXXXXX`.
            record_id: the row, `recXXXXXXXX`.
            field: the attachment column, name or `fldXXXXXXXX`.
            filename: the name the file gets in Airtable, with its extension.
            content_type: MIME type, e.g. "image/png", "application/pdf", "text/csv".
            file_base64: the file's bytes, base64-encoded (5 MB max).
        """
        client = _client()
        return _run(lambda: client.upload_attachment(
            base_id, record_id, field,
            filename=filename, content_type=content_type, file_b64=file_base64,
        ))

    # ==================================================================
    # Sync CSV — pas de paramètre `op` non plus
    # ==================================================================

    @mcp.tool()
    def airtable_sync(
        base_id: str,
        table: str,
        sync_id: str,
        csv_data: str,
    ) -> dict:
        """⚠️ WRITES: push raw CSV into a table set up as an Airtable "Sync API" source.

        This is NOT a CSV import into an ordinary table. The table must have been
        created in Airtable through the "Sync from other sources → API" flow, which is
        what produces `sync_id`; you find it in the synced table's settings. To load
        rows into a normal table, use `airtable_record(op="create")` instead.

        Each push REPLACES the synced content — the CSV is the source of truth, not an
        append. Limits: 10 000 rows, 500 columns, 2 MB per push, and 20 pushes per
        5 minutes per base (a tighter limit than the rest of the API).

        Args:
            base_id: the base, `appXXXXXXXX`.
            table: the synced table, name or `tblXXXXXXXX`.
            sync_id: the API endpoint sync id from the synced table's settings.
            csv_data: the CSV itself, header row included.
        """
        client = _client()
        result = _run(lambda: client.sync_csv(base_id, table, sync_id, csv_data))
        return result if isinstance(result, dict) else {"ok": True, "response": result}
