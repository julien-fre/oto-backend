"""La recopie des tables historiques vers `nodes` n'a plus lieu au démarrage.

Cinq conversions tournaient à chaque boot — projets, pages, procédures, tableaux,
lignes — et déposaient dans `nodes` une image marquée `props.legacy`. Elles
préparaient une bascule de lecture qui n'aura pas lieu : les deux univers vivent
côte à côte, chacun avec ses verbes, jusqu'au décommissionnement de l'ancien.

**Ce garde-fou lit le CODE, jamais un commentaire.** Il parcourt l'AST de
`db/_init.py` et cherche des APPELS ; un module qui expliquerait longuement qu'il
a cessé de recopier tout en gardant l'appel échoue ici. C'est la seule forme utile
— la précédente génération de gardes de ce dépôt s'accusait elle-même en lisant sa
propre prose, et passait au vert sur du code cassé.

⚠️ Ce test n'interdit PAS les fonctions `convert_*` de `db/nodes.py` : elles
survivent à l'arrêt et partent avec le déblaiement. Ce qui est interdit, c'est
qu'un boot les rappelle.
"""
from __future__ import annotations

import ast
import pathlib

_INIT = pathlib.Path(__file__).resolve().parents[1] / "oto_mcp" / "db" / "_init.py"

# Les cinq conversions du monde historique. `convert_guides` en fait partie :
# malgré son nom, elle convertit les PROCÉDURES (`org_instructions`), pas les
# couches de contexte.
_CONVERSIONS = {
    "convert_projects",
    "convert_docs",
    "convert_guides",
    "convert_tables",
    "convert_rows",
}


def _appels(source: str) -> set[str]:
    noms: set[str] = set()
    for n in ast.walk(ast.parse(source)):
        if not isinstance(n, ast.Call):
            continue
        f = n.func
        if isinstance(f, ast.Name):
            noms.add(f.id)
        elif isinstance(f, ast.Attribute):
            noms.add(f.attr)
    return noms


def test_le_boot_n_appelle_plus_aucune_conversion():
    rappelees = _CONVERSIONS & _appels(_INIT.read_text(encoding="utf-8"))
    assert not rappelees, (
        "Le démarrage rappelle une conversion : "
        + ", ".join(sorted(rappelees))
        + ". La recopie est arrêtée (2026-09-01) — la nouvelle surface part de "
        "vide et se remplit par ses propres verbes."
    )


def test_le_boot_n_importe_plus_les_conversions():
    """Un import qui survit annonce une réactivation en préparation."""
    arbre = ast.parse(_INIT.read_text(encoding="utf-8"))
    importes = {
        a.name
        for n in ast.walk(arbre)
        if isinstance(n, ast.ImportFrom)
        for a in n.names
    }
    assert not (_CONVERSIONS & importes), sorted(_CONVERSIONS & importes)


def test_les_couches_de_contexte_gardent_leur_projection():
    """Le contre-test : ce qui reste DOIT rester.

    Sans lui, ce fichier serait satisfait par un `_init.py` qui aurait aussi coupé
    le seul chemin du readme plateforme sur une base neuve — un arrêt qui emporte
    plus que ce qu'on a décidé d'arrêter.
    """
    arbre = ast.parse(_INIT.read_text(encoding="utf-8"))
    execute = [
        n
        for n in ast.walk(arbre)
        if isinstance(n, ast.Call)
        and any(
            isinstance(a, ast.Name) and a.id == "CONVERT_GUIDES_TO_NODES_SQL"
            for a in n.args
        )
    ]
    assert execute, (
        "Le boot ne joue plus CONVERT_GUIDES_TO_NODES_SQL : sur une base neuve, "
        "le readme plateforme n'atteint plus `nodes`. Cette projection ne part "
        "qu'avec un seed qui écrit nativement."
    )
