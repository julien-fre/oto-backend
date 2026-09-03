"""Backlinks `[[Titre]]` (lot 3 Ship 4) — le graphe léger de pages qui se citent.

La page EST l'entité, le backlink EST la mention (ADR entités du plan §9) : pas de
NER, pas de liens *typés* — seulement `[[…]]` non typé, résolu à l'écriture contre
le **projet courant + la KB de l'org** (précédence projet > KB). Table dérivée
`doc_links`, reconstructible.

**Hook au niveau `db`** (pas capacité) : `resolve_change` appelle `db.update_doc`
en direct → un hook posé au niveau capacité raterait les acceptations. Ce module
est appelé par `db.create_doc`/`update_doc`/`delete_doc`.

Ambiguïté ≠ inexistence (plan E1) : titres non uniques par projet →
**précédence déterministe** (projet courant > KB, puis plus petit id) ; N=0 =
lien-souche (rendu côté UI, aucune ligne stockée) ; N>1 = on lie le premier (jamais
de création). La résolution est insensible à la casse et aux espaces de bord.
"""
from __future__ import annotations

import re
from typing import Optional

# `[[Titre de page]]` — capture le titre brut (sans les crochets). Non greedy,
# pas de `]` interne (un titre n'en contient pas), borné en longueur (anti-abus).
_WIKILINK = re.compile(r"\[\[\s*([^\[\]\n]{1,200}?)\s*\]\]")


def extract_titles(body_md: str) -> list[str]:
    """Titres cités par `[[…]]` dans un corps markdown, dédupliqués (casse/espaces
    normalisés pour la clé, forme d'origine conservée pour l'ordre d'apparition)."""
    seen: set[str] = set()
    out: list[str] = []
    for m in _WIKILINK.finditer(body_md or ""):
        t = " ".join(m.group(1).split())
        k = t.casefold()
        if t and k not in seen:
            seen.add(k)
            out.append(t)
    return out


def reresolve_referrers(conn, project_id: int, *titles: Optional[str]) -> None:
    """Re-résout les liens SORTANTS des pages qui citent l'un des `titles` par `[[…]]`
    (oto/#6 C). Appelé quand une page est CRÉÉE ou RENOMMÉE : un `[[Titre]]` écrit AVANT
    que la page cible existe (ou sous son ancien nom) était un lien-souche non stocké —
    en re-résolvant les référents, il se lie (création) ou se délie proprement (renommage).
    Scope = projet + sa KB (là où `[[Titre]]` résoudrait). Petits arbres → scan + filtre
    Python (pas d'ILIKE à échapper)."""
    keys = {" ".join((t or "").split()).casefold() for t in titles if t and t.strip()}
    if not keys:
        return
    kb = _kb_project_of(conn, project_id)
    scope = [project_id] + ([kb] if kb and kb != project_id else [])
    rows = conn.execute(
        "SELECT id, project_id, body_md FROM docs WHERE project_id = ANY(%s)",
        (scope,)).fetchall()
    for r in rows:
        cited = {t.casefold() for t in extract_titles(r["body_md"] or "")}
        if cited & keys:
            refresh_links(conn, r["id"], r["project_id"], r["body_md"] or "")


def _kb_project_of(conn, project_id: int) -> Optional[int]:
    """La KB de l'org qui CONTEXTE ce projet (org owner, sinon `context_org_id`
    membre, sinon l'org d'un projet de groupe), ou None. Une requête."""
    row = conn.execute(
        "SELECT owner_type, owner_id, context_org_id FROM projects WHERE id = %s",
        (project_id,)).fetchone()
    if row is None:
        return None
    org_id: Optional[str] = None
    if row["owner_type"] == "org":
        org_id = str(row["owner_id"])
    elif row["context_org_id"] is not None:
        org_id = str(row["context_org_id"])
    elif row["owner_type"] == "group":
        g = conn.execute("SELECT org_id FROM org_groups WHERE id = %s",
                         (row["owner_id"],)).fetchone()
        org_id = str(g["org_id"]) if g else None
    if org_id is None:
        return None
    kb = conn.execute("SELECT kb_project_id FROM orgs WHERE id = %s::bigint",
                      (org_id,)).fetchone()
    return int(kb["kb_project_id"]) if kb and kb["kb_project_id"] is not None else None


def refresh_links(conn, from_doc: int, project_id: int, body_md: str,
                  trace: Optional[dict] = None) -> None:
    """Recalcule les backlinks SORTANTS de `from_doc` (appelé à create/update).
    Résout chaque `[[Titre]]` contre le projet courant puis la KB (précédence),
    par plus petit id à égalité ; remplace en bloc les liens existants du doc.

    `trace` (dict mutable, optionnel) = le relevé des citations qui n'ont RIEN
    trouvé, sous `citations_sans_cible` (#611). Un lien-souche n'est stocké nulle
    part et n'était dit nulle part : la page se croyait citée, l'index ne le
    savait pas, et personne ne pouvait l'apprendre autrement qu'en interrogeant
    les liens entrants de chaque cible une par une.

    ⚠️ **Le cas mesuré est une ASYMÉTRIE de portée, pas un bug d'extraction**
    (reproduit le 03/09 sur le banc factice) : une page de la **KB** résout
    contre `[KB]` seule, tandis qu'une page de **projet** résout contre
    `[projet, KB]`. Une page de KB qui cite une page de projet ne la trouve donc
    jamais — alors que l'inverse marche. Le lien est à sens unique dans l'index
    et à double sens dans la prose. Ce n'est pas réparable par une réécriture,
    ce qui explique qu'un `op=update` complet n'ait rien changé.
    """
    titles = extract_titles(body_md)
    conn.execute("DELETE FROM doc_links WHERE from_doc = %s", (from_doc,))
    if not titles:
        return
    kb_pid = _kb_project_of(conn, project_id)
    scope = [project_id] + ([kb_pid] if kb_pid and kb_pid != project_id else [])
    # Tous les docs candidats des projets en scope, en un scan ; on choisit en Python
    # (petits arbres) : même projet d'abord (précédence), puis plus petit id.
    rows = conn.execute(
        "SELECT id, project_id, title FROM docs WHERE project_id = ANY(%s)",
        (scope,)).fetchall()
    by_title: dict[str, list[dict]] = {}
    for r in rows:
        by_title.setdefault(" ".join((r["title"] or "").split()).casefold(), []).append(r)

    def _rank(r: dict) -> tuple:
        return (0 if r["project_id"] == project_id else 1, r["id"])

    targets: set[int] = set()
    orphelines: list[str] = []
    for t in titles:
        cands = by_title.get(t.casefold())
        if not cands:
            orphelines.append(t)           # lien-souche (N=0) : rendu UI, pas stocké
            continue
        winner = min(cands, key=_rank)
        if winner["id"] != from_doc:       # une page ne se cite pas elle-même
            targets.add(winner["id"])
    for to_doc in targets:
        conn.execute(
            "INSERT INTO doc_links (from_doc, to_doc) VALUES (%s, %s) "
            "ON CONFLICT DO NOTHING", (from_doc, to_doc))
    if trace is not None and orphelines:
        trace["citations_sans_cible"] = orphelines
        trace["citations_sans_cible_hint"] = (
            f"{len(orphelines)} citation(s) `[[…]]` ne désignent aucune page "
            "atteignable depuis ici, donc elles ne créent AUCUN lien entrant : "
            "la cible lira cette page comme absente de ses liens. La résolution "
            "regarde ce projet puis la base de connaissance de l'org — et rien "
            "d'autre : depuis une page de la BASE, une page de projet est hors "
            "de portée. Vérifie le titre exact, ou place la cible à portée.")


def backlinks_of(conn, doc_id: int) -> list[dict]:
    """Pages qui CITENT `doc_id` (« Cité par »), avec leur projet. Filtrage d'accès
    = à l'appelant (capacité) : ici on rend tout, le call-site scope."""
    rows = conn.execute(
        "SELECT d.id, d.project_id, d.title FROM doc_links l "
        "JOIN docs d ON d.id = l.from_doc WHERE l.to_doc = %s ORDER BY d.title",
        (doc_id,)).fetchall()
    return [dict(r) for r in rows]
