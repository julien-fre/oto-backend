"""Le DESSIN d'une procédure, quand elle en a un — FACULTATIF.

⚠️ **Le dessin n'est plus exigé** (Alexis, 18/09/2026). Il l'avait été le 23/08/2026
(b34af1cc) pour un besoin d'affichage d'un front partenaire, où il était la vue par
défaut de la page du process — une règle de rendu d'UN consommateur devenue règle du
cœur, servie à tous les agents. Une procédure se lit désormais en prose ; une
procédure sans dessin n'appelle plus aucun avertissement. Ce qui reste ici ne sert
qu'aux procédures qui en ont UN : le servir sans le tracé (le marqueur), le remettre à
l'écriture, et dire quand il ne se dessinera pas ou qu'il y en a deux.

Côté front, un dessin est UN bloc de code non tagué (``` sans langage) tracé en
caractères semi-graphiques, reparsé en graphe et redessiné en cartes ; un dessin hors
grammaire est refusé par le parseur, qui retombe sur les caractères bruts.

Ce module ne reparse rien : la grammaire complète vit dans `src/lib/ascii-diagram.ts`
côté front, et la redoubler ici fabriquerait deux vérités qui divergeraient au premier
changement de rendu. On garde le seul test qui ne peut pas mentir dans les deux sens :
**« l'auteur a-t-il dessiné quelque chose ? »**, exactement le test `isDrawing` du front
(≥ 3 lignes portant un glyphe, ≥ 20 glyphes au total) — un seuil délibérément exigeant,
pour qu'un échantillon shell avec une flèche égarée ne passe pas pour un dessin.

⚠️ **Warning, jamais un refus** : ADR 0014/0035 — les checks croisés d'une écriture de
procédure signalent la dérive, ils ne la bloquent pas.

⚠️ **Seuls les blocs NON TAGUÉS comptent** : c'est le routeur du front qui en décide
(`process-code-block.tsx`) — un ```text plein de caractères de tracé ne sera jamais
dessiné, donc le compter comme un dessin serait un faux positif silencieux, la classe
de bug que ce module existe pour fermer.
"""
from __future__ import annotations

import re

# L'alphabet du dessin, tel que la spec le nomme : les caractères de tracé de boîtes
# (U+2500–U+257F) plus les trois marqueurs de flux. Sous-ensemble strict de la classe
# du front (`DRAWING_GLYPH` dans `drawing.ts`, qui couvre aussi blocs et flèches) :
# ce qui passe ici passe donc là-bas, jamais l'inverse.
GLYPHS = re.compile(r"[─-╿▼▶▪]")

# Les deux seuils du front, à l'identique. Les changer ici sans les changer là-bas
# rendrait le warning menteur dans un sens ou dans l'autre.
MIN_GLYPH_LINES = 3
MIN_GLYPHS = 20

# Un bloc fencé : la ligne d'ouverture avec son langage optionnel, puis tout jusqu'à
# la fermeture — cherchée en début de ligne, pour qu'un ``` dans la prose ne close pas
# un bloc trop tôt. Même forme que `FENCE` dans `drawing.ts`.
_FENCE = re.compile(r"^[ \t]*```[ \t]*([\w-]*)[^\n]*\n(.*?)^[ \t]*```[ \t]*$", re.M | re.S)

# Le marqueur est arrivé, mais il n'y avait pas de dessin à remettre à cet endroit : la
# nouvelle version en est privée. Le dessin étant facultatif, l'absence seule ne dit
# rien — c'est la PERTE qu'on signale, parce qu'elle n'est presque jamais voulue.
PERDU = ("the `<!-- flowchart: … -->` line was dropped: there is no stored drawing at "
         "this slug and scope to put back (op=create, a new slug, or an omitted scope = "
         "user). The drawing still lives in the version you read — write there, or pass "
         "the drawing itself")

# Deux blocs qui dessinent : la page n'en rend qu'UN, le premier. Le cas se fabrique
# quand un corps arrive avec le marqueur ET un vrai dessin — `avec_le_dessin` remet
# alors le tracé stocké à côté du neuf, et `has_diagram` seul répondait « oui, il y a
# un dessin », donc rien n'était dit.
DOUBLE = ("two drawings in this body — the process page renders the FIRST one only. "
          "If you drew a new flowchart, DROP the `<!-- flowchart: … -->` marker line: "
          "kept, it put the stored drawing back next to yours")


def is_drawing(block: str) -> bool:
    """Ce bloc est-il un dessin plutôt qu'un échantillon de code ? Port du `isDrawing`
    du front : assez de glyphes, sur assez de lignes, pour que ce soit une structure et
    pas de la ponctuation."""
    lines_with = 0
    total = 0
    for line in block.split("\n"):
        found = len(GLYPHS.findall(line))
        if found:
            lines_with += 1
        total += found
    return lines_with >= MIN_GLYPH_LINES and total >= MIN_GLYPHS


def compter_les_dessins(body_md: str) -> int:
    """Combien de blocs NON TAGUÉS dessinent dans ce corps. La page n'en rend qu'un —
    le premier — donc au-delà de 1, un tracé est écrit et jamais montré."""
    return sum(1 for lang, block in _FENCE.findall(body_md or "")
               if not lang and is_drawing(block))


def has_diagram(body_md: str) -> bool:
    """Le corps porte-t-il un dessin que la page du process saura rendre ?"""
    return compter_les_dessins(body_md) > 0


def trouver_le_dessin(body_md: str) -> tuple[int, int] | None:
    """L'étendue `(début, fin)` du PREMIER bloc fencé non tagué qui dessine — fences
    comprises — ou `None`. Le même bloc que `has_diagram` compte, au même critère."""
    for m in _FENCE.finditer(body_md or ""):
        if not m.group(1) and is_drawing(m.group(2)):
            return m.span()
    return None


# ── Le dessin servi à l'agent : un marqueur, pas le tracé ─────────────────────
#
# Le dessin est la vue par défaut de la PAGE — un humain le regarde. L'agent qui
# déroule la procédure lit les étapes, qui disent le même flux en prose ; le tracé lui
# coûte ~1 000 jetons par lecture (mesuré le 10/09/2026 : 3 304 caractères = 983 jetons
# Haiku, et une procédure est lue à chaque run) et ne lui apprend rien qu'il n'ait
# déjà. Sur la face MCP, `op=get` remplace donc le bloc par UNE ligne.
#
# ⚠️ Cette ligne est un MARQUEUR, pas un commentaire : l'agent qui édite relit puis
# réécrit (`op=get` → `op=set`), et sans elle chaque édition d'agent ferait DISPARAÎTRE
# le dessin — la page se rendrait vide. À l'écriture, `avec_le_dessin` remet à sa place
# le tracé de la version courante. Un corps qui arrive avec un VRAI dessin le garde ;
# un corps sans marqueur ni dessin s'écrit tel quel — le dessin est facultatif.
# Les corps STOCKÉS ne portent jamais le marqueur — il ne vit qu'entre les deux appels.
#
# ⚠️ Ce que le marqueur NE promet pas : `avec_le_dessin` relit le corps COURANT de la
# ligne visée par l'écriture. Une écriture qui vise ailleurs n'a rien à relire, et le
# marqueur s'efface : `op=create`, un slug neuf, ou un `scope` omis après avoir lu
# l'org (le défaut d'écriture est `user`, ADR 0068). C'est pour ça que la ligne dit
# désormais « same slug and scope » plutôt que « op=set keeps the drawing ».
# ⚠️ Et marqueur + VRAI dessin dans le même corps donne DEUX blocs dessinants : la
# page n'en rend qu'un, le premier. `has_diagram` répondait « oui » et se taisait ;
# `compter_les_dessins` le compte, et `diagram_check` le DIT (`DOUBLE`).
_MARQUEUR = re.compile(r"^[ \t]*<!--[ \t]*flowchart:[^\n]*?-->[ \t]*$", re.M)


def marqueur(version, lignes: int) -> str:
    """La ligne qui remplace le tracé — et qui dit à QUELLES conditions il revient.

    ⚠️ Elle a d'abord promis « keep this line and op=set keeps the drawing », sans
    condition. C'était faux dans quatre cas mesurés le 10/09/2026, tous des écritures
    qui ne visent pas la ligne d'où le marqueur vient : `op=create` (rien à relire),
    un slug neuf, un `scope` omis (le défaut d'écriture est `user` — on relit l'org et
    on écrit chez soi, ADR 0068), et le marqueur simplement non recopié. Le dessin est
    alors perdu : la nouvelle version n'en a plus, ce qui est permis mais pas voulu.

    Un texte servi est du code de prod : une promesse sans condition sera crue."""
    return (f"<!-- flowchart: v{version}, {lignes} lines, omitted here — keep this line "
            "VERBATIM; op=set puts the drawing back only on the SAME slug and scope you "
            "read (no scope = user, not org). op=create never does. "
            "op=get full=true reads it -->")


def sans_le_dessin(body_md: str, version) -> str:
    """Le corps avec son dessin remplacé par le marqueur ; intact s'il n'en a pas."""
    etendue = trouver_le_dessin(body_md)
    if etendue is None:
        return body_md
    debut, fin = etendue
    lignes = body_md[debut:fin].count("\n") + 1
    return body_md[:debut] + marqueur(version, lignes) + body_md[fin:]


def porte_le_marqueur(body_md: str) -> bool:
    return bool(_MARQUEUR.search(body_md or ""))


def marqueur_sans_dessin(body_md: str, courant_md: str) -> bool:
    """Le corps envoyé porte le marqueur, mais le corps courant n'a pas de dessin à
    remettre : `avec_le_dessin` va effacer la ligne, et le dessin est perdu ici."""
    return porte_le_marqueur(body_md) and trouver_le_dessin(courant_md) is None


def avec_le_dessin(body_md: str, courant_md: str) -> str:
    """Le corps à ÉCRIRE : chaque marqueur remplacé par le dessin du corps courant.
    Sans dessin courant (création, ou une procédure qui n'en a jamais eu), le marqueur
    s'efface — et le corps s'écrit sans dessin, ce qui est permis."""
    if not porte_le_marqueur(body_md):
        return body_md
    etendue = trouver_le_dessin(courant_md)
    dessin = courant_md[etendue[0]:etendue[1]] if etendue else ""
    # `\g<0>`-style replacement is not needed: the marker line is replaced whole.
    return _MARQUEUR.sub(lambda _m: dessin, body_md)


# ── Le dessin est là, mais il ne se dessinera pas ────────────────────────────
#
# Le cas qui coûte le plus cher : l'auteur A dessiné, le bloc passe `is_drawing`, et le parseur du front le refuse
# quand même — la page rend alors les caractères bruts, un pavé gris là où la
# procédure devait montrer ses cartes. Le parseur compose pourtant une phrase
# exacte sur ce qu'il n'a pas su lire, puis la jette. L'auteur (une IA, presque
# toujours) ne l'apprend jamais et réécrit la même faute à la version suivante.
#
# ⚠️ Même compromis que `GLYPHS`/`MIN_GLYPHS` plus haut, et mêmes limites : on ne
# reparse RIEN. On ne porte que les règles LIGNE À LIGNE — celles qui ne demandent
# aucun état du parseur, et qui sont chacune le décalque d'un `bail()` précis de
# `ascii-diagram.ts`. Elles sont choisies pour être un SOUS-ENSEMBLE STRICT : ce
# qui est signalé ici est toujours refusé là-bas, jamais l'inverse. Les 72
# procédures publiques qui se dessinent aujourd'hui servent de garde (cf.
# `tests/test_procedure_diagram_lint.py`) — un lint qui crie sur un dessin correct
# apprendrait à l'auteur à ignorer TOUS les avertissements.
#
# Ce que ça ne promet pas, et qui est dit dans la réponse (`"checked": "lines"`) :
# un lint propre ne garantit pas que le schéma se dessine. Les refus structurels
# — une branche qui saute en avant, une boîte que rien n'atteint — demandent le
# graphe, donc le parseur, donc le front.

_FLECHE = "▼"
_PUCE = "▪"
# La voie qui repart et REVIENT plus bas. Comme `_PUCE`, elle peut porter une
# ligne de légende sans glyphe de tracé, que le parseur laisse tomber — la
# compter comme de la prose égarée serait un faux positif sur un dessin correct.
_RETOUR = "▷"
_BOITE = set("┌┐└┘│╔╗╚╝║")
# « la ligne appartient encore au tracé » — même classe que `STRUCTURAL` du front.
_STRUCTUREL = re.compile(r"[─-╿▶▼]")


def lint_du_trace(block: str) -> list[dict]:
    """Les fautes visibles ligne à ligne. `[]` = rien de LOCALEMENT faux."""
    fautes: list[dict] = []
    une_boite_vue = False
    for i, ligne in enumerate(block.split("\n"), start=1):
        # `prepare()` refuse le dessin entier à la première tabulation : elle rend
        # faux tout index de colonne, donc fausse toute attribution.
        if "\t" in ligne:
            fautes.append({"line": i, "rule": "tab",
                           "says": "a tab makes every column index a lie",
                           "fix": "indent the drawing with spaces only"})
            continue
        if any(c in _BOITE for c in ligne):
            une_boite_vue = True
            continue          # une ligne de boîte est lue par une autre branche
        if _FLECHE in ligne:
            derniere = ligne.rfind(_FLECHE)
            # Seul le texte APRÈS la dernière flèche est une étiquette. Entre deux
            # flèches, il serait rattaché au mauvais arc : le parseur refuse la
            # ligne, et avec elle le schéma entier.
            if ligne[:derniere].replace(_FLECHE, "").strip():
                fautes.append({
                    "line": i, "rule": "text-between-arrows",
                    "says": "text sits between two flow arrows, so it cannot be told "
                            "which branch it labels",
                    "fix": "label only after the LAST arrow on the row — or, for a "
                           "branch that leaves the lane, draw a named exit "
                           "(`\u251c\u2500\u2500\u2500\u25b6  \u25aa reason`)",
                })
            continue
        if (une_boite_vue and ligne.strip() and not _STRUCTUREL.search(ligne)
                and _PUCE not in ligne and _RETOUR not in ligne):
            fautes.append({"line": i, "rule": "loose-text",
                           "says": "a line of prose inside the drawing",
                           "fix": "move it into a box, or into the margin of one"})
    return fautes


def _trace_et_offset(body_md: str) -> tuple[str, int] | None:
    """Le tracé rendu par la page, et la ligne du corps où il commence."""
    etendue = trouver_le_dessin(body_md or "")
    if etendue is None:
        return None
    debut, fin = etendue
    # `trouver_le_dessin` rend l'étendue FENCES COMPRISES. Le lint ne doit voir
    # que le tracé : la ligne d'ouverture porte le ``` et la fermante est une
    # ligne sans glyphe, que `loose-text` prendrait pour de la prose égarée.
    lignes = body_md[debut:fin].split("\n")[1:]
    while lignes and lignes[-1].strip().startswith("```"):
        lignes.pop()
    return "\n".join(lignes), (body_md or "").count("\n", 0, debut) + 2


# Au plus deux fautes nommées : la première est presque toujours la cause, et un
# mur d'avertissements se lit comme du bruit.
_MAX_FAUTES = 2


def _phrase(fautes: list[dict]) -> str:
    """Les fautes, dites comme les autres avertissements de ce module : une phrase."""
    dites = "; ".join(f"line {f['line']}: {f['says']} — {f['fix']}"
                      for f in fautes[:_MAX_FAUTES])
    reste = len(fautes) - _MAX_FAUTES
    et_le_reste = f" (+{reste} more)" if reste > 0 else ""
    return ("the drawing will NOT render as the flow figure, the page will show the "
            f"raw characters instead — {dites}{et_le_reste}. Line-level checks only: "
            "a clean result is not a promise it renders.")


def diagram_check(body_md: str, *, marqueur_perdu: bool = False) -> dict:
    """Check croisé à l'écriture, dans la forme des autres (`slots_check`,
    `write_check`) : la clé est TOUJOURS présente, `None` = le check a tourné et
    n'a rien trouvé à dire. Best-effort — un check ne casse jamais une écriture.

    Aucun dessin : rien à dire — le dessin est facultatif. Le check ne parle que d'un
    dessin présent (deux dessins, ou un tracé qui ne se dessinera pas)."""
    try:
        combien = compter_les_dessins(body_md)
        if combien == 0:
            return {"diagram_warning": PERDU if marqueur_perdu else None}
        if combien > 1:
            return {"diagram_warning": DOUBLE}
        # Un seul dessin : reste à dire s'il se dessinera.
        trouve = _trace_et_offset(body_md)
        fautes = lint_du_trace(trouve[0]) if trouve else []
        if not fautes:
            return {"diagram_warning": None}
        for f in fautes:
            f["line"] += trouve[1] - 1        # ligne du CORPS, pas du bloc
        return {"diagram_warning": _phrase(fautes)}
    # noqa: SILENT — contrôle de forme optionnel : pas d'avertissement plutôt qu'un faux
    except Exception:  # noqa: BLE001 — cf. `slots_check`
        return {"diagram_warning": None}
