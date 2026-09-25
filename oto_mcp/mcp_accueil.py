"""Le 401 de `/mcp` dit ce qu'est oto et comment s'y connecter (oto-backend#1071).

Un agent qui ouvrait `https://mcp.oto.cx/mcp` sans jeton recevait un **401 au corps
vide**, même sur `initialize` : il n'en tirait que la métadonnée OAuth, ni ce qu'est le
service, ni le geste pour s'y connecter. Ce 401 naît dans `RequireAuthMiddleware` de
fastmcp (`fastmcp/server/auth/middleware.py`), que `http_app()` pose sur la route
`/mcp` : sans en-tête `Authorization`, corps vide (`_send_missing_auth`) ; jeton
invalide, le JSON `{"error", "error_description"}` (`_send_auth_error`). La classe est
construite par fastmcp, pas par nous : le seul point où l'on tient la réponse est un
middleware ASGI autour de l'app, comme `TenantChallengeMiddleware` pour l'en-tête.

Ce qui est garanti, parce que c'est la négociation OAuth d'un client conforme :
- **le statut et `WWW-Authenticate` ne changent pas d'un octet**, ni les métadonnées de
  découverte (`/.well-known/*`), que ce middleware ne voit pas (autre chemin) ;
- seul le corps change : vide → texte (`text/plain`) ; JSON d'erreur → le même JSON,
  augmenté d'un champ `help` portant le texte. `content-length` suit ;
- un 401 ailleurs que sur `/mcp` (la face REST `/api/*` a ses propres 401) et tout autre
  statut passent tels quels.

**Seulement sur l'hôte principal d'oto** (`mcp.oto.cx`, et `mcp.oto.ninja` en
préproduction). Le texte est de NOTRE marque : il renvoie vers `oto.cx/llms.txt`, notre
plugin et notre CLI. Une instance servie ailleurs (ADR 0070), l'hôte d'un tenant
(ADR 0052), l'endpoint d'org `<slug>--mcp.<D>` ou un projet publié en portée org
gardent le 401 d'avant, corps compris — le choix le plus prudent : se taire plutôt que
de nommer oto chez quelqu'un d'autre. La liste est fermée et échoue fermée (un hôte
inconnu ne reçoit rien), même règle que `tools/apollo.py` pour nos hôtes.
"""
from __future__ import annotations

import json

#: Les hôtes où le texte est servi : les NÔTRES, jamais déduits de `OTO_MCP_PUBLIC_URL`
#: (une instance tierce la déclare aussi, et ce texte n'est pas le sien).
HOTES_OTO = frozenset({"mcp.oto.cx", "mcp.oto.ninja"})

LLMS_TXT = "https://oto.cx/llms.txt"

_CHEMINS = frozenset({"/mcp", "/mcp/"})


def texte_d_accueil(hote: str) -> str:
    """Le texte servi sur l'hôte `hote` (l'un de `HOTES_OTO`) — dix lignes au plus."""
    return (
        "oto is a toolbox for AI agents: one MCP server for B2B prospecting, French "
        "company data, CRM, email, messaging and documents.\n"
        f"This endpoint requires an OAuth token. Read this first: {LLMS_TXT}\n"
        "To connect:\n"
        f"- add https://{hote}/mcp as a remote MCP connector (your client runs the "
        "OAuth sign-in);\n"
        "- or, in Claude Code: claude plugin marketplace add otomata-tech/oto-plugin"
        " && claude plugin install oto@otomata-oto\n"
        "- or the CLI: pipx install oto-cli\n"
    )


def _hote(scope) -> str:
    for k, v in scope.get("headers") or ():
        if k == b"host":
            return v.decode("latin-1").split(":")[0].strip().lower()
    return ""


def _corps_enrichi(corps: bytes, type_: bytes, texte: str) -> "tuple[bytes, bytes] | None":
    """Le nouveau `(corps, content-type)`, ou None pour laisser la réponse intacte."""
    if not corps.strip():
        return texte.encode("utf-8"), b"text/plain; charset=utf-8"
    if not type_.lower().startswith(b"application/json"):
        return None
    doc = json.loads(corps)
    if not isinstance(doc, dict):
        return None
    doc["help"] = texte
    return json.dumps(doc).encode("utf-8"), type_


class McpAccueilMiddleware:
    """Pose le texte d'accueil sur le 401 de `/mcp`, hôte principal d'oto seulement."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or scope.get("path") not in _CHEMINS:
            await self.app(scope, receive, send)
            return
        hote = _hote(scope)
        if hote not in HOTES_OTO:
            await self.app(scope, receive, send)
            return

        debut: dict | None = None
        morceaux: list[bytes] = []

        async def _send(message):
            nonlocal debut
            if message["type"] == "http.response.start":
                if int(message.get("status") or 0) == 401:
                    debut = message          # retenu jusqu'au corps complet
                    return
                await send(message)
                return
            if debut is None or message["type"] != "http.response.body":
                await send(message)
                return
            morceaux.append(message.get("body", b""))
            if message.get("more_body", False):
                return
            await self._servir(debut, b"".join(morceaux), hote, send)

        await self.app(scope, receive, _send)

    @staticmethod
    async def _servir(debut: dict, corps: bytes, hote: str, send) -> None:
        entetes = list(debut.get("headers") or ())
        type_ = next((v for k, v in entetes if k.lower() == b"content-type"), b"")
        nouveau = _corps_enrichi(corps, type_, texte_d_accueil(hote))
        if nouveau is not None:
            corps, type_ = nouveau
            entetes = [(k, v) for k, v in entetes
                       if k.lower() not in (b"content-length", b"content-type")]
            entetes += [(b"content-type", type_),
                        (b"content-length", str(len(corps)).encode("latin-1"))]
            debut = {**debut, "headers": entetes}
        await send(debut)
        await send({"type": "http.response.body", "body": corps})
