"""Le corps d'un nœud, parsé en BLOCS — lot M2 du modèle de contenu (#287).

0054-D2 / 0063-D2 : le corps d'un nœud est une **composition de blocs stockés**, pas
un markdown qu'on reparserait à chaque lecture. On y gagne l'adressage natif (un
bloc a un identifiant, donc une prose peut le désigner), l'édition chirurgicale
(remplacer un paragraphe sans réécrire la page) et le verrouillage fin.

**Ce qui ne devient PAS des blocs** : les révisions. Un instantané sérialisé doit
rester atomique et lisible tel quel — le reconstituer par assemblage serait le
rendre dépendant de l'état courant des blocs. Le courant en table pour l'adressage,
l'historique en document pour l'intégrité (0063-D2).

## L'invariant du parse, et pourquoi il vaut mieux qu'une grammaire

**Chaque bloc porte sa SOURCE EXACTE (`props->>'md'`), et la concaténation des blocs
d'un nœud rend le corps au caractère près.** Ce n'est pas une commodité : c'est ce
qui rend le parse *vérifiable*. Un découpage qui prétend comprendre le markdown
finit toujours par en perdre un bout (une fin de ligne, un espace significatif dans
un bloc de code, une liste indentée) — et cette perte n'est visible qu'au moment où
quelqu'un relit sa page et n'y reconnaît plus ce qu'il avait écrit. Ici la propriété
se teste en une ligne : `render_blocks(parse_blocks(md)) == md`, sur n'importe quel
corpus.

Corollaire assumé : **une seule source de vérité par bloc**. Le contenu d'un bloc de
code n'est pas stocké une deuxième fois « en structuré » à côté de sa source — deux
copies d'une même donnée finissent par diverger. `code_of()` le dérive à la demande.

## Le grain : paragraphes, titres, et le code isolé

- une **clôture de code** (``` / ~~~) est un bloc `code` à elle seule (0054-D2 :
  « code, isolé du texte ») ; son info-string devient `props->>'lang'` ;
- le reste se coupe aux **lignes vides** et **autour des titres**, ce qui reproduit
  l'outline du document — le grain qu'attend n'importe quel éditeur de blocs.
- l'**inline nu** (lien, emphase, mention sans attribut) reste du markup DANS le bloc
  texte (0054-D2, tranché le 05/08) : couper un paragraphe en trois parce qu'il
  contient un lien serait une régression de lecture. Les blocs `image` et `référence`
  naîtront des surfaces d'édition, pas d'une conversion de markdown — un markdown
  brut ne porte pas les attributs qui les définissent.

## ⚠️ Aujourd'hui ces blocs sont une PROJECTION

Le corps courant reste `props->>'body_md'` (et, côté legacy, `docs.body_md`). Le
parse est rejoué au boot pour tout nœud dont le corps a changé — marqueur
`props->>'blocks_md5'`, donc **no-op** quand rien ne bouge. Le jour où les blocs
deviennent la source de vérité, c'est l'ÉCRITURE qui les posera et ce module se
réduira au parseur.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Iterable, Optional

from ._conn import _connect

logger = logging.getLogger(__name__)

TEXT = "text"
CODE = "code"

# Ouverture/fermeture de clôture de code : jusqu'à 3 espaces d'indentation, au moins
# trois backticks ou tildes. L'info-string (le `python` de ```python) n'existe qu'à
# l'ouverture — une ligne de fermeture n'en porte jamais.
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})[ \t]*(\S[^\n]*)?$")
# Titre ATX. Sert à COUPER, pas à typer : un titre reste du texte (0054-D2 ne
# connaît que texte/code/image/référence — un genre « titre » de plus serait un
# concept de plus, ce que tout le chantier cherche à éviter).
_HEADING = re.compile(r"^ {0,3}#{1,6}([ \t]|$)")


def parse_blocks(md: str) -> list[dict]:
    """Découpe un corps markdown en blocs `{type, md, lang?}`.

    Invariant : `"".join(b["md"] for b in parse_blocks(x)) == x`, toujours.
    """
    if not md:
        return []
    lines = md.splitlines(keepends=True)
    blocks: list[dict] = []
    buf: list[str] = []
    i, n = 0, len(lines)
    while i < n:
        m = _FENCE.match(lines[i].rstrip("\r\n"))
        if not m:
            buf.append(lines[i])
            i += 1
            continue
        _flush_text(buf, blocks)
        fence, info = m.group(1), (m.group(2) or "").strip()
        chunk, i = [lines[i]], i + 1
        while i < n:
            chunk.append(lines[i])
            close = _FENCE.match(lines[i].rstrip("\r\n"))
            i += 1
            # Ferme sur le MÊME caractère et au moins aussi longue, sans info-string
            # (CommonMark). Une clôture non fermée court jusqu'à la fin du document —
            # c'est aussi ce que fait un rendu markdown.
            if close and close.group(1)[0] == fence[0] \
                    and len(close.group(1)) >= len(fence) and not close.group(2):
                break
        block = {"type": CODE, "md": "".join(chunk)}
        if info:
            block["lang"] = info.split()[0]
        blocks.append(block)
    _flush_text(buf, blocks)
    return blocks


def _flush_text(buf: list[str], blocks: list[dict]) -> None:
    """Vide le tampon de texte en blocs, coupés aux lignes vides et aux titres.

    Les lignes vides restent COLLÉES au bloc qu'elles suivent : le séparateur
    appartient au bloc du dessus. C'est ce qui évite des blocs de blanc, tout en
    gardant la concaténation exacte."""
    if not buf:
        return
    cur: list[str] = []
    pending = False           # une coupure est due avant le prochain contenu

    def close() -> None:
        if not cur:
            return
        piece = "".join(cur)
        if not piece.strip() and blocks:
            # Du blanc seul (typiquement juste après une clôture de code) : il
            # prolonge le bloc précédent plutôt que d'en former un vide.
            blocks[-1]["md"] += piece
        else:
            blocks.append({"type": TEXT, "md": piece})
        cur.clear()

    for line in buf:
        if not line.strip():
            cur.append(line)
            pending = True
            continue
        if pending:
            close()
            pending = False
        if _HEADING.match(line.rstrip("\r\n")):
            close()               # le titre ouvre son propre bloc…
            cur.append(line)
            pending = True        # …et le referme aussitôt
            continue
        cur.append(line)
    close()
    buf.clear()


def render_blocks(blocks: Iterable[dict]) -> str:
    """Le corps, reconstitué depuis ses blocs. Exactement l'original."""
    return "".join(b["md"] for b in blocks)


def code_of(block: dict) -> Optional[str]:
    """Le contenu d'un bloc de code, DÉRIVÉ de sa source (jamais stocké à part :
    deux copies d'une même donnée finissent par diverger). None si ce n'en est pas."""
    if block.get("type") != CODE:
        return None
    body = block["md"].splitlines(keepends=True)[1:]     # sans la clôture ouvrante
    # Le séparateur qui suit une clôture lui a été COLLÉ (cf. `_flush_text`) : on
    # remonte au-delà de ce blanc avant de chercher la fermeture, sinon un bloc suivi
    # d'une ligne vide se lit comme une clôture non fermée et rend ses backticks.
    end = len(body)
    while end and not body[end - 1].strip():
        end -= 1
    if end and _FENCE.match(body[end - 1].rstrip("\r\n")):
        return "".join(body[:end - 1])
    return "".join(body)                                  # clôture jamais fermée


# --- Backfill au boot ---------------------------------------------------------

# Le marqueur d'idempotence. `md5` et pas sha : c'est l'empreinte que PostgreSQL
# calcule nativement (`md5(text)`), donc le filtre SQL ci-dessous peut se comparer
# sans que Python n'ait à relire un seul corps quand rien n'a bougé — le boot
# nominal coûte UNE requête qui ne rend rien.
_MARKER = "blocks_md5"

_SELECT_STALE = (
    "SELECT id, public_id, COALESCE(props->>'body_md', '') AS body "
    "FROM nodes WHERE props ? 'body_md' "
    f"AND (props->>'{_MARKER}') IS DISTINCT FROM md5(COALESCE(props->>'body_md', '')) "
    "AND NOT (id = ANY(%s)) ORDER BY id LIMIT %s"
)

_INSERT_BLOCK = (
    "INSERT INTO blocks (public_id, node_id, position, type, props) "
    "VALUES (%s, %s, %s, %s, %s::jsonb) "
    "ON CONFLICT ON CONSTRAINT blocks_public_id_key DO UPDATE SET "
    "  position = EXCLUDED.position, type = EXCLUDED.type, "
    "  props = EXCLUDED.props, updated_at = NOW()"
)


def block_public_id(node_public_id: str, index: int) -> str:
    """L'identifiant d'un bloc, DÉRIVÉ de (nœud, rang) — pas de son contenu.

    Deux propriétés voulues : rejouer le parse ne fabrique pas d'identifiants neufs
    (donc pas de doublons, et un `ON CONFLICT` suffit), et **l'adresse d'un bloc
    survit à la réécriture de son texte** — ce qui est le propre d'une adresse. Un
    nœud NATIF, lui, tirera ses identifiants au sort (0059-D3)."""
    return "blk_" + hashlib.md5(
        f"{node_public_id}:{index}".encode("utf-8")).hexdigest()[:24]


def write_node_blocks(conn, node_id: int, node_public_id: str, body: str) -> int:
    """(Ré)écrit les blocs d'UN nœud depuis son corps, et pose le marqueur.

    ⚠️ Le marqueur est l'empreinte du corps QU'ON VIENT DE PARSER, pas une relecture
    SQL de `props->>'body_md'` : si le corps a changé entre la lecture et l'écriture,
    l'empreinte ne correspondra pas au nouveau corps et le boot suivant re-parsera.
    Relire en SQL stamperait au contraire le nouveau corps avec les blocs de
    l'ancien — un décalage définitif, et silencieux."""
    parsed = parse_blocks(body)
    conn.execute("DELETE FROM blocks WHERE node_id = %s", (node_id,))
    if parsed:
        params = []
        for idx, b in enumerate(parsed):
            props = {k: v for k, v in b.items() if k != "type"}
            params.append((block_public_id(node_public_id, idx), node_id,
                           (idx + 1) * 16, b["type"], json.dumps(props)))
        with conn.cursor() as cur:
            cur.executemany(_INSERT_BLOCK, params)
    conn.execute(
        f"UPDATE nodes SET props = props || jsonb_build_object('{_MARKER}', %s::text) "
        "WHERE id = %s",
        (hashlib.md5(body.encode("utf-8")).hexdigest(), node_id))
    return len(parsed)


def backfill_node_blocks(*, batch: int = 200) -> int:
    """Parse le corps des nœuds qui n'ont pas (ou plus) leurs blocs. Rejouable.

    Appelé par `init_db` APRÈS la transaction de schéma (les nœuds convertis y sont
    déjà commités). **Fail-open, par nœud** : ces blocs ne sont lus par aucune
    surface — faire tomber un boot de production pour un markdown biscornu serait
    hors de proportion. L'échec est loggué et le nœud ÉCARTÉ de cette passe ; sans
    cet écart, il serait resélectionné indéfiniment (son marqueur n'ayant pas été
    posé) et bloquerait tous les suivants. Il est retenté au boot d'après.

    ⚠️ Le marqueur vit dans `props`, que la conversion RÉÉCRIT en newer-wins quand la
    source change : un corps édité perd donc son marqueur et se re-parse au boot
    suivant. C'est voulu — les blocs sont une projection tant que l'écriture n'est
    pas basculée."""
    done: int = 0
    skipped: list[int] = []
    while True:
        with _connect() as conn:
            rows = conn.execute(_SELECT_STALE, (skipped, batch)).fetchall()
            if not rows:
                return done
            for r in rows:
                try:
                    with conn.transaction():
                        write_node_blocks(conn, int(r["id"]), r["public_id"],
                                          r["body"] or "")
                    done += 1
                except Exception:
                    logger.warning("blocs : nœud %s non parsé (fail-open)",
                                   r["public_id"], exc_info=True)
                    skipped.append(int(r["id"]))
