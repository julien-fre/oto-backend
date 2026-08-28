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

import logging
import os
import sys

_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def _configurer_le_journal() -> None:
    """Pose les handlers AVANT tout import qui journalise — et c'est le point.

    ⚠️ Le premier tiers du démarrage se passe **à l'import** d'`oto_mcp.server`, qui
    construit l'instance anonyme au niveau module et prépare donc la base. Tant que
    `logging.basicConfig` vivait dans `server.main`, tout ce que ce tiers journalisait
    en INFO tombait dans le vide : sans handler, le `lastResort` de la stdlib n'émet
    qu'à partir de WARNING. Constaté en production le 2026-08-28 — les lignes
    `boot: <étape> <n> ms` que l'ADR 0065 demande n'apparaissaient **nulle part** dans
    le journal de la box, alors que le code les émettait.

    Une instrumentation qui ne journalise pas est pire que pas d'instrumentation : on
    la croit posée, et on mesure en croyant avoir mesuré.

    Idempotent : `basicConfig` ne fait rien si le root logger a déjà des handlers, donc
    l'appel qui subsiste dans `server.main` reste un no-op inoffensif.
    """
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format=_FORMAT)


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
    # AVANT l'import : cet import EST déjà du démarrage, et il journalise.
    _configurer_le_journal()
    from .server import main as serve
    serve()


if __name__ == "__main__":
    main()
