#!/usr/bin/env python3
"""Écrit en LISTE les transitions et les terminaux de cycle de vie stockés en chaîne (oto#63).

`lifecycle.transitions: {"a": "b"}` était accepté à la pose — la boucle de contrôle
enrobait la chaîne d'une liste pour la juger — puis stocké tel quel. Le validateur
d'écriture le parcourait lettre par lettre, le dashboard appelait `.map` dessus et
levait au rendu. La pose le refuse désormais, et la lecture refuse le changement d'état
d'une ligne dont le bloc stocké est hors forme (`cycle_de_vie.table_des_transitions`).
Ce script convertit l'existant : chaque valeur chaîne (ou nombre) devient une liste à
un élément, `{"a": "b"}` → `{"a": ["b"]}`. Le graphe ne change pas, seule sa forme.

**`lifecycle.terminal`, même défaut, même conversion** (05/10/2026) : `"fait"` était
parcouru lettre par lettre à la pose, ignoré à la lecture au profit des terminaux
dérivés, et le dashboard l'appelle en `.map`. La pose le refuse et la lecture lève
(`cycle_de_vie.fautes_de_terminal`) ; `"terminal": "fait"` → `"terminal": ["fait"]`.

⚠️ **La base est PARTAGÉE entre préproduction et production.** Lancé À LA MAIN, UNE
fois, depuis le commit qui l'apporte — **avant** de taguer ce commit pour la prod : le
code d'avant lit déjà les listes, la conversion ne change rien pour lui, alors que le
code d'après refuserait l'ancienne forme. **À blanc par défaut** : sans `--appliquer`,
il liste ce qu'il ferait, et n'écrit rien.

    # 1. à blanc : lire le rapport
    ./.venv/bin/python -m scripts.transitions_en_liste [--tableau <ns_id> …] \\
        [--procedure <id> …] [--bibliotheque <id> …]
    # 2. écrire
    ./.venv/bin/python -m scripts.transitions_en_liste --appliquer [filtres…]
    # 3. vérifier : un second passage à blanc doit annoncer 0 partout

Trois familles, la même conversion (`convertir`), chacune par son chemin d'écriture
normal — mêmes chemins que `renommer_reglages_tete` :

- les **tableaux** (`user_datastores.schema`), par `DatastorePg.set_schema` via le
  store système de `durcir_schemas`, journalisés dans `tool_calls`
  (`args.migration_systeme`) ;
- les **schémas cibles des slots de procédure** (`org_instructions.slots[*].schema`),
  par `org_store.set_instruction` : une nouvelle version, l'ancienne gardée, une
  procédure retirée listée et jamais écrite ;
- les **entrées de la bibliothèque** (`guide_library.slots[*].schema`), par
  `org_store.publish_guide` — sans conversion, le premier fork serait refusé.

Chaque colonne qui porte un `lifecycle` est vue, pas seulement la file : la pose juge
la forme sur toutes. Une valeur qui n'est ni une liste, ni une chaîne, ni un nombre
(`null`, un objet) — ou une table `transitions` qui n'est pas un objet — n'a pas de
conversion évidente : listée « à arbitrer », jamais écrite. Un schéma qui a bougé
depuis l'inventaire est sauté. Idempotent.
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Optional

from oto_mcp import calllog, db, org_store
from oto_mcp import slots as slots_mod
from oto_mcp.datastore.errors import SchemaDefinitionError
from scripts.durcir_schemas import (_StoreSysteme, inventaire, inventaire_bibliotheque,
                                    inventaire_procedures)

MIGRATION = "transitions_en_liste"
#: L'auteur d'une version de procédure ou d'entrée de bibliothèque écrite ici.
AUTEUR = f"migration:{MIGRATION}"
FAMILLES = ("tableaux", "procedures", "bibliotheque")


class Inmigrable(Exception):
    """Une valeur sans conversion évidente — listée, jamais écrite."""


@dataclass
class Plan:
    """Ce que la conversion fait à UN schéma. Pur."""
    schema: Any
    converties: list = field(default_factory=list)   # ["colonne.état: 'b'"]

    @property
    def vide(self) -> bool:
        return not self.converties


def _seule(v: Any) -> bool:
    return isinstance(v, (str, int)) and not isinstance(v, bool)


def convertir(schema: Any) -> Plan:
    """Le plan de conversion d'un schéma. Lève `Inmigrable` en nommant la colonne et
    l'état quand une valeur n'a pas de conversion évidente."""
    if not isinstance(schema, dict) or not isinstance(schema.get("fields"), list):
        return Plan(schema)
    converties: list[str] = []
    champs = []
    for f in schema["fields"]:
        lc = f.get("lifecycle") if isinstance(f, dict) else None
        if not isinstance(lc, dict):
            champs.append(f)
            continue
        col = f.get("key")
        neuf = dict(lc)
        transitions = lc.get("transitions")
        if transitions is not None:
            if not isinstance(transitions, dict):
                raise Inmigrable(f"`{col}` : lifecycle.transitions vaut {transitions!r}, "
                                 f"pas un objet")
            neuves = {}
            for etat, cibles in transitions.items():
                if isinstance(cibles, list):
                    neuves[etat] = cibles
                elif _seule(cibles):
                    neuves[etat] = [cibles]
                    converties.append(f"{col}.{etat}: {cibles!r}")
                else:
                    raise Inmigrable(f"`{col}` : lifecycle.transitions[{etat!r}] vaut "
                                     f"{cibles!r}, ni liste ni état")
            neuf["transitions"] = neuves
        terminal = lc.get("terminal")
        if terminal is not None and not isinstance(terminal, list):
            if not _seule(terminal):
                raise Inmigrable(f"`{col}` : lifecycle.terminal vaut {terminal!r}, "
                                 f"ni liste ni état")
            neuf["terminal"] = [terminal]
            converties.append(f"{col}.terminal: {terminal!r}")
        champs.append({**f, "lifecycle": neuf})
    if not converties:
        return Plan(schema)
    return Plan({**schema, "fields": champs}, converties)


def convertir_slots(slots: Any) -> tuple[list, dict]:
    """`(slots convertis, {nom du slot: Plan})` — seuls les slots qui changent sont
    dans le dict. Lève `Inmigrable` en nommant le slot."""
    neufs, plans = [], {}
    for s in slots if isinstance(slots, list) else []:
        if isinstance(s, dict) and isinstance(s.get("schema"), dict):
            try:
                plan = convertir(s["schema"])
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
              "converties": plan.converties})
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
    """Republie l'entrée telle quelle, slots convertis. Relue juste avant : une entrée
    republiée depuis l'inventaire est sautée, jamais écrasée."""
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
    b = bilan[nom] = {"parcourus": len(elements), "a_convertir": 0, "ecrits": 0,
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
        b["a_convertir"] += 1
        sortie(titre(x) + (" — RETIRÉE" if retiree(x) else ""))
        for ou, plan in plans.items():
            bilan["valeurs"] += len(plan.converties)
            sortie(f"  {ou + ' : ' if ou else ''}" + ", ".join(plan.converties)
                   + " → en liste")
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
    plan = convertir(t["schema"])
    return plan, ({"": plan} if not plan.vide else {})


def _slots_de(x: dict):
    slots, plans = convertir_slots(x["slots"])
    return slots, {f"slot `{k}`": v for k, v in plans.items()}


def executer(*, appliquer: bool = False, tableaux: Optional[list[int]] = None,
             procedures: Optional[list[int]] = None,
             bibliotheque: Optional[list[int]] = None, sortie=print) -> dict:
    """Le passage complet. Rend le bilan (aussi imprimé). Sans filtre, les trois
    familles ; un filtre ne parcourt que la sienne."""
    filtre = bool(tableaux or procedures or bibliotheque)
    bilan: dict = {"inmigrables": [], "valeurs": 0}
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
           + " ; ".join(f"{bilan[f]['a_convertir']} {f} à convertir sur "
                        f"{bilan[f]['parcourus']}" for f in FAMILLES)
           + f" ({bilan['valeurs']} valeur(s) chaîne)")
    if bilan["inmigrables"]:
        sortie(f"  à arbitrer (non écrits) : {len(bilan['inmigrables'])}")
    if appliquer:
        for f in FAMILLES:
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
    echecs = any(bilan[f]["refuses"] or bilan[f]["bouges"] for f in FAMILLES)
    return 1 if echecs or bilan["inmigrables"] else 0


if __name__ == "__main__":
    sys.exit(main())
