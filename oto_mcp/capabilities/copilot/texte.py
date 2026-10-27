"""Text plumbing of the loop: a served tool result as text for the model, the announced
cut, the `<next>…</next>` chips taken out of the streamed answer, and the final answer
as plain prose for the voice."""
from __future__ import annotations

import json
import re

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


#: The spoken answer is cut here, at the last sentence end before it.
PLAFOND_PAROLE = 1500
_SUITE = re.compile(r"<next>.*?(?:</next>|$)", re.S)
_BLOC_CODE = re.compile(r"```.*?(?:```|$)", re.S)
_IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_LIEN = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_URL = re.compile(r"<?https?://\S+>?")
_CODE = re.compile(r"`([^`]*)`")
_TITRE = re.compile(r"^\s{0,3}#{1,6}\s*", re.M)
_CITATION = re.compile(r"^\s{0,3}>\s?", re.M)
_PUCE = re.compile(r"^\s*(?:[-*+•]|\d+[.)])\s+", re.M)
_REGLE = re.compile(r"^\s*(?:[-*_]\s*){3,}$", re.M)
_TABLEAU = re.compile(r"^\s*\|?\s*:?-{2,}.*$", re.M)
_EMPHASE = re.compile(r"(\*\*|__|\*|_|~~)(?=\S)(.+?)(?<=\S)\1")
_FIN_DE_PHRASE = re.compile(r"[.!?…](?:[\"'»)\]]*)(?=\s|$)")


def texte_a_dire(contenu) -> str:
    """The final answer as plain prose for the voice: the assistant message's text
    blocks, without the `<next>` chips, code, links' targets, bare URLs, nor markdown
    marks; at most `PLAFOND_PAROLE` characters, cut at the last sentence end before
    (else at a word). '' when nothing is left to say."""
    if isinstance(contenu, str):
        brut = contenu
    else:
        brut = "\n".join(b.get("text") or "" for b in contenu or []
                         if isinstance(b, dict) and b.get("type") == "text")
    t = _SUITE.sub(" ", brut)
    t = _BLOC_CODE.sub(" ", t)
    t = _IMAGE.sub(r"\1", t)
    t = _LIEN.sub(r"\1", t)
    t = _URL.sub(" ", t)
    t = _CODE.sub(r"\1", t)
    t = _REGLE.sub(" ", t)
    t = _TABLEAU.sub(" ", t)
    t = _TITRE.sub("", t)
    t = _CITATION.sub("", t)
    t = _PUCE.sub("", t)
    for _ in range(2):
        t = _EMPHASE.sub(r"\2", t)
    t = t.replace("|", " ")
    # A line that ends without punctuation (a title, a list item) still reads as a pause.
    lignes = [l.strip() for l in t.splitlines() if l.strip()]
    t = " ".join(l if re.search(r"[.!?:;…,]$", l) else f"{l}." for l in lignes)
    t = re.sub(r"\s+", " ", t).strip()
    if len(t) <= PLAFOND_PAROLE:
        return t
    tete = t[:PLAFOND_PAROLE]
    fins = [m.end() for m in _FIN_DE_PHRASE.finditer(tete)]
    if fins and fins[-1] >= PLAFOND_PAROLE // 3:
        return tete[:fins[-1]].strip()
    return tete.rsplit(" ", 1)[0].strip()
