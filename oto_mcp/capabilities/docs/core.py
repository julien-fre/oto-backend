"""Le dispatcher `me.doc` et son descripteur — la seule surface du domaine « doc ».

Un Doc appartient à un projet et **hérite de son accès** (`ownership.can_access` sur le
projet — pas d'ownership propre). Le `brief_md` du projet reste la page d'entrée ; les
Docs sont les pages, en arbre via `parent_id`. kind ∈ {doc (humain), note (agent),
source (import)}. CRUD + move, co-déclaré MCP+REST.

⚠️ **L'ORDRE des branches de `_doc` est un contrat, pas une mise en page.** Trois d'entre
elles se résolvent AVANT le gate `doc_id` — les propositions (une proposition de création
porte `doc_id=NULL`) et les deux lectures par projet. Les remonter ou les descendre change
ce qui est atteignable ; c'est la raison pour laquelle le dispatcher reste ici, entier et
lisible d'un coup, pendant que les corps vivent dans les modules du domaine.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

from ... import db
from .._authz import PROJECT_SHARED_READ
from .._types import Capability, ResolvedCtx, RestBinding
from ..registry import CAPABILITIES
from . import changes, common, history, patch, reads, view, writes
from .common import require


class DocInput(BaseModel):
    op: Literal["create", "bulk_create", "list", "search", "get", "update", "patch",
                "delete", "move", "revisions", "revert", "request_change", "list_changes",
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
    # revert (#657) : la version à restaurer, adressée par le `id` que rend op=revisions.
    # PAS un numéro d'ordre : `doc_revisions` n'en porte aucun, et un rang calculé sur
    # une liste plafonnée (`limit`) désignerait une autre version au prochain appel.
    revision_id: Optional[int] = None
    # delete (#657) : True = ne supprime RIEN, rend seulement ce que la suppression
    # emporterait (de quoi annoncer « ceci supprimera N pages » avant de la faire).
    dry_run: Optional[bool] = None
    # Projection de SORTIE, honorée par list/get/create/update/patch/move (`view.FIELDS_OPS`)
    # et REFUSÉE ailleurs. Omis : la liste rend son index, `get` la page entière, une
    # écriture son accusé. `["*"]` = la page entière partout.
    fields: Optional[list[str]] = Field(default=None, description=(
        "Output projection on list/get/create/update/patch/move (refused elsewhere). "
        "Omitted, a list returns its index and a write its receipt; `[\"*\"]` returns "
        "the whole page everywhere; a list of names picks columns."))


def _doc(ctx: ResolvedCtx, inp: DocInput) -> dict:
    sub = ctx.sub
    if sub is None:
        # Endpoint publié sans login : l'autz a déjà validé le contexte, on borne ici
        # les VERBES (lecture seule). `common.can(None, …)` borne le PÉRIMÈTRE au projet.
        require(inp.op in common.SHARED_READ_OPS, "forbidden",
                "Lecture seule sur un projet partagé.", 403)

    # `fields` se valide UNE fois, pour toutes les ops — et APRÈS l'autz, pour qu'un
    # appelant anonyme se heurte au 403 plutôt qu'à un 400 qui lui décrirait la surface.
    if inp.fields is not None:
        require(inp.op in view.FIELDS_OPS, "unsupported_fields",
                f"`fields` ne s'applique qu'aux ops {', '.join(sorted(view.FIELDS_OPS))} — "
                f"op={inp.op} rend une forme fixe. Retire-le.")
        require(bool(inp.fields), "empty_fields",
                "`fields` est une liste vide : omets-le pour la vue par défaut, passe "
                '`["*"]` pour la page entière, ou nomme les colonnes voulues.')

    # `revision_id` et `dry_run` n'ont de sens que sur UNE op chacun. Un argument
    # accepté-et-ignoré coûte exactement ce qu'il prétendait économiser, et rien ne le
    # signale à l'appelant (leçon générale du signal #461) — ici le prix serait pire
    # qu'un surcoût : `dry_run=true` avalé par une op qui supprime pour de bon détruit
    # précisément ce que l'appelant croyait seulement simuler.
    if inp.revision_id is not None:
        require(inp.op == "revert", "unsupported_revision_id",
                "`revision_id` ne s'applique qu'à op=revert (la version à restaurer). "
                "Pour LIRE l'historique, c'est op=revisions, qui ne prend que `doc_id`.")
    if inp.dry_run is not None:
        require(inp.op == "delete", "unsupported_dry_run",
                "`dry_run` ne s'applique qu'à op=delete — les autres ops n'ont pas de "
                "mode simulation et exécuteraient pour de bon. Retire-le.")

    if inp.op == "create":
        return writes.create(sub, inp)

    if inp.op == "bulk_create":
        return writes.bulk_create(sub, inp)

    # ── Propositions (Ship 3) — AVANT le gate doc_id : une proposition de CRÉATION a
    # doc_id=NULL, elle serait inatteignable sinon. On résout le projet par request_id
    # (resolve) / project_id (create-proposal, list) / doc_id (modif, legacy).
    if inp.op == "resolve_change":
        return changes.resolve(sub, inp)

    if inp.op == "list_changes" and inp.project_id is not None:
        return changes.list_by_project(sub, inp)

    if inp.op == "request_change" and inp.doc_id is None:
        return changes.propose_create(sub, inp)

    if inp.op == "list":
        return reads.liste(sub, inp)

    if inp.op == "search":
        return reads.search(ctx, inp)

    # ops par doc_id (résolvent le projet pour l'autz)
    require(inp.doc_id is not None, "missing_doc", "`doc_id` requis.")
    row = db.get_doc_by_id(int(inp.doc_id))
    require(row is not None, "unknown_doc", f"Doc #{inp.doc_id} inconnu.", 404)
    pid = row["project_id"]

    if inp.op == "get":
        return reads.get(sub, inp, row, pid)

    if inp.op == "revisions":
        return history.revisions(sub, inp, pid)

    if inp.op == "revert":
        return history.revert(sub, inp, row, pid)

    if inp.op == "backlinks":
        return reads.backlinks(sub, inp, row, pid)

    if inp.op == "set_public":
        return writes.set_public(sub, inp, row, pid)

    if inp.op == "request_change":
        return changes.propose_update(sub, inp, row, pid)

    if inp.op == "list_changes":
        return changes.list_by_doc(sub, inp, pid)

    if inp.op == "update":
        return writes.update(sub, inp, row, pid)

    if inp.op == "patch":
        return patch.patch(sub, inp, row, pid)

    if inp.op == "delete":
        return writes.delete(sub, inp, row, pid)

    return writes.move(sub, inp, row, pid)


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
            "revisions (doc_id → version history, newest first; each row's `id` is what "
            "op=revert takes) / revert (doc_id + `revision_id` from op=revisions → puts "
            "that past title+body back). A revert moves FORWARD: the current state is "
            "snapshotted first, so nothing is lost and a revert can itself be reverted; "
            "the response echoes `reverted_from`. It honours `expected_rev` too — pass it "
            "or you may silently overwrite a peer's edit. It restores a VERSION of a page "
            "that still exists; it does NOT undo a delete (a deleted page took its "
            "revisions with it) / backlinks (doc_id → the "
            "pages that CITE this one). LINK PAGES with `[[Exact page title]]` in body_md — "
            "that wiki-link is the ONLY thing that creates a backlink (prose mentions, "
            "[text](doc:88) and [text](/docs/88) create none). Resolved AT WRITE TIME against "
            "the current project then the org KB, case- and edge-space-insensitive; a title "
            "that doesn't exist yet is kept as a stub and links itself once the page is "
            "created or renamed. ⚠️ That is the reach of RESOLUTION, not of the graph, "
            "and they differ BOTH ways. (a) The graph is not symmetric: a page in the "
            "org KB resolves against the KB alone, so it can NEVER link to a page living "
            "in a project — while that project page links back to it fine (the KB is "
            "itself a project, so an ordinary backlink is already cross-project). A page "
            "can therefore be cited from the org's top map and still read as an orphan "
            "here: do not use backlinks as a completeness or orphan check without "
            "knowing that. (b) op=backlinks shows every STORED link whatever its project, "
            "including one left behind by a page MOVED between projects — no resolution "
            "would make it today, and it disappears, silently, the next time the citing "
            "page is written. So a cross-project backlink is not proof that the same "
            "`[[…]]`, written now, would resolve. (c) The list is filtered by YOUR "
            "access: citations living in projects you cannot read are removed. When "
            "that happens the response says `hidden_by_access: true` — « nobody cites "
            "this page » and « three pages cite it, you cannot see them » call for "
            "opposite moves, so the second is never reported as the first. The COUNT "
            "of hidden ones is deliberately not given: it would tell you how many "
            "pages exist in projects that are closed to you. Every write says which of its `[[…]]` "
            "found nothing, under `citations_sans_cible` / request_change (read-only "
            "users propose a new body_md/title + message) / list_changes (owner: pending "
            "requests) / resolve_change (request_id + accept: true applies it, false rejects) "
            "/ set_public (public: true → shareable public read-only link to THIS PAGE "
            "ALONE: the reader gets its title and body, and nothing else — not the "
            "project, not the sibling pages, not this page's own sub-pages, which each "
            "need their own link ; false → private ; returns public_url) "
            "/ delete (removes the page AND its whole subtree, "
            "revisions included — irreversible, there is no trash and no undelete. The "
            "response says how many pages went with it (`descendants`); ask FIRST with "
            "`dry_run: true`, which deletes nothing and returns the same count, whenever "
            "a human has to confirm) / move (reparent/reorder "
            "in-project via parent_id [null=top-level] + position; OR cross-project via "
            "`to_project`=target project id → moves the page AND its subtree there, "
            "write required on both. ⚠️ A move is NOT free for links: the page's own "
            "`[[…]]` are re-resolved in the TARGET project (some become stubs), while "
            "the links pointing AT it are left stored though now out of reach — they "
            "still show in op=backlinks and die on the citing page's next write. After "
            "reorganising a tree, rewrite the citing pages and read their "
            "`citations_sans_cible`). kind ∈ doc|note|source. EMBED A LIVE DATASTORE in a "
            "page body with a fenced block ```oto-data<newline><namespace-name-or-id><newline>``` "
            "→ the viewer renders that datastore's table LIVE (always up to date). Prefer this "
            "over a hand-typed summary table when the data lives in a datastore (single source "
            "of truth, no drift)."
        ),
        mcp="oto_doc",
        rest=RestBinding("POST", "/api/me/docs"),
    ),
]
