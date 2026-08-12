"""Export du journal d'audit org-scopé (ADR 0009 ; oto-backend#67).

Le trust center public annonce un « journal d'audit de tous les appels d'outils ».
Ce journal existe (`tool_calls`, via le calllog) mais n'était lisible que par un
opérateur plateforme (`/api/admin/monitoring/*`). Ici on l'ouvre à un **org_admin**
pour SON org — preuve de conformité (RGPD art. 28, ISO 42001), revue, dossier client.

Surface : capacité **REST-only** `GET /api/orgs/{id}/audit-log/export`, gatée
`ORG_ADMIN_OF`. Retourne du JSON structuré (`{org_id, count, calls[]}`) — le bouton
« exporter CSV » du dashboard sérialise ce JSON côté client (l'adaptateur REST des
capacités ne produit que du JSON ; pas de stream text/csv ici).

Org-scoping = **exact** : on filtre `tool_calls.org_id` (l'org sous laquelle l'appel a
été émis, stampée par le seam `current_org` à l'insert) — PAS l'appartenance des
membres (un membre de N orgs ne pollue donc pas l'export). ⚠ Les appels antérieurs à la
colonne `org_id` (NULL) n'apparaissent dans aucun export — non reconstructibles.
Jamais d'args ni de secret (garantie calllog) — colonnes : horodatage, user (sub/email),
outil, namespace, durée, ok, erreur.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, field_validator

from .. import db
from ..tool_visibility import namespace_of
from ._authz import ORG_ADMIN_OF
from ._types import cap_limit, Capability, ResolvedCtx, RestBinding
from .registry import CAPABILITIES

_ID = {"id": "org_id"}


class AuditCall(BaseModel):
    """Une ligne d'audit. Jamais d'arguments ni de secret (garantie calllog).
    `created_at` sort en `"YYYY-MM-DD HH:MM:SS"` — pas d'ISO, pas d'offset (le tzinfo
    est retiré par le row factory, pas converti), alors que `since`/`until` en entrée
    sont, eux, de l'ISO."""
    id: int
    created_at: str
    sub: Optional[str] = None
    # None si aucun compte `users` ne correspond au sub (compte machine, user purgé).
    email: Optional[str] = None
    tool: Optional[str] = None
    ok: Optional[bool] = None
    error: Optional[str] = None
    duration_ms: Optional[int] = None
    # Dérivé du nom d'outil (1er token avant `_`), None si `tool` est vide.
    namespace: Optional[str] = None


class AuditExport(BaseModel):
    """Journal d'audit d'une org — pièce de conformité (RGPD art. 28, ISO 42001).
    Trois limites à connaître AVANT de le présenter comme exhaustif :

    ⚠️ **`count` est la taille de la PAGE, pas la population.** C'est `len(calls)`
    après troncature (`limit`, défaut 1000, plafonné à 5000 côté store). Un export de
    `count: 1000` ne dit pas si 1000 ou 50 000 appels ont eu lieu — il n'y a ni total
    ni curseur. Restreindre la fenêtre `since`/`until` est le seul moyen d'être sûr
    de tout voir.

    ⚠️ **La rétention du journal est de ~30 jours** (purge au boot,
    `OTO_MCP_CALL_LOG_RETENTION_DAYS`). Au-delà, les lignes n'existent plus : un
    `since` ancien rend une fenêtre VIDE qui ressemble à « aucune activité ». Ce n'est
    pas une pagination, c'est une disparition.

    ⚠️ **Seuls les appels d'OUTILS MCP sont journalisés** (`kind='mcp'`). Les gestes
    faits au dashboard (poser une clé, changer un rôle) n'y figurent pas : ce journal
    trace ce que l'agent a exécuté, pas tout ce qui a été fait dans l'org. Et les
    appels antérieurs à la colonne `org_id` (NULL) n'apparaissent dans aucun export
    d'org — non reconstructibles.

    Le scope est EXACT : les appels ÉMIS SOUS cette org (`tool_calls.org_id`), jamais
    l'appartenance — un membre de N orgs n'apporte ici que ce qu'il y a fait."""
    org_id: int
    # Bornes réécho telles que reçues (None si omises) — pas de valeur par défaut
    # substituée, donc `since: null` ne veut pas dire « depuis toujours » mais
    # « depuis le début de ce qui n'a pas été purgé ».
    since: Optional[str] = None
    until: Optional[str] = None
    count: int
    calls: list[AuditCall]


class AuditExportInput(BaseModel):
    org_id: int
    since: Optional[str] = None       # borne basse ISO (timestamptz), incluse
    until: Optional[str] = None       # borne haute ISO, incluse
    limit: int = 1000

    # C'est la lentille la plus exposée : sur un EXPORT, un grand nombre paraît
    # légitime, donc rien ne le rendait suspect. Écrête au défaut servi (#300).
    @field_validator("limit")
    @classmethod
    def _cap_limit(cls, v):
        return cap_limit(v, 1000)


def _export(ctx: ResolvedCtx, inp: AuditExportInput) -> dict:
    calls = db.list_tool_calls_for_org(inp.org_id, since=inp.since, until=inp.until, limit=inp.limit)
    for c in calls:
        c["namespace"] = namespace_of(c["tool"]) if c.get("tool") else None
    return {"org_id": inp.org_id, "since": inp.since, "until": inp.until,
            "count": len(calls), "calls": calls}


CAPABILITIES += [
    Capability(
        key="org.audit_log.export", handler=_export, Input=AuditExportInput,
        authz=ORG_ADMIN_OF("org_id"), Output=AuditExport,
        description="Org audit log of tool calls (org_admin): timestamp, user, tool, "
                    "namespace, duration, ok/error — never args or secrets. Window via "
                    "since/until (ISO). Scoped to calls emitted UNDER this org.",
        rest=RestBinding("GET", "/api/orgs/{id}/audit-log/export", _ID),
    ),
]

