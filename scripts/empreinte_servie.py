#!/usr/bin/env python3
"""L'empreinte de ce que le serveur SERT au modèle — descriptions et schémas d'outils.

**Pourquoi ce script existe, et il naît d'une erreur datée.** La convention de
`docs/conventions.md` demande qu'une PR qui allonge une description servie annonce son
delta en caractères. Le 29/08/2026, la PR #573 l'a annoncé — **et le chiffre était
faux** : il avait été obtenu en mesurant les *docstrings* du code (`+162` sur
`data_write`), alors que le serveur en sert **2 058 caractères** là où la docstring en
fait 2 776. Le harnais ne sert pas la docstring : il en retire le bloc `Args:`, désindente,
et normalise. Le delta réellement servi était `+75`.

> **Une convention qui demande un chiffre sans dire comment l'obtenir produit des chiffres
> différents chez chacun.** Et un écart de comptage est pire qu'un chiffre absent : il rend
> aveugle l'outil qui compare — on ne distingue plus « mesuré autrement » de « la moitié du
> correctif manque ».

D'où la règle qui accompagne ce script : **le delta d'une PR sort d'ici, pas d'un
comptage à la main.**

**Ce qu'il mesure, et pourquoi c'est ça** : il monte les outils **comme le serveur les
monte** (`tools.register_all` puis `capabilities._mcp_adapter.register`, la séquence de
`server.py`), puis lit ce que `tools/list` rendrait. C'est le texte que le modèle reçoit
à chaque connexion — donc la seule longueur qui pèse sur son comportement.

Usage :

    python scripts/empreinte_servie.py                      # tout, en tableau
    python scripts/empreinte_servie.py data_write oto_doc   # ces outils seulement
    python scripts/empreinte_servie.py --json               # sortie machine
    python scripts/empreinte_servie.py --diff origin/main   # delta contre un état du tronc

⚠️ `--diff` **clone** le dépôt dans un répertoire temporaire pour y mesurer l'autre état :
deux versions du même paquet ne peuvent pas coexister dans un processus. Le clone est
jeté à la fin, et le tree courant n'est jamais touché — pas de `git checkout`, pas de
worktree posé dans un checkout que d'autres sessions partagent peut-être.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent


def _monter() -> list:
    """Monte les outils dans l'ordre du serveur et rend ce que `tools/list` servirait.

    L'ordre compte : `register_all` d'abord (il remplit les registres de seams), les
    capacités ensuite — c'est la séquence de `server.py`. Monter autrement rendrait une
    surface différente de celle qui est servie, ce qui viderait la mesure de son sens.

    ⚠️ **La racine du dépôt passe DEVANT tout le reste dans le chemin d'import.** Sans
    ça, `python scripts/empreinte_servie.py` met `scripts/` en tête et `oto_mcp` est
    résolu par l'installation éditable du venv — c'est-à-dire un AUTRE checkout. Le
    script mesurerait alors le tree de quelqu'un d'autre en croyant mesurer le sien,
    et un `--diff` comparerait deux choses sans rapport. Vécu le 29/08 : le premier
    relevé annonçait quinze outils modifiés par une PR qui n'en touchait aucun."""
    sys.path.insert(0, str(RACINE))
    from fastmcp import FastMCP

    from oto_mcp.capabilities import _mcp_adapter
    from oto_mcp.capabilities import registry as cap_registry
    from oto_mcp.tools import register_all

    mcp = FastMCP("empreinte-servie")
    register_all(mcp)
    _mcp_adapter.register(mcp, cap_registry.CAPABILITIES)
    return asyncio.run(mcp.list_tools())


def relever(noms: list[str] | None = None) -> dict:
    """`{outil: {description, schema, sha256}}` — les longueurs de ce qui est servi.

    L'empreinte porte sur description **et** schéma d'entrée : un paramètre ajouté ou
    une énumération élargie changent ce que le modèle lit, au même titre qu'une phrase."""
    out: dict[str, dict] = {}
    for t in _monter():
        if noms and t.name not in noms:
            continue
        desc = t.description or ""
        schema = getattr(t, "parameters", None) or {}
        brut = json.dumps(schema, sort_keys=True, ensure_ascii=False)
        out[t.name] = {
            "description": len(desc),
            "schema": len(brut),
            "sha256": hashlib.sha256((desc + "\n" + brut).encode()).hexdigest()[:12],
        }
    return dict(sorted(out.items()))


def _relever_ailleurs(ref: str, noms: list[str] | None) -> dict:
    """Le même relevé, sur un autre état du tronc, dans un clone jeté après usage."""
    # ⚠️ La référence se résout ICI, dans le dépôt de l'appelant, et on ne passe au clone
    # qu'un SHA. Un nom comme `origin/main` n'a pas le même sens des deux côtés : dans le
    # clone, `origin` c'est le dépôt local, donc `origin/main` y désigne la branche
    # `main` LOCALE — souvent en retard sur le tronc réel. Le premier essai a ainsi
    # comparé à un état vieux de plusieurs jours, et annoncé vingt outils modifiés par
    # une PR qui n'en touchait aucun. Un chiffre plausible et faux, encore.
    sha = subprocess.run(["git", "-C", str(RACINE), "rev-parse", ref],
                         check=True, capture_output=True, text=True).stdout.strip()
    tmp = Path(tempfile.mkdtemp(prefix="empreinte-"))
    try:
        clone = tmp / "repo"
        subprocess.run(["git", "clone", "--quiet", "--no-hardlinks", str(RACINE), str(clone)],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", str(clone), "checkout", "--quiet", sha],
                       check=True, capture_output=True)
        # ⚠️ L'INSTRUMENT VOYAGE, LE SUJET CHANGE : on recopie CE script dans le clone
        # plutôt que d'utiliser celui que `ref` porte — il peut n'en porter aucun (le
        # cas de sa propre PR d'introduction), ou en porter une version qui compte
        # autrement. Mesurer deux états avec deux règles rendrait un delta qui mélange
        # le changement du code et le changement de la mesure : exactement la confusion
        # que ce script existe pour supprimer.
        shutil.copy2(Path(__file__).resolve(), clone / "scripts" / "empreinte_servie.py")
        cmd = [sys.executable, "scripts/empreinte_servie.py", "--json", *(noms or [])]
        r = subprocess.run(cmd, cwd=clone, capture_output=True, text=True,
                           env={**os.environ, "PYTHONPATH": str(clone)})
        if r.returncode != 0:
            raise SystemExit(f"relevé impossible sur `{ref}` ({sha[:8]}) :\n{r.stderr[-2000:]}")
        return json.loads(r.stdout)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _tableau(rel: dict) -> None:
    largeur = max((len(n) for n in rel), default=10)
    print(f"{'outil':{largeur}}  {'description':>11}  {'schéma':>7}  empreinte")
    for nom, v in rel.items():
        print(f"{nom:{largeur}}  {v['description']:>11}  {v['schema']:>7}  {v['sha256']}")
    print(f"\n{len(rel)} outils · {sum(v['description'] for v in rel.values())} caractères "
          f"de description servis au total")


def _diff(avant: dict, apres: dict) -> int:
    """Le delta outil par outil. Rend le nombre d'outils dont l'empreinte a changé."""
    noms = sorted(set(avant) | set(apres))
    bouges = [n for n in noms if avant.get(n, {}).get("sha256") != apres.get(n, {}).get("sha256")]
    if not bouges:
        print("aucun outil servi n'a changé — ni description, ni schéma")
        return 0
    largeur = max(len(n) for n in bouges)
    print(f"{'outil':{largeur}}  {'description':>22}  {'schéma':>16}")
    total = 0
    for n in bouges:
        a, b = avant.get(n), apres.get(n)
        if a is None:
            print(f"{n:{largeur}}  {'NOUVEL OUTIL':>22}  {b['schema']:>16}")
            total += b["description"]
            continue
        if b is None:
            print(f"{n:{largeur}}  {'RETIRÉ':>22}  {'':>16}")
            total -= a["description"]
            continue
        dd, ds = b["description"] - a["description"], b["schema"] - a["schema"]
        total += dd
        print(f"{n:{largeur}}  {a['description']:>7} → {b['description']:<5} "
              f"{dd:+5d}  {a['schema']:>5} → {b['schema']:<5} {ds:+4d}")
    print(f"\n{len(bouges)} outil(s) servi(s) modifié(s) · "
          f"delta de description : {total:+d} caractères")
    print("→ c'est CE chiffre qui s'annonce dans la PR (docs/conventions.md).")
    return len(bouges)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("outils", nargs="*", help="limiter à ces outils (défaut : tous)")
    p.add_argument("--json", action="store_true", help="sortie machine")
    p.add_argument("--diff", metavar="REF", help="comparer à cet état du tronc")
    a = p.parse_args()
    noms = a.outils or None
    apres = relever(noms)
    if a.diff:
        _diff(_relever_ailleurs(a.diff, noms), apres)
    elif a.json:
        print(json.dumps(apres, ensure_ascii=False))
    else:
        _tableau(apres)


if __name__ == "__main__":
    main()
