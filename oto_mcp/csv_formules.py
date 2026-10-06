"""Une cellule CSV qu'un tableur ouvrirait en formule.

Excel et LibreOffice lisent comme le début d'une formule une cellule qui commence
par `=`, `+`, `-`, `@`, une tabulation ou un retour chariot (OWASP, « CSV
injection »). Un export qu'on ouvre dans un tableur recopie des valeurs écrites
par des tiers : le texte est donc préfixé d'une apostrophe, que le tableur masque.
Un nombre n'est jamais touché : `-5` reste un nombre négatif.

La lecture (`csv_tolerant`) retire ce préfixe : un export réimporté tel quel rend
les valeurs d'origine, pas `'+33…`.
"""
from __future__ import annotations

_FORMULE = ("=", "+", "-", "@", "\t", "\r")


def neutraliser(texte: str) -> str:
    """Le texte d'une cellule, préfixé d'une apostrophe s'il s'ouvrirait en formule."""
    return "'" + texte if texte.startswith(_FORMULE) else texte


def cellule(value):
    """Une valeur de cellule : un texte est neutralisé, toute autre valeur rendue telle quelle."""
    return neutraliser(value) if isinstance(value, str) else value


def relire(texte: str) -> str:
    """L'inverse de `neutraliser` à l'import : `'=…` redevient `=…`. Une apostrophe
    devant tout autre caractère est une donnée, laissée telle quelle."""
    if texte.startswith("'") and texte[1:].startswith(_FORMULE):
        return texte[1:]
    return texte
