"""Un banc minuscule pour éprouver le parcours d'atteignabilité (#792).

⚠️ **Ce module n'est pas du code de production et n'est monté nulle part.** Il existe
parce qu'on ne peut pas éprouver un garde-fou sur les capacités qu'il garde : elles
changent, et un banc qui s'appuie sur elles finit par mesurer leur évolution plutôt
que la sienne. Ici, les réponses sont connues d'avance et le resteront.

Il imite les trois formes réelles du dépôt :

- un refus **littéral** sur le chemin du handler ;
- un refus **relayé** — le code voyage dans une exception métier, ressort par un
  `AuthzDenied(400, e.code)` ;
- un refus **voisin**, levé par une fonction que le handler n'appelle pas : c'est le
  cas qui restait vert quand la question était « existe-t-il dans ce module ? ».
"""
from __future__ import annotations

from oto_mcp.capabilities._types import AuthzDenied


class RefusDeSaisie(Exception):
    """Une exception métier qui PORTE un code, comme celles du coffre."""

    def __init__(self, code: str, message: str = ""):
        super().__init__(code)
        self.code = code
        self.message = message


def garde():
    raise AuthzDenied(404, "introuvable", "rien à cette adresse")


def coffre():
    raise RefusDeSaisie("valeur_refusee", "hors du jeu attendu")


def voisine():
    """Jamais appelée par `handler` — c'est tout l'objet du banc."""
    raise AuthzDenied(403, "jamais_par_ce_chemin", "une AUTRE capacité")


def handler():
    garde()
    try:
        coffre()
    except RefusDeSaisie as e:
        raise AuthzDenied(400, getattr(e, "code", str(e)), "relayé tel quel")
