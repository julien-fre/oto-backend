"""« Pousser à un membre » : installer un connecteur dans la boîte à outils d'UN membre.

ADR 0050 §E, décision Q2 du 11/09/2026 (amende l'ADR 0031, force-connecteur-par-user).

Le geste posait une préférence de visibilité par outil (`user_enabled_tools`), que le
régime de sélection ignore sur un connecteur non installé : en production, 18 poussées
du 11/08 au 11/09, dont 11 visaient un membre qui n'avait pas le connecteur — rien n'est
jamais apparu chez lui, et la réponse disait `ok`. Désormais le geste INSTALLE,
provenance `admin`, par la fonction unique du kit (`connectors.kit.appliquer(…,
pousser_a=sub)`) — sans toucher au kit, et avec les exceptions du membre (§E6) : une
ligne active n'est pas réécrite ; une PAUSE ou un RETRAIT de sa part n'est jamais
défait, et le geste est alors REFUSÉ (avec la date du retrait) plutôt que de répondre
`ok` sur un geste qui n'a rien fait. Plus aucune préférence par outil n'est écrite.

Installer n'est pas autoriser (§E1) : l'accès réel reste gardé à l'appel (credential).
autz `ORG_ADMIN_OF` : l'org_admin gouverne SON org.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from ... import db, org_store
from .._authz import ORG_ADMIN_OF
from .._types import AuthzDenied, Capability, DeclaredError, ResolvedCtx, RestBinding
from ..registry import CAPABILITIES
from .kit import appliquer_servi
from .selection import _connector_tools

_ID_CONN = {"id": "org_id", "connector": "connector"}


class ForceConnectorInput(BaseModel):
    org_id: int
    connector: str
    member: str  # sub Logto OU email du membre cible


class ForceConnectorResult(BaseModel):
    """Le connecteur est installé et actif dans la boîte à outils du membre, pour cette
    org — visible pour son agent à sa PROCHAINE conversation. Un geste sans effet
    (pause ou retrait du membre) n'arrive jamais ici : il est refusé."""
    ok: bool                                  # toujours vrai sur un 200
    org_id: int
    connector: str
    # Le sub RÉSOLU du membre — l'entrée acceptait un email, la réponse ne le renvoie jamais.
    member: str
    # `installed` = posé par ce geste (provenance `admin`) ; `already_active` = il l'avait
    # déjà, actif (sa ligne n'est pas réécrite).
    result: Literal["installed", "already_active"]
    # ALIAS déprécié (champ d'avant la décision Q2, qui comptait des préférences par
    # outil) : le nombre d'outils du connecteur, lus du registre BOOT, que sa boîte porte
    # désormais. `0` = registre non réchauffé (script hors serveur), pas un connecteur
    # sans outils.
    tools_forced: int
    note: str


def _resolve_member(org_id: int, target: str) -> str:
    """Résout le sub du membre cible (email accepté) + vérifie son appartenance à l'org."""
    sub = target
    if "@" in target:
        u = db.get_user_by_email(target)
        if not u:
            raise AuthzDenied(404, "unknown_user", f"Aucun user avec l'email `{target}`.")
        sub = u["sub"]
    if org_store.get_org_role(org_id, sub) is None:
        raise AuthzDenied(400, "user_not_in_org",
                          f"`{target}` n'est pas membre de l'org #{org_id}.")
    return sub


async def _force_connector(ctx: ResolvedCtx, inp: ForceConnectorInput) -> dict:
    sub = _resolve_member(inp.org_id, inp.member)
    out = appliquer_servi(inp.org_id, ajouter=[inp.connector], pousser_a=sub)
    (ch,) = out["changes"]
    if ch["removed_by_member"]:
        raise AuthzDenied(
            409, "removed_by_member",
            f"Refusé, rien n'a été écrit : ce membre a retiré `{inp.connector}` lui-même le "
            f"{ch.get('removed_at')} (UTC). La plateforme ne défait pas son geste ; s'il en "
            f"a besoin, c'est à lui de le réinstaller.",
            details={"removed_at": ch.get("removed_at")})
    if ch["paused"]:
        raise AuthzDenied(
            409, "paused_by_member",
            f"Refusé, rien n'a été écrit : ce membre a `{inp.connector}` installé et l'a mis "
            f"en pause lui-même. La plateforme ne le reprend pas à sa place ; il le reprend "
            f"quand il veut.")
    return {"ok": True, "org_id": inp.org_id, "connector": inp.connector, "member": sub,
            "result": "installed" if ch["installed"] else "already_active",
            "tools_forced": len(_connector_tools(inp.connector)), "note": out["note"]}


CAPABILITIES += [
    Capability(
        key="connectors.force.member", handler=_force_connector, Input=ForceConnectorInput,
        authz=ORG_ADMIN_OF("org_id"), Output=ForceConnectorResult,
        description="[org admin] Install a connector in ONE member's toolbox in this org "
                    "(provenance `admin`) — it is NOT added to your org's kit. Never over "
                    "their own choice: if they paused it or removed it themselves, the push "
                    "is REFUSED and says so, with the date. Refused too if the connector is "
                    "unknown or not available for your org. Not an access grant: keys and "
                    "access rules still apply at call time. Their agent sees it at their "
                    "NEXT conversation. `member` = sub or email.",
        errors=(DeclaredError(404, "unknown_user", "aucun compte ne porte cet email"),
                DeclaredError(400, "user_not_in_org", "la cible n'est pas membre de l'org"),
                DeclaredError(404, "unknown_connector", "nom inconnu du registre"),
                DeclaredError(409, "org_disabled",
                              "connecteur non disponible pour les membres de l'org"),
                DeclaredError(409, "platform_disabled", "connecteur coupé par la plateforme"),
                DeclaredError(409, "removed_by_member",
                              "le membre l'a retiré lui-même — jamais défait, rien n'est écrit"),
                DeclaredError(409, "paused_by_member",
                              "le membre l'a mis en pause lui-même — rien n'est écrit"),),
        rest=RestBinding("POST", "/api/orgs/{id}/connectors/{connector}/force", _ID_CONN),
    ),
]
