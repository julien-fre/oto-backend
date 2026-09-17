"""Nextmotion — les ventes au-delà des devis et factures : paiements, statistiques de
chiffre d'affaires, et totaux financiers d'un patient.

Module frère de `nextmotion.py` (cf. `Connector.modules`) ; devis et factures y restent.
Trois outils, parce que leurs paramètres ne se recouvrent pas (ADR 0047) :

- `nextmotion_payment` — list | get. Un paiement EMBARQUE sa facture entière, donc son
  patient : la facture passe par la même liste blanche que `nextmotion_invoice`.
- `nextmotion_statistics` — `kind` × une période : agrégats de la clinique, aucun
  patient. `meta` (objet libre, non décrit par la spec) et `label_field` (paramètre non
  documenté, qui pourrait changer ce que nomment les libellés) ne sont pas servis.
- `nextmotion_patient_stats` — les totaux d'UN patient désigné par son id : devis,
  facturé, payé, avoirs, remboursements, dates de première et dernière visite. Rien qui
  l'identifie ; ni la date d'ouverture de son dossier ni son activité photo (la vie du
  dossier médical).
"""
from __future__ import annotations

from typing import Literal, Optional

from fastmcp import FastMCP

from .nextmotion_garde import _bad, _client, _need, _paging, _refuse_ignored, _run
from .nextmotion_socle import (_CHART, _PATIENT_STATS, _PAYMENT, _WITHHELD, _one, _page,
                               _shape)

_payment = _shape(_PAYMENT)
_chart = _shape(_CHART)
_patient_stats = _shape(_PATIENT_STATS)

_STATISTICS = {
    "appointment_income": lambda c, cid, **kw: c.get_appointment_income_statistics(cid, **kw),
    "treatment_types": lambda c, cid, **kw: c.list_treatment_type_statistics(cid, **kw),
    "treatment_types_income": lambda c, cid, **kw: c.list_treatment_type_income_statistics(
        cid, **kw),
}


def register(mcp: FastMCP) -> None:

    @mcp.tool()
    def nextmotion_payment(
        op: Literal["list", "get"] = "list",
        clinic_id: Optional[str] = None,
        payment_id: Optional[str] = None,
        invoice_id: Optional[str] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        fields: Optional[list] = None,
    ) -> dict:
        """Payments of a Nextmotion clinic — amount per means (check, cash, card,
        transfer, stripe, voucher, other, custom mediums), deferred date, and the
        invoice paid. Health data, titles and free text are withheld; the patient is
        served by their ID ONLY (no name, email or phone; no tool resolves it to a
        person).

        `op`: **"list"** (default, `clinic_id`, optional `invoice_id`) |
        **"get"** (`payment_id`).

        Args:
            op: list (default) | get.
            clinic_id: op="list" — the clinic.
            payment_id: op="get" — the payment.
            invoice_id: op="list" — only this invoice's payments.
            limit / offset: op="list" — pagination (limit 1..100, default 50).
            fields: op="list" — keep only these keys per row (`id` always kept);
                omitted or `["*"]` = the default view.
        """
        if op == "list":
            _need(op, clinic_id=clinic_id)
            _refuse_ignored(op, payment_id=payment_id)
            c = _client()
            return _page(_run(lambda: c.list_payments(
                clinic_id, invoice_id=invoice_id, **_paging(limit, offset))),
                "payments", _payment, fields=fields)
        if op == "get":
            _need(op, payment_id=payment_id)
            _refuse_ignored(op, clinic_id=clinic_id, invoice_id=invoice_id, limit=limit,
                            offset=offset, fields=fields)
            c = _client()
            return _one(_run(lambda: c.get_payment(payment_id)), "payment", _payment)
        raise _bad("op doit être 'list' ou 'get'.")

    @mcp.tool()
    def nextmotion_statistics(
        kind: Literal["appointment_income", "treatment_types", "treatment_types_income"],
        clinic_id: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        period_type: Optional[Literal["year", "month", "week", "day"]] = None,
    ) -> dict:
        """Clinic-level statistics of a Nextmotion clinic, as bar charts: `title`,
        `labels` (one per period) and `datasets` (`data` aligned on the labels).

        `kind`:
        - "appointment_income" — income from appointments (one chart).
        - "treatment_types" — draft-quoted, quoted and invoiced treatment counts, one
          chart per treatment type.
        - "treatment_types_income" — income from invoiced treatments, one chart per
          treatment type.

        Args:
            kind: which statistic.
            clinic_id: the clinic.
            start_date / end_date: YYYY-MM-DD, end inclusive; omitted, Nextmotion
                spans 6 periods up to the current one.
            period_type: year | month (Nextmotion default) | week | day.
        """
        if kind not in _STATISTICS:
            raise _bad(f"kind inconnu : {kind!r}.")
        _need("statistics", clinic_id=clinic_id)
        c = _client()
        env = _run(lambda: _STATISTICS[kind](
            c, clinic_id, start_date=start_date, end_date=end_date,
            period_type=period_type))
        data = env.get("data") if isinstance(env, dict) else None
        charts = data if isinstance(data, list) else [data] if data is not None else []
        return {"kind": kind, "charts": [_chart(ch) for ch in charts]}

    @mcp.tool()
    def nextmotion_patient_stats(patient_id: str) -> dict:
        """Financial totals of ONE Nextmotion patient, by patient id: quoted,
        invoiced, paid, credit-note and reimbursement totals, first and last visit
        times, review requests and clicks. Nothing identifies the patient, and no
        tool resolves the id to a person: do not try to infer who it is.

        Args:
            patient_id: the patient id, as served by appointments, quotes, invoices,
                payments or journeys.
        """
        _need("get", patient_id=patient_id)
        c = _client()
        return _one(_run(lambda: c.get_patient_stats(patient_id)), "patient_stats",
                    _patient_stats, withheld=_WITHHELD)
