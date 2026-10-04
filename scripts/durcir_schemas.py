#!/usr/bin/env python3
"""Range le vocabulaire des schémas EXISTANTS avant la fermeture (oto#34, #35, #127).

Le 01/10/2026, le vocabulaire des schémas de tableau se ferme : une clé qu'aucun niveau
n'admet (`schema_keys.ADMISES` — tête, colonne, sous-champ, élément de liste, bloc
`lifecycle`) est refusée à la pose et au patch. Le refus ne porte que sur ce qu'un
geste pose ou modifie : les clés déjà stockées restent, tolérées, nommées en `warning`.
Ce script les RANGE, une fois, pour que le parc parle le même vocabulaire que le refus.

⚠️ **La base est PARTAGÉE entre préproduction et production.** Lancé À LA MAIN, UNE
fois, depuis le commit qui l'apporte, AVANT la bascule du refus — ce n'est pas une
migration de boot. **À blanc par défaut** : sans `--appliquer`, il liste tableau par
tableau ce qu'il ferait, et n'écrit rien.

    # 1. à blanc : lire le rapport
    ./.venv/bin/python -m scripts.durcir_schemas [--tableau <ns_id> …] [--procedure <id> …]
                                                 [--entree <id> …]
    # 2. écrire
    ./.venv/bin/python -m scripts.durcir_schemas --appliquer [--tableau …] [--procedure …]
                                                 [--entree …]
    # 3. vérifier : un second passage à blanc doit annoncer 0 tableau, 0 procédure et
    #    0 entrée de bibliothèque

Trois familles de schémas, rangées par les mêmes règles :

- les **tableaux** (`user_datastores.schema`) ;
- les **schémas cibles des slots de procédure** (`org_instructions.slots[*].schema`,
  un slot `tableau` qui prescrit la forme du tableau attendu, ADR 0035 × 0046) — ce
  schéma est PROVISIONNÉ tel quel sur le tableau lié (`slots.provision_tableau_schema`),
  le laisser en l'état réintroduirait l'ancien vocabulaire dans un tableau neuf.
  Les procédures RETIRÉES (archivées) ne sont pas réécrites : aucune écriture ne les
  accepte, elles sont listées ;
- les **schémas cibles des slots des entrées de bibliothèque** (`guide_library.slots`,
  oto#34) — un fork copie ces slots TELS QUELS dans la procédure qu'il crée
  (`org_store.fork_into_org`) : les laisser en l'état réintroduirait l'ancien
  vocabulaire dans chaque procédure forkée, donc dans chaque tableau qu'elle
  provisionne.

Sans filtre, les trois familles passent ; `--tableau`, `--procedure` et `--entree`
limitent chacune la sienne, et les autres ne sont alors pas parcourues.

## Ce qu'il fait, niveau par niveau

- **textes d'aide** — `note`, `help`, `hint`, `placeholder` sont REPLIÉS dans
  `description`, dans cet ordre, séparés par un saut de ligne, une `description` déjà
  présente gardée en tête ; un texte identique à un morceau déjà là n'est pas répété ;
- **`enum`** — SUPPRIMÉE quand `options` existe déjà (c'est `options` qui fait foi ; le
  rapport dit si les deux listes étaient identiques), RENOMMÉE en `options` sinon. ⚠️
  Renommer ARME la liste sur un tableau `strict` : la pose rend alors, comme toute pose,
  le relevé des lignes en place qui la violent — le rapport le recopie ;
- **`origine`** — SUPPRIMÉE : plus aucun lecteur depuis le 08/09/2026 ;
- **`semantic_search` en tête** — SUPPRIMÉE : c'est un paramètre d'appel, déjà
  appliqué ailleurs (colonne `user_datastores.semantic_search`) ;
- **toute autre clé inconnue** — DÉPLACÉE dans `meta` au même niveau, telle quelle.
  Jamais convertie en clé active : un `read_only` reste inerte dans `meta`, il ne
  devient pas un `readonly`. Le script range, il ne devine pas une intention.

Un tableau que le rangement ne peut pas traiter sans arbitrage — `meta` déjà présent
et qui n'est pas un objet, une clé déjà présente dans `meta` avec une autre valeur,
`meta` au-delà de sa borne — n'est PAS écrit : il est listé, avec la raison.

## Écriture : le chemin normal de chaque famille

Chaque schéma s'écrit par `DatastorePg.set_schema`, le chemin de `data_set_schema` :
validation (dont le refus des clés inconnues, quand il est là), index de clé métier,
recalcul des formules, relevés. Il n'existe pas de pose de schéma « hors appel » : le
script porte donc l'autorité de la PLATEFORME par un store dédié (`_StoreSysteme`) qui
résout le tableau par son numéro, sans principal, et journalise chaque pose dans
`tool_calls` (`data_set_schema`, `sub` nul, `args.migration_systeme`). Le schéma est
relu juste avant d'écrire : s'il a bougé depuis l'inventaire, le tableau est SAUTÉ et
compté, jamais réécrit depuis une lecture périmée.

Une procédure s'écrit par `org_store.set_instruction` — le chemin de
`oto_procedure(op='set')` : une NOUVELLE VERSION, avec son instantané dans
`org_instruction_revisions` (l'historique garde l'ancienne, restaurable), le corps,
le titre et la description reconduits tels quels, `set_by` = `migration:durcir_schemas`.
Les slots rangés sont d'abord repassés par `slots.validate_slots` (la validation de
la surface — ils n'ont plus de clé inconnue, rien n'y est toléré), et l'écriture porte
`expected_version` : une procédure modifiée depuis l'inventaire est sautée, jamais
écrasée.

Une entrée de bibliothèque s'écrit par `org_store.publish_guide` — le chemin de
`library.publish` : une RE-publication par son auteur, qui incrémente `version`. Tout
le reste de l'entrée est reconduit tel quel (corps, titre, description, auteur,
visibilité, catégorie, étiquettes, provenance) ; `published_by` devient
`migration:durcir_schemas`, et l'écriture est journalisée dans `tool_calls`
(`library_publish`, `sub` nul, `args.migration_systeme`) avec la version et le
publieur d'avant. ⚠️ La bibliothèque n'a PAS de table d'historique : la version
d'avant ne survit que dans la procédure source (`source_org_id`/`source_slug`) et
dans ce journal. Les slots rangés sont validés par la publication elle-même
(`slots.validate_slots`, contre les slots en place), et l'entrée est relue juste
avant d'écrire : une entrée re-publiée depuis l'inventaire
est sautée, jamais écrasée.

Idempotent : un second passage ne trouve plus rien à ranger.
"""
from __future__ import annotations

import argparse
import copy
import sys
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Optional

from oto_mcp import calllog, db, org_store
from oto_mcp import slots as slots_mod
from oto_mcp.datastore import reglages
from oto_mcp.datastore import schema_keys as sk
from oto_mcp.datastore.core import DatastoreNotFound, DatastorePg
from oto_mcp.datastore.errors import SchemaDefinitionError
from oto_mcp.db._conn import _connect_autocommit

MIGRATION = "durcir_schemas"
#: L'auteur d'une version de procédure écrite par ce script : l'historique le nomme.
AUTEUR = f"migration:{MIGRATION}"

#: Une lecture du parc reste bornée : la base est partagée avec la production.
STATEMENT_TIMEOUT = "15s"


class Inmigrable(Exception):
    """Un rangement qui demanderait un arbitrage — le tableau est listé, jamais écrit."""


@dataclass
class Plan:
    """Ce que le rangement fait à UN schéma. Pur : calculé sans lire ni écrire."""
    schema: Any
    repliees: list = field(default_factory=list)    # (chemin, [clés])
    deplacees: list = field(default_factory=list)   # (chemin, [clés])
    supprimees: list = field(default_factory=list)  # (chemin, clé, motif)
    enum: list = field(default_factory=list)        # (chemin, cas)

    @property
    def vide(self) -> bool:
        return not (self.repliees or self.deplacees or self.supprimees or self.enum)


def _nom(chemin: str, cle: str = "") -> str:
    base = chemin or "tête"
    return f"{base}.{cle}" if cle and chemin else (f"{cle} (tête)" if cle else base)


def _replier_les_aides(noeud: dict, chemin: str, plan: Plan) -> None:
    """`note`/`help`/`hint`/`placeholder` → `description`. Seules les CHAÎNES se
    replient : une aide d'une autre forme part dans `meta` avec le reste."""
    aides = [k for k in sk.TEXTES_D_AIDE if isinstance(noeud.get(k), str)]
    desc = noeud.get("description")
    if not aides or (desc is not None and not isinstance(desc, str)):
        return
    morceaux = [desc] if desc and desc.strip() else []
    for k in aides:
        texte = noeud.pop(k)
        if texte.strip() and texte.strip() not in {m.strip() for m in morceaux}:
            morceaux.append(texte)
    if morceaux:
        noeud["description"] = "\n".join(morceaux)
    plan.repliees.append((chemin, aides))


def _ranger_enum(noeud: dict, chemin: str, plan: Plan) -> None:
    if "enum" not in noeud:
        return
    if "options" in noeud:
        enum = noeud.pop("enum")
        cas = ("supprimée — `options` présente, identique" if enum == noeud["options"]
               else f"supprimée — `options` présente et DIFFÉRENTE, elle fait foi "
                    f"(enum était {enum!r})")
    else:
        # Renommée EN PLACE : l'ordre des clés d'une colonne est celui qu'on relit.
        items = list(noeud.items())
        noeud.clear()
        noeud.update(("options" if k == "enum" else k, v) for k, v in items)
        cas = "renommée en `options`"
    plan.enum.append((chemin, cas))


def _vers_meta(noeud: dict, cles: list, chemin: str) -> None:
    meta = noeud.get(sk.META, {})
    if not isinstance(meta, dict):
        raise Inmigrable(f"{_nom(chemin, sk.META)} existe et n'est pas un objet")
    meta = dict(meta)
    for k in cles:
        if k in meta and meta[k] != noeud[k]:
            raise Inmigrable(f"{_nom(chemin, sk.META)}.{k} existe déjà avec une autre "
                             f"valeur que {_nom(chemin, k)}")
        meta[k] = noeud.pop(k)
    if sk.taille_json(meta) > sk.META_MAX_OCTETS:
        raise Inmigrable(f"{_nom(chemin, sk.META)} ferait {sk.taille_json(meta)} octets "
                         f"en JSON, au-delà de la borne ({sk.META_MAX_OCTETS})")
    noeud[sk.META] = meta


def _durcir_noeud(noeud: dict, niveau: str, chemin: str, plan: Plan) -> dict:
    noeud = dict(noeud)
    admises = sk.ADMISES[niveau]
    if "description" in admises:
        _replier_les_aides(noeud, chemin, plan)
    if "options" in admises:
        _ranger_enum(noeud, chemin, plan)
    for cle, motif in (("origine", "sans lecteur depuis le 08/09/2026"),
                       ("semantic_search", "paramètre d'appel, appliqué ailleurs")):
        if cle in noeud and (cle == "origine" or niveau == "tete"):
            del noeud[cle]
            plan.supprimees.append((chemin, cle, motif))
    # Descendre — seulement par une clé que ce niveau ADMET : un `lifecycle` posé sur
    # un sous-champ part entier dans `meta`, son contenu n'a pas de niveau.
    sous = "champ" if niveau == "tete" else "sous_champ"
    if "fields" in admises and isinstance(noeud.get("fields"), list):
        noeud["fields"] = [
            _durcir_noeud(f, sous, f"{chemin + '.' if chemin else ''}fields."
                          f"{f.get('key', '?')}", plan) if isinstance(f, dict) else f
            for f in noeud["fields"]]
    if niveau != "tete":
        if "of" in admises and isinstance(noeud.get("of"), dict):
            noeud["of"] = _durcir_noeud(noeud["of"], "element", f"{chemin}.of", plan)
        if "lifecycle" in admises and isinstance(noeud.get("lifecycle"), dict):
            noeud["lifecycle"] = _durcir_noeud(noeud["lifecycle"], "cycle",
                                               f"{chemin}.lifecycle", plan)
    # oto#127 : les anciens réglages de tête ne sont pas des clés à ranger dans
    # `meta` — ils ont un équivalent, que `renommer_reglages_tete` pose.
    reste = [k for k in noeud if k not in admises
             and not (niveau == "tete" and k in reglages.ANCIENS)]
    if reste:
        _vers_meta(noeud, reste, chemin)
        plan.deplacees.append((chemin, reste))
    return noeud


def durcir(schema: Any) -> Plan:
    """Le plan de rangement d'un schéma. Lève `Inmigrable` quand il faut arbitrer."""
    if not isinstance(schema, dict):
        return Plan(schema=schema)
    plan = Plan(schema=None)
    plan.schema = _durcir_noeud(copy.deepcopy(schema), "tete", "", plan)
    return plan


# ── lecture et écriture ──────────────────────────────────────────────────────

class _StoreSysteme(DatastorePg):
    """Le store de `data_set_schema`, avec l'autorité de la PLATEFORME : il résout un
    tableau par son numéro, sans principal, et possède le titre (un réglage d'accès
    agent ne bouge jamais ici, mais la garde ne doit pas le demander à personne)."""

    def __init__(self) -> None:
        super().__init__(None)

    def _resolve(self, datastore: str, *, write: bool = False) -> int:
        ns = db.get_datastore_by_id(int(datastore))
        if not ns:
            raise DatastoreNotFound(datastore)
        self.dernier_tableau = {"ns_id": int(ns["id"]), "datastore": ns.get("datastore")}
        return int(ns["id"])

    def _peut_forcer(self, ns_id: int) -> bool:
        return True


def inventaire(tableaux: Optional[list[int]] = None) -> list[dict]:
    """Les tableaux à schéma, lus en une requête bornée."""
    with _connect_autocommit() as conn:
        conn.execute(f"SET statement_timeout = '{STATEMENT_TIMEOUT}'")
        sql = ("SELECT id, namespace, owner_type, owner_id, schema FROM user_datastores "
               "WHERE schema IS NOT NULL")
        if tableaux:
            rows = conn.execute(sql + " AND id = ANY(%s) ORDER BY id",
                                (list(tableaux),)).fetchall()
        else:
            rows = conn.execute(sql + " ORDER BY id").fetchall()
    return [dict(r) for r in rows]


def _journaliser(t: dict, plan: Plan, ok: bool, erreur: Optional[str]) -> None:
    calllog.log_rest_call(
        "data_set_schema", sub=None, ok=ok, error=erreur,
        org_id=int(t["owner_id"]) if t["owner_type"] == "org" else None,
        args={"datastore": str(t["id"]), "migration_systeme": MIGRATION,
              "repliees": [f"{_nom(c)}: {', '.join(k)}" for c, k in plan.repliees],
              "deplacees_dans_meta": [f"{_nom(c)}: {', '.join(k)}"
                                      for c, k in plan.deplacees],
              "supprimees": [_nom(c, k) for c, k, _ in plan.supprimees],
              "enum": [f"{_nom(c)}: {cas}" for c, cas in plan.enum]})


def ecrire(t: dict, plan: Plan) -> tuple[str, Optional[str]]:
    """`("ecrit", warning)`, `("bouge", None)` ou `("refuse", raison)`."""
    en_place = (db.get_datastore_by_id(int(t["id"])) or {}).get("schema")
    if en_place != t["schema"]:
        return "bouge", None
    try:
        out = _StoreSysteme().set_schema(str(t["id"]), plan.schema)
    except (SchemaDefinitionError, ValueError) as e:
        _journaliser(t, plan, False, str(e))
        return "refuse", str(e)
    _journaliser(t, plan, True, None)
    return "ecrit", out.get("warning")


# ── les slots de procédure ───────────────────────────────────────────────────

def inventaire_procedures(procedures: Optional[list[int]] = None) -> list[dict]:
    """Les procédures dont un slot déclare un schéma cible."""
    with _connect_autocommit() as conn:
        conn.execute(f"SET statement_timeout = '{STATEMENT_TIMEOUT}'")
        sql = ("SELECT id, owner_type, owner_id, slug, version, body_md, slots, "
               "archived_at FROM org_instructions "
               "WHERE jsonb_typeof(slots) = 'array' AND EXISTS ("
               "  SELECT 1 FROM jsonb_array_elements(slots) s "
               "   WHERE jsonb_typeof(s) = 'object' AND s ? 'schema')")
        if procedures:
            rows = conn.execute(sql + " AND id = ANY(%s) ORDER BY id",
                                (list(procedures),)).fetchall()
        else:
            rows = conn.execute(sql + " ORDER BY id").fetchall()
    return [dict(r) for r in rows]


def durcir_slots(slots: list) -> tuple[list, dict]:
    """`(slots rangés, {nom du slot: Plan})` — seuls les slots qui changent sont dans
    le dict. Lève `Inmigrable` en nommant le slot."""
    neufs, plans = [], {}
    for s in slots:
        if isinstance(s, dict) and isinstance(s.get("schema"), dict):
            try:
                plan = durcir(s["schema"])
            except Inmigrable as e:
                raise Inmigrable(f"slot `{s.get('name')}` : {e}") from None
            if not plan.vide:
                plans[s.get("name")] = plan
                s = {**s, "schema": plan.schema}
        neufs.append(s)
    return neufs, plans


def ecrire_procedure(p: dict, slots: list) -> tuple[str, Optional[str]]:
    """`("ecrit", None)`, `("bouge", None)` ou `("refuse", raison)`."""
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


# ── les slots des entrées de bibliothèque ────────────────────────────────────

def inventaire_bibliotheque(entrees: Optional[list[int]] = None) -> list[dict]:
    """Les entrées de bibliothèque dont un slot déclare un schéma cible — lues par la
    VUE `guide_library`, comme tout le code de la bibliothèque."""
    with _connect_autocommit() as conn:
        conn.execute(f"SET statement_timeout = '{STATEMENT_TIMEOUT}'")
        sql = ("SELECT id, slug, title, description, body_md, slots, author_kind, "
               "author_org_id, author_display, category, tags, visibility, "
               "source_org_id, source_slug, forked_from, version, published_by "
               "FROM guide_library "
               "WHERE jsonb_typeof(slots) = 'array' AND EXISTS ("
               "  SELECT 1 FROM jsonb_array_elements(slots) s "
               "   WHERE jsonb_typeof(s) = 'object' AND s ? 'schema')")
        if entrees:
            rows = conn.execute(sql + " AND id = ANY(%s) ORDER BY id",
                                (list(entrees),)).fetchall()
        else:
            rows = conn.execute(sql + " ORDER BY id").fetchall()
    return [dict(r) for r in rows]


def _journaliser_entree(e: dict, plans: dict, ok: bool, erreur: Optional[str]) -> None:
    calllog.log_rest_call(
        "library_publish", sub=None, ok=ok, error=erreur,
        org_id=e["author_org_id"],
        args={"entree": e["id"], "slug": e["slug"], "migration_systeme": MIGRATION,
              "version_avant": e["version"], "publie_par_avant": e["published_by"],
              "slots": {nom: _lignes(plan) for nom, plan in plans.items()}})


def ecrire_entree(e: dict, slots: list, plans: dict) -> tuple[str, Optional[str]]:
    """`("ecrit", None)`, `("bouge", None)` ou `("refuse", raison)`."""
    en_place = org_store.get_library_entry(entry_id=e["id"], include_unlisted=True)
    if not en_place or (en_place["version"], en_place["slots"]) != (e["version"],
                                                                     e["slots"]):
        return "bouge", None
    try:
        # `publish_guide` valide les slots lui-même (oto#34), sous son verrou.
        org_store.publish_guide(
            slug=e["slug"], title=e["title"], description=e["description"],
            body_md=e["body_md"], author_kind=e["author_kind"],
            author_org_id=e["author_org_id"], author_display=e["author_display"],
            category=e["category"], tags=e["tags"], visibility=e["visibility"],
            source_org_id=e["source_org_id"], source_slug=e["source_slug"],
            forked_from=e["forked_from"], published_by=AUTEUR, slots=slots)
    except (org_store.LibrarySlugTaken, ValueError) as err:
        _journaliser_entree(e, plans, False, str(err))
        return "refuse", str(err)
    _journaliser_entree(e, plans, True, None)
    return "ecrit", None


# ── le rapport ───────────────────────────────────────────────────────────────

def _lignes(plan: Plan) -> list[str]:
    out = []
    if plan.repliees:
        out.append("  repliées dans description : " + " ; ".join(
            f"{_nom(c)} ({', '.join(k)})" for c, k in plan.repliees))
    if plan.deplacees:
        out.append("  rangées dans meta : " + " ; ".join(
            f"{_nom(c)} ({', '.join(k)})" for c, k in plan.deplacees))
    if plan.supprimees:
        out.append("  supprimées : " + " ; ".join(
            f"{_nom(c, k)} ({m})" for c, k, m in plan.supprimees))
    for c, cas in plan.enum:
        out.append(f"  enum : {_nom(c)} — {cas}")
    return out


def _compter(bilan: dict, plan: Plan) -> None:
    for _, cles in plan.deplacees:
        bilan["deplacees"].update(cles)
    for _, cles in plan.repliees:
        bilan["repliees"].update(cles)
    bilan["supprimees"].update(k for _, k, _ in plan.supprimees)
    bilan["enum"].update(cas.split(" (enum était")[0] for _, cas in plan.enum)


def executer(*, appliquer: bool = False, tableaux: Optional[list[int]] = None,
             procedures: Optional[list[int]] = None,
             entrees: Optional[list[int]] = None, sortie=print) -> dict:
    """Le passage complet. Rend le bilan (aussi imprimé). Sans filtre, les trois
    familles ; un filtre ne parcourt que la sienne."""
    filtre = bool(tableaux or procedures or entrees)
    parc = inventaire(tableaux) if (tableaux or not filtre) else []
    procs = inventaire_procedures(procedures) if (procedures or not filtre) else []
    biblio = inventaire_bibliotheque(entrees) if (entrees or not filtre) else []
    bilan = {"a_schema": len(parc), "a_durcir": 0, "ecrits": 0, "bouges": [],
             "refuses": [], "inmigrables": [], "deplacees": Counter(),
             "repliees": Counter(), "supprimees": Counter(), "enum": Counter(),
             "procedures_a_slots": len(procs), "procedures_a_durcir": 0,
             "procedures_ecrites": 0, "procedures_bougees": [],
             "procedures_refusees": [], "procedures_retirees": [],
             "entrees_a_slots": len(biblio), "entrees_a_durcir": 0,
             "entrees_ecrites": 0, "entrees_bougees": [], "entrees_refusees": []}
    for t in parc:
        try:
            plan = durcir(t["schema"])
        except Inmigrable as e:
            bilan["inmigrables"].append((t["id"], str(e)))
            sortie(f"ns {t['id']} « {t['namespace']} » — À ARBITRER, non écrit : {e}")
            continue
        if plan.vide:
            continue
        bilan["a_durcir"] += 1
        _compter(bilan, plan)
        sortie(f"ns {t['id']} « {t['namespace']} » ({t['owner_type']} {t['owner_id']})")
        for ligne in _lignes(plan):
            sortie(ligne)
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
            sortie(f"  → REFUSÉ par la pose : {detail}")

    for p in procs:
        nom = (f"procédure {p['id']} « {p['slug']} » ({p['owner_type']} "
               f"{p['owner_id']}, v{p['version']})")
        try:
            slots, plans = durcir_slots(p["slots"])
        except Inmigrable as e:
            bilan["inmigrables"].append((f"procédure {p['id']}", str(e)))
            sortie(f"{nom} — À ARBITRER, non écrite : {e}")
            continue
        if not plans:
            continue
        bilan["procedures_a_durcir"] += 1
        sortie(nom + (" — RETIRÉE" if p["archived_at"] else ""))
        for slot, plan in plans.items():
            _compter(bilan, plan)
            sortie(f"  slot `{slot}`")
            for ligne in _lignes(plan):
                sortie("  " + ligne)
        if not appliquer:
            continue
        if p["archived_at"]:
            bilan["procedures_retirees"].append(p["id"])
            sortie("  → NON écrite : la procédure est retirée, aucune écriture ne "
                   "l'accepte")
            continue
        etat, detail = ecrire_procedure(p, slots)
        if etat == "ecrit":
            bilan["procedures_ecrites"] += 1
            sortie(f"  → écrite en v{p['version'] + 1} (la v{p['version']} reste "
                   f"dans l'historique)")
        elif etat == "bouge":
            bilan["procedures_bougees"].append(p["id"])
            sortie("  → SAUTÉE : la procédure a changé depuis l'inventaire, relancer")
        else:
            bilan["procedures_refusees"].append((p["id"], detail))
            sortie(f"  → REFUSÉE : {detail}")

    for e in biblio:
        nom = (f"entrée de bibliothèque {e['id']} « {e['slug']} » ({e['author_kind']}"
               f"{' ' + str(e['author_org_id']) if e['author_org_id'] else ''}, "
               f"v{e['version']})")
        try:
            slots, plans = durcir_slots(e["slots"])
        except Inmigrable as err:
            bilan["inmigrables"].append((f"entrée {e['id']}", str(err)))
            sortie(f"{nom} — À ARBITRER, non écrite : {err}")
            continue
        if not plans:
            continue
        bilan["entrees_a_durcir"] += 1
        sortie(nom)
        for slot, plan in plans.items():
            _compter(bilan, plan)
            sortie(f"  slot `{slot}`")
            for ligne in _lignes(plan):
                sortie("  " + ligne)
        if not appliquer:
            continue
        etat, detail = ecrire_entree(e, slots, plans)
        if etat == "ecrit":
            bilan["entrees_ecrites"] += 1
            sortie(f"  → re-publiée en v{e['version'] + 1}")
        elif etat == "bouge":
            bilan["entrees_bougees"].append(e["id"])
            sortie("  → SAUTÉE : l'entrée a changé depuis l'inventaire, relancer")
        else:
            bilan["entrees_refusees"].append((e["id"], detail))
            sortie(f"  → REFUSÉE : {detail}")

    def _compte(c: Counter) -> str:
        return ", ".join(f"{k}×{n}" for k, n in c.most_common()) or "aucune"

    sortie("")
    sortie(f"— {'ÉCRIT' if appliquer else 'À BLANC'} : {bilan['a_durcir']} tableau(x) "
           f"à durcir sur {bilan['a_schema']} à schéma ; "
           f"{bilan['procedures_a_durcir']} procédure(s) à durcir sur "
           f"{bilan['procedures_a_slots']} à slot schématisé ; "
           f"{bilan['entrees_a_durcir']} entrée(s) de bibliothèque à durcir sur "
           f"{bilan['entrees_a_slots']} à slot schématisé")
    sortie(f"  clés rangées dans meta : {_compte(bilan['deplacees'])}")
    sortie(f"  clés repliées dans description : {_compte(bilan['repliees'])}")
    sortie(f"  clés supprimées : {_compte(bilan['supprimees'])}")
    sortie(f"  enum : {_compte(bilan['enum'])}")
    if bilan["inmigrables"]:
        sortie(f"  à arbitrer (non écrits) : {len(bilan['inmigrables'])}")
    if appliquer:
        sortie(f"  tableaux écrits : {bilan['ecrits']} ; sautés (bougés) : "
               f"{len(bilan['bouges'])} ; refusés : {len(bilan['refuses'])}")
        sortie(f"  procédures écrites : {bilan['procedures_ecrites']} ; sautées "
               f"(bougées) : {len(bilan['procedures_bougees'])} ; refusées : "
               f"{len(bilan['procedures_refusees'])} ; retirées (non écrites) : "
               f"{len(bilan['procedures_retirees'])}")
        sortie(f"  entrées de bibliothèque écrites : {bilan['entrees_ecrites']} ; "
               f"sautées (bougées) : {len(bilan['entrees_bougees'])} ; refusées : "
               f"{len(bilan['entrees_refusees'])}")
    return bilan


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--appliquer", action="store_true",
                   help="écrire (sans lui : à blanc, rien n'est écrit)")
    p.add_argument("--tableau", type=int, action="append",
                   help="limiter à ce tableau (répétable)")
    p.add_argument("--procedure", type=int, action="append",
                   help="limiter à cette procédure, par son id (répétable)")
    p.add_argument("--entree", type=int, action="append",
                   help="limiter à cette entrée de bibliothèque, par son id (répétable)")
    a = p.parse_args(argv)
    bilan = executer(appliquer=a.appliquer, tableaux=a.tableau,
                     procedures=a.procedure, entrees=a.entree)
    return 1 if (bilan["refuses"] or bilan["bouges"] or bilan["procedures_refusees"]
                 or bilan["procedures_bougees"] or bilan["entrees_refusees"]
                 or bilan["entrees_bougees"]) else 0


if __name__ == "__main__":
    sys.exit(main())
