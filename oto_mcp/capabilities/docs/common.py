"""Ce que les modules du domaine « doc » partagent — aucun descripteur ici.

Deux choses : le refus nommé, et LE droit d'accès aux pages d'un projet. Le second est
un **seam unique** : chaque branche du dispatcher l'appelle par `common.can(...)`, jamais
par un nom importé à l'unité. Un `from .common import can` figerait la fonction à
l'import, et l'unique point de bascule du domaine deviendrait autant de copies qu'il y a
de modules — ce que le fichier plat d'avant garantissait par construction et qu'un
découpage perdrait en silence.
"""
from __future__ import annotations

from typing import Optional

from ... import ownership
from .._types import AuthzDenied

PROJECT_RTYPE = "project"

# Ops servies au destinataire d'un projet publié : LECTURE seule. Tout le reste
# (création, édition, déplacement, publication de page, propositions) exige un `sub`
# — même posture que les tools de gouvernance du datastore.
# `search` en est ABSENT : il délègue à `search_mod.search(sub, …)`, dont le scoping
# est bâti sur un `sub` (projets accessibles). Le destinataire lit l'arbre (`list`)
# puis la page (`get`) — pas de chemin de recherche tant qu'il n'est pas scopé.
SHARED_READ_OPS = frozenset({"list", "get", "revisions", "backlinks"})


def require(cond, code: str, msg: str, status: int = 400) -> None:
    if not cond:
        raise AuthzDenied(status, code, msg)


def can(sub: Optional[str], project_id: int, want: str) -> bool:
    """Droit d'accès aux pages d'un projet. `sub is None` = destinataire d'un endpoint
    publié (ADR 0032) : LECTURE seule, et seulement sur LE projet publié — jamais
    l'arbre documentaire de l'org (pendant de `_anon_project_tableau_ns_ids`).
    Fail-closed : hors de ce projet, ou pour une écriture, c'est non."""
    if sub is None:
        if want != "read":
            return False
        from ... import subdomain_project
        pid = subdomain_project.current_anon_project_id()
        return (pid is not None and int(pid) == int(project_id)
                and subdomain_project.current_anon_docs_exposed())
    return ownership.can_access(sub, PROJECT_RTYPE, str(project_id), want)
