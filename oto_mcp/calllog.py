"""Journal des appels — middleware MCP inliné (ex-lib `otomata-calllog`) + gestes REST.

Une ligne par `call_tool` reçu : qui (sub du JWT), quel tool, arguments tronqués,
durée, succès/erreur. L'écriture part en tâche de fond : zéro latence ajoutée, un
échec de journalisation n'échoue jamais le tool.

Le journal a **un seul domicile** : la table `tool_calls` (`db.insert_tool_call`),
et **un seul module** — ici. `log_rest_call` y ajoute les gestes faits depuis le
dashboard (`kind='rest'`), sous le MÊME vocabulaire de `tool` que la surface MCP
(`data_write`…), pour qu'une lecture de journal voie l'agent ET l'humain.

Historique : lib `otomata-calllog` (extraite d'ogic 2026-06-12), inlinée ici le
2026-07-23 (otomata-calllog#1) — le backend était son dernier consommateur, et le
**contrat canonique** (schéma de ligne `tool_calls`, dashboards comparables entre
serveurs MCP) vit désormais dans le socle `otomata-mcp` (`logging.py`). Le schéma
local étend ce contrat en OTO-LOCAL : `kind`, `session_id`, `run_id`, `org_id`,
`client_id` (cf. `db/_schema.py`).
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable

from fastmcp.server.middleware import Middleware

logger = logging.getLogger("oto_mcp.calllog")

MAX_ARG_CHARS = 300
MAX_ERROR_CHARS = 500

Sink = Callable[[dict], Awaitable[None]]

# Références fortes sur les écritures en tâche de fond (anti-GC asyncio).
_PENDING: set = set()


def truncated_args(arguments: dict | None, max_chars: int = MAX_ARG_CHARS) -> dict | None:
    """Arguments journalisables : scalaires gardés tels quels, le reste
    stringifié et coupé — le journal montre l'intention, pas le payload."""
    if not arguments:
        return None
    out: dict[str, Any] = {}
    for k, v in arguments.items():
        if not (v is None or isinstance(v, (int, float, bool))):
            v = str(v)
            if len(v) > max_chars:
                v = v[:max_chars] + "…"
        out[k] = v
    return out


MAX_FIELDS = 50
MAX_FIELD_CHARS = 64

# Références fortes sur les écritures REST en tâche de fond (anti-GC asyncio).
_REST_PENDING: set = set()


def _fields_list(fields: Any) -> list[str]:
    """Champs touchés par l'écriture, bornés — gardés comme un VRAI tableau JSON
    (`truncated_args` stringifierait la liste, et le journal ne serait plus
    exploitable colonne par colonne)."""
    if not fields:
        return []
    return [str(f)[:MAX_FIELD_CHARS] for f in list(fields)[:MAX_FIELDS]]


def log_rest_call(tool: str, *, sub: str | None, args: dict | None = None,
                  fields: Any = None, ok: bool = True, error: str | None = None,
                  org_id: int | None = None, duration_ms: int | None = None) -> None:
    """Journalise un GESTE fait depuis le dashboard (REST) dans le flux unifié.

    Même table, même fonction d'insertion et même discipline que le middleware MCP :
    **best-effort** (l'écriture part en tâche de fond, un échec se log en warning et
    n'échoue JAMAIS la requête métier) et arguments passés à `truncated_args` (le
    journal montre l'intention, jamais le payload).

    `tool` doit nommer le geste dans le vocabulaire de la surface MCP (`data_write`,
    `data_delete_row`, `data_release`) : les lectures du journal (parcours d'une
    ligne, activité d'un tableau) filtrent là-dessus, pas sur la route HTTP.
    ⚠️ Distinct de la ligne de route posée par `api_routes.RestCallLogger`
    (`tool='PATCH /api/…'`, sans args) : celle-là est de la télémétrie de surface,
    celle-ci porte le SENS du geste (quelle ligne, quels champs, quel état avant/après).
    """
    row: dict[str, Any] = {
        "server": "oto",
        "kind": "rest",
        "sub": sub,
        "tool": tool,
        "args": {**(truncated_args(args) or {}), "fields": _fields_list(fields)},
        "ok": bool(ok),
        "error": (str(error)[:MAX_ERROR_CHARS] if error else None),
        "org_id": org_id,
        "duration_ms": duration_ms,
    }
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # Appelé hors event loop (contexte synchrone) : on insère en direct, même
        # chemin d'insertion — le journal reste best-effort, pas de file d'attente.
        _insert_rest(row)
        return
    task = loop.create_task(_emit_rest(row))
    _REST_PENDING.add(task)
    task.add_done_callback(_REST_PENDING.discard)


async def _emit_rest(row: dict) -> None:
    """Écrit hors event loop (`to_thread` : l'INSERT psycopg est sync)."""
    try:
        await asyncio.to_thread(_insert_rest, row)
    except Exception:  # noqa: BLE001 — le journal ne casse jamais le service
        logger.warning("journalisation rest en échec (%s)", row.get("tool"), exc_info=True)


def _insert_rest(row: dict) -> None:
    """Stampe l'org de l'acteur (scope d'audit, comme le sink MCP) puis insère."""
    try:
        if row.get("org_id") is None and row.get("sub"):
            from . import access
            row["org_id"] = access.current_org(row["sub"])
    except Exception:  # noqa: BLE001 — org indisponible ⇒ ligne non scopée, pas d'échec
        logger.debug("org de journalisation rest indisponible", exc_info=True)
    try:
        from . import db
        db.insert_tool_call(row)
    except Exception:  # noqa: BLE001
        logger.warning("insert tool_call rest en échec (%s)", row.get("tool"), exc_info=True)


class ToolCallLogger(Middleware):
    """Middleware FastMCP : journalise chaque on_call_tool via le sink fourni.

    Sous-classe `fastmcp.server.middleware.Middleware` : son `__call__` dispatche
    vers nos hooks `on_*`. Un simple duck-typing (juste `on_call_tool`) ne suffit
    pas — fastmcp ≥3 appelle le middleware comme un callable et lèverait
    « 'ToolCallLogger' object is not callable », cassant le handshake MCP.

    `identity` = callable → {"sub": …, "email": …} (auth Logto custom d'oto :
    le `get_access_token` fastmcp par défaut ne la voit pas).
    """

    def __init__(self, sink: Sink, server: str, identity: Callable[[], dict] | None = None):
        self.sink = sink
        self.server = server
        self.identity = identity or (lambda: {})

    async def on_initialize(self, context, call_next):
        """Journalise le HANDSHAKE lui-même (`kind='protocol'`, ADR 0017 « un seul flux »).

        Pourquoi : deux mécanismes centraux sont calculés à l'`initialize` — la règle
        de visibilité des tools (`SessionVisibilityMiddleware`) et l'injection des
        blocs A/C (`DynamicInstructionsMiddleware`) — donc « une fois par session ».
        Or la cadence RÉELLE de re-handshake des clients n'a jamais été mesurée :
        « Claude rehandshake par conversation » est une croyance de docstring, pas
        une observation. Le sink stampe déjà `session_id` et `client_id` (`azp` :
        claude.ai, Claude Code, ChatGPT…) → une ligne par handshake suffit à trancher,
        par client, sur du trafic réel.

        Isolé du monitoring d'outils : toutes ses lectures filtrent `kind='mcp'`.
        """
        # ⚠️ ASYMÉTRIE fastmcp : `on_initialize` reçoit un `InitializeRequest` ENTIER
        # (params sous `.params`), là où `on_call_tool` reçoit directement les params.
        # Le repli sur `msg` couvre une éventuelle normalisation amont.
        params = getattr(context.message, "params", None) or context.message
        info = getattr(params, "clientInfo", None)
        row: dict[str, Any] = {
            "server": self.server,
            "kind": "protocol",
            "sub": None,
            "email": None,
            "tool": "initialize",
            "args": {
                "client_name": getattr(info, "name", None),
                "client_version": getattr(info, "version", None),
                "protocol_version": getattr(params, "protocolVersion", None),
            },
        }
        try:
            row.update({k: v for k, v in self.identity().items() if k in ("sub", "email")})
        except Exception:
            pass
        t0 = time.monotonic()
        try:
            result = await call_next(context)
        except Exception as e:
            self._record({**row, "ok": False, "error": str(e)[:MAX_ERROR_CHARS]}, t0)
            raise
        self._record({**row, "ok": True, "error": None}, t0)
        return result

    async def on_call_tool(self, context, call_next):
        row: dict[str, Any] = {
            "server": self.server,
            "sub": None,
            "email": None,
            "tool": context.message.name,
            "args": truncated_args(context.message.arguments),
        }
        try:
            row.update({k: v for k, v in self.identity().items() if k in ("sub", "email")})
        except Exception:
            pass
        t0 = time.monotonic()
        try:
            result = await call_next(context)
        except Exception as e:
            self._record({**row, "ok": False, "error": str(e)[:MAX_ERROR_CHARS]}, t0)
            raise
        self._record({**row, "ok": True, "error": None}, t0)
        return result

    def _record(self, row: dict, t0: float) -> None:
        row["duration_ms"] = int((time.monotonic() - t0) * 1000)
        task = asyncio.create_task(self._insert(row))
        _PENDING.add(task)
        task.add_done_callback(_PENDING.discard)

    async def _insert(self, row: dict) -> None:
        try:
            await self.sink(row)
        except Exception:
            logger.exception("journalisation tool_call en échec (%s)", row.get("tool"))
