"""Base de connaissance d'org = la zone « Documents » (réunion
30/06, fusion KB↔Document). Une SEULE base par org : un projet dédié « Base de
connaissance », possédé par l'org active, résolu ici — et créé à la demande par
`op="ensure"` SEULEMENT (`op="get"` lit, cf. `KbInput`). La
zone Documents du dashboard l'ouvre via le composant doc existant — on réutilise
tout le substrat docs (pages arborescentes, versions, partage public, demande de
modif) sans nouvelle table.

Isolé dans son fichier (pas de collision avec `projects.py`) ; n'utilise que des
fonctions db existantes (zéro schéma neuf).

**Ancré PAR ID depuis le lot 3 (chantier 0.3)** : `orgs.kb_project_id` est la source
de vérité — le nom n'est plus un marqueur (renommer la KB ne casse plus rien, deux
appels concurrents ne créent plus deux KB). Auto-réparation : une ancre pendouillante
(projet archivé ou transféré hors org) est levée puis re-posée sur un projet neuf ;
verrou = claim optimiste (`claim_kb_project`), le perdant archive son doublon.

**Semée en ANGLAIS depuis le 2026-09-03 (#527)** : le nom et le résumé étaient des
littéraux français, servis tels quels à des orgs entièrement anglophones. Rien ici ne
devine une langue — voir le commentaire de `KB_NAME`."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel

from .. import db, org_store
from ._authz import SUB_ONLY
from ._types import AuthzDenied, Capability, ResolvedCtx, RestBinding
from .registry import CAPABILITIES

# Le libellé SEMÉ. En anglais depuis le 2026-09-03 (#527) : la base naissait en
# français dans des orgs entièrement anglophones, en tête de leur écran de projets.
# On ne DEVINE pas la langue — la plateforme n'a aucun signal honnête (`users.locale`
# n'est posée que sur une poignée de comptes, `billing_identities` est vide), et de
# toute façon la KB appartient à l'ORG quand `ensure` est appelé par UN membre. On
# sème donc dans la langue de la surface servie, qui est l'anglais.
#: La phrase qui manquait — une seule définition, servie par toutes les `op`.
_VISIBLE_TO = ("TOUS les membres de l'org — cette base est partagée, elle n'est pas "
               "personnelle. Pour un espace privé à toi seul, crée un projet "
               "(`oto_project op=create`, owner_type='user').")

KB_NAME = "Knowledge base"
KB_BRIEF = ("The org-wide knowledge base: shared reference pages "
            "(processes, context, conventions). One per org.")

# Le libellé semé JUSQU'AU 2026-09-03. Aucune base neuve ne le porte plus, mais toutes
# celles posées avant, oui — le backfill d'ancre de `db/_init.py` et le rapport de
# `scripts/archive_empty_kb_projects.py` s'y adossent, et eux seuls.
# ⚠️ Ni celui-ci ni `KB_NAME` n'est une clé de RÉSOLUTION : l'ancre `orgs.kb_project_id`
# l'est depuis le lot 3 (chantier 0.3). Chercher la KB par son nom, c'est perdre toute
# base renommée — ce que faisait encore `tools/docs_app.py` jusqu'à ce même lot.
KB_NAME_LEGACY_FR = "Base de connaissance"


class KbInput(BaseModel):
    """`get` LIT, `ensure` CRÉE — la distinction n'est pas cosmétique.

    Jusqu'ici la seule op, `get`, créait la KB au passage (« résolue et créée
    paresseusement »). Or ce endpoint est monté à la racine des fronts : le
    simple fait d'ouvrir l'app posait un projet « Base de connaissance » VIDE
    dans l'org de chaque client, que personne n'avait demandé et dont personne
    ne pouvait expliquer l'origine (remonté par un client, 19/08). Une lecture
    ne doit rien écrire ; l'écriture a désormais son verbe."""

    op: Literal["get", "ensure"] = "get"
    # ⚠️ Le défaut n'est PLUS de poser au niveau de l'org (décision d'Alexis, 04/09).
    # `ensure` sur une org sans base REFUSE, et nomme les deux gestes possibles : la
    # base partagée de l'org, ou un projet à soi. Créer une ressource visible de tous
    # les membres redevient un acte demandé, pas la conséquence d'un verbe dont le nom
    # — « assure-toi que » — sonne comme une vérification.
    # Incident fondateur : une DG demande « ma knowledge base », l'agent appelle
    # `ensure`, et un document marqué « non diffusable » se retrouve 3 min 18 s dans un
    # espace visible de toute l'entreprise.
    create_shared: bool = False


class KbView(BaseModel):
    """Ancre de la base de connaissance de l'org active.

    Cette surface est consolidée comme ses voisines (le verbe vit dans le corps,
    `op=`), mais elle n'a **qu'une seule `op`** — `get` — donc l'intersection des
    réponses de toutes ses `op` EST la réponse entière : ce modèle décrit la 200
    en totalité, ce n'est pas une enveloppe partielle. Un `op` ajouté ici devra
    donc, soit rendre ces trois champs, soit faire retomber la déclaration sur
    l'intersection commune (garde-fou `test_kb_output_holds_for_every_op`).

    Ce que la réponse ne contient PAS : les pages elles-mêmes. `project_id` est
    l'entrée — l'arbre, les versions, le partage public et les propositions de
    modification se lisent et s'écrivent avec `oto_doc` (`POST /api/me/docs`)."""
    project_id: Optional[int]  # le projet dédié, ou None : op="get" sur une org qui
                               # n'a PAS encore de KB (op="ensure" la crée)
    name: str                # son nom courant — renommable, l'ancre est l'id ;
                             # KB_NAME quand il n'y a pas encore de projet
    brief_md: str            # brief du projet KB ('' si vidé, ou pas de KB)
    # ⚠️ QUI VOIT — le fait que cette réponse taisait, et qui a coûté (04/09/2026).
    # Une DG demande à son agent de « mettre à jour sa base de connaissance » ; il
    # appelle `ensure`, qui CRÉE un projet possédé par l'ORG, visible de tous ses
    # membres, et y dépose un document stratégique marqué « non diffusable ». Il s'en
    # aperçoit 3 min plus tard, déplace la page et archive la base. Entre-temps le
    # contenu a été exposé 3 min 18 s, et personne n'en a été averti — la réponse
    # rendait `{project_id, name, brief_md}` sans un mot sur la portée.
    # « Ma base de connaissance » se comprend comme « la mienne » ; celle-ci est
    # celle de l'ORG. Le mot manquant est ici.
    # SANS défaut, délibérément : un champ à valeur par défaut ne serait pas `required`
    # dans le document servi, et le garde-fou `test_kb_output_reaches_the_openapi_document`
    # le refuse — « une déclaration qui n'atteint pas le document est décorative ».
    # Le rendre obligatoire garantit aussi qu'une `op` future ne pourra pas l'oublier.
    visible_to: str
    # Distingue « je viens de la créer » de « elle existait déjà ». Sans lui, un
    # appelant ne peut pas savoir qu'il a fait naître une ressource partagée.
    created: bool


def _anchored_kb(org: int) -> "tuple[Optional[int], Optional[dict]]":
    """(ancre, projet) — projet None si l'ancre est absente OU pendouillante
    (projet disparu / archivé / plus org-owned de CETTE org, ex. transféré)."""
    pid = org_store.get_kb_project_id(org)
    if pid is None:
        return None, None
    p = db.get_project_by_id(pid)
    ok = (p is not None and p.get("archived_at") is None
          and p.get("owner_type") == "org" and str(p.get("owner_id")) == str(org))
    return pid, (p if ok else None)


def _kb(ctx: ResolvedCtx, inp: KbInput) -> dict:
    org = ctx.org_id
    if org is None:
        raise AuthzDenied(400, "no_active_org", "Aucune org active.")
    pid, kb = _anchored_kb(org)
    cree = False
    if kb is None:
        if inp.op == "get":
            # Lecture pure : ni création, ni réparation d'ancre pendouillante
            # (les deux écrivent). L'appelant qui a besoin d'un project_id pour
            # ÉCRIRE demande `ensure` ; celui qui affiche une zone Documents
            # vide n'a rien à créer pour ça.
            return {"project_id": None, "name": KB_NAME, "brief_md": "",
                    # Le garde-fou `test_kb_output_holds_for_every_op` exige que CHAQUE
                    # op rende les champs déclarés : ici la base n'existe pas encore,
                    # mais dire dès la lecture ce qu'elle SERA évite de l'apprendre
                    # après l'avoir créée.
                    "visible_to": _VISIBLE_TO, "created": False}
        # ⚠️ RÉPARER n'est pas CRÉER, et deux bancs existants l'ont rappelé. Une ancre
        # présente mais pendouillante (projet archivé, ou transféré hors de l'org) veut
        # dire que cette org AVAIT une base : la rétablir ne fait naître aucune
        # ressource que personne n'a voulue. Le cran ne porte donc que sur la PREMIÈRE
        # création — aucune ancre, aucune trace.
        if pid is None and not inp.create_shared:
            # Le refus PORTE le geste, les deux : celui qu'on demandait peut-être, et
            # celui qu'on voulait vraiment. Sans le second, on pousse à confirmer sans
            # savoir qu'il existait une autre réponse.
            raise AuthzDenied(
                409, "shared_creation_not_confirmed",
                "Cette org n'a pas encore de base de connaissance, et en créer une "
                "pose un projet visible de TOUS ses membres — ce n'est pas un espace "
                "personnel. Deux gestes possibles, choisis exprès : pour la base "
                "PARTAGÉE de l'org, rappelle `oto_kb op=ensure create_shared=true` ; "
                "pour un espace à toi seul, invisible même des administrateurs, "
                "`oto_project op=create` (owner_type='user'). Si la demande disait "
                "« ma » base de connaissance, c'est le second.")
        if pid is not None:
            # Ancre pendouillante — compare-and-clear (jamais écraser une réparation
            # concurrente déjà re-posée).
            org_store.clear_kb_project(org, pid)
        # ⚠️ Projet possédé par l'ORG : visible de TOUS ses membres, créé ici par
        # n'importe quel membre authentifié (`SUB_ONLY`). C'est voulu — une base de
        # connaissance est faite pour être partagée — mais l'appelant doit l'APPRENDRE
        # de la réponse (`visible_to`, `created`), pas le découvrir après coup.
        cree = True
        new_pid = db.create_project("org", str(org), KB_NAME, KB_BRIEF, created_by=ctx.sub)
        if org_store.claim_kb_project(org, new_pid):
            db.log_project_activity(new_pid, ctx.sub, "kb.create", KB_NAME)
        else:
            # Un appel concurrent a gagné le claim — son projet est LA KB, le
            # doublon fraîchement créé est archivé (pas de delete dur des projets).
            db.archive_project(new_pid)
        _, kb = _anchored_kb(org)
        if kb is None:
            raise AuthzDenied(409, "kb_unavailable",
                              "La base de connaissance n'a pas pu être résolue — réessaie.")
    return {"project_id": kb["id"], "name": kb["name"],
            "brief_md": kb.get("brief_md", ""),
            "visible_to": _VISIBLE_TO, "created": bool(cree)}


CAPABILITIES += [
    Capability(
        key="me.kb", handler=_kb, Input=KbInput, authz=SUB_ONLY, Output=KbView,
        description=(
            "Resolve the active org's KNOWLEDGE BASE — a single dedicated project, "
            f"seeded as \"{KB_NAME}\" and freely renamable: it is anchored by project "
            "id, so NEVER look it up by name, call this tool. This is the org-wide "
            "Documents space; its pages "
            "are managed with oto_doc (tree, versions, public share, change requests). "
            "⚠️ CREATING it is now an EXPLICIT act: `ensure` on an org that "
            "has no knowledge base yet REFUSES (`shared_creation_not_confirmed`) and "
            "names both moves — `create_shared=true` for the ORG's shared base, or "
            "`oto_project op=create` (owner_type='user') for a space only you can see. "
            "If the request said « MY knowledge base », it is the second. Repairing a "
            "dangling anchor is not creating, and needs no confirmation. "
            "⚠️ It belongs to the ORG and is visible to EVERY member — « the knowledge "
            "base » is never a personal space, whatever the request sounded like. The "
            "answer says so in `visible_to`, and `created: true` tells you that YOU just "
            "brought a shared project into existence. For something only you can see, "
            "make a project instead (`oto_project op=create`, owner_type='user'). "
            "op=\"get\" (default) READS the anchor and returns project_id=null when the "
            "org has no knowledge base yet — it never creates one, so opening a "
            "Documents view costs the org nothing. op=\"ensure\" resolves it, repairs a "
            "dangling anchor, and creates the shared base ONLY with create_shared=true: "
            "use it right before writing the first page, not to look."
        ),
        mcp="oto_kb",
        rest=RestBinding("POST", "/api/me/kb"),
    ),
]
