"""The soft hyphen (U+00AD), removed from text served to an agent.

Some CMSs insert this character to break words at the end of a line. Invisible on
screen, it ends up in the middle of words in the text the agent reads — including
proper names. Measured on 12/09/2026 on an enrichment campaign: 5 pages out of
266 contained it, and in one case the agent copied a person's name with a
wrong letter at the break point (oto#208).

It is a layout instruction, not content: removing it loses nothing.

⚠️ **Only U+00AD is removed, because only it was observed.** The other invisibles
of the same nature (U+200B, U+2060, U+FEFF) were not measured on these pages; adding
them as a precaution might remove a character that carries meaning elsewhere.
They will come in here when measured, not before.
"""
from __future__ import annotations

CESURE = "­"


def retirer(texte: str) -> str:
    """The text without its soft hyphens."""
    return texte.replace(CESURE, "") if CESURE in texte else texte


def retirer_des_representations(res: dict) -> dict:
    """Remove the soft hyphens from a scrape's `markdown` and `text`. Modifies and returns
    `res` — the raw `html`, if there is one, is untouched: whoever wants the page as
    is already has it."""
    for cle in ("markdown", "text"):
        if isinstance(res.get(cle), str):
            res[cle] = retirer(res[cle])
    return res
