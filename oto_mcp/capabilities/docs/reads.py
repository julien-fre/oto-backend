"""Ce qui LIT une page ou l'arbre, sans rien écrire : `list`, `search`, `get`,
`backlinks`.

`list` et `search` résolvent leur projet par `project_id` ; `get` et `backlinks`
reçoivent la ligne déjà résolue par le gate `doc_id` du dispatcher.
"""
from __future__ import annotations

from typing import Optional

from ... import db, output_projection
from .._types import ResolvedCtx
from . import common, view
from .common import require


def liste(sub: Optional[str], inp) -> dict:
    require(inp.project_id is not None, "missing_project", "`project_id` requis.")
    require(common.can(sub, inp.project_id, "read"), "forbidden", "Accès refusé.", 403)
    # Une liste sert à choisir quoi ouvrir : elle rend l'INDEX de l'arbre, pas les
    # corps (37 pages = 201 K caractères, refusés par le client). `body_length`
    # remplace `body_md` ; `fields=["*"]` rend le brut. (`fields` est validé en tête
    # du dispatcher, pour toutes les ops d'un coup — voir `view.FIELDS_OPS`.)
    rows, notice = output_projection.summarize(
        [view.view(d, sub) for d in db.list_docs_for_project(int(inp.project_id))],
        body_fields=("body_md",), fields=inp.fields, always=view.ALWAYS)
    return {"project_id": inp.project_id, "docs": rows,
            **({"projection": notice} if notice else {})}


def search(ctx: ResolvedCtx, inp) -> dict:
    # DÉPRÉCIÉ (lot 3 Ship 1) : rerouté sur le chemin UNIQUE de recherche
    # (`oto_search` scope=project kinds=page) — un seul verbe, un seul code.
    # Forme de sortie conservée-approchée (`results`), + le pointeur.
    sub = ctx.sub
    require(inp.project_id is not None, "missing_project", "`project_id` requis.")
    require(inp.query and inp.query.strip(), "missing_query", "`query` requis.")
    require(common.can(sub, inp.project_id, "read"), "forbidden", "Accès refusé.", 403)
    from ... import search as search_mod
    out = search_mod.search(sub, ctx.org_id, inp.query.strip(),
                            scope="project", project_id=int(inp.project_id),
                            kinds=["page"])
    return {"project_id": inp.project_id, "query": inp.query.strip(),
            "deprecated": "utilise oto_search (scope=project) — même chemin, toutes sources",
            "results": [{"id": h["ref"], "project_id": h.get("project_id"),
                         "title": h["title"], "snippet": h.get("passage") or "",
                         "updated_at": h.get("updated_at")} for h in out["hits"]]}


def get(sub: Optional[str], inp, row: dict, pid: int) -> dict:
    require(common.can(sub, pid, "read"), "forbidden", "Accès refusé.", 403)
    # Une lecture nue rend la page entière ; `fields` est HONORÉ quand il est là
    # (#461/#525 : lire le seul `rev` avant un patch ne doit plus coûter la page).
    return view.projected(row, sub, inp.fields, brut_par_defaut=True)


def backlinks(sub: Optional[str], inp, row: dict, pid: int) -> dict:
    # « Cité par » (Ship 4) : les pages qui mentionnent celle-ci via [[…]],
    # FILTRÉES par accès (une page d'un projet non lisible ne fuite pas).
    require(common.can(sub, pid, "read"), "forbidden", "Accès refusé.", 403)
    seen: dict[int, bool] = {}

    def _readable(prj: int) -> bool:
        if prj not in seen:
            seen[prj] = common.can(sub, prj, "read")
        return seen[prj]

    tous = db.doc_backlinks(int(inp.doc_id))
    cites = [b for b in tous if _readable(b["project_id"])]
    out = {"doc_id": inp.doc_id, "backlinks": cites, "count": len(cites)}
    # oto#42, entrée 4 : le filtrage d'accès retirait des citations en silence, et le
    # hint affirmait alors « personne ne cite encore cette page » — une phrase FAUSSE
    # servie à un agent qui n'avait aucun moyen de le savoir. « Trois pages la citent,
    # tu n'y as pas accès » et « aucune page ne la cite » appellent deux gestes
    # opposés : demander un accès, ou écrire le lien qui manque.
    # ⚠️ Le NOMBRE n'est pas rendu : il révélerait combien de pages existent dans des
    # projets fermés à l'appelant. Le fait qu'il y en ait est déjà l'information utile
    # — le nombre demanderait un arbitrage de divulgation, pas une nuit de correctifs.
    masquees = len(tous) - len(cites)
    if masquees:
        out["hidden_by_access"] = True
        out["hidden_hint"] = (
            "Des pages citent celle-ci depuis des projets auxquels tu n'as pas accès : "
            "ce relevé est PARTIEL. Demande l'accès au projet concerné si tu as besoin "
            "du graphe complet.")
    if not cites and not masquees:
        # Un zéro muet se lit comme « la fonction ne marche pas » : personne ne
        # peut deviner que SEUL `[[Titre]]` compte (signal #244 — trois formats
        # de lien essayés, tous inertes, aucun indice nulle part).
        out["hint"] = (
            f"Personne ne cite encore cette page. Un backlink naît d'un lien wiki "
            f"`[[{row.get('title') or 'Titre exact'}]]` écrit dans le corps d'une "
            "autre page (résolu à l'écriture, insensible à la casse) — la prose, "
            "`[texte](doc:ID)` et `[texte](/docs/ID)` n'en créent aucun.")
    return out
