"""Le manifeste d'une app : sa forme, et ce que son AUTEUR peut réellement toucher.

Une app s'exécute chez son lecteur, avec les droits du lecteur ; son manifeste dit
quels tableaux, champs et agents elle peut toucher. Le front applique ce manifeste à
chaque appel. Le serveur, ici, le juge À L'ÉCRITURE :
- **la forme** — mêmes règles que le front (`tulina-page.ts::readManifest`), pour qu'un
  agent apprenne son erreur en écrivant, pas à l'ouverture de l'app ;
- **l'auteur** — chaque tableau déclaré doit se résoudre POUR L'AUTEUR dans son org
  active, chaque champ déclaré exister dans son schéma, chaque agent exister dans
  l'org. Un éditeur ne peut donc pas déclarer un tableau qu'il ne voit pas lui-même.

Ce n'est pas encore la règle « droits = auteur ∩ lecteur » à l'APPEL (le front ne
transmet pas l'app à chaque lecture) ; c'est ce qui la rend possible : l'auteur est
connu du serveur et le manifeste qu'il pose est déjà borné par ses droits.
"""
from __future__ import annotations

import re
from typing import Any, Optional

ALIAS = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
MAX_HEIGHT = 4000
DEFAULT_HEIGHT = 480
MAX_TABLES = 20
MAX_AGENTS = 20


def _liste_de_noms(v: Any) -> Optional[list[str]]:
    if not isinstance(v, list) or any(not isinstance(x, str) or not x for x in v):
        return None
    return v


def forme(raw: Any) -> tuple[dict, list[str]]:
    """Le manifeste normalisé et la liste de ce qui ne va pas (vide = valide).
    Ne lit aucune base."""
    problems: list[str] = []
    if not isinstance(raw, dict):
        return {}, ["The manifest must be a JSON object."]
    tables: dict[str, dict] = {}
    raw_tables = raw.get("tables") or {}
    if not isinstance(raw_tables, dict):
        problems.append("`tables` must map a name to a table.")
        raw_tables = {}
    if len(raw_tables) > MAX_TABLES:
        problems.append(f"At most {MAX_TABLES} tables.")
    for alias, t in raw_tables.items():
        where = f"tables.{alias}"
        if not ALIAS.match(alias):
            problems.append(f"`{alias}` isn't a usable table name.")
            continue
        if not isinstance(t, dict):
            problems.append(f"{where} must be an object with a `table`.")
            continue
        ref = str(t["table"]) if isinstance(t.get("table"), (int, str)) and str(t.get("table")).strip() else None
        if ref is None:
            problems.append(f"{where}.table is missing.")
            continue
        fields = None
        if t.get("fields") is not None:
            fields = _liste_de_noms(t["fields"])
            if fields is None:
                problems.append(f"{where}.fields must be a list of names.")
        sets: dict[str, list[str]] = {}
        if t.get("set") is not None:
            if not isinstance(t["set"], dict):
                problems.append(f"{where}.set must map a field to its allowed values.")
            else:
                for f, vals in t["set"].items():
                    ok = _liste_de_noms(vals)
                    if ok is None:
                        problems.append(f"{where}.set.{f} must be a list of values.")
                    else:
                        sets[f] = ok
        add: list[str] = []
        if t.get("add") is not None:
            add = _liste_de_noms(t["add"]) or []
            if not add and t["add"]:
                problems.append(f"{where}.add must be a list of names.")
        tables[alias] = {"table": ref.strip(), "fields": fields, "set": sets, "add": add}
    agents: dict[str, dict] = {}
    raw_agents = raw.get("agents") or {}
    if not isinstance(raw_agents, dict):
        problems.append("`agents` must map a name to an agent id.")
        raw_agents = {}
    if len(raw_agents) > MAX_AGENTS:
        problems.append(f"At most {MAX_AGENTS} agents.")
    for alias, a in raw_agents.items():
        if not ALIAS.match(alias):
            problems.append(f"`{alias}` isn't a usable agent name.")
            continue
        entry = a if isinstance(a, dict) else {"id": a}
        try:
            aid = int(entry.get("id"))
        except (TypeError, ValueError):
            aid = 0
        if aid <= 0:
            problems.append(f"agents.{alias} must be an agent id.")
            continue
        run = entry.get("run") is True
        agents[alias] = {"id": aid, "run": run, "input": run and entry.get("input") is True}
    height = raw.get("height")
    height = min(int(height), MAX_HEIGHT) if isinstance(height, (int, float)) and height > 0 else DEFAULT_HEIGHT
    clean = {
        "title": raw.get("title") if isinstance(raw.get("title"), str) else None,
        "height": height,
        "tables": tables,
        "agents": agents,
        "files": raw.get("files") is True,
    }
    return clean, problems


def auteur(sub: str, org_id: Optional[int], clean: dict) -> list[str]:
    """Ce que l'AUTEUR ne peut pas toucher parmi ce que le manifeste déclare."""
    from .. import db
    from ..datastore.core import DatastoreAmbigu, DatastoreNotFound, make_store

    problems: list[str] = []
    store = make_store(sub)
    for alias, t in clean["tables"].items():
        try:
            store._resolve(t["table"])
            schema = store.get_schema(t["table"])
        except (DatastoreNotFound, DatastoreAmbigu):
            # Même phrase qu'un tableau absent ou invisible : rien ne dit lequel.
            problems.append(f"tables.{alias}: table `{t['table']}` isn't one you can read here.")
            continue
        declared = {f.get("key") for f in (schema or {}).get("fields") or [] if isinstance(f, dict)}
        if not declared:
            continue
        named = set(t["fields"] or []) | set(t["set"]) | set(t["add"])
        unknown = sorted(n for n in named if n not in declared)
        if unknown:
            problems.append(f"tables.{alias}: no such field: {', '.join(unknown)}.")
    for alias, a in clean["agents"].items():
        if org_id is None or db.get_trigger(a["id"], int(org_id)) is None:
            problems.append(f"agents.{alias}: agent {a['id']} doesn't exist in this organization.")
    return problems
