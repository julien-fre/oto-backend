"""Ce qui ÉCRIT : la page elle-même et sa place dans l'arbre — `create`, `bulk_create`,
`update`, `move`, `delete` — plus `set_public`, qui écrit son exposition.

Toute écriture de corps passe par `db.update_doc` : c'est lui qui prend le snapshot de
révision, re-résout les backlinks, propage un renommage et détecte le conflit optimiste.
Un UPDATE de son cru perdrait les quatre — la règle vaut aussi pour `patch` et `revert`,
qui vivent à côté.
"""
from __future__ import annotations

from typing import Optional

from ... import db
from . import common, notify, view
from .common import require


def create(sub: Optional[str], inp) -> dict:
    require(inp.project_id is not None, "missing_project", "`project_id` requis.")
    require(inp.title and inp.title.strip(), "missing_title", "`title` requis.")
    # « Les lecteurs proposent » (Ship 3) : un viewer (lecture SANS écriture) qui
    # crée obtient une PROPOSITION de création, pas la page.
    if not common.can(sub, inp.project_id, "write"):
        require(common.can(sub, inp.project_id, "read"), "forbidden", "Accès refusé.", 403)
        req = db.add_doc_change_request(
            sub, project_id=int(inp.project_id), proposed_parent_id=inp.parent_id,
            proposed_kind=(inp.kind or "doc"),
            proposed_title=inp.title.strip(), proposed_body_md=inp.body_md or "",
            message=inp.message)
        notify.cr_created(int(inp.project_id), sub, is_create=True, doc_title=None)
        return {"status": "proposal_created", "request": req}
    if inp.parent_id is not None:
        parent = db.get_doc_by_id(int(inp.parent_id))
        require(parent and parent["project_id"] == inp.project_id, "bad_parent",
                "Parent invalide (autre projet ou inexistant).")
    did = db.create_doc(int(inp.project_id), inp.title.strip(), parent_id=inp.parent_id,
                        body_md=inp.body_md or "", kind=(inp.kind or "doc"), created_by=sub,
                        description=inp.description)
    db.log_project_activity(int(inp.project_id), sub, "doc.create", inp.title.strip())
    return view.projected(db.get_doc_by_id(did), sub, inp.fields,
                          brut_par_defaut=False, hint=view.HINT_ACCUSE)


def bulk_create(sub: Optional[str], inp) -> dict:
    # A4 (#6) : créer N pages en UN appel (33 pages ≠ 33 allers-retours). Arbre en un
    # coup via `parent_index` (index d'une page PLUS TÔT dans le lot) ; sinon `parent_id`.
    require(inp.project_id is not None, "missing_project", "`project_id` requis.")
    require(common.can(sub, inp.project_id, "write"), "forbidden", "Écriture refusée.", 403)
    require(bool(inp.pages), "missing_pages", "`pages` (liste non vide) requis.")
    if inp.parent_id is not None:
        par = db.get_doc_by_id(int(inp.parent_id))
        require(par and par["project_id"] == inp.project_id, "bad_parent",
                "`parent_id` invalide (autre projet ou inexistant).")
    created: list[int] = []
    for i, p in enumerate(inp.pages):
        title = str(p.get("title") or "").strip()
        require(bool(title), "missing_title", f"page #{i} sans `title`.")
        pi = p.get("parent_index")
        parent = created[pi] if isinstance(pi, int) and 0 <= pi < len(created) else inp.parent_id
        created.append(db.create_doc(
            int(inp.project_id), title, parent_id=parent, body_md=p.get("body_md") or "",
            kind=(p.get("kind") or "doc"), created_by=sub, description=p.get("description")))
    db.log_project_activity(int(inp.project_id), sub, "doc.bulk_create", f"{len(created)} pages")
    return {"created": created, "count": len(created)}


def set_public(sub: Optional[str], inp, row: dict, pid: int) -> dict:
    # Partager publiquement (ou retirer) — action d'écriture (gap #4a).
    require(common.can(sub, pid, "write"), "forbidden", "Écriture refusée.", 403)
    token = db.set_doc_public(int(inp.doc_id), bool(inp.public))
    db.log_project_activity(pid, sub, "doc.set_public",
                            f"{row.get('title')}:{bool(inp.public)}")
    return {"ok": True, "id": inp.doc_id, "public": bool(token),
            "public_url": view.public_doc_url(token, sub) if token else None}


def update(sub: Optional[str], inp, row: dict, pid: int) -> dict:
    require(common.can(sub, pid, "write"), "forbidden", "Écriture refusée.", 403)
    try:
        db.update_doc(int(inp.doc_id), title=(inp.title.strip() if inp.title else None),
                      body_md=inp.body_md, kind=inp.kind, edited_by=sub,
                      description=inp.description, expected_rev=inp.expected_rev)
    except db.DocConflict as e:
        # Écrasement concurrent évité : le doc a changé depuis la lecture du client.
        require(False, "conflict",
                f"Le doc a été modifié entre-temps (rev actuelle {e.current_rev}). "
                f"Relis-le (op=get) et refais ton édition sur la version à jour.", 409)
    db.log_project_activity(pid, sub, "doc.update", row.get("title"))
    return view.projected(db.get_doc_by_id(int(inp.doc_id)), sub, inp.fields,
                          brut_par_defaut=False, hint=view.HINT_ACCUSE)


def delete(sub: Optional[str], inp, row: dict, pid: int) -> dict:
    # La cascade sur le sous-arbre était correcte et MUETTE (#657) : la réponse ne
    # disait pas ce qu'elle emportait, donc aucun front ne pouvait annoncer « ceci
    # supprimera N pages » ni prévenir que c'était sans retour. On ne change pas ce
    # que le geste FAIT — on le DÉCLARE, et `dry_run` permet de le demander avant.
    require(common.can(sub, pid, "write"), "forbidden", "Écriture refusée.", 403)
    if inp.dry_run:
        n = db.count_doc_descendants(int(inp.doc_id))
        return {"ok": True, "id": inp.doc_id, "deleted": False, "dry_run": True,
                "descendants": n,
                "hint": (
                    f"Rien n'a été supprimé. Un op=delete sur « {row.get('title')} » "
                    f"emporterait cette page et {n} descendante(s), avec leur "
                    f"historique de versions. C'est SANS RETOUR : op=revert restaure "
                    f"une version d'une page qui existe encore, il ne ressuscite pas "
                    f"une page supprimée.")}
    n = db.delete_doc(int(inp.doc_id))
    db.log_project_activity(
        pid, sub, "doc.delete",
        f"{row.get('title')} (+{n} sous-page(s))" if n else row.get("title"))
    out = {"ok": True, "id": inp.doc_id, "deleted": True, "descendants": n}
    if n:
        # Même posture que `removed_subsections` sur op=patch : ce qui est parti EN
        # PLUS de la cible se dit, sinon la perte ne se découvre qu'à l'usage.
        out["warning"] = (
            f"Cette suppression a aussi emporté {n} sous-page(s) et leur historique "
            f"de versions — la cascade suit tout le sous-arbre. Sans retour : "
            f"op=revert ne restaure qu'une version d'une page existante. Passe "
            f"`dry_run: true` pour connaître le compte AVANT de supprimer.")
    return out


def move(sub: Optional[str], inp, row: dict, pid: int) -> dict:
    # move — nouveau parent dans le MÊME projet (cycle profond non gardé en v1) ET/OU
    # réordonnancement (Ship 2 : `position` = index cible, la fratrie est réindexée).
    require(common.can(sub, pid, "write"), "forbidden", "Écriture refusée.", 403)

    # A4 (#6) : déplacement CROSS-PROJET — la page + son sous-arbre changent de projet.
    # Écriture requise sur la SOURCE (ci-dessus) ET la CIBLE ; le parent proposé (si
    # fourni) doit appartenir au projet cible.
    if inp.to_project is not None and int(inp.to_project) != pid:
        tgt = int(inp.to_project)
        require(db.get_project_by_id(tgt) is not None, "unknown_project",
                f"Projet cible #{tgt} inconnu.", 404)
        require(common.can(sub, tgt, "write"), "forbidden",
                "Écriture refusée sur le projet cible.", 403)
        if inp.parent_id is not None:
            require(int(inp.parent_id) != int(inp.doc_id), "bad_parent",
                    "Un doc ne peut pas être son propre parent.")
            parent = db.get_doc_by_id(int(inp.parent_id))
            require(parent and parent["project_id"] == tgt, "bad_parent",
                    "Parent invalide (doit être une page du projet cible).")
        n = db.move_doc_to_project(int(inp.doc_id), tgt,
                                   inp.parent_id if "parent_id" in inp.model_fields_set else None,
                                   position=inp.position)
        db.log_project_activity(pid, sub, "doc.move_out", f"{row.get('title')} → projet {tgt}")
        db.log_project_activity(tgt, sub, "doc.move_in", row.get("title"))
        out = view.projected(db.get_doc_by_id(int(inp.doc_id)), sub, inp.fields,
                             brut_par_defaut=False, hint=view.HINT_ACCUSE)
        out["moved_count"] = n
        return out

    if inp.parent_id is not None:
        require(int(inp.parent_id) != int(inp.doc_id), "bad_parent",
                "Un doc ne peut pas être son propre parent.")
        parent = db.get_doc_by_id(int(inp.parent_id))
        require(parent and parent["project_id"] == pid, "bad_parent",
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
    return view.projected(db.get_doc_by_id(int(inp.doc_id)), sub, inp.fields,
                          brut_par_defaut=False, hint=view.HINT_ACCUSE)
