"""Denylist de tools par org/équipe — gouvernance de visibilité au grain OUTIL,
PAS une barrière de sécurité (ADR 0031, même esprit que
`tool_visibility.DEFAULT_HIDDEN_TOOLS`) : un org_admin/chef d'équipe masque des
tools SPÉCIFIQUES par défaut pour son org/équipe. `user_enabled_tools` (override
perso positif) lève TOUJOURS ce masquage — même échappatoire qu'un masqué-par-
défaut plateforme, un cran plus spécifique. Additif entre paliers (union à la
lecture, `session_visibility.compute_hidden_tools`) : une équipe ne peut JAMAIS
révéler un tool que l'org a masqué (deux ensembles négatifs, jamais un retrait
croisé).

Remplace l'ancienne baseline ALLOWLIST org/équipe (`orgs.default_tools`/
`org_groups.default_tools`, ADR 0012/0015) retirée le 2026-07-03 (commit
`3951a57`) : celle-ci masquait tout ce qui n'était PAS listé — un tool ajouté
après coup arrivait masqué par défaut pour toute org/équipe ayant posé une
baseline. Ici on choisit ce qu'on masque ; le reste, y compris les tools
futurs, reste visible par défaut.

Lecture = `ORG_MEMBER_OF`/`GROUP_MEMBER_OF` (transparence — un membre voit la
gouvernance qui s'applique à lui). Écriture = `ORG_ADMIN_OF`/`GROUP_ADMIN_OF`.
`refresh_visibility=True` : la bascule re-pousse la visibilité sur la session
MCP du caller ; effet pour les autres membres à leur session suivante.
"""
from __future__ import annotations

from pydantic import BaseModel

from .. import db, group_store, org_store, tool_registry
from ..tool_visibility import is_protected
from ._authz import GROUP_ADMIN_OF, GROUP_MEMBER_OF, ORG_ADMIN_OF, ORG_MEMBER_OF
from ._types import AuthzDenied, Capability, ResolvedCtx, RestBinding
from .registry import CAPABILITIES

_ID = {"id": "org_id"}
_GID = {"id": "group_id"}


def _known_tool_names() -> set[str]:
    return set(tool_registry.boot_tool_names())


def _reject_unhidable(name: str) -> None:
    """Refuse un tool inconnu, et un tool PROTÉGÉ (anti-lockout, source unique
    `tool_visibility.PROTECTED_TOOLS`).

    Sans ce second refus, masquer `oto_whoami` écrirait la ligne et répondrait
    `hidden: true` — mais `is_tool_visible` court-circuite sur les protégés, donc
    le tool resterait visible : un admin croirait avoir masqué quelque chose. Les
    deux autres faces du même geste refusent déjà (`oto_disable_tool` lève,
    `POST /api/me/tools/{name}` → 400 `protected_tool`) ; celle-ci s'aligne."""
    if name not in _known_tool_names():
        raise AuthzDenied(404, "unknown_tool", f"Tool `{name}` inconnu.")
    if is_protected(name):
        raise AuthzDenied(
            400, "protected_tool",
            f"`{name}` ne peut pas être masqué : c'est un outil protégé "
            "(méta-toolset, identité, échappatoire de contexte ou boucle d'usage). "
            "Le masquer n'aurait aucun effet.")


class OrgHiddenToolsListInput(BaseModel):
    org_id: int


class OrgHiddenToolSetInput(BaseModel):
    org_id: int
    name: str                  # tool (placeholder {name}, auto-mappé)


class OrgHiddenTools(BaseModel):
    """Le denylist **de l'org**, et lui seul.

    ⚠️ Trois lectures fausses à éviter, toutes coûteuses côté front :

    1. **Ce n'est PAS la liste des outils invisibles pour un membre.** C'est un des
       paliers d'un calcul additif (`session_visibility.compute_hidden_tools` :
       plateforme ∪ org ∪ équipe active, moins les overrides perso positifs). Un
       outil absent d'ici peut être masqué par la plateforme ou par l'équipe ; un
       outil présent ici peut rester visible pour qui s'est posé un override
       (`oto_enable_tool`) — c'est de la gouvernance, PAS une barrière d'accès
       (ADR 0031). Pour « que voit CE membre ? », c'est `oto_list_my_tools`.
    2. **`disabled_tools: []` veut dire « l'org ne masque rien », sans ambiguïté.**
       Cette lecture frappe la table directement et LÈVE sur hoquet DB (pas de
       200-vide de consolation). C'est la différence avec le calcul de session, qui
       lui est fail-open par palier : là-bas une liste vide peut vouloir dire « la
       vue n'a pas pu être dérivée ». Ici non — pas de troisième état à gérer.
    3. **Un nom listé n'est pas forcément un outil qui existe encore.** La pose
       refuse un outil inconnu, mais rien ne nettoie une ligne dont l'outil a
       ensuite été renommé ou retiré du catalogue : le résidu reste servi tel quel
       (inerte). Ne pas en déduire un catalogue.

    Trié par nom, jamais par date de pose."""
    org_id: int
    disabled_tools: list[str]        # noms EXACTS d'outils, ordre alphabétique


class OrgHiddenToolState(BaseModel):
    """État VOULU du denylist d'org après le geste — pas un compte-rendu d'écriture.

    Les deux gestes sont **idempotents** : masquer deux fois répond `hidden: true`
    sans rien réécrire (`ON CONFLICT DO NOTHING`), démasquer un outil qui n'était
    pas masqué répond `hidden: false` sans erreur. Donc `hidden` = « voilà où en est
    l'org sur cet outil », jamais « une ligne a bougé » : un client qui compte les
    changements ne peut pas le faire ici.

    ⚠️ `hidden: true` ne rend pas l'outil inaccessible — un membre lève toujours ce
    masquage pour lui-même avec `oto_enable_tool` (ADR 0031). Et le masquage prend
    effet à la session MCP **suivante** des autres membres ; seule celle de
    l'appelant est rafraîchie à chaud (`refresh_visibility`)."""
    org_id: int
    tool: str                        # l'outil visé, écho du paramètre de chemin
    hidden: bool


def _org_list(ctx: ResolvedCtx, inp: OrgHiddenToolsListInput) -> dict:
    if not org_store.get_org(inp.org_id):
        raise AuthzDenied(404, "unknown_org", f"Org #{inp.org_id} inconnue.")
    return {"org_id": inp.org_id, "disabled_tools": db.list_org_disabled_tools(inp.org_id)}


def _org_hide(ctx: ResolvedCtx, inp: OrgHiddenToolSetInput) -> dict:
    _reject_unhidable(inp.name)
    db.add_org_disabled_tool(inp.org_id, inp.name, disabled_by=ctx.sub)
    return {"org_id": inp.org_id, "tool": inp.name, "hidden": True}


# ⚠️ **L'ASYMÉTRIE EST VOULUE : démasquer ne valide PAS le nom** (là où masquer refuse
# un tool inconnu ou protégé). C'était un effet de bord, c'est une décision (#293), et
# elle est écrite dans les `description=` des deux capacités `*_unhide` — donc dans le
# contrat publié, pas seulement ici.
#
# Ce qu'elle sert : rien ne nettoie une ligne dont le tool a été renommé ou retiré du
# catalogue. Sans cette porte, ce résidu serait DÉFINITIF — refusé au démasquage parce
# qu'inconnu, invisible autrement qu'en lisant la table.
#
# Pourquoi pas plutôt « valider des deux côtés + une purge » : la purge n'a aucun
# référentiel fiable de ce qui existe. `tool_registry.boot_tool_names()` ne liste que ce
# qui a été MONTÉ au boot — `tools.register_all` désactive silencieusement (warning) tout
# module dont une dép optionnelle manque, et le registre rend `[]` tant qu'il n'est pas
# réchauffé. Une purge branchée dessus effacerait la gouvernance d'une org au premier
# import raté. Un référentiel faux qui SUPPRIME est pire qu'un résidu inerte.
#
# Conséquence assumée : ces deux verbes répondent 200 `hidden: false` pour n'importe
# quelle chaîne. C'est un DELETE idempotent sur une table de gouvernance (ADR 0031), pas
# une barrière d'accès — il ne révèle rien et n'ouvre rien.

def _org_unhide(ctx: ResolvedCtx, inp: OrgHiddenToolSetInput) -> dict:
    db.remove_org_disabled_tool(inp.org_id, inp.name)
    return {"org_id": inp.org_id, "tool": inp.name, "hidden": False}


class GroupHiddenToolsListInput(BaseModel):
    group_id: int


class GroupHiddenToolSetInput(BaseModel):
    group_id: int
    name: str                  # tool (placeholder {name}, auto-mappé)


class GroupHiddenTools(BaseModel):
    """Le denylist **de l'équipe**, et lui seul — miroir d'`OrgHiddenTools` au grain
    équipe, mêmes trois pièges (palier parmi d'autres, `[]` non ambigu car la lecture
    lève sur hoquet DB, noms possiblement périmés).

    ⚠️ Piège propre à ce grain : la liste **n'inclut pas** ce que l'org masque de son
    côté. Les deux paliers s'unissent à la lecture de session, jamais ici — afficher
    ceci comme « ce que mon équipe cache » sous-déclare donc l'effet réel. Et
    l'inverse est impossible par construction : une équipe ne peut que RÉTRÉCIR
    (aucune ligne d'équipe ne révèle un outil masqué par l'org)."""
    group_id: int
    disabled_tools: list[str]        # noms EXACTS d'outils, ordre alphabétique


class GroupHiddenToolState(BaseModel):
    """État VOULU du denylist d'équipe après le geste — miroir d'`OrgHiddenToolState`
    (idempotent des deux côtés, `hidden` = état et non écriture, masquage levable par
    un override perso, effet à la session suivante des autres membres)."""
    group_id: int
    tool: str                        # l'outil visé, écho du paramètre de chemin
    hidden: bool


def _group_list(ctx: ResolvedCtx, inp: GroupHiddenToolsListInput) -> dict:
    if not group_store.get_group(inp.group_id):
        raise AuthzDenied(404, "unknown_group", f"Équipe #{inp.group_id} inconnue.")
    return {"group_id": inp.group_id, "disabled_tools": db.list_group_disabled_tools(inp.group_id)}


def _group_hide(ctx: ResolvedCtx, inp: GroupHiddenToolSetInput) -> dict:
    _reject_unhidable(inp.name)
    db.add_group_disabled_tool(inp.group_id, inp.name, disabled_by=ctx.sub)
    return {"group_id": inp.group_id, "tool": inp.name, "hidden": True}


def _group_unhide(ctx: ResolvedCtx, inp: GroupHiddenToolSetInput) -> dict:
    # Pas de validation du nom — même échappatoire assumée qu'`_org_unhide`, cf. le
    # bloc qui la motive au-dessus.
    db.remove_group_disabled_tool(inp.group_id, inp.name)
    return {"group_id": inp.group_id, "tool": inp.name, "hidden": False}


CAPABILITIES += [
    Capability(
        key="tools.org_list", handler=_org_list, Input=OrgHiddenToolsListInput,
        authz=ORG_MEMBER_OF("org_id"), Output=OrgHiddenTools,
        description="List the tools your org_admin has hidden by default for your org. "
                    "Governance only (ADR 0031) — you can always self-override with "
                    "oto_enable_tool.",
        rest=RestBinding("GET", "/api/orgs/{id}/tools/hidden", _ID),
    ),
    Capability(
        key="tools.org_hide", handler=_org_hide, Input=OrgHiddenToolSetInput,
        authz=ORG_ADMIN_OF("org_id"), refresh_visibility=True,
        Output=OrgHiddenToolState,
        description="[org admin] Hide a specific tool by default for your whole org. "
                    "Members can still self-override with oto_enable_tool — this is "
                    "governance, not an access barrier. name = exact tool name.",
        rest=RestBinding("PUT", "/api/orgs/{id}/tools/{name}/hidden", _ID),
    ),
    Capability(
        key="tools.org_unhide", handler=_org_unhide, Input=OrgHiddenToolSetInput,
        authz=ORG_ADMIN_OF("org_id"), refresh_visibility=True,
        Output=OrgHiddenToolState,
        description="[org admin] Remove your org's default-hide on a tool. Idempotent: "
                    "a tool that was not hidden answers 200 hidden:false. Accepts a name "
                    "the hide side would refuse (unknown or protected), so a stale row "
                    "left by a renamed tool can be cleaned up.",
        rest=RestBinding("DELETE", "/api/orgs/{id}/tools/{name}/hidden", _ID),
    ),
    Capability(
        key="tools.group_list", handler=_group_list, Input=GroupHiddenToolsListInput,
        authz=GROUP_MEMBER_OF("group_id"), Output=GroupHiddenTools,
        description="List the tools your team lead has hidden by default for your team. "
                    "This is the TEAM's own denylist — it does not include what the org "
                    "hides (the two add up only when a session is computed).",
        rest=RestBinding("GET", "/api/groups/{id}/tools/hidden", _GID),
    ),
    Capability(
        key="tools.group_hide", handler=_group_hide, Input=GroupHiddenToolSetInput,
        authz=GROUP_ADMIN_OF("group_id"), refresh_visibility=True,
        Output=GroupHiddenToolState,
        description="[team lead] Hide a specific tool by default for your team. Additive "
                    "with the org's own denylist (never reveals an org-hidden tool). "
                    "Members can still self-override with oto_enable_tool.",
        rest=RestBinding("PUT", "/api/groups/{id}/tools/{name}/hidden", _GID),
    ),
    Capability(
        key="tools.group_unhide", handler=_group_unhide, Input=GroupHiddenToolSetInput,
        authz=GROUP_ADMIN_OF("group_id"), refresh_visibility=True,
        Output=GroupHiddenToolState,
        description="[team lead] Remove your team's default-hide on a tool. Idempotent, "
                    "and accepts a name the hide side would refuse — so a stale row left "
                    "by a renamed tool can be cleaned up.",
        rest=RestBinding("DELETE", "/api/groups/{id}/tools/{name}/hidden", _GID),
    ),
]
