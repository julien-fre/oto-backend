"""Nextmotion — les leads (prospects) et les réglages d'une clinique.

Module frère de `nextmotion.py` (cf. `Connector.modules`). Deux outils :

- `nextmotion_lead` — list | get. ⚠️ Un lead EST une personne : son nom, prénom, email,
  téléphone, ses notes et sa référence externe (un identifiant chez un tiers, qui la
  retrouverait) ne sont PAS servis. Restent le pipeline : source, statut, canal, soin
  souhaité et zone (des étiquettes du catalogue de la clinique), relances, rendez-vous
  prévu, praticien assigné. Le filtre `search` de l'API, qui cherche sur le nom,
  l'email ou le téléphone, n'est pas exposé.
- `nextmotion_setting` — les réglages, `kind` × list | get : abonnement Nextmotion
  (`feature`), moyens de paiement personnalisés, étiquettes, gabarits de communication
  et de documents (MÉTADONNÉES seules : leur corps est un objet libre non décrit par la
  spec), webhooks (sans leurs `headers`, qui portent d'ordinaire un secret).
"""
from __future__ import annotations

from typing import Literal, Optional

from fastmcp import FastMCP

from .nextmotion_garde import Kind, _client, _serve_kind
from .nextmotion_socle import (_COMMUNICATION_TEMPLATE, _DOCUMENT_TEMPLATE, _FEATURE,
                               _LABEL, _LEAD, _NAMED, _PERSONNE, _WEBHOOK, _shape)

_LEADS = {"lead": Kind("leads", _shape(_LEAD),
                       lambda c, cid, **p: c.list_leads(cid, **p),
                       lambda c, i: c.get_lead(i), (), _PERSONNE)}

_SETTINGS = {
    "feature": Kind(
        "features", _shape(_FEATURE), lambda c, cid, **p: c.list_clinic_features(cid, **p)),
    "payment_medium": Kind(
        "payment_mediums", _shape(_NAMED),
        lambda c, cid, **p: c.list_payment_mediums(cid, **p),
        lambda c, i: c.get_payment_medium(i)),
    "object_label": Kind(
        "object_labels", _shape(_LABEL),
        lambda c, cid, label_types, **p: c.list_object_labels(cid, types=label_types, **p),
        None, ("label_types",)),
    "communication_template": Kind(
        "communication_templates", _shape(_COMMUNICATION_TEMPLATE),
        lambda c, cid, template_kind, **p: c.list_communication_templates(
            cid, kind=template_kind, **p),
        lambda c, i: c.get_communication_template(i), ("template_kind",)),
    "document_template": Kind(
        "document_templates", _shape(_DOCUMENT_TEMPLATE),
        lambda c, cid, document_type, master_template_id, **p: c.list_document_templates(
            cid, type=document_type, master_id=master_template_id, **p),
        lambda c, i: c.get_document_template(i), ("document_type", "master_template_id")),
    "webhook": Kind(
        "webhooks", _shape(_WEBHOOK), lambda c, cid, **p: c.list_webhooks(cid, **p),
        lambda c, i: c.get_webhook(i)),
}

_LABEL_TYPES = Literal["patient", "call", "quote_tag", "quote_channel", "lead_status",
                       "lead_source", "lead_channel", "lead_desired_treatment", "lead_zone"]


def register(mcp: FastMCP) -> None:

    @mcp.tool()
    def nextmotion_lead(
        op: Literal["list", "get"] = "list",
        clinic_id: Optional[str] = None,
        lead_id: Optional[str] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        fields: Optional[list] = None,
    ) -> dict:
        """Leads (prospects) of a Nextmotion clinic — the sales pipeline: source,
        status, last channel, desired treatment and zone (clinic labels), follow-up
        and message counts, response and scheduled appointment times, assigned
        practitioner, done flag.

        The person is NOT served: no name, email, phone, notes or external
        reference, and no tool resolves a lead to a person — do not try to infer who
        it is. Searching leads by name is not offered.

        `op`: **"list"** (default, `clinic_id`) | **"get"** (`lead_id`).

        Args:
            op: list (default) | get.
            clinic_id: op="list" — the clinic.
            lead_id: op="get" — the lead.
            limit / offset: op="list" — pagination (limit 1..100, default 50).
            fields: op="list" — keep only these keys per row (`id` always kept);
                omitted or `["*"]` = the default view.
        """
        return _serve_kind(_LEADS, "lead", op, client=_client, clinic_id=clinic_id,
                           item_id=lead_id, filters={}, limit=limit, offset=offset,
                           fields=fields)

    @mcp.tool()
    def nextmotion_setting(
        kind: Literal["feature", "payment_medium", "object_label", "communication_template",
                      "document_template", "webhook"],
        op: Literal["list", "get"] = "list",
        clinic_id: Optional[str] = None,
        item_id: Optional[str] = None,
        label_types: Optional[list[_LABEL_TYPES]] = None,
        template_kind: Optional[Literal["email", "sms", "whatsapp"]] = None,
        document_type: Optional[Literal[0, 1, 2, 3, 4, 6, 7, 8]] = None,
        master_template_id: Optional[str] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        fields: Optional[list] = None,
    ) -> dict:
        """Settings and referentials of a Nextmotion clinic.

        `kind`:
        - "feature" — the clinic's Nextmotion subscription options (code, prices,
          payment methods, counts, sale / cancel / end times); list only.
        - "payment_medium" — custom payment means.
        - "object_label" — labels: lead sources, statuses, channels, desired
          treatments and zones, quote tags and channels, call and patient tags;
          filter `label_types`; list only.
        - "communication_template" — email / SMS / WhatsApp templates, metadata only
          (type, enabled); filter `template_kind`.
        - "document_template" — document templates, metadata only (type, name,
          signature flags); filters `document_type` (0 PRESCRIPTION, 1 QUOTE,
          2 INVOICE, 3 DEPOSIT_INVOICE, 4 CREDIT_NOTE, 6 IMAGE_RIGHTS_CONSENT,
          7 ADMINISTRATIVE, 8 VISIT) and `master_template_id`.
        - "webhook" — webhooks (action type, URL; headers withheld).

        `op`: **"list"** (default, needs `clinic_id`) | **"get"** (`item_id`).
        Each filter belongs to its kind only; the others refuse it.

        Args:
            kind: which setting.
            op: list (default) | get.
            clinic_id: op="list" — the clinic.
            item_id: op="get" — the object of that kind.
            label_types / template_kind / document_type / master_template_id:
                op="list" — the kind's filters (see above).
            limit / offset: op="list" — pagination (limit 1..100, default 50).
            fields: op="list" — keep only these keys per row (`id` always kept);
                omitted or `["*"]` = the default view.
        """
        filters = {"label_types": label_types, "template_kind": template_kind,
                   "document_type": document_type, "master_template_id": master_template_id}
        return _serve_kind(_SETTINGS, kind, op, client=_client, clinic_id=clinic_id,
                           item_id=item_id, filters=filters, limit=limit, offset=offset,
                           fields=fields)
