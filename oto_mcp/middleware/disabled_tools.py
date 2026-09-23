"""`UserDisabledToolsMiddleware` — la visibilité des tools du user, par session."""
from __future__ import annotations

import logging
from typing import Optional

from fastmcp.server.dependencies import get_http_headers
from fastmcp.server.middleware import Middleware
from starlette.concurrency import run_in_threadpool

from ..auth.hooks import current_user_sub_from_token
from ..session_visibility import apply_session_visibility

logger = logging.getLogger(__name__)


def _org_membre(sub: str, org: int) -> bool:
    """Sync (threadpool, cf. #1049) : le sub est-il membre de `org` ? Même source de
    vérité que `roles.is_org_member` (utilisée partout ailleurs pour l'appartenance)."""
    from .. import roles
    return roles.is_org_member(sub, org)


async def _org_de_mission(sub: str) -> Optional[int]:
    """Org à passer à `apply_session_visibility(..., org=…)` pour une session
    ouverte avec l'en-tête `X-Oto-Org` — ou `None` (dérive de la maison, comportement
    inchangé) (#1058, 23/09/2026).

    Mesuré le 22/09 : la boîte à outils d'une session dérivait TOUJOURS de la maison
    (`access.current_org`), jamais de l'org RÉELLE de la mission en cours — à
    l'`initialize`, aucun jeton d'appel n'existe encore (`_org=`/`_run_id=` n'arrivent
    qu'avec le premier `call_tool`). Une bascule de la maison du compte porteur des
    workers de flotte (sans déploiement) a donc masqué tous les connecteurs de TOUTES
    les flottes de ce compte pendant ~2h, alors que les APPELS de ces flottes
    continuaient de s'exécuter sous l'org réelle de la mission — seule la boîte
    AFFICHÉE suivait la maison.

    Le runner ouvre une session PAR APPEL et connaît l'org de sa mission dès la
    CONSTRUCTION du client (avant même `run_start` — cf. discussion #1058/#1060) :
    il porte `X-Oto-Org` sur la requête HTTP d'`initialize`, comme la face REST le
    porte déjà sur les siennes (`api/routes.py::_parse_view_org`). Sans cet en-tête
    (session humaine, dashboard, claude.ai) : dérive de la maison, RIEN ne change —
    aujourd'hui, aucun client MCP existant ne pose cet en-tête sur `/mcp` (côté REST
    seul `ViewAsMiddleware` le lit, et seulement sur `/api/*`).

    **Fail-open à chaque étage** : en-tête absent, mal formé, org inconnue, sub non
    membre, ou tout défaut interne → `None`, jamais un refus au handshake. La
    visibilité est de la gouvernance, pas une barrière (ADR 0031) ; la vraie garde
    d'appartenance reste jouée à CHAQUE appel (`run_org.pin_for_call`,
    `access.current_org`)."""
    headers = get_http_headers(include={"x-oto-org"})
    raw = (headers.get("x-oto-org") or "").strip()
    if not raw:
        return None
    try:
        org = int(raw)
    except ValueError:
        return None
    if org <= 0:
        return None
    try:
        membre = await run_in_threadpool(_org_membre, sub, org)
    except Exception as e:  # noqa: BLE001 — fail-open : un hoquet ne casse pas le handshake
        logger.warning("org de mission illisible pour la visibilité (sub=%s org=%s): %s",
                       sub, org, e)
        return None
    return org if membre else None


class UserDisabledToolsMiddleware(Middleware):
    """Applique la visibilité des tools du user à sa session MCP.

    Au handshake `initialize`, pour le `sub` JWT courant, on calcule l'ensemble
    effectif des tools à masquer = `user_disabled_tools` ∪ (masqués par défaut non
    activés) ∪ (connecteurs non activés/en pause) ∪ (gates admin/alpha) et on pose
    une visibility rule session-scopée. Le calcul + l'application vivent dans
    `session_visibility` (partagés avec le refresh à chaud post-`oto_use_org`,
    ADR 0009/0011/0015). fastmcp gère nativement filtrage `tools/list`, blocage
    `tools/call` et émission de `tools/list_changed`.

    Pas de sub identifiable (stdio local, discovery non-authentifié) → on ne filtre
    rien : la machine du dev a accès complet, le masquage par défaut ne concerne que
    la surface multi-user authentifiée.

    Une session ouverte avec l'en-tête `X-Oto-Org` (le runner de flotte, cf.
    `_org_de_mission`) voit la boîte de l'ORG DE LA MISSION, pas la maison mutable
    du compte porteur (#1058) — sans cet en-tête, comportement inchangé.
    """

    async def on_initialize(self, context, call_next):
        result = await call_next(context)
        try:
            sub = current_user_sub_from_token()
        # noqa: SILENT — dette déclarée : sub avalé, la requête devient anonyme sans dire pourquoi (#424, verdict C)
        except Exception:
            sub = None
        if not sub:
            return result
        ctx = context.fastmcp_context
        if ctx is None:
            logger.warning("fastmcp_context is None at on_initialize for sub=%s", sub)
            return result
        org = await _org_de_mission(sub)
        kwargs = {} if org is None else {"org": org}
        await apply_session_visibility(ctx, sub, **kwargs)
        return result
