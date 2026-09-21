"""Journal du handshake MCP : ce que le serveur a RÉELLEMENT servi à un client.

Pourquoi : un client (Codex, ChatGPT, celui d'un tenant) ne reçoit parfois aucun outil,
et rien ne dit, côté serveur, ce qui lui a été rendu. Le journal `tool_calls`
(`calllog.ToolCallLogger`) trace les APPELS, pas les listes ; l'`initialize` y est déjà
(`kind='protocol'`) mais sans le protocole négocié ni l'hôte. Ce module écrit UNE ligne
de log par `initialize` et par requête de liste — le client, le protocole, l'hôte
(masqué s'il n'est pas connu, cf. `_etiquette_hote`), et le NOMBRE d'éléments rendus. Observabilité pure : il ne lit rien qu'il ne rende tel quel.

    mcp.handshake initialize client=<nom>/<version> protocol_requested=<v> protocol_negotiated=<v> host=<hôte|projet|inconnu>
    mcp.handshake list method=<tools/list|prompts/list|…> count=<n> client=<nom>/<version> host=<hôte|projet|inconnu>

**Jamais** de contenu : ni nom d'outil, ni schéma, ni description, ni `sub`, ni e-mail, ni
jeton. Seuls comptent un `len()` et des attributs lus. Les valeurs venues du client (nom,
version, hôte) sont nettoyées (jeu de caractères restreint, longueur bornée) : une valeur
hostile ne peut pas forger une seconde ligne de journal.

**Où c'est appelé** : `ToolAliasMiddleware` (`middleware/alias.py`) — le plus externe de
NOS middlewares pour `tools/list`, donc le dernier à toucher la liste, après la
visibilité de session (posée à l'`initialize`), l'enrichissement des descriptions ET
l'ajout des alias dépréciés : le compte journalisé est celui que le client reçoit. Un
hôte plus interne (`ToolCallLogger`) sous-compterait. Aucun middleware n'est ajouté et
l'ordre de la chaîne (gardé par `tests/middleware/test_middleware_order.py`) est intact.

Coût : `len()` + quelques lectures d'attributs, DANS la boucle (serveur MONO-LOOP,
`docs/event-loop-perf.md`) — aucun accès DB, aucune sérialisation. Fail-open : une
difficulté ici ne casse jamais un handshake ; elle se voit (`warning`).
"""
from __future__ import annotations

import logging
import re

from fastmcp.server.dependencies import get_http_headers

from . import config, tenancy

logger = logging.getLogger(__name__)

_SAFE = re.compile(r"[^A-Za-z0-9._:/+\-]")
_MAX = 64
_ABSENT = "-"


def _clean(value) -> str:
    """Une valeur venue du client, ramenée à un jeton inoffensif d'une seule ligne."""
    if value is None or value == "":
        return _ABSENT
    return _SAFE.sub("?", str(value))[:_MAX]


def _hote_connu(hote: str) -> bool:
    """`hote` est-il un hôte que la plateforme ANNONCE ELLE-MÊME (pas une URL-capacité) ?

    Trois sources, toutes en mémoire : l'hôte public de l'instance, ses audiences alt
    et `mcp.<domaine de projet>` (l'hôte canonique, préprod comme prod), et les hôtes
    DÉCLARÉS d'un tenant (`tenancy.current().for_host`)."""
    if hote in {config.public_host(), f"mcp.{config.project_domain()}",
                *config.mcp_audience_alt_hosts()}:
        return True
    return tenancy.current().for_host(hote) is not None


def _etiquette_hote(hote: str) -> str:
    """La valeur JOURNALISÉE d'un hôte : lui-même s'il est connu, sinon une étiquette fixe.

    ⚠️ Le sous-domaine d'un projet publié (`<slug>.mcp.<D>`, `<slug>.share.<D>`) est,
    en mode `secret`, une URL-CAPACITÉ (ADR 0032) : le slug EST le secret, et les logs
    applicatifs sont lisibles bien au-delà de ceux qui le connaissent. Liste blanche,
    pas liste noire : tout hôte non reconnu est masqué, quel que soit son mode."""
    if not hote:
        return _ABSENT
    if _hote_connu(hote):
        return _clean(hote)
    domaine = config.project_domain()
    if hote.endswith((f".mcp.{domaine}", f".share.{domaine}")):
        return "projet"
    return "inconnu"


def _host() -> str:
    """Hôte demandé par le client, MASQUÉ s'il n'est pas connu (`_etiquette_hote`).

    Derrière le proxy, `x-forwarded-host` prime : le filtre s'applique à la valeur
    RETENUE (première valeur, port retiré) — il ne peut donc pas être contourné par
    l'un ou l'autre en-tête."""
    headers = get_http_headers(include={"host", "x-forwarded-host"})
    brut = headers.get("x-forwarded-host") or headers.get("host") or ""
    retenu = brut.split(",")[0].strip().lower().split(":")[0]
    try:
        return _etiquette_hote(retenu)
    except Exception:  # noqa: BLE001 — jamais la valeur brute : à défaut, l'étiquette fixe
        logger.warning("mcp.handshake hôte non classé (fail-closed, étiquette fixe)",
                       exc_info=True)
        return "inconnu"


def _client(params) -> str:
    """`nom/version` du client, d'après les params de l'`initialize`."""
    info = getattr(params, "clientInfo", None)
    return f"{_clean(getattr(info, 'name', None))}/{_clean(getattr(info, 'version', None))}"


def _client_of_session(context) -> str:
    """Le client d'une requête de liste : `clientInfo` gardé par la session MCP.

    Absent (session sans état, ou pas de session) ⟹ `-/-` : c'est aussi une réponse."""
    ctx = getattr(context, "fastmcp_context", None)
    try:
        params = ctx.session.client_params if ctx is not None else None
    # noqa: SILENT — la session peut ne pas exister hors requête ; « client inconnu » est la réponse
    except Exception:  # noqa: BLE001
        params = None
    return _client(params)


def log_initialize(context, result) -> None:
    """Une ligne par `initialize`. `result` = l'`InitializeResult` rendu (protocole négocié)."""
    try:
        # ⚠️ ASYMÉTRIE fastmcp (cf. `calllog.ToolCallLogger.on_initialize`) : `on_initialize`
        # reçoit la requête ENTIÈRE, params sous `.params`.
        params = getattr(context.message, "params", None) or context.message
        logger.info(
            "mcp.handshake initialize client=%s protocol_requested=%s "
            "protocol_negotiated=%s host=%s",
            _client(params),
            _clean(getattr(params, "protocolVersion", None)),
            _clean(getattr(result, "protocolVersion", None)),
            _host(),
        )
    except Exception:  # noqa: BLE001 — un journal ne casse pas un handshake
        logger.warning("mcp.handshake initialize non journalisé (fail-open)", exc_info=True)


def log_list(method: str, context, items) -> None:
    """Une ligne par requête de liste. `items` = la liste TELLE QUE rendue au client."""
    try:
        logger.info(
            "mcp.handshake list method=%s count=%d client=%s host=%s",
            method, len(items), _client_of_session(context), _host(),
        )
    except Exception:  # noqa: BLE001 — un journal ne casse pas une liste
        logger.warning("mcp.handshake list non journalisée (fail-open)", exc_info=True)


class JournalListesMixin:
    """`prompts/list`, `resources/list`, `resources/templates/list` — journalisées.

    Aucun de nos middlewares ne modifie ces listes : celle que voit le plus externe est
    celle que le client reçoit. `tools/list` et `initialize` sont journalisés à la main
    dans `ToolAliasMiddleware` (c'est lui qui change la liste d'outils, et il sait quel
    est son dernier `return`)."""

    async def on_list_prompts(self, context, call_next):
        items = await call_next(context)
        log_list("prompts/list", context, items)
        return items

    async def on_list_resources(self, context, call_next):
        items = await call_next(context)
        log_list("resources/list", context, items)
        return items

    async def on_list_resource_templates(self, context, call_next):
        items = await call_next(context)
        log_list("resources/templates/list", context, items)
        return items
