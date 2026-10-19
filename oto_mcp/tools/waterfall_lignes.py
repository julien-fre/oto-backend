"""The waterfalls over a TABLE: `waterfall_<x>_act(op="update_rows")`.

A list was enriched one tool call per row: the agent read each row, called the
waterfall, read the find and typed it back with `data_write` — every row's data twice
through a model's context, and every turn re-sending the conversation. Here the agent
names the ROWS and the COLUMNS once; the server reads each row, runs the same cascade
(`waterfall._run_cascade`, never a second copy of it), writes the find back into the
row and answers COUNTS and CODES — never a value read or found (`par_reference`, the
same contract as `lemlist_push_rows`).

What makes it cheaper than N calls, beyond the tokens:

- **A row is paid once.** Only rows whose `status_column` is empty are taken; a row
  done (`found`), searched in vain (`no_match`) or `unqualified` keeps its status, so
  the next call takes the next rows. Clearing a status puts the row back.
- **Same inputs, one lookup.** Rows that give the same inputs (ten rows of one
  company, a contact listed twice) share ONE cascade, written to each.
- **The row's own columns are inputs.** Map every column you have: a LinkedIn URL
  opens the cheap URL-based steps.
- **Parallel**, `PARALLELE` rows at a time, inside one REST call.

⚠️ **The asynchronous steps are left out** (lemlist, FullEnrich, Dropcontact, the
Apollo phone reveal): they would be paid now and answer after the row is written, so
their find would be lost. The receipt names them (`left_out_async_steps`); for those,
the one-contact tools collect them.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Literal, Optional

from fastmcp import FastMCP
from starlette.concurrency import run_in_threadpool

from ..datastore import par_reference as pr
from ..mcp_errors import McpError
from .lecture import ACTE, documente

#: Rows judged at once. Each runs a whole cascade; providers see this many in flight.
PARALLELE = 8
#: No cascade STARTS after this many seconds — what started finishes inside the REST
#: bound (45 s): its steps stop being tried at `CASCADE_S`, as a single call's at 40.
DEPART_S = 12.0
CASCADE_S = 34.0
#: Rows per call by `filter` (each costs at least one provider call).
LOT_DEFAUT = 25

#: What a row can feed, per waterfall — the parameters of its one-contact tool.
ENTREES = {
    "waterfall_email": ("first_name", "last_name", "full_name", "domain",
                        "company_name", "linkedin_url"),
    "waterfall_phone": ("first_name", "last_name", "full_name", "domain",
                        "company_name", "linkedin_url"),
    "waterfall_company": ("company_name", "domain", "siren", "country"),
    "waterfall_person": ("first_name", "last_name", "full_name", "domain",
                         "company_name", "linkedin_url", "email"),
    "waterfall_people": ("domain", "company_name", "company_linkedin_url", "country"),
}
#: The status written on a row, so the next call never pays it again.
TROUVE, SANS, ECARTE, ECHEC = "found", "no_match", "unqualified", "failed"
#: What a find carries besides its own fields.
PROVIDER = "provider"
#: The fields whose value is a LIST: a missing column for them is declared `json`.
CHAMPS_LISTE = frozenset({"all", "people"})


def _a_declarer(sorties: dict, status_column: str, champs: dict) -> list[dict]:
    """The output and status columns the table does not declare yet — a column is
    declared before it is written (an undeclared one is refused)."""
    new = [{"key": status_column, "type": "text"}] if status_column not in champs else []
    for champ, col in sorties.items():
        if col not in champs and all(f["key"] != col for f in new):
            new.append({"key": col, "type": "json" if champ in CHAMPS_LISTE else "text"})
    return new


def _au_type(v: Any, type_: Optional[str]) -> Any:
    """A found value in the shape its column takes (the declared format is a contract)."""
    if type_ in ("json", "list"):
        return v if type_ == "json" or isinstance(v, list) else [v]
    if type_ == "number":
        if isinstance(v, bool):
            return None
        if isinstance(v, (int, float)):
            return v
        try:
            return float(str(v).replace(",", "").strip())
        except ValueError:
            return None     # « 11-50 » in a number column: left empty, never guessed
    if isinstance(v, list):
        return ", ".join(str(x) for x in v if not isinstance(x, (dict, list)))
    if isinstance(v, dict):
        return json.dumps(v, ensure_ascii=False, default=str)
    return v if isinstance(v, str) else str(v)


def _entrees(ns: str, valeurs: dict, appel: dict) -> dict:
    """A row's inputs, through the SAME builder as the one-contact tool (its checks:
    a 9-digit SIREN, a LinkedIn company page…). Raises the tool's refusal."""
    from . import waterfall as w
    v = {k: (str(x) if x is not None and not isinstance(x, str) else x)
         for k, x in valeurs.items()}
    if ns == "waterfall_company":
        return w._company_inputs(v.get("company_name"), v.get("domain"), v.get("siren"),
                                 v.get("country"))
    if ns == "waterfall_people":
        return w._people_inputs(v.get("company_name"), v.get("domain"),
                                v.get("company_linkedin_url"), appel.get("roles"),
                                v.get("country") or appel.get("country"),
                                appel.get("limit"))
    return w._person_inputs(v.get("first_name"), v.get("last_name"), v.get("full_name"),
                            v.get("domain"), v.get("company_name"), v.get("linkedin_url"),
                            v.get("email") if ns == "waterfall_person" else None)


def _cellule(kind: str, champ: str, trouve: dict, fournisseur: Optional[str]) -> Any:
    if champ == PROVIDER:
        return fournisseur
    v = trouve.get(champ)
    if kind == "people" and champ == "people" and isinstance(v, list):
        # A cell, not a dossier: who, their title, their profile — and Jev's answers.
        return [{k: x for k, x in {
            "full_name": p.get("full_name"), "title": p.get("title"),
            "linkedin_url": p.get("linkedin_url"),
            "fit": {n: a.get("noul", a.get("choice", a.get("score")))
                    for n, a in ((p.get("qualification") or {}).get("answers")
                                 or {}).items() if isinstance(a, dict)} or None,
        }.items() if x} for p in v if isinstance(p, dict)]
    return v


def _valider(ns: str, inputs: Any, output: Any, status_column: Any) -> tuple[dict, dict]:
    if not isinstance(inputs, dict) or not inputs:
        raise pr.refus("waterfall_rows_inputs",
                       "`inputs`: {input: column}, e.g. {\"linkedin_url\": \"linkedin\", "
                       "\"domain\": \"website\"}. Nothing was sent.")
    inconnues = sorted(set(inputs) - set(ENTREES[ns]))
    if inconnues:
        raise pr.refus("waterfall_rows_inputs",
                       f"`inputs`: {', '.join(inconnues)} — not an input of {ns}; one "
                       f"of {', '.join(ENTREES[ns])}. Nothing was sent.")
    entrees = pr.valider_correspondance(inputs, nom="inputs")
    if not isinstance(output, dict) or not output:
        raise pr.refus("waterfall_rows_output",
                       "`output`: {field of the find: column}, e.g. {\"email\": "
                       "\"email\", \"status\": \"email_status\", \"provider\": "
                       "\"email_source\"}. Nothing was sent.")
    sorties = pr.valider_correspondance(output, nom="output")
    if not isinstance(status_column, str) or not status_column.strip():
        raise pr.refus("waterfall_rows_status", "`status_column` is required: it is what "
                       "keeps a row from being paid twice. Nothing was sent.")
    if status_column in sorties.values():
        raise pr.refus("waterfall_rows_status", "`status_column` cannot also be an "
                       "`output` column. Nothing was sent.")
    return entrees, sorties


async def fill_rows(mcp: FastMCP, ns: str, *, datastore, inputs: dict, output: dict,
                    status_column: str, row_ids: Optional[list], filter: Optional[dict],
                    batch_size: int, providers: Optional[list[str]], limits: dict,
                    qualify: Optional[dict], appel: dict, dry_run: bool) -> dict:
    """The table pass behind the five `waterfall_<x>_act(op="update_rows")`."""
    from . import waterfall as w
    kind = w._QUALIFIABLE.get(ns) or ns.split("_", 1)[1]
    steps = w.STEPS_BY_NAMESPACE[ns]
    entrees, sorties = _valider(ns, inputs, output, status_column)
    # What can run HERE: the synchronous steps, among `providers` if given — a row is
    # judged against these, never against a step the table mode leaves out.
    voulus = {str(p).strip().lower() for p in providers or []}
    actifs = [s for s in steps if s.collect is None and (not voulus or s.key in voulus)]
    if not actifs:
        raise pr.refus("waterfall_rows_no_step",
                       "no synchronous step to run: the asynchronous ones ("
                       + ", ".join(sorted(s.key for s in steps if s.collect is not None))
                       + ") are left out on a table. Nothing was sent.")
    # The table is read (and the write right checked) off the loop: it is the database.
    lot = await run_in_threadpool(pr.ouvrir, datastore, row_ids=row_ids, filter=filter,
                                  colonne_etat=status_column, limite=batch_size)
    inconnues = await run_in_threadpool(pr.colonnes_inconnues, lot, entrees.values())
    if inconnues:
        raise pr.refus("push_rows_unknown_columns",
                       f"columns missing from the table: {', '.join(inconnues)}. "
                       "Nothing was sent.", columns=inconnues)
    from . import jev_rows as jr
    schema = await run_in_threadpool(lot.store.get_schema, lot.adresse)
    champs = jr.fields_of(schema)
    fermees = sorted(set(sorties.values()) & set(jr.closed_of(schema)))
    if fermees or status_column in jr.closed_of(schema):
        raise pr.refus("waterfall_rows_closed_columns",
                       f"columns agents cannot write: "
                       f"{', '.join(fermees or [status_column])}. Nothing was sent.")
    a_creer = _a_declarer(sorties, status_column, champs)
    if a_creer and not dry_run:
        try:
            await run_in_threadpool(lot.store.patch_schema, lot.adresse, fields=a_creer)
        except ValueError as e:
            raise pr.refus("waterfall_rows_columns",
                           f"could not declare the columns: {e}. Nothing was sent.")
    types = {**{k: f.get("type", "text") for k, f in champs.items()},
             **{f["key"]: f["type"] for f in a_creer}}

    recu = pr.Recu()
    # One lookup per distinct set of inputs: `signature → (inputs, [row ids, revision])`.
    groupes: dict[str, tuple[dict, list[str]]] = {}
    for ligne in lot.lignes:
        rid = str(ligne.get("_id"))
        if pr.tenue_ailleurs(ligne):
            recu.ecarter(rid, "row_locked")
            continue
        valeurs = {k: pr.valeur(ligne, col) for k, col in entrees.items()}
        try:
            entree = _entrees(ns, valeurs, appel)
        except McpError:
            # The row's own value is wrong (a SIREN of 8 digits…): its verdict, so it is
            # written — never the message, which can quote the value.
            recu.echec(rid, "invalid_input")
            if not dry_run:
                await run_in_threadpool(pr.ecrire, lot, rid, {
                    status_column: ECHEC, f"{status_column}.comment": "invalid_input"})
            continue
        if not any(w._runnable(s, entree) for s in actifs):
            # Not a verdict: the row waits until its inputs are filled.
            recu.ecarter(rid, "missing_input")
            continue
        sig = json.dumps(entree, sort_keys=True, default=str)
        groupes.setdefault(sig, (entree, []))[1].append(rid)

    gauche = sorted({s.key for s in steps if s.collect is not None})
    extra = {("would_create_columns" if dry_run else "created_columns"):
             [f["key"] for f in a_creer],
             "unique_lookups": len(groupes),
             "rows_sharing_a_lookup": sum(len(r) - 1 for _, r in groupes.values()),
             "left_out_async_steps": gauche,
             "written_back": {"output": sorties, "status_column": status_column}}

    if dry_run:
        plan = None
        if groupes:
            premiere = next(iter(groupes.values()))[0]
            plan = await w._run_cascade(mcp, ns, kind, steps, premiere, providers, True,
                                        limits, qualify, synchronous_only=True)
            # The plan, never its arguments: they are the row's values.
            plan = {**plan, "attempts": [
                {k: v for k, v in a.items() if k != "arguments"}
                for a in plan.get("attempts") or []]}
        recu.compter("would_look_up", len(groupes))
        # `rendre` counts what is left: the database, off the loop.
        return await run_in_threadpool(
            lambda: recu.rendre(lot, selectionnees=len(lot.lignes), dry_run=True,
                                traitees=len(lot.lignes), first_lookup_plan=plan, **extra))

    debut = time.monotonic()
    porte = asyncio.Semaphore(PARALLELE)
    vides = {champ: True for champ in sorties}
    qualif = {"calls": 0, "not_qualified": 0, "cost_usd": 0.0}
    #: Rows of `groupes` whose cascade did not START (time): left pending.
    attente = {"rows": 0}

    async def _une(sig: str, entree: dict, rids: list[str]) -> None:
        async with porte:
            if time.monotonic() - debut > DEPART_S:
                attente["rows"] += len(rids)
                return          # not started: the rows stay pending for the next call
            try:
                out = await w._run_cascade(
                    mcp, ns, kind, steps, entree, providers, False, limits, qualify,
                    deadline_at=debut + CASCADE_S, synchronous_only=True)
            except McpError:
                for rid in rids:
                    recu.echec(rid, "invalid_input")
                    await run_in_threadpool(pr.ecrire, lot, rid, {
                        status_column: ECHEC, f"{status_column}.comment": "invalid_input"})
                return
        q = out.get("qualification") or {}
        if q.get("status") == "done":
            qualif["calls"] += q.get("calls", 1)
            qualif["not_qualified"] += q.get("not_qualified") or 0
            qualif["cost_usd"] += q.get("cost_usd") or 0
        trouve = out.get(kind) if out.get("found") else None
        attempts = out.get("attempts") or []
        if trouve:
            patch = {}
            for champ, col in sorties.items():
                v = _cellule(kind, champ, trouve, out.get("provider"))
                if v not in (None, "", []):
                    v = _au_type(v, types.get(col))
                if v not in (None, "", []):
                    patch[col] = v
            for champ, col in sorties.items():
                if col in patch:
                    vides[champ] = False
            patch.update({status_column: TROUVE,
                          f"{status_column}.comment": out.get("provider")})
            cle = "found"
        elif any(a.get("status") == "unqualified" for a in attempts):
            patch, cle = {status_column: ECARTE}, "unqualified"
        elif any(a.get("status") == "skipped" and a.get("code") == "time_budget"
                 for a in attempts):
            # Out of time mid-cascade: nothing written, the next call tries it again.
            for rid in rids:
                recu.ecarter(rid, "time_budget")
            return
        else:
            # Every provider answered and none found it: a verdict, kept, so the next
            # call doesn't pay for it again.
            patch, cle = {status_column: SANS}, "no_match"
        for rid in rids:
            code = await run_in_threadpool(pr.ecrire, lot, rid, patch)
            if code:
                recu.echec(rid, f"writeback_{code}")
            else:
                recu.compter(cle)

    await asyncio.gather(*(_une(sig, e, r) for sig, (e, r) in groupes.items()))
    pas_partis = attente["rows"]
    if pas_partis or recu.comptes.get("skipped_time_budget"):
        recu.arret = "time_budget"
    if recu.comptes.get("found"):
        # A typo in `output` writes nothing, silently: named, never guessed.
        extra["output_fields_never_found"] = sorted(c for c, v in vides.items() if v)
    if qualif["calls"]:
        extra["qualification"] = {**qualif, "cost_usd": round(qualif["cost_usd"], 8)}
    return await run_in_threadpool(
        lambda: recu.rendre(lot, selectionnees=len(lot.lignes), dry_run=False,
                            traitees=len(lot.lignes) - max(pas_partis, 0), **extra))


_DOC = """Run the {label} waterfall over a TABLE's rows, on the server — op `update_rows`.
        Nothing personal goes through this call, and nothing found comes back: name the
        rows and the columns once, oto reads each row, runs the same cascade as
        `{ns}` and writes the find into the row.

        Cheaper than one call per row: only rows whose `status_column` is empty are
        taken, and each gets `found` (its provider in the status comment), `no_match`
        {unqualified}or `failed` — so a row is never paid twice; call again with the
        same arguments until `remaining` is 0 (clear a status to retry that row). Rows
        with the same inputs share ONE lookup. Up to {par} rows run in parallel; a call
        that runs out of time says `stopped: "time_budget"` and leaves the rest pending.
        ⚠️ The asynchronous steps ({asynchrones}) are left out here — they would answer
        after the row is written; the one-contact tool collects them.
        The answer is counts plus `errors: [{{row_id, code}}]`; `dry_run` reads the rows,
        counts the lookups and returns the first one's plan, calling nothing.

        Args:
            op: `update_rows`.
            datastore: the table, by name or number.
            inputs: {{input: column}} — inputs are {entrees}. Map every column
                you have: more inputs open more (and cheaper) steps.
            output: {{field of the find: column}} — {champs}, or `provider`.
            status_column: where found | no_match{unq_short} | failed is written.
            row_ids: the rows to run (at most 50). Exclusive with `filter`.
            filter: data_rows filter grammar; `{{}}` = every row not yet treated.
            batch_size: rows per call with `filter` (1-50, default {lot}).
            providers: the steps to try, in order, for these rows (see `{ns}`).
            max_paid_steps / apify_max_usd: as on `{ns}`, per row.{extra_args}
            dry_run: read and plan only — nothing called, nothing written.
        """

_CHAMPS = {
    "waterfall_email": "`email`, `status`, `all`",
    "waterfall_phone": "`phone`, `all`",
    "waterfall_company": "`name`, `legal_name`, `siren`, `domain`, `website`, "
                         "`linkedin_url`, `industry`, `employees`, `revenue_range`, "
                         "`city`, `country`, `founded`, `phone`, `description`",
    "waterfall_person": "`full_name`, `title`, `headline`, `company_name`, "
                        "`company_linkedin_url`, `linkedin_url`, `seniority`, `city`, "
                        "`country`",
    "waterfall_people": "`people` (a list: name, title, profile, Jev's answers), `count`",
}
_LABELS = {"waterfall_email": "email", "waterfall_phone": "phone",
           "waterfall_company": "company", "waterfall_person": "person",
           "waterfall_people": "people-at-a-company"}
_QUALIFY_ROW = """
            qualify / qualify_min: a Jev rubric, as on `{ns}`, judged per lookup."""
_PEOPLE_ROW = """
            roles / limit: as on `waterfall_people`, the same for every row."""


def register(mcp: FastMCP) -> None:
    from . import waterfall as w

    def _outil(ns: str):
        asynchrones = ", ".join(sorted({s.key for s in w.STEPS_BY_NAMESPACE[ns]
                                        if s.collect is not None})) or "none"
        qualifiable = ns in w._QUALIFIABLE
        doc = _DOC.format(
            label=_LABELS[ns], ns=ns, par=PARALLELE, lot=LOT_DEFAUT,
            asynchrones=asynchrones, entrees=", ".join(f"`{e}`" for e in ENTREES[ns]),
            champs=_CHAMPS[ns],
            unqualified="(`unqualified` when nobody passes `qualify_min`) "
            if ns == "waterfall_people" else "",
            unq_short=" | unqualified" if ns == "waterfall_people" else "",
            extra_args=(_QUALIFY_ROW.format(ns=ns) if qualifiable else "")
            + (_PEOPLE_ROW if ns == "waterfall_people" else ""))

        @mcp.tool(name=f"{ns}_act", annotations=ACTE)
        @documente(doc)
        async def acte(
            op: Literal["update_rows"],
            datastore: str,
            inputs: dict[str, str],
            output: dict[str, str],
            status_column: str,
            row_ids: Optional[list[str]] = None,
            filter: Optional[dict] = None,
            batch_size: int = LOT_DEFAUT,
            providers: Optional[list[str]] = None,
            max_paid_steps: Optional[int] = None, apify_max_usd: Optional[float] = None,
            qualify: Optional[dict] = None, qualify_min: Optional[float] = None,
            roles: Optional[list[str]] = None, limit: int = 10,
            dry_run: bool = False,
        ) -> dict:
            if (qualify is not None or qualify_min is not None) and not qualifiable:
                raise pr.refus("waterfall_rows_qualify",
                               f"`qualify` is not offered on {ns}: an email or a phone "
                               "has nothing to qualify. Nothing was sent.")
            if ns != "waterfall_people" and (roles or limit != 10):
                raise pr.refus("waterfall_rows_people_args",
                               "`roles` and `limit` belong to waterfall_people_act. "
                               "Nothing was sent.")
            return await fill_rows(
                mcp, ns, datastore=datastore, inputs=inputs, output=output,
                status_column=status_column, row_ids=row_ids, filter=filter,
                batch_size=batch_size, providers=providers,
                limits=w._limits(max_paid_steps, apify_max_usd),
                qualify=w._qualify_args(qualify, qualify_min) if qualifiable else None,
                appel={"roles": roles, "limit": limit}, dry_run=dry_run)

        return acte

    for ns in w.STEPS_BY_NAMESPACE:
        _outil(ns)
