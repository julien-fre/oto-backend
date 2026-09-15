#!/usr/bin/env python3
"""Répartit les fichiers de test en N parts, pour les jouer en parallèle.

**Le risque de ce découpage n'est pas d'être mal équilibré, c'est d'être TROUÉ.**
Un fichier qui n'appartient à aucune part n'est jamais joué, et la CI reste verte —
elle ne rougit pas, elle devient aveugle. C'est le même défaut qu'une erreur de
collecte, en silencieux. La répartition est donc CALCULÉE à chaque run depuis la
collecte réelle, jamais figée dans un fichier que quelqu'un devrait penser à
mettre à jour, et `--verifier` prouve que l'union des parts est exactement
l'ensemble collecté.

Le poids d'un fichier n'est pas son nombre de tests : un module qui ouvre une base
paie un `CREATE DATABASE` + `DROP DATABASE` qui pèse plus que ses tests. Mesuré le
15/09/2026 sur le job `test` : le plus lent des 25 est un SETUP à 25,75 s, et trois
des cinq premiers sont des setups. Un module à base compte donc pour son coût fixe
en plus de ses tests.
"""
from __future__ import annotations

import argparse
import pathlib
import re
import subprocess
import sys

RACINE = pathlib.Path(__file__).resolve().parent.parent
# Coût fixe d'un module qui ouvre une base, en « équivalents tests ». Calibré sur
# les setups observés (9 à 26 s) contre un test unitaire (quelques ms) : l'ordre de
# grandeur suffit, c'est un ÉQUILIBRAGE, pas une facturation.
COUT_BASE = 120
_BASE = re.compile(r"\b(live|pg_dsn|pg_module_dsn|pg_box)\b")


def _collecte() -> dict[str, int]:
    """{fichier: nombre de tests}, depuis la collecte réelle de pytest."""
    r = subprocess.run([sys.executable, "-m", "pytest", "tests/", "--collect-only",
                        "-q", "-p", "no:cacheprovider"],
                       cwd=RACINE, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"la collecte a échoué (code {r.returncode}) :\n{r.stdout[-2000:]}")
    compte: dict[str, int] = {}
    for ligne in r.stdout.splitlines():
        if "::" in ligne:
            compte[ligne.split("::", 1)[0]] = compte.get(ligne.split("::", 1)[0], 0) + 1
    if not compte:
        sys.exit("aucun test collecté — refus de rendre une répartition vide")
    return compte


def _poids(fichier: str, tests: int) -> int:
    try:
        ouvre_une_base = bool(_BASE.search((RACINE / fichier).read_text()))
    except OSError:
        ouvre_une_base = False
    return tests + (COUT_BASE if ouvre_une_base else 0)


def repartir(compte: dict[str, int], parts: int) -> list[list[str]]:
    """Le plus lourd d'abord, posé sur la part la moins chargée (LPT)."""
    paniers: list[list[str]] = [[] for _ in range(parts)]
    charges = [0] * parts
    for fichier, tests in sorted(compte.items(), key=lambda kv: -_poids(*kv)):
        i = charges.index(min(charges))
        paniers[i].append(fichier)
        charges[i] += _poids(fichier, tests)
    return paniers


def main() -> None:
    a = argparse.ArgumentParser()
    a.add_argument("--parts", type=int, required=True)
    a.add_argument("--part", type=int, help="index 0-based de la part à rendre")
    a.add_argument("--verifier", action="store_true",
                   help="prouve que l'union des parts est l'ensemble collecté")
    o = a.parse_args()

    compte = _collecte()
    paniers = repartir(compte, o.parts)

    if o.verifier:
        union: list[str] = [f for p in paniers for f in p]
        manquants = set(compte) - set(union)
        doublons = len(union) - len(set(union))
        vides = [i for i, p in enumerate(paniers) if not p]
        tests_par_part = [sum(compte[f] for f in p) for p in paniers]
        print(f"{len(compte)} fichiers, {sum(compte.values())} tests")
        print(f"tests par part : {tests_par_part}")
        if manquants:
            sys.exit(f"TROU : {len(manquants)} fichier(s) dans aucune part — "
                     f"ils ne seraient JAMAIS joués : {sorted(manquants)[:5]}")
        if doublons:
            sys.exit(f"{doublons} fichier(s) dans plusieurs parts")
        if vides:
            sys.exit(f"part(s) vide(s) : {vides} — le découpage est plus fin que la suite")
        print("✓ couverture complète, aucun doublon")
        return

    if o.part is None:
        a.error("--part est requis hors --verifier")
    print("\n".join(paniers[o.part]))


if __name__ == "__main__":
    main()
