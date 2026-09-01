"""Le même mur ne se dit pas de deux façons.

Le refus « il faut être administrateur de cette org » s'écrivait à quatre endroits
sous DEUX formulations — « de ton org active » et « de l'org #N ». Même mur, deux
phrases, et un appelant en déduit deux causes.

Le relevé du journal de #681 le montre en dix minutes : après un premier refus,
l'appelant vise l'équipe (`_group=3`), se prend une erreur de validation brute, puis
repasse le scope explicitement — et reçoit l'AUTRE formulation du premier refus. Il
alterne donc entre deux stratégies en croyant progresser. Ce n'est pas un appelant qui
ignore un refus : c'est un refus qui se présente comme deux.
"""
from __future__ import annotations

import ast
import inspect

from oto_mcp.capabilities import _authz


def test_le_refus_nomme_lorg():
    """« ton org active » oblige à deviner laquelle — et c'est cette ambiguïté qui
    fait retenter au lieu de comprendre."""
    refus = _authz._refus_org_admin(35)
    assert "#35" in str(refus.detail if hasattr(refus, "detail") else refus)
    assert refus.status == 403 and refus.code == "forbidden"


def test_une_seule_formulation_dans_tout_le_module():
    """TRIPWIRE — le mur se dit à une seule place. Deux phrases pour un même refus
    coûtent à l'appelant le temps de croire qu'il progresse."""
    # ⚠️ On lit les CHAÎNES du module, jamais sa prose : une docstring cite ce refus
    # pour expliquer qu'il serait FAUX à cet endroit-là, et un tripwire qui accuse le
    # commentaire au lieu du code finit désactivé.
    src = inspect.getsource(_authz)
    arbre = ast.parse(src)
    docstrings = set()
    for n in ast.walk(arbre):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
            d = ast.get_docstring(n, clean=False)
            if d:
                docstrings.add(d)
    en_dur = [n.value for n in ast.walk(arbre)
              if isinstance(n, ast.Constant) and isinstance(n.value, str)
              and "Réservé à un" in n.value and "org" in n.value
              and n.value not in docstrings
              and "administrateur de l'org #" not in n.value]
    assert not en_dur, (
        "refus d'admin d'ORG écrit à la main :\n  " + "\n  ".join(en_dur)
        + "\n→ passer par `_refus_org_admin(org_id)`.\n"
        "(Les refus des AUTRES paliers — plateforme, tenant, équipe — sont des murs "
        "distincts et gardent leur phrase : c'est le même mur dit deux fois qui trompe, "
        "pas deux murs différents.)")


def test_tous_les_sites_passent_par_la_source_unique():
    """Le compte des appels : si un site se remet à formuler le sien, il ne descend
    pas ici — c'est le compte qui le dit."""
    src = inspect.getsource(_authz)
    assert src.count("raise _refus_org_admin(org_id)") >= 4, (
        "un des quatre sites de refus ne passe plus par la source unique")
