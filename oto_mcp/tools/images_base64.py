"""Images embedded as base64 (`data:image/…;base64,…`), removed from served content.

Measured on 14/09/2026 on two home pages read by hosted agents: 227,106
characters of which 91% base64 images (4 images), and 66,644 of which 35% (19 images). An
agent does not read an encoded image: it pays for it on every turn that re-reads the page. Three
jobs crossed their 150,000-token limit on the same line for this reason
(oto#246).

What is removed: the ENCODING, and only it. A trace replaces it, stating the type and the
size removed, so the agent knows there was an image. The page's text,
links and ordinary image URLs do not change. Only `data:image/` is targeted:
another base64 payload may carry an obfuscated address (`mail_obfuscation`).
"""
from __future__ import annotations

import re

#: A base64 IMAGE data URI, with at least 64 characters of encoding: below that,
#: the trace would be longer than what it replaces.
_IMAGE_BASE64 = re.compile(
    r"data:(image/[A-Za-z0-9.+-]+)((?:;[A-Za-z0-9=.+-]+)*);base64,([A-Za-z0-9+/=]{64,})")


def retirer(texte: str) -> tuple[str, int, int]:
    """`(text without its base64 images, number removed, characters removed)`."""
    compte = [0, 0]

    def _trace(m: re.Match) -> str:
        compte[0] += 1
        compte[1] += len(m.group(0))
        return f"data:{m.group(1)} — {len(m.group(3))} characters of base64 removed"

    return _IMAGE_BASE64.sub(_trace, texte), compte[0], compte[1]


def alleger(res: dict) -> dict:
    """Removes base64 images from a scrape's representations (`markdown`, `text`) and
    SAYS SO in `images_base64_retirees` when there were any. Mutates and returns `res`."""
    nombre = caracteres = 0
    for cle in ("markdown", "text"):
        if isinstance(res.get(cle), str):
            res[cle], n, c = retirer(res[cle])
            nombre += n
            caracteres += c
    if nombre:
        res["images_base64_retirees"] = {"nombre": nombre, "caracteres": caracteres}
    return res
