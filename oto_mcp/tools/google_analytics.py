"""Google Analytics 4 — lecture par CLÉ DE COMPTE DE SERVICE : propriétés,
rapports, temps réel, catalogue des dimensions et métriques, événements clés.

Wrappe `oto.tools.google_analytics.GA4Client` (Admin + Data API v1beta).
Credential à UN champ secret (`secret_kind="fields"`, résolu par
`access.resolve_credential_fields`) : `service_account_json`, le fichier JSON de
la clé, entier. Pas l'OAuth du connecteur `google` : le consentement d'un
utilisateur au scope `analytics.readonly` est bloqué par Google pour notre
application (cf. `providers/google_analytics.py`).

**Cinq tools, lecture seule** — le client n'a aucune méthode d'écriture, et le
scope demandé (`analytics.readonly`) l'interdirait :

- `ga4_properties` — comptes et propriétés que le compte de service voit (et,
  sur demande, leurs flux de données) ;
- `ga4_report` — `:runReport`, les 30 derniers jours complets par défaut ;
- `ga4_realtime` — `:runRealtimeReport` ;
- `ga4_metadata` — les dimensions et métriques utilisables sur une propriété ;
- `ga4_key_events` — les événements clés configurés.

**Deux refus nommés**, parce que ce sont les deux fautes probables :
- un nom de dimension ou de métrique invalide (400 `INVALID_ARGUMENT`) → le
  message de Google, qui nomme le champ fautif, plus le renvoi vers `ga4_metadata` ;
- un compte de service sans accès à la propriété (403) → l'email du compte de
  service, à ajouter comme Lecteur dans GA4.

**Projection** : un rapport GA4 brut répète le nom de chaque colonne et enveloppe
chaque cellule (`{"value": "12"}`) ; la vue par défaut est une TABLE (`columns` +
`rows`, métriques typées) qui garde les avertissements de fiabilité (échantillonnage,
seuils). Le catalogue d'une propriété dépasse 450 entrées (mesuré le 25/09/2026) :
sa vue par défaut rend les noms d'API groupés par catégorie, le détail d'une entrée
se demande par `search`. `full=True` rend partout la réponse brute.
"""
from __future__ import annotations

from typing import Literal, Optional

from fastmcp import FastMCP
from mcp.types import INVALID_PARAMS, ErrorData

from .. import access
from ..connectors import verify as connector_verify
from ..mcp_errors import McpError

_CONNECTOR = "google_analytics"
_FIELD = "service_account_json"
_DEFAULT_LIMIT = 100
# `include_streams` fait un appel par propriété : au-delà, on refuse plutôt que de
# faire attendre l'agent sur des dizaines d'appels qu'il n'a pas vus venir.
_MAX_STREAM_PROPERTIES = 25
_METADATA_HINT = ("vérifie les noms avec `ga4_metadata` (noms d'API comme `activeUsers`, "
                  "`eventName` — pas les libellés de l'interface GA4)")


def _bad(msg: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=msg))


def _upstream_message(e) -> str:
    """Le refus amont, traduit en conduite. Lu sur la CLASSE du refus (le statut
    canonique Google, classé par le client), jamais sur le texte."""
    from oto.tools.google_analytics import (GA4InvalidArgument, GA4PermissionDenied,
                                            GA4ServiceDisabled, ServiceAccountAuthError)
    if isinstance(e, GA4InvalidArgument):
        return (f"GA4 a refusé la requête : {e.message.strip()} — {_METADATA_HINT}. "
                "Certaines combinaisons dimension × métrique sont aussi incompatibles.")
    if isinstance(e, GA4PermissionDenied):
        quoi = e.resource or "cette ressource"
        return (f"Le compte de service {e.client_email} n'a pas accès à {quoi}. Ajoute "
                "cet email comme Lecteur de la propriété dans GA4 (Administration → "
                "Gestion des accès à la propriété) ; `ga4_properties` liste ce qu'il "
                "voit déjà.")
    if isinstance(e, GA4ServiceDisabled):
        return ("Une API Google Analytics n'est pas activée dans le projet Google Cloud "
                "du compte de service : active « Google Analytics Data API » et « Google "
                "Analytics Admin API » (console Google Cloud → API et services), puis "
                f"réessaie. Détail Google : {e.message}")
    if isinstance(e, ServiceAccountAuthError):
        return (f"Google refuse d'émettre un jeton pour cette clé de compte de service "
                f"({e.body}) : clé supprimée ou révoquée, ou compte de service désactivé "
                "— un administrateur doit déposer une nouvelle clé JSON.")
    status = e.status_code
    if status == 429:
        return ("GA4 : quota de requêtes de la propriété atteint (429) — réessaie plus "
                "tard, ou réduis la taille des rapports.")
    if status >= 500:
        return f"GA4 est momentanément indisponible (HTTP {status}) — réessaie plus tard."
    return f"GA4 a refusé la requête (HTTP {status}) : {getattr(e, 'message', '') or e.body}"


def _count_properties(summaries: list) -> int:
    return sum(len(a.get("propertySummaries") or ()) for a in summaries)


def _verify(fields: dict, config: dict | None = None) -> dict:  # noqa: ARG001
    """Sonde « tester la connexion » : `accountSummaries`, sans effet de bord.

    Rend QUI la clé authentifie (l'email du compte de service) et ce qu'elle voit
    (comptes, propriétés). ⚠️ Zéro propriété visible est un REFUS, pas un vert : la
    clé est valide mais le connecteur ne peut rien lire — le cas d'un compte de
    service qu'on a oublié d'ajouter comme Lecteur dans GA4."""
    from oto.tools.common.errors import UpstreamHTTPError
    from oto.tools.google_analytics import GA4Client

    try:
        client = GA4Client(fields[_FIELD])
    except ValueError as e:
        raise connector_verify.NonAutorise(str(e)) from None
    try:
        summaries = client.account_summaries()
    except UpstreamHTTPError as e:
        if e.status_code in (401, 403):
            raise connector_verify.NonAutorise(_upstream_message(e)) from None
        raise
    n = _count_properties(summaries)
    if n == 0:
        raise connector_verify.NonAutorise(
            f"La clé authentifie ({client.client_email}) mais ne voit aucune propriété "
            "GA4 : ajoute cet email comme Lecteur de la propriété dans GA4 "
            "(Administration → Gestion des accès à la propriété).")
    return {"identity": {"service_account": client.client_email,
                         "accounts": len(summaries), "properties": n}}


def _compact_stream(s: dict) -> dict:
    web = s.get("webStreamData") or {}
    app = s.get("androidAppStreamData") or s.get("iosAppStreamData") or {}
    out = {"stream": s.get("name"), "type": s.get("type"), "name": s.get("displayName"),
           "measurement_id": web.get("measurementId"), "url": web.get("defaultUri"),
           "app": app.get("packageName") or app.get("bundleId")}
    return {k: v for k, v in out.items() if v}


def _compact_meta_entry(entry: dict) -> dict:
    out = {"api_name": entry.get("apiName"), "ui_name": entry.get("uiName"),
           "category": entry.get("category"), "type": entry.get("type"),
           "description": entry.get("description"),
           "custom": entry.get("customDefinition") or None,
           "expression": entry.get("expression")}
    return {k: v for k, v in out.items() if v}


def _by_category(entries: list) -> dict:
    grouped: dict[str, list] = {}
    for e in entries:
        grouped.setdefault(e.get("category") or "(sans catégorie)", []).append(e.get("apiName"))
    return grouped


def register(mcp: FastMCP) -> None:
    from oto.tools.common.errors import UpstreamHTTPError
    from oto.tools.google_analytics import GA4Client, flatten_report

    connector_verify.register(_CONNECTOR, _verify)

    def _client() -> GA4Client:
        return GA4Client(access.resolve_credential_fields(_CONNECTOR)[_FIELD])

    def _run(fn):
        try:
            return fn()
        except ValueError as e:
            raise _bad(str(e))
        except UpstreamHTTPError as e:
            raise _bad(_upstream_message(e))

    def _table(resp: dict, prop: str, **echo) -> dict:
        out = {"property": prop, **{k: v for k, v in echo.items() if v is not None},
               **flatten_report(resp)}
        out["projection"] = ("vue en table (colonnes = dimensions puis métriques) ; "
                             "full=True rend la réponse brute de GA4")
        return out

    @mcp.tool()
    def ga4_properties(include_streams: bool = False, full: bool = False) -> dict:
        """The Google Analytics accounts and GA4 properties this connector can read
        — call it first: every other `ga4_*` tool needs a `property` id from here.

        Only properties where the service account was added as a user (Viewer is
        enough) are listed. A property you expect but don't see = that access is
        missing in GA4 (Admin → Property access management).

        Args:
            include_streams: also list each property's data streams (web/app,
                measurement id `G-…`, site URL) — one extra call per property,
                refused above 25 properties.
            full: raw `accountSummaries` (and raw streams) instead of the compact view.
        """
        def _go():
            client = _client()
            summaries = client.account_summaries()
            n = _count_properties(summaries)
            if include_streams and n > _MAX_STREAM_PROPERTIES:
                raise ValueError(
                    f"{n} propriétés visibles : include_streams ferait {n} appels. "
                    "Liste d'abord les propriétés, puis cible celle qui t'intéresse.")
            if full:
                out = {"accountSummaries": summaries, "property_count": n}
                if include_streams:
                    out["dataStreams"] = {
                        p["property"]: client.list_data_streams(p["property"])
                        for a in summaries for p in a.get("propertySummaries") or ()}
                return out
            accounts = []
            for a in summaries:
                props = []
                for p in a.get("propertySummaries") or ():
                    item = {"property": p.get("property"), "name": p.get("displayName")}
                    if p.get("propertyType") not in (None, "PROPERTY_TYPE_ORDINARY"):
                        item["type"] = p["propertyType"]
                    if include_streams:
                        item["streams"] = [_compact_stream(s) for s in
                                           client.list_data_streams(p["property"])]
                    props.append(item)
                accounts.append({"account": a.get("account"), "name": a.get("displayName"),
                                 "properties": props})
            return {"accounts": accounts, "property_count": n}
        return _run(_go)

    @mcp.tool()
    def ga4_report(
        property: str,
        metrics: list[str],
        dimensions: Optional[list[str]] = None,
        start_date: str = "30daysAgo",
        end_date: str = "yesterday",
        dimension_filter: Optional[dict] = None,
        metric_filter: Optional[dict] = None,
        order_by: Optional[list[str]] = None,
        limit: int = _DEFAULT_LIMIT,
        offset: int = 0,
        full: bool = False,
    ) -> dict:
        """A GA4 report (Data API `runReport`) on one property: metrics broken down
        by dimensions over a date range. Default range = the last 30 complete days
        (`30daysAgo` → `yesterday`, today excluded — same as GA4's "Last 30 days").

        Use API names, not UI labels — `ga4_metadata` lists what the property
        supports (custom dimensions included, e.g. `customEvent:plan`). Common:
        metrics `activeUsers`, `sessions`, `screenPageViews`, `eventCount`,
        `keyEvents`, `engagementRate`, `totalRevenue`; dimensions `date`,
        `eventName`, `pagePath`, `country`, `deviceCategory`,
        `sessionDefaultChannelGroup`, `sessionSource`.

        Returns a table: `columns` (dimensions then metrics), `rows`, and
        `row_count` = total matching rows (page with `offset`). `metadata` carries
        GA4's reliability flags — `samplingMetadatas` (sampled),
        `subjectToThresholding` (small counts hidden for privacy),
        `dataLossFromOtherRow` (rare values folded into "(other)"): report them
        with the numbers, never silently.

        Args:
            property: GA4 property id, `123456789` or `properties/123456789` (from
                `ga4_properties`) — not a `G-…` measurement id.
            metrics: metric API names, e.g. ["activeUsers", "sessions"].
            dimensions: dimension API names, e.g. ["date", "eventName"].
            start_date / end_date: `YYYY-MM-DD`, `today`, `yesterday` or `NdaysAgo`.
            dimension_filter: simple form `{"eventName": "purchase", "country":
                ["France", "Belgium"]}` (text = exact match, list = any of,
                combined with AND), or a full GA4 FilterExpression (`andGroup`,
                `orGroup`, `notExpression`, `filter`) for other operators.
            metric_filter: same forms, on metrics (a number = equality; use a
                FilterExpression with `numericFilter` for > / <).
            order_by: e.g. ["-sessions", "date"] — `-` = descending.
            limit: max rows returned (default 100).
            offset: rows to skip, for paging.
            full: raw GA4 response instead of the table.
        """
        def _go():
            resp = _client().run_report(
                property, metrics=metrics, dimensions=dimensions, start_date=start_date,
                end_date=end_date, dimension_filter=dimension_filter,
                metric_filter=metric_filter, order_by=order_by, limit=limit,
                offset=offset or None)
            if full:
                return resp
            out = _table(resp, property, date_range=f"{start_date} → {end_date}")
            if out["row_count"] > offset + len(out["rows"]):
                out["next_offset"] = offset + len(out["rows"])
            return out
        return _run(_go)

    @mcp.tool()
    def ga4_realtime(
        property: str,
        metrics: Optional[list[str]] = None,
        dimensions: Optional[list[str]] = None,
        dimension_filter: Optional[dict] = None,
        metric_filter: Optional[dict] = None,
        order_by: Optional[list[str]] = None,
        limit: int = _DEFAULT_LIMIT,
        minutes_ago: Optional[int] = None,
        full: bool = False,
    ) -> dict:
        """What is happening right now on a GA4 property (Data API
        `runRealtimeReport`) — the last 30 minutes (60 on GA4 360).

        Realtime supports a SMALLER set of names than `ga4_report`: metrics
        `activeUsers`, `eventCount`, `screenPageViews`, `keyEvents`; dimensions
        such as `country`, `city`, `deviceCategory`, `platform`, `eventName`,
        `unifiedScreenName`, `minutesAgo`. No rows = nobody active in the window,
        not an error.

        Args:
            property: GA4 property id (from `ga4_properties`).
            metrics: default ["activeUsers"].
            dimensions: e.g. ["country"].
            dimension_filter / metric_filter / order_by: same forms as `ga4_report`.
            limit: max rows returned (default 100).
            minutes_ago: narrow the window to the last N minutes.
            full: raw GA4 response instead of the table.
        """
        def _go():
            resp = _client().run_realtime_report(
                property, metrics=metrics or ["activeUsers"], dimensions=dimensions,
                dimension_filter=dimension_filter, metric_filter=metric_filter,
                order_by=order_by, limit=limit, minutes_ago=minutes_ago)
            if full:
                return resp
            return _table(resp, property, window_minutes=minutes_ago)
        return _run(_go)

    @mcp.tool()
    def ga4_metadata(
        property: str,
        kind: Literal["all", "dimensions", "metrics"] = "all",
        search: Optional[str] = None,
        full: bool = False,
    ) -> dict:
        """The dimensions and metrics a GA4 property supports — the vocabulary of
        `ga4_report`, custom definitions included (`customEvent:…`, `customUser:…`).
        Check here before building a report, or when one is refused for an
        invalid name.

        Default view: API names grouped by category (a property has 450+
        entries). Pass `search` to get the matching entries in detail (UI name,
        description, metric type), or `full=True` for everything raw.

        Args:
            property: GA4 property id (from `ga4_properties`); `0` = the catalogue
                common to all properties (no custom definitions).
            kind: "all" (default) | "dimensions" | "metrics".
            search: case-insensitive match on API name, UI name or description.
            full: raw `/metadata` response.
        """
        def _go():
            meta = _client().get_metadata(property)
            parts = ("dimensions", "metrics") if kind == "all" else (kind,)
            if full:
                return {p: meta.get(p) or [] for p in parts}
            if search:
                needle = search.lower()
                out = {}
                for p in parts:
                    hits = [e for e in meta.get(p) or ()
                            if needle in " ".join(str(e.get(k) or "") for k in
                                                  ("apiName", "uiName", "description")).lower()]
                    out[p] = [_compact_meta_entry(e) for e in hits]
                return out
            out = {p: _by_category(meta.get(p) or []) for p in parts}
            out["counts"] = {p: len(meta.get(p) or ()) for p in parts}
            out["projection"] = ("noms d'API par catégorie ; `search` rend le détail des "
                                 "entrées qui correspondent, full=True le brut")
            return out
        return _run(_go)

    @mcp.tool()
    def ga4_key_events(property: str, full: bool = False) -> dict:
        """The key events (formerly "conversions") configured on a GA4 property —
        the events whose counts `ga4_report` reports under the `keyEvents` metric.

        Args:
            property: GA4 property id (from `ga4_properties`).
            full: raw `keyEvents` records.
        """
        def _go():
            events = _client().list_key_events(property)
            if full:
                return {"keyEvents": events}
            rows = []
            for e in events:
                item = {"event_name": e.get("eventName"),
                        "counting_method": e.get("countingMethod"),
                        "created": e.get("createTime"), "custom": e.get("custom"),
                        "default_value": e.get("defaultValue")}
                rows.append({k: v for k, v in item.items() if v is not None})
            return {"key_events": rows, "count": len(rows)}
        return _run(_go)
