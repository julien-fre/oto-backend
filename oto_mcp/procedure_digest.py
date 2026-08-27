"""Le DIGEST d'auto-amélioration — le bloc d'ouverture obligatoire d'une procédure.

Une procédure s'ouvre sur ce qu'elle a appris de son dernier déroulé et sur ce qui a
été corrigé, daté :

    > **Self-improvement digest** — <ce que le dernier run a appris et ce qui a été
    > corrigé, daté>.

Pourquoi une garde plutôt qu'une convention : le bloc n'a de valeur que s'il est TENU.
Une procédure qui a tourné et dont le digest date d'il y a trois versions ment plus
qu'elle n'informe ; une procédure qui n'a jamais tourné doit le dire, en une phrase.
Ce module ne sait vérifier ni la fraîcheur ni la véracité — il vérifie la seule chose
qu'un serveur PEUT voir : **le bloc est-il là, et à sa place**.

⚠️ **La place, c'est « avant tout le reste, sauf un H1 de titre »**, et ça vient du
rendu, pas du goût : la page d'un process retire un titre de tête qui répète le nom de
la procédure (`stripLeadingTitleHeading`, tulina-app-front) et affiche le sien. Un digest
posé AU-DESSUS de ce H1 laisserait donc le titre orphelin au milieu de la page ; posé
en dessous, il est la première chose que le lecteur voit. Les corps qui n'ont pas ce H1
(ils ouvrent sur `## Goal`) le portent en tout premier.

⚠️ **Warning, jamais un refus** — même régime que `procedure_diagram` et `slots`
(ADR 0014/0035) : la procédure s'enregistre, l'auteur reçoit le signal. Un refus
casserait toute réécriture des procédures vivantes qui n'ont pas encore leur digest.
"""
from __future__ import annotations

import re

# Le marqueur, tel que la consigne l'écrit. La casse est libre (un auteur écrira
# « Self-Improvement »), le gras et le tiret ne sont pas exigés ici : ce module garde la
# PRÉSENCE et la PLACE, la forme exacte est affaire de relecture, pas de serveur.
MARKER = re.compile(r"self[-\s]?improvement\s+digest", re.I)

# Un titre de tête (H1) : le seul bloc qui a le droit de précéder le digest.
_H1 = re.compile(r"^[ \t]*#[ \t]+\S")
_QUOTE = re.compile(r"^[ \t]*>")

WARNING = ("no self-improvement digest — a procedure opens with "
           "`> **Self-improvement digest** — …`: what the last run taught and what was "
           "fixed, dated (one sentence if it has never been run)")


def _blocks(body_md: str) -> list[list[str]]:
    """Le corps découpé en blocs séparés par des lignes vides, blocs vides retirés."""
    out: list[list[str]] = []
    current: list[str] = []
    for line in (body_md or "").split("\n"):
        if line.strip():
            current.append(line)
        elif current:
            out.append(current)
            current = []
    if current:
        out.append(current)
    return out


def has_digest(body_md: str) -> bool:
    """Le digest est-il le bloc d'ouverture ? Un H1 de titre a le droit de le précéder
    (la page du process le retire au rendu) ; rien d'autre."""
    blocks = _blocks(body_md)
    if not blocks:
        return False
    first = blocks[0]
    # Un H1 SEUL en tête est sauté — un H1 suivi de prose dans le même bloc ne l'est pas.
    if len(first) == 1 and _H1.match(first[0]):
        blocks = blocks[1:]
        if not blocks:
            return False
        first = blocks[0]
    return bool(_QUOTE.match(first[0]) and MARKER.search("\n".join(first)))


def digest_check(body_md: str) -> dict:
    """Check croisé à l'écriture, dans la forme des autres (`diagram_check`,
    `slots_check`) : la clé est TOUJOURS présente, `None` = rien à signaler.
    Best-effort — un check ne casse jamais une écriture."""
    try:
        return {"digest_warning": None if has_digest(body_md) else WARNING}
    # noqa: SILENT — contrôle de forme optionnel : pas d'avertissement plutôt qu'un faux
    except Exception:  # noqa: BLE001 — cf. `slots_check`
        return {"digest_warning": None}
