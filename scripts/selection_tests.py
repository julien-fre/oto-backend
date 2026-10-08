#!/usr/bin/env python3
"""Sélection des tests au push sur `main` : les volets touchés, plus le socle (#1185).

POURQUOI.

Un push sur le tronc attendait la suite complète (~20 000 tests, 9 à 13 min) avant la
préproduction, puis le tag la rejouait avant la prod : une journée de lots enchaînés payait
ces minutes deux fois par lot. Décision du 08/10/2026 : au push, seuls les volets touchés
par le diff, PLUS un socle de gardes transverses toujours joué ; au tag, la suite complète,
une seule fois (`deploy.yml`). Le socle couvre le constat de #1111 : les rouges du 01/10
venaient de tests HORS des fichiers modifiés (sortie déclarée des capacités, façade
d'erreur MCP, contrat servi, concordance carte/client).

CE QU'IL REND.

`cible=` dans `$GITHUB_OUTPUT` : les fichiers de tests à jouer, séparés par des espaces,
ou une chaîne VIDE pour la suite complète. Le résumé du run dit quels volets ont été
retenus, à cause de quels fichiers — ou pourquoi la suite est complète.

QUAND C'EST LA SUITE COMPLÈTE.

Une base inconnue (nouvelle branche, poussée forcée : `before` vaut 0000… ou n'existe
pas dans l'historique), un fichier qui tombe dans `[complete]` de la table, un fichier qui
ne tombe dans AUCUN volet — jamais de trou silencieux —, ou une sélection qui dépasse
`seuil_part` des fichiers de tests. Un diff illisible ou une table invalide n'est PAS un
cas de repli : le script échoue, et le job avec lui.

La table et l'ordre des règles : `tests/volets.toml`. Ses invariants :
`tests/test_selection_tests.py`.

Usage : selection_tests.py --base <sha> --tete <sha>
        selection_tests.py --constat <junit.xml> --base <sha> --tete <sha>
"""

from __future__ import annotations

import argparse
import ast
import os
import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
TABLE = RACINE / "tests" / "volets.toml"

# Ce que la sélection peut rendre sans danger dans une ligne de commande : un chemin de
# fichier de test. Tout autre caractère arrête le script (la cible part dans un shell).
_CHEMIN_SUR = re.compile(r"^[A-Za-z0-9_./-]+$")
# Un nom de fichier trop commun pour qu'une mention dans un test désigne CE fichier.
_NOMS_COMMUNS = {"__init__.py", "conftest.py", "ORDRE"}
_SHA_NUL = re.compile(r"^0+$")


class TableInvalide(Exception):
    """La table ne se lit pas comme attendu : on ne sélectionne rien sur une table fausse."""


# ── Globs ──────────────────────────────────────────────────────────────────────

def glob_en_regex(motif: str) -> re.Pattern[str]:
    """`*` ne franchit pas `/`, `**` franchit tout (et `**/` peut ne rien couvrir)."""
    i, sortie = 0, []
    while i < len(motif):
        if motif.startswith("**/", i):
            sortie.append("(?:.*/)?")
            i += 3
        elif motif.startswith("**", i):
            sortie.append(".*")
            i += 2
        elif motif[i] == "*":
            sortie.append("[^/]*")
            i += 1
        elif motif[i] == "?":
            sortie.append("[^/]")
            i += 1
        else:
            sortie.append(re.escape(motif[i]))
            i += 1
    return re.compile("^" + "".join(sortie) + "$")


@dataclass
class Volet:
    nom: str
    sources: list[str]
    tests: list[str]
    _src: list[re.Pattern[str]] = field(default_factory=list, repr=False)
    _tst: list[re.Pattern[str]] = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        self._src = [glob_en_regex(g) for g in self.sources]
        self._tst = [glob_en_regex(g) for g in self.tests]

    def couvre(self, chemin: str) -> bool:
        return any(r.match(chemin) for r in self._src)

    def est_test(self, chemin: str) -> bool:
        return any(r.match(chemin) for r in self._tst)


@dataclass
class Table:
    seuil_part: float
    complete: list[str]
    raison_complete: str
    volets: list[Volet]
    socle: list[str]
    _complete: list[re.Pattern[str]] = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        self._complete = [glob_en_regex(g) for g in self.complete]

    def force_complete(self, chemin: str) -> bool:
        return any(r.match(chemin) for r in self._complete)


def charger_table(chemin: Path, racine: Path) -> Table:
    try:
        brut = tomllib.loads(chemin.read_text(encoding="utf-8"))
        part = float(brut["seuil_part"])
        complete = list(brut["complete"]["chemins"])
        raison = str(brut["complete"]["raison"])
        socle = list(brut["socle"]["tests"])
        volets = [Volet(v["nom"], list(v["sources"]), list(v["tests"])) for v in brut.get("volet", [])]
        for f in brut.get("famille", []):
            noms: set[str] = set()
            for motif in f["noms"]:
                rx = glob_en_regex(motif)
                for p in _fichiers(racine, motif.split("/", 1)[0]):
                    nom = Path(p).stem
                    if rx.match(p) and not nom.startswith("_"):
                        noms.add(nom)
            for nom in sorted(noms):
                volets.append(Volet(
                    "{} {}".format(f["nom"], nom),
                    [g.replace("{nom}", nom) for g in f["sources"]],
                    [g.replace("{nom}", nom) for g in f["tests"]],
                ))
    except (KeyError, TypeError, ValueError, tomllib.TOMLDecodeError) as e:
        raise TableInvalide("{} : {}".format(chemin, e)) from e
    if not 0 < part <= 1:
        raise TableInvalide("seuil_part doit être dans ]0, 1] : {}".format(part))
    return Table(part, complete, raison, volets, socle)


# ── L'état du dépôt ────────────────────────────────────────────────────────────

def _fichiers(racine: Path, dossier: str) -> list[str]:
    base = racine / dossier
    if not base.is_dir():
        return []
    return sorted(
        p.relative_to(racine).as_posix()
        for p in base.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    )


def fichiers_de_tests(racine: Path) -> list[str]:
    return [p for p in _fichiers(racine, "tests") if Path(p).name.startswith("test_") and p.endswith(".py")]


def est_fichier_de_test(chemin: str) -> bool:
    return chemin.startswith("tests/") and Path(chemin).name.startswith("test_") and chemin.endswith(".py")


def module_de(chemin: str) -> str | None:
    """`oto_mcp/db/usage.py` → `oto_mcp.db.usage` ; autre chose → None."""
    if not (chemin.startswith("oto_mcp/") and chemin.endswith(".py")):
        return None
    parts = list(Path(chemin).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


class Index:
    """Ce que les tests importent et citent, lu une fois par exécution."""

    def __init__(self, racine: Path, tests: list[str]) -> None:
        self.tests = tests
        self.imports: dict[str, set[str]] = {}
        self.textes: dict[str, str] = {}
        for t in tests:
            texte = (racine / t).read_text(encoding="utf-8")
            self.textes[t] = texte
            self.imports[t] = _imports_oto(texte, t)

    def importeurs(self, modules: set[str]) -> set[str]:
        return {t for t, m in self.imports.items() if m & modules}

    def citant(self, nom: str) -> set[str]:
        return {t for t, texte in self.textes.items() if nom in texte}


def _imports_oto(texte: str, chemin: str) -> set[str]:
    """Les modules `oto_mcp…` qu'un test importe directement (`from a import b` compte
    `a` ET `a.b`, qui peut être un sous-module)."""
    try:
        arbre = ast.parse(texte, filename=chemin)
    except SyntaxError as e:
        raise SystemExit("{} ne se lit pas : {}".format(chemin, e))
    out: set[str] = set()
    for n in ast.walk(arbre):
        if isinstance(n, ast.Import):
            out.update(a.name for a in n.names if a.name.startswith("oto_mcp"))
        elif isinstance(n, ast.ImportFrom) and n.level == 0 and (n.module or "").startswith("oto_mcp"):
            out.add(n.module)
            out.update("{}.{}".format(n.module, a.name) for a in n.names)
    return out


# ── La sélection ───────────────────────────────────────────────────────────────

@dataclass
class Selection:
    complete: bool
    raisons: list[str]
    tests: list[str]
    volets: dict[str, list[str]]

    @property
    def cible(self) -> str:
        return "" if self.complete else " ".join(self.tests)


def selectionner(modifies: list[str], table: Table, racine: Path, index: Index | None = None) -> Selection:
    tous = fichiers_de_tests(racine)
    index = index or Index(racine, tous)
    existants = set(tous)
    choisis: set[str] = {t for t in table.socle}
    volets: dict[str, list[str]] = {}
    raisons: list[str] = []

    for f in modifies:
        if table.force_complete(f):
            raisons.append("{} : {}".format(f, table.raison_complete))
            continue
        couvert = False
        if est_fichier_de_test(f):
            couvert = True
            if f in existants:
                choisis.add(f)
        for v in table.volets:
            if v.couvre(f):
                couvert = True
                volets.setdefault(v.nom, []).append(f)
        mod = module_de(f)
        if mod:
            choisis |= index.importeurs({mod})
        nom = Path(f).name
        if nom not in _NOMS_COMMUNS:
            choisis |= index.citant(nom)
        if not couvert:
            raisons.append("{} n'appartient à aucun volet de tests/volets.toml".format(f))

    for v in table.volets:
        if v.nom not in volets:
            continue
        choisis |= {t for t in tous if v.est_test(t)}
        modules = {m for m in (module_de(p) for p in _fichiers(racine, "oto_mcp") if v.couvre(p)) if m}
        choisis |= index.importeurs(modules)

    tests = sorted(t for t in choisis if t in existants)
    if not raisons and len(tests) > table.seuil_part * len(tous):
        raisons.append("la sélection ({} fichiers sur {}) dépasse seuil_part = {}".format(
            len(tests), len(tous), table.seuil_part))
    for t in tests:
        if not _CHEMIN_SUR.match(t):
            raise SystemExit("chemin de test refusé dans une ligne de commande : {!r}".format(t))
    return Selection(bool(raisons), raisons, tests, volets)


# ── Le diff ────────────────────────────────────────────────────────────────────

def _git(depot: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=depot, capture_output=True, text=True)


def fichiers_modifies(base: str, tete: str, depot: Path = RACINE) -> list[str] | str:
    """La liste des chemins modifiés, ou la raison pour laquelle la base n'est pas jugeable
    (alors : suite complète). Une erreur de git sur une base CONNUE fait échouer."""
    if not base or _SHA_NUL.match(base):
        return "base inconnue ({!r}) : nouvelle branche ou première poussée".format(base)
    if _git(depot, "cat-file", "-e", "{}^{{commit}}".format(base)).returncode != 0:
        return "la base {} n'est pas dans l'historique (poussée forcée ?)".format(base)
    r = _git(depot, "diff", "--name-only", "--no-renames", base, tete)
    if r.returncode != 0:
        raise SystemExit("git diff {} {} a échoué : {}".format(base, tete, r.stderr.strip()))
    return [l for l in r.stdout.splitlines() if l]


# ── Sorties ────────────────────────────────────────────────────────────────────

def resume(sel: Selection, base: str, tete: str, total: int) -> str:
    lignes = ["### Sélection des tests ({}…{})".format(base[:8], tete[:8]), ""]
    if sel.complete:
        lignes.append("**Suite complète**, parce que :")
        lignes += ["- {}".format(r) for r in sel.raisons]
        return "\n".join(lignes) + "\n"
    lignes.append("**{} fichiers de tests sur {}** : socle + volets touchés.".format(len(sel.tests), total))
    lignes.append("")
    for nom, fichiers in sorted(sel.volets.items()):
        lignes.append("- volet **{}** : {}".format(nom, ", ".join("`{}`".format(f) for f in fichiers)))
    if not sel.volets:
        lignes.append("- aucun volet de code touché (socle, tests modifiés et tests qui citent les fichiers)")
    return "\n".join(lignes) + "\n"


def _ecrire(variable: str, texte: str) -> None:
    chemin = os.environ.get(variable)
    if chemin:
        with open(chemin, "a", encoding="utf-8") as f:
            f.write(texte)


def _fichier_du_cas(classe: str, existants: set[str]) -> str:
    """`tests.datastore.test_x.TestY` → `tests/datastore/test_x.py` : le plus long préfixe
    qui désigne un fichier de tests existant."""
    parts = classe.split(".")
    for n in range(len(parts), 0, -1):
        chemin = "/".join(parts[:n]) + ".py"
        if chemin in existants:
            return chemin
    return classe


def constat(junit: Path, sel: Selection, racine: Path) -> str:
    """Au tag, après un rouge : chaque fichier rouge aurait-il été joué au push ?

    Le rapport JUnit vient de notre propre `pytest`, dans le même job : ElementTree suffit
    (expat ne résout pas d'entité externe)."""
    import xml.etree.ElementTree as ET

    existants = set(fichiers_de_tests(racine))
    rouges: set[str] = set()
    for cas in ET.parse(junit).getroot().iter("testcase"):
        if cas.find("failure") is not None or cas.find("error") is not None:
            rouges.add(_fichier_du_cas(cas.get("classname", ""), existants))
    lignes = ["### Constat #1185 : ces rouges auraient-ils été vus au push ?", ""]
    if sel.complete:
        lignes.append("Au push de ce sha, la suite aurait été complète : tous vus.")
    for r in sorted(rouges):
        vu = sel.complete or r in sel.tests
        lignes.append("- `{}` : {}".format(r, "vu au push" if vu else "**AURAIT FILÉ** jusqu'au tag"))
    return "\n".join(lignes) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--base", required=True)
    ap.add_argument("--tete", required=True)
    ap.add_argument("--constat", type=Path, help="junit.xml d'une suite complète rouge")
    a = ap.parse_args(argv)

    table = charger_table(TABLE, RACINE)
    total = len(fichiers_de_tests(RACINE))
    modifies = fichiers_modifies(a.base, a.tete)
    if isinstance(modifies, str):
        sel = Selection(True, [modifies], [], {})
    else:
        sel = selectionner(modifies, table, RACINE)

    if a.constat:
        texte = constat(a.constat, sel, RACINE)
        print(texte)
        _ecrire("GITHUB_STEP_SUMMARY", texte)
        return 0

    texte = resume(sel, a.base, a.tete, total)
    print(texte)
    _ecrire("GITHUB_STEP_SUMMARY", texte)
    _ecrire("GITHUB_OUTPUT", "cible={}\n".format(sel.cible))
    return 0


if __name__ == "__main__":
    sys.exit(main())
