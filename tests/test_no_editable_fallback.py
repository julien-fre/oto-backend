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
"""
from __future__ import annotations

import pathlib
import sys
import types

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


def test_aucun_module_ne_vient_d_un_autre_checkout():
    etrangers = _etrangers(sys.modules)
    assert not etrangers, (
        "ces modules sont servis depuis un AUTRE checkout que celui qu'on teste "
        f"(repli de l'install editable) : {etrangers} — un import est resté sur un "
        "chemin déplacé ; la suite ment tant qu'il résout ailleurs.")


def test_le_garde_fou_mord():
    """Un garde-fou d'inventaire se prouve en lui présentant l'anomalie qu'il vise."""
    faux = types.ModuleType("oto_mcp.parti_ailleurs")
    faux.__file__ = "/data/oto/backend/oto_mcp/parti_ailleurs.py"
    assert _etrangers({"oto_mcp.parti_ailleurs": faux}) == {
        "oto_mcp.parti_ailleurs": "/data/oto/backend/oto_mcp/parti_ailleurs.py"}
