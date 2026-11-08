"""Le propriétaire NOMMÉ à la création d'un tableau — vérifié une fois, pour les deux faces.

Le défaut reste la personne (`DatastorePg._default_owner`, ADR 0068) : ce module ne
décide rien quand l'appelant ne nomme personne. Il traduit `owner: {type, id}` en
`(owner_type, owner_id)` après avoir vérifié l'appartenance — le store, lui, pose ce
qu'on lui donne et laisse cette vérification à l'appelant.

⚠️ Jusqu'au 08/11/2026 cette vérification ne vivait que dans la capacité REST
(`POST /api/datastores`), et le tool MCP `data_create_datastore` n'avait pas de
paramètre `owner` : un agent ne pouvait créer qu'un tableau privé, et sa description
l'envoyait vers une route qu'il ne peut pas appeler. Deux faces, un seul geste : la
règle vit ICI, chaque face ne fait que traduire le refus dans sa langue.
"""
from __future__ import annotations

from typing import Optional

from .. import roles


class ProprietaireRefuse(Exception):
    """Refus nommé : `status` HTTP et `code` stable, les mêmes sur les deux faces."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code


def resoudre(sub: str, owner: Optional[dict]) -> tuple[Optional[str], str]:
    """`(owner_type, owner_id)` à passer au store.

    `owner_type=None` = l'appelant n'a nommé personne : le store applique le défaut et
    sait qu'il n'a rien été demandé (c'est ce fait-là qui décide de l'avertissement de
    portée, pas la valeur). `owner_id` vaut alors `sub`, sans effet."""
    owner = owner or {}
    owner_type = ((owner.get("type") or "").strip() or None)
    if owner_type is None or owner_type == "user":
        return owner_type, sub
    if owner_type not in ("org", "group"):
        raise ProprietaireRefuse(400, "invalid_owner_type",
                                 "owner.type must be one of: user, org, group")
    try:
        cible = int(owner.get("id"))
    except (TypeError, ValueError):
        raise ProprietaireRefuse(400, "invalid_owner_id",
                                 f"owner.id must be the {owner_type}'s number")
    if owner_type == "org":
        if not roles.is_org_member(sub, cible):
            raise ProprietaireRefuse(403, "not_org_member",
                                     f"you are not a member of org {cible}")
    elif not roles.can_read_group(sub, cible):
        raise ProprietaireRefuse(403, "not_group_member",
                                 f"you are not a member of team {cible}")
    return owner_type, str(cible)
