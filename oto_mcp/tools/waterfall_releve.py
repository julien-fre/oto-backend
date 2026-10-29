"""The waterfalls' COLLECT pass over a table: `waterfall_<x>_act(op="collect_rows")`.

`update_rows` with `asynchronous=true` submits each row's asynchronous step (Pipecorn's
phone and email lookups, lemlist, FullEnrich, Dropcontact, the Apollo phone reveal)
without waiting for it: the row is written `pending`, its collect call kept in
`<status_column>_job`. This pass reads those calls back:

- **One read per job.** Rows that shared a lookup share its job: read once, written to
  each. Reads are free; `PARALLELE_ASYNC` run at once, up to `LOT_ASYNC_MAX` rows a call.
- **Done with a find** → the find is written, `found`. **Done without** → the cascade
  goes on from the NEXT step (Kaspr, Lusha… after Pipecorn), in this same call; that
  continuation submits again if it reaches another asynchronous step. **Not done** →
  the row is left `pending`, counted `still_pending`.
- **A read that fails for good** (the job failed: credits ran out while it ran) puts the
  row back in the queue — status and job cleared, the next `update_rows` takes it; a
  transient failure leaves it pending.
- **A job never done** is requeued too, once it has waited `ATTENTE_MAX_S` since its
  submission (`submitted_at` in the handle): without that bound a lost job would keep its
  row `pending` for ever, and a caller looping until `remaining` is 0 would never stop.
- A guard refusal on the read (the connector deactivated for the org, the tool not in a
  hosted job's list) is read as transient (`collect_unavailable`): the row stays pending
  until the ceiling above requeues it.

Billing: a read bills nothing until the job is done; the provider's tool notes what the
finished job cost, once per job (`pipecorn`: per `enrichment_id`).
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Optional

from fastmcp import FastMCP
from starlette.concurrency import run_in_threadpool

from ..auth.hooks import current_user_sub_from_token
from ..datastore import par_reference as pr
from ..mcp_errors import McpError
from ..waterfall import appel, ordre, qualification
from ..waterfall.etape import _payload, _runnable
from ..waterfall.moteur import _run_cascade
from ..waterfall.plan import STEPS_BY_NAMESPACE
from .waterfall_ecriture import EN_ATTENTE, _Ecriture, _preparer, colonne_poignee
from .waterfall_lignes import (CASCADE_S, DEPART_S, PARALLELE, PARALLELE_ASYNC,
                               _entrees, _taille, _valider)

#: How long a submitted job may stay unfinished before its row is put back in the
#: queue (a fresh submission is the honest retry): FullEnrich takes up to ~4 min,
#: Pipecorn and lemlist far less.
ATTENTE_MAX_S = 15 * 60


def _maintenant() -> float:
    return time.time()


def _poignee(ligne: dict, colonne: str) -> Optional[dict]:
    """The row's collect call, as `update_rows` wrote it — or None."""
    v = pr.valeur(ligne, colonne)
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except ValueError:
            return None
    if isinstance(v, dict) and isinstance(v.get("provider"), str) \
            and isinstance(v.get("arguments"), dict):
        return v
    return None


async def collect_rows(mcp: FastMCP, ns: str, *, datastore, inputs: dict, output: dict,
                       status_column: str, row_ids: Optional[list],
                       filter: Optional[dict], batch_size: Optional[int],
                       providers: Optional[list[str]], limits: dict,
                       qualify: Optional[dict], appel_args: dict, dry_run: bool) -> dict:
    """The collect pass behind `waterfall_<x>_act(op="collect_rows")`."""
    kind = qualification._QUALIFIABLE.get(ns) or ns.split("_", 1)[1]
    steps = STEPS_BY_NAMESPACE[ns]
    par_cle = {s.key: s for s in steps}
    colonnes, sorties = _valider(ns, inputs, output, status_column)
    job = colonne_poignee(status_column)
    limite, plafond = _taille(batch_size, True)
    lot = await run_in_threadpool(pr.ouvrir, datastore, row_ids=row_ids, filter=filter,
                                  colonne_etat=status_column, limite=limite,
                                  etat=EN_ATTENTE, plafond=plafond)
    types, a_creer = await _preparer(lot, colonnes, sorties, status_column, dry_run,
                                     poignee=True)
    recu = pr.Recu()
    # One read per job: `job signature → (handle, inputs of its first row, [row ids])`.
    groupes: dict[str, tuple[dict, dict, list[str]]] = {}
    for ligne in lot.lignes:
        rid = str(ligne.get("_id"))
        if pr.valeur(ligne, status_column) != EN_ATTENTE:
            recu.ecarter(rid, "not_pending")        # a named row that is not waiting
            continue
        if pr.tenue_ailleurs(ligne):
            recu.ecarter(rid, "row_locked")
            continue
        poignee = _poignee(ligne, job)
        etape = par_cle.get(poignee["provider"]) if poignee else None
        if etape is None or etape.collect is None:
            recu.ecarter(rid, "no_job")              # nothing to read: `update_rows` again
            continue
        try:
            entree = _entrees(ns, {k: pr.valeur(ligne, c) for k, c in colonnes.items()},
                              appel_args)
        except McpError:
            recu.ecarter(rid, "invalid_input")
            continue
        sig = json.dumps(poignee, sort_keys=True, default=str)
        groupes.setdefault(sig, (poignee, entree, []))[2].append(rid)

    extra = {("would_create_columns" if dry_run else "created_columns"):
             [f["key"] for f in a_creer], "jobs": len(groupes),
             "written_back": {"output": sorties, "status_column": status_column,
                              "job_column": job}}
    if dry_run:
        recu.compter("would_collect", len(groupes))
        return await run_in_threadpool(
            lambda: recu.rendre(lot, selectionnees=len(lot.lignes), dry_run=True,
                                traitees=len(lot.lignes), **extra))

    sub = await run_in_threadpool(current_user_sub_from_token)
    # The order the rows were looked up in — what comes AFTER a step that found nothing.
    ordre_cles = [s.key for s in (await run_in_threadpool(
        ordre._plan, ns, steps, providers, sub))[0]]
    debut = time.monotonic()
    porte = asyncio.Semaphore(PARALLELE_ASYNC)
    # A continuation is a whole cascade (Kaspr, Lusha…): as many at once as the
    # synchronous pass, not as many as the free reads.
    suite = asyncio.Semaphore(PARALLELE)
    ecr = _Ecriture(lot, recu, kind, sorties, types, status_column, qualify)
    attente = {"rows": 0}

    async def _un(poignee: dict, entree: dict, rids: list[str]) -> None:
        etape = par_cle[poignee["provider"]]
        async with porte:
            if time.monotonic() - debut > DEPART_S:
                attente["rows"] += len(rids)
                return
            issue = await appel.collect_once(mcp, sub, etape, poignee["arguments"])
            if issue is None or (not issue.ok and issue.retryable):
                for rid in rids:      # transient: still pending, read again next call
                    recu.ecarter(rid, f"collect_{(issue and issue.code) or 'unavailable'}")
                return
            if not issue.ok:
                # The job failed for good (credits ran out while it ran): back in the
                # queue, the next `update_rows` submits it again.
                await ecr.remettre(rids, f"collect_{issue.code or 'failed'}")
                return
            if issue.retenu:
                for rid in rids:
                    recu.ecarter(rid, "redaction_withheld")
                return
            resultat = _payload(issue)
            if not etape.collect.done(resultat):
                depuis = poignee.get("submitted_at")
                if isinstance(depuis, (int, float)) and \
                        _maintenant() - depuis > ATTENTE_MAX_S:
                    await ecr.remettre(rids, "collect_timeout")
                else:
                    recu.compter("still_pending", len(rids))
                return
            trouve = etape.extract(resultat, entree)
            if trouve:
                await ecr.trouve(etape.key, trouve, rids)
                return
            # Done without a find: the cascade goes on from the next step, now.
            reste = ordre_cles[ordre_cles.index(etape.key) + 1:] \
                if etape.key in ordre_cles else []
            reste = [k for k in reste if not par_cle[k].fills]
        if not any(_runnable(par_cle[k], entree) for k in reste):
            await ecr.sans(rids)
            return
        async with suite:
            try:
                out = await _run_cascade(mcp, ns, kind, steps, entree, reste, False,
                                         limits, qualify, deadline_at=debut + CASCADE_S,
                                         submit_only=True)
            except McpError:
                await ecr.remettre(rids, "invalid_input")
                return
        await ecr.issue(out, rids, requeue=True)

    await asyncio.gather(*(_un(p, e, r) for p, e, r in groupes.values()))
    if attente["rows"]:
        recu.arret = "time_budget"
    ecr.fin(recu, extra)
    return await run_in_threadpool(
        lambda: recu.rendre(lot, selectionnees=len(lot.lignes), dry_run=False,
                            traitees=len(lot.lignes) - attente["rows"], **extra))
