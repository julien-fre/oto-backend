"""Cliquet : le paquet `access/` ne connaît pas le commerce (ADR 0070 §7).

Le cœur relit les droits déclarés d'une org (`org_entitlements`) ; le commerce les
écrit (`billing_droits`). Jusqu'au lot 2 de #806, `access.quotas` importait `billing`
pour demander « quel est ton forfait, quelles options donne-t-il ? » — donc le cœur
savait qu'un forfait existe, et une instance sans notre commerce livrait du code
jamais emprunté.

Ce banc refuse tout retour : aucun import d'un module `billing*` (même tardif, dans
une fonction), et aucune lecture de l'état d'abonnement par le store.
"""
from __future__ import annotations

import ast
import pathlib

from oto_mcp import access

PKG = pathlib.Path(access.__file__).parent
# Les lectures de l'état de commerce que le cœur faisait et ne fait plus.
_LECTURES_DU_COMMERCE = ("subscription_plan_for_org", "active_subscription_plans",
                         "plan_options", "plan_is_unmetered")


def _imports(arbre: ast.AST) -> list[str]:
    """Tous les modules et noms importés, y compris dans le corps des fonctions."""
    vus: list[str] = []
    for n in ast.walk(arbre):
        if isinstance(n, ast.Import):
            vus += [a.name for a in n.names]
        elif isinstance(n, ast.ImportFrom):
            vus.append(n.module or "")
            vus += [a.name for a in n.names]
    return vus


def test_aucun_module_d_access_n_importe_le_commerce():
    fautifs = {}
    for f in sorted(PKG.glob("*.py")):
        noms = [n for n in _imports(ast.parse(f.read_text(encoding="utf-8")))
                if "billing" in n.split(".")[-1] or ".billing" in n]
        if noms:
            fautifs[f.name] = noms
    assert not fautifs, (
        f"`access/` importe le commerce : {fautifs}. Le cœur relit les droits "
        "déclarés (`entitlements.org_has`) ; c'est le commerce qui les écrit.")


def test_aucun_module_d_access_ne_lit_l_etat_d_abonnement():
    fautifs = {}
    for f in sorted(PKG.glob("*.py")):
        arbre = ast.parse(f.read_text(encoding="utf-8"))
        noms = {n.attr for n in ast.walk(arbre) if isinstance(n, ast.Attribute)}
        noms |= {n.id for n in ast.walk(arbre) if isinstance(n, ast.Name)}
        lus = sorted(noms & set(_LECTURES_DU_COMMERCE))
        if lus:
            fautifs[f.name] = lus
    assert not fautifs, f"`access/` lit l'état du commerce : {fautifs}"


def test_le_cliquet_voit_bien_un_import_tardif():
    """Un import dans une fonction est celui qui s'était glissé : le banc doit le voir."""
    src = "def f():\n    from .. import billing\n    return billing\n"
    assert "billing" in _imports(ast.parse(src))
