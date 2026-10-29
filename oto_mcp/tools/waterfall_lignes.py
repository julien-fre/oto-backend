"""The waterfalls over a TABLE: `waterfall_<x>_act(op="update_rows")`.

A list was enriched one tool call per row: the agent read each row, called the
waterfall, read the find and typed it back with `data_write` — every row's data twice
through a model's context, and every turn re-sending the conversation. Here the agent
names the ROWS and the COLUMNS once; the server reads each row, runs the same cascade
(`waterfall.moteur._run_cascade`, never a second copy of it), writes the find back into the
row and answers COUNTS and CODES — never a value read or found (`par_reference`, the
same contract as `lemlist_push_rows`).

What makes it cheaper than N calls, beyond the tokens:

- **A row is paid once.** Only rows whose `status_column` is empty are taken; a row
  done (`found`), searched in vain (`no_match`) or `unqualified` keeps its status, so
  the next call takes the next rows. Clearing a status puts the row back.
- **A failure is not a verdict.** `no_match` is written only when a step ANSWERED
  without finding and none failed; a row whose steps failed, or never ran, stays empty
  — the receipt names its code, and the next call tries it again.
- **Same inputs, one lookup.** Rows that give the same inputs (ten rows of one
  company, a contact listed twice) share ONE cascade, written to each.
- **The row's own columns are inputs.** Map every column you have: a LinkedIn URL
  opens the cheap URL-based steps.
- **Parallel**, `PARALLELE` rows at a time, inside one REST call.

⚠️ **The asynchronous steps are left out by default** (lemlist, FullEnrich,
Dropcontact, the Apollo phone reveal): they would be paid now and answer after the row
is written. The receipt names them (`left_out_async_steps`). A step with a synchronous
twin (`Step.sync`: Pipecorn's phone and email) runs as that twin (`etape.for_table`).
**With `asynchronous=true`** they are SUBMITTED, not waited for: the row is written
`pending` with its collect call (`<status_column>_job`), and `collect_rows`
(`waterfall_releve.py`) reads it back — the way to hundreds of rows in a few calls.
What the passes write on a row lives in `waterfall_ecriture.py`, one copy.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Literal, Optional

from fastmcp import FastMCP
from starlette.concurrency import run_in_threadpool

from .. import outils_du_travail
from ..auth.hooks import current_user_sub_from_token
from ..datastore import par_reference as pr
from ..mcp_errors import McpError
from ..waterfall import entrees, qualification
from ..waterfall.etape import _runnable, for_table
from ..waterfall.moteur import _limits, _run_cascade
from ..waterfall.plan import STEPS_BY_NAMESPACE
from .lecture import ACTE, documente
from .waterfall_ecriture import (ECHEC, EN_ATTENTE, _Ecriture, _preparer,
                                 colonne_poignee)

#: Rows judged at once. Each runs a whole cascade; providers see this many in flight.
PARALLELE = 8
#: No cascade STARTS after this many seconds — what started finishes inside the REST
#: bound (45 s): its steps stop being tried at `CASCADE_S`, as a single call's at 40.
DEPART_S = 12.0
CASCADE_S = 34.0
#: Rows per call by `filter` (each costs at least one provider call).
LOT_DEFAUT = 25
#: The ASYNCHRONOUS passes (`update_rows` with `asynchronous`, `collect_rows`): each row
#: costs one brief call — a submission, a free read — so a call takes more rows, more at
#: once. Hundreds of rows go in a few calls: the provider works on them in parallel.
PARALLELE_ASYNC = 16
LOT_ASYNC_DEFAUT = 100
LOT_ASYNC_MAX = 200

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
    "waterfall_tech": ("domain", "company_name"),
}
def _entrees(ns: str, valeurs: dict, appel: dict) -> dict:
    """A row's inputs, through the SAME builder as the one-contact tool (its checks:
    a 9-digit SIREN, a LinkedIn company page…). Raises the tool's refusal."""
    v = {k: (str(x) if x is not None and not isinstance(x, str) else x)
         for k, x in valeurs.items()}
    if ns == "waterfall_company":
        return entrees._company_inputs(v.get("company_name"), v.get("domain"),
                                       v.get("siren"), v.get("country"))
    if ns == "waterfall_tech":
        return entrees._tech_inputs(v.get("company_name"), v.get("domain"),
                                    appel.get("technologies"))
    if ns == "waterfall_people":
        return entrees._people_inputs(v.get("company_name"), v.get("domain"),
                                      v.get("company_linkedin_url"), appel.get("roles"),
                                      v.get("country") or appel.get("country"),
                                      appel.get("limit"))
    return entrees._person_inputs(v.get("first_name"), v.get("last_name"),
                                  v.get("full_name"), v.get("domain"),
                                  v.get("company_name"), v.get("linkedin_url"),
                                  v.get("email") if ns == "waterfall_person" else None)


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
    colonnes = pr.valider_correspondance(inputs, nom="inputs")
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
    return colonnes, sorties


def _planifies(plan: Optional[dict]) -> list[str]:
    """Les outils des étapes qu'un `dry_run` dit `planned` — variantes comprises."""
    noms: list[str] = []
    for a in (plan or {}).get("attempts") or []:
        for p in [a, *(a.get("tried") or [])]:
            if p.get("status") == "planned" and isinstance(p.get("tool"), str):
                noms.append(p["tool"])
    return noms


def _outils_a_donner(refuses: list[str], *, dry_run: bool = False) -> dict:
    """Sync (base) : ce que le reçu dit des étapes refusées au travail — les outils à
    donner à l'agent et le geste qui les lui donne, la forme du refus d'une recette-code
    (`missing_tools`, `fix`, `recipes/code.refus_hors_liste`). Les autres étapes ont
    tourné : le reçu reste celui d'un passage, pas un refus. `fix` est un geste de
    personne (`tools_human_only`) ; `next_step` dit à l'agent de s'arrêter en les nommant,
    pas de conclure à un quota.

    `dry_run` : les étapes PLANIFIÉES que le travail refusera. Le plan les disait
    `planned` sans un mot — c'est ce qui a fait conclure un agent à une panne de fournisseur
    (vécu le 26/10/2026) : le plan mentait, l'agent a cru le plan."""
    manque = outils_du_travail.manquants(refuses)
    if not manque:
        return {}
    noms = manque["missing"]
    agent = f"agent {manque['trigger_id']}" if manque.get("trigger_id") else "this agent"
    sort = ("will be skipped on the real call" if dry_run
            else "were skipped, and their rows stay pending")
    return {"missing_tools": noms,
            "fix": outils_du_travail.correctif(manque.get("trigger_id"),
                                               manque.get("tools") or [], noms),
            "next_step": (f"{outils_du_travail.CODE}: the steps that call {noms} {sort}, "
                          f"because {agent} is not allowed to use them (job "
                          f"{manque['job_id']}). This is not a quota or a key. Stop with "
                          "run_finish(outcome=\"blocked\") naming them: the owner adds "
                          f"them to {agent}'s tools in one click (the agent's page shows "
                          "them).")}


def _taille(batch_size: Optional[int], asynchrone: bool) -> tuple[int, int]:
    """`(rows this call, ceiling)`: an asynchronous pass costs one brief call per row
    (a submission, a free read), so it takes more rows than a synchronous one."""
    if asynchrone:
        return (LOT_ASYNC_DEFAUT if batch_size is None else batch_size), LOT_ASYNC_MAX
    return (LOT_DEFAUT if batch_size is None else batch_size), pr.MAX_LIGNES


async def fill_rows(mcp: FastMCP, ns: str, *, datastore, inputs: dict, output: dict,
                    status_column: str, row_ids: Optional[list], filter: Optional[dict],
                    batch_size: Optional[int], providers: Optional[list[str]],
                    limits: dict, qualify: Optional[dict], appel: dict, dry_run: bool,
                    asynchronous: bool = False) -> dict:
    """The table pass behind the `waterfall_<x>_act(op="update_rows")`. With
    `asynchronous`, an asynchronous step is SUBMITTED, not waited for: the row is
    written `pending` with its collect call, read later by `collect_rows`."""
    kind = qualification._QUALIFIABLE.get(ns) or ns.split("_", 1)[1]
    steps = STEPS_BY_NAMESPACE[ns]
    colonnes, sorties = _valider(ns, inputs, output, status_column)
    # What can run HERE: the synchronous steps (or every step, asynchronous ones
    # submitted, with `asynchronous`), among `providers` if given — a row is judged
    # against these, never against a step the table mode leaves out.
    voulus = {str(p).strip().lower() for p in providers or []}
    inconnus = sorted(voulus - {s.key for s in steps})
    if inconnus:
        # Refused once, here: inside the pass it would fail every row in turn.
        raise pr.refus("waterfall_rows_providers",
                       f"`providers`: {', '.join(inconnus)} — not a step of {ns}; one of "
                       f"{', '.join(s.key for s in steps)}. Nothing was sent.")
    candidats = steps if asynchronous else [t for t in (for_table(s) for s in steps)
                                            if t is not None]
    actifs = [t for t in candidats if not voulus or t.key in voulus]
    if not actifs:
        raise pr.refus("waterfall_rows_no_step",
                       "no synchronous step to run: the asynchronous ones ("
                       + ", ".join(sorted(s.key for s in steps if for_table(s) is None))
                       + ") are left out on a table — pass `asynchronous=true` to submit "
                       "them and `collect_rows` to read them. Nothing was sent.")
    if qualify:
        # Jev unusable: refused before a row is read — never rows written unjudged.
        sub = await run_in_threadpool(current_user_sub_from_token)
        await run_in_threadpool(qualification._require_qualification, sub, ns)
    limite, plafond = _taille(batch_size, asynchronous)
    # The table is read (and the write right checked) off the loop: it is the database.
    lot = await run_in_threadpool(pr.ouvrir, datastore, row_ids=row_ids, filter=filter,
                                  colonne_etat=status_column, limite=limite,
                                  plafond=plafond)
    types, a_creer = await _preparer(lot, colonnes, sorties, status_column, dry_run,
                                     poignee=asynchronous)

    recu = pr.Recu()
    # One lookup per distinct set of inputs: `signature → (inputs, [row ids, revision])`.
    groupes: dict[str, tuple[dict, list[str]]] = {}
    for ligne in lot.lignes:
        rid = str(ligne.get("_id"))
        if pr.tenue_ailleurs(ligne):
            recu.ecarter(rid, "row_locked")
            continue
        valeurs = {k: pr.valeur(ligne, col) for k, col in colonnes.items()}
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
        if not any(_runnable(s, entree) for s in actifs):
            # Not a verdict: the row waits until its inputs are filled.
            recu.ecarter(rid, "missing_input")
            continue
        sig = json.dumps(entree, sort_keys=True, default=str)
        groupes.setdefault(sig, (entree, []))[1].append(rid)

    gauche = [] if asynchronous else sorted({s.key for s in steps if for_table(s) is None})
    extra = {("would_create_columns" if dry_run else "created_columns"):
             [f["key"] for f in a_creer],
             "unique_lookups": len(groupes),
             "rows_sharing_a_lookup": sum(len(r) - 1 for _, r in groupes.values()),
             "left_out_async_steps": gauche,
             "written_back": {"output": sorties, "status_column": status_column,
                              **({"job_column": colonne_poignee(status_column)}
                                 if asynchronous else {})}}

    if dry_run:
        plan = None
        if groupes:
            premiere = next(iter(groupes.values()))[0]
            plan = await _run_cascade(mcp, ns, kind, steps, premiere, providers, True,
                                      limits, qualify, synchronous_only=not asynchronous)
            # The plan, never its arguments: they are the row's values.
            plan = {**plan, "attempts": [
                {k: v for k, v in a.items() if k != "arguments"}
                for a in plan.get("attempts") or []]}
        recu.compter("would_look_up", len(groupes))
        if plan is not None:
            extra.update(await run_in_threadpool(
                lambda: _outils_a_donner(_planifies(plan), dry_run=True)))
        # `rendre` counts what is left: the database, off the loop.
        return await run_in_threadpool(
            lambda: recu.rendre(lot, selectionnees=len(lot.lignes), dry_run=True,
                                traitees=len(lot.lignes), first_lookup_plan=plan, **extra))

    debut = time.monotonic()
    porte = asyncio.Semaphore(PARALLELE_ASYNC if asynchronous else PARALLELE)
    ecr = _Ecriture(lot, recu, kind, sorties, types, status_column, qualify)
    #: Rows of `groupes` whose cascade did not START (time): left pending.
    attente = {"rows": 0}

    async def _une(sig: str, entree: dict, rids: list[str]) -> None:
        async with porte:
            if time.monotonic() - debut > DEPART_S:
                attente["rows"] += len(rids)
                return          # not started: the rows stay pending for the next call
            try:
                out = await _run_cascade(
                    mcp, ns, kind, steps, entree, providers, False, limits, qualify,
                    deadline_at=debut + CASCADE_S, synchronous_only=not asynchronous,
                    submit_only=asynchronous)
            except McpError:
                for rid in rids:
                    recu.echec(rid, "invalid_input")
                    await run_in_threadpool(pr.ecrire, lot, rid, {
                        status_column: ECHEC, f"{status_column}.comment": "invalid_input"})
                return
        await ecr.issue(out, rids)

    await asyncio.gather(*(_une(sig, e, r) for sig, (e, r) in groupes.items()))
    pas_partis = attente["rows"]
    if pas_partis or recu.comptes.get("skipped_time_budget"):
        recu.arret = "time_budget"
    ecr.fin(recu, extra)
    if ecr.refuses:
        extra.update(await run_in_threadpool(_outils_a_donner, ecr.refuses))
    if recu.comptes.get("submitted"):
        extra["next_step"] = (f"{recu.comptes['submitted']} row(s) submitted, written "
                              f"`{EN_ATTENTE}`: call op=collect_rows with the same "
                              "datastore, inputs, output and status_column in a minute, "
                              "then again until its `remaining` is 0.")
    return await run_in_threadpool(
        lambda: recu.rendre(lot, selectionnees=len(lot.lignes), dry_run=False,
                            traitees=len(lot.lignes) - max(pas_partis, 0), **extra))


_DOC = """Run the {label} waterfall over a TABLE's rows, on the server — ops `update_rows`
        and `collect_rows`.
        Nothing personal goes through this call, and nothing found comes back: name the
        rows and the columns once, oto reads each row, runs the same cascade as
        `{ns}` and writes the find into the row.

        Cheaper than one call per row: only rows whose `status_column` is empty are
        taken, and each gets `found` (its provider in the status comment), `no_match`
        {unqualified}or `failed` — so a row is never paid twice; call again with the
        same arguments until `remaining` is 0 (clear a status to retry that row).
        `no_match` is written only when a provider answered without finding and none
        failed: a row whose steps failed or never ran stays empty, its code in `errors`. Rows
        with the same inputs share ONE lookup. Up to {par} rows run in parallel; a call
        that runs out of time says `stopped: "time_budget"` and leaves the rest pending.
        Asynchronous steps ({asynchrones}): left out by default (they answer after the
        row is written) — except where a synchronous twin exists (Pipecorn's phone and
        email run synchronously). With `asynchronous=true` they are SUBMITTED instead,
        not waited for: the row is written `pending`, its collect call in
        `<status_column>_job` — up to {lot_async_max} rows a call, {par_async} at once,
        so hundreds of rows go in a few calls. Then op `collect_rows` (same datastore,
        inputs, output, status_column) reads the pending rows back — free reads, one per
        job: a find is written `found`; a job done without one goes on to the next steps
        in the same call; one not done yet stays `pending`. Call it until its
        `remaining` is 0.
        The answer is counts plus `errors: [{{row_id, code}}]`; `dry_run` reads the rows,
        counts the lookups and returns the first one's plan, calling nothing.
        From a hosted agent, a step whose tool the agent may not call is skipped and
        its rows stay pending (`tool_not_in_job`, not a quota): the answer then names
        them (`missing_tools`) with the owner's one-click `fix`.

        Args:
            op: `update_rows` (look up the rows not yet treated) or `collect_rows` (read
                back the rows an asynchronous `update_rows` left `pending`).
            datastore: the table, by name or number.
            inputs: {{input: column}} — inputs are {entrees}. Map every column
                you have: more inputs open more (and cheaper) steps.
            output: {{field of the find: column}} — {champs}, or `provider`.
            status_column: where found | no_match{unq_short} | failed is written.
            row_ids: the rows to run (at most 50). Exclusive with `filter`.
            filter: data_rows filter grammar; `{{}}` = every row not yet treated.
            batch_size: rows per call with `filter` — 1-50 (default {lot}); with
                `asynchronous` or `collect_rows`, 1-{lot_async_max} (default {lot_async}).
            asynchronous: `update_rows` only — submit the asynchronous steps instead
                of leaving them out (or running their synchronous twin), for
                `collect_rows` to read.
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
    "waterfall_tech": "`technologies`, `count`, `source` (website | vendor_database | "
                      "job_postings), `by_category`, `matched` (with `technologies`)",
}
_LABELS = {"waterfall_email": "email", "waterfall_phone": "phone",
           "waterfall_company": "company", "waterfall_person": "person",
           "waterfall_people": "people-at-a-company", "waterfall_tech": "tech stack"}
_QUALIFY_ROW = """
            qualify / qualify_min: a Jev rubric, as on `{ns}`, judged per lookup."""
_PEOPLE_ROW = """
            roles / people_per_row: as `roles` / `limit` on `waterfall_people`, the
                same for every row (default 10)."""
_TECH_ROW = """
            technologies: as on `waterfall_tech`, the same for every row — a row is
                `found` only when one of them is detected, `no_match` when none is."""


def register(mcp: FastMCP) -> None:
    """Mounts the six `<ns>_act` — the second module of the waterfall connectors
    (`Connector.modules=("waterfall", "waterfall_lignes")`), loaded once."""

    def _outil(ns: str):
        asynchrones = ", ".join(sorted({s.key for s in STEPS_BY_NAMESPACE[ns]
                                        if s.collect is not None})) or "none"
        qualifiable = ns in qualification._QUALIFIABLE
        doc = _DOC.format(
            label=_LABELS[ns], ns=ns, par=PARALLELE, lot=LOT_DEFAUT,
            par_async=PARALLELE_ASYNC, lot_async=LOT_ASYNC_DEFAUT,
            lot_async_max=LOT_ASYNC_MAX,
            asynchrones=asynchrones, entrees=", ".join(f"`{e}`" for e in ENTREES[ns]),
            champs=_CHAMPS[ns],
            unqualified="(`unqualified` when nobody passes `qualify_min`) "
            if ns == "waterfall_people" else "",
            unq_short=" | unqualified" if ns == "waterfall_people" else "",
            extra_args=(_QUALIFY_ROW.format(ns=ns) if qualifiable else "")
            + (_PEOPLE_ROW if ns == "waterfall_people" else "")
            + (_TECH_ROW if ns == "waterfall_tech" else ""))

        @mcp.tool(name=f"{ns}_act", annotations=ACTE)
        @documente(doc)
        async def acte(
            op: Literal["update_rows", "collect_rows"],
            datastore: str,
            inputs: dict[str, str],
            output: dict[str, str],
            status_column: str,
            row_ids: Optional[list[str]] = None,
            filter: Optional[dict] = None,
            batch_size: Optional[int] = None,
            providers: Optional[list[str]] = None,
            max_paid_steps: Optional[int] = None, apify_max_usd: Optional[float] = None,
            qualify: Optional[dict] = None, qualify_min: Optional[float] = None,
            roles: Optional[list[str]] = None, people_per_row: int = 10,
            technologies: Optional[list[str]] = None,
            dry_run: bool = False,
            asynchronous: bool = False,
        ) -> dict:
            if (qualify is not None or qualify_min is not None) and not qualifiable:
                raise pr.refus("waterfall_rows_qualify",
                               f"`qualify` is not offered on {ns}: an email or a phone "
                               "has nothing to qualify. Nothing was sent.")
            if ns != "waterfall_people" and (roles or people_per_row != 10):
                raise pr.refus("waterfall_rows_people_args",
                               "`roles` and `people_per_row` belong to "
                               "waterfall_people_act. "
                               "Nothing was sent.")
            if ns != "waterfall_tech" and technologies:
                raise pr.refus("waterfall_rows_tech_args",
                               "`technologies` belongs to waterfall_tech_act. "
                               "Nothing was sent.")
            qualif = (qualification._qualify_args(qualify, qualify_min)
                      if qualifiable else None)
            appel = {"roles": roles, "limit": people_per_row, "technologies": technologies}
            if op == "collect_rows":
                if asynchronous:
                    raise pr.refus("waterfall_rows_collect_args",
                                   "`asynchronous` belongs to update_rows: collect_rows "
                                   "reads what it submitted. Nothing was sent.")
                from .waterfall_releve import collect_rows
                return await collect_rows(
                    mcp, ns, datastore=datastore, inputs=inputs, output=output,
                    status_column=status_column, row_ids=row_ids, filter=filter,
                    batch_size=batch_size, providers=providers,
                    limits=_limits(max_paid_steps, apify_max_usd), qualify=qualif,
                    appel_args=appel, dry_run=dry_run)
            return await fill_rows(
                mcp, ns, datastore=datastore, inputs=inputs, output=output,
                status_column=status_column, row_ids=row_ids, filter=filter,
                batch_size=batch_size, providers=providers,
                limits=_limits(max_paid_steps, apify_max_usd), qualify=qualif,
                appel=appel, dry_run=dry_run, asynchronous=asynchronous)

        return acte

    for ns in STEPS_BY_NAMESPACE:
        _outil(ns)
