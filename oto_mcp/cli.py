"""Le point d'entrée `oto-mcp` — le serveur par défaut, la maintenance sur demande.

Sans argument, c'est le serveur : `oto-mcp` démarre uvicorn, exactement comme avant
(l'unit systemd l'appelle nu, elle n'a pas changé). Avec `maintenance <travail>`, ce
sont les travaux de l'ADR 0065 (`oto_mcp.maintenance`).

⚠️ **L'aiguillage est là, et pas dans `server.main`, pour une raison mesurable** :
importer `oto_mcp.server` construit une instance MCP COMPLÈTE au niveau module
(`mcp = _build_mcp("noauth")` — register_all, montage des capacités, préparation de la
base). Un timer de maintenance qui paierait ce prix à chaque tir prendrait des dizaines
de secondes pour supprimer trois lignes. Ici, `import oto_mcp.server` n'a lieu que sur
le chemin du serveur.
"""
from __future__ import annotations

import sys


def main() -> None:
    argv = sys.argv[1:]
    if argv and argv[0] == "maintenance":
        from . import maintenance
        raise SystemExit(maintenance.main(argv[1:]))
    if argv:
        raise SystemExit(
            f"oto-mcp : argument inconnu {argv[0]!r}.\n"
            "  oto-mcp                        démarre le serveur\n"
            "  oto-mcp maintenance <travail>  joue un travail de maintenance "
            "(--help pour la liste)")
    from .server import main as serve
    serve()


if __name__ == "__main__":
    main()
