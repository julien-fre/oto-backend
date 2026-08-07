"""Capacité « app OAuth de l'éditeur » — poser l'app d'oto chez un fournisseur.

Pourquoi ça existe : sur un connecteur à consentement (Zoho & co), l'utilisateur devait
créer LUI-MÊME une app OAuth chez le fournisseur (mode « Self Client ») avant de pouvoir
connecter quoi que ce soit — une console développeur à traverser, des scopes à cocher à
la main, et trois incidents de scopes mal choisis (#190, #202, Desk articles-only). Avec
une app d'éditeur posée ici, il ne reste que le geste qui compte : consentir.

**Ce qui est posé n'est pas une clé d'accès.** `client_id`/`client_secret` identifient
l'ÉDITEUR qui demande l'accès ; les données, elles, ne s'ouvrent qu'avec le
`refresh_token` né du consentement de l'utilisateur, rangé à SON nom. L'invariant qui
garantit cette séparation est documenté dans `credentials_store` §app d'éditeur.

**REST seulement, super admin** : la face MCP est délibérément absente — un secret brut
en argument d'outil transiterait par le contexte du modèle (règle du repo, cf. la pose
des secrets d'org).
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from .. import connector_flow, credentials_store
from ._authz import SUPER_ADMIN
from ._types import AuthzDenied, Capability, ResolvedCtx, RestBinding
from .registry import CAPABILITIES


def _guard(connector: str) -> str:
    """Le connecteur doit avoir un flux de consentement — poser une app d'éditeur sur
    un connecteur à clé API n'aurait aucun sens (rien ne la consommerait)."""
    name = (connector or "").strip()
    if not connector_flow.supports(name):
        raise AuthzDenied(400, "no_consent_flow",
                          f"« {name} » n'a pas de flux de connexion par consentement : "
                          "une app d'éditeur n'y servirait à rien.")
    return name


class ListInput(BaseModel):
    connector: Optional[str] = None


class SetInput(BaseModel):
    connector: str
    # La région fait partie de la CLÉ, pas d'un réglage : une app OAuth est enregistrée
    # dans son data center et rejetée par les autres.
    data_center: str
    client_id: str
    client_secret: str


class DeleteInput(BaseModel):
    connector: str
    data_center: str


def _list(ctx: ResolvedCtx, inp: ListInput) -> dict:  # noqa: ARG001
    return {"apps": credentials_store.list_editor_apps(inp.connector or None)}


def _set(ctx: ResolvedCtx, inp: SetInput) -> dict:
    name = _guard(inp.connector)
    try:
        credentials_store.set_editor_app(
            name, inp.data_center,
            {"client_id": inp.client_id.strip(), "client_secret": inp.client_secret.strip()},
            set_by=ctx.sub)
    except ValueError as e:
        raise AuthzDenied(400, "invalid_editor_app", str(e))
    return {"connector": name, "data_center": inp.data_center.strip().lower(),
            "callback_url": connector_flow.callback_url(name)}


def _delete(ctx: ResolvedCtx, inp: DeleteInput) -> dict:  # noqa: ARG001
    if not credentials_store.clear_editor_app(inp.connector, inp.data_center):
        raise AuthzDenied(404, "unknown_editor_app",
                          "aucune app d'éditeur pour ce connecteur et cette région.")
    return {"ok": True, "connector": inp.connector,
            "data_center": inp.data_center.strip().lower()}


CAPABILITIES += [
    Capability(
        key="platform.editor_app.list", handler=_list, Input=ListInput,
        authz=SUPER_ADMIN, mcp=None,
        rest=RestBinding("GET", "/api/admin/editor-apps"),
        description="Apps OAuth d'éditeur posées (connecteur × région), sans secret.",
    ),
    Capability(
        key="platform.editor_app.set", handler=_set, Input=SetInput,
        authz=SUPER_ADMIN, mcp=None,
        rest=RestBinding("POST", "/api/admin/editor-apps"),
        description="Pose/rote l'app OAuth d'oto pour un connecteur et une région.",
    ),
    Capability(
        key="platform.editor_app.delete", handler=_delete, Input=DeleteInput,
        authz=SUPER_ADMIN, mcp=None,
        rest=RestBinding("DELETE", "/api/admin/editor-apps/{connector}/{data_center}"),
        description="Retire l'app OAuth d'éditeur d'un connecteur pour une région.",
    ),
]
