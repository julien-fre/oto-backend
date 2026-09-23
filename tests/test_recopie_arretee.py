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


def test_la_derniere_recopie_est_arretee_elle_aussi():
    """La sixième, celle des couches de contexte (`guides` → `nodes`), a survécu aux
    cinq autres jusqu'au 23/09/2026 (otomata-tech/oto#239).

    Elle était gardée pour une raison qui ne tenait plus : on la croyait « le seul
    chemin par lequel le readme plateforme arrive sur une base NEUVE ». Vérifié : son
    unique source était la table `platform_instructions`, que **plus personne
    n'écrit** — `instructions.seed_platform_blocks` est un no-op depuis l'ADR 0042, et
    la surface d'administration du bloc A lit et écrit `nodes` depuis le 28/07. Sur une
    base neuve elle ne semait donc RIEN, et le bloc A retombe sur sa constante
    (`instructions._platform_block`). Ce qu'elle faisait encore, en revanche : écrire à
    chaque boot dans une table que plus rien ne lit pour servir, et arbitrer en
    « la plus récente gagne » — une synchronisation permanente là où il fallait une
    fenêtre de promotion.
    """
    src = _INIT.read_text(encoding="utf-8")
    assert "CONVERT_GUIDES_TO_NODES_SQL" not in src
    assert "to_regclass('guides')" not in src


def test_le_bloc_plateforme_garde_son_repli():
    """Le contre-test : ce qui reste DOIT rester. Sans lui, l'arrêt ci-dessus serait
    satisfait par un code qui aurait aussi coupé le défaut du bloc A — et une base
    neuve servirait un socle de session VIDE à tous les comptes."""
    from oto_mcp import instructions
    assert instructions.default_block(instructions.KEY_SECRET_SAUCE).strip()
