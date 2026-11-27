"""Les RÔLES d'une app (`docs/apps.md` § Rôles d'une app) : qui, parmi ses lecteurs, voit
et fait quoi.

Le manifeste déclare ce que l'app PEUT toucher (tableaux, champs, valeurs posables,
colonnes ajoutables, agents, fichiers). Il peut en plus déclarer des **rôles** —
`roles: {admin: {…}, user: {…}}` et `default_role` — dont chacun RESSERRE ce
manifeste pour qui le tient, jamais plus :

- `tables` : absent = tous les tableaux du manifeste ; présent = ceux-là SEULS, chacun
  `true` (tel que déclaré) ou `{fields?, set?, add?}`, dont chaque clé ABSENTE hérite du
  manifeste et chaque clé PRÉSENTE en est un sous-ensemble (`set: {}` et `add: []` :
  aucune écriture) ;
- `agents` : absent = ceux du manifeste ; présent = une liste de noms, ou
  `{nom: true | {run?, input?}}` (`run: false` : lire ses runs sans le lancer) ;
- `files` : absent = comme le manifeste ; `false` = ni dépôt ni liste de fichiers ;
- `sections` : des noms libres. Le HTML marque ses parties (`data-oto-section="nom"`) ;
  servi à qui tient le rôle (`op=get`), il perd au serveur tout élément marqué d'une
  section que le rôle n'ouvre pas (`apps_sections.py`), et l'app lit les siennes par
  `oto.access()`. ⚠️ Seul le balisage MARQUÉ est retiré : un script non marqué part
  entier. Les données des tableaux, elles, sont protégées par le manifeste resserré.

**Où le rôle s'applique** : une seule fois, à l'entrée de l'appel d'une app ouverte
(`apps_appel._app_pour`) — le manifeste servi à TOUTE action (lectures, lot, agrégats,
gestes, consentement, fichiers, runs) est déjà le manifeste resserré, rien d'autre
n'a à le savoir. `op=get` sert ce même manifeste resserré, sans la définition des
rôles.

**Qui tient quel rôle** : le rôle d'app d'un partage (`resource_grants.app_role`, posé
par qui gouverne l'app, `oto_apps op=set_role`) ; le partage nominatif d'abord, puis
d'équipe, puis d'org — à type égal, le premier rôle dans l'ordre du manifeste. Sans
rôle posé (ou un rôle que le manifeste ne déclare plus) : `default_role`, OBLIGATOIRE
dès qu'un rôle est déclaré — jamais un repli sur le manifeste entier. **Qui peut
modifier l'app** (propriétaire, Edit, Manage) n'est borné par aucun rôle : il réécrirait
le manifeste. Un éditeur VOIT l'app comme un rôle par `as_role` sur l'appel.

Le rôle ne remplace ni View/Use (cliquer reste Use), ni la règle auteur ∩ lecteur :
il ne fait que retirer.
"""
from __future__ import annotations

from typing import Any, Optional

MAX_ROLES = 20
MAX_SECTIONS = 50
MAX_LABEL = 80


def _noms(v: Any) -> Optional[list[str]]:
    if not isinstance(v, list) or any(not isinstance(x, str) or not x for x in v):
        return None
    return v


def _table_du_role(where: str, spec: Any, t: dict, problems: list[str]) -> Optional[dict]:
    """La spécification d'un tableau dans un rôle, normalisée (clés présentes seules)."""
    if spec is True:
        return {}
    if not isinstance(spec, dict):
        problems.append(f"{where} must be true or an object with fields / set / add.")
        return None
    out: dict = {}
    if "fields" in spec:
        f = _noms(spec["fields"])
        if f is None:
            problems.append(f"{where}.fields must be a list of names.")
        else:
            if t.get("fields") is not None:
                trop = sorted(set(f) - set(t["fields"]))
                if trop:
                    problems.append(f"{where}.fields: not declared by the app: {', '.join(trop)}.")
            out["fields"] = f
    if "set" in spec:
        if not isinstance(spec["set"], dict):
            problems.append(f"{where}.set must map a field to its allowed values.")
        else:
            sets: dict[str, list[str]] = {}
            for champ, vals in spec["set"].items():
                ok = _noms(vals)
                permis = (t.get("set") or {}).get(champ)
                if ok is None:
                    problems.append(f"{where}.set.{champ} must be a list of values.")
                elif permis is None:
                    problems.append(f"{where}.set.{champ}: the app can't set this field.")
                elif set(ok) - set(permis):
                    problems.append(f"{where}.set.{champ}: values the app can't set: "
                                    f"{', '.join(sorted(set(ok) - set(permis)))}.")
                else:
                    sets[champ] = ok
            out["set"] = sets
    if "add" in spec:
        a = _noms(spec["add"]) if spec["add"] else []
        if a is None:
            problems.append(f"{where}.add must be a list of names.")
        else:
            trop = sorted(set(a) - set(t.get("add") or []))
            if trop:
                problems.append(f"{where}.add: columns the app can't fill: {', '.join(trop)}.")
            out["add"] = a
    inconnues = sorted(set(spec) - {"fields", "set", "add"})
    if inconnues:
        problems.append(f"{where}: unknown keys: {', '.join(inconnues)}.")
    return out


def forme(raw: dict, clean: dict, alias_ok) -> list[str]:
    """Normalise `raw.roles` / `raw.default_role` dans `clean` (déjà normalisé pour ses
    tableaux et agents) et rend ce qui ne va pas. Sans rôles : rien n'est posé."""
    problems: list[str] = []
    raw_roles = raw.get("roles")
    if raw_roles is None or raw_roles == {}:
        if raw.get("default_role") is not None:
            problems.append("`default_role` needs `roles`.")
        return problems
    if not isinstance(raw_roles, dict):
        return ["`roles` must map a role name to what it opens."]
    if len(raw_roles) > MAX_ROLES:
        problems.append(f"At most {MAX_ROLES} roles.")
    roles: dict[str, dict] = {}
    for key, r in raw_roles.items():
        where = f"roles.{key}"
        if not alias_ok(key):
            problems.append(f"`{key}` isn't a usable role name.")
            continue
        if not isinstance(r, dict):
            problems.append(f"{where} must be an object.")
            continue
        role: dict = {}
        label = r.get("label")
        role["label"] = (label.strip()[:MAX_LABEL] if isinstance(label, str) and label.strip()
                         else key)
        if "tables" in r:
            if not isinstance(r["tables"], dict):
                problems.append(f"{where}.tables must map a table name to true or an object.")
            else:
                tables: dict[str, dict] = {}
                for alias, spec in r["tables"].items():
                    t = clean["tables"].get(alias)
                    if t is None:
                        problems.append(f"{where}.tables.{alias}: the app declares no such table.")
                        continue
                    n = _table_du_role(f"{where}.tables.{alias}", spec, t, problems)
                    if n is not None:
                        tables[alias] = n
                role["tables"] = tables
        if "agents" in r:
            ra = r["agents"]
            if isinstance(ra, list):
                ra = {a: True for a in ra if isinstance(a, str)} if _noms(ra) is not None else None
            if not isinstance(ra, dict):
                problems.append(f"{where}.agents must be a list of agent names or an object.")
            else:
                agents: dict[str, dict] = {}
                for alias, spec in ra.items():
                    a = clean["agents"].get(alias)
                    if a is None:
                        problems.append(f"{where}.agents.{alias}: the app declares no such agent.")
                        continue
                    if spec is True:
                        agents[alias] = {}
                    elif isinstance(spec, dict):
                        n = {}
                        for k in ("run", "input"):
                            if k in spec:
                                if not isinstance(spec[k], bool):
                                    problems.append(f"{where}.agents.{alias}.{k} must be true or false.")
                                elif spec[k] and not a.get(k):
                                    problems.append(f"{where}.agents.{alias}.{k}: the app doesn't "
                                                    f"allow it for this agent.")
                                else:
                                    n[k] = spec[k]
                        agents[alias] = n
                    else:
                        problems.append(f"{where}.agents.{alias} must be true or an object.")
                role["agents"] = agents
        if "files" in r:
            if not isinstance(r["files"], bool):
                problems.append(f"{where}.files must be true or false.")
            elif r["files"] and not clean.get("files"):
                problems.append(f"{where}.files: the app takes no files.")
            else:
                role["files"] = r["files"]
        if "sections" in r:
            s = _noms(r["sections"])
            if s is None or len(s) > MAX_SECTIONS or any(len(x) > 64 for x in s):
                problems.append(f"{where}.sections must be a list of at most {MAX_SECTIONS} "
                                "short names.")
            else:
                role["sections"] = s
        inconnues = sorted(set(r) - {"label", "tables", "agents", "files", "sections"})
        if inconnues:
            problems.append(f"{where}: unknown keys: {', '.join(inconnues)}.")
        roles[key] = role
    defaut = raw.get("default_role")
    if not isinstance(defaut, str) or defaut not in raw_roles:
        problems.append("`default_role` must name one of `roles`: it is what a person "
                        "without a role of their own gets.")
    clean["roles"] = roles
    # L'ordre des rôles tel qu'ÉCRIT : JSONB ne garde pas l'ordre des clés d'un objet,
    # et c'est lui qui départage deux partages de même type (`role_du_lecteur`).
    clean["role_order"] = list(roles)
    clean["default_role"] = defaut if isinstance(defaut, str) else None
    return problems


def ordre(manifest: dict) -> list[str]:
    """Les rôles déclarés, dans l'ordre où le manifeste les a écrits (`role_order`)."""
    roles = manifest.get("roles") or {}
    ecrits = [k for k in (manifest.get("role_order") or []) if k in roles]
    return ecrits + [k for k in roles if k not in ecrits]


def champs_nommes(clean: dict) -> dict[str, set[str]]:
    """Par tableau, les champs que les rôles nomment — à juger contre le schéma quand le
    manifeste n'a pas de liste de champs (`apps_manifeste.auteur`)."""
    out: dict[str, set[str]] = {}
    for r in (clean.get("roles") or {}).values():
        for alias, spec in (r.get("tables") or {}).items():
            out.setdefault(alias, set()).update(spec.get("fields") or [])
    return out


def resserrer(manifest: dict, role: Optional[str]) -> dict:
    """Le manifeste vu par qui tient `role` (None = le manifeste entier), SANS la
    définition des rôles. Ne fait que retirer."""
    m = {k: v for k, v in manifest.items() if k not in ("roles", "role_order", "default_role")}
    r = (manifest.get("roles") or {}).get(role) if role else None
    if r is None:
        return m
    tables = manifest.get("tables") or {}
    if "tables" in r:
        tables = {a: {**tables[a], **spec} for a, spec in r["tables"].items() if a in tables}
    m["tables"] = tables
    agents = manifest.get("agents") or {}
    if "agents" in r:
        out = {}
        for a, spec in r["agents"].items():
            if a not in agents:
                continue
            run = bool(agents[a].get("run")) and spec.get("run", True)
            # Passer un texte n'existe qu'en lançant : sans `run`, pas d'`input`.
            out[a] = {**agents[a], "run": run,
                      "input": run and bool(agents[a].get("input")) and spec.get("input", True)}
        agents = out
    m["agents"] = agents
    m["files"] = bool(manifest.get("files")) and r.get("files", True)
    return m


def role_du_lecteur(sub: str, app: dict) -> Optional[str]:
    """Le rôle qui borne `sub` dans `app` (manifeste servi), None = aucun : l'app ne
    déclare pas de rôle, ou `sub` peut la modifier."""
    from .. import db, ownership
    from .apps import APP_RTYPE

    roles = (app.get("manifest") or {}).get("roles") or {}
    if not roles:
        return None
    rid = str(app["id"])
    if ownership.can_access(sub, APP_RTYPE, rid, "write"):
        return None
    poses = db.apps.app_roles_of(rid, list(ownership.accessor_scope(sub).principal_pairs()))
    rang = ordre(app["manifest"])
    for ptype in ("user", "group", "org"):
        tenus = [r for t, r in poses if t == ptype and r in roles]
        if tenus:
            return min(tenus, key=rang.index)
    return app["manifest"].get("default_role")


def acces(manifest: dict, role: Optional[str]) -> dict:
    """Ce que l'app LIT de son lecteur (`oto.access()`) : son rôle et ses sections.
    `role` None = tout (aucune section n'est retirée : `sections` None)."""
    r = (manifest.get("roles") or {}).get(role) if role else None
    if r is None:
        return {"role": None, "role_label": None, "sections": None}
    return {"role": role, "role_label": r.get("label") or role, "sections": r.get("sections")}
