"""Les PROPOSITIONS (Ship 3) : « les lecteurs proposent, les auteurs valident ».

Un compte qui a la LECTURE d'un projet mais pas l'écriture ne se heurte pas à un refus :
son geste devient une demande, qu'un validateur tranche. D'où deux formes de demande —
créer une page, modifier une page — et deux voies de lecture, par projet et par page.

⚠️ Ces branches se résolvent **AVANT le gate `doc_id`** du dispatcher : une proposition
de CRÉATION porte `doc_id=NULL`, elle serait inatteignable sinon. Ce n'est pas un détail
de rangement, c'est l'ordre du dispatcher qui le garantit (`core._doc`).
"""
from __future__ import annotations

from typing import Optional

from ... import db
from . import common, notify
from .common import require


def resolve(sub: Optional[str], inp) -> dict:
    require(inp.request_id is not None, "missing_request", "`request_id` requis.")
    cr = db.get_doc_change_request(int(inp.request_id))
    require(cr is not None, "unknown_request", "Demande inconnue.", 404)
    require(cr["status"] == "pending", "already_resolved", "Demande déjà traitée.")
    cr_pid = cr.get("project_id") or (
        (db.get_doc_by_id(int(cr["doc_id"])) or {}).get("project_id") if cr.get("doc_id") else None)
    require(cr_pid is not None, "unknown_request", "Cible de la demande introuvable.", 404)
    require(common.can(sub, cr_pid, "write"), "forbidden", "Écriture refusée.", 403)
    if inp.accept:
        if cr.get("doc_id"):
            # MODIF : la page cible existe-t-elle encore ? sinon on ferme (motif).
            if db.get_doc_by_id(int(cr["doc_id"])) is None:
                db.resolve_doc_change_request(int(inp.request_id), "rejected", sub)
                notify.cr_resolved(cr, False)
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
    notify.cr_resolved(cr, bool(inp.accept))
    return {"ok": True, "id": inp.request_id, "accepted": bool(inp.accept)}


def list_by_project(sub: Optional[str], inp) -> dict:
    # Toutes les propositions en attente d'un PROJET (drawer « Propositions (N) »).
    require(common.can(sub, inp.project_id, "write"), "forbidden", "Écriture refusée.", 403)
    return {"project_id": inp.project_id,
            "requests": db.list_change_requests_by_project([int(inp.project_id)])}


def propose_create(sub: Optional[str], inp) -> dict:
    # Proposition de CRÉATION (viewer) : project_id + emplacement proposé.
    require(inp.project_id is not None, "missing_project", "`project_id` ou `doc_id` requis.")
    require(inp.title and inp.title.strip(), "missing_title", "`title` requis.")
    require(common.can(sub, inp.project_id, "read"), "forbidden", "Accès refusé.", 403)
    req = db.add_doc_change_request(
        sub, project_id=int(inp.project_id), proposed_parent_id=inp.parent_id,
        proposed_kind=(inp.kind or "doc"),
        proposed_title=inp.title.strip(), proposed_body_md=inp.body_md or "",
        message=inp.message)
    notify.cr_created(int(inp.project_id), sub, is_create=True, doc_title=None)
    return {"ok": True, "request": req}


def propose_update(sub: Optional[str], inp, row: dict, pid: int) -> dict:
    # MODIF (doc_id) — lecture seule → propose ; ≥ accès LECTURE au projet.
    require(common.can(sub, pid, "read"), "forbidden", "Accès refusé.", 403)
    body = inp.body_md if inp.body_md is not None else row.get("body_md", "")
    req = db.add_doc_change_request(
        sub, doc_id=int(inp.doc_id),
        proposed_title=(inp.title.strip() if inp.title else None),
        proposed_body_md=body, message=inp.message)
    db.log_project_activity(pid, sub, "doc.change_request", row.get("title"))
    notify.cr_created(int(pid), sub, is_create=False, doc_title=row.get("title"))
    return {"ok": True, "request": req}


def list_by_doc(sub: Optional[str], inp, pid: int) -> dict:
    # Par doc (legacy — la voie par projet est gérée avant le gate).
    require(common.can(sub, pid, "write"), "forbidden", "Écriture refusée.", 403)
    return {"doc_id": inp.doc_id,
            "requests": db.list_doc_change_requests(int(inp.doc_id))}
