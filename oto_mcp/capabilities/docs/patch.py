"""`op=patch` — éditer UNE région d'une page sans la réémettre.

Le module existe pour une raison qui tient en une phrase : **deux axes d'adressage,
jamais un mot réservé**. `section` est l'espace de noms des titres markdown ; `region`
celui des régions SANS titre (aujourd'hui le seul préambule). Les faire tenir dans un
seul axe (`section="__preamble__"`, proposé par les signaux #481/#492/#507) rendrait
inadressable toute page portant réellement ce titre — le jour où ça arrive, rien ne le
signalerait.
"""
from __future__ import annotations

from typing import Optional

from ... import db, doc_patch
from . import common, view
from .common import require

# La poignée du préambule, écrite UNE seule fois : elle sert dans le refus d'ambiguïté,
# dans le refus « cible manquante » et dans le refus « section introuvable » qui la
# pointe. Trois formulations divergeraient à la première évolution.
POIGNEE_PREAMBULE = 'region="preamble"'


def refus_section_introuvable(heading: str, disponibles: list, corps: str) -> str:
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
                f"s'adresse par {POIGNEE_PREAMBULE}.")
    return msg


def patch(sub: Optional[str], inp, row: dict, pid: int) -> dict:
    # Édition PARTIELLE (top5 #3) : ne touche QUE la région visée → deux auteurs sur
    # des régions différentes ne s'écrasent plus. On applique le patch puis on réécrit
    # via update_doc (révisions + backlinks + conflit optimiste conservés) : tout
    # nouveau chemin d'écriture passe par LÀ, jamais par un UPDATE de son cru.
    require(common.can(sub, pid, "write"), "forbidden", "Écriture refusée.", 403)
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
    require(not (vise_section and inp.region), "ambiguous_target",
            "`section` et `region` désignent deux cibles différentes : n'en passe "
            "qu'une. `section` = un titre markdown (la section court jusqu'au "
            f"prochain titre de niveau ≤) ; {POIGNEE_PREAMBULE} = ce qui précède le "
            "premier titre.")
    require(vise_section or inp.region, "missing_target",
            "Cible requise : `section` (le titre markdown de la section à modifier) "
            f"ou {POIGNEE_PREAMBULE} (ce qui précède le premier titre — bandeau de "
            "provenance, front-matter, tout ce qui n'appartient à aucune section).")

    # `mode=delete` retire la cible : il ne prend pas de contenu, et les trois autres
    # en exigent un. Refusé et NOMMÉ des deux côtés — un argument accepté-et-ignoré
    # coûte exactement ce qu'il prétendait économiser, et rien ne le signale à
    # l'appelant (leçon générale du signal #461).
    if mode == "delete":
        require(inp.body_md is None, "unexpected_body",
                "`mode=delete` retire la cible : il ne prend pas de `body_md`. Pour "
                "VIDER une section en gardant son titre, c'est `mode=replace` avec "
                "`body_md` vide.")
    else:
        require(inp.body_md is not None, "missing_body",
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
        require(False, "unknown_section",
                refus_section_introuvable(inp.section, e.available, corps), 404)
    except doc_patch.HeadingInPreamble as e:
        require(False, "heading_in_preamble",
                f"Le préambule est ce qui PRÉCÈDE le premier titre : y écrire un "
                f"titre ({', '.join(e.found)}) le refermerait, et le patch suivant "
                f"n'atteindrait plus ce bandeau. Retire le titre du `body_md`, ou "
                f"vise cette section par `section`.")
    except doc_patch.PreambleAbsent:
        require(False, "empty_preamble",
                "Cette page n'a pas de préambule : son premier titre ouvre le corps, "
                "il n'y a rien à supprimer au-dessus.", 404)
    except doc_patch.PreambleIsWholePage:
        require(False, "preamble_is_whole_page",
                "Cette page n'a aucun titre : « ce qui précède le premier titre » y "
                "est la page ENTIÈRE, et la supprimer la viderait. Si c'est ce que "
                "tu veux : op=update avec le nouveau corps, ou op=delete pour la page.")
    try:
        db.update_doc(int(inp.doc_id), body_md=new_body, edited_by=sub,
                      expected_rev=inp.expected_rev)
    except db.DocConflict as e:
        require(False, "conflict",
                f"Le doc a été modifié entre-temps (rev actuelle {e.current_rev}). "
                f"Relis-le (op=get) et refais ton patch sur la version à jour.", 409)
    cible = inp.section if vise_section else "(préambule)"
    db.log_project_activity(pid, sub, "doc.patch", f"{row.get('title')} § {cible}")
    # #530 : c'est le patch qui souffrait le plus — il existe précisément pour éditer
    # une page trop longue pour être lue entière, et il en rendait le corps complet.
    out = view.projected(db.get_doc_by_id(int(inp.doc_id)), sub, inp.fields,
                         brut_par_defaut=False, hint=view.HINT_ACCUSE)
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
