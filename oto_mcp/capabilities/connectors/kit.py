"""Capacités du KIT d'organisation — les trois gestes d'org (ADR 0050 §E8, oto#166).

« Poser le kit en entier » (`connectors.recommend`), « ajouter au kit »
(`connectors.bulk_select`, l'ex-« activer pour toute l'org ») et « retirer du kit »
(`connectors.unset_default`) passent tous par UNE fonction, `connectors.kit.appliquer`,
qui écrit le kit et les boîtes à outils des membres dans la même transaction et rend
une réponse chiffrée par connecteur.

Les déclarations `Capability(...)` restent dans `selection.py`, à leur place : l'ordre
des routes est figé (`tests/api/api_routes_table.txt`). Ce module porte les formes
d'entrée/sortie et les handlers.

Les champs rendus AVANT ce lot (`recommended`, `activated`, `skipped`,
`added_to_org_defaults`, `removed`) restent servis, même sens : deux fronts les lisent
(le dashboard, et celui du tenant partenaire). Les champs du kit s'y AJOUTENT.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel

from ...connectors import kit as connector_kit
from .._types import AuthzDenied, ResolvedCtx


class RecommendInput(BaseModel):
    org_id: int                          # injecté du placeholder {id} (ORG_ADMIN_OF)
    connectors: list[str] = []           # le kit ENTIER ; [] = kit vide


class BulkSelectInput(BaseModel):
    org_id: int
    name: str                            # connecteur (placeholder {name}, auto-mappé)


class UnsetDefaultInput(BaseModel):
    org_id: int
    name: str                            # connecteur (placeholder {name}, auto-mappé)


class KitChange(BaseModel):
    """Ce qu'un connecteur AJOUTÉ au kit ou RETIRÉ du kit a fait chez les membres.

    Un ajout (`change="added"`) porte les cinq compteurs d'installation ; un retrait
    (`change="removed"`) porte `uninstalled` et `kept`. Un champ absent ne vaut pas
    zéro : il n'appartient pas à ce sens de changement."""
    connector: str
    change: Literal["added", "removed"]
    installed: Optional[int] = None          # posé chez N membres (provenance `kit`)
    already_active: Optional[int] = None     # déjà installé et actif : intact
    paused: Optional[int] = None             # en pause chez le membre : intact
    removed_by_member: Optional[int] = None  # retiré par le membre lui-même : laissé retiré
    # Poussée seulement : la date à laquelle le membre l'a retiré lui-même.
    removed_at: Optional[str] = None
    uninstalled: Optional[int] = None        # retrait : désinstallé chez D membres
    # Retrait : membres qui le GARDENT, par provenance de leur installation — `membre`,
    # `admin`, `socle`, `inconnue` (décision Q1 : seul ce que le kit a posé part).
    kept: Optional[dict[str, int]] = None


class _KitApplied(BaseModel):
    """Le kit après le geste, et ce que le geste a fait chez les membres."""
    kit: list[str]                           # le kit de l'org APRÈS le geste
    members: int                             # membres de l'org à qui le geste s'est appliqué
    changes: list[KitChange]                 # un par connecteur ajouté ou retiré
    # Connecteurs nommés par le geste qui n'ont PAS changé le kit (déjà dedans pour
    # un ajout, absents pour un retrait) : rien n'est rejoué chez les membres.
    unchanged: list[str]
    # Connecteurs du kit que l'org a coupés APRÈS les y avoir mis : ils y restent,
    # installés et masqués chez tous, et reviennent seuls à la réouverture (§E2).
    cut: list[str] = []
    note: str                                # quand l'agent d'un membre le verra
    unchanged_note: Optional[str] = None     # pourquoi rien n'a été rejoué, et comment faire
    cut_note: Optional[str] = None           # présent quand `cut` n'est pas vide


class OrgRecommendedConnectors(_KitApplied):
    """Écho du kit posé en entier. `recommended` = le kit (nom d'avant le kit, gardé
    pour les fronts qui le lisent)."""
    org_id: int
    recommended: list[str]


class BulkSelectResult(_KitApplied):
    """« Ajouter au kit » (l'ex-« activer pour toute l'org »)."""
    org_id: int
    connector: str
    activated: int                   # membres chez qui le connecteur vient d'être installé
    # Membres laissés tels quels : déjà actif, en pause, ou retiré par eux-mêmes — le
    # détail est dans `changes`. `skipped>0` n'est pas une anomalie.
    skipped: int
    # `false` = le connecteur était DÉJÀ au kit : rien n'est rejoué (cf. `unchanged_note`).
    added_to_org_defaults: bool


class UnsetDefaultResult(_KitApplied):
    """« Retirer du kit ». `removed=false` = il n'y était pas."""
    org_id: int
    connector: str
    removed: bool


def appliquer_servi(org_id: int, **geste) -> dict:
    """`connectors.kit.appliquer`, ses refus traduits pour les deux faces. Partagé par
    les gestes du kit et par la poussée (`force.py`)."""
    try:
        return connector_kit.appliquer(org_id, **geste)
    except connector_kit.OrgInconnue:
        raise AuthzDenied(404, "unknown_org", f"Org #{org_id} inconnue.")
    except connector_kit.AjoutRefuse as e:
        _refus_d_ajout(e)


def _refus_d_ajout(e) -> None:
    """§E2 — le refus dit POURQUOI, au geste : l'admin l'apprend ici, pas des semaines
    plus tard chez un membre. Mêmes jetons que ceux déjà servis par la gouvernance
    d'activation. Trois `raise` LITTÉRAUX et non un couple calculé : le cliquet des refus
    déclarés (`tests/_refus_atteignables.py`) ne lit que les littéraux — un code calculé
    rend la déclaration « décorative » à ses yeux, alors qu'elle est servie."""
    raisons = {r["reason"] for r in e.refus}
    detail = " ; ".join(f"`{r['connector']}` {connector_kit.RAISONS[r['reason']]}"
                        for r in e.refus)
    message = f"Refusé, rien n'a été écrit (ni au kit, ni chez tes membres) : {detail}."
    details = {"refused": e.refus}
    if "unknown" in raisons:
        raise AuthzDenied(404, "unknown_connector", message, details=details)
    if "platform_disabled" in raisons:
        raise AuthzDenied(409, "platform_disabled", message, details=details)
    raise AuthzDenied(409, "org_disabled", message, details=details)


def _change(out: dict, name: str) -> Optional[dict]:
    return next((c for c in out["changes"] if c["connector"] == name), None)


def _recommend(ctx: ResolvedCtx, inp: RecommendInput) -> dict:
    """[org admin] Pose le kit ENTIER ; seule sa différence avec le kit actuel
    s'applique aux membres (cf. `connectors.kit`)."""
    out = appliquer_servi(inp.org_id, kit=inp.connectors)
    return {"org_id": inp.org_id, "recommended": out["kit"], **out}


def _bulk_select(ctx: ResolvedCtx, inp: BulkSelectInput) -> dict:
    """[org admin] Ajoute `name` au kit : installé chez chaque membre actuel qui ne
    l'a pas (jamais par-dessus son choix), et chez tout futur membre au semis."""
    # La garde d'écriture (§E2) est DANS la fonction d'application : même refus,
    # mêmes jetons (`unknown_connector`, `org_disabled`, `platform_disabled`) pour les
    # trois gestes. Un connecteur déjà au kit n'est pas un ajout : il n'est pas jugé.
    out = appliquer_servi(inp.org_id, ajouter=[inp.name])
    ch = _change(out, inp.name)
    laisses = (ch["already_active"] + ch["paused"] + ch["removed_by_member"]) if ch else 0
    return {"org_id": inp.org_id, "connector": inp.name,
            "activated": ch["installed"] if ch else 0, "skipped": laisses,
            "added_to_org_defaults": ch is not None, **out}


def _unset_default(ctx: ResolvedCtx, inp: UnsetDefaultInput) -> dict:
    """[org admin] Retire `name` du kit. Ne masque jamais le connecteur de la library
    (ce n'est pas le levier d'exposition)."""
    out = appliquer_servi(inp.org_id, retirer=[inp.name])
    return {"org_id": inp.org_id, "connector": inp.name,
            "removed": _change(out, inp.name) is not None, **out}
