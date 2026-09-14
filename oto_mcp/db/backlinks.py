"""Backlinks `[[Titre]]` (lot 3 Ship 4) — le graphe léger de pages qui se citent.

La page EST l'entité, le backlink EST la mention (ADR entités du plan §9) : pas de
NER, pas de liens *typés* — seulement `[[…]]` non typé, résolu à l'écriture contre
le **projet de la page**, puis **les projets que possède l'organisation** qui
contexte ce projet. Table dérivée `doc_links`, reconstructible.

**La portée, tranchée le 11/09/2026** (signaux #888, #890). La seconde marche était
jusque-là le seul projet ANCRÉ de l'org (`orgs.kb_project_id`) — or aucun chemin de
création d'org n'a jamais posé d'ancre : elle naissait paresseusement (à l'ouverture
du tableau de bord jusqu'au 19/08, puis par `oto_kb` jusqu'à son retrait le 10/09).
Une org créée depuis ne pouvait donc lier aucune page d'un projet à une autre, quand
les anciennes le pouvaient. La portée est maintenant la même partout, sans ancre :
- d'abord le projet de la page — le plus proche l'emporte toujours ;
- puis les projets VIVANTS POSSÉDÉS par l'org. Jamais un projet d'équipe (fermé à
  dessein, ADR 0049), un projet personnel, ni un projet d'une autre organisation : la
  frontière de l'org n'est jamais franchie, et un projet d'org étant lisible de tous
  ses membres, la résolution ne dit rien que ses membres ne voient déjà.

**Hook au niveau `db`** (pas capacité) : posé au niveau capacité, il raterait tout
chemin qui écrit une page en appelant `db` en direct. Ce module est appelé par
`db.create_doc`/`update_doc`/`delete_doc`.

Ambiguïté ≠ inexistence (plan E1) : N=0 = lien-souche (rendu côté UI, aucune ligne
stockée, dit dans `citations_sans_cible`). Un titre porté par des pages de PLUSIEURS
projets de l'org — et absent du projet de la page — est AMBIGU : aucune page n'est
choisie, ni la plus ancienne ni une « plus proche » devinée, et c'est dit avec les
candidats (`citations_ambigues`). À l'intérieur d'UN projet, des titres en double se
départagent par le plus petit id (jamais de création). La résolution est insensible
à la casse et aux espaces de bord.
"""
from __future__ import annotations

import re
from typing import Optional

# `[[Titre de page]]` — capture le titre brut (sans les crochets). Non greedy,
# pas de `]` interne (un titre n'en contient pas), borné en longueur (anti-abus).
_WIKILINK = re.compile(r"\[\[\s*([^\[\]\n]{1,200}?)\s*\]\]")


def _cle(titre: Optional[str]) -> str:
    return " ".join((titre or "").split()).casefold()


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


def _org_of_project(conn, project_id: int) -> Optional[str]:
    """L'org qui CONTEXTE ce projet (propriétaire org, sinon `context_org_id` membre,
    sinon l'org d'un projet d'équipe), ou None. Une requête, deux pour une équipe."""
    row = conn.execute(
        "SELECT owner_type, owner_id, context_org_id FROM projects WHERE id = %s",
        (project_id,)).fetchone()
    if row is None:
        return None
    if row["owner_type"] == "org":
        return str(row["owner_id"])
    if row["context_org_id"] is not None:
        return str(row["context_org_id"])
    if row["owner_type"] == "group":
        g = conn.execute("SELECT org_id FROM org_groups WHERE id = %s",
                         (row["owner_id"],)).fetchone()
        return str(g["org_id"]) if g else None
    return None


def _org_owned_projects(conn, org_id: Optional[str]) -> list[int]:
    """Les projets VIVANTS possédés par `org_id` — la seconde marche de la résolution.
    Ni équipe, ni personnel, ni une autre org : c'est la frontière, pas un réglage."""
    if org_id is None:
        return []
    rows = conn.execute(
        "SELECT id FROM projects WHERE owner_type = 'org' AND owner_id = %s "
        "AND archived_at IS NULL", (str(org_id),)).fetchall()
    return [int(r["id"]) for r in rows]


def resolution_scope(conn, project_id: int) -> list[int]:
    """Les projets où un `[[Titre]]` écrit dans `project_id` peut trouver sa cible :
    lui-même d'abord, puis les projets de l'org qui le contexte."""
    org_projects = _org_owned_projects(conn, _org_of_project(conn, project_id))
    return [project_id] + [p for p in org_projects if p != project_id]


def _referrer_projects(conn, project_id: int) -> list[int]:
    """Les projets dont les pages PEUVENT citer une page de `project_id` — l'inverse de
    `resolution_scope`. Une page d'un projet d'org vivant est à portée de toute page
    contextée par cette org (ses projets, ceux de ses équipes, les projets personnels
    de ses membres) ; une page d'un autre projet ne l'est que du sien."""
    row = conn.execute(
        "SELECT owner_type, owner_id, archived_at FROM projects WHERE id = %s",
        (project_id,)).fetchone()
    if row is None or row["owner_type"] != "org" or row.get("archived_at") is not None:
        return [project_id]
    org = str(row["owner_id"])
    rows = conn.execute(
        "SELECT id FROM projects WHERE (owner_type = 'org' AND owner_id = %s) "
        "OR context_org_id::text = %s OR (owner_type = 'group' AND owner_id IN "
        "(SELECT id::text FROM org_groups WHERE org_id::text = %s))",
        (org, org, org)).fetchall()
    return sorted({project_id} | {int(r["id"]) for r in rows})


def reresolve_referrers(conn, project_id: int, *titles: Optional[str]) -> None:
    """Re-résout les liens SORTANTS des pages qui citent l'un des `titles` par `[[…]]`
    (oto/#6 C). Appelé quand une page est CRÉÉE ou RENOMMÉE : un `[[Titre]]` écrit AVANT
    que la page cible existe (ou sous son ancien nom) était un lien-souche non stocké —
    en re-résolvant les référents, il se lie (création) ou se délie proprement (renommage).

    Portée = tous les projets depuis lesquels la page est citable (`_referrer_projects`).
    Du temps de l'ancre, elle se réduisait au projet de la cible + l'ancre : une cible
    née DANS l'ancre laissait souches les pages des autres projets qui la citaient,
    jusqu'à leur propre réécriture. Préfiltre SQL sur `[[` (une page sans lien n'a rien
    à re-résoudre), puis filtre exact en Python (pas d'ILIKE à échapper)."""
    keys = {_cle(t) for t in titles if t and t.strip()}
    if not keys:
        return
    rows = conn.execute(
        "SELECT id, project_id, body_md FROM docs WHERE project_id = ANY(%s) "
        "AND body_md LIKE %s", (_referrer_projects(conn, project_id), "%[[%")).fetchall()
    for r in rows:
        cited = {t.casefold() for t in extract_titles(r["body_md"] or "")}
        if cited & keys:
            refresh_links(conn, r["id"], r["project_id"], r["body_md"] or "")


def refresh_links(conn, from_doc: int, project_id: int, body_md: str,
                  trace: Optional[dict] = None) -> None:
    """Recalcule les backlinks SORTANTS de `from_doc` (appelé à create/update).
    Résout chaque `[[Titre]]` contre le projet de la page, puis contre les projets de
    son org ; remplace en bloc les liens existants du doc.

    `trace` (dict mutable, optionnel) = le relevé des citations qui n'ont RIEN
    trouvé, sous `citations_sans_cible` (#611), et de celles qui en ont trouvé
    PLUSIEURS dans des projets différents, sous `citations_ambigues` (#888). Un
    lien-souche n'est stocké nulle part et n'était dit nulle part : la page se
    croyait citée, l'index ne le savait pas.

    ⚠️ **Le graphe reste asymétrique, mais plus entre projets d'org.** Une page d'un
    projet d'équipe ou personnel résout vers les projets de l'org ; aucune page d'un
    projet d'org ne peut, elle, atteindre une page d'équipe ou personnelle.

    ⚠️ **La portée d'ÉCRITURE n'est pas celle de LECTURE** (signal #696, mesuré
    sur vrai PG par `test_backlinks.py`) : `backlinks_of` rend TOUTE ligne pointant
    vers la page, sans filtre de projet — et rien ne recale les liens ENTRANTS
    d'une page déplacée (`move_doc_to_project` ne re-résout que les liens SORTANTS
    des pages déplacées). Une ligne stockée survit donc au déplacement de sa cible,
    hors de toute portée de résolution, et ne meurt qu'à la prochaine écriture de
    la page qui cite. Le relevé ci-dessous doit le DIRE.
    """
    titles = extract_titles(body_md)
    conn.execute("DELETE FROM doc_links WHERE from_doc = %s", (from_doc,))
    if not titles:
        return
    # Tous les docs candidats des projets en portée, en un scan (titres seuls) ; on
    # choisit en Python : même projet d'abord, sinon UN seul projet de l'org, sinon rien.
    rows = conn.execute(
        "SELECT id, project_id, title FROM docs WHERE project_id = ANY(%s)",
        (resolution_scope(conn, project_id),)).fetchall()
    by_title: dict[str, list[dict]] = {}
    for r in rows:
        by_title.setdefault(_cle(r["title"]), []).append(r)

    targets: set[int] = set()
    orphelines: list[str] = []
    ambigues: list[dict] = []
    for t in titles:
        cands = by_title.get(t.casefold())
        if not cands:
            orphelines.append(t)           # lien-souche (N=0) : rendu UI, pas stocké
            continue
        locaux = [c for c in cands if c["project_id"] == project_id]
        if not locaux and len({c["project_id"] for c in cands}) > 1:
            ambigues.append({"titre": t, "candidats": [
                {"doc_id": c["id"], "project_id": c["project_id"]}
                for c in sorted(cands, key=lambda c: (c["project_id"], c["id"]))]})
            continue
        winner = min(locaux or cands, key=lambda c: c["id"])
        if winner["id"] != from_doc:       # une page ne se cite pas elle-même
            targets.add(winner["id"])
    for to_doc in targets:
        conn.execute(
            "INSERT INTO doc_links (from_doc, to_doc) VALUES (%s, %s) "
            "ON CONFLICT DO NOTHING", (from_doc, to_doc))
    if trace is None:
        return
    if orphelines:
        trace["citations_sans_cible"] = orphelines
        trace["citations_sans_cible_hint"] = (
            f"{len(orphelines)} citation(s) `[[…]]` ne désignent aucune page "
            "atteignable depuis ici, donc elles ne créent AUCUN lien entrant : "
            "la cible lira cette page comme absente de ses liens. À L'ÉCRITURE, "
            "la résolution regarde ce projet, puis les projets que possède "
            "l'organisation — jamais un projet d'équipe, un projet personnel ni un "
            "projet d'une autre organisation : une page qui vit là est hors de "
            "portée. `op=backlinks` ne mesure PAS "
            "la même chose : il rend les liens DÉJÀ STOCKÉS quel que soit leur "
            "projet, y compris ceux qu'un déplacement de page a laissés hors de "
            "cette portée — y voir un lien venu d'ailleurs ne dément donc pas ce "
            "relevé, et ne prouve pas que la citation résoudrait aujourd'hui : "
            "ce lien-là disparaîtra à la prochaine écriture de la page qui cite. "
            "Vérifie le titre exact, ou place la cible à portée.")
    if ambigues:
        trace["citations_ambigues"] = ambigues
        trace["citations_ambigues_hint"] = (
            f"{len(ambigues)} citation(s) `[[…]]` désignent un titre que portent des "
            "pages de PLUSIEURS projets de l'organisation (`candidats`) : aucune "
            "n'est choisie — ni la plus ancienne, ni une plus proche devinée — donc "
            "AUCUN lien n'est créé. Pour lier : rends le titre unique dans "
            "l'organisation (renomme l'une des pages), ou place la page visée dans "
            "le projet de la page qui cite, qui l'emporte toujours.")


def backlinks_of(conn, doc_id: int) -> list[dict]:
    """Pages qui CITENT `doc_id` (« Cité par »), avec leur projet. Filtrage d'accès
    = à l'appelant (capacité) : ici on rend tout, le call-site scope."""
    rows = conn.execute(
        "SELECT d.id, d.project_id, d.title FROM doc_links l "
        "JOIN docs d ON d.id = l.from_doc WHERE l.to_doc = %s ORDER BY d.title",
        (doc_id,)).fetchall()
    return [dict(r) for r in rows]
