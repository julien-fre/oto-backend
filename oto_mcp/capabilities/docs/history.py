"""Les VERSIONS d'une page : les lister (`revisions`), en restaurer une (`revert`).

⚠️ Ce n'est PAS « annuler une suppression » : une page effacée a emporté ses révisions
en cascade, il n'y a plus de ligne à restaurer. Deux gestes différents, et seul le
premier existe ici.
"""
from __future__ import annotations

from typing import Optional

from ... import db
from . import common, view
from .common import require


def revisions(sub: Optional[str], inp, pid: int) -> dict:
    require(common.can(sub, pid, "read"), "forbidden", "Accès refusé.", 403)
    return {"doc_id": inp.doc_id,
            "revisions": db.list_doc_revisions(int(inp.doc_id))}


def revert(sub: Optional[str], inp, row: dict, pid: int) -> dict:
    # Restaurer une version antérieure (#657). Le snapshot était pris depuis
    # toujours (`update_doc`), rien ne le REPOSAIT : le retour arrière se faisait à
    # la main — lire op=revisions, republier le corps par op=update — ce qu'un front
    # tiers ne peut pas offrir comme un geste.
    #
    # Régime : on restaure EN AVANT, jamais en rembobinant — même choix que
    # `org.instruction.revert`. L'état courant est snapshotté à son tour (c'est
    # `update_doc` qui le fait), donc un revert se re-revert et rien n'est perdu.
    require(common.can(sub, pid, "write"), "forbidden", "Écriture refusée.", 403)
    require(inp.revision_id is not None, "missing_revision",
            "`revision_id` requis : le champ `id` de la ligne voulue dans "
            "op=revisions (l'historique de CETTE page). Ce n'est pas un numéro de "
            "version — les pages n'en portent pas.")
    rev = db.get_doc_revision(int(inp.doc_id), int(inp.revision_id))
    require(rev is not None, "unknown_revision",
            f"Aucune version #{inp.revision_id} dans l'historique du doc "
            f"#{inp.doc_id} (une révision d'une AUTRE page ne compte pas). "
            f"Liste-les par op=revisions.", 404)
    try:
        # Passe par `update_doc` comme tout chemin d'écriture : snapshot de l'état
        # courant, backlinks re-résolus, renommage propagé, conflit optimiste — un
        # UPDATE de son cru perdrait les quatre.
        db.update_doc(int(inp.doc_id), title=rev["title"], body_md=rev["body_md"],
                      edited_by=sub, expected_rev=inp.expected_rev)
    except db.DocConflict as e:
        require(False, "conflict",
                f"Le doc a été modifié entre-temps (rev actuelle {e.current_rev}). "
                f"Relis-le (op=get) et refais ta restauration sur la version à "
                f"jour — sinon tu écrases l'édition d'un pair sans la voir.", 409)
    db.log_project_activity(pid, sub, "doc.revert",
                            f"{row.get('title')} ← révision {inp.revision_id}")
    out = view.projected(db.get_doc_by_id(int(inp.doc_id)), sub, inp.fields,
                         brut_par_defaut=False, hint=view.HINT_ACCUSE)
    # `version` est un état NEUF, pas celui qu'on restaure : `reverted_from` est la
    # seule trace de l'intention (même écho que `InstructionReverted`).
    out["reverted_from"] = int(inp.revision_id)
    return out
