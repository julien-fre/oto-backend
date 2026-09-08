#!/usr/bin/env python3
"""Ce qu'une charge d'outil gagnerait en TOON — et ce que le serveur en ferait.

Phase de MESURE de la recette « sorties compactes » : on mesure avant de toucher au
code, et on remesure après. `docs/conventions.md` le dit pour l'empreinte servie et
ça vaut ici : *une convention qui demande un chiffre sans dire d'où il sort produit
des chiffres différents chez chacun.* Le chiffre sort donc d'ici, pas d'un comptage
à la main ni d'une estimation.

    python3 scripts/toon_gain.py charge.json
    oto-mcp-dump-un-appel | python3 scripts/toon_gain.py -
    python3 scripts/toon_gain.py captures/*.json

La charge attendue est le JSON tel que l'outil le rend — pour un appel réel, celui
que `oto_admin_monitoring(op="call", call_id=…)` permet de rejouer.

La colonne qui décide est **décision** : c'est ce que `ToonTextChannelMiddleware`
ferait de cette charge-là, marge comprise. Un gain de 18 % s'affiche donc « json »,
et c'est le comportement voulu — le seuil existe parce que diverger de format a un
coût permanent qu'un gain marginal ne paie pas.

Le compte de référence est en CARACTÈRES, comme la décision du serveur. Avec
`tiktoken` installé, la sortie ajoute une colonne en tokens : plus proche de ce qui
est facturé, mais absent des dépendances du backend — et les deux mesures ne se
sont jamais éloignées de plus de deux points sur les charges mesurées le 08/09/2026.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from oto_mcp import toon  # noqa: E402

try:
    import tiktoken
    _ENC = tiktoken.get_encoding("cl100k_base")
except Exception:  # noqa: BLE001 - dépendance facultative, l'absence n'est pas une erreur
    _ENC = None


def _tokens(texte: str) -> int | None:
    return len(_ENC.encode(texte)) if _ENC is not None else None


def _mesure(nom: str, charge: object) -> tuple[str, int, int, float, str]:
    texte = json.dumps(charge, ensure_ascii=False)
    rendu = toon.choisir(charge, texte)
    servi = rendu if rendu is not None else texte
    gain = 100.0 * (1.0 - len(servi) / len(texte)) if texte else 0.0
    # `encode` sans `choisir` : dire « la forme s'y prête mais le gain n'y est pas »
    # est ce qui distingue une charge à écarter d'une charge à aplatir.
    if rendu is not None:
        pourquoi = "TOON"
    elif toon.encode(charge) is None:
        pourquoi = "json (forme refusée)"
    else:
        pourquoi = "json (sous la marge)"
    return nom, len(texte), len(servi), gain, pourquoi


def main(argv: list[str]) -> int:
    cibles = argv[1:]
    if not cibles:
        print(__doc__.strip().splitlines()[0], file=sys.stderr)
        print("usage: toon_gain.py <charge.json> [...] | -", file=sys.stderr)
        return 2

    lignes = []
    for cible in cibles:
        try:
            brut = sys.stdin.read() if cible == "-" else open(cible, encoding="utf-8").read()
            charge = json.loads(brut)
        except (OSError, ValueError) as exc:
            print(f"{cible}: illisible — {exc}", file=sys.stderr)
            return 1
        lignes.append(_mesure("(stdin)" if cible == "-" else os.path.basename(cible), charge))

    largeur = max(len(l[0]) for l in lignes)
    entete = f"{'charge':{largeur}} {'JSON c.':>9} {'servi c.':>9} {'gain':>7}  décision"
    print(entete)
    print("-" * len(entete))
    for nom, avant, apres, gain, pourquoi in lignes:
        print(f"{nom:{largeur}} {avant:9d} {apres:9d} {gain:6.1f}%  {pourquoi}")

    if _ENC is not None:
        print()
        for cible, (nom, *_rest) in zip(cibles, lignes):
            brut = open(cible, encoding="utf-8").read() if cible != "-" else None
            if brut is None:
                continue
            charge = json.loads(brut)
            texte = json.dumps(charge, ensure_ascii=False)
            rendu = toon.choisir(charge, texte) or texte
            avant_tk, apres_tk = _tokens(texte), _tokens(rendu)
            print(f"{nom:{largeur}} {avant_tk:9d} {apres_tk:9d} "
                  f"{100.0 * (1 - apres_tk / avant_tk):6.1f}%  (tokens)")
    else:
        print("\n(tiktoken absent : mesure en caractères seulement)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
