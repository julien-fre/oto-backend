"""La césure conditionnelle (U+00AD), retirée du texte servi à un agent.

Certains CMS insèrent ce caractère pour couper les mots en fin de ligne. Invisible à
l'écran, il arrive au milieu des mots dans le texte que lit l'agent — y compris des
noms propres. Mesuré le 12/09/2026 sur une campagne d'enrichissement : 5 pages sur
266 le contenaient, et dans un cas l'agent a recopié un nom de personne avec une
lettre fautive à l'endroit de la coupure (oto#208).

C'est une consigne de mise en page, pas du contenu : la retirer ne perd rien.

⚠️ **Seul U+00AD est retiré, parce que seul lui a été constaté.** Les autres invisibles
de même nature (U+200B, U+2060, U+FEFF) n'ont pas été mesurés sur ces pages ; les
ajouter par précaution retirerait peut-être un caractère qui porte un sens ailleurs.
Ils entreront ici sur mesure, pas avant.
"""
from __future__ import annotations

CESURE = "­"


def retirer(texte: str) -> str:
    """Le texte sans ses césures conditionnelles."""
    return texte.replace(CESURE, "") if CESURE in texte else texte


def retirer_des_representations(res: dict) -> dict:
    """Retire les césures du `markdown` et du `text` d'un scrape. Modifie et rend
    `res` — le `html` brut, s'il y en a un, n'est pas touché : qui veut la page telle
    quelle l'a déjà."""
    for cle in ("markdown", "text"):
        if isinstance(res.get(cle), str):
            res[cle] = retirer(res[cle])
    return res
