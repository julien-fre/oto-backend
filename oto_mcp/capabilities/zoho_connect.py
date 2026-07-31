"""Capacité « connexion Zoho server-based » — démarrage + modes disponibles.

ADR 0042 §Convergence des surfaces : un verbe de plateforme naît **capacité**, pas
route REST écrite à la main. Ces deux verbes ont été écrits en REST pur le
2026-07-28 (motif hérité de folk/google, antérieur à la convergence) — ils
sont ramenés ici, et gagnent au passage une **face MCP** : l'agent peut fabriquer le
lien de consentement et le tendre à l'utilisateur, ce qui est précisément le geste
utile en conversation.

Ce qui RESTE en route écrite à la main (`api_routes_zoho.py`) : le **callback**.
Zoho y redirige le NAVIGATEUR — sans en-tête d'auth, avec une réponse 302 — ce
qu'un contrat de capacité (JSON + autz) ne peut pas exprimer. C'est déclaré comme
tel dans `test_rest_modules_are_capabilities.py`.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel

from .. import access, zoho_oauth
from ._authz import ORG_MEMBER
from ._types import AuthzDenied, Capability, ResolvedCtx, RestBinding
from .registry import CAPABILITIES


class ZohoConnectInput(BaseModel):
    op: Literal["start", "modes"] = "start"
    connector: str                      # zoho | zohodesk | zohoanalytics
    # Région du compte Zoho — REQUISE pour `start` : l'app OAuth et le token sont
    # liés à leur data center, un client `.eu` sur `accounts.zoho.com` est rejeté
    # par un `invalid_client` opaque. Indevinable, donc explicite.
    data_center: Optional[str] = None


def _guard(inp: ZohoConnectInput) -> None:
    if not zoho_oauth.supports(inp.connector):
        raise AuthzDenied(400, "unknown_zoho_connector",
                          f"« {inp.connector} » n'est pas un connecteur Zoho.")


def _modes(ctx: ResolvedCtx, inp: ZohoConnectInput) -> dict:
    """De quoi le front (ou l'agent) décide quoi afficher : le connecteur supporte-t-il
    le server-based, et une app est-elle DÉJÀ à disposition (la mienne, celle de mon
    équipe, de mon org ou de la plateforme — cascade habituelle) ?"""
    _guard(inp)
    return {
        "connector": inp.connector,
        "self_client": True,            # toujours disponible
        "server_based": True,
        "has_app": zoho_oauth.has_app(inp.connector, ctx.sub),
        "scopes": list(zoho_oauth.SCOPES[inp.connector]),
    }


def start_for(ctx: ResolvedCtx, connector: str, data_center: str) -> dict:
    """URL de consentement à ouvrir. L'app (client_id/secret) vient du COFFRE, via la
    cascade — jamais d'une variable d'env : chaque org peut apporter la sienne, et une
    clé plateforme donne le « un clic » à tous sans changer de code.

    Partagé avec le flux générique (`connector_flow`, déclaré dans tools/zoho.py) : il
    ne doit exister qu'UNE façon de démarrer un consentement Zoho, sinon les deux
    surfaces divergent — c'est exactement ce que la convergence cherche à éviter."""
    try:
        url = zoho_oauth.build_auth_url(
            ctx.sub, ctx.org_id or 0, connector, (data_center or "").lower(),
            app=zoho_oauth.app_fields(connector, ctx.sub))
    except zoho_oauth.ZohoOAuthError as e:
        raise AuthzDenied(400, "zoho_oauth_unavailable", str(e))
    return {"auth_url": url, "connector": connector}


def _start(ctx: ResolvedCtx, inp: ZohoConnectInput) -> dict:
    _guard(inp)
    return start_for(ctx, inp.connector, inp.data_center or "")


def _dispatch(ctx: ResolvedCtx, inp: ZohoConnectInput) -> dict:
    return _modes(ctx, inp) if inp.op == "modes" else _start(ctx, inp)


class ZohoVerbInput(BaseModel):
    """Entrée des faces REST — par-verbe, donc SANS `op` (le chemin le porte)."""
    connector: str
    data_center: Optional[str] = None


# Motif `platform.instructions` (ADR 0042) : UNE capacité op-aware pour le MCP +
# des capacités par-verbe pour REST, mêmes handlers. Les faces REST sont
# idiomatiques (un chemin = un verbe) et le MCP garde une surface consolidée
# (ADR 0047, un tool par objet métier).
CAPABILITIES += [
    Capability(
        key="me.zoho_connect",
        handler=_dispatch,
        Input=ZohoConnectInput,
        authz=ORG_MEMBER,
        mcp="oto_zoho_connect",
        rest=None,
        description=(
            "Connexion Zoho « server-based » (CRM / Desk / Analytics) : op='modes' "
            "dit si une app OAuth est déjà disponible et quels scopes oto demandera ; "
            "op='start' (avec `data_center` : eu, com, in, au, jp, ca) renvoie l'URL "
            "de consentement à OUVRIR dans un navigateur — au retour, le refresh token "
            "est rangé au coffre. Prérequis : client_id + client_secret de l'app Zoho "
            "posés sur la carte du connecteur (ou partagés par l'org)."),
    ),
    # `me.zoho_connect.start` a été RETIRÉE : elle n'existait que pour porter la face
    # REST `/api/zoho/oauth/start`, désormais servie par le chemin fixe
    # `/api/me/connectors/{name}/connect` (capacité `me.connector_connect`), via le
    # même `start_for`. Le démarrage garde sa face MCP sur `me.zoho_connect` op=start.
    # `me.zoho_connect.modes` a été RETIRÉE de même : son unique consommateur était le
    # widget nommé du dashboard, supprimé avec la généralisation. L'op reste servie par
    # `me.zoho_connect` (op='modes') côté MCP — un agent qui prépare une connexion a de
    # bonnes raisons de demander « une app est-elle déjà disponible ? ». Une surface REST
    # sans appelant, elle, est une dette qui se paie à chaque lecture du code.
]
