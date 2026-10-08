"""Les interrupteurs oui/non d'une instance, lus dans son environnement.

Une seule grammaire pour tous : `1`, `true`, `yes`, `on` ouvrent ; absente, vide, `0`,
`false`, `no`, `off` ferment ; toute autre valeur LÈVE — un `yes` mal orthographié
trancherait sinon en silence, et l'exploitation n'en saurait rien.

L'appelant lit la variable LUI-MÊME, par son nom littéral (`os.environ.get("…")`) :
l'inventaire (`env_inventory.py`) trouve les lectures d'environnement par l'AST, et
une lecture faite ici, par un nom passé en argument, lui échapperait.
"""
from __future__ import annotations

OUI = ("1", "true", "yes", "on")
NON = ("", "0", "false", "no", "off")


class InterrupteurIllisible(RuntimeError):
    """Une variable d'instance porte une valeur qui n'est ni un oui ni un non."""


def oui_non(nom: str, brut: str | None) -> bool:
    """La valeur `brut` de la variable `nom`, lue comme un interrupteur."""
    valeur = (brut or "").strip().lower()
    if valeur in OUI:
        return True
    if valeur in NON:
        return False
    raise InterrupteurIllisible(
        f"{nom}={valeur!r} n'est ni un oui ({', '.join(OUI)}) ni un non "
        f"(absente, vide, {', '.join(n for n in NON if n)}) : corrige la déclaration "
        "de l'instance.")
