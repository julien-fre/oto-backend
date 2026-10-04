#!/usr/bin/env python3
"""Traduit les trois anciens réglages de tête en deux, sur les schémas EXISTANTS (oto#127).

`strict`, `unknown_fields` et `key_required` sont remplacés par `unknown_columns`
(`create` | `report` | `reject`) et `new_rows` (`create` | `reject`) — un réglage par
axe (`oto_mcp/datastore/reglages.py`, qui porte la table de traduction). Ce script
réécrit chaque schéma qui porte un ancien réglage avec son équivalent EXACT : le
comportement du tableau ne change pas, seul son vocabulaire.

⚠️ **La base est PARTAGÉE entre préproduction et production.** Lancé À LA MAIN, UNE
fois, depuis le commit qui l'apporte, une fois ce commit déployé (il admet les
nouveaux réglages et lit encore les anciens) et AVANT le commit qui refuse les anciens
noms — ce n'est pas une migration de boot. **À blanc par défaut** : sans `--appliquer`,
il liste ce qu'il ferait, et n'écrit rien.

    # 1. à blanc : lire le rapport
    ./.venv/bin/python -m scripts.renommer_reglages_tete [--tableau <ns_id> …] \\
        [--procedure <id> …] [--bibliotheque <id> …]
    # 2. écrire
    ./.venv/bin/python -m scripts.renommer_reglages_tete --appliquer [filtres…]
    # 3. vérifier : un second passage à blanc doit annoncer 0 partout

Trois familles, la même traduction (`reglages.traduire`) :

- les **tableaux** (`user_datastores.schema`), écrits par `DatastorePg.set_schema` —
  le chemin de `data_set_schema` — via le store système de `durcir_schemas`, et
  journalisés dans `tool_calls` (`args.migration_systeme`) ;
- les **schémas cibles des slots de procédure** (`org_instructions.slots[*].schema`),
  écrits par `org_store.set_instruction` : une nouvelle version, l'ancienne gardée
  dans l'historique, `expected_version` contre l'écrasement ; une procédure retirée
  est listée, jamais écrite ;
- les **entrées de la bibliothèque** (`guide_library.slots[*].schema`), que le fork
  recopie dans une procédure d'org — sans traduction, le premier fork après la bascule
  serait refusé. Écrites par `org_store.publish_guide`, le chemin de la publication,
  qui valide les slots contre ceux qu'il remplace : même slug, même auteur, tous les
  champs reconduits, une version de plus.

Un schéma qui porterait DÉJÀ un nouveau réglage contradictoire avec la traduction des
anciens est listé « à arbitrer », jamais écrit. Un schéma qui a bougé depuis
l'inventaire est sauté. Idempotent.
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

MIGRATION = "renommer_reglages_tete"
#: L'auteur d'une version de procédure ou d'entrée de bibliothèque écrite ici.
AUTEUR = f"migration:{MIGRATION}"


class Inmigrable(Exception):
    """Une traduction qui demanderait un arbitrage — listée, jamais écrite."""


@dataclass
class Plan:
    """Ce que la traduction fait à UN schéma. Pur."""
    schema: Any
    anciens: dict      # {ancien réglage: valeur}
    nouveaux: dict     # {nouveau réglage: valeur}

    @property
    def vide(self) -> bool:
        return not self.anciens


def renommer(schema: Any) -> Plan:
    """Le plan de traduction d'un schéma. Les nouveaux réglages prennent la PLACE du
    premier ancien (l'ordre des clés de tête est celui qu'on relit). Lève `Inmigrable`
    quand un nouveau réglage déjà posé contredit la traduction."""
    if not isinstance(schema, dict):
        return Plan(schema, {}, {})
    anciens = {k: schema[k] for k in schema if k in reglages.ANCIENS}
    if not anciens:
        return Plan(schema, {}, {})
    nouveaux = reglages.traduire(schema)
    for axe, v in nouveaux.items():
        if axe in schema and schema[axe] != v:
            raise Inmigrable(f"`{axe}: {schema[axe]!r}` déjà posé, alors que "
                             f"{', '.join(f'`{k}`' for k in anciens)} se traduit par "
                             f"`{axe}: {v!r}`")
    out: dict = {}
    for k, v in schema.items():
        if k in reglages.ANCIENS:
            for axe, nv in nouveaux.items():
                if axe not in out:
                    out[axe] = nv
            continue
        if k in nouveaux:
            continue          # déjà posé, identique : il garde la place des anciens
        out[k] = v
    return Plan(out, anciens, nouveaux)


def _decrire(plan: Plan) -> str:
    return (", ".join(f"{k}: {v!r}" for k, v in plan.anciens.items()) + " → "
            + ", ".join(f"{k}: {v!r}" for k, v in plan.nouveaux.items()))


def renommer_slots(slots: Any) -> tuple[list, dict]:
    """`(slots traduits, {nom du slot: Plan})` — seuls les slots qui changent sont dans
    le dict. Lève `Inmigrable` en nommant le slot."""
    neufs, plans = [], {}
    for s in slots if isinstance(slots, list) else []:
        if isinstance(s, dict) and isinstance(s.get("schema"), dict):
            try:
                plan = renommer(s["schema"])
            except Inmigrable as e:
                raise Inmigrable(f"slot `{s.get('name')}` : {e}") from None
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
              "anciens": plan.anciens, "nouveaux": plan.nouveaux})
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
    """Republie l'entrée telle quelle, slots traduits. Relue juste avant : une entrée
    republiée depuis l'inventaire est sautée, jamais écrasée. `publish_guide` valide
    les slots lui-même, sous son verrou, contre ceux qu'il remplace (oto#34) : une clé
    inconnue déjà stockée et inchangée passe, la traduction est jugée comme une pose."""
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
    b = bilan[nom] = {"parcourus": len(elements), "a_traduire": 0, "ecrits": 0,
                      "bouges": [], "refuses": [], "retires": []}
    for x in elements:
        try:
            neuf, plans = schemas(x)
        except Inmigrable as e:
            bilan["inmigrables"].append((f"{nom} {x['id']}", str(e)))
            sortie(f"{titre(x)} — À ARBITRER, non écrit : {e}")
            continue
        if not plans:
            continue
        b["a_traduire"] += 1
        sortie(titre(x) + (" — RETIRÉE" if retiree(x) else ""))
        for ou, plan in plans.items():
            bilan["traductions"][_decrire(plan)] += 1
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
    plan = renommer(t["schema"])
    return plan, ({"": plan} if not plan.vide else {})


def _slots_de(x: dict):
    slots, plans = renommer_slots(x["slots"])
    return slots, {f"slot `{k}`": v for k, v in plans.items()}


def executer(*, appliquer: bool = False, tableaux: Optional[list[int]] = None,
             procedures: Optional[list[int]] = None,
             bibliotheque: Optional[list[int]] = None, sortie=print) -> dict:
    """Le passage complet. Rend le bilan (aussi imprimé). Sans filtre, les trois
    familles ; un filtre ne parcourt que la sienne."""
    filtre = bool(tableaux or procedures or bibliotheque)
    bilan: dict = {"inmigrables": [], "traductions": Counter()}
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
           + " ; ".join(f"{bilan[f]['a_traduire']} {f} à traduire sur "
                        f"{bilan[f]['parcourus']}"
                        for f in ("tableaux", "procedures", "bibliotheque")))
    for trad, n in bilan["traductions"].most_common():
        sortie(f"  {n} × {trad}")
    if bilan["inmigrables"]:
        sortie(f"  à arbitrer (non écrits) : {len(bilan['inmigrables'])}")
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
    return 1 if echecs or bilan["inmigrables"] else 0


if __name__ == "__main__":
    sys.exit(main())
