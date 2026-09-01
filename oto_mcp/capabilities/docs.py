"""Doc — page markdown arborescente d'un projet (incrément 3, modèle produit 2026-06-27).

Un Doc appartient à un projet et **hérite de son accès** (`ownership.can_access` sur le
projet — pas d'ownership propre). Le `brief_md` du projet reste la page d'entrée ; les
Docs sont les pages, en arbre via `parent_id`. kind ∈ {doc (humain), note (agent),
source (import)}. CRUD + move, co-déclaré MCP+REST.
"""
from __future__ import annotations

import logging
import os
from typing import Literal, Optional

from pydantic import BaseModel

from .. import db, doc_patch, email, org_store, output_projection, ownership
from ._authz import PROJECT_SHARED_READ, SUB_ONLY
from ._types import AuthzDenied, Capability, ResolvedCtx, RestBinding
from .registry import CAPABILITIES
from .. import config

logger = logging.getLogger(__name__)

PROJECT_RTYPE = "project"


def _dash_url(sub: Optional[str] = None) -> str:
    """L'adresse du tableau de bord servie à ce compte (celle de son tenant s'il en
    a une). Sans `sub` : la nôtre — les surfaces anonymes n'ont pas de tenant."""
    return config.dashboard_url_for(sub)


def _email_of(sub: Optional[str]) -> Optional[str]:
    if not sub:
        return None
    return (db.get_user(sub) or {}).get("email")


def _locale_of(sub: Optional[str]) -> Optional[str]:
    """Préférence de langue du DESTINATAIRE (`users.locale`, oto-backend#700).
    None (pas de sub, ou compte sans préférence posée) ⟹ le gabarit sert FR,
    comportement inchangé."""
    if not sub:
        return None
    return (db.get_user(sub) or {}).get("locale")


def _project_url(sub: Optional[str], pid: Optional[int], org: Optional[int]) -> Optional[str]:
    """Le lien du projet TEL QU'IL EXISTE chez ce destinataire, ou None.

    ⚠️ On ne colle PAS notre chemin sous le domaine du partenaire : ses vues ne
    portent pas les mêmes adresses (`links.py`), et un lien mort est pire qu'un lien
    absent. `link_for` rend None quand le tenant n'a pas cette vue, et l'email part
    alors SANS bouton — la nouvelle reste utile sans lien.
    """
    if not pid:
        return None
    from .. import links
    return links.link_for("project", sub=sub, id=int(pid),
                          org=int(org) if org is not None else "")


def _brand_of(sub: Optional[str]) -> str:
    """Le nom du produit sous lequel CE destinataire nous connaît. « ouvrir dans oto »
    dans un email envoyé à un utilisateur d'un tenant tiers est un faux, même quand l'URL est
    juste."""
    _base, marque = config.front_for(sub)
    return marque or "oto"


def _notify_cr_created(pid: int, proposer_sub: str, *, is_create: bool,
                       doc_title: Optional[str]) -> None:
    """Prévient les VALIDATEURS qu'une proposition attend (oto/#6, « les auteurs
    valident »). Destinataires = org_admins de l'org du projet + le propriétaire si le
    projet est user-owned, SAUF le proposeur. Best-effort — ne casse jamais la création."""
    try:
        project = db.get_project_by_id(int(pid)) or {}
        pname = project.get("name")
        org = project.get("context_org_id")
        # On garde le SUB à côté de l'email : chaque destinataire peut vivre sous un
        # produit différent, donc l'adresse et la marque se calculent PAR PERSONNE.
        # Une seule URL pour tout le monde envoyait la moitié des validateurs chez un
        # produit qu'ils n'ont pas.
        recips: set[tuple[str, str]] = set()
        if org is not None:
            for m in org_store.list_org_members(int(org)):
                if m.get("org_role") == "org_admin" and m.get("sub") != proposer_sub:
                    if e := _email_of(m.get("sub")):
                        recips.add((str(m["sub"]), e))
        if project.get("owner_type") == "user" and project.get("owner_id") != proposer_sub:
            if e := _email_of(project.get("owner_id")):
                recips.add((str(project["owner_id"]), e))
        if not recips:
            return
        proposer = (db.get_user(proposer_sub) or {}).get("name") or (db.get_user(proposer_sub) or {}).get("email")
        for sub_dest, to in recips:
            email.send_change_request_email(
                to, project_name=pname, doc_title=doc_title, proposer=proposer,
                is_create=is_create, app_url=_project_url(sub_dest, pid, org),
                brand=_brand_of(sub_dest), locale=_locale_of(sub_dest))
    except Exception as e:  # best-effort
        logger.warning("notify CR created (project %s) failed: %s", pid, e)


def _notify_cr_resolved(cr: dict, accepted: bool) -> None:
    """Prévient le PROPOSEUR que sa proposition a été tranchée (oto/#6). Best-effort."""
    try:
        to = _email_of(cr.get("requested_by"))
        if not to:
            return
        pname = cr.get("project_name")
        pid = cr.get("project_id") or (cr.get("doc_id") and (db.get_doc_by_id(int(cr["doc_id"])) or {}).get("project_id"))
        dest = cr.get("requested_by")
        email.send_change_request_resolved_email(
            to, project_name=pname, doc_title=cr.get("doc_title"), accepted=accepted,
            app_url=_project_url(dest, pid, None), brand=_brand_of(dest),
            locale=_locale_of(dest))
    except Exception as e:  # best-effort
        logger.warning("notify CR resolved (#%s) failed: %s", cr.get("id"), e)


def _public_doc_url(token: str, sub: Optional[str] = None) -> Optional[str]:
    """Lien public d'un doc partagé (gap #4a). Suit le tenant de celui qui partage :
    ce lien part chez des TIERS, c'est la vitrine la plus visible de la marque.

    `None` si le produit du partenaire n'a pas de page publique — la page reste
    partagée, elle n'a simplement pas d'adresse à sa marque."""
    from .. import links
    return links.link_for("public_doc", sub=sub, token=token)


class DocInput(BaseModel):
    op: Literal["create", "bulk_create", "list", "search", "get", "update", "patch",
                "delete", "move", "revisions", "request_change", "list_changes",
                "resolve_change", "set_public", "backlinks"]
    project_id: Optional[int] = None   # create / list / search
    doc_id: Optional[int] = None       # get / update / delete / move / request_change / list_changes
    query: Optional[str] = None        # search : termes recherchés dans titre + corps
    parent_id: Optional[int] = None    # create / move (None = 1er niveau sous le projet)
    title: Optional[str] = None
    body_md: Optional[str] = None
    kind: Optional[Literal["doc", "note", "source"]] = None
    description: Optional[str] = None  # chapô (Ship 2) — '' efface (fallback dérivé)
    position: Optional[int] = None     # move : INDEX cible (0-based) dans la fratrie
    request_id: Optional[int] = None   # resolve_change
    message: Optional[str] = None      # request_change : note libre du demandeur
    accept: Optional[bool] = None      # resolve_change : True = accepter (applique), False = refuser
    public: Optional[bool] = None      # set_public : True = partager publiquement, False = retirer
    expected_rev: Optional[str] = None  # update/patch : rev (ETag) lue par le client → conflit optimiste
    section: Optional[str] = None       # patch : titre (heading markdown) de la section ciblée
    # patch : la RÉGION sans titre visée, sur un axe SÉPARÉ de `section` (cf. op=patch).
    region: Optional[Literal["preamble"]] = None
    mode: Optional[Literal["replace", "append", "prepend", "delete"]] = None  # patch : défaut replace
    to_project: Optional[int] = None    # move : projet cible (déplacer la page + son sous-arbre)
    pages: Optional[list[dict]] = None  # bulk_create : [{title, body_md?, kind?, description?, parent_index?}]
    # Projection de SORTIE, honorée par list/get/create/update/patch/move (`_FIELDS_OPS`)
    # et REFUSÉE ailleurs. Omis : la liste rend son index, `get` la page entière, une
    # écriture son accusé. `["*"]` = la page entière partout.
    fields: Optional[list[str]] = None


def _require(cond, code: str, msg: str, status: int = 400) -> None:
    if not cond:
        raise AuthzDenied(status, code, msg)


def _can(sub: Optional[str], project_id: int, want: str) -> bool:
    """Droit d'accès aux pages d'un projet. `sub is None` = destinataire d'un endpoint
    publié (ADR 0032) : LECTURE seule, et seulement sur LE projet publié — jamais
    l'arbre documentaire de l'org (pendant de `_anon_project_tableau_ns_ids`).
    Fail-closed : hors de ce projet, ou pour une écriture, c'est non."""
    if sub is None:
        if want != "read":
            return False
        from .. import subdomain_project
        pid = subdomain_project.current_anon_project_id()
        return (pid is not None and int(pid) == int(project_id)
                and subdomain_project.current_anon_docs_exposed())
    return ownership.can_access(sub, PROJECT_RTYPE, str(project_id), want)


# Ops servies au destinataire d'un projet publié : LECTURE seule. Tout le reste
# (création, édition, déplacement, publication de page, propositions) exige un `sub`
# — même posture que les tools de gouvernance du datastore.
# `search` en est ABSENT : il délègue à `search_mod.search(sub, …)`, dont le scoping
# est bâti sur un `sub` (projets accessibles). Le destinataire lit l'arbre (`list`)
# puis la page (`get`) — pas de chemin de recherche tant qu'il n'est pas scopé.
_SHARED_READ_OPS = frozenset({"list", "get", "revisions", "backlinks"})


def _doc_url(sub: Optional[str], row: dict) -> Optional[str]:
    """L'adresse de CETTE page chez ce lecteur, ou None (signal #599).

    Le manque remonté : après `op=create`, la réponse porte l'id, le projet, le `rev`
    — rien qui réponde à « et je la lis où ? ». Les contournements observés étaient
    tous mauvais : rendre la page publique (inacceptable pour de l'interne), ou
    RECONSTRUIRE l'adresse en lisant le routeur du tableau de bord — un patron appris
    par cœur dans une consigne, qui fabrique des liens plausibles et faux dès que la
    route bouge. L'adresse se sert donc d'ici, où elle est déjà connue, comme
    `data_url` la sert pour un tableau depuis toujours.

    `None` n'est pas un échec : le produit du lecteur peut n'avoir aucune vue de page
    (`links.link_for`, « pas de patron, pas de lien »), et le patron d'un tenant peut
    réclamer un paramètre qu'on ne porte pas ici — `{org}` par exemple, qu'une page ne
    connaît pas sans une requête de plus par ligne de liste. Dans les deux cas la
    réponse part SANS adresse, ce qui reste juste ; un lien mort, lui, ne se
    diagnostique pas, il se subit."""
    from .. import links
    return links.link_for("doc", sub=sub, id=row.get("id"),
                          project_id=row.get("project_id"))


def _view(row: dict, sub: Optional[str] = None) -> dict:
    out = {k: row.get(k) for k in
           ("id", "project_id", "parent_id", "title", "description", "position",
            "body_md", "kind", "created_at", "updated_at")}
    # L'adresse web de la page, à côté de son id (#599).
    out["url"] = _doc_url(sub, row)
    # rev = ETag de contenu : à relire par le client et repasser en `expected_rev`
    # sur op=update pour détecter un écrasement concurrent (oto/#6).
    out["rev"] = db.doc_rev(row.get("title"), row.get("body_md"))
    tok = row.get("public_token")
    out["public"] = bool(tok)
    out["public_url"] = _public_doc_url(tok, sub) if tok else None
    return out


# Colonnes gardées quoi qu'on demande : de quoi ADRESSER la page ensuite (la relire, la
# patcher, la situer dans l'arbre). UNE seule liste pour la liste, la lecture projetée et
# l'accusé d'écriture — trois règles d'adressage divergeraient à la première évolution.
# `url` en fait partie depuis #599 : l'accusé d'une écriture est justement le moment
# où l'on demande « c'est où ? », et c'est le moment où la projection est la plus
# agressive. Une adresse qu'une projection emporte ne répond jamais à la question.
_ALWAYS = ("id", "project_id", "parent_id", "title", "url")

# Les ops qui savent PROJETER leur sortie. Passer `fields` ailleurs est REFUSÉ, pas avalé :
# c'est la leçon générale du signal #461, où `op=get` acceptait `fields` et rendait quand
# même les ~30 K caractères de la page. Un argument accepté-et-ignoré coûte exactement ce
# qu'il prétendait économiser, et rien ne le signale à l'appelant.
_FIELDS_OPS = frozenset({"list", "get", "create", "update", "patch", "move"})

# La phrase servie dans la notice d'un ACCUSÉ d'écriture. Distincte de « vue de tri » :
# l'agent ne trie pas, il vient d'écrire — lui dire le contraire l'enverrait relire.
# La poignée du préambule, écrite UNE seule fois : elle sert dans le refus d'ambiguïté,
# dans le refus « cible manquante » et dans le refus « section introuvable » qui la
# pointe. Trois formulations divergeraient à la première évolution.
_POIGNEE_PREAMBULE = 'region="preamble"'


def _refus_section_introuvable(heading: str, disponibles: list, corps: str) -> str:
    """Le message d'un `section=` qui ne résout pas : les sections DISPONIBLES, et —
    si la page en a un — le rappel que le préambule existe et n'est pas une section.

    C'est le garde-fou du piège du mot réservé (signaux #481/#492/#507, qui
    proposaient `section="__preamble__"`) : cette forme-là ne devient JAMAIS un
    synonyme silencieux de la région — elle se heurte à ce refus, qui apprend la
    bonne poignée. Sans quoi une page portant un vrai titre « __preamble__ »
    deviendrait inadressable, et le jour où ça arrive rien ne le signalerait."""
    dispo = ", ".join(disponibles) or "(aucune)"
    msg = f"Section « {heading} » introuvable. Sections disponibles : {dispo}."
    if doc_patch.preamble(corps).strip():
        msg += (f" Cette page a aussi un PRÉAMBULE (ce qui précède le premier titre : "
                f"bandeau de provenance, front-matter) : ce n'est pas une section, il "
                f"s'adresse par {_POIGNEE_PREAMBULE}.")
    return msg


_HINT_ACCUSE = ("Accusé d'écriture : la page est enregistrée, son corps n'est pas rejoué "
                "— tu viens de l'écrire. "
                '`fields=["*"]` le rend, `fields=[…]` choisit les colonnes.')


def _projected(row: dict, sub: Optional[str], fields: Optional[list[str]], *,
               brut_par_defaut: bool, hint: Optional[str] = None) -> dict:
    """Une page passée au MÊME seam de projection que la liste (`summarize`).

    **La décision de forme, et son pourquoi (signaux #461, #506, #525, #530) :**

    - Une **LECTURE** (`op=get`) rend la page ENTIÈRE par défaut : livrer le contenu EST
      son travail, et le dashboard en dépend (la revue de proposition affiche le `body_md`
      de cette réponse). Elle honore `fields` quand on lui en donne — le cas courant étant
      « relis-moi juste le `rev` avant de patcher », que `update`/`patch` exigent.
    - Une **ÉCRITURE** (`create`/`update`/`patch`/`move`) rend un **ACCUSÉ** par défaut :
      identité, titre, `rev`, `updated_at`, et la TAILLE du corps. L'appelant vient
      d'écrire ce corps — le lui rejouer ne lui apprend rien et lui coûte tout : sur les
      deux pages réelles de la KB d'un client (128 K et 85 K caractères), la réponse
      dépassait le plafond de résultat du client, si bien qu'**une écriture RÉUSSIE était
      rendue à l'agent comme un échec** (#530). Un agent qui lit cet échec au premier degré
      réécrit — double écriture — ou déclare l'opération ratée. Le corps reste à un
      `fields=["*"]` de distance.
    - Ce n'est pas une exception mais un RALLIEMENT : les écritures qui ne passaient pas
      par `_view` rendent déjà un accusé (`bulk_create`, `delete`, `set_public`). Les
      quatre ci-dessus étaient les dernières à rejouer la page.

    ⚠️ Projeter ≠ tronquer : on retire des COLONNES et on le DIT (`projection`), on ne
    coupe jamais un texte — sinon l'agent croit avoir lu."""
    if fields is None and brut_par_defaut:
        return _view(row, sub)
    rows, notice = output_projection.summarize(
        [_view(row, sub)], body_fields=("body_md",), fields=fields,
        always=_ALWAYS, hint=hint)
    return {**rows[0], **({"projection": notice} if notice else {})}


def _doc(ctx: ResolvedCtx, inp: DocInput) -> dict:
    sub = ctx.sub
    if sub is None:
        # Endpoint publié sans login : l'autz a déjà validé le contexte, on borne ici
        # les VERBES (lecture seule). `_can(None, …)` borne le PÉRIMÈTRE au projet.
        _require(inp.op in _SHARED_READ_OPS, "forbidden",
                 "Lecture seule sur un projet partagé.", 403)

    # `fields` se valide UNE fois, pour toutes les ops — et APRÈS l'autz, pour qu'un
    # appelant anonyme se heurte au 403 plutôt qu'à un 400 qui lui décrirait la surface.
    if inp.fields is not None:
        _require(inp.op in _FIELDS_OPS, "unsupported_fields",
                 f"`fields` ne s'applique qu'aux ops {', '.join(sorted(_FIELDS_OPS))} — "
                 f"op={inp.op} rend une forme fixe. Retire-le.")
        _require(bool(inp.fields), "empty_fields",
                 "`fields` est une liste vide : omets-le pour la vue par défaut, passe "
                 '`["*"]` pour la page entière, ou nomme les colonnes voulues.')

    if inp.op == "create":
        _require(inp.project_id is not None, "missing_project", "`project_id` requis.")
        _require(inp.title and inp.title.strip(), "missing_title", "`title` requis.")
        # « Les lecteurs proposent » (Ship 3) : un viewer (lecture SANS écriture) qui
        # crée obtient une PROPOSITION de création, pas la page.
        if not _can(sub, inp.project_id, "write"):
            _require(_can(sub, inp.project_id, "read"), "forbidden", "Accès refusé.", 403)
            req = db.add_doc_change_request(
                sub, project_id=int(inp.project_id), proposed_parent_id=inp.parent_id,
                proposed_kind=(inp.kind or "doc"),
                proposed_title=inp.title.strip(), proposed_body_md=inp.body_md or "",
                message=inp.message)
            _notify_cr_created(int(inp.project_id), sub, is_create=True, doc_title=None)
            return {"status": "proposal_created", "request": req}
        if inp.parent_id is not None:
            parent = db.get_doc_by_id(int(inp.parent_id))
            _require(parent and parent["project_id"] == inp.project_id, "bad_parent",
                     "Parent invalide (autre projet ou inexistant).")
        did = db.create_doc(int(inp.project_id), inp.title.strip(), parent_id=inp.parent_id,
                            body_md=inp.body_md or "", kind=(inp.kind or "doc"), created_by=sub,
                            description=inp.description)
        db.log_project_activity(int(inp.project_id), sub, "doc.create", inp.title.strip())
        return _projected(db.get_doc_by_id(did), sub, inp.fields,
                          brut_par_defaut=False, hint=_HINT_ACCUSE)

    if inp.op == "bulk_create":
        # A4 (#6) : créer N pages en UN appel (33 pages ≠ 33 allers-retours). Arbre en un
        # coup via `parent_index` (index d'une page PLUS TÔT dans le lot) ; sinon `parent_id`.
        _require(inp.project_id is not None, "missing_project", "`project_id` requis.")
        _require(_can(sub, inp.project_id, "write"), "forbidden", "Écriture refusée.", 403)
        _require(bool(inp.pages), "missing_pages", "`pages` (liste non vide) requis.")
        if inp.parent_id is not None:
            par = db.get_doc_by_id(int(inp.parent_id))
            _require(par and par["project_id"] == inp.project_id, "bad_parent",
                     "`parent_id` invalide (autre projet ou inexistant).")
        created: list[int] = []
        for i, p in enumerate(inp.pages):
            title = str(p.get("title") or "").strip()
            _require(bool(title), "missing_title", f"page #{i} sans `title`.")
            pi = p.get("parent_index")
            parent = created[pi] if isinstance(pi, int) and 0 <= pi < len(created) else inp.parent_id
            created.append(db.create_doc(
                int(inp.project_id), title, parent_id=parent, body_md=p.get("body_md") or "",
                kind=(p.get("kind") or "doc"), created_by=sub, description=p.get("description")))
        db.log_project_activity(int(inp.project_id), sub, "doc.bulk_create", f"{len(created)} pages")
        return {"created": created, "count": len(created)}

    # ── Propositions (Ship 3) — AVANT le gate doc_id : une proposition de CRÉATION a
    # doc_id=NULL, elle serait inatteignable sinon. On résout le projet par request_id
    # (resolve) / project_id (create-proposal, list) / doc_id (modif, legacy).
    if inp.op == "resolve_change":
        _require(inp.request_id is not None, "missing_request", "`request_id` requis.")
        cr = db.get_doc_change_request(int(inp.request_id))
        _require(cr is not None, "unknown_request", "Demande inconnue.", 404)
        _require(cr["status"] == "pending", "already_resolved", "Demande déjà traitée.")
        cr_pid = cr.get("project_id") or (
            (db.get_doc_by_id(int(cr["doc_id"])) or {}).get("project_id") if cr.get("doc_id") else None)
        _require(cr_pid is not None, "unknown_request", "Cible de la demande introuvable.", 404)
        _require(_can(sub, cr_pid, "write"), "forbidden", "Écriture refusée.", 403)
        if inp.accept:
            if cr.get("doc_id"):
                # MODIF : la page cible existe-t-elle encore ? sinon on ferme (motif).
                if db.get_doc_by_id(int(cr["doc_id"])) is None:
                    db.resolve_doc_change_request(int(inp.request_id), "rejected", sub)
                    _notify_cr_resolved(cr, False)
                    return {"ok": True, "id": inp.request_id, "accepted": False,
                            "reason": "page supprimée"}
                db.update_doc(int(cr["doc_id"]),
                              title=(cr.get("proposed_title") or None),
                              body_md=cr.get("proposed_body_md"), edited_by=sub)
            else:
                # CRÉATION : parent supprimé entre-temps → rattache à la racine (mention).
                parent = cr.get("proposed_parent_id")
                if parent is not None and db.get_doc_by_id(int(parent)) is None:
                    parent = None
                db.create_doc(int(cr_pid), (cr.get("proposed_title") or "Sans titre"),
                              parent_id=parent, body_md=cr.get("proposed_body_md") or "",
                              kind=(cr.get("proposed_kind") or "doc"), created_by=sub)
            db.resolve_doc_change_request(int(inp.request_id), "accepted", sub)
            db.log_project_activity(int(cr_pid), sub, "doc.change_accepted", cr.get("proposed_title"))
        else:
            db.resolve_doc_change_request(int(inp.request_id), "rejected", sub)
            db.log_project_activity(int(cr_pid), sub, "doc.change_rejected", cr.get("proposed_title"))
        _notify_cr_resolved(cr, bool(inp.accept))
        return {"ok": True, "id": inp.request_id, "accepted": bool(inp.accept)}

    if inp.op == "list_changes" and inp.project_id is not None:
        # Toutes les propositions en attente d'un PROJET (drawer « Propositions (N) »).
        _require(_can(sub, inp.project_id, "write"), "forbidden", "Écriture refusée.", 403)
        return {"project_id": inp.project_id,
                "requests": db.list_change_requests_by_project([int(inp.project_id)])}

    if inp.op == "request_change" and inp.doc_id is None:
        # Proposition de CRÉATION (viewer) : project_id + emplacement proposé.
        _require(inp.project_id is not None, "missing_project", "`project_id` ou `doc_id` requis.")
        _require(inp.title and inp.title.strip(), "missing_title", "`title` requis.")
        _require(_can(sub, inp.project_id, "read"), "forbidden", "Accès refusé.", 403)
        req = db.add_doc_change_request(
            sub, project_id=int(inp.project_id), proposed_parent_id=inp.parent_id,
            proposed_kind=(inp.kind or "doc"),
            proposed_title=inp.title.strip(), proposed_body_md=inp.body_md or "",
            message=inp.message)
        _notify_cr_created(int(inp.project_id), sub, is_create=True, doc_title=None)
        return {"ok": True, "request": req}

    if inp.op == "list":
        _require(inp.project_id is not None, "missing_project", "`project_id` requis.")
        _require(_can(sub, inp.project_id, "read"), "forbidden", "Accès refusé.", 403)
        # Une liste sert à choisir quoi ouvrir : elle rend l'INDEX de l'arbre, pas les
        # corps (37 pages = 201 K caractères, refusés par le client). `body_length`
        # remplace `body_md` ; `fields=["*"]` rend le brut. (`fields` est validé en tête
        # de `_doc`, pour toutes les ops d'un coup — voir `_FIELDS_OPS`.)
        rows, notice = output_projection.summarize(
            [_view(d, sub) for d in db.list_docs_for_project(int(inp.project_id))],
            body_fields=("body_md",), fields=inp.fields, always=_ALWAYS)
        return {"project_id": inp.project_id, "docs": rows,
                **({"projection": notice} if notice else {})}

    if inp.op == "search":
        # DÉPRÉCIÉ (lot 3 Ship 1) : rerouté sur le chemin UNIQUE de recherche
        # (`oto_search` scope=project kinds=page) — un seul verbe, un seul code.
        # Forme de sortie conservée-approchée (`results`), + le pointeur.
        _require(inp.project_id is not None, "missing_project", "`project_id` requis.")
        _require(inp.query and inp.query.strip(), "missing_query", "`query` requis.")
        _require(_can(sub, inp.project_id, "read"), "forbidden", "Accès refusé.", 403)
        from .. import search as search_mod
        out = search_mod.search(sub, ctx.org_id, inp.query.strip(),
                                scope="project", project_id=int(inp.project_id),
                                kinds=["page"])
        return {"project_id": inp.project_id, "query": inp.query.strip(),
                "deprecated": "utilise oto_search (scope=project) — même chemin, toutes sources",
                "results": [{"id": h["ref"], "project_id": h.get("project_id"),
                             "title": h["title"], "snippet": h.get("passage") or "",
                             "updated_at": h.get("updated_at")} for h in out["hits"]]}

    # ops par doc_id (résolvent le projet pour l'autz)
    _require(inp.doc_id is not None, "missing_doc", "`doc_id` requis.")
    row = db.get_doc_by_id(int(inp.doc_id))
    _require(row is not None, "unknown_doc", f"Doc #{inp.doc_id} inconnu.", 404)
    pid = row["project_id"]

    if inp.op == "get":
        _require(_can(sub, pid, "read"), "forbidden", "Accès refusé.", 403)
        # Une lecture nue rend la page entière ; `fields` est HONORÉ quand il est là
        # (#461/#525 : lire le seul `rev` avant un patch ne doit plus coûter la page).
        return _projected(row, sub, inp.fields, brut_par_defaut=True)

    if inp.op == "revisions":
        _require(_can(sub, pid, "read"), "forbidden", "Accès refusé.", 403)
        return {"doc_id": inp.doc_id,
                "revisions": db.list_doc_revisions(int(inp.doc_id))}

    if inp.op == "backlinks":
        # « Cité par » (Ship 4) : les pages qui mentionnent celle-ci via [[…]],
        # FILTRÉES par accès (une page d'un projet non lisible ne fuite pas).
        _require(_can(sub, pid, "read"), "forbidden", "Accès refusé.", 403)
        seen: dict[int, bool] = {}
        def _readable(prj: int) -> bool:
            if prj not in seen:
                seen[prj] = _can(sub, prj, "read")
            return seen[prj]
        cites = [b for b in db.doc_backlinks(int(inp.doc_id)) if _readable(b["project_id"])]
        out = {"doc_id": inp.doc_id, "backlinks": cites, "count": len(cites)}
        if not cites:
            # Un zéro muet se lit comme « la fonction ne marche pas » : personne ne
            # peut deviner que SEUL `[[Titre]]` compte (signal #244 — trois formats
            # de lien essayés, tous inertes, aucun indice nulle part).
            out["hint"] = (
                f"Personne ne cite encore cette page. Un backlink naît d'un lien wiki "
                f"`[[{row.get('title') or 'Titre exact'}]]` écrit dans le corps d'une "
                "autre page (résolu à l'écriture, insensible à la casse) — la prose, "
                "`[texte](doc:ID)` et `[texte](/docs/ID)` n'en créent aucun.")
        return out

    if inp.op == "set_public":
        # Partager publiquement (ou retirer) — action d'écriture (gap #4a).
        _require(_can(sub, pid, "write"), "forbidden", "Écriture refusée.", 403)
        token = db.set_doc_public(int(inp.doc_id), bool(inp.public))
        db.log_project_activity(pid, sub, "doc.set_public",
                                f"{row.get('title')}:{bool(inp.public)}")
        return {"ok": True, "id": inp.doc_id, "public": bool(token),
                "public_url": _public_doc_url(token, sub) if token else None}

    if inp.op == "request_change":
        # MODIF (doc_id) — lecture seule → propose ; ≥ accès LECTURE au projet.
        _require(_can(sub, pid, "read"), "forbidden", "Accès refusé.", 403)
        body = inp.body_md if inp.body_md is not None else row.get("body_md", "")
        req = db.add_doc_change_request(
            sub, doc_id=int(inp.doc_id),
            proposed_title=(inp.title.strip() if inp.title else None),
            proposed_body_md=body, message=inp.message)
        db.log_project_activity(pid, sub, "doc.change_request", row.get("title"))
        _notify_cr_created(int(pid), sub, is_create=False, doc_title=row.get("title"))
        return {"ok": True, "request": req}

    if inp.op == "list_changes":
        # Par doc (legacy — la voie par projet est gérée avant le gate).
        _require(_can(sub, pid, "write"), "forbidden", "Écriture refusée.", 403)
        return {"doc_id": inp.doc_id,
                "requests": db.list_doc_change_requests(int(inp.doc_id))}

    if inp.op == "update":
        _require(_can(sub, pid, "write"), "forbidden", "Écriture refusée.", 403)
        try:
            db.update_doc(int(inp.doc_id), title=(inp.title.strip() if inp.title else None),
                          body_md=inp.body_md, kind=inp.kind, edited_by=sub,
                          description=inp.description, expected_rev=inp.expected_rev)
        except db.DocConflict as e:
            # Écrasement concurrent évité : le doc a changé depuis la lecture du client.
            _require(False, "conflict",
                     f"Le doc a été modifié entre-temps (rev actuelle {e.current_rev}). "
                     f"Relis-le (op=get) et refais ton édition sur la version à jour.", 409)
        db.log_project_activity(pid, sub, "doc.update", row.get("title"))
        return _projected(db.get_doc_by_id(int(inp.doc_id)), sub, inp.fields,
                          brut_par_defaut=False, hint=_HINT_ACCUSE)

    if inp.op == "patch":
        # Édition PARTIELLE (top5 #3) : ne touche QUE la région visée → deux auteurs sur
        # des régions différentes ne s'écrasent plus. On applique le patch puis on réécrit
        # via update_doc (révisions + backlinks + conflit optimiste conservés) : tout
        # nouveau chemin d'écriture passe par LÀ, jamais par un UPDATE de son cru.
        _require(_can(sub, pid, "write"), "forbidden", "Écriture refusée.", 403)
        corps = row.get("body_md") or ""
        mode = inp.mode or "replace"
        vise_section = bool(inp.section and inp.section.strip())

        # ── DEUX AXES D'ADRESSAGE, jamais un mot réservé (signaux #481, #492, #507) ──
        # `section` = l'espace de noms des TITRES markdown ; `region` = celui des régions
        # SANS titre (aujourd'hui le seul préambule : ce qui précède le premier titre —
        # bandeau de provenance, front-matter). Les signaux proposaient de faire tenir la
        # seconde dans la première (`section="__preamble__"`) : refusé. Rien n'empêche une
        # page d'écrire `## __preamble__` en titre, et ce jour-là la même chaîne
        # désignerait deux choses. Sur un axe séparé, la collision est impossible par
        # CONSTRUCTION — pas par improbabilité — et les deux restent adressables.
        # Les deux ensemble, ou aucun des deux : on refuse, on ne devine pas laquelle prime.
        _require(not (vise_section and inp.region), "ambiguous_target",
                 "`section` et `region` désignent deux cibles différentes : n'en passe "
                 "qu'une. `section` = un titre markdown (la section court jusqu'au "
                 f"prochain titre de niveau ≤) ; {_POIGNEE_PREAMBULE} = ce qui précède le "
                 "premier titre.")
        _require(vise_section or inp.region, "missing_target",
                 "Cible requise : `section` (le titre markdown de la section à modifier) "
                 f"ou {_POIGNEE_PREAMBULE} (ce qui précède le premier titre — bandeau de "
                 "provenance, front-matter, tout ce qui n'appartient à aucune section).")

        # `mode=delete` retire la cible : il ne prend pas de contenu, et les trois autres
        # en exigent un. Refusé et NOMMÉ des deux côtés — un argument accepté-et-ignoré
        # coûte exactement ce qu'il prétendait économiser, et rien ne le signale à
        # l'appelant (leçon générale du signal #461).
        if mode == "delete":
            _require(inp.body_md is None, "unexpected_body",
                     "`mode=delete` retire la cible : il ne prend pas de `body_md`. Pour "
                     "VIDER une section en gardant son titre, c'est `mode=replace` avec "
                     "`body_md` vide.")
        else:
            _require(inp.body_md is not None, "missing_body",
                     "`body_md` (nouveau contenu) requis.")

        # Une section court jusqu'au prochain titre de niveau ≤ : un `replace` — ou un
        # `delete` — sur un `###` emporte donc ses `####`. C'est la sémantique voulue (on
        # remplace / retire une section ENTIÈRE), mais silencieuse elle transforme le
        # patch — vendu comme le mode sûr — en écrasement du travail d'un autre auteur
        # (signal #334). On ne refuse pas : on ANNONCE ce qui part, la révision précédente
        # permettant de revenir en arrière (op=revisions).
        removed = (doc_patch.subsections(corps, inp.section)
                   if vise_section and mode in ("replace", "delete") else [])
        try:
            if vise_section:
                new_body = doc_patch.patch_section(corps, inp.section, inp.body_md,
                                                   mode=mode)
            else:
                new_body = doc_patch.patch_preamble(corps, inp.body_md, mode=mode)
        except doc_patch.SectionNotFound as e:
            # Le refus nomme les sections disponibles ET, si la page a un préambule,
            # la poignée qui l'atteint : c'est ici que `section="__preamble__"` se heurte
            # à un mur qui apprend, plutôt qu'à un synonyme deviné.
            _require(False, "unknown_section",
                     _refus_section_introuvable(inp.section, e.available, corps), 404)
        except doc_patch.HeadingInPreamble as e:
            _require(False, "heading_in_preamble",
                     f"Le préambule est ce qui PRÉCÈDE le premier titre : y écrire un "
                     f"titre ({', '.join(e.found)}) le refermerait, et le patch suivant "
                     f"n'atteindrait plus ce bandeau. Retire le titre du `body_md`, ou "
                     f"vise cette section par `section`.")
        except doc_patch.PreambleAbsent:
            _require(False, "empty_preamble",
                     "Cette page n'a pas de préambule : son premier titre ouvre le corps, "
                     "il n'y a rien à supprimer au-dessus.", 404)
        except doc_patch.PreambleIsWholePage:
            _require(False, "preamble_is_whole_page",
                     "Cette page n'a aucun titre : « ce qui précède le premier titre » y "
                     "est la page ENTIÈRE, et la supprimer la viderait. Si c'est ce que "
                     "tu veux : op=update avec le nouveau corps, ou op=delete pour la page.")
        try:
            db.update_doc(int(inp.doc_id), body_md=new_body, edited_by=sub,
                          expected_rev=inp.expected_rev)
        except db.DocConflict as e:
            _require(False, "conflict",
                     f"Le doc a été modifié entre-temps (rev actuelle {e.current_rev}). "
                     f"Relis-le (op=get) et refais ton patch sur la version à jour.", 409)
        cible = inp.section if vise_section else "(préambule)"
        db.log_project_activity(pid, sub, "doc.patch", f"{row.get('title')} § {cible}")
        # #530 : c'est le patch qui souffrait le plus — il existe précisément pour éditer
        # une page trop longue pour être lue entière, et il en rendait le corps complet.
        out = _projected(db.get_doc_by_id(int(inp.doc_id)), sub, inp.fields,
                         brut_par_defaut=False, hint=_HINT_ACCUSE)
        if removed:
            out["removed_subsections"] = removed
            verbe = ("retiré la section ENTIÈRE, titre compris" if mode == "delete"
                     else "remplacé la section ENTIÈRE")
            issue = ("vise directement la sous-section" if mode == "delete"
                     else "vise directement la sous-section, ou mode=append")
            out["warning"] = (
                f"`mode={mode}` sur « {inp.section} » a {verbe}, donc aussi "
                f"{len(removed)} sous-section(s) : {', '.join(removed)}. "
                f"Si tu ne voulais pas les perdre : op=revisions puis republie leur "
                f"contenu (ou {issue}).")
        return out

    if inp.op == "delete":
        _require(_can(sub, pid, "write"), "forbidden", "Écriture refusée.", 403)
        db.delete_doc(int(inp.doc_id))   # CASCADE sur le sous-arbre
        db.log_project_activity(pid, sub, "doc.delete", row.get("title"))
        return {"ok": True, "id": inp.doc_id, "deleted": True}

    # move — nouveau parent dans le MÊME projet (cycle profond non gardé en v1) ET/OU
    # réordonnancement (Ship 2 : `position` = index cible, la fratrie est réindexée).
    _require(_can(sub, pid, "write"), "forbidden", "Écriture refusée.", 403)

    # A4 (#6) : déplacement CROSS-PROJET — la page + son sous-arbre changent de projet.
    # Écriture requise sur la SOURCE (ci-dessus) ET la CIBLE ; le parent proposé (si
    # fourni) doit appartenir au projet cible.
    if inp.to_project is not None and int(inp.to_project) != pid:
        tgt = int(inp.to_project)
        _require(db.get_project_by_id(tgt) is not None, "unknown_project",
                 f"Projet cible #{tgt} inconnu.", 404)
        _require(_can(sub, tgt, "write"), "forbidden",
                 "Écriture refusée sur le projet cible.", 403)
        if inp.parent_id is not None:
            _require(int(inp.parent_id) != int(inp.doc_id), "bad_parent",
                     "Un doc ne peut pas être son propre parent.")
            parent = db.get_doc_by_id(int(inp.parent_id))
            _require(parent and parent["project_id"] == tgt, "bad_parent",
                     "Parent invalide (doit être une page du projet cible).")
        n = db.move_doc_to_project(int(inp.doc_id), tgt,
                                   inp.parent_id if "parent_id" in inp.model_fields_set else None,
                                   position=inp.position)
        db.log_project_activity(pid, sub, "doc.move_out", f"{row.get('title')} → projet {tgt}")
        db.log_project_activity(tgt, sub, "doc.move_in", row.get("title"))
        out = _projected(db.get_doc_by_id(int(inp.doc_id)), sub, inp.fields,
                         brut_par_defaut=False, hint=_HINT_ACCUSE)
        out["moved_count"] = n
        return out

    if inp.parent_id is not None:
        _require(int(inp.parent_id) != int(inp.doc_id), "bad_parent",
                 "Un doc ne peut pas être son propre parent.")
        parent = db.get_doc_by_id(int(inp.parent_id))
        _require(parent and parent["project_id"] == pid, "bad_parent",
                 "Parent invalide (autre projet ou inexistant).")
    # Trois intentions distinguées par `model_fields_set` (JSON null ≠ absent) :
    # parent FOURNI (id ou null=racine) = reparenter là ; absent + `position` posé =
    # réordonner DANS la fratrie courante ; absent + rien = racine (historique).
    if "parent_id" in inp.model_fields_set:
        target_parent = inp.parent_id
    elif inp.position is not None:
        target_parent = row.get("parent_id")
    else:
        target_parent = None
    db.move_doc(int(inp.doc_id), target_parent, position=inp.position)
    # Un déplacement ne touche à aucun contenu : rejouer le corps est du pur poids.
    return _projected(db.get_doc_by_id(int(inp.doc_id)), sub, inp.fields,
                      brut_par_defaut=False, hint=_HINT_ACCUSE)


CAPABILITIES += [
    Capability(
        key="me.doc", handler=_doc, Input=DocInput, authz=PROJECT_SHARED_READ,
        description=(
            "Docs (markdown pages tree inside a project; inherit the project's access). "
            "**This is also the org KNOWLEDGE BASE**: resolve it with oto_kb → project_id, "
            "then read/search/write reference pages here (the dashboard « Documents » zone). "
            "Prefer it over the web for org facts (processes, context, conventions), and "
            "CAPTURE durable, sourced facts here (kind=source/note) as you learn them. "
            "op=create (project_id, title; optional parent_id/body_md/kind) / bulk_create "
            "(project_id + `pages`=[{title, body_md?, kind?, parent_index?}] → N pages in ONE "
            "call, build a tree via parent_index = an earlier page in the batch) / list "
            "(project_id → the page INDEX, build the tree via parent_id: titles and "
            "`body_md_length`, NOT the bodies — pick a page here, then op=get it. "
            '`fields=["*"]` returns whole pages, `fields=[…]` picks columns) / search (project_id + '
            "query → full-text hits {id,title,kind,snippet}: LOCATE a page, then get its "
            "content) / get (the whole page, incl. `rev`, an ETag; pass `fields=[…]` to read "
            'ONLY those columns — `fields=["id","rev"]` gets the rev for an optimistic patch '
            "without paying for the body) / update (title/body_md/kind, full body; "
            "snapshots the prior version; pass `expected_rev` from op=get for optimistic "
            "conflict detection → 409 if the page changed since) / patch (edit ONE region in "
            "place, WITHOUT re-emitting the page — this is how you edit a page too long to "
            "re-send: `mode` replace|append|prepend|delete, and ONE target, either "
            "`section`=its markdown heading + `body_md` = that section's BODY, WITHOUT "
            "repeating the heading (the server keeps it), OR "
            '`region="preamble"` = everything ABOVE the first heading (provenance banner, '
            '"Last verified" line, front-matter) — it belongs to no section, so no '
            "`section` value can ever reach it; that is a SEPARATE axis, never a reserved "
            'heading name like "__preamble__" (a page may legitimately have such a '
            "heading, and it stays reachable via `section`). Passing both, or neither, is "
            "refused. `mode=delete` removes the target INCLUDING its heading (pass no "
            "`body_md`) — the only way to drop a heading without rewriting the page; to "
            "merely empty a section and keep its heading, use mode=replace with an empty "
            "`body_md`. Two authors on different regions don't clobber; every mode honours "
            "`expected_rev` and snapshots a revision. "
            "SCOPE: a section runs to the next heading of EQUAL-OR-HIGHER level, so its "
            "NESTED sub-sections are part of it — replacing OR deleting a `###` also takes "
            "its `####` children (the response then lists `removed_subsections`). To keep "
            "them, target the sub-heading itself or use mode=append) / "
            "A SUCCESSFUL WRITE (create/update/patch/move) returns a RECEIPT, not the page: "
            "id, title, `url`, `rev`, `updated_at` and `body_md_length` — you just wrote the body, "
            'so it is not replayed back at you. Add `fields=["*"]` if you really want the '
            "stored page back, or `fields=[…]` to pick columns. / "
            "A page's `description` is a chapô you STORE: leave it out and the index "
            "DERIVES one from the first prose line of the body (marked `description_derived`), "
            "so it moves with every body edit — that is not an overwrite. Pass `description` "
            "explicitly to pin one that stops following the body. / "
            "EVERY page carries `url` — the web address to READ it, in the reader's own "
            "product. That is the answer to \"where is it?\": hand it over as-is, never "
            "rebuild an address from a pattern. `null` means that reader's product has no "
            "such view — then say where it lives (project + title) rather than invent a link. / "
            "revisions (doc_id → version history, newest first) / backlinks (doc_id → the "
            "pages that CITE this one). LINK PAGES with `[[Exact page title]]` in body_md — "
            "that wiki-link is the ONLY thing that creates a backlink (prose mentions, "
            "[text](doc:88) and [text](/docs/88) create none). Resolved AT WRITE TIME against "
            "the current project then the org KB, case- and edge-space-insensitive; a title "
            "that doesn't exist yet is kept as a stub and links itself once the page is "
            "created or renamed / request_change (read-only "
            "users propose a new body_md/title + message) / list_changes (owner: pending "
            "requests) / resolve_change (request_id + accept: true applies it, false rejects) "
            "/ set_public (public: true → shareable public read-only link, false → private ; "
            "returns public_url) / delete (cascades its subtree) / move (reparent/reorder "
            "in-project via parent_id [null=top-level] + position; OR cross-project via "
            "`to_project`=target project id → moves the page AND its subtree there, "
            "write required on both). kind ∈ doc|note|source. EMBED A LIVE DATASTORE in a "
            "page body with a fenced block ```oto-data<newline><namespace-name-or-id><newline>``` "
            "→ the viewer renders that datastore's table LIVE (always up to date). Prefer this "
            "over a hand-typed summary table when the data lives in a datastore (single source "
            "of truth, no drift)."
        ),
        mcp="oto_doc",
        rest=RestBinding("POST", "/api/me/docs"),
    ),
]
