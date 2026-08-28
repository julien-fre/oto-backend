"""Édition PARTIELLE d'une page markdown (oto/#6 top5 #3).

`op=update` remplaçait tout le corps → deux auteurs qui touchent des sections
DIFFÉRENTES s'écrasaient. `patch_section` cible UNE section par son titre (heading
markdown) et n'y touche que là : replace / append / prepend / delete. Fonctions PURES
(pas d'I/O) → le caller relit le doc, applique le patch, réécrit via `update_doc` (qui
garde révisions + backlinks + conflit optimiste).

**Deux manques d'adressage comblés le 2026-08-28** (signaux #481, #492, #507 et #583,
tous du même client, sur son ingestion quotidienne). Même cause : le titre markdown
était la SEULE poignée.

1. **Le préambule** — ce qui précède le premier titre — n'appartient à aucune section et
   était donc hors d'atteinte. Or chaque page de cette base ouvre sur un bandeau de
   provenance daté : rafraîchir une date coûtait la réécriture de 128 000 caractères, si
   bien que les pages les plus longues portaient les bandeaux les plus périmés. D'où
   `preamble()` / `patch_preamble()`.
2. **Un titre ne pouvait pas se retirer lui-même** : `replace` garde le titre, donc purger
   l'entrée J-14 d'un journal glissant laissait un titre orphelin. D'où `mode='delete'`.

⚠️ **Le préambule n'est PAS désigné par un nom de section.** Les signaux proposaient
`section='__preamble__'` ; refusé. Un mot réservé DANS `section` est un mot que rien
n'empêche une page d'écrire en titre — `## __preamble__` est un titre markdown valide —
et le jour où elle le fait, la même chaîne désigne deux choses. Le préambule se désigne
donc sur un AUTRE AXE (`region=`), donc dans un autre espace de noms : la collision
devient impossible par CONSTRUCTION, pas par improbabilité. Un `section` qui ne résout
pas est refusé en nommant les sections disponibles, jamais rattrapé en silence.

⚠️ **La portée d'une section ne change pas** : elle court jusqu'au prochain titre de
niveau ≤, ses sous-sections en font partie (sémantique tranchée au signal #334, gardée
et rendue explicite). `delete` a exactement la même portée que `replace`.
"""
from __future__ import annotations

import re

_HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")

#: La seule RÉGION sans titre adressable aujourd'hui. Un axe ouvert : le jour où il faut
#: viser un autre bloc anonyme (front-matter YAML, pied de page), il s'ajoute ici sans
#: toucher à l'espace de noms des titres.
REGION_PREAMBLE = "preamble"
REGIONS = (REGION_PREAMBLE,)

#: `delete` est un MODE et non une op : il partage l'adressage, le verrou optimiste et
#: l'accusé avec les trois autres. (Et `op=delete` existe déjà — il supprime LA PAGE.)
MODES = ("replace", "append", "prepend", "delete")


class SectionNotFound(Exception):
    """Le titre visé n'existe pas dans le corps (le caller renvoie une erreur
    actionnable listant les sections disponibles)."""
    def __init__(self, heading: str, available: list[str]):
        self.heading = heading
        self.available = available
        super().__init__(f"section introuvable: {heading!r}")


class HeadingInPreamble(Exception):
    """Le corps proposé pour le préambule contient un titre markdown.

    Refusé, pas absorbé : un titre écrit là devient le PREMIER titre de la page, donc la
    région « avant le premier titre » se rétrécit toute seule et le patch du lendemain
    n'atteint plus le bandeau. C'est le pendant, côté région, de l'absorption du titre
    propre d'une section (signal #328) : la même famille de défaut — un patch qui rend la
    page impatchable — mais ici rien ne peut être deviné, donc on refuse en nommant."""
    def __init__(self, found: list[str]):
        self.found = found
        super().__init__(f"titre(s) dans le préambule: {found!r}")


class PreambleAbsent(Exception):
    """`mode='delete'` sur une page dont le premier titre ouvre le corps : il n'y a rien
    à retirer. Un no-op qui rend « ok » est un mensonge — on refuse."""


class PreambleIsWholePage(Exception):
    """`mode='delete'` sur une page SANS AUCUN titre : « ce qui précède le premier
    titre » est alors la page entière, et la supprimer la viderait. Deux lectures
    défendables + un geste destructeur = forme ambiguë, donc refus nommé."""


def _norm(s: str) -> str:
    """Normalise un titre pour comparaison : sans `#`, sans casse, espaces réduits."""
    return re.sub(r"\s+", " ", s.lstrip("#").strip()).lower()


def headings(body: str) -> list[str]:
    """Titres de section (texte brut, sans `#`) présents dans le corps."""
    out: list[str] = []
    for line in (body or "").split("\n"):
        m = _HEADING.match(line)
        if m:
            out.append(m.group(2).strip())
    return out


def _first_heading_index(lines: list[str]) -> int:
    """Index de la 1re ligne de titre, ou `len(lines)` si la page n'en a aucun.

    C'est LA frontière du préambule : tout ce qui est avant n'appartient à aucune
    section, et c'est précisément ce que `section=` ne pouvait pas atteindre."""
    for i, line in enumerate(lines):
        if _HEADING.match(line):
            return i
    return len(lines)


def preamble(body: str) -> str:
    """Ce qui précède le premier titre — le bandeau de provenance des pages du client.
    Chaîne vide si la page ouvre sur son titre."""
    lines = (body or "").split("\n")
    return "\n".join(lines[:_first_heading_index(lines)])


def _strip_own_heading(new_body: str, target: str) -> str:
    """Retire de `new_body` un titre de tête identique à la section visée.

    Le titre est CONSERVÉ par le serveur (il vit dans `head`) : un `body_md` qui le
    reprend produisait deux sections homonymes — et le doc devenait irréparable par
    patch, puisque le patch suivant ciblait la PREMIÈRE (vide) des deux (signal #328).
    On absorbe plutôt que de refuser : « le corps d'une section ne redéclare pas son
    propre titre » est une convention qu'un agent ne peut pas deviner."""
    lines = (new_body or "").split("\n")
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i >= len(lines):
        return new_body
    m = _HEADING.match(lines[i])
    if not m or _norm(m.group(2)) != target:
        return new_body
    rest = lines[i + 1:]
    while rest and not rest[0].strip():
        rest.pop(0)
    return "\n".join(rest)


def _locate(lines: list[str], target: str) -> tuple[int, int, int] | None:
    """`(index du titre, niveau, fin exclusive)` de la section `target` (déjà normalisé).

    Fin de section = prochain titre de niveau ≤ (une sous-section reste DEDANS).
    `None` si le titre n'existe pas."""
    for i, line in enumerate(lines):
        m = _HEADING.match(line)
        if not m or _norm(m.group(2)) != target:
            continue
        level = len(m.group(1))
        j = i + 1
        while j < len(lines):
            m2 = _HEADING.match(lines[j])
            if m2 and len(m2.group(1)) <= level:
                break
            j += 1
        return i, level, j
    return None


def subsections(body: str, heading: str) -> list[str]:
    """Titres des SOUS-sections imbriquées dans `heading` (niveau strictement >).

    Une section court jusqu'au prochain titre de niveau ≤ : ses sous-sections en font
    partie, donc `mode='replace'` — et `mode='delete'` — les emportent AVEC le reste.
    Tant que c'était silencieux, patcher un parent écrasait le travail d'un autre auteur
    sur l'enfant — le contraire de ce que le patch promet (signal #334). Le caller
    annonce ce qu'il retire. Liste vide si la section est introuvable ou sans enfant."""
    lines = (body or "").split("\n")
    found = _locate(lines, _norm(heading))
    if not found:
        return []
    i, _level, j = found
    return [m.group(2).strip()
            for m in (_HEADING.match(ln) for ln in lines[i + 1:j]) if m]


def _check_mode(mode: str, new_body: str | None) -> None:
    """Un mode inconnu, ou un contenu passé à `delete`, s'arrête ICI.

    `delete` ne prend pas de corps : l'accepter-et-l'ignorer est exactement la famille
    de défauts que ce dépôt refuse (leçon #461 — un argument avalé coûte ce qu'il
    prétendait économiser, et rien ne le signale à l'appelant)."""
    if mode not in MODES:
        raise ValueError(f"mode invalide: {mode}")
    if mode == "delete":
        if new_body is not None:
            raise ValueError("mode='delete' ne prend pas de contenu (new_body)")
    elif new_body is None:
        raise ValueError(f"mode={mode!r} exige un contenu (new_body)")


def _recolle(garde: list[str], tail: list[str]) -> str:
    """Recolle un corps dont une région a été RETIRÉE (`mode='delete'`).

    Sans ça, les lignes vides qui encadraient la région s'empilent à chaque suppression
    et la page se creuse de blancs au fil des purges quotidiennes. On retire les vides
    de queue, puis on repose UNE ligne — séparateur avant la suite, ou simple fin de
    fichier quand la section supprimée était la dernière."""
    garde = list(garde)
    while garde and not garde[-1].strip():
        garde.pop()
    if garde:
        garde.append("")
    return "\n".join(garde + tail)


def patch_section(body: str, heading: str, new_body: str | None = None,
                  mode: str = "replace") -> str:
    """Retourne le corps COMPLET avec la section `heading` modifiée.

    `mode` : 'replace' (remplace le contenu SOUS le titre, garde le titre) /
    'append' (ajoute à la fin de la section) / 'prepend' (insère juste après le titre) /
    'delete' (retire la section ET son titre — sans `new_body`).
    La section court du titre jusqu'au PROCHAIN titre de niveau ≤ (ou la fin) : ses
    SOUS-sections en font partie et sont donc remplacées — ou supprimées — elles aussi ;
    `subsections()` dit lesquelles, pour que le caller l'annonce.
    `new_body` = le CORPS de la section : s'il rouvre lui-même le titre visé, ce titre
    de tête est absorbé (jamais dupliqué).
    Lève `SectionNotFound` si le titre n'existe pas."""
    _check_mode(mode, new_body)
    lines = (body or "").split("\n")
    target = _norm(heading)
    found = _locate(lines, target)
    if found is None:
        raise SectionNotFound(heading, headings(body))
    i, _level, j = found
    head, inner, tail = lines[:i + 1], lines[i + 1:j], lines[j:]
    if mode == "delete":
        # `head` porte la ligne de titre : on la laisse tomber avec le reste — c'est
        # tout l'objet du mode (signal #583 : `replace` gardait un titre orphelin).
        return _recolle(lines[:i], tail)
    new_lines = _strip_own_heading(new_body or "", target).split("\n")
    if mode == "replace":
        # Une ligne vide encadre proprement le nouveau contenu sous le titre.
        section = [""] + new_lines + ([""] if tail else [])
    elif mode == "append":
        # Retire les vides de fin de section avant d'ajouter.
        while inner and not inner[-1].strip():
            inner.pop()
        section = inner + [""] + new_lines + ([""] if tail else [])
    else:  # prepend
        section = [""] + new_lines + [""] + inner
    return "\n".join(head + section + tail)


def patch_preamble(body: str, new_body: str | None = None,
                   mode: str = "replace") -> str:
    """Retourne le corps COMPLET avec le PRÉAMBULE modifié — ce qui précède le premier
    titre, donc ce qui n'appartient à aucune section (signaux #481, #492, #507).

    Mêmes modes que `patch_section`. Sur une page qui n'a pas encore de préambule,
    replace/append/prepend en CRÉENT un (poser un bandeau la première fois) ; `delete`
    refuse, parce qu'un no-op annoncé « ok » est un mensonge.

    Lève `HeadingInPreamble` si le contenu proposé porte un titre (il refermerait la
    région), `PreambleAbsent` s'il n'y a rien à supprimer, `PreambleIsWholePage` si la
    page n'a aucun titre — supprimer viderait tout, et « le préambule » y a deux
    lectures défendables : on refuse plutôt que de deviner."""
    _check_mode(mode, new_body)
    lines = (body or "").split("\n")
    k = _first_heading_index(lines)
    inner, tail = lines[:k], lines[k:]
    if mode == "delete":
        if not tail:
            raise PreambleIsWholePage()
        if not "\n".join(inner).strip():
            raise PreambleAbsent()
        return _recolle([], tail)
    dedans = headings(new_body or "")
    if dedans:
        raise HeadingInPreamble(dedans)
    new_lines = (new_body or "").split("\n")
    # Pas de ligne de titre au-dessus : le préambule OUVRE le document, donc pas de `[""]`
    # de tête — sinon la page commencerait par une ligne vide, à chaque rafraîchissement.
    if mode == "replace":
        region = new_lines + ([""] if tail else [])
    elif mode == "append":
        while inner and not inner[-1].strip():
            inner.pop()
        vide = not "\n".join(inner).strip()
        region = ([] if vide else inner + [""]) + new_lines + ([""] if tail else [])
    else:  # prepend
        vide = not "\n".join(inner).strip()
        region = new_lines + ([] if vide else [""] + inner) + ([""] if vide and tail else [])
    return "\n".join(region + tail)
