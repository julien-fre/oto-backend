"""`AccountSuspendedMiddleware` — un compte en pause, ou d'un tenant désactivé, n'atteint
aucun outil. La garde est `garde_identite.refus` ; ce middleware en est la porte MCP."""
from __future__ import annotations

import logging

from fastmcp.server.middleware import Middleware
from starlette.concurrency import run_in_threadpool

from .. import garde_identite
from ..auth.hooks import current_user_sub_from_token
from ..db import CompteEnPause, TenantDesactive
from ..mcp_errors import McpError

logger = logging.getLogger(__name__)


class AccountSuspendedMiddleware(Middleware):
    """Refuse TOUTE requête MCP d'un compte mis en pause (`users.suspended_at`) ou
    qualifié sous un tenant désactivé (`tenants.disabled_at`, oto-backend#1165).

    ⚠️ **Sur le sub CANONIQUE** (après le drain d'alias de
    `current_user_sub_from_token`) : c'est ce qui coupe aussi un jeton de NOTRE annuaire
    dont l'ancien identifiant est redirigé vers un compte du tenant désactivé.

    **Sur `on_request`, pas sur `on_call_tool`.** Un compte neutralisé ne doit pas
    seulement être empêché d'agir : le handshake injecte les instructions de son org
    (plateforme, org, équipe — les instructions que l'organisation lui adresse),
    et `tools/list` révèle la boîte à outils qu'on lui a composée. Garder l'appel
    seul laisserait un compte sorti continuer à LIRE ce qu'on lui a retiré le droit
    de faire. `on_request` couvre `initialize`, `tools/list`, `tools/call`,
    `resources/*` et `prompts/*` d'un seul geste.

    **Le refus est un `McpError`, pas un résultat vide ni un outil masqué.** La
    visibilité ne garde rien (ADR 0031/0066-R4) ; et un agent qui reçoit une liste
    vide conclut que la plateforme est cassée, pas que son compte est en pause. Le
    message dit ce qui se passe et ce qui débloque — c'est le seul texte que la
    personne ou son agent verra.

    **Position : juste sous le renommage d'outils, au-dessus de tout le reste.** Rien
    de la chaîne (contexte d'appel, rédaction, visibilité, journal) n'a de raison de
    tourner pour une requête qu'on va refuser, et le refus n'a besoin d'aucun de leurs
    apports : il ne lit ni le nom de l'outil, ni l'org active, ni les arguments.

    ⚠️ **Pas de cache.** Une pause doit mordre à la requête suivante, pas à la
    prochaine expiration d'un cache : c'est toute la différence entre neutraliser et
    demander poliment. Le coût est une lecture sur clé primaire par requête MCP.

    Pas de sub identifiable (dev local sans Logto, discovery non authentifié) : on ne
    refuse rien — il n'y a pas de compte à neutraliser, et fermer là couperait le run
    local de tout le monde sans qu'aucune pause n'ait été posée.
    """

    async def on_request(self, context, call_next):
        try:
            sub = current_user_sub_from_token()
        except (CompteEnPause, TenantDesactive) as refus:
            # L'ANCIEN identifiant d'un compte en pause : sans ligne à lui (la fusion
            # l'a supprimée), `refus` ci-dessous ne le verrait pas — c'est `upsert_user`
            # qui reconnaît que son alias mène à un compte neutralisé. Même refus que
            # la face REST (`api.base._authenticate`), au contrat de ce middleware.
            # Même levée pour la NAISSANCE d'un compte d'un tenant désactivé (#1165).
            raise McpError(_erreur(refus.code, str(refus))) from refus
        # Tout autre échec d'identité MONTE (journalisé par le seam, #464) : le
        # rattraper laissait passer la requête ANONYME, donc sans la garde de pause.
        if sub:
            refus = await run_in_threadpool(garde_identite.refus, sub)
            if refus:
                raise McpError(_erreur(*refus))
        return await call_next(context)


def _erreur(code: str, message: str):
    """L'objet d'erreur du protocole, construit sans dépendre du nom de sa classe.

    `McpError` attend un `ErrorData` du SDK amont ; ce SDK a déjà renommé une classe
    sous nos pieds (cf. `mcp_errors`), donc on ne l'importe pas au chargement du
    module — le nommer ici, au plus près de l'usage, garde un seul point à corriger.
    Code `-32003` : la famille « requête refusée par le serveur » du JSON-RPC."""
    from mcp.types import ErrorData
    return ErrorData(code=-32003, message=message,
                     data={"code": code, "retryable": False})
