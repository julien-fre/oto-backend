"""`oto-mcp migrer [args alembic…]` — Alembic, avec la configuration du dépôt.

`oto-mcp migrer upgrade head`, `oto-mcp migrer current`, `oto-mcp migrer upgrade head
--sql`… : les arguments sont ceux de la commande `alembic`, passés tels quels. Ce qui
change, c'est d'où vient la configuration — du paquet, jamais du répertoire courant :

- `alembic.ini` à la racine de l'arbre (installation éditable, `pip install -e .`) ;
- le registre des révisions là où le démarrage le lit (`db._version_alembic._REGISTRE`) :
  la version qu'estampille le démarrage et celle qu'applique `migrer` sortent du même
  dossier ;
- la connexion par `DATABASE_URL`, lue par `db/migrations/env.py` comme le pool.

Une base plus ancienne que la référence du registre (squash, docs/migrations-versionnees.md
§5.4) est refusée par `env.py` avant toute commande : `migrer` sort en la nommant, avec le
tag qui la monte d'abord — jamais sur le « Can't locate revision » d'Alembic.

**Pourquoi une sous-commande et pas `python -m alembic`** (oto-backend#1105) : sur une box
passée au lanceur de secrets (`deploy/lanceur_secrets.py`), `DATABASE_URL` n'existe que
dans l'environnement que le lanceur construit, et le lanceur n'exécute que `oto-mcp …` ou
un script de l'arbre. `lanceur_secrets.py migrer upgrade head` suffit donc — la commande
exacte, sous `systemd-run`, est dans docs/migrations-versionnees.md §5.
"""
from __future__ import annotations

from alembic.config import CommandLine, Config

from .db._version_alembic import _REGISTRE, BaseAnterieureALaReference

ALEMBIC_INI = _REGISTRE.parents[2] / "alembic.ini"


def main(argv: list[str]) -> int:
    if not ALEMBIC_INI.is_file():
        raise SystemExit(f"oto-mcp migrer : {ALEMBIC_INI} introuvable — `migrer` exige "
                         "l'arbre du dépôt installé en éditable (pip install -e .)")
    ligne = CommandLine(prog="oto-mcp migrer")
    options = ligne.parser.parse_args(["-c", str(ALEMBIC_INI), *argv])
    if not hasattr(options, "cmd"):
        ligne.parser.error("une commande alembic est attendue (upgrade head, current…)")
    if options.config != [str(ALEMBIC_INI)]:
        ligne.parser.error("-c/--config : la configuration est celle du dépôt, pas une autre")
    config = Config(file_=str(ALEMBIC_INI), ini_section=options.name, cmd_opts=options)
    config.set_main_option("script_location", str(_REGISTRE))
    try:
        ligne.run_cmd(config, options)
    except BaseAnterieureALaReference as refus:
        raise SystemExit(f"oto-mcp migrer : {refus}") from refus
    return 0
