"""La suite doit exercer CE tree, jamais un autre checkout (vécu le 2026-08-28).

Le tree partagé `/data/oto/backend` est installé en **editable** : son finder est
appendu à `sys.meta_path`. Un import resté à l'ancien chemin après un déplacement
(`from .. import datastore_schema as dsv2`, caché dans une liste avec un alias) y
**résout encore** — sur le fichier d'AVANT, dans l'autre checkout. La suite passe
alors au vert sur du code qui n'existe plus dans la branche, et c'est la CI qui
l'apprend, onze `ImportError` plus tard.

Le garde-fou ne connaît aucun nom de module : il vérifie une propriété du RÉSULTAT —
tout `oto_mcp.*` chargé vient du répertoire de ce dépôt. Il reste donc vrai au
prochain déplacement, et à celui d'après.

Inerte en CI (aucun autre checkout n'y existe) : c'est en LOCAL qu'il mord, et c'est
là que le défaut vit.

Ses deux exemples se construisent hors du dépôt ou dedans, **jamais par un chemin
écrit en dur** : un exemple dont le verdict dépend de l'endroit où le dépôt est
posé prouve l'environnement, pas la garde.
"""
from __future__ import annotations

import contextlib
import importlib.util
import pathlib
import sys

from oto_mcp import server  # noqa: F401 — charge le boot réel, donc le gros du paquet

RACINE = pathlib.Path(__file__).resolve().parents[1]


def _etrangers(modules) -> dict[str, str]:
    """Les modules `oto_mcp.*` servis depuis un AUTRE répertoire que ce dépôt."""
    out = {}
    for nom, mod in list(modules.items()):
        if nom != "oto_mcp" and not nom.startswith("oto_mcp."):
            continue
        fichier = getattr(mod, "__file__", None)
        if not fichier:
            continue
        chemin = pathlib.Path(fichier).resolve()
        if RACINE not in chemin.parents:
            out[nom] = str(chemin)
    return out


@contextlib.contextmanager
def _charge(nom: str, fichier: pathlib.Path):
    """Charge VRAIMENT `fichier` sous `nom` dans `sys.modules`, et l'en retire après.

    Un `__file__` posé à la main prouverait la comparaison de chaînes ; ce qu'on veut
    prouver, c'est le module tel que le finder editable le sert — donc un vrai
    chargement, dont le `__file__` vient du fichier lui-même.
    """
    spec = importlib.util.spec_from_file_location(nom, fichier)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[nom] = mod
    try:
        spec.loader.exec_module(mod)
        yield mod
    finally:
        sys.modules.pop(nom, None)


def test_aucun_module_ne_vient_d_un_autre_checkout():
    etrangers = _etrangers(sys.modules)
    assert not etrangers, (
        "ces modules sont servis depuis un AUTRE checkout que celui qu'on teste "
        f"(repli de l'install editable) : {etrangers} — un import est resté sur un "
        "chemin déplacé ; la suite ment tant qu'il résout ailleurs.")


def test_le_garde_fou_mord(tmp_path):
    """Un garde-fou d'inventaire se prouve en lui présentant l'anomalie qu'il vise."""
    ailleurs = tmp_path / "ailleurs" / "oto_mcp"
    ailleurs.mkdir(parents=True)
    fichier = ailleurs / "parti_ailleurs.py"
    fichier.write_text('"""Le fichier d\'AVANT, resté dans l\'autre checkout."""\n')

    with _charge("oto_mcp.parti_ailleurs", fichier):
        signales = _etrangers(sys.modules)

    assert signales.get("oto_mcp.parti_ailleurs") == str(fichier.resolve()), (
        "un `oto_mcp.*` servi hors du dépôt doit être signalé ; il ne l'est pas, "
        f"la garde est aveugle à ce qu'elle vise : {signales}")


def test_le_garde_fou_laisse_passer_ce_qui_vient_D_ICI():
    """L'autre sens : une garde qui signale tout ne signale rien."""
    dedans = RACINE / "oto_mcp" / "fod" / "__init__.py"
    assert dedans.is_file(), f"exemple introuvable dans le dépôt : {dedans}"

    with _charge("oto_mcp.reste_ici", dedans):
        signales = _etrangers(sys.modules)

    assert "oto_mcp.reste_ici" not in signales, (
        "un `oto_mcp.*` servi depuis CE dépôt ne doit pas être signalé ; la garde "
        f"crie au loup et son verdict ne veut plus rien dire : {signales}")
