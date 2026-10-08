#!/usr/bin/env python3
"""Répartit la suite de tests en N parts jouées en parallèle, une par runner (#1111).

POURQUOI.

Le job `test` jouait toute la suite sur UN runner (`pytest -n 4`) : ~9 min le 01/10/2026,
~14 min le 08/10 (21 500 cas). Chaque correctif d'une ligne payait ce temps, souvent deux
ou trois fois. Plus de workers sur la même machine n'est pas la piste : le 07/09, quatre
workers calibrés sur un chiffre deviné ont fait tuer `pytest` par l'OOM killer sept fois de
suite (tronc rouge six heures, cf. `scripts/empreinte_collecte.py`). La piste, c'est plus
de MACHINES : N runners, chacun avec un `-n` dérivé de SA mémoire.

LE RISQUE N'EST PAS UN DÉCOUPAGE MAL ÉQUILIBRÉ, C'EST UN DÉCOUPAGE TROUÉ.

Un fichier qui n'appartient à aucune part n'est jamais joué, et la CI reste verte : elle ne
rougit pas, elle devient aveugle. D'où trois règles, toutes mécaniques :

1. Les parts se calculent depuis les fichiers PRÉSENTS SUR LE DISQUE, jamais depuis le
   fichier de durées : un fichier de test neuf, absent des durées, tombe quand même dans
   une part (avec un poids par défaut, la médiane des connus).
2. Chaque part publie sa collecte (`--collect-only`, les nodeids exacts), un job à part
   publie la collecte de RÉFÉRENCE (la commande de toujours, `pytest` sans chemin), et
   `verifier` exige : union des parts = référence, aucun doublon. Sinon, rouge.
3. Le découpage est DÉTERMINISTE (tri total, aucune source de hasard) : le plan et les
   parts le recalculent chacun de leur côté et tombent sur le même résultat — `verifier`
   le prouverait sinon.

LE POIDS D'UN FICHIER EST SA DURÉE MESURÉE, PAS SON NOMBRE DE TESTS.

Un module à base paie un `CREATE DATABASE` + le schéma complet (88 s de SETUP pour un seul
module le 08/10, en CI) : son nombre de tests ne dit rien de son coût. Le poids est la somme
setup + call + teardown de ses cas, lue dans les rapports JUnit des parts
(`scripts/durees_suite.json`, sous-commande `mesurer`). Le fichier se régénère depuis
n'importe quel run CI complet :

    gh run download <run_id> --repo otomata-tech/oto-backend -n suite-durees -D /tmp/durees
    cp /tmp/durees/durees_suite.json scripts/durees_suite.json

(l'agrégateur `verdict` de `.github/workflows/suite-tests.yml` produit cet artefact à
chaque suite complète, et dit dans le résumé du run de combien les durées ont dérivé).

UN GROUPE XDIST NE SE SCINDE JAMAIS ENTRE DEUX PARTS (#963).

L'unité de répartition est le FICHIER : le groupement automatique (`tests/_groupes_xdist.py`)
nomme ses groupes d'après le fichier, il ne déborde donc jamais d'une part. Un
`xdist_group(name="…")` ÉCRIT à la main peut, lui, réunir plusieurs fichiers : ces fichiers
sont fusionnés en UN atome avant la répartition.

LE NOMBRE DE WORKERS D'UNE PART SE DÉRIVE DE SA MACHINE.

Sous-commande `part` : elle mesure le pic de mémoire de la collecte de SES fichiers (le pic
qu'un worker xdist paiera, la mort du 07/09 tombait pendant la collecte), lit la mémoire
disponible de la machine où elle tourne, et en déduit `-n` par la règle de
`scripts/empreinte_collecte.py` (marge de 20 %, un process pour le contrôleur, borné par les
cœurs). Aucun nombre recopié.

Sous-commandes : `plan`, `part`, `reference`, `verifier`, `mesurer`, `fusionner` (cf. `--help`).
Bibliothèque standard seulement : le plan et le verdict tournent sans installer le projet.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import resource
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
DUREES = RACINE / "scripts" / "durees_suite.json"

#: Ce qui est réparti quand la cible est vide. La RÉFÉRENCE, elle, est `pytest` sans chemin
#: (la commande de toujours) : un test qui vivrait hors de `tests/` serait collecté par elle
#: et par aucune part — `verifier` le nommerait, au lieu de l'oublier.
CHEMINS_PAR_DEFAUT = ("tests",)

#: Motifs de fichiers et dossiers ignorés : les défauts de pytest (`python_files`,
#: `norecursedirs`) — le dépôt n'en déclare aucun.
_MOTIFS_FICHIERS = (re.compile(r"^test_.*\.py$"), re.compile(r"^.*_test\.py$"))
_DOSSIERS_IGNORES = re.compile(
    r"^(\..*|_darcs|build|CVS|dist|node_modules|venv|\{arch\}|.*\.egg|__pycache__)$")

#: Secondes de tests (cumul des cas) par worker et par part que vise le plan. Une part paie
#: en plus ~1 min de frais fixes (démarrage, installation par le verrou, bac à sable, sa
#: collecte mesurée puis celle de chaque worker) et le run ~35 s de plan et de verdict :
#: à 120 s, la suite complète (~3 200 s de cas le 08/10, 4 cœurs) tient en 7 parts et
#: ~3 min 30 de mur. Baisser ce chiffre ajoute des parts : moins de mur, autant de minutes
#: runner facturées à l'arrondi, plus de places prises dans la file de jobs simultanés.
CIBLE_PAR_WORKER_S = 120.0

#: Plafond de parts. Au-delà, chaque part paie ses frais fixes pour un gain de mur qui fond,
#: et un run prend trop des 20 jobs simultanés d'une organisation au forfait gratuit
#: (les autres jobs du même run, d'autres runs, d'autres workflows partagent cette file).
PARTS_MAX = 8

#: La marge de `scripts/empreinte_collecte.py` (MARGE) — même règle, même chiffre ; un banc
#: (`tests/test_repartir_suite_1111.py`) vérifie qu'elles ne divergent pas.
MARGE = 0.80

_XDIST_GROUP = re.compile(r"""xdist_group\(\s*(?:name\s*=\s*)?["']([^"']+)["']""")


class Refus(Exception):
    """Une entrée qu'on ne sait pas répartir sans risquer un trou : on le dit et on sort."""


# ─────────────────────────────────────────────────────────────────────────────
# Les fichiers : ce que pytest collecterait sous ces chemins.


def _est_fichier_de_test(nom: str) -> bool:
    return any(m.match(nom) for m in _MOTIFS_FICHIERS)


def _parcourir(dossier: Path) -> list[Path]:
    trouves = []
    for entree in sorted(dossier.iterdir()):
        if entree.is_dir():
            if not _DOSSIERS_IGNORES.match(entree.name):
                trouves.extend(_parcourir(entree))
        elif entree.is_file() and _est_fichier_de_test(entree.name):
            trouves.append(entree)
    return trouves


def lire_cible(cible: str) -> list[str]:
    """La cible (chemins séparés par des espaces) validée, ou les chemins par défaut.

    Chaque élément doit être un fichier ou un dossier EXISTANT du dépôt. Un nodeid
    (`::`), une option (`-…`) ou un chemin qui sort du dépôt sont refusés : la cible devient
    des arguments de `pytest`, et elle peut venir d'un autre job."""
    elements = cible.split()
    if not elements:
        return list(CHEMINS_PAR_DEFAUT)
    for e in elements:
        if e.startswith("-"):
            raise Refus(f"cible : « {e} » ressemble à une option de pytest — seuls des chemins sont acceptés")
        if "::" in e:
            raise Refus(f"cible : « {e} » est un nodeid — la répartition se fait par fichier, donner le fichier")
        chemin = (RACINE / e).resolve()
        if chemin != RACINE and RACINE not in chemin.parents:
            raise Refus(f"cible : « {e} » sort du dépôt")
        if not chemin.exists():
            raise Refus(f"cible : « {e} » n'existe pas dans cet arbre")
    return elements


def fichiers_de_test(chemins: list[str]) -> list[str]:
    """Les fichiers de test sous ces chemins, relatifs à la racine, triés, sans doublon."""
    vus: set[str] = set()
    for c in chemins:
        p = RACINE / c
        candidats = [p] if p.is_file() else _parcourir(p)
        for f in candidats:
            vus.add(f.resolve().relative_to(RACINE).as_posix())
    if not vus:
        raise Refus(f"aucun fichier de test sous {' '.join(chemins)} — rien à répartir")
    return sorted(vus)


def atomes(fichiers: list[str]) -> list[tuple[str, ...]]:
    """Les unités indivisibles : un fichier, ou les fichiers qu'un même `xdist_group`
    écrit à la main réunit (union-find sur le nom du groupe)."""
    parent = {f: f for f in fichiers}

    def racine(f: str) -> str:
        while parent[f] != f:
            parent[f] = parent[parent[f]]
            f = parent[f]
        return f

    premier_du_groupe: dict[str, str] = {}
    for f in fichiers:
        for nom in _XDIST_GROUP.findall((RACINE / f).read_text(encoding="utf-8")):
            if nom in premier_du_groupe:
                parent[racine(f)] = racine(premier_du_groupe[nom])
            else:
                premier_du_groupe[nom] = f
    paquets: dict[str, list[str]] = {}
    for f in fichiers:
        paquets.setdefault(racine(f), []).append(f)
    return sorted(tuple(sorted(p)) for p in paquets.values())


# ─────────────────────────────────────────────────────────────────────────────
# Les durées et la répartition.


def lire_durees(chemin: Path = DUREES) -> dict[str, float]:
    donnees = json.loads(chemin.read_text(encoding="utf-8"))
    durees = donnees["durees"]
    if not durees:
        raise Refus(f"{chemin} ne porte aucune durée")
    return {f: float(s) for f, s in durees.items()}


def mediane(valeurs: list[float]) -> float:
    v = sorted(valeurs)
    m = len(v) // 2
    return v[m] if len(v) % 2 else (v[m - 1] + v[m]) / 2


def poids(atome: tuple[str, ...], durees: dict[str, float], defaut: float) -> float:
    return sum(durees.get(f, defaut) for f in atome)


def repartir(atomes_: list[tuple[str, ...]], durees: dict[str, float],
             parts: int) -> list[list[str]]:
    """Le plus lourd d'abord, posé sur la part la moins chargée (LPT). Tri total : le
    même arbre donne toujours le même découpage."""
    if parts < 1:
        raise Refus(f"{parts} part(s) : il en faut au moins une")
    if parts > len(atomes_):
        raise Refus(f"{parts} parts pour {len(atomes_)} atome(s) : une part serait vide")
    defaut = mediane(list(durees.values()))
    ordre = sorted(atomes_, key=lambda a: (-poids(a, durees, defaut), a))
    paniers: list[list[str]] = [[] for _ in range(parts)]
    charges = [0.0] * parts
    for a in ordre:
        i = min(range(parts), key=lambda k: (charges[k], k))
        paniers[i].extend(a)
        charges[i] += poids(a, durees, defaut)
    return [sorted(p) for p in paniers]


def nombre_de_parts(atomes_: list[tuple[str, ...]], durees: dict[str, float],
                    coeurs: int) -> int:
    """Assez de parts pour que chacune porte ~CIBLE_PAR_WORKER_S par worker, borné par
    PARTS_MAX et par le nombre d'atomes. Les cœurs sont ceux de la machine du plan, de la
    même classe que celles des parts ; le `-n` réel, lui, est dérivé par chaque part."""
    defaut = mediane(list(durees.values()))
    total = sum(poids(a, durees, defaut) for a in atomes_)
    voulu = math.ceil(total / (max(1, coeurs) * CIBLE_PAR_WORKER_S))
    return max(1, min(PARTS_MAX, len(atomes_), voulu))


def decouper(cible: str, parts: int | None = None, coeurs: int | None = None,
             durees: dict[str, float] | None = None) -> list[list[str]]:
    """La répartition complète d'une cible : la fonction unique que plan et parts
    appellent, pour tomber sur le même résultat."""
    d = lire_durees() if durees is None else durees
    a = atomes(fichiers_de_test(lire_cible(cible)))
    n = parts if parts is not None else nombre_de_parts(a, d, coeurs or os.cpu_count() or 1)
    return repartir(a, d, n)


# ─────────────────────────────────────────────────────────────────────────────
# La mémoire de LA machine qui exécute.


def memoire_disponible() -> int:
    """MemAvailable, en octets. Muet = refus : on ne choisit pas `-n` à l'aveugle."""
    with open("/proc/meminfo", encoding="ascii") as flux:
        for ligne in flux:
            cle, _, reste = ligne.partition(":")
            if cle == "MemAvailable":
                return int(reste.split()[0]) * 1024
    raise Refus("/proc/meminfo ne donne pas MemAvailable — aucun nombre de workers n'est dérivable")


def workers_pour(disponible: int, pic: int, coeurs: int) -> tuple[int, str]:
    """La règle de `scripts/empreinte_collecte.py` : chaque worker paie le pic d'une
    collecte, le contrôleur vit à côté, 20 % restent au système ; borné par les cœurs."""
    if pic <= 0:
        raise Refus("pic de collecte nul : la mesure n'a rien relevé, aucun `-n` n'est dérivable")
    budget = disponible * MARGE
    tiennent = int(budget // pic)
    workers = max(1, tiennent - 1)
    explication = (f"{disponible / 1024**3:.2f} Go disponibles, {budget / 1024**3:.2f} Go "
                   f"utilisables ({MARGE:.0%}) ; collecte de cette part : {pic / 1024**3:.2f} Go "
                   f"→ {tiennent} process tiennent, {workers} worker(s) contrôleur déduit")
    if workers > coeurs:
        explication += f" ; borné par les cœurs : {coeurs}"
        workers = coeurs
    return workers, explication


_NODEID = re.compile(r"^\S+\.py::\S")


def lire_nodeids(sortie: str) -> list[str]:
    """Les nodeids d'un `--collect-only -q` : le PREMIER bloc de lignes en forme de nodeid.
    Pas toutes les lignes qui contiennent `::` — le résumé des avertissements, plus bas,
    répète des nodeids, et les compter fabriquerait de faux doublons."""
    bloc: list[str] = []
    for ligne in sortie.splitlines():
        if _NODEID.match(ligne):
            bloc.append(ligne)
        elif bloc:
            break
    return bloc


def collecter(arguments: list[str]) -> tuple[list[str], int]:
    """`pytest --collect-only -q` sur ces arguments, dans un fils : rend (nodeids, pic de
    RSS du fils en octets). ⚠️ Appelé au plus UNE fois par processus : `RUSAGE_CHILDREN`
    est le maximum de TOUS les fils déjà moissonnés (cf. `scripts/empreinte_collecte.py`) —
    un seul fils, et le chiffre est le sien."""
    acheve = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider",
         *arguments],
        cwd=RACINE, capture_output=True, text=True)
    pic = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss * 1024
    if acheve.returncode != 0:
        raise Refus(f"la collecte a échoué (code {acheve.returncode}) :\n"
                    f"{acheve.stdout[-3000:]}\n{acheve.stderr[-2000:]}")
    nodeids = lire_nodeids(acheve.stdout)
    if not nodeids:
        raise Refus("la collecte n'a rendu aucun test")
    return nodeids, pic


# ─────────────────────────────────────────────────────────────────────────────
# Le verdict : aucun test perdu, aucun joué deux fois.


def comparer(reference: list[str], parts: dict[str, list[str]]) -> list[str]:
    """Rend la liste des écarts (vide = couverture exacte)."""
    ecarts = []
    attendus = set(reference)
    if len(attendus) != len(reference):
        ecarts.append(f"la collecte de référence porte {len(reference) - len(attendus)} doublon(s)")
    vus: dict[str, str] = {}
    for nom in sorted(parts):
        for n in parts[nom]:
            if n in vus:
                ecarts.append(f"DOUBLON : {n} est collecté par {vus[n]} ET par {nom}")
            vus[n] = nom
    manquants = sorted(attendus - set(vus))
    en_trop = sorted(set(vus) - attendus)
    if manquants:
        ecarts.append(f"TROU : {len(manquants)} test(s) de la référence ne sont joués par "
                      f"aucune part, dont : " + ", ".join(manquants[:10]))
    if en_trop:
        ecarts.append(f"{len(en_trop)} test(s) joués par une part sont absents de la "
                      f"référence, dont : " + ", ".join(en_trop[:10]))
    total = sum(len(v) for v in parts.values())
    if total != len(reference):
        ecarts.append(f"SOMME : {total} collectés par les parts ≠ {len(reference)} par la référence")
    return ecarts


def _lignes(chemin: Path) -> list[str]:
    return [l for l in chemin.read_text(encoding="utf-8").splitlines() if l.strip()]


# ─────────────────────────────────────────────────────────────────────────────
# Les durées mesurées, depuis les rapports JUnit des parts.


def fichier_du_cas(classname: str, name: str, connus: set[str]) -> str | None:
    """Le fichier d'un `<testcase>` JUnit. pytest y écrit le nodeid « mangé » : chemin
    pointé + classes dans `classname`, ou tout dans `name` pour un rapport de MODULE
    (module sauté entier, erreur de collecte). On essaie le préfixe le plus long qui
    désigne un fichier connu."""
    morceaux = (classname or name).split(".")
    for k in range(len(morceaux), 0, -1):
        candidat = "/".join(morceaux[:k]) + ".py"
        if candidat in connus:
            return candidat
    return None


def durees_depuis_junit(rapports: list[Path], connus: set[str]) -> tuple[dict[str, float], int]:
    """{fichier: secondes}, et le nombre de cas qu'aucun fichier connu n'a réclamés."""
    durees: dict[str, float] = {}
    orphelins = 0
    for r in rapports:
        for cas in ET.parse(r).getroot().iter("testcase"):
            f = fichier_du_cas(cas.get("classname", ""), cas.get("name", ""), connus)
            if f is None:
                orphelins += 1
                continue
            durees[f] = durees.get(f, 0.0) + float(cas.get("time") or 0.0)
    return durees, orphelins


def fusionner_junit(rapports: list[Path], sortie: Path) -> int:
    """Un seul `<testsuites>` qui porte les `<testsuite>` de chaque part ; rend le nombre de
    cas. Un rapport absent ou illisible est un refus : un rapport fusionné troué ferait
    taire un rouge."""
    if not rapports:
        raise Refus("aucun rapport JUnit à fusionner")
    racine = ET.Element("testsuites")
    for r in rapports:
        if not r.is_file():
            raise Refus(f"rapport JUnit absent : {r}")
        lu = ET.parse(r).getroot()
        suites = [lu] if lu.tag == "testsuite" else list(lu.iter("testsuite"))
        racine.extend(suites)
    ET.ElementTree(racine).write(sortie, encoding="utf-8", xml_declaration=True)
    return sum(1 for _ in racine.iter("testcase"))


def ecrire_durees(durees: dict[str, float], source: str, chemin: Path) -> None:
    chemin.write_text(json.dumps({
        "source": source,
        "regenerer": "gh run download <run_id> -n suite-durees, cf. scripts/repartir_suite.py",
        "unite": "secondes, somme setup + call + teardown des cas du fichier",
        "durees": {f: round(s, 2) for f, s in sorted(durees.items())},
    }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


# ─────────────────────────────────────────────────────────────────────────────
# Les sous-commandes.


def _plan(o: argparse.Namespace) -> int:
    durees = lire_durees()
    a = atomes(fichiers_de_test(lire_cible(o.cible)))
    coeurs = os.cpu_count() or 1
    n = nombre_de_parts(a, durees, coeurs)
    paniers = repartir(a, durees, n)
    defaut = mediane(list(durees.values()))
    inconnus = sorted(f for p in paniers for f in p if f not in durees)
    print(f"parts={json.dumps(list(range(n)))}")
    print(f"nombre={n}")
    resume = [f"### Répartition de la suite : {n} part(s)",
              f"Cible : `{o.cible.strip() or 'suite complète'}` — "
              f"{sum(len(p) for p in paniers)} fichiers, {len(a)} atomes, "
              f"{coeurs} cœurs sur la machine du plan, cible {CIBLE_PAR_WORKER_S:.0f} s par worker.",
              "", "| part | fichiers | durée estimée (s, cumul des cas) |", "|---|---|---|"]
    for i, p in enumerate(paniers):
        resume.append(f"| {i} | {len(p)} | {sum(durees.get(f, defaut) for f in p):.0f} |")
    if inconnus:
        resume += ["", f"{len(inconnus)} fichier(s) absents de `scripts/durees_suite.json`, "
                   f"comptés à la médiane ({defaut:.1f} s) : " + ", ".join(inconnus[:20])]
    if o.resume:
        with open(o.resume, "a", encoding="utf-8") as flux:
            flux.write("\n".join(resume) + "\n")
    print("\n".join(resume), file=sys.stderr)
    return 0


def _part(o: argparse.Namespace) -> int:
    paniers = decouper(o.cible, parts=o.parts)
    if not 0 <= o.part < len(paniers):
        raise Refus(f"part {o.part} hors de 0..{len(paniers) - 1}")
    fichiers = paniers[o.part]
    sortie = Path(o.sortie)
    sortie.mkdir(parents=True, exist_ok=True)
    (sortie / "fichiers.txt").write_text("\n".join(fichiers) + "\n", encoding="utf-8")
    nodeids, pic = collecter(fichiers)
    (sortie / "collecte.txt").write_text("\n".join(nodeids) + "\n", encoding="utf-8")
    workers, explication = workers_pour(memoire_disponible(), pic, os.cpu_count() or 1)
    print(f"part {o.part}/{o.parts} : {len(fichiers)} fichiers, {len(nodeids)} tests collectés",
          file=sys.stderr)
    print(f"workers : {explication} → -n {workers}", file=sys.stderr)
    # Le jeton de `_jeton_de_suite.py` protège un poste PARTAGÉ ; ici le runner est seul :
    # autant de places que de workers, sinon il en bloque une partie à vide.
    print(f"OTO_SUITE_WORKERS={workers}")
    print(f"OTO_TEST_PG_PLACES={workers}")
    return 0


def _reference(o: argparse.Namespace) -> int:
    cible = o.cible.split()
    if cible:
        lire_cible(o.cible)
    # Cible vide : `pytest` SANS chemin, la commande que jouait l'ancien job `test`.
    nodeids, _ = collecter(cible)
    Path(o.sortie).write_text("\n".join(nodeids) + "\n", encoding="utf-8")
    print(f"référence : {len(nodeids)} tests collectés", file=sys.stderr)
    return 0


def _verifier(o: argparse.Namespace) -> int:
    dossier = Path(o.dossier)
    reference = _lignes(dossier / o.reference)
    parts: dict[str, list[str]] = {}
    absentes = []
    for i in range(o.parts):
        chemin = dossier / f"{o.prefixe}{i}" / "collecte.txt"
        if not chemin.is_file():
            absentes.append(i)
            continue
        parts[f"part {i}"] = _lignes(chemin)
    lignes = [f"### Aucun test perdu : {len(reference)} collectés par la référence",
              "", "| part | collectés |", "|---|---|"]
    lignes += [f"| {nom} | {len(v)} |" for nom, v in sorted(parts.items())]
    lignes.append(f"| **somme** | **{sum(len(v) for v in parts.values())}** |")
    ecarts = [f"part {i} : aucune collecte publiée — ses tests n'ont pas pu être comptés"
              for i in absentes] + comparer(reference, parts)
    lignes += [""] + ([f"❌ {e}" for e in ecarts] or ["✅ union des parts = référence, aucun doublon"])
    texte = "\n".join(lignes)
    print(texte)
    if o.resume:
        with open(o.resume, "a", encoding="utf-8") as flux:
            flux.write(texte + "\n")
    return 1 if ecarts else 0


def _mesurer(o: argparse.Namespace) -> int:
    rapports = [Path(r) for r in o.junit]
    connus = set(fichiers_de_test(list(CHEMINS_PAR_DEFAUT)))
    mesurees, orphelins = durees_depuis_junit(rapports, connus)
    if not mesurees:
        raise Refus("aucune durée lue dans ces rapports JUnit")
    ecrire_durees(mesurees, o.source, Path(o.sortie))
    lignes = [f"### Durées mesurées : {len(mesurees)} fichiers, "
              f"{sum(mesurees.values()):.0f} s de cas cumulés"]
    if orphelins:
        lignes.append(f"⚠️ {orphelins} cas JUnit rattachés à aucun fichier de `tests/`")
    if DUREES.is_file():
        avant = lire_durees()
        ecart = sorted(((mesurees.get(f, 0.0) - avant.get(f, 0.0), f)
                        for f in set(avant) | set(mesurees)), key=lambda t: -abs(t[0]))
        lignes.append(f"Versionné : {sum(avant.values()):.0f} s. Plus forts écarts :")
        lignes += [f"- `{f}` : {d:+.1f} s" for d, f in ecart[:10]]
    texte = "\n".join(lignes)
    print(texte)
    if o.resume:
        with open(o.resume, "a", encoding="utf-8") as flux:
            flux.write(texte + "\n")
    return 0


def _fusionner(o: argparse.Namespace) -> int:
    cas = fusionner_junit([Path(r) for r in o.junit], Path(o.sortie))
    print(f"{len(o.junit)} rapport(s) JUnit fusionnés, {cas} cas", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    a = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    s = a.add_subparsers(dest="commande", required=True)

    p = s.add_parser("plan", help="combien de parts : `parts=[…]` et `nombre=N` (GITHUB_OUTPUT)")
    p.add_argument("--cible", default="")
    p.add_argument("--resume", help="fichier où ajouter le tableau (GITHUB_STEP_SUMMARY)")
    p.set_defaults(fn=_plan)

    p = s.add_parser("part", help="les fichiers et la collecte d'une part, et son `-n` (GITHUB_ENV)")
    p.add_argument("--cible", default="")
    p.add_argument("--parts", type=int, required=True)
    p.add_argument("--part", type=int, required=True)
    p.add_argument("--sortie", required=True, help="dossier : fichiers.txt, collecte.txt")
    p.set_defaults(fn=_part)

    p = s.add_parser("reference", help="la collecte de référence (cible vide = `pytest` sans chemin)")
    p.add_argument("--cible", default="")
    p.add_argument("--sortie", required=True)
    p.set_defaults(fn=_reference)

    p = s.add_parser("verifier", help="union des collectes des parts = référence, sans doublon")
    p.add_argument("--dossier", required=True)
    p.add_argument("--reference", required=True, help="chemin relatif au dossier")
    p.add_argument("--parts", type=int, required=True)
    p.add_argument("--prefixe", default="suite-part-", help="sous-dossier de chaque part")
    p.add_argument("--resume")
    p.set_defaults(fn=_verifier)

    p = s.add_parser("mesurer", help="durées par fichier depuis des rapports JUnit")
    p.add_argument("junit", nargs="+")
    p.add_argument("--sortie", required=True)
    p.add_argument("--source", required=True, help="d'où viennent ces mesures (run, sha)")
    p.add_argument("--resume")
    p.set_defaults(fn=_mesurer)

    p = s.add_parser("fusionner", help="un seul rapport JUnit pour toutes les parts")
    p.add_argument("junit", nargs="+")
    p.add_argument("--sortie", required=True)
    p.set_defaults(fn=_fusionner)

    o = a.parse_args(argv)
    try:
        return o.fn(o)
    except Refus as refus:
        print(f"::error title=Répartition de la suite refusée::{refus}", file=sys.stderr)
        print(f"REFUS : {refus}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
