"""Writing a waterfall's outcome on a TABLE's rows — what `update_rows` (synchronous
and submit passes, `waterfall_lignes`) and `collect_rows` (`waterfall_releve`) write:
the find in the output columns, the row's status (`found`, `no_match`, `unqualified`,
`failed`, `pending` with its collect call), or nothing when the outcome is not a verdict.
ONE copy, so the three passes cannot drift apart."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from starlette.concurrency import run_in_threadpool

from .. import outils_du_travail
from ..datastore import par_reference as pr

#: The status written on a row, so the next call never pays it again.
TROUVE, SANS, ECARTE, ECHEC = "found", "no_match", "unqualified", "failed"
#: A row submitted to an asynchronous step, its answer not read yet (`collect_rows`).
EN_ATTENTE = "pending"


def colonne_poignee(status_column: str) -> str:
    """The column that keeps a pending row's collect call: `<status_column>_job`."""
    return f"{status_column}_job"
#: What a find carries besides its own fields.
PROVIDER = "provider"
#: The fields whose value is a LIST: a missing column for them is declared `json`.
CHAMPS_LISTE = frozenset({"all", "people", "technologies", "matched", "by_category"})


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


def _hors_liste(attempts: list[dict]) -> list[str]:
    """Les outils des étapes refusées parce qu'un travail hébergé ne les a pas dans sa
    liste (`tool_not_in_job`) — variantes comprises. Une telle étape est SAUTÉE par la
    cascade (`appel._call`), comme une garde : sans ce relevé, la ligne revenait
    `no_step_answered`, et l'agent concluait à un quota épuisé (vécu le 26/10/2026)."""
    noms: list[str] = []
    for a in attempts:
        for p in [a, *(a.get("tried") or [])]:
            if (p.get("code") == outils_du_travail.CODE and isinstance(p.get("tool"), str)
                    and p["tool"] not in noms):
                noms.append(p["tool"])
    return noms


def _pas_un_verdict(out: dict, trouve: Any, attempts: list[dict],
                   qualify: Optional[dict]) -> Optional[str]:
    """Why a cascade's outcome is NOT a verdict to write on its row — None when it is: a
    find Jev was asked to judge and did not; a miss where no step ANSWERED without
    finding, or one failed (a refused key, a provider error, a withheld result), or a
    limit cut the cascade — the row is left to retry."""
    if trouve:
        q = out.get("qualification")
        if qualify and (q or {}).get("status") != "done":
            return "qualification_skipped"
        return None
    if any(a.get("status") == "unqualified" for a in attempts):
        return None
    if any(a.get("status") == "skipped" and a.get("code") == "time_budget"
           for a in attempts):
        return "time_budget"
    if _hors_liste(attempts):
        # Un fournisseur n'a pas tourné parce que l'agent n'a pas son outil : un `no_match`
        # écrit ici ne serait jamais repris une fois l'outil donné.
        return outils_du_travail.CODE
    if any(a.get("status") in ("failed", "withheld") for a in attempts):
        return "step_failed"
    if any(a.get("code") == "max_paid_steps" for a in attempts):
        return "max_paid_steps"
    if not any(a.get("status") == "no_match" for a in attempts):
        return "no_step_answered"
    return None


@dataclass
class _Ecriture:
    """What a pass writes on its rows, from a cascade's outcome — ONE copy, for the
    synchronous pass, the submit pass and the collect pass (`waterfall_releve`)."""
    lot: Any
    recu: Any
    kind: str
    sorties: dict
    types: dict
    status_column: str
    qualify: Optional[dict]
    vides: dict = field(default_factory=dict)
    qualif: dict = field(default_factory=lambda: {"calls": 0, "not_qualified": 0,
                                                  "cost_usd": 0.0})
    refuses: list = field(default_factory=list)

    async def _poser(self, rids: list[str], patch: dict, cle: str) -> None:
        for rid in rids:
            code = await run_in_threadpool(pr.ecrire, self.lot, rid, patch)
            if code:
                self.recu.echec(rid, f"writeback_{code}")
            else:
                self.recu.compter(cle)

    async def trouve(self, fournisseur: Optional[str], trouve: dict,
                     rids: list[str]) -> None:
        patch = {}
        for champ, col in self.sorties.items():
            v = _cellule(self.kind, champ, trouve, fournisseur)
            if v not in (None, "", []):
                v = _au_type(v, self.types.get(col))
            if v not in (None, "", []):
                patch[col] = v
                self.vides[champ] = False
        patch.update({self.status_column: TROUVE,
                      f"{self.status_column}.comment": fournisseur,
                      **({colonne_poignee(self.status_column): None}
                         if colonne_poignee(self.status_column) in self.types else {})})
        await self._poser(rids, patch, "found")

    async def sans(self, rids: list[str]) -> None:
        await self._poser(rids, {self.status_column: SANS}, "no_match")

    async def remettre(self, rids: list[str], code: str) -> None:
        """Back in the queue: status and handle cleared — the next `update_rows` takes
        the row again. Named in the receipt by `code`."""
        patch = {self.status_column: ""}
        if colonne_poignee(self.status_column) in self.types:
            patch[colonne_poignee(self.status_column)] = None
        for rid in rids:
            await run_in_threadpool(pr.ecrire, self.lot, rid, patch)
            self.recu.ecarter(rid, code)

    async def issue(self, out: dict, rids: list[str], *, requeue: bool = False) -> None:
        """A cascade's outcome on its rows: found, submitted (pending, its handle kept on
        the row), unqualified, no_match — or not a verdict: nothing written, the row
        retried (`requeue`: put back in the queue, when it was pending)."""
        q = out.get("qualification") or {}
        if q.get("status") == "done":
            self.qualif["calls"] += q.get("calls", 1)
            self.qualif["not_qualified"] += q.get("not_qualified") or 0
            self.qualif["cost_usd"] += q.get("cost_usd") or 0
        trouve = out.get(self.kind) if out.get("found") else None
        attempts = out.get("attempts") or []
        self.refuses.extend(n for n in _hors_liste(attempts) if n not in self.refuses)
        attente = out.get("pending") or []
        if not trouve and attente:
            # Submitted (`submit_only`): the row keeps the collect call.
            p = attente[0]
            await self._poser(rids, {
                self.status_column: EN_ATTENTE,
                f"{self.status_column}.comment": p["provider"],
                colonne_poignee(self.status_column): {
                    "provider": p["provider"], "tool": p["tool"],
                    "arguments": p["arguments"],
                    # When it was submitted: a job never done is requeued after
                    # `waterfall_releve.ATTENTE_MAX_S` (epoch seconds).
                    "submitted_at": int(time.time())}}, "submitted")
            return
        motif = _pas_un_verdict(out, trouve, attempts, self.qualify)
        if motif:
            # Not a verdict (out of time, a step failed, Jev did not judge): nothing
            # written, the receipt names why, and the next call tries the row again.
            if requeue:
                await self.remettre(rids, motif)
            else:
                for rid in rids:
                    self.recu.ecarter(rid, motif)
            return
        if trouve:
            await self.trouve(out.get("provider"), trouve, rids)
        elif any(a.get("status") == "unqualified" for a in attempts):
            await self._poser(rids, {self.status_column: ECARTE}, "unqualified")
        else:
            # A step answered without finding and none failed: a verdict, kept, so the
            # next call doesn't pay for it again.
            await self.sans(rids)

    def fin(self, recu, extra: dict) -> None:
        if recu.comptes.get("found"):
            # A typo in `output` writes nothing, silently: named, never guessed.
            extra["output_fields_never_found"] = sorted(
                c for c in self.sorties if self.vides.get(c, True))
        if self.qualif["calls"]:
            extra["qualification"] = {**self.qualif,
                                      "cost_usd": round(self.qualif["cost_usd"], 8)}


async def _preparer(lot, colonnes: dict, sorties: dict, status_column: str,
                    dry_run: bool, poignee: bool) -> tuple[dict, list[dict]]:
    """The table's columns checked and the missing output ones declared (the handle's
    too, for an asynchronous pass): `(types, declared)`."""
    inconnues = await run_in_threadpool(pr.colonnes_inconnues, lot, colonnes.values())
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
    if poignee and colonne_poignee(status_column) not in champs:
        a_creer.append({"key": colonne_poignee(status_column), "type": "json"})
    if a_creer and not dry_run:
        try:
            await run_in_threadpool(lot.store.patch_schema, lot.adresse, fields=a_creer)
        except ValueError as e:
            raise pr.refus("waterfall_rows_columns",
                           f"could not declare the columns: {e}. Nothing was sent.")
    types = {**{k: f.get("type", "text") for k, f in champs.items()},
             **{f["key"]: f["type"] for f in a_creer}}
    return types, a_creer
