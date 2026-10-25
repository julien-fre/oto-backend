"""Text plumbing of the loop: a served tool result as text for the model, the announced
cut, and the `<next>…</next>` chips taken out of the streamed answer."""
from __future__ import annotations

import json

#: A tool result is cut here, and the model is TOLD so — never a silent fragment.
PLAFOND_RESULTAT = 80_000
_OUVRANT = "<next>"


def texte_du_resultat(result) -> str:
    """A served result (`ToolResult`, or anything JSON-able) as the text the model reads:
    its text contents, else its structured content."""
    contenu = getattr(result, "content", None)
    if isinstance(contenu, list):
        parts = [getattr(c, "text", None) for c in contenu]
        parts = [p for p in parts if isinstance(p, str)]
        if parts:
            return "\n".join(parts)
        structure = getattr(result, "structured_content", None)
        if structure is not None:
            return json.dumps(structure, ensure_ascii=False, default=str)
        return ""
    if isinstance(result, str):
        return result
    return json.dumps(result, ensure_ascii=False, default=str)


def couper(texte: str) -> str:
    """At most `PLAFOND_RESULTAT` characters, and the cut said in words to the model."""
    if len(texte) <= PLAFOND_RESULTAT:
        return texte or "(empty)"
    reste = len(texte) - PLAFOND_RESULTAT
    return (f"{texte[:PLAFOND_RESULTAT]}\n[cut: {reste} more characters — narrow the call "
            "(filter, fields, limit) to see the rest]")


class FiltreSuite:
    """The streamed answer minus a trailing `<next>a | b | c</next>` block, which becomes
    chips. What might be the start of the tag is held back until it can't be; nothing of
    the block ever reaches the screen."""

    def __init__(self, sortie):
        self._sortie = sortie
        self._retenu = ""
        self._dedans = False
        self._capte = ""

    def pousser(self, texte: str) -> None:
        if self._dedans:
            self._capte += texte
            return
        self._retenu += texte
        at = self._retenu.find(_OUVRANT)
        if at >= 0:
            if at > 0:
                self._sortie(self._retenu[:at])
            self._capte = self._retenu[at + len(_OUVRANT):]
            self._retenu = ""
            self._dedans = True
            return
        garde = 0
        for k in range(min(len(_OUVRANT) - 1, len(self._retenu)), 0, -1):
            if _OUVRANT.startswith(self._retenu[-k:]):
                garde = k
                break
        if len(self._retenu) > garde:
            self._sortie(self._retenu[:len(self._retenu) - garde])
            self._retenu = self._retenu[len(self._retenu) - garde:]

    def finir(self) -> list[str]:
        """Flushes what was held and returns up to three suggestions."""
        if not self._dedans:
            if self._retenu:
                self._sortie(self._retenu)
                self._retenu = ""
            return []
        corps = self._capte.split("</next>", 1)[0]
        items = []
        for morceau in corps.replace("\n", "|").split("|"):
            m = morceau.strip().lstrip("-* ").strip()
            if m:
                items.append(m)
        return items[:3]
