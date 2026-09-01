#!/usr/bin/env python3
"""Refuse un nom de client, de personne réelle ou de domaine client dans le dépôt.

## Pourquoi

Ce dépôt est **public** (MIT). La règle « jamais de nom de client ni de personne
dans un repo public » a été appliquée deux fois à la main le 2026-09-01 — #709
pour les sources (42 fichiers), #747 pour les tests (~300 occurrences) — et le
tronc était à zéro. Un lot mergé le même jour à 10:26 en a **réintroduit** :
un en-tête de module et un en-tête de test. La règle a donc cédé le jour même où
elle a été appliquée.

Une règle qui ne tient que par la discipline ne tient pas. Celle-ci tient par un
contrôle qui ROUGIT.

## Où vit la liste — et pourquoi pas ici

La liste des termes interdits **n'est pas dans ce dépôt**, sous aucune forme, pas
même hachée. Un fichier d'empreintes salées commité dans un dépôt public est un
**oracle d'appartenance** : hacher coûte à l'attaquant exactement ce qu'il nous
coûte par candidat, donc l'étirement de clé n'achète aucune asymétrie — quiconque
se demande « telle société est-elle cliente d'otomata ? » hache le nom et regarde.
Notre seul avantage serait de tester ~10⁴ jetons quand un curieux en teste ~10⁶ :
un facteur 100, pas un secret. Des empreintes rendraient la liste non-greppable,
pas confidentielle — or c'est l'APPARTENANCE elle-même qu'on protège.

La liste vit donc dehors, et se résout dans cet ordre :

1. `OTO_NOMS_CLIENTS` — le contenu (un terme par ligne). Ce que la CI injecte
   depuis un secret de dépôt.
2. `OTO_NOMS_CLIENTS_FICHIER` — un chemin de fichier.
3. `~/.otomata/noms-clients.txt` — le défaut d'un poste de travail.

Lignes vides et lignes commençant par `#` ignorées.

## Trois états, pas deux

Comme `contrat-front.py`, et pour la même raison : un contrôle qui ne peut pas
juger doit le DIRE, jamais rendre un vert muet.

- `0` — jugé, rien trouvé.
- `1` — jugé, occurrence(s) trouvée(s).
- `2` — **pas jugé** : aucune liste disponible. C'est le cas d'une PR venue d'un
  fork, qui n'a pas accès aux secrets ; le risque visé est notre propre
  réintroduction, et nos branches, elles, l'ont.

⚠️ **Le `2` fait ÉCHOUER le job CI, il ne se contente pas d'avertir** — corrigé le
2026-09-01, le jour de sa pose. Le job traduisait « pas jugé » en `exit 0` avec une
annotation ; résultat, il a rendu « success » sur tous ses runs, dont celui de sa
propre fusion, alors que le secret n'a jamais été posé et qu'aucun jugement n'avait
donc été rendu. L'annotation disait vrai, mais `gh pr checks`, la liste des checks
et la coche de la PR ne lisent QUE la conclusion. **Un garde-fou qui ne peut pas
s'exécuter doit être ROUGE, jamais vert avec une note** : l'état « pas jugé » rendu
en vert fabrique une PREUVE POSITIVE, la pire forme d'un contrôle défaillant — les
autres se voient au moins quand on regarde. Le job reste hors des contrôles requis :
rouge et visible, pas bloquant.

## Portée

**Tout le dépôt** — `git ls-files` : sources, tests, docs, workflows, manifestes.
C'est le point explicite de oto-private#85 : #709 n'avait vu que les sources et
#747 que les tests, et la réintroduction a touché les deux à la fois.

## L'échappatoire, et pourquoi elle est nominative

`# noqa: CLIENT — <raison>` sur la ligne. La raison est **obligatoire** : elle
distingue une dette DÉCLARÉE d'une dette contractée sans le dire. Un `# noqa:
CLIENT` nu est refusé au même titre qu'une occurrence, sinon l'échappatoire
devient le chemin par défaut.

Elle couvre aujourd'hui les identifiants **fonctionnels** que #709 et #747 ont
délibérément laissés (liste CORS de repli, table `RETURN_APPS`, script de
migration de tenants) : les déplacer casse une redirection réelle ou rompt la
réconciliation d'un compte existant. Leur relocalisation vers la config privée
est le second volet de oto-private#85 — pas ce lot.

## La frontière de mot est ASYMÉTRIQUE, et c'est délibéré

Bornée à GAUCHE, libre à DROITE. Mesuré : un terme client de quatre lettres
apparaissait 3034 fois dans ce dépôt… à l'intérieur de « registre »,
« enregistrement », « register ». Une frontière à gauche tue cette classe entière
de faux positifs (le terme y est précédé d'une lettre). La liberté à droite, elle,
attrape ce qu'une frontière des deux côtés manquerait : `<tenant>_doc`,
`<client>-bridge`, `<campagne>-leads`, un slug concaténé. Un contrôle qui crie
pour tout cesse d'être lu ; un contrôle qui ne voit pas un suffixe ne sert à rien.

Usage : `python scripts/lint_noms_clients.py [chemin]` (défaut : la racine du
dépôt). Le garde-fou est exercé par `tests/test_lint_noms_clients.py`, qui prouve
aussi qu'il MORD.
"""
from __future__ import annotations

import os
import pathlib
import re
import subprocess
import sys
from typing import Iterable, Iterator, NamedTuple

# `# noqa: CLIENT — <raison>` : tiret cadratin, demi-cadratin, simple ou deux-points,
# et la raison doit porter au moins un caractère utile. Même grammaire que le
# `# noqa: SILENT` de `lint_silences.py` — une seule forme à retenir dans ce dépôt.
NOQA = re.compile(r"#\s*noqa:[^#\n]*\bCLIENT\b\s*[—–:-]\s*(?P<raison>\S.*)$")

# Ce qui compte comme « lettre » pour la frontière gauche. Les accents en sont :
# sinon un patronyme accentué (`Lefèvre`) se ferait borner au milieu.
LETTRE = r"0-9A-Za-zÀ-ÖØ-öø-ÿ"

# Extensions dont le contenu n'est pas du texte relu par un humain.
BINAIRE = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip", ".gz",
    ".woff", ".woff2", ".ttf", ".otf", ".parquet", ".db", ".sqlite", ".mo",
})


class Occurrence(NamedTuple):
    path: str
    lineno: int
    terme: str
    source: str

    def __str__(self) -> str:
        return f"{self.path}:{self.lineno}: [{self.terme}] {self.source.strip()[:160]}"


def charger_termes() -> list[str] | None:
    """Résout la liste depuis l'extérieur du dépôt. `None` = rien à juger avec."""
    brut = os.environ.get("OTO_NOMS_CLIENTS")
    if not brut:
        chemin = os.environ.get("OTO_NOMS_CLIENTS_FICHIER")
        p = pathlib.Path(chemin).expanduser() if chemin else (
            pathlib.Path.home() / ".otomata" / "noms-clients.txt")
        if not p.is_file():
            return None
        brut = p.read_text(encoding="utf-8")
    termes = [l.strip() for l in brut.splitlines()]
    termes = [t for t in termes if t and not t.startswith("#")]
    return termes or None


def compiler(termes: Iterable[str]) -> re.Pattern[str]:
    """Une seule alternation, bornée à gauche seulement (cf. docstring du module)."""
    alt = "|".join(re.escape(t) for t in sorted(termes, key=len, reverse=True))
    return re.compile(f"(?<![{LETTRE}])({alt})", re.IGNORECASE)


def fichiers_suivis(racine: pathlib.Path) -> list[str]:
    """Tout le dépôt, tel qu'il est PUBLIÉ — donc ce que git suit, rien d'autre."""
    out = subprocess.run(["git", "ls-files", "-z"], cwd=racine,
                         capture_output=True, text=True, check=True).stdout
    return [f for f in out.split("\0") if f]


def scanner(racine: pathlib.Path, termes: Iterable[str]) -> Iterator[Occurrence]:
    motif = compiler(termes)
    for rel in fichiers_suivis(racine):
        if pathlib.Path(rel).suffix.lower() in BINAIRE:
            continue
        try:
            texte = (racine / rel).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue          # binaire non listé, ou lien mort : rien à relire
        for lineno, ligne in enumerate(texte.splitlines(), 1):
            trouve = motif.search(ligne)
            if not trouve:
                continue
            if NOQA.search(ligne):
                continue      # dette DÉCLARÉE, avec sa raison
            yield Occurrence(rel, lineno, trouve.group(1), ligne)


def main(argv: list[str]) -> int:
    racine = pathlib.Path(argv[1]) if len(argv) > 1 else \
        pathlib.Path(__file__).resolve().parent.parent
    termes = charger_termes()
    if termes is None:
        print("lint_noms_clients: AUCUNE liste de termes — rien n'a été jugé.\n"
              "  Renseigner OTO_NOMS_CLIENTS, OTO_NOMS_CLIENTS_FICHIER, ou\n"
              "  ~/.otomata/noms-clients.txt (un terme par ligne).", file=sys.stderr)
        return 2
    occurrences = list(scanner(racine, termes))
    if not occurrences:
        print(f"lint_noms_clients: aucun nom de client sous {racine} "
              f"({len(termes)} termes confrontés)")
        return 0
    print(f"lint_noms_clients: {len(occurrences)} occurrence(s) dans un dépôt PUBLIC\n",
          file=sys.stderr)
    for o in occurrences:
        print(f"  {o}", file=sys.stderr)
    print("\n  Remplacer par la convention du dépôt (`acme`, `Jane Doe`, prose\n"
          "  générique, TLD `.test`), ou déclarer la dette avec\n"
          "  `# noqa: CLIENT — <raison>` si la valeur est FONCTIONNELLE.",
          file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
