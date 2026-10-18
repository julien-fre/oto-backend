#!/usr/bin/env python3
"""Réécrit en anglais les instructions de départ que la PLATEFORME a composées en français.

`capabilities/_instruction.py` compose désormais en anglais : une instruction composée
en français faisait rendre aux agents hébergés leur compte rendu en français, quelle
que soit la langue de la personne. Mais l'instruction composée est ÉCRITE dans la base
à la déclaration (`runner_triggers.input`, `runner_fleets.input`) : les agents déjà
déclarés gardent la leur. Ce script les réécrit — et rien d'autre.

⚠️ **La base est PARTAGÉE entre préproduction et production.** Lancé À LA MAIN, depuis
le commit qui l'apporte une fois déployé — ce n'est pas une migration de boot. **À blanc
par défaut** : sans `--appliquer`, il liste ce qu'il ferait et n'écrit rien. Idempotent.

    # 1. à blanc : lire le rapport
    ./.venv/bin/python -m scripts.instructions_en_anglais
    # 2. écrire
    ./.venv/bin/python -m scripts.instructions_en_anglais --appliquer
    # 3. vérifier : un second passage à blanc doit annoncer 0 à réécrire

## Ce qu'il réécrit

Une instruction n'est réécrite que si elle est ÉGALE, au caractère près, à ce que
l'ancienne composition française rendait pour les champs de SA ligne (procédure,
tableau, filtre) — recomposés ici, gelés tels qu'ils étaient servis. Une instruction
écrite par quelqu'un n'est jamais touchée, même en français : elle est à lui.

Trois paquets au rapport :
- `a_reecrire` : égalité exacte → remplacée par la composition anglaise des mêmes champs ;
- `a_relire` : porte le marqueur « Lis la procédure » sans égaler la composition — une
  variante plus ancienne (`@claimed` avant le 07/09/2026, file nommée avant #1067) ou
  une composée retouchée à la main. Listée par id, JAMAIS devinée ;
- le reste : ignoré.

L'écriture est une comparaison-échange (`WHERE input = <lue>`) : une instruction
retouchée entre la lecture et l'écriture n'est pas écrasée, elle est comptée.

⚠️ **À passer juste après le déploiement, AVANT de relancer une campagne encore
adressée par le NOM de son tableau** : `_lignes_reservables.fixer_le_tableau` reconnaît
une consigne composée en la recomposant, désormais en anglais. Une consigne française
n'y est plus reconnue : la campagne serait re-clée sur le numéro en gardant sa consigne
française, et ce script ne la reconnaîtrait plus non plus (son tableau a changé) — elle
tomberait dans `a_relire`.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Any, Optional

from oto_mcp.capabilities import _instruction
from oto_mcp.db._conn import _connect_autocommit

_MARQUEUR = "Lis la procédure"

# ── L'ancienne composition, GELÉE telle qu'elle était servie (origin/main 274ad60b) ──

_BILAN_FR = ("conclus par un bilan bref de ce que tu as fait et de ce qui t'a "
             "manqué.")


def _derivee_fr(slug: str) -> str:
    return (f"Lis la procédure `{slug}` et applique-la. Elle fait autorité : "
            f"n'invente rien qu'elle ne dise, et {_BILAN_FR}")


def _de_file_fr(slug: str, namespace: Optional[str],
                row_filter: Optional[dict[str, Any]] = None) -> str:
    if not namespace:
        return _derivee_fr(slug)
    filtre = (f", filtre `{json.dumps(row_filter, ensure_ascii=False)}`"
              if row_filter else "")
    return (
        f"Lis la procédure `{slug}` et applique-la : elle fait autorité, "
        "n'invente rien qu'elle ne dise.\n"
        f"Ta file de travail est le tableau `{namespace}`{filtre}. Réserve UNE "
        "SEULE ligne avec `data_claim_next` — jamais une deuxième, un autre "
        "agent prendra la suivante. Si la réservation ne rend rien, la file est "
        "vide : conclus et arrête-toi.\n"
        "La réservation te rend `ns_id`, le NUMÉRO du tableau : adresse-le par ce "
        "numéro dans tous tes appels suivants (`namespace: 174`). Un nom marche "
        "encore, il est en cours de retrait.\n"
        "Écris ton résultat en un seul appel sur la ligne que tu as réservée : "
        "`data_claim_next` t'a rendu son `_id`, passe-le dans `id`. Relis-la, puis "
        f"{_BILAN_FR}")


# ── Le plan ──────────────────────────────────────────────────────────────────────

def _filtre(v: Any) -> Optional[dict]:
    """`row_filter` relu de la base : jsonb (dict) ou texte selon le pilote."""
    if v is None or isinstance(v, dict):
        return v
    return json.loads(v)


def plan_declencheur(t: dict) -> Optional[str]:
    """L'instruction anglaise qui remplace celle du déclencheur, ou None."""
    slug = t.get("procedure")
    if slug and t.get("input") == _derivee_fr(slug):
        return _instruction.derivee(slug)
    return None


_FILTRE_CITE = re.compile(r", filtre `(\{.*\})`\. Réserve UNE")


def _filtre_dans_l_ordre(texte: str, filtre: Optional[dict]) -> Optional[dict]:
    """Le filtre tel que l'instruction l'a CITÉ, s'il égale celui de la ligne.

    ⚠️ jsonb ne garde pas l'ordre des clés : relu de la base, un filtre à plusieurs
    clés ne se recompose plus au caractère près. L'ordre servi est dans le texte."""
    m = _FILTRE_CITE.search(texte or "")
    if not m or filtre is None:
        return filtre
    try:
        cite = json.loads(m.group(1))
    except ValueError:
        return filtre
    return cite if cite == filtre else filtre


def plan_campagne(f: dict) -> Optional[str]:
    """L'instruction anglaise qui remplace celle de la campagne, ou None."""
    slug, ns = f.get("procedure"), f.get("namespace")
    filtre = _filtre_dans_l_ordre(f.get("input"), _filtre(f.get("row_filter")))
    if slug and f.get("input") == _de_file_fr(slug, ns, filtre):
        return _instruction.de_file(slug, ns, filtre)
    return None


_TABLES = {
    "runner_triggers": ("id, org_id, procedure, input", plan_declencheur),
    "runner_fleets": ("id, org_id, procedure, namespace, row_filter, input", plan_campagne),
}


def inventaire(conn) -> dict[str, dict[str, list]]:
    rapport: dict[str, dict[str, list]] = {}
    for table, (cols, plan) in _TABLES.items():
        lignes = conn.execute(
            f"SELECT {cols} FROM {table} WHERE input LIKE %s ORDER BY id",
            (f"%{_MARQUEUR}%",)).fetchall()
        a_reecrire, a_relire = [], []
        for r in lignes:
            neuve = plan(dict(r))
            if neuve is None:
                a_relire.append((r["id"], r["org_id"]))
            else:
                a_reecrire.append((r["id"], r["org_id"], r["input"], neuve))
        rapport[table] = {"a_reecrire": a_reecrire, "a_relire": a_relire}
    return rapport


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--appliquer", action="store_true",
                    help="écrire (sans : à blanc, rien n'est écrit)")
    args = ap.parse_args(argv)
    with _connect_autocommit() as conn:
        rapport = inventaire(conn)
        for table, paquets in rapport.items():
            print(f"{table} : {len(paquets['a_reecrire'])} à réécrire, "
                  f"{len(paquets['a_relire'])} à relire")
            for id_, org, *_ in paquets["a_reecrire"]:
                print(f"  réécrire  id={id_} org={org}")
            for id_, org in paquets["a_relire"]:
                print(f"  relire    id={id_} org={org}")
        if not args.appliquer:
            print("à blanc : rien n'a été écrit (--appliquer pour écrire)")
            return 0
        ecrites = bougees = 0
        for table, paquets in rapport.items():
            for id_, _org, lue, neuve in paquets["a_reecrire"]:
                n = conn.execute(
                    f"UPDATE {table} SET input = %s WHERE id = %s AND input = %s",
                    (neuve, id_, lue)).rowcount
                if n:
                    ecrites += 1
                else:
                    bougees += 1
                    print(f"  {table} id={id_} : retouchée depuis la lecture, laissée telle quelle")
        print(f"{ecrites} réécrite(s), {bougees} laissée(s) car retouchée(s) entre-temps")
    return 0


if __name__ == "__main__":
    sys.exit(main())
