"""Les étapes d'une recette DÉCLARATIVE, lues dans son corps. Pur.

Une page de processus dessine les étapes d'une recette à l'endroit où il la lance
(`docs/recettes.md` §Affichage). Une recette déclarative les DIT déjà — pour chaque
ligne d'un tableau, quel outil, quel filtre, quelles colonnes, quelle clé : on les lit
dans la version qui tourne, donc elles ne dérivent jamais de ce qu'elle fait, et
personne n'a rien à écrire. Une recette-code n'a rien de lisible : ses étapes se
déclarent (`oto_recipe op=describe`).

Aucun crédit ici : ce qu'une étape facture n'est pas dans le corps (le plafond
`limits.max_units` n'est pas un prix).
"""
from __future__ import annotations

from typing import Any, Optional

from . import code

_LABEL_MAX = 60
_DETAIL_MAX = 200


def _court(texte: str, borne: int) -> str:
    return texte if len(texte) <= borne else texte[: borne - 1].rstrip() + "…"


def _etape(label: str, detail: str) -> dict:
    return {"label": _court(label, _LABEL_MAX), "detail": _court(detail, _DETAIL_MAX)}


def _source(corps: dict) -> str:
    pag = ((corps.get("source") or {}).get("pagination") or {})
    pages = (corps.get("limits") or {}).get("max_pages")
    if pag.get("type") == "page":
        taille = f"{pag['size']} a page, " if isinstance(pag.get("size"), int) else ""
        return f"Page by page, {taille}at most {pages} pages" if pages else "Page by page"
    if pag.get("type") == "cursor":
        return f"Cursor by cursor, at most {pages} pages" if pages else "Cursor by cursor"
    return "One call"


def _filtre(clauses: list) -> Optional[str]:
    tables = sorted({str(w.get("table")) for w in clauses
                     if isinstance(w, dict) and w.get("op") == "not_in_table"})
    autres = [w for w in clauses if isinstance(w, dict) and w.get("op") != "not_in_table"]
    morceaux = []
    if autres:
        morceaux.append(f"{len(autres)} condition{'s' if len(autres) > 1 else ''} on each item")
    if tables:
        morceaux.append("skips what table " + ", ".join(f"#{t}" for t in tables)
                        + " already holds")
    return "; ".join(morceaux) or None


def etapes_lues(corps: Any) -> Optional[dict]:
    """`{steps: [...]}` lu dans un corps déclaratif, ou None (recette-code, corps
    illisible). Même forme que `display`, sans crédits."""
    if not isinstance(corps, dict) or code.est_code(corps) or not corps.get("tool"):
        return None
    etapes = []
    fe = corps.get("for_each")
    if isinstance(fe, dict) and fe.get("datastore") is not None:
        filtre = " matching a filter" if fe.get("filter") else ""
        etapes.append(_etape("For each row",
                             f"Rows of table #{fe['datastore']}{filtre} whose "
                             f"{fe.get('status_column')} is empty"))
    etapes.append(_etape(f"Call {corps['tool']}", _source(corps)))
    filtre = _filtre(corps.get("where") or [])
    if filtre:
        etapes.append(_etape("Keep matching items", filtre))
    colonnes = len(corps.get("map") or {}) + len(corps.get("values") or {})
    cle = (corps.get("key") or {}).get("column")
    existantes = ("updated" if corps.get("on_existing") == "update" else "left untouched")
    etapes.append(_etape("Write rows",
                         f"{colonnes} column{'s' if colonnes != 1 else ''}, one row per "
                         f"{cle}; rows already there are {existantes}"))
    return {"steps": etapes}
