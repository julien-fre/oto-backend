"""Taxonomie d'erreurs de tools — classification + scrub partagés (D2, oto-backend#124).

Point unique qui CLASSE une exception de tool remontée par fastmcp (catégorie machine
`code` + `retryable`) et SCRUBBE son message pour l'agent. Réutilisé par :

- `sentry_setup` : décider si une erreur est un bug backend (report) ou gérée (drop) —
  les prédicats `_is_*` ci-dessous ;
- `ErrorEnvelopeMiddleware` (`middleware.py`) : rendre à l'agent une erreur au **contrat
  uniforme** `{code, retryable, hint}`, sans stacktrace / route interne / id technique
  (`classify` + `scrub`).

fastmcp emballe l'erreur d'un tool dans un `ToolError` → tous les prédicats **remontent
la chaîne** `__cause__`/`__context__` jusqu'à l'exception d'origine.
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Iterator, Optional

from fastmcp.exceptions import NotFoundError
from mcp.shared.exceptions import McpError
from mcp.types import INTERNAL_ERROR, INVALID_PARAMS, INVALID_REQUEST
from pydantic import ValidationError

# Codes JSON-RPC d'erreur d'ENTRÉE/CONFIG côté user (pendant natif d'un 4xx amont) :
# « pose ta clé », « connecte ton compte », param/org invalide. Levés
# intentionnellement par les tools/capacités, pas des bugs backend.
_USER_INPUT_CODES = {INVALID_PARAMS, INVALID_REQUEST}


def _chain(exc) -> Iterator[BaseException]:
    """L'exception et sa chaîne de causes (`__cause__` puis `__context__`), sans cycle."""
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        yield exc
        exc = exc.__cause__ or exc.__context__


def _upstream_status(exc) -> Optional[int]:
    """Code HTTP amont porté par UNE exception, sinon None.

    Couvre `UpstreamHTTPError` (oto-core, `.status_code`), `httpx`/`requests`
    HTTPError (`.response.status_code`) et les erreurs connecteur typées maison
    (`.status`, ex. `NinjaError`).
    """
    for attr in ("status_code", "status"):
        v = getattr(exc, attr, None)
        if isinstance(v, int):
            return v
    v = getattr(getattr(exc, "response", None), "status_code", None)
    return v if isinstance(v, int) else None


def _upstream_retryable(exc) -> Optional[bool]:
    """Sémantique de réessai DÉCLARÉE par le connecteur amont, sinon None.

    Le statut HTTP seul ment chez certains fournisseurs : Hunter renvoie 429 pour
    « crédits du plan épuisés » (rien à réessayer) et 403 pour la limite de débit
    (transitoire) — l'inverse de la convention. Le module connecteur est le seul à
    savoir ; il le dit via un attribut `retryable` sur son exception, la taxonomie
    l'honore. Seam générique, spécificité DANS le module (jamais un `if hunter`).
    """
    for e in _chain(exc):
        v = getattr(e, "retryable", None)
        if isinstance(v, bool):
            return v
    return None


def upstream_status_in_chain(exc) -> Optional[int]:
    """Premier code HTTP amont trouvé en remontant la chaîne, sinon None."""
    for e in _chain(exc):
        sc = _upstream_status(e)
        if sc is not None:
            return sc
    return None


def _is_managed_connector_error(exc) -> bool:
    """True si la chaîne porte un refus client amont (4xx) — erreur de connecteur
    gérée, pas un bug backend."""
    for e in _chain(exc):
        sc = _upstream_status(e)
        if sc is not None and 400 <= sc < 500:
            return True
    return False


def _is_user_input_error(exc) -> bool:
    """True si la chaîne porte une `McpError` de code d'entrée/config user
    (INVALID_PARAMS / INVALID_REQUEST) — refus explicite, pas un bug backend."""
    for e in _chain(exc):
        if isinstance(e, McpError) and getattr(e.error, "code", None) in _USER_INPUT_CODES:
            return True
    return False


def _is_arg_validation_error(exc) -> bool:
    """True si la chaîne porte une `ValidationError` pydantic (args rejetés)."""
    for e in _chain(exc):
        if isinstance(e, ValidationError):
            return True
    return False


def _arg_error_message(exc) -> str:
    """« Arguments invalides » qui NOMME la clé fautive — parité avec la face REST.

    La face REST refuse un champ inconnu en nommant l'excédent ET les attendus
    (`_rest_adapter`, 400 `unknown_fields`) ; la face MCP disait « vérifie les paramètres
    de l'outil », ce qui laisse deviner LEQUEL. Mesuré le 14/08 : deux formes fautives
    (`{op:"draft"}`, `{action:"draft"}`) refusées sans nommer la clé, puis l'appel
    recomposé à neuf — en oubliant le paramètre cherché depuis quatre essais.

    La `ValidationError` pydantic porte tout : `loc` = la clé, `type` = la nature du
    refus (`extra_forbidden` = clé inconnue, `missing` = clé requise absente)."""
    err = next((e for e in _chain(exc) if isinstance(e, ValidationError)), None)
    if err is None:
        return "Arguments invalides — vérifie les paramètres de l'outil."
    inconnus, manquants, autres = [], [], []
    try:
        for d in err.errors():
            cle = ".".join(str(p) for p in (d.get("loc") or ())) or "?"
            kind = d.get("type") or ""
            if kind == "extra_forbidden":
                inconnus.append(cle)
            elif kind.startswith("missing"):
                manquants.append(cle)
            else:
                autres.append(f"{cle} ({d.get('msg') or kind})")
    except Exception:      # forme pydantic inattendue : on ne casse pas le message
        return "Arguments invalides — vérifie les paramètres de l'outil."
    bouts = []
    if inconnus:
        bouts.append(f"champ(s) non reconnu(s) : {', '.join(inconnus)}")
    if manquants:
        bouts.append(f"champ(s) requis absent(s) : {', '.join(manquants)}")
    if autres:
        bouts.append(f"valeur(s) refusée(s) : {'; '.join(autres)}")
    if not bouts:
        return "Arguments invalides — vérifie les paramètres de l'outil."
    return ("Arguments invalides — " + " · ".join(bouts)
            + ". Le schéma exact : oto_tool_schema(name=…).")


def _is_oauth_exchange_refused(exc) -> bool:
    """True si la chaîne porte un REFUS du serveur d'autorisation (`OAuthExchangeRefused`).

    Le refus décrit la Connected App ou le grant de l'UTILISATEUR — code expiré, scopes
    absents, callback divergente, restriction IP — jamais notre code. La chaîne suffit :
    chaque connecteur re-lève son message traduit `from e`, donc la cause d'origine reste
    visible ici sans que la taxonomie ait à connaître un seul connecteur par son nom.

    Import local : `oauth_flow` importe la config au chargement, et ce module est importé
    très tôt par le middleware Sentry."""
    try:
        from .oauth_flow import OAuthExchangeRefused
    except Exception:
        return False
    return any(isinstance(e, OAuthExchangeRefused) for e in _chain(exc))


def _is_upstream_managed_error(exc) -> bool:
    """True si la chaîne porte une erreur de connecteur amont d'INPUT/config SANS
    statut HTTP (oto-backend#90) : facette LinkedIn introuvable, compte non connecté,
    param non supporté, identity_mismatch… `UnipileError` (oto-core) modélise ça — un
    refus d'entrée user, jamais un bug backend. Les 4xx portent déjà `.status_code`
    (couverts par `_is_managed_connector_error`) ; les erreurs RÉSEAU (message «
    réseau ») restent reportées (transitoire, potentielle panne, hors input).

    Reconnu par NOM de classe (`UnipileError`) pour ne pas coupler la taxonomie à
    l'import d'oto-core (le module doit rester importable seul, sans cycle)."""
    for e in _chain(exc):
        if type(e).__name__ == "UnipileError" and getattr(e, "status_code", None) is None:
            if "réseau" not in str(e).lower():
                return True
    return False


# Déconnexion du CLIENT pendant qu'on lui répondait. Le client MCP ferme le POST
# (onglet fermé, conversation abandonnée, timeout côté claude.ai) et le serveur écrit
# dans un stream déjà mort. Rien n'a mal tourné CHEZ NOUS : il n'y a plus personne au
# bout du fil. Deux formes du MÊME incident, chaînées dans le même event :
#   - `ClosedResourceError` (anyio) quand le SDK MCP pousse dans le stream fermé ;
#   - `RuntimeError: Unexpected ASGI message … after response already completed`
#     quand uvicorn refuse les headers d'une réponse déjà terminée.
# 38 événements Sentry en 3 semaines, aucun actionnable.
_CLIENT_DISCONNECT_TYPES = {
    "ClosedResourceError", "BrokenResourceError", "EndOfStream", "ClientDisconnect",
}
_ASGI_AFTER_COMPLETE = "after response already completed"


def _is_client_disconnect(exc) -> bool:
    """True si la chaîne porte une déconnexion client en cours de réponse.

    Reconnu par NOM de classe (comme `_is_upstream_managed_error`) : la taxonomie ne
    doit pas importer anyio ni le SDK MCP pour rester importable seule.

    ⚠️ VOLONTAIREMENT hors de `_is_expected_error` : ce prédicat ne répond pas à la
    même question. `_is_expected_error` = « faut-il en tenir l'agent responsable ? »,
    et sert aussi à `ErrorEnvelopeMiddleware` pour composer la réponse RENDUE à
    l'agent. Ici, il n'y a plus d'agent à qui répondre — la seule décision qui reste
    est « faut-il réveiller quelqu'un ? », qui est une question Sentry. D'où l'appel
    séparé dans `_before_send`.
    """
    for e in _chain(exc):
        if type(e).__name__ in _CLIENT_DISCONNECT_TYPES:
            return True
        if isinstance(e, RuntimeError) and _ASGI_AFTER_COMPLETE in str(e):
            return True
    return False


_UNKNOWN_TOOL = re.compile(r"Unknown tool: '([^']+)'")


def _unknown_tool_name(exc) -> Optional[str]:
    """Nom de l'outil si la chaîne porte le refus de dispatch fastmcp « Unknown
    tool » — l'outil n'est pas monté dans CETTE session (connecteur non installé,
    sélection ADR 0019/0050, ou tool masqué). La visibilité filtre `tools/list`,
    pas `tools/call` : un agent peut toujours TENTER un nom (il le déduit d'un
    ref d'instance, du catalogue, d'une conversation) → le refus serveur doit
    être actionnable, pas un 500 opaque (vécu 2026-07-16, signaux #224/#225 :
    deux agents ont conclu à un bug credential). None sinon."""
    for e in _chain(exc):
        if isinstance(e, NotFoundError):
            m = _UNKNOWN_TOOL.search(str(e))
            if m:
                return m.group(1)
    return None


def _connector_of_tool(name: str) -> Optional[str]:
    """Connecteur propriétaire du namespace de `name`, si le registre le connaît.
    Import paresseux — la taxonomie reste importable seule (et sans cycle)."""
    try:
        from . import providers
        from .tool_visibility import namespace_of
        con = providers.connector_for_namespace(namespace_of(name))
        return con.name if con else None
    except Exception:
        return None


def _surviving_siblings(name: str) -> Optional[list[str]]:
    """Les outils du MÊME namespace qui existent encore — quand `name`, lui, n'existe pas.

    `None` = on ne peut rien affirmer : le nom EST au registre (il n'est donc pas retiré,
    juste non monté), ou le registre n'est pas réchauffé (hors serveur il rend une liste
    vide — en conclure « l'outil n'existe plus » ferait mentir CHAQUE message), ou son
    namespace n'a plus rien à proposer.

    DÉRIVÉ, jamais une table de renommages à tenir : une table serait à nourrir à chaque
    consolidation, donc périmée au premier oubli — et c'est exactement ce genre d'oubli
    qui produit le message trompeur qu'on ferme ici."""
    try:
        from . import tool_registry
        from .tool_visibility import namespace_of
        connus = set(tool_registry.boot_tool_names())
        if not connus or name in connus:
            return None
        ns = namespace_of(name)
        voisins = sorted(t for t in connus if namespace_of(t) == ns)
        return voisins or None
    except Exception:
        return None


def _is_expected_error(exc) -> bool:
    """Erreur gérée, à NE PAS reporter à Sentry : 4xx amont OU refus d'entrée/config
    user OU args rejetés OU refus d'échange OAuth OU outil non monté (condition de
    toolbox, pas un bug).
    Les vraies exceptions code (5xx, KeyError, InvalidTag…) restent reportées."""
    return (_is_managed_connector_error(exc)
            or _is_user_input_error(exc)
            or _is_arg_validation_error(exc)
            or _is_upstream_managed_error(exc)
            or _is_oauth_exchange_refused(exc)
            or _unknown_tool_name(exc) is not None)


# --- Enveloppe d'erreur rendue à l'agent (D2) --------------------------------

@dataclass
class ErrorInfo:
    """Erreur normalisée présentée à l'agent. `code` = catégorie machine ;
    `retryable` = l'agent peut réessayer tel quel ; `message` scrubbé (zéro
    stacktrace/route/id) ; `hint` = quoi faire, quand dérivable."""
    code: str
    retryable: bool
    message: str
    hint: Optional[str] = None
    # Connecteur en cause, quand il est dérivable du nom de l'outil (`tool_not_mounted`).
    # Le classifieur reste PUR (il ne voit qu'une exception) : c'est l'enveloppe, qui a
    # le contexte de session, qui s'en sert pour enrichir le hint (instances à portée).
    connector: Optional[str] = None


# net::ERR_* (erreurs Chromium crues) — remplacent tout le message (aucune info utile).
_NET_ERR = re.compile(r"net::ERR_[A-Z_]+")
# Routes internes (« Cannot GET /api/v1/… », chemins d'API) — fuite de topologie serveur.
_ROUTE = re.compile(r"(?:Cannot\s+(?:GET|POST|PUT|DELETE|PATCH)\s+)?/(?:api|v\d)[\w/.\-]*", re.I)
# Jetons techniques longs (account_id, uuid) ≥ 20 chars — fuite d'identifiants internes.
_LONG_ID = re.compile(r"\b[A-Za-z0-9][A-Za-z0-9_\-]{19,}\b")
_TIMEOUT_MARKERS = ("timeout", "timed out", "délai d'attente", "read timed out")


def scrub(message: str) -> str:
    """Retire d'un message d'erreur les fuites internes (net::ERR_*, routes, ids
    techniques). Best-effort — appliqué aux messages amont, jamais aux `McpError`
    qu'on a nous-mêmes curées."""
    if not message:
        return ""
    if _NET_ERR.search(message):
        return "Échec réseau amont (hôte non résolu ou service injoignable)."
    message = _ROUTE.sub("[route interne]", message)
    message = _LONG_ID.sub("[id]", message)
    return message.strip()


def _first_upstream_message(exc) -> str:
    """Str de la 1ʳᵉ exception de la chaîne portant un statut amont (pour scrub)."""
    for e in _chain(exc):
        if _upstream_status(e) is not None:
            return str(e)
    return str(exc)


def _looks_like_timeout(exc) -> bool:
    for e in _chain(exc):
        if isinstance(e, (asyncio.TimeoutError, TimeoutError)):
            return True
        if any(m in str(e).lower() for m in _TIMEOUT_MARKERS):
            return True
    return False


def classify(exc) -> ErrorInfo:
    """Classe une exception de tool en `ErrorInfo` au contrat uniforme.

    Ordre : (1) `McpError` qu'on a levée (message curé conservé) ; (2) args pydantic
    rejetés ; (3) statut HTTP amont (timeout/rate-limit/not-found/authz/4xx/5xx) ;
    (4) timeout non typé ; (5) reste = interne — **aucun écho du `str(exc)`** (anti-fuite).
    """
    # (1) McpError curée par un tool/capacité : message déjà agent-facing.
    for e in _chain(exc):
        if isinstance(e, McpError):
            jcode = getattr(e.error, "code", None)
            msg = (getattr(e.error, "message", None) or "").strip()
            if jcode in _USER_INPUT_CODES:
                return ErrorInfo("invalid_input", False, msg or "Requête invalide.")
            # McpError levée avec un autre code (rare) : on garde le texte curé,
            # traité comme interne non-retryable.
            return ErrorInfo("internal", False, msg or "Erreur interne du serveur.")

    # (2) Arguments rejetés (le LLM a passé de mauvais paramètres) — en NOMMANT la clé.
    if _is_arg_validation_error(exc):
        return ErrorInfo("invalid_input", False, _arg_error_message(exc))

    # (2b) Refus de dispatch fastmcp : l'outil est enregistré côté serveur mais pas
    # monté dans CETTE session (connecteur non installé / masqué). Rendu actionnable
    # avec les deux voies : `oto_call` (immédiat, sans installation — ADR 0036) ou
    # l'installation du connecteur. Sans ça : « Erreur interne du serveur ».
    name = _unknown_tool_name(exc)
    if name:
        # Un nom RETIRÉ n'est pas un connecteur absent — et le confondre envoie chercher
        # un problème de montage qui n'existe pas. Vécu le 14/08 : `gmail_search`,
        # supprimé par la consolidation google (33→13 tools), répondait « le connecteur
        # google n'est pas installé dans ta toolbox » alors que google ÉTAIT installé et
        # que les verbes du nom disparu vivaient dans `gmail_message`. La session a
        # cherché un demi-montage inexistant.
        voisins = _surviving_siblings(name)
        if voisins is not None:
            return ErrorInfo(
                "unknown_tool", False,
                f"L'outil `{name}` n'existe plus (nom retiré ou jamais existé). "
                f"Les outils de ce domaine aujourd'hui : {', '.join(voisins)}.",
                "ses verbes vivent probablement sous l'un d'eux, en paramètre `op` — "
                f"lis son schéma avec oto_tool_schema(name='{voisins[0]}')")
        con = _connector_of_tool(name)
        if con:
            return ErrorInfo(
                "tool_not_mounted", False,
                f"L'outil `{name}` n'est pas monté dans ta session : le connecteur "
                f"`{con}` n'est pas installé dans ta toolbox (ou l'outil y est masqué).",
                f"appelle-le immédiatement via oto_call(name='{name}', args={{…}}) ; "
                f"ou installe le connecteur — oto_connector(op='select', name='{con}') "
                f"— et ouvre une nouvelle conversation pour le voir listé",
                connector=con)
        return ErrorInfo("unknown_tool", False, f"Outil `{name}` inconnu.",
                         "vérifie le nom exact avec oto_list_my_tools")

    # (3) Statut HTTP amont.
    sc = upstream_status_in_chain(exc)
    if sc is not None:
        raw = scrub(_first_upstream_message(exc))
        if sc in (408, 504):
            return ErrorInfo("upstream_timeout", True,
                             "Délai d'attente dépassé côté service amont.",
                             "réessaie dans un instant")
        if sc == 429:
            # Le connecteur peut démentir le statut (Hunter : 429 = crédits du plan
            # épuisés, pas un débit trop rapide) → son verdict prime, et son message
            # dit quoi faire à la place.
            declared = _upstream_retryable(exc)
            retryable = True if declared is None else declared
            return ErrorInfo("rate_limited" if retryable else "quota_exhausted",
                             retryable,
                             raw or "Trop de requêtes côté service amont.",
                             "réessaie après une courte pause" if retryable
                             else "inutile de réessayer : change de source ou fais "
                                  "monter le plan du connecteur")
        if sc == 404:
            return ErrorInfo("not_found", False,
                             raw or "Ressource introuvable côté service amont.")
        if sc in (401, 403):
            return ErrorInfo("not_authorized", False,
                             raw or "Accès refusé par le service amont.",
                             "vérifie que le connecteur est connecté et autorisé")
        if 400 <= sc < 500:
            return ErrorInfo("upstream_4xx", False,
                             raw or f"Requête refusée par le service amont ({sc}).")
        if 500 <= sc < 600:
            return ErrorInfo("upstream_5xx", True,
                             f"Le service amont a rencontré une erreur ({sc}).",
                             "réessaie plus tard")

    # (4) Timeout non porté par un statut.
    if _looks_like_timeout(exc):
        return ErrorInfo("upstream_timeout", True,
                         "Délai d'attente dépassé.", "réessaie dans un instant")

    # (4b) Erreur connecteur amont GÉRÉE sans statut HTTP (UnipileError d'input/config,
    # #90) : son message est agent-utile (« Facette introuvable… », « compte non
    # connecté ») → on l'écho TEL QUEL plutôt qu'un « Erreur interne » opaque.
    #
    # PAS de `scrub` ici (retiré le 2026-07-28, signal #282) : ces messages sont rédigés
    # PAR NOUS dans oto-core, pas relayés de l'amont — c'est exactement le cas que la
    # docstring de `scrub` exclut (« jamais aux McpError qu'on a nous-mêmes curées »).
    # Les scrubber détruisait leur seule valeur : `identity_mismatch` compare l'id
    # DEMANDÉ et l'id REÇU, tous deux des identifiants LinkedIn publics de >20 caractères
    # → `_LONG_ID` rendait « profil demandé '[id]', reçu '[id]' », c'est-à-dire un message
    # qui dit qu'il y a une différence sans jamais dire laquelle. Les messages VRAIMENT
    # amont gardent leur scrub : ils passent par le chemin (3), au-dessus.
    if _is_upstream_managed_error(exc):
        for e in _chain(exc):
            if type(e).__name__ == "UnipileError":
                return ErrorInfo("invalid_input", False,
                                 str(e) or "Requête refusée par le service amont.")

    # (5) Reste = bug/erreur interne : PAS d'écho de str(exc) (anti-fuite).
    return ErrorInfo("internal", False, "Erreur interne du serveur.")


def jsonrpc_code(info: ErrorInfo) -> int:
    """Code JSON-RPC de la `McpError` rendue : INVALID_PARAMS pour un refus d'entrée
    (arguments, outil non monté/inconnu — l'agent doit changer son APPEL, pas
    réessayer), INTERNAL_ERROR sinon (le discriminant fin vit dans `data.oto.code`)."""
    return (INVALID_PARAMS
            if info.code in ("invalid_input", "tool_not_mounted", "unknown_tool")
            else INTERNAL_ERROR)
