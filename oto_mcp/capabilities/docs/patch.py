"""`op=patch` — éditer UNE région d'une page sans la réémettre.

La grammaire (deux axes d'adressage `section` / `region`, jamais un mot réservé), le
moteur et ses refus vivent dans `region`, partagés avec `oto_guide op=patch`. Ce qui
reste ici est propre à la PAGE : le droit d'écrire au projet, la simulation
(`dry_run`), l'écriture versionnée (`update_doc`) et l'accusé.
"""
from __future__ import annotations

from typing import Optional

from ... import db
from . import common, region, view
from .common import require


def patch(sub: Optional[str], inp, row: dict, pid: int) -> dict:
    # Édition PARTIELLE (top5 #3) : ne touche QUE la région visée → deux auteurs sur
    # des régions différentes ne s'écrasent plus. On applique le patch puis on réécrit
    # via update_doc (révisions + backlinks + conflit optimiste conservés) : tout
    # nouveau chemin d'écriture passe par LÀ, jamais par un UPDATE de son cru.
    require(common.can(sub, pid, "write"), "forbidden", "Write refused.", 403)
    corps = row.get("body_md") or ""
    mode = inp.mode or "replace"
    # Deux axes d'adressage (`section` / `region`), jamais un mot réservé, et un
    # contenu si et seulement si le mode en prend un : `region.valider`.
    vise_section = region.valider(inp.section, inp.region, mode, inp.body_md)

    # Une section court jusqu'au prochain titre de niveau ≤ : un `replace` — ou un
    # `delete` — sur un `###` emporte donc ses `####`. C'est la sémantique voulue, mais
    # silencieuse elle transforme le patch — vendu comme le mode sûr — en écrasement du
    # travail d'un autre auteur (signal #334). On ne refuse pas : on ANNONCE ce qui part,
    # la révision précédente permettant de revenir en arrière (op=revisions).
    fait = region.appliquer(corps, inp.section if vise_section else None, inp.body_md,
                            mode, region.PAGE)
    new_body, removed, emporte = fait.corps, fait.sous_sections, fait.emporte
    cible = inp.section if vise_section else "(preamble)"
    if inp.dry_run:
        # oto#171 : sans simulation, le seul moyen de savoir ce qu'un `replace` emporte
        # était de l'essayer sur une page jetable créée dans le projet du client.
        # `rev` = la version sur laquelle ce relevé vaut : la repasser en `expected_rev`
        # garantit que le vrai patch emporte exactement ce qui a été annoncé.
        return {"ok": True, "id": inp.doc_id, "dry_run": True, "written": False,
                "mode": mode, "target": cible, "removed": emporte,
                "rev": db.doc_rev(row.get("title"), corps),
                "hint": ("Nothing was written. `removed` = the lines of the CURRENT body "
                         "this patch would remove (null: this mode removes nothing). Pass "
                         "`rev` back as `expected_rev` so the real patch applies to this "
                         "same version.")}
    try:
        db.update_doc(int(inp.doc_id), body_md=new_body, edited_by=sub,
                      expected_rev=inp.expected_rev, face=common.face_de_l_appel(),
                      regroupable=True)
    except db.DocConflict as e:
        require(False, "conflict",
                f"The doc changed in the meantime (current rev {e.current_rev}). "
                f"Read it again (op=get) and redo your patch on the current version.", 409)
    db.log_project_activity(pid, sub, "doc.patch", f"{row.get('title')} § {cible}")
    # #530 : c'est le patch qui souffrait le plus — il existe précisément pour éditer
    # une page trop longue pour être lue entière, et il en rendait le corps complet.
    out = view.projected(db.get_doc_by_id(int(inp.doc_id)), sub, inp.fields,
                         brut_par_defaut=False, hint=view.HINT_ACCUSE)
    if emporte is not None:
        out["removed"] = emporte
    if removed:
        out["removed_subsections"] = removed
        verbe = ("removed the ENTIRE section, heading included" if mode == "delete"
                 else "replaced the ENTIRE section")
        issue = ("target the sub-section directly" if mode == "delete"
                 else "target the sub-section directly, or use mode=append")
        out["warning"] = (
            f"`mode={mode}` on “{inp.section}” {verbe}, hence also "
            f"{len(removed)} sub-section(s): {', '.join(removed)}. "
            f"If you did not mean to lose them: op=revisions, then republish their "
            f"content (or {issue}).")
    return out
