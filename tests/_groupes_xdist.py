"""Un module à fixture MODULE-scopée reste sur UN worker xdist (#963).

Sous `pytest -n 4` la distribution par défaut (`load`) découpe au niveau du TEST : deux
tests d'un même module peuvent tomber sur deux workers, et une fixture `scope="module"`
est alors instanciée DEUX fois — deux bases Postgres jetables (`pg_module_dsn`), deux
états. Le second test ne voit jamais ce que le premier a écrit dans « sa » base :
`get_instruction(...)` rend `None`, le tronc rougit sur un test sans rapport avec le
changement, puis passe au run suivant. Vu le 15/09/2026 sur
`test_procedure_paliers_681.py`, et sur deux runs consécutifs du tronc.

**Pourquoi pas `--dist loadfile` / `loadscope` nu** : ils regroupent TOUS les fichiers, y
compris les ~600 qui n'ont aucune fixture module-scopée et que `load` équilibre au
grain fin (charge diffuse, cf. docs/commands.md §Suite parallèle en CI). Ici seuls les
tests dont la fermeture de fixtures contient une fixture `module` reçoivent
`xdist_group(<fichier>)` ; avec `--dist loadgroup` les autres restent répartis test par
test. La détection est faite sur les fixtures, pas sur une liste de fichiers : un module
futur est couvert sans qu'on ait à y penser.

⚠️ Le hook appelant doit être `tryfirst` : xdist lit `xdist_group` dans son propre
`pytest_collection_modifyitems` ; un marqueur posé après lui est ignoré sans erreur.

⚠️ `xdist_group` n'a d'effet qu'avec `--dist loadgroup` : les workflows le passent, et
`test_groupes_xdist_963.py` refuse un `-n` sans lui. Hors xdist (run série), le marqueur
est inerte.
"""
from __future__ import annotations

import pytest


def _fixtures_module(item: pytest.Item) -> list[str]:
    """Noms des fixtures `scope="module"` dans la fermeture de l'item (autouse comprises)."""
    info = getattr(item, "_fixtureinfo", None)
    if info is None:
        return []
    return sorted(nom for nom, defs in info.name2fixturedefs.items()
                  if defs and defs[-1].scope == "module")


def fichier_de(item: pytest.Item) -> str:
    return item.nodeid.split("::", 1)[0]


def regrouper(items: list[pytest.Item]) -> int:
    """Pose `xdist_group(<fichier>)` sur chaque item à fixture module-scopée. Rend le
    nombre d'items marqués."""
    n = 0
    for item in items:
        if item.get_closest_marker("xdist_group") is None and _fixtures_module(item):
            item.add_marker(pytest.mark.xdist_group(name=fichier_de(item)))
            n += 1
    return n
