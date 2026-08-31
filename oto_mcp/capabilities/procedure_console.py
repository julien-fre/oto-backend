"""Console procédures MCP consolidée (ADR 0047, B2) — `oto_procedure`.

Réunit les 9 tools MCP du domaine guide/procédure membre en UN : lecture
(`get`/`list`), écriture
(`set` — org_admin au palier org, **membre de l'équipe** au palier `scope='group'` ;
`delete`, destructeur, reste au **chef d'équipe** — #681 ; épinglable par `org` /
`group`) et bibliothèque publique
(`library_list`/`library_get`/`publish`/`fork`/`unpublish`). Les handlers de
domaine (`orgs_instructions`, `guide_library`) sont réutilisés tels quels ;
leurs faces REST `/api/me/instructions*` ne bougent pas (palier org, org_admin).

⚠️ L'index des guides nommés (skills) est APPENDU à la description de CE
tool par `DynamicInstructionsMiddleware.on_list_tools` (via `_GUIDE_GET_TOOL`,
middleware/dynamic_instructions.py) — les skills ne sont pas des outils, c'est leur seul canal de
découverte. Le filtre d'usage (`org.instruction.usage`) compte les appels sur ce
nom de tool.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel

from . import guide_library
from .orgs import instructions as orgs_instructions
from ._authz import (BY_OP, GROUP_ADMIN_OPT, GROUP_MEMBER_OPT, ORG_ADMIN_OPT,
                     ORG_MEMBER, ORG_MEMBER_OPT, SUB_ONLY)
from ._types import AuthzDenied, Capability, ResolvedCtx
from .registry import CAPABILITIES


# Le palier d'autz suit le SCOPE demandé (#681). Deux règles par geste, jamais une
# combinaison à énumérer op par op : `BY_OP(..., fields=("scope",))` est le même
# combinateur, appliqué à l'autre axe. Une valeur de `scope` hors map (« team »,
# « perso »…) est refusée net par le combinateur, avec la liste des valeurs attendues —
# plutôt que silencieusement traitée comme l'org, ce qui écrirait au mauvais endroit.
#
# ⚠️ `None` et `"org"` doivent MAPPER LA MÊME règle : `scope` est optionnel, et son
# absence ne veut pas dire « palier inconnu ».
_LIRE = BY_OP({None: ORG_MEMBER_OPT("org"), "org": ORG_MEMBER_OPT("org"),
               "group": GROUP_MEMBER_OPT("group")}, fields=("scope",))
# ⚠️ `set` et `delete` ne partagent PAS une règle : ce n'est pas la surface qui décide du
# palier, c'est le VERBE.
#
# `set` au palier équipe = **membre** de l'équipe. C'est tout le lot #681 : celui qui
# DÉROULE la procédure est un membre, pas un chef — réserver l'écriture au chef réservait
# l'apprentissage à qui n'exécute pas, et la boucle que la procédure promet ne se fermait
# jamais. Le coût mesuré de la garde d'avant n'était pas théorique : pour laisser une
# opératrice annoter son mode d'emploi, il fallait la faire chef d'équipe — un rôle qui
# emporte les CLÉS PARTAGÉES de l'équipe. Une garde d'écriture trop grossière forçait donc
# une élévation de droits dans un domaine sans rapport.
#
# Ce qui rend l'ouverture tenable, c'est que le geste est RÉVERSIBLE : chaque écriture
# ajoute une version et `from_version` restaure la précédente. Le risque d'une procédure
# qui pilote un agent se traite là — par les versions et par le digest qui dit ce qui a
# changé — et non en fermant la porte à ceux qui s'en servent.
_ECRIRE = BY_OP({None: ORG_ADMIN_OPT("org"), "org": ORG_ADMIN_OPT("org"),
                 "group": GROUP_MEMBER_OPT("group")}, fields=("scope",))
# `delete` reste au **chef d'équipe** : il emporte la procédure ET tout son historique,
# sans corbeille — rien ne le défait. Un geste destructeur n'est pas un geste de travail.
_SUPPRIMER = BY_OP({None: ORG_ADMIN_OPT("org"), "org": ORG_ADMIN_OPT("org"),
                    "group": GROUP_ADMIN_OPT("group")}, fields=("scope",))


def _need(val, code: str, msg: str):
    if val is None or (isinstance(val, str) and not val.strip()):
        raise AuthzDenied(400, code, msg)
    return val


class ProcedureInput(BaseModel):
    op: Literal["get", "list", "set", "delete",
                "library_list", "library_get", "publish", "fork", "unpublish"]
    slug: Optional[str] = None
    guide_id: Optional[int] = None         # get : lecture par ID STABLE (ADR 0032)
    doctrine_id: Optional[int] = None      # ALIAS déprécié du précédent (retrait 27/09/2026, #519)
    scope: Optional[str] = None            # org (défaut) | group — LECTURE ET ÉCRITURE
    version: Optional[int] = None          # get
    with_history: bool = False             # get
    query: Optional[str] = None            # list / library_list
    body_md: Optional[str] = None          # set
    title: Optional[str] = None            # set / publish
    description: Optional[str] = None      # set / publish
    from_version: Optional[int] = None     # set (revert)
    slots: Optional[list] = None           # set (ADR 0035)
    org: Optional[int] = None              # set/delete : org explicite (#69)
    group: Optional[int] = None            # scope=group : équipe explicite (#681)
    public_slug: Optional[str] = None      # publish
    category: Optional[str] = None         # publish / library_list
    tags: Optional[list] = None            # publish
    visibility: Optional[str] = None       # publish : public | unlisted
    new_slug: Optional[str] = None         # fork
    id: Optional[int] = None               # unpublish : id d'entrée bibliothèque
    author_kind: Optional[str] = None      # library_list : otomata | org
    limit: int = 100                       # library_list


async def _procedure(ctx: ResolvedCtx, inp: ProcedureInput) -> dict:
    oi, lib = orgs_instructions, guide_library
    if inp.op == "get":
        return await oi._get_guide(ctx, oi.GuideGetInput(
            slug=inp.slug, guide_id=inp.guide_id, doctrine_id=inp.doctrine_id,
            scope=inp.scope or "org",
            version=inp.version, with_history=inp.with_history))
    if inp.op == "list":
        return oi._list_guides(ctx, oi.GuideListInput(query=inp.query, scope=inp.scope))
    if inp.op == "set":
        return await oi._set_instruction(ctx, oi.ConsoleInstrSetInput(
            slug=inp.slug, body_md=inp.body_md, title=inp.title,
            description=inp.description, from_version=inp.from_version,
            slots=inp.slots, org=inp.org, scope=inp.scope, group=inp.group))
    if inp.op == "delete":
        return oi._delete_instruction(ctx, oi.ConsoleGuideDeleteInput(
            slug=_need(inp.slug, "missing_slug", "`slug` requis pour delete."),
            org=inp.org, scope=inp.scope, group=inp.group))
    if inp.op == "library_list":
        return lib._list(ctx, lib.LibraryListInput(
            query=inp.query, category=inp.category, author_kind=inp.author_kind,
            limit=inp.limit))
    if inp.op == "library_get":
        return lib._get(ctx, lib.LibraryGetInput(
            slug=_need(inp.slug, "missing_slug", "`slug` (public) requis pour library_get.")))
    if inp.op == "publish":
        return lib._publish(ctx, lib.PublishInput(
            slug=_need(inp.slug, "missing_slug", "`slug` (skill d'org) requis pour publish."),
            public_slug=inp.public_slug, title=inp.title, description=inp.description,
            category=inp.category, tags=inp.tags, visibility=inp.visibility or "public"))
    if inp.op == "fork":
        return lib._fork(ctx, lib.ForkInput(
            slug=_need(inp.slug, "missing_slug", "`slug` (public) requis pour fork."),
            new_slug=inp.new_slug))
    return lib._unpublish(ctx, lib.UnpublishInput(
        id=_need(inp.id, "missing_id", "`id` (entrée bibliothèque) requis pour unpublish.")))


CAPABILITIES += [
    Capability(
        key="org.procedure.console", handler=_procedure, Input=ProcedureInput,
        authz=BY_OP({
            # `list` honore `_org=` comme `get` (signal #248 : `set org=Y` répondait
            # ok, puis `list org=Y` rendait toujours le catalogue de l'org MAISON —
            # l'agent croyait sa procédure perdue). Le fix cross-org du 27/07 n'avait
            # posé ORG_MEMBER_OPT que sur `get`, laissant la moitié du signal ouverte.
            "get": _LIRE, "list": _LIRE,
            "set": _ECRIRE, "delete": _SUPPRIMER,
            "library_list": SUB_ONLY, "library_get": SUB_ONLY,
            "publish": ORG_MEMBER, "fork": ORG_MEMBER, "unpublish": SUB_ONLY,
        }),
        description=(
            "Your org's procedures (named guides / skills) + the public library. The base "
            "guide is INJECTED at connect — op=get with `slug` loads ONE skill's full "
            "markdown (`scope=group` targets your active department; `guide_id` loads by "
            "STABLE id, incl. one SHARED to your org; `org` pins the read to an EXPLICIT org "
            "id you are a member of — cross-org load of a named skill by slug) / list (catalog: "
            "slug/title/description, "
            "no body) / set (write: `slug` is REQUIRED — one named skill. `scope='group'` "
            "writes your TEAM's procedure and only needs you to be a MEMBER of it "
            "(`group` pins an explicit team id): whoever RUNS a procedure may improve it, "
            "and a bad edit is undone with `from_version`. The default `scope='org'` needs "
            "org_admin. ⚠️ The "
            "org README (the prose injected into every session, « socle de l'org ») is NOT a "
            "procedure: write it with `oto_guide(op='write', scope='org', delivery='init')`. "
            "⚠️ EVERY procedure OPENS with `> **Self-improvement digest** — …` as its first block (what the last run taught and what was fixed, dated; one sentence if it has never been run — never invent a run). "
            "⚠️ EVERY procedure must carry a FLOWCHART — one untagged fenced block drawn in "
            "box characters, placed right after the « At a glance » table (or the intro) and "
            "before the first phase heading. It is the DEFAULT view of the process page, and "
            "the grammar is a contract: read the `procedure-flowchart` guide before writing "
            "one. The response carries `diagram_warning` when the body has none. "
            "`from_version` "
            "restores; `slots` = required entities referenced <slot:name> in the prose; `org` "
            "pins an explicit org id) / delete (exact `slug`; same `scope`/`group`/`org` "
            "axes as set, but DESTRUCTIVE — it takes the whole version history, so "
            "`scope='group'` needs the team LEAD) — and the PUBLIC library: "
            "op=library_list (browse/search, filter category/author_kind) / library_get (full "
            "body by public slug) / publish (share one of your org's skills; visibility="
            "public|unlisted) / fork (copy a public entry into your org, optional `new_slug`) "
            "/ unpublish (`id`)."),
        mcp=orgs_instructions._GUIDE_GET_TOOL,
    ),
]
