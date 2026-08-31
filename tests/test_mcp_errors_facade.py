"""Un seul endroit du dépôt nomme la classe d'erreur du SDK amont.

Le SDK a renommé `McpError` → `MCPError`. Deux noms, **aucun recouvrement** : la
version d'avant n'expose que l'ancien, celle d'après que le nouveau. 213 fichiers
l'importaient en direct — le renommage a donc cassé le CI d'un coup, sur toutes les
branches à la fois, pendant que les environnements de développement plus anciens
restaient verts.

C'est le mode d'échec qui coûte le plus cher : **invisible là où on travaille, total
là où on vérifie**. Et il ne se répare pas en épinglant une version — `mcp` n'est même
pas une dépendance déclarée, elle arrive derrière `fastmcp`.
"""
from __future__ import annotations

import pathlib

RACINE = pathlib.Path(__file__).resolve().parent.parent
FACADE = RACINE / "oto_mcp" / "mcp_errors.py"


def test_la_facade_resout_la_classe():
    from oto_mcp.mcp_errors import McpError
    assert isinstance(McpError, type) and issubclass(McpError, Exception)


def test_elle_essaie_le_nom_RECENT_en_premier():
    """L'ordre n'est pas neutre : une version qui porterait les deux (alias de
    transition) doit nous voir prendre celui qui RESTE, pas celui qui part."""
    src = FACADE.read_text(encoding="utf-8")
    assert src.index("MCPError") < src.index("import McpError    # noqa"), (
        "l'ancien nom est essayé avant le nouveau : on s'accrocherait à l'alias "
        "déprécié tant qu'il survit, et on casserait le jour où il disparaît")


def test_PERSONNE_d_autre_ne_nomme_la_classe_amont():
    """TRIPWIRE — c'est tout l'objet du lot. Un seul import direct qui revient, et le
    prochain renommage recasse le CI de toutes les branches en même temps."""
    fautifs = []
    for f in list((RACINE / "oto_mcp").rglob("*.py")) + list((RACINE / "tests").rglob("*.py")):
        if f.resolve() == FACADE.resolve():
            continue
        for n, ligne in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            nu = ligne.strip()
            if nu.startswith("from mcp.shared.exceptions import") and "Error" in nu:
                fautifs.append(f"{f.relative_to(RACINE)}:{n}")
    assert not fautifs, (
        "la classe d'erreur amont est nommée hors de sa façade :\n  "
        + "\n  ".join(fautifs)
        + "\n→ `from ..mcp_errors import McpError`. Le nom amont bouge ; le nôtre non.")


def test_la_facade_ne_depend_de_rien_du_projet():
    """Elle est importée par 213 modules, dont certains très bas : lui donner une
    dépendance interne créerait un cycle d'import au premier d'entre eux."""
    src = FACADE.read_text(encoding="utf-8")
    for ligne in src.splitlines():
        nu = ligne.strip()
        if nu.startswith(("from .", "from oto_mcp")):
            raise AssertionError(f"la façade dépend du projet : {nu}")
