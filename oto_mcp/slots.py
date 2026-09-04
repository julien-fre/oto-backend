"""Slots de procédure (ADR 0035, B1) — déclaration d'entités requises + convention
de référence par nom dans la prose.

Une procédure (`org_instructions`) déclare ses **entités à instance** (quel tableau,
quel compte de connecteur, quelle page Documents) sous forme de **slots typés nommés** dans la
colonne JSONB `slots` : `{name, type, description?, connector?}`. La prose les
référence **par nom** via le marqueur `<slot:name>` (même famille que `<tool:slug>`
d'ADR 0014) — l'agent sait toujours de quelle entité on parle, jamais un nom
d'instance en dur (le binding nom→instance vit dans le projet, `project_links`).

B1 = canari no-op : déclaration + vérification croisée à l'écriture (`slots_check`),
AUCUN effet runtime (pas de résolution ni d'enforcement — B3). Les types réutilisent
la taxonomie `target_type` de `project_links` (sous-ensemble à instance).

« derive don't duplicate » : la logique marqueur→slot ne vit qu'ici.
"""
from __future__ import annotations

import re
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from . import providers, tool_registry
from .tool_visibility import namespace_of

# Sous-ensemble À INSTANCE de la taxonomie project_links.target_type (ADR 0035
# arbitrages) : `procedure` ne se binde pas via un slot. `doc` = une page Documents
# (repointé sur Documents le 2026-07-03).
SLOT_TYPES = ("tableau", "connecteur", "doc")

# Nom de slot = clé du binding côté projet → même hygiène qu'un slug.
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

# Convention d'écriture dans la prose (forme fixée en B1, cf. ADR 0035 §questions).
MARKER = re.compile(r"<slot:([a-z0-9_-]+)>")

_MAX_SLOTS = 32
_MAX_DESC = 500


# ── La forme d'un slot, DÉCLARÉE (#658) ──────────────────────────────────────────
# Elle était parfaitement définie — dans `validate_slots` ci-dessous, c'est-à-dire
# nulle part que le contrat sache lire. Les cinq endroits qui servaient `slots` le
# faisaient en `Optional[list]` nu, ce qui donne en OpenAPI un tableau SANS `items` :
# « une liste de n'importe quoi ». Un front qui dérive son formulaire du contrat ne
# pouvait rien en faire, alors que la donnée arrivait — d'où l'écran resté fermé.
#
# Les modèles vivent ICI, au contact du validateur qui les produit : deux domiciles
# pour une même forme, c'est la garantie qu'un champ ajouté à l'un manquera à l'autre.
# `tests/test_slots_declares.py` tient le cliquet dans l'autre sens.
#
# ⚠️ Ils DÉCRIVENT une réponse, ils ne valident aucune entrée. Les deux `Input` qui
# portent `slots` (`InstrSetInput`, `AdminInstrSetInput`) restent délibérément en
# `Optional[list]` : `validate_slots` NORMALISE (minuscules, troncature de la
# description, `connector` déduit du nom) là où un modèle REFUSERAIT, et ses messages
# d'erreur sont actionnables champ par champ. Resserrer l'entrée changerait ce que le
# serveur accepte — ce lot ne touche qu'à ce qu'il DIT.

SlotType = Literal[SLOT_TYPES]


class SlotDecl(BaseModel):
    """Une entité requise déclarée par une procédure (ADR 0035), citée `<slot:name>`
    dans la prose. C'est la sortie de `validate_slots` — donc la forme SERVIE.

    Les clés facultatives sont réellement ABSENTES quand elles ne s'appliquent pas
    (`validate_slots` ne pose jamais une clé vide) : `description` seulement si elle
    a été écrite, `connector` seulement sur un slot `connecteur`, `schema` seulement
    sur un slot `tableau`."""
    model_config = ConfigDict(populate_by_name=True)

    # Normalisé `[a-z0-9][a-z0-9_-]{0,63}`. C'est la CLÉ du binding côté projet
    # (`project_links.slot`) : c'est par ce nom, et pas par un id, que le projet dit
    # quelle instance concrète répond au slot.
    name: str
    # ⚠️ Le jeu est fermé et dérivé de `SLOT_TYPES` — jamais réécrit à la main, sinon
    # les deux listes divergent au premier type ajouté.
    #
    # ⚠️ `base` a été un type valide pendant DEUX HEURES, le 2026-07-02 (22h48 → 00h46,
    # commits 03f111eb → 2a0bb977, « B1 canari no-op ») avant d'être renommé `doc`, et
    # AUCUNE migration n'a réécrit la colonne. Une déclaration écrite dans cette
    # fenêtre serait donc servie telle quelle, hors de cet énuméré. Fenêtre mesurée,
    # risque assumé : l'énuméré vaut mieux qu'un `str` pour qui rend un formulaire.
    type: SlotType
    # Tronquée à 500 caractères à l'écriture.
    description: Optional[str] = None
    # Slots `connecteur` seulement. Le connecteur visé : `connector` explicite à
    # l'écriture, sinon le NOM du slot. Toujours présent sur un slot `connecteur`.
    connector: Optional[str] = None
    # Slots `tableau` seulement (ADR 0035 × 0046) : le schéma CIBLE du tableau attendu
    # (`fields`/`strict`/`lifecycle`/`key`). Au binding, un namespace vierge est
    # PROVISIONNÉ avec ; un namespace déjà schématisé autrement lève un warning non
    # bloquant. Le champ s'appelle `schema` sur le fil ; le nom python est décalé parce
    # que `schema` masque une méthode héritée de `BaseModel` (même parade que
    # `capabilities/datastore/schema.py`, le schéma OpenAPI étant généré `by_alias`).
    declared_schema: Optional[dict] = Field(default=None, alias="schema",
                                            serialization_alias="schema")


class SuggestedSlot(SlotDecl):
    """Un slot que la prose RÉCLAME sans qu'il soit déclaré — jamais un warning, une
    suggestion (`slots_check`). Même forme qu'une déclaration, PLUS le motif : c'est
    ce qui la distingue, et c'est pourquoi `suggested_slots` ne peut pas être typé
    `list[SlotDecl]` malgré la ressemblance."""
    # Toujours présent sur une suggestion. Rendu tel quel — c'est le texte que l'auteur
    # de la procédure lit pour comprendre ce qu'il lui manque.
    reason: str


def normalize_name(name: object) -> str:
    """Normalise un NOM de slot (clé du binding projet, ADR 0035 B2) — lower/trim,
    même hygiène que la déclaration. Lève `ValueError` actionnable si invalide."""
    n = str(name or "").strip().lower()
    if not _NAME_RE.match(n):
        raise ValueError(
            f"nom de slot invalide ({name!r}) — attendu [a-z0-9][a-z0-9_-]*, 64 car. max.")
    return n


def validate_slots(raw: object) -> list[dict]:
    """Valide et normalise une déclaration de slots. Lève `ValueError` avec un
    message ACTIONNABLE (structure, type inconnu, nom invalide/dupliqué) — les
    incohérences DOUCES (connecteur inconnu du registre, slot jamais référencé)
    sont des warnings de `slots_check`, jamais un refus (soft-binding 0014)."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("`slots` doit être une liste d'objets {name, type, description?}.")
    if len(raw) > _MAX_SLOTS:
        raise ValueError(f"`slots` : {_MAX_SLOTS} entrées max.")
    out: list[dict] = []
    seen: set[str] = set()
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"`slots[{i}]` doit être un objet {{name, type, description?}}.")
        try:
            name = normalize_name(item.get("name"))
        except ValueError as e:
            raise ValueError(f"`slots[{i}].name` : {e}")
        if name in seen:
            raise ValueError(f"`slots` : nom `{name}` dupliqué (le nom est la clé du binding).")
        seen.add(name)
        stype = str(item.get("type") or "").strip().lower()
        if stype not in SLOT_TYPES:
            raise ValueError(
                f"`slots[{i}].type` invalide ({stype!r}) — attendu {'|'.join(SLOT_TYPES)}.")
        slot: dict = {"name": name, "type": stype}
        desc = item.get("description")
        if desc:
            slot["description"] = str(desc).strip()[:_MAX_DESC]
        connector = item.get("connector")
        if connector is not None and stype != "connecteur":
            raise ValueError(f"`slots[{i}].connector` réservé au type `connecteur`.")
        if stype == "connecteur":
            # Le connecteur visé : champ `connector` explicite, sinon le nom du slot.
            slot["connector"] = str(connector or name).strip().lower()
        # Schéma CIBLE d'un slot tableau (ADR 0035 × 0046) : la procédure prescrit la
        # FORME du tableau attendu (fields/strict/lifecycle/key) — plus une prescription
        # en prose. Au binding, un namespace vierge est PROVISIONNÉ avec ce schéma ;
        # un namespace déjà schématisé différemment lève un warning (non bloquant).
        schema = item.get("schema")
        if schema is not None:
            if stype != "tableau":
                raise ValueError(f"`slots[{i}].schema` réservé au type `tableau`.")
            if not isinstance(schema, dict):
                raise ValueError(f"`slots[{i}].schema` doit être un objet schéma datastore.")
            from .datastore import schema as dsv2
            errors = dsv2.validate_schema_def(schema)
            if errors:
                raise ValueError(
                    f"`slots[{i}].schema` invalide : " + " ; ".join(errors))
            slot["schema"] = schema
        unknown = set(item) - {"name", "type", "description", "connector", "schema"}
        if unknown:
            raise ValueError(f"`slots[{i}]` : champs inconnus {sorted(unknown)}.")
        out.append(slot)
    return out


def slot_refs(text: str) -> list[str]:
    """Noms de slots cités via `<slot:name>` dans la prose, dédupliqués, dans l'ordre."""
    out: list[str] = []
    seen: set[str] = set()
    for m in MARKER.finditer(text or ""):
        n = m.group(1)
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


def _referenced_connectors(body_md: str) -> set[str]:
    """Connecteurs (noms du registre) des tools cités `<tool:slug>` dans la prose."""
    out: set[str] = set()
    for name in tool_registry.ref_names(body_md):
        con = providers.connector_for_namespace(namespace_of(name))
        if con is not None:
            out.add(con.name)
    return out


def slots_check(body_md: str, slots: Optional[list]) -> dict:
    """Vérification croisée à l'écriture (ADR 0035, pendant du `write_check` 0014).
    **Non bloquant** — l'écriture a lieu, l'auteur (IA ou UI) reçoit les signaux :
    - `unresolved_slots` : `<slot:name>` dans la prose sans déclaration (ref morte) ;
    - `unreferenced_slots` : déclaré, jamais cité dans la prose ;
    - `slot_warnings` : incohérences douces (connecteur inconnu du registre,
      connecteur déclaré dont aucun tool n'est référencé) ;
    - `suggested_slots` : connecteurs à identités référencés par `<tool:>` mais non
      déclarés (la prose trahit un besoin de binding — grandfathering, suggestion).
    Best-effort : jamais d'exception (un check ne casse pas une écriture)."""
    slots = slots or []
    declared = {s["name"] for s in slots}
    refs = slot_refs(body_md)
    result = {
        "slots": slots,
        "unresolved_slots": [n for n in refs if n not in declared],
        "unreferenced_slots": sorted(declared - set(refs)),
        "slot_warnings": [],
        "suggested_slots": [],
    }
    try:
        referenced = _referenced_connectors(body_md)
        declared_connectors: set[str] = set()
        for s in slots:
            if s["type"] != "connecteur":
                continue
            con = s.get("connector") or s["name"]
            declared_connectors.add(con)
            if con not in providers.REGISTRY:
                result["slot_warnings"].append(
                    f"slot `{s['name']}` : connecteur `{con}` inconnu du registre.")
            elif con not in referenced:
                result["slot_warnings"].append(
                    f"slot `{s['name']}` : aucun tool `<tool:{con}_…>` référencé dans la prose.")
        # L'inverse (suggestion, jamais un warning) : un tool d'un connecteur à
        # IDENTITÉS est cité sans slot connecteur déclaré → binding probablement requis.
        from .connectors import identities as connector_identities
        for con in sorted(referenced - declared_connectors):
            if connector_identities.supports(con):
                result["suggested_slots"].append(
                    {"name": con, "type": "connecteur", "connector": con,
                     "reason": f"la prose référence des tools `{con}` (connecteur à identités) "
                               "sans slot déclaré — le projet ne saura pas quel compte binder."})
    # noqa: SILENT — dette déclarée : l'avertissement de slot manquant disparaît (#424, verdict C)
    except Exception:  # noqa: BLE001 — check best-effort, jamais bloquant
        pass
    return result


# ── Schéma cible d'un slot tableau : résolution + provisionnement (0035 × 0046) ──

def target_schema_for(slot_name: str, links: list) -> Optional[dict]:
    """Schéma CIBLE du slot `slot_name` : la déclaration `schema` d'un slot tableau
    de même nom dans les procédures LIÉES au projet (première trouvée). None si
    aucune procédure liée ne prescrit ce slot."""
    from . import org_store
    for l in links or []:
        if l.get("target_type") != "procedure":
            continue
        ref = str(l.get("target_ref") or "")
        if not ref.isdigit():
            continue
        instr = org_store.get_instruction_by_id(int(ref))
        for s in (instr or {}).get("slots") or []:
            if s.get("name") == slot_name and s.get("type") == "tableau" \
                    and isinstance(s.get("schema"), dict):
                return s["schema"]
    return None


def provision_tableau_schema(ns_id: int, target: dict) -> dict:
    """Applique le schéma CIBLE d'un slot au namespace bindé (ADR 0035 × 0046) :
    - namespace SANS schéma → **provisionné** (le tableau naît avec le contrat —
      la clé métier déclarée pose aussi son index UNIQUE, sauf données déjà en
      doublon : on ne strictifie jamais des données sales en silence) ;
    - schéma déjà IDENTIQUE → no-op (`conform`) ;
    - schéma DIFFÉRENT → on ne touche à rien, warning non bloquant (`mismatch`) —
      écraser un schéma posé serait une perte silencieuse.
    Renvoie {status: provisioned|conform|mismatch|dirty_key, warning?}."""
    from . import db
    ns = db.get_datastore_namespace_by_id(int(ns_id)) or {}
    current = ns.get("schema")
    if current:
        if current == target:
            return {"status": "conform"}
        return {"status": "mismatch",
                "warning": (f"le tableau `{ns.get('namespace')}` a déjà un schéma "
                            "DIFFÉRENT du schéma cible déclaré par la procédure — "
                            "binding posé tel quel ; aligne-le via data_set_schema "
                            "si c'est voulu.")}
    key = target.get("key")
    key = key if isinstance(key, str) and key else None
    if key and db.datastore_key_dup_groups(int(ns_id), key):
        return {"status": "dirty_key",
                "warning": (f"schéma cible non provisionné : la clé `{key}` a des "
                            "doublons dans les rows existantes — résorbe-les puis "
                            "pose le schéma via data_set_schema.")}
    db.set_datastore_schema(int(ns_id), target)
    if key:
        try:
            db.datastore_ensure_key_index(int(ns_id), key)
        except db.KeyIndexUnavailable as e:
            # Même parti que `set_schema` : le schéma EST provisionné, seule la
            # contrainte anti-course manque, et la maintenance la repose.
            return {"status": "provisioned", "warning": str(e)}
    return {"status": "provisioned"}
