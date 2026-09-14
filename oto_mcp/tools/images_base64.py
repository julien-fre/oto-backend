"""Les images incluses en base64 (`data:image/…;base64,…`), retirées d'un contenu servi.

Mesuré le 14/09/2026 sur deux pages d'accueil lues par des agents hébergés : 227 106
caractères dont 91 % d'images en base64 (4 images), et 66 644 dont 35 % (19 images). Un
agent ne lit pas une image encodée : il la paie à chaque tour qui relit la page. Trois
travaux ont franchi leur borne de 150 000 jetons sur la même ligne pour cette raison
(oto#246).

Ce qui est retiré : l'ENCODAGE, et lui seul. Une trace le remplace, qui dit le type et la
taille retirée, pour que l'agent sache qu'il y avait une image. Le texte de la page, ses
liens et ses URL d'images ordinaires ne bougent pas. Seules les `data:image/` sont visées :
une autre charge en base64 peut porter une adresse obfusquée (`mail_obfuscation`).
"""
from __future__ import annotations

import re

#: Une URI de donnée d'IMAGE en base64, d'au moins 64 caractères d'encodage : en dessous,
#: la trace serait plus longue que ce qu'elle remplace.
_IMAGE_BASE64 = re.compile(
    r"data:(image/[A-Za-z0-9.+-]+)((?:;[A-Za-z0-9=.+-]+)*);base64,([A-Za-z0-9+/=]{64,})")


def retirer(texte: str) -> tuple[str, int, int]:
    """`(texte sans ses images base64, nombre retiré, caractères retirés)`."""
    compte = [0, 0]

    def _trace(m: re.Match) -> str:
        compte[0] += 1
        compte[1] += len(m.group(0))
        return f"data:{m.group(1)} — {len(m.group(3))} caractères de base64 retirés"

    return _IMAGE_BASE64.sub(_trace, texte), compte[0], compte[1]


def alleger(res: dict) -> dict:
    """Retire les images base64 des représentations d'un scrape (`markdown`, `text`) et
    le DIT dans `images_base64_retirees` quand il y en avait. Modifie et rend `res`."""
    nombre = caracteres = 0
    for cle in ("markdown", "text"):
        if isinstance(res.get(cle), str):
            res[cle], n, c = retirer(res[cle])
            nombre += n
            caracteres += c
    if nombre:
        res["images_base64_retirees"] = {"nombre": nombre, "caracteres": caracteres}
    return res
