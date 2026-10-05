#!/usr/bin/env python3
"""Fige les colonnes des tableaux : déclare celles que les lignes PORTENT sans que le
schéma les déclare (oto#124).

À partir du 21/10/2026 (`datastore/colonnes_non_declarees.py`), une écriture qui pose
une colonne non déclarée est REFUSÉE, sur tous les tableaux. Sans ce gel, la garde
atterrirait sur un existant déjà hors règle : mesuré le 04/10/2026, 524 couples
tableau/colonne non déclarés sur 53 tableaux à schéma, et 75 tableaux sans schéma du
tout — chacun verrait refuser des écritures sur des colonnes qui y vivent depuis
longtemps, sans que personne n'ait rien changé ce jour-là. L'ordre est donc :
**relever, déclarer l'existant, puis armer** — jamais l'inverse.

⚠️ **La base est PARTAGÉE entre préproduction et production.** Lancé À LA MAIN, depuis
le commit qui l'apporte une fois déployé — ce n'est pas une migration de boot. **À blanc
par défaut** : sans `--appliquer`, il liste tableau par tableau ce qu'il ferait, et
n'écrit rien. **À relancer juste avant la bascule** : une colonne créée pendant le
préavis doit être gelée elle aussi. Idempotent.

    # 1. à blanc : lire le rapport
    ./.venv/bin/python -m scripts.figer_colonnes [--tableau <ns_id> …]
    # 2. écrire
    ./.venv/bin/python -m scripts.figer_colonnes --appliquer [--tableau <ns_id> …]
    # 3. vérifier : un second passage à blanc doit annoncer 0 tableau à figer

## Ce qu'il fait

Pour chaque tableau — à schéma ou SANS —, les colonnes de premier niveau que portent
ses lignes et que son schéma ne déclare pas sont AJOUTÉES à la fin de `fields`, de la
plus portée à la moins portée. Le `type` est déduit des valeurs par la règle de
`datastore/types_inferes.py` (homogénéité stricte ; hétérogène ou vide → PAS de type,
qui ne contraint rien). Rien d'autre : ni `options`, ni `required`, ni réglage de tête —
on déclare des COLONNES, pas un contrat. Un tableau sans schéma reçoit
`{"fields": [...]}`.

Ce qui n'est PAS une colonne et reste tel quel, compté au rapport : une clé pointée
(`x.comment` littérale — une relique, que la pose d'un schéma refuse comme nom de
colonne), une clé vide, une clé technique (`_…`). Aucune écriture ne peut les reposer :
elles n'ont rien à geler.

## Écriture : le chemin de `data_set_schema`

Par `DatastorePg.set_schema`, via le store système de `durcir_schemas` (`_StoreSysteme`,
autorité de la PLATEFORME, sans principal), avec le schéma EN PLACE complété — rien
n'est retiré, et la pose rend `declarations_effacees` si c'était le cas (le rapport le
recopierait). Chaque pose est journalisée dans `tool_calls` (`data_set_schema`, `sub`
nul, `args.migration_systeme`, `args.colonnes`). Le schéma est relu juste avant
d'écrire : s'il a bougé depuis l'inventaire, le tableau est SAUTÉ et compté, jamais
réécrit depuis une lecture périmée.

## Lecture : un tableau à la fois, bornée

Une requête par tableau (`statement_timeout` 15 s) : les clés et valeurs des colonnes
non déclarées de ses lignes. Un tableau qui dépasse la borne est listé « non lu »,
jamais deviné.
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Optional

from oto_mcp import calllog, db
from oto_mcp.datastore import colonnes_non_declarees as cnd
from oto_mcp.datastore import types_inferes as ti
from oto_mcp.datastore.couches import unwrap
from oto_mcp.datastore.errors import SchemaDefinitionError
from oto_mcp.db._conn import _connect_autocommit
from scripts.durcir_schemas import STATEMENT_TIMEOUT, _StoreSysteme

MIGRATION = "figer_colonnes"


@dataclass
class Plan:
    """Ce que le gel fait à UN tableau. Pur : calculé sans lire ni écrire."""
    schema: Any                                       # le schéma résultant
    ajout: list = field(default_factory=list)          # [{"key", "type"?}]
    portees: dict = field(default_factory=dict)        # {colonne: cellules non vides}
    non_colonnes: list = field(default_factory=list)   # clés laissées telles quelles

    @property
    def vide(self) -> bool:
        return not self.ajout


def figer(schema: Any, valeurs: dict) -> Plan:
    """Le plan de gel d'un tableau. `valeurs` = `{colonne: [valeurs stockées]}` des
    colonnes que ses lignes portent HORS de celles que le schéma déclare."""
    connues = cnd.declarees(schema if isinstance(schema, dict) else None)
    restantes = {k: v for k, v in valeurs.items() if k not in connues}
    ajout = ti.colonnes_a_declarer(restantes, connues)
    non_colonnes = sorted(k for k in restantes if not ti.declarable(k))
    portees = {f["key"]: sum(1 for v in restantes[f["key"]]
                             if unwrap(v) not in (None, "", [], {})) for f in ajout}
    if not ajout:
        return Plan(schema=schema, non_colonnes=non_colonnes)
    neuf = dict(schema) if isinstance(schema, dict) else {}
    neuf["fields"] = list(neuf.get("fields") or []) + ajout
    return Plan(schema=neuf, ajout=ajout, portees=portees, non_colonnes=non_colonnes)


# ── lecture ──────────────────────────────────────────────────────────────────

def inventaire(tableaux: Optional[list[int]] = None) -> list[dict]:
    """TOUS les tableaux — à schéma comme sans —, lus en une requête bornée."""
    with _connect_autocommit() as conn:
        conn.execute(f"SET statement_timeout = '{STATEMENT_TIMEOUT}'")
        sql = "SELECT id, namespace, owner_type, owner_id, schema FROM user_datastores"
        if tableaux:
            rows = conn.execute(sql + " WHERE id = ANY(%s) ORDER BY id",
                                (list(tableaux),)).fetchall()
        else:
            rows = conn.execute(sql + " ORDER BY id").fetchall()
    return [dict(r) for r in rows]


def valeurs_non_declarees(ns_id: int, declarees: set) -> dict:
    """`{colonne: [valeurs]}` des colonnes que les lignes du tableau portent hors de
    `declarees`. UNE requête, bornée — c'est le découpage : un tableau par requête."""
    with _connect_autocommit() as conn:
        conn.execute(f"SET statement_timeout = '{STATEMENT_TIMEOUT}'")
        rows = conn.execute(
            "SELECT e.key AS k, e.value AS v FROM datastore_rows r "
            "CROSS JOIN LATERAL jsonb_each(r.data) e "
            "WHERE r.ns_id = %s AND NOT (e.key = ANY(%s))",
            (ns_id, sorted(declarees))).fetchall()
    out: dict = {}
    for r in rows:
        out.setdefault(r["k"], []).append(r["v"])
    return out


# ── écriture ─────────────────────────────────────────────────────────────────

def _journaliser(t: dict, plan: Plan, ok: bool, erreur: Optional[str]) -> None:
    calllog.log_rest_call(
        "data_set_schema", sub=None, ok=ok, error=erreur,
        org_id=int(t["owner_id"]) if t["owner_type"] == "org" else None,
        args={"datastore": str(t["id"]), "migration_systeme": MIGRATION,
              "colonnes": ", ".join(f"{f['key']}: {f.get('type') or 'sans type'}"
                                    for f in plan.ajout)})


def ecrire(t: dict, plan: Plan) -> tuple[str, Optional[str]]:
    """`("ecrit", remarque)`, `("bouge", None)` ou `("refuse", raison)`."""
    en_place = (db.get_datastore_by_id(int(t["id"])) or {}).get("schema")
    if en_place != t["schema"]:
        return "bouge", None
    try:
        out = _StoreSysteme().set_schema(str(t["id"]), plan.schema)
    except (SchemaDefinitionError, ValueError) as e:
        _journaliser(t, plan, False, str(e))
        return "refuse", str(e)
    _journaliser(t, plan, True, None)
    # Le schéma en place est reposé COMPLÉTÉ : rien ne devait partir. Si la pose dit le
    # contraire, c'est à lire — le rapport le recopie.
    effacees = out.get("declarations_effacees")
    return "ecrit", (f"⚠️ déclarations effacées : {effacees}" if effacees
                     else out.get("warning"))


# ── le passage ───────────────────────────────────────────────────────────────

def _ligne(f: dict, portees: dict) -> str:
    return f"{f['key']} ({f.get('type') or 'sans type'}, {portees.get(f['key'], 0)})"


def executer(*, appliquer: bool = False, tableaux: Optional[list[int]] = None,
             sortie=print) -> dict:
    """Le passage complet. Rend le bilan (aussi imprimé)."""
    parc = inventaire(tableaux)
    bilan = {"parcourus": len(parc), "a_figer": 0, "sans_schema_a_figer": 0,
             "colonnes": 0, "ecrits": 0, "bouges": [], "refuses": [], "non_lus": [],
             "types": Counter(), "non_colonnes": 0}
    for t in parc:
        schema = t["schema"] if isinstance(t["schema"], dict) else None
        titre = (f"ns {t['id']} « {t['namespace']} » ({t['owner_type']} "
                 f"{t['owner_id']}){'' if schema else ' — SANS schéma'}")
        try:
            valeurs = valeurs_non_declarees(int(t["id"]), cnd.declarees(schema))
        except Exception as e:  # noqa: BLE001 — un tableau illisible n'arrête pas le parc
            bilan["non_lus"].append((t["id"], type(e).__name__))
            sortie(f"{titre} — NON LU ({type(e).__name__}) : relancer avec "
                   f"--tableau {t['id']}")
            continue
        plan = figer(t["schema"], valeurs)
        bilan["non_colonnes"] += len(plan.non_colonnes)
        if plan.vide:
            if plan.non_colonnes:
                sortie(f"{titre} — rien à figer ; laissées (pas des colonnes) : "
                       + ", ".join(f"`{k}`" for k in plan.non_colonnes))
            continue
        bilan["a_figer"] += 1
        bilan["sans_schema_a_figer"] += 0 if schema else 1
        bilan["colonnes"] += len(plan.ajout)
        bilan["types"].update(f.get("type") or "sans type" for f in plan.ajout)
        sortie(f"{titre} — {len(plan.ajout)} colonne(s) à déclarer : "
               + ", ".join(_ligne(f, plan.portees) for f in plan.ajout))
        if plan.non_colonnes:
            sortie("  laissées (pas des colonnes) : "
                   + ", ".join(f"`{k}`" for k in plan.non_colonnes))
        if not appliquer:
            continue
        etat, detail = ecrire(t, plan)
        if etat == "ecrit":
            bilan["ecrits"] += 1
            sortie("  → écrit" + (f" — la pose dit : {detail}" if detail else ""))
        elif etat == "bouge":
            bilan["bouges"].append(t["id"])
            sortie("  → SAUTÉ : le schéma a changé depuis l'inventaire, relancer")
        else:
            bilan["refuses"].append((t["id"], detail))
            sortie(f"  → REFUSÉ : {detail}")
    sortie("")
    sortie(f"— {'ÉCRIT' if appliquer else 'À BLANC'} : {bilan['a_figer']} tableau(x) à "
           f"figer sur {bilan['parcourus']} (dont {bilan['sans_schema_a_figer']} sans "
           f"schéma), {bilan['colonnes']} colonne(s) à déclarer")
    for typ, n in bilan["types"].most_common():
        sortie(f"  {n} × {typ}")
    if bilan["non_colonnes"]:
        sortie(f"  clés laissées (pointées, vides ou techniques) : {bilan['non_colonnes']}")
    if bilan["non_lus"]:
        sortie(f"  NON LUS : {len(bilan['non_lus'])} — "
               + ", ".join(str(i) for i, _ in bilan["non_lus"]))
    if appliquer:
        sortie(f"  écrits : {bilan['ecrits']} ; sautés (bougés) : {len(bilan['bouges'])}"
               f" ; refusés : {len(bilan['refuses'])}")
    return bilan


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--appliquer", action="store_true",
                   help="écrire (sans lui : à blanc, rien n'est écrit)")
    p.add_argument("--tableau", type=int, action="append",
                   help="limiter à ce tableau (répétable)")
    a = p.parse_args(argv)
    bilan = executer(appliquer=a.appliquer, tableaux=a.tableau)
    return 1 if (bilan["bouges"] or bilan["refuses"] or bilan["non_lus"]) else 0


if __name__ == "__main__":
    sys.exit(main())
