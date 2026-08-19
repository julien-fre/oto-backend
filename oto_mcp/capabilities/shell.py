"""`/shell` v0 — le CHROME de l'application, en un appel, en lecture seule.

Ce que le front rend en permanence sur ses huit écrans et qui ne change pas quand on
navigue : l'entreprise, la personne, le rail rangé en sections, les compteurs, et un
index court de connecteurs pour la palette. Contracté dans `shell-contract.md` (front)
et accepté le 16/08 (`reponse-shell-contract-2026-08-16.md`) — **surface PRÉCOCE et
déclarée provisoire** : les FORMES se contractent, le stockage reste variable.

**Ce n'est ni un index, ni une recherche, ni une liste paginable.** L'argument du front
est le bon et il commande tout le reste : une coquille partielle ne rendrait pas « la
suite au prochain appel », elle rendrait « ce nœud est introuvable » pour un nœud qui
existe. D'où : aucune pagination, et c'est la PROFONDEUR qui se coupe (compteurs `more`,
le patron de l'épine), jamais la liste.

**Les sections sont CALCULÉES à l'appel, jamais stockées** — elles sont la projection
de l'ownership (ADR 0049) sur une personne : `everyone` = l'org, `team` = une par équipe
dont elle est membre, `private` = ce qu'elle possède, `shared` = ce qu'on lui a partagé
en direct et qu'aucune équipe ne couvre déjà.

Trois garanties du contrat, tenues ici :

1. **Pas de doublon** — un nœud est dans EXACTEMENT une section. Le rangement est un
   ordre de priorité (org > équipe > soi > partage direct), pas une union : un nœud
   accessible par son équipe ET partagé nominativement se range sous l'équipe. Sans
   ça le rail montre deux fois la même page, et on en déduit qu'il y en a deux.
2. **`shared` est conditionnelle et sans `context`** — absente quand vide, et le SEUL
   cas où une section n'a pas de ligne de contexte : une section de partages reçus ne
   range rien, donc elle n'a pas de « + ».
3. **L'ordre vient de nous** — le front n'en fabrique aucun.

⚠️ **`private` = TOUS les nœuds que la personne possède, y compris ceux qu'elle a
partagés.** Le contrat dit « ses nœuds à lui, partagés avec personne » ; on sert plus
large, et c'est délibéré : c'est SON rail. Une page qu'elle a écrite puis partagée
disparaîtrait de son propre rail au moment où elle la partage — le partage se lit
alors comme une perte. La nuance est ici pour qu'elle soit vue, pas subie.
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Literal, Optional

from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from .. import access, connector_selection, group_store, org_store
from ..db import shell as db_shell
from ._authz import ORG_MEMBER
from ._types import Capability, NotModified, ResolvedCtx, RestBinding
from .registry import CAPABILITIES

logger = logging.getLogger(__name__)

# Le patron de l'épine, aux mêmes chiffres : la carte se lit d'un coup d'œil ou elle
# ne sert à rien. Ce qui dépasse est COMPTÉ (`more`), jamais coupé en silence.
_PROFONDEUR = 2
_BUDGET_PAR_SECTION = 200

# `type` choisit le GLYPHE de la ligne, pas l'écran (contrat §2). `agent` et `execution`
# sont au contrat mais n'ont pas de source : 0054-D6/D7 les prévoient, aucun code ne les
# écrit, et le préalable est un arbitrage qui appartient au front (« Agents /
# procédures »). On ne les invente pas — un nœud sans genre connu se rend en `page`.
_TYPE_PAR_KIND = {"page": "page", "tableau": "table"}


class ShellInput(BaseModel):
    # La version que le client porte déjà. Égale à la nôtre ⟹ 304, et il garde son
    # cache. `rev` et non un `ETag` : c'est NOTRE convention (celle d'`oto_doc`), et
    # le front a demandé la nôtre plutôt que d'imposer la sienne.
    rev: Optional[str] = None


class RailNode(BaseModel):
    id: str
    name: str
    type: Literal["page", "table", "agent", "execution"]
    badge: Optional[str] = None
    sharedBy: Optional[str] = None
    children: Optional[list["RailNode"]] = None
    # Nombre d'enfants NON rendus (profondeur ou budget). Compté, jamais tu : une
    # branche coupée sans compteur se lit comme une branche vide.
    more: Optional[int] = None


class RailContext(BaseModel):
    name: str


class RailSection(BaseModel):
    id: str
    name: str
    kind: Literal["everyone", "team", "private", "shared"]
    # Absent sur `shared` SEULEMENT (contrat §3, garantie 2).
    context: Optional[RailContext] = None
    # L'objet dont la section dérive (l'équipe). Servi, JAMAIS rendu : il sert à
    # l'invalidation et à la navigation. Absent hors `team`.
    origin: Optional[str] = None
    nodes: list[RailNode] = []


class ShellCompany(BaseModel):
    name: Optional[str] = None
    logoUrl: Optional[str] = None


class ShellUser(BaseModel):
    name: Optional[str] = None


class ShellConnector(BaseModel):
    id: str
    name: str
    # Le pont outil → branchement. Sans lui, aucun type ne voyait le 404 (le front a
    # eu le bug) — c'est la raison d'être de cette liste, pas un ornement.
    connectionId: str


class ShellOut(BaseModel):
    company: ShellCompany
    user: ShellUser
    sections: list[RailSection]
    # Des choses qui ATTENDENT la personne, jamais des volumes. Les compteurs sans
    # source sont ABSENTS plutôt que faux : un `0` affirme « rien ne t'attend ».
    counters: dict[str, int]
    connectors: list[ShellConnector]
    rev: str
    # Partages reçus qu'aucun nœud ne porte encore (les procédures — cf. `db/shell`).
    # Rendu pour qu'une section « Partagé » incomplète ne se lise pas comme vide.
    grants_sans_noeud: Optional[int] = None


def _type_of(kind: str) -> str:
    return _TYPE_PAR_KIND.get(kind or "", "page")


def _arbre(lignes: list[dict], *, shared_par: Optional[dict] = None) -> list[RailNode]:
    """Les nœuds d'une section en ARBRE, bornés en profondeur ET en nombre.

    Même patron que l'épine d'un projet : une seule lecture, assemblage en mémoire
    (les arbres d'une section sont petits), et ce qui dépasse est compté. Les racines
    sont les nœuds dont le parent n'appartient PAS à la section — sinon une page dont
    le parent vit ailleurs (autre propriétaire) disparaîtrait du rail au lieu d'y
    remonter à la racine.
    """
    par_parent: dict = {}
    presents = {l["id"] for l in lignes}
    for l in lignes:
        par_parent.setdefault(l["parent_id"], []).append(l)
    budget = {"n": 0}

    def _sous_arbre(node_id) -> int:
        n, pile = 0, [node_id]
        while pile:
            for k in par_parent.get(pile.pop(), []):
                n += 1
                pile.append(k["id"])
        return n

    def _noeud(l: dict, prof: int) -> RailNode:
        budget["n"] += 1
        out = RailNode(id=l["public_id"], name=l.get("title") or "",
                       type=_type_of(l.get("kind")))
        if shared_par is not None:
            out.sharedBy = shared_par.get(l["public_id"])
        enfants = par_parent.get(l["id"], [])
        if not enfants:
            return out
        if prof <= 0 or budget["n"] >= _BUDGET_PAR_SECTION:
            out.more = _sous_arbre(l["id"])
            return out
        rendus = []
        for k in enfants:
            if budget["n"] >= _BUDGET_PAR_SECTION:
                out.more = len(enfants) - len(rendus)
                break
            rendus.append(_noeud(k, prof - 1))
        out.children = rendus
        return out

    racines = [l for l in lignes if l["parent_id"] not in presents]
    # ⚠️ Le budget borne AUSSI les racines, pas seulement les enfants. L'épine d'un
    # projet n'a qu'une racine, donc la question ne s'y posait pas ; une section en a
    # autant que l'org a de pages de premier niveau. Sans cette borne, `everyone` rend
    # les 400 racines d'une org fournie et le plafond ne protège rien — c'est le cas
    # que le budget existe pour couvrir.
    rendues = []
    for r in racines:
        if budget["n"] >= _BUDGET_PAR_SECTION:
            break
        rendues.append(_noeud(r, _PROFONDEUR))
    coupees = len(racines) - len(rendues)
    if coupees and rendues:
        # Le reste est COMPTÉ sur la dernière racine rendue : le contrat interdit une
        # pagination, pas un compteur — et une liste tronquée sans compteur se lit
        # comme une liste complète.
        rendues[-1].more = (rendues[-1].more or 0) + coupees
    return rendues


def _connecteurs(sub: str, org_id: Optional[int]) -> list[ShellConnector]:
    """Le VERDICT EFFECTIF, borné aux connecteurs INSTALLÉS de la personne.

    ⚠️ **Jamais le catalogue.** Résoudre les ~90 connecteurs déclarés fait marcher la
    cascade pour chacun — c'est la promenade qui a gelé le handshake le 15/08. Sa
    toolbox en compte une poignée, et c'est elle que la palette doit proposer : un
    outil que la personne n'a pas installé n'a rien à faire dans SA palette.

    Un connecteur sans branchement résoluble n'entre pas dans la liste (contrat §7) —
    la palette ne propose que ce qui marcherait si on cliquait.
    """
    if org_id is None:
        return []
    out: list[ShellConnector] = []
    for nom, etat in sorted(connector_selection.list_selection(sub, org_id).items()):
        if etat != "active":
            continue
        try:
            mode = access.credential_mode_for(sub, nom)
        except Exception:      # un connecteur qui ne résout pas ne casse pas le rail
            logger.warning("shell: verdict connecteur %s indisponible", nom, exc_info=True)
            continue
        if mode in ("forbidden", "over_quota"):
            continue
        # `connectionId` = le palier qui RÉSOUT. C'est ce que le front appelle « le
        # branchement » : deux personnes du même outil peuvent résoudre par des
        # paliers différents, et l'identifiant doit le refléter.
        out.append(ShellConnector(id=nom, name=nom, connectionId=f"{mode}:{nom}"))
    return out


def _rev(corps: dict) -> str:
    """Empreinte du CORPS canonique — la version que le client renvoie pour un 304.

    Sur le corps sérialisé trié, pas sur un `updated_at` max : le rail dépend d'un
    nom d'org, d'une appartenance d'équipe et d'un verdict de connecteur, dont aucun
    ne touche `nodes.updated_at`. Une empreinte de contenu ne peut pas rater un
    changement qu'elle ne sait pas nommer.
    """
    return hashlib.sha256(
        json.dumps(corps, sort_keys=True, ensure_ascii=False, default=str).encode()
    ).hexdigest()[:32]


def _compose(ctx: ResolvedCtx) -> dict:
    """Tout le travail SYNCHRONE du rail. Appelé hors boucle (cf. `_shell`)."""
    sub, org_id = ctx.sub, ctx.org_id
    org = org_store.get_org(org_id) if org_id is not None else None
    equipes = group_store.list_groups_for_user(sub, org_id) if org_id is not None else []

    proprios = ([("org", str(org_id))] if org_id is not None else [])
    proprios += [("group", str(g["group_id"])) for g in equipes]
    proprios += [("user", sub)]
    lignes = db_shell.nodes_for_owners(proprios)

    par_proprio: dict = {}
    for l in lignes:
        par_proprio.setdefault((l["owner_type"], str(l["owner_id"])), []).append(l)

    sections: list[RailSection] = []
    if org_id is not None:
        sections.append(RailSection(
            id="g-everyone", name="Tout le monde", kind="everyone",
            context=RailContext(name="Contexte — Tout le monde"),
            nodes=_arbre(par_proprio.get(("org", str(org_id)), []))))
    for g in equipes:
        gid = str(g["group_id"])
        nom = g.get("name") or "Équipe"
        sections.append(RailSection(
            id=f"g-{gid}", name=nom, kind="team", origin=gid,
            context=RailContext(name=f"Contexte — {nom}"),
            nodes=_arbre(par_proprio.get(("group", gid), []))))
    sections.append(RailSection(
        id="sec-private", name="Privé", kind="private",
        context=RailContext(name="Contexte — Privé"),
        nodes=_arbre(par_proprio.get(("user", sub), []))))

    # ── `shared` : les partages DIRECTS, moins ce qu'une autre section range déjà ──
    grants = db_shell.direct_grants(sub)
    par_id, sans_noeud = db_shell.resolve_grant_nodes(grants)
    deja_rangés = {l["public_id"] for l in lignes}
    candidats = [pid for pid in par_id if pid not in deja_rangés]
    partages = db_shell.nodes_by_public_id(candidats)
    if partages:
        auteurs = db_shell.names_of(
            (par_id[l["public_id"]] or {}).get("granted_by") for l in partages)
        shared_par = {l["public_id"]:
                      auteurs.get((par_id[l["public_id"]] or {}).get("granted_by"))
                      for l in partages}
        sections.append(RailSection(
            id="sec-shared", name="Partagé", kind="shared",
            nodes=_arbre(partages, shared_par=shared_par)))

    corps = {
        "company": ShellCompany(name=(org or {}).get("name"),
                                logoUrl=(org or {}).get("logo_url")).model_dump(),
        "user": ShellUser(name=_nom_de(sub)).model_dump(),
        "sections": [s.model_dump(exclude_none=True) for s in sections],
        "counters": _compteurs(ctx),
        "connectors": [c.model_dump() for c in _connecteurs(sub, org_id)],
    }
    if sans_noeud:
        corps["grants_sans_noeud"] = sans_noeud
    corps["rev"] = _rev({k: v for k, v in corps.items() if k != "rev"})
    return corps


def _nom_de(sub: Optional[str]) -> Optional[str]:
    if not sub:
        return None
    return db_shell.names_of([sub]).get(sub)


def _compteurs(ctx: ResolvedCtx) -> dict:
    """Ce qui ATTEND la personne. `home` seul — les autres ABSENTS, jamais à zéro.

    Un `0` affirme « rien ne t'attend » ; une clé absente dit « on ne sait pas encore
    compter ça ». `agents` et `connectors` naîtront avec les surfaces qui savent les
    compter (accord du 16/08), pas avant.
    """
    try:
        from .inbox import InboxInput, _inbox
        return {"home": int(_inbox(ctx, InboxInput()).get("count") or 0)}
    except Exception:      # un compteur indisponible ne fait pas tomber le chrome
        logger.warning("shell: compteur home indisponible", exc_info=True)
        return {}


async def _shell(ctx: ResolvedCtx, inp: ShellInput):
    """Le chrome, ou un 304 si le client porte déjà notre version.

    ⚠️ **Tout le corps part au THREADPOOL.** Le serveur est mono-loop et cette lecture
    fait plusieurs requêtes DB plus une marche de cascade par connecteur installé —
    exécutée sur la boucle, elle gèlerait toutes les autres sessions. C'est la leçon
    du 15/08, prise à la construction plutôt qu'après l'incident.
    """
    corps = await run_in_threadpool(_compose, ctx)
    if inp.rev and inp.rev == corps.get("rev"):
        return NotModified(corps["rev"])
    return corps


CAPABILITIES += [
    Capability(
        key="me.shell", handler=_shell, Input=ShellInput, Output=ShellOut,
        authz=ORG_MEMBER,
        description=(
            "The application CHROME in one call: company, user, the rail in ordered "
            "SECTIONS (everyone / one per team / private / shared-when-not-empty), "
            "counters of things AWAITING you, and a short connector index for the "
            "command palette. Read-only, never paginated — depth is capped instead "
            "(`more` counts what was cut). Pass `rev` from a previous answer for a "
            "conditional read: unchanged returns `{not_modified: true, rev}` (HTTP 304 "
            "on REST) so you keep your cached copy. PROVISIONAL surface: the shape is "
            "contracted, not frozen."),
        mcp="oto_shell",
        rest=RestBinding("GET", "/api/me/shell", provisoire=True),
    ),
]
