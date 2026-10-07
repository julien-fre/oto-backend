"""Le contrat d'une recette : ce que son corps doit dire, vérifié à l'écriture. Pur.

Une recette mal écrite se refuse quand on la propose, avec la liste de ce qui ne va
pas — jamais à la troisième page d'une exécution, quand des lignes sont déjà écrites et
des crédits déjà dépensés.

**Les noms du corps sont en anglais** : un agent les écrit et les relit, et les
erreurs qu'il reçoit les citent.
"""
from __future__ import annotations

import copy
import re
from typing import Any, Optional

from ..tool_visibility import namespace_of
from ..tools.meta import _NON_DISPATCHABLE as NAMESPACES_INTERDITS
from . import correspondance as co
from . import outils as ou

SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")
_COLONNE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,62}$")
#: Les connecteurs qui font tourner un MODÈLE : ils lisent, mais une recette est « sans
#: modèle » par contrat — un jugement par modèle sur des lignes, c'est `jev_rows`.
#: (`NAMESPACES_INTERDITS`, importé de `tools/meta` : le méta et le spine, qu'une recette
#: n'appelle jamais — elle contournerait ses propres gardes.)
NAMESPACES_A_MODELE = frozenset({"jev", "lighton"})
PAGINATIONS = ("page", "cursor", "none")
OPS_WHERE = ("eq", "ne", "in", "not_in", "contains_any", "empty", "not_empty",
             "in_table", "not_in_table")
OPS_TABLE = ("in_table", "not_in_table")
PORTEES_ARGUMENTS = {"params"}
PORTEES_ELEMENT = {"params", "item"}
#: `for_each` : chaque ligne d'un tableau PARENT déclenche l'appel ; `{{row.col}}` la cite.
PORTEE_LIGNE = "row"
MAX_PARENTS = 200
MAX_PARENTS_DEFAUT = 25
MODES = ("pull", "per_row", "push")
PORTEE_TRAVAIL = "job"
#: `async` : au-delà, un travail soumis est tenu pour perdu (`failed:timeout`) — jamais
#: resoumis : la plupart des fournisseurs factureraient deux fois.
ATTENTE_MAX = 86_400
ATTENTE_DEFAUT = 1_800
#: `per_row` : par défaut, seules les cases VIDES d'une ligne sont remplies — une valeur
#: posée par quelqu'un n'est jamais écrasée sans le dire (`update`).
EXISTANT_PAR_LIGNE = ("fill_empty", "update")
MAX_PAGES = 50
MAX_PAGES_DEFAUT = 20
#: Plafond du plafond de dépense : 50 pages de 1 000 éléments. Au-delà, ce n'est plus une
#: recette qu'on rejoue, c'est un import — et une faute de frappe d'un zéro coûte cher.
MAX_UNITS = 50_000


class RecetteInvalide(ValueError):
    """Le corps ne respecte pas le contrat. `problemes` = une phrase par défaut."""

    def __init__(self, problemes: list[str]):
        super().__init__("; ".join(problemes))
        self.problemes = problemes


def _colonne_ok(nom: Any) -> bool:
    return isinstance(nom, str) and bool(_COLONNE.match(nom)) and not nom.startswith("_")


def _portees(probs: list, ou: str, gab: Any, permises: set) -> None:
    try:
        inconnues = co.portees_citees(gab) - permises
    except co.GabaritInvalide as e:
        probs.append(f"{ou}: {e}")
        return
    if inconnues:
        probs.append(f"{ou}: unknown scope(s) {sorted(inconnues)} — allowed here: "
                     f"{sorted(permises)}")


def _source(probs: list, source: Any) -> dict:
    if source is None:
        source = {}
    if not isinstance(source, dict):
        probs.append("`source` must be an object")
        return {}
    pag = source.get("pagination") or {"type": "none"}
    if not isinstance(pag, dict) or pag.get("type") not in PAGINATIONS:
        probs.append(f"`source.pagination.type` must be one of {list(PAGINATIONS)}")
        return source
    if pag["type"] in ("page", "cursor") and not isinstance(pag.get("param"), str):
        probs.append("`source.pagination.param` (the argument that carries the page or "
                     "cursor) is required")
    if pag["type"] == "cursor" and not isinstance(pag.get("next"), str):
        probs.append("`source.pagination.next` (path to the next cursor in the result) "
                     "is required")
    if "size" in pag and (not isinstance(pag["size"], int) or pag["size"] < 1):
        probs.append("`source.pagination.size` must be a positive integer")
    if "size" in pag and not isinstance(pag.get("size_param"), str):
        probs.append("`source.pagination.size_param` is required with `size`")
    return {"items": source.get("items") or "", "pagination": pag}


def _tableau_ok(v: Any) -> bool:
    """Un tableau s'adresse par son NUMÉRO (`docs/datastore.md`)."""
    return (isinstance(v, int) and not isinstance(v, bool) and v > 0) \
        or (isinstance(v, str) and v.isdigit())


def _exigees(probs: list, ou: str, bloc: dict) -> None:
    """`require` : les colonnes qui doivent être remplies pour qu'une ligne soit prise.
    Sans lui, TOUTES les colonnes citées par les `arguments` le sont — trop strict dès
    qu'un argument est facultatif (téléphone, LinkedIn…)."""
    req = bloc.get("require")
    if req is not None and (not isinstance(req, list) or not req
                            or not all(_colonne_ok(c) for c in req)):
        probs.append(f"`{ou}.require` must be a non-empty list of column names")


def _lignes(probs: list, rows: Any) -> dict:
    """`rows` d'une recette `per_row` : quelles lignes du tableau enrichir."""
    if not isinstance(rows, dict):
        probs.append("`rows` is required in `per_row` mode: {status_column, filter, "
                     "max_rows}")
        return {}
    if not _colonne_ok(rows.get("status_column")):
        probs.append("`rows.status_column` is required: the column where each row gets "
                     "`done`, `not_found` or `failed:<code>`, so a re-run skips it")
    if rows.get("filter") is not None and not isinstance(rows["filter"], dict):
        probs.append("`rows.filter` must be an object (the grammar of `data_rows`)")
    _exigees(probs, "rows", rows)
    n = rows.setdefault("max_rows", MAX_PARENTS_DEFAUT)
    if not isinstance(n, int) or not 1 <= n <= MAX_PARENTS:
        probs.append(f"`rows.max_rows` (rows per call) must be 1 to {MAX_PARENTS}")
    return rows


def _asynchrone(probs: list, c: dict, rows: dict) -> None:
    """`async` d'une recette `per_row` : soumettre (l'outil de la recette), puis
    collecter (`collect.tool`, nommé par `recipes/outils.SOUMISSIONS`)."""
    a = c.get("async")
    if not isinstance(a, dict):
        probs.append("`async` must be an object {id, ready, collect {tool, arguments}, "
                     "max_wait_seconds}")
        return
    if c.get("tool") not in ou.SOUMISSIONS:
        probs.append(f"`async`: `{c.get('tool')}` is not a submit tool a recipe may call "
                     f"(allowed: {sorted(ou.SOUMISSIONS)})")
    collect = a.get("collect")
    if not isinstance(collect, dict) or collect.get("tool") not in ou.collecteurs(
            c.get("tool")):
        probs.append(f"`async.collect.tool` must be the collect tool of `{c.get('tool')}`: "
                     f"{sorted(ou.collecteurs(c.get('tool'))) or 'none'}")
    elif not isinstance(collect.setdefault("arguments", {}), dict):
        probs.append("`async.collect.arguments` must be an object")
    else:
        _portees(probs, "`async.collect.arguments`", collect["arguments"],
                 PORTEES_ARGUMENTS | {PORTEE_LIGNE, PORTEE_TRAVAIL})
    for champ in ("id", "ready"):
        if not isinstance(a.get(champ), str) or not a[champ]:
            probs.append(f"`async.{champ}` (path in the {'submit' if champ == 'id' else 'collect'} "
                         "reply) is required")
    keep = a.get("keep")
    if keep is not None and (not isinstance(keep, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in keep.items())):
        probs.append("`async.keep` must be {name: path}: fields of the submit reply kept "
                     "with the job, cited as {{job.name}}")
    n = a.setdefault("max_wait_seconds", ATTENTE_DEFAUT)
    if not isinstance(n, int) or not 60 <= n <= ATTENTE_MAX:
        probs.append(f"`async.max_wait_seconds` must be 60 to {ATTENTE_MAX}")
    etat = rows.get("status_column")
    col = a.setdefault("job_column", f"{etat}_job" if etat else None)
    if not _colonne_ok(col):
        probs.append("`async.job_column` must be a column name")
    if not rows.get("require"):
        probs.append("`rows.require` is required with `async`: the columns a row must "
                     "have before credits are spent on it")


def _arguments_ecrivains(probs: list, ou_: str, outil: Any, args: Any,
                         ops_permises: Optional[set] = None) -> None:
    if not isinstance(args, dict):
        probs.append(f"`{ou_}.arguments` must be an object")
        return
    _portees(probs, f"`{ou_}.arguments`", args, PORTEES_ARGUMENTS | {PORTEE_LIGNE})
    if outil not in ou.POUSSEES:
        probs.append(f"`{ou_}`: `{outil}` is not a tool a recipe may push to "
                     f"(allowed: {sorted(ou.POUSSEES)})")
        return
    if ou.POUSSEES[outil] is None:
        return
    op = args.get("op")
    if not isinstance(op, str) or co.portees_citees(op):
        probs.append(f"`{ou_}.arguments.op` must be written literally (never a template)")
    elif not ou.op_permise(outil, op) or op not in (ops_permises or ou.OPS_ECRITURE):
        probs.append(f"`{ou_}.arguments.op`: `{op}` is not allowed here for `{outil}` "
                     f"(allowed: {sorted((ops_permises or ou.OPS_ECRITURE) & (ou.POUSSEES[outil] or set()))})")


def _pousse(probs: list, c: dict) -> None:
    """Ce que `push` exige : un effet chez le tiers, donc tout se dit."""
    if c.get("side_effects") is not True:
        probs.append("`side_effects: true` is required: a `push` recipe creates or "
                     "updates records in another app")
    rows = _lignes(probs, c.get("rows"))
    if not rows.get("require"):
        probs.append("`rows.require` is required in `push`: the columns a row must have "
                     "before it is sent")
    for interdit in ("for_each", "key", "async", "pick", "map", "values"):
        if c.get(interdit) not in (None, {}):
            probs.append(f"`{interdit}` has no meaning in `push`")
    if (c["source"].get("pagination") or {}).get("type", "none") != "none":
        probs.append("`source.pagination`: a `push` call is one call per row")
    _arguments_ecrivains(probs, "push", c.get("tool"), c.get("arguments"))
    envoient = sorted(ou.outils(c) & ou.ENVOIENT)
    if envoient and c.get("allow_sending") is not True:
        probs.append(f"{envoient} can trigger a send (a lead added to a running campaign "
                     "gets its sequence): `allow_sending: true` is required")
    ident = c.get("id")
    if not isinstance(ident, dict) or not _colonne_ok(ident.get("column")) \
            or not isinstance(ident.get("path"), str):
        probs.append("`id` {column, path} is required: where the created record's id is "
                     "read in the reply, and the column it is written back to — what makes "
                     "a re-run update instead of creating twice")
    maj = c.get("update")
    if maj is not None:
        if not isinstance(maj, dict):
            probs.append("`update` must be an object {tool, arguments}")
        else:
            _arguments_ecrivains(probs, "update", maj.setdefault("tool", c.get("tool")),
                                 maj.get("arguments"))
    rech = c.get("lookup")
    if rech is not None:
        if not isinstance(rech, dict) or not isinstance(rech.get("id_path"), str) \
                or not isinstance(rech.get("items"), str) or not rech.get("items"):
            probs.append("`lookup` must be an object {tool, arguments, items, id_path}: "
                         "`items` (the path to the LIST of matches) and `id_path` are "
                         "required — a lookup that can't count its matches would create "
                         "duplicates")
        else:
            outil = rech.setdefault("tool", c.get("tool"))
            args = rech.get("arguments")
            if not isinstance(args, dict):
                probs.append("`lookup.arguments` must be an object")
            else:
                _portees(probs, "`lookup.arguments`", args,
                         PORTEES_ARGUMENTS | {PORTEE_LIGNE})
                # Une recherche : l'op `search` d'un outil de poussée, ou un outil
                # déclaré en lecture (vérifié à l'exécution, sur le catalogue servi).
                if outil in ou.POUSSEES and ou.POUSSEES[outil] is None:
                    probs.append(f"`lookup.tool`: `{outil}` has no search op — a lookup "
                                 "is a search (a read-only tool, or a push tool's `search`)")
                elif outil in ou.POUSSEES:
                    _arguments_ecrivains(probs, "lookup", outil, args, {"search"})
    if c.get("errors") is not None and not isinstance(c["errors"], str):
        probs.append("`errors` must be a path in the reply")
    c["map"], c["values"] = {}, {}
    etat = rows.get("status_column")
    if etat and isinstance(ident, dict) and etat == ident.get("column"):
        probs.append("`id.column` and `rows.status_column` must differ")


def _par_ligne(probs: list, c: dict) -> None:
    """Ce que `per_row` exige et refuse en plus du tronc commun."""
    rows = _lignes(probs, c.get("rows"))
    if c.get("async") is not None:
        _asynchrone(probs, c, rows)
    if c.get("for_each") is not None:
        probs.append("`for_each` is for `pull`; in `per_row` the rows to enrich are "
                     "chosen by `rows`")
    if c.get("key") is not None:
        probs.append("`key`: a `per_row` recipe writes back into the row it read — it "
                     "needs no key")
    if (c["source"].get("pagination") or {}).get("type", "none") != "none":
        probs.append("`source.pagination`: a `per_row` call is one call per row, never "
                     "paginated")
    if c.get("pick") not in (None, "first"):
        probs.append("`pick` can only be `first`: when the result is a list, take its "
                     "first item. Without it, a list of several items marks the row "
                     "`ambiguous`")
    c.setdefault("on_existing", "fill_empty")
    if c["on_existing"] not in EXISTANT_PAR_LIGNE:
        probs.append(f"`on_existing` must be one of {list(EXISTANT_PAR_LIGNE)} in "
                     "`per_row`")
    etat = rows.get("status_column")
    if etat and (etat in (c.get("map") or {}) or etat in (c.get("values") or {})):
        probs.append(f"`rows.status_column` (`{etat}`) cannot also be written by `map` "
                     "or `values`")


def _pour_chaque(probs: list, fe: Any) -> Optional[dict]:
    """`for_each` normalisé, ou None s'il n'est pas déclaré."""
    if fe is None:
        return None
    if not isinstance(fe, dict):
        probs.append("`for_each` must be an object {datastore, status_column, filter, "
                     "max_parents, max_items_per_row}")
        return None
    if not _tableau_ok(fe.get("datastore")):
        probs.append("`for_each.datastore` (the NUMBER of the table whose rows drive the "
                     "calls) is required")
    if not _colonne_ok(fe.get("status_column")):
        probs.append("`for_each.status_column` is required: the parent column where each "
                     "row gets `done` or `empty` once pulled, so a re-run skips it")
    if fe.get("filter") is not None and not isinstance(fe["filter"], dict):
        probs.append("`for_each.filter` must be an object (the grammar of `data_rows`)")
    _exigees(probs, "for_each", fe)
    n = fe.setdefault("max_parents", MAX_PARENTS_DEFAUT)
    if not isinstance(n, int) or not 1 <= n <= MAX_PARENTS:
        probs.append(f"`for_each.max_parents` (parent rows per call) must be 1 to "
                     f"{MAX_PARENTS}")
    m = fe.get("max_items_per_row")
    if m is not None and (not isinstance(m, int) or m < 1):
        probs.append("`for_each.max_items_per_row` must be a positive integer")
    return fe


def _clause(probs: list, i: int, w: Any, portees: set) -> None:
    if not isinstance(w, dict) or not isinstance(w.get("path"), str) \
            or w.get("op") not in OPS_WHERE:
        probs.append(f"`where[{i}]`: needs `path` and `op` in {list(OPS_WHERE)}")
        return
    if w.get("normalize") is not None and w["normalize"] not in co.NORMALISEURS:
        probs.append(f"`where[{i}].normalize` must be one of {list(co.NORMALISEURS)}")
    if w["op"] in OPS_TABLE:
        if not _tableau_ok(w.get("table")) or not _colonne_ok(w.get("column")):
            probs.append(f"`where[{i}]`: `{w['op']}` needs `table` (a table NUMBER) and "
                         "`column` (the column holding the values to match)")
    elif w["op"] in ("in", "not_in", "contains_any") and not (
            isinstance(w.get("value"), list) or co.portees_citees(w.get("value"))):
        probs.append(f"`where[{i}]`: `{w['op']}` takes a list `value`")
    else:
        _portees(probs, f"`where[{i}].value`", w.get("value"), portees)


def _correspondance(probs: list, mapping: Any, portees: set) -> None:
    if not isinstance(mapping, dict) or not mapping:
        probs.append("`map` must be a non-empty object {column: spec}")
        return
    for col, spec in mapping.items():
        if not _colonne_ok(col):
            probs.append(f"`map`: `{col}` is not a valid column name")
        if isinstance(spec, str):
            continue
        if not isinstance(spec, dict) or not ({"path", "template", "const"} & set(spec)):
            probs.append(f"`map.{col}`: a path string, or an object with `path`, "
                         "`template` or `const`")
            continue
        if "template" in spec:
            _portees(probs, f"`map.{col}.template`", spec["template"], portees)
        if "max" in spec and (not isinstance(spec["max"], int) or spec["max"] < 1):
            probs.append(f"`map.{col}.max` must be a positive integer")


def valider(corps: Any) -> dict:
    """Le corps normalisé (défauts posés), ou `RecetteInvalide` avec TOUS les défauts."""
    probs: list[str] = []
    if not isinstance(corps, dict):
        raise RecetteInvalide(["the recipe must be an object"])
    c = copy.deepcopy(corps)
    mode = c.setdefault("mode", "pull")
    if mode not in MODES:
        probs.append(f"`mode` must be one of {list(MODES)}")
    par_ligne = mode == "per_row"
    pousse = mode == "push"
    outil = c.get("tool")
    if not isinstance(outil, str) or not outil:
        probs.append("`tool` (the connector tool to call) is required")
    elif namespace_of(outil) in NAMESPACES_INTERDITS:
        probs.append(f"`tool`: `{outil}` is a platform tool — a recipe only calls "
                     "connector tools")
    elif namespace_of(outil) in NAMESPACES_A_MODELE:
        probs.append(f"`tool`: `{outil}` runs a model — a recipe never calls one (to "
                     "judge rows with a model, use `jev_rows`)")
    fe = None if (par_ligne or pousse) else _pour_chaque(probs, c.get("for_each"))
    if fe is None and not (par_ligne or pousse):
        c.pop("for_each", None)
    # `{{row.…}}` n'existe que sous `for_each`, `per_row` ou `push` : ailleurs, une faute
    # de frappe.
    avec_ligne = bool(fe) or par_ligne or pousse
    p_args = PORTEES_ARGUMENTS | ({PORTEE_LIGNE} if avec_ligne else set())
    p_elem = PORTEES_ELEMENT | ({PORTEE_LIGNE} if avec_ligne else set())
    args = c.setdefault("arguments", {})
    if not isinstance(args, dict):
        probs.append("`arguments` must be an object")
    else:
        _portees(probs, "`arguments`", args, p_args)
    params = c.setdefault("params", {})
    if not isinstance(params, dict):
        probs.append("`params` must be an object {name: {required, default}}")
    c["source"] = _source(probs, c.get("source"))
    for i, w in enumerate(c.setdefault("where", []) or []):
        _clause(probs, i, w, p_args)
    if not pousse:
        _correspondance(probs, c.get("map"), p_elem)
    valeurs = c.setdefault("values", {})
    if not isinstance(valeurs, dict):
        probs.append("`values` must be an object {column: value or template}")
    else:
        for col, gab in valeurs.items():
            if not _colonne_ok(col):
                probs.append(f"`values`: `{col}` is not a valid column name")
            _portees(probs, f"`values.{col}`", gab, p_args)
    cle = c.get("key")
    if par_ligne:
        _par_ligne(probs, c)
    elif pousse:
        _pousse(probs, c)
    elif not isinstance(cle, dict) or not _colonne_ok(cle.get("column")):
        probs.append("`key.column` (the column that identifies a row) is required")
    elif cle.get("template") is not None:
        _portees(probs, "`key.template`", cle["template"], p_elem)
    elif cle["column"] not in (c.get("map") or {}):
        probs.append("`key`: without `key.template`, `key.column` must be one of the "
                     "`map` columns")
    if not (par_ligne or pousse) and c.setdefault("on_existing", "skip") \
            not in ("skip", "update"):
        probs.append("`on_existing` must be `skip` or `update`")
    lim = c.get("limits")
    if not isinstance(lim, dict) or not isinstance(lim.get("max_units"), int) \
            or not 1 <= lim["max_units"] <= MAX_UNITS:
        probs.append(f"`limits.max_units` (a hard cap, in the tool's own billed units, 1 "
                     f"to {MAX_UNITS}) is required: there is no default, because units "
                     "differ per tool")
    else:
        pages = lim.setdefault("max_pages", MAX_PAGES_DEFAUT)
        if not isinstance(pages, int) or not 1 <= pages <= MAX_PAGES:
            probs.append(f"`limits.max_pages` must be 1 to {MAX_PAGES}")
    if c.setdefault("units", "calls" if (par_ligne or pousse) else "items") \
            not in ("items", "calls"):
        probs.append("`units` must be `items` (one unit per item returned) or `calls`")
    pag = (c.get("source") or {}).get("pagination") or {}
    if c["units"] == "items" and pag.get("type") == "page" and isinstance(pag.get("size"), int) \
            and isinstance(lim, dict) and isinstance(lim.get("max_units"), int) \
            and lim["max_units"] < pag["size"]:
        probs.append("`limits.max_units` must be at least `source.pagination.size`: a page "
                     "is never cut, so a cap below one page would never call the tool")
    if probs:
        raise RecetteInvalide(probs)
    return c


def params_resolus(corps: dict, fournis: Any) -> dict:
    """Les paramètres d'une exécution : ceux fournis, puis les défauts déclarés. Refus
    si un paramètre requis manque ou si un inconnu est passé (une faute de frappe ne
    doit pas filtrer en silence sur une valeur vide)."""
    declares = corps.get("params") or {}
    fournis = dict(fournis or {})
    probs = [f"unknown param `{k}` (declared: {sorted(declares) or 'none'})"
             for k in fournis if declares and k not in declares]
    out: dict = {}
    for nom, spec in declares.items():
        spec = spec if isinstance(spec, dict) else {}
        if nom in fournis:
            out[nom] = fournis[nom]
        elif "default" in spec:
            out[nom] = spec["default"]
        elif spec.get("required"):
            probs.append(f"missing required param `{nom}`")
    if not declares:
        out = fournis
    if probs:
        raise RecetteInvalide(probs)
    return out
