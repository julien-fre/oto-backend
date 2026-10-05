#!/usr/bin/env python3
"""Retire `unknown_columns` des schémas STOCKÉS (oto#124) — plus aucun réglage.

Arbitré le 05/10/2026 : les colonnes et les valeurs sont toujours vérifiées. Le
réglage `unknown_columns` (`create` | `report` | `reject`) est refusé à la pose et au
patch depuis le commit qui apporte ce script ; ce script le RETIRE des schémas qui le
portent encore. Pas de tolérance permanente du stocké : après lui, plus rien ne le lit.

⚠️ **Quand le lancer : APRÈS le 21/10/2026** (jour UTC, `OTO_VALIDATION_COMPLETE_LE`).
D'ici là, `reglages.format_contraignant` LIT encore le réglage stocké : c'est lui qui
garde la validation complète aux tableaux réglés `report`/`reject` pendant le préavis.
Le retirer avant la date les ferait retomber au préavis (une valeur hors options
écrite au lieu d'être refusée). À partir de la date, la validation complète
s'applique à tous et le réglage n'est plus consulté : le retrait ne change aucun
comportement, et aucune lecture ne casse entre le déploiement et lui (le stocké
inchangé est toléré à la pose comme au patch, et dit à la lecture).

⚠️ **La base est PARTAGÉE entre préproduction et production.** Lancé À LA MAIN, UNE
fois, depuis le commit qui l'apporte (ou un descendant). **À blanc par défaut** : sans
`--appliquer`, il liste ce qu'il ferait, et n'écrit rien.

    # 1. à blanc : lire le rapport
    ./.venv/bin/python -m scripts.retirer_unknown_columns [--tableau <ns_id> …] \\
        [--procedure <id> …] [--bibliotheque <id> …]
    # 2. écrire
    ./.venv/bin/python -m scripts.retirer_unknown_columns --appliquer [filtres…]
    # 3. vérifier : un second passage à blanc doit annoncer 0 partout

Trois familles, sur le modèle du renommage d'oto#127 :

- les **tableaux** (`user_datastores.schema`), écrits par `DatastorePg.set_schema` —
  le chemin de `data_set_schema` — via le store système de `durcir_schemas`, et
  journalisés dans `tool_calls` (`args.migration_systeme`) ;
- les **schémas cibles des slots de procédure** (`org_instructions.slots[*].schema`),
  écrits par `org_store.set_instruction` : une nouvelle version, l'ancienne gardée
  dans l'historique, `expected_version` contre l'écrasement ; une procédure retirée
  est listée, jamais écrite ;
- les **entrées de la bibliothèque** (`guide_library.slots[*].schema`), que le fork
  recopie dans une procédure d'org. Écrites par `org_store.publish_guide`, le chemin
  de la publication : même slug, même auteur, tous les champs reconduits.

Un schéma qui a bougé depuis l'inventaire est sauté (relancer). Idempotent.
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from dataclasses import dataclass
from typing import Any, Optional

from oto_mcp import calllog, db, org_store
from oto_mcp import slots as slots_mod
from oto_mcp.datastore import reglages
from oto_mcp.datastore.errors import SchemaDefinitionError
from scripts.durcir_schemas import (_StoreSysteme, inventaire, inventaire_bibliotheque,
                                    inventaire_procedures)

MIGRATION = "retirer_unknown_columns"
#: L'auteur d'une version de procédure ou d'entrée de bibliothèque écrite ici.
AUTEUR = f"migration:{MIGRATION}"
CLE = reglages.UNKNOWN_COLUMNS


@dataclass
class Plan:
    """Ce que le retrait fait à UN schéma. Pur."""
    schema: Any
    retire: dict      # {"unknown_columns": valeur} — vide = rien à faire

    @property
    def vide(self) -> bool:
        return not self.retire


def retirer(schema: Any) -> Plan:
    """Le plan de retrait d'un schéma : le même, sans `unknown_columns` en tête, dans
    l'ordre de ses autres clés."""
    if not isinstance(schema, dict) or CLE not in schema:
        return Plan(schema, {})
    return Plan({k: v for k, v in schema.items() if k != CLE}, {CLE: schema[CLE]})


def _decrire(plan: Plan) -> str:
    return f"{CLE}: {plan.retire[CLE]!r} retiré"


def retirer_slots(slots: Any) -> tuple[list, dict]:
    """`(slots sans le réglage, {nom du slot: Plan})` — seuls les slots qui changent
    sont dans le dict."""
    neufs, plans = [], {}
    for s in slots if isinstance(slots, list) else []:
        if isinstance(s, dict) and isinstance(s.get("schema"), dict):
            plan = retirer(s["schema"])
            if not plan.vide:
                plans[s.get("name")] = plan
                s = {**s, "schema": plan.schema}
        neufs.append(s)
    return neufs, plans


# ── écritures, par le chemin normal de chaque famille ────────────────────────

def ecrire_tableau(t: dict, plan: Plan) -> tuple[str, Optional[str]]:
    """`("ecrit", warning)`, `("bouge", None)` ou `("refuse", raison)`."""
    en_place = (db.get_datastore_by_id(int(t["id"])) or {}).get("schema")
    if en_place != t["schema"]:
        return "bouge", None
    try:
        out = _StoreSysteme().set_schema(str(t["id"]), plan.schema)
        ok, erreur = True, None
    except (SchemaDefinitionError, ValueError) as e:
        out, ok, erreur = {}, False, str(e)
    calllog.log_rest_call(
        "data_set_schema", sub=None, ok=ok, error=erreur,
        org_id=int(t["owner_id"]) if t["owner_type"] == "org" else None,
        args={"datastore": str(t["id"]), "migration_systeme": MIGRATION,
              "retire": plan.retire})
    return ("ecrit", out.get("warning")) if ok else ("refuse", erreur)


def ecrire_procedure(p: dict, slots: list) -> tuple[str, Optional[str]]:
    try:
        valides = slots_mod.validate_slots(slots)
        org_store.set_instruction(
            p["owner_type"], p["owner_id"], p["slug"], p["body_md"], slots=valides,
            set_by=AUTEUR, expected_version=p["version"])
    except org_store.InstructionVersionConflict:
        return "bouge", None
    except (org_store.InstructionArchived, ValueError) as e:
        return "refuse", str(e)
    return "ecrit", None


def ecrire_entree(e: dict, slots: list) -> tuple[str, Optional[str]]:
    """Republie l'entrée telle quelle, slots sans le réglage. Relue juste avant : une
    entrée republiée depuis l'inventaire est sautée, jamais écrasée. `publish_guide` valide
    les slots lui-même, sous son verrou, contre ceux qu'il remplace (oto#34) : une clé
    inconnue déjà stockée et inchangée passe, le retrait n'en pose aucune."""
    actuelle = org_store.get_library_entry(entry_id=int(e["id"]), include_unlisted=True)
    if not actuelle or (actuelle["version"], actuelle["slots"]) != (e["version"],
                                                                   e["slots"]):
        return "bouge", None
    try:
        org_store.publish_guide(
            slug=e["slug"], title=e["title"] or "", description=e["description"] or "",
            body_md=e["body_md"], author_kind=e["author_kind"],
            author_org_id=e["author_org_id"], author_display=e["author_display"] or "",
            category=e["category"] or "", tags=list(e["tags"] or []),
            visibility=e["visibility"], source_org_id=e["source_org_id"],
            source_slug=e["source_slug"], forked_from=e["forked_from"],
            published_by=AUTEUR, slots=slots)
    except (org_store.LibrarySlugTaken, ValueError) as err:   # LibrarySlotsInvalid
        return "refuse", str(err)
    return "ecrit", None


# ── le passage ───────────────────────────────────────────────────────────────

def _famille(bilan: dict, nom: str, elements: list, *, schemas, ecrire, titre,
             appliquer: bool, sortie, retiree=lambda x: False) -> None:
    b = bilan[nom] = {"parcourus": len(elements), "a_retirer": 0, "ecrits": 0,
                      "bouges": [], "refuses": [], "retires": []}
    for x in elements:
        neuf, plans = schemas(x)
        if not plans:
            continue
        b["a_retirer"] += 1
        sortie(titre(x) + (" — RETIRÉE" if retiree(x) else ""))
        for ou, plan in plans.items():
            bilan["retraits"][_decrire(plan)] += 1
            sortie(f"  {ou + ' : ' if ou else ''}{_decrire(plan)}")
        if not appliquer:
            continue
        if retiree(x):
            b["retires"].append(x["id"])
            sortie("  → NON écrite : retirée, aucune écriture ne l'accepte")
            continue
        etat, detail = ecrire(x, neuf)
        if etat == "ecrit":
            b["ecrits"] += 1
            sortie("  → écrit" + (f" — la pose dit : {detail}" if detail else ""))
        elif etat == "bouge":
            b["bouges"].append(x["id"])
            sortie("  → SAUTÉ : a changé depuis l'inventaire, relancer")
        else:
            b["refuses"].append((x["id"], detail))
            sortie(f"  → REFUSÉ : {detail}")


def _schema_du_tableau(t: dict):
    plan = retirer(t["schema"])
    return plan, ({"": plan} if not plan.vide else {})


def _slots_de(x: dict):
    slots, plans = retirer_slots(x["slots"])
    return slots, {f"slot `{k}`": v for k, v in plans.items()}


def executer(*, appliquer: bool = False, tableaux: Optional[list[int]] = None,
             procedures: Optional[list[int]] = None,
             bibliotheque: Optional[list[int]] = None, sortie=print) -> dict:
    """Le passage complet. Rend le bilan (aussi imprimé). Sans filtre, les trois
    familles ; un filtre ne parcourt que la sienne."""
    filtre = bool(tableaux or procedures or bibliotheque)
    bilan: dict = {"retraits": Counter()}
    _famille(bilan, "tableaux",
             inventaire(tableaux) if (tableaux or not filtre) else [],
             schemas=_schema_du_tableau,
             ecrire=lambda t, plan: ecrire_tableau(t, plan),
             titre=lambda t: f"ns {t['id']} « {t['namespace']} » "
                             f"({t['owner_type']} {t['owner_id']})",
             appliquer=appliquer, sortie=sortie)
    _famille(bilan, "procedures",
             inventaire_procedures(procedures) if (procedures or not filtre) else [],
             schemas=_slots_de, ecrire=ecrire_procedure,
             titre=lambda p: f"procédure {p['id']} « {p['slug']} » ({p['owner_type']} "
                             f"{p['owner_id']}, v{p['version']})",
             retiree=lambda p: bool(p["archived_at"]),
             appliquer=appliquer, sortie=sortie)
    _famille(bilan, "bibliotheque",
             inventaire_bibliotheque(bibliotheque) if (bibliotheque or not filtre)
             else [],
             schemas=_slots_de, ecrire=ecrire_entree,
             titre=lambda e: f"bibliothèque {e['id']} « {e['slug']} » "
                             f"(v{e['version']})",
             appliquer=appliquer, sortie=sortie)

    sortie("")
    sortie(f"— {'ÉCRIT' if appliquer else 'À BLANC'} : "
           + " ; ".join(f"{bilan[f]['a_retirer']} {f} à retirer sur "
                        f"{bilan[f]['parcourus']}"
                        for f in ("tableaux", "procedures", "bibliotheque")))
    for trad, n in bilan["retraits"].most_common():
        sortie(f"  {n} × {trad}")
    if appliquer:
        for f in ("tableaux", "procedures", "bibliotheque"):
            b = bilan[f]
            sortie(f"  {f} écrits : {b['ecrits']} ; sautés (bougés) : "
                   f"{len(b['bouges'])} ; refusés : {len(b['refuses'])}"
                   + (f" ; retirés (non écrits) : {len(b['retires'])}"
                      if b["retires"] else ""))
    return bilan


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--appliquer", action="store_true",
                   help="écrire (sans lui : à blanc, rien n'est écrit)")
    p.add_argument("--tableau", type=int, action="append",
                   help="limiter à ce tableau (répétable)")
    p.add_argument("--procedure", type=int, action="append",
                   help="limiter à cette procédure, par son id (répétable)")
    p.add_argument("--bibliotheque", type=int, action="append",
                   help="limiter à cette entrée de bibliothèque, par son id (répétable)")
    a = p.parse_args(argv)
    bilan = executer(appliquer=a.appliquer, tableaux=a.tableau,
                     procedures=a.procedure, bibliotheque=a.bibliotheque)
    echecs = any(bilan[f]["refuses"] or bilan[f]["bouges"]
                 for f in ("tableaux", "procedures", "bibliotheque"))
    return 1 if echecs else 0


if __name__ == "__main__":
    sys.exit(main())
