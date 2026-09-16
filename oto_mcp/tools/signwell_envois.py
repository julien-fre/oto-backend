"""SignWell — envois groupés, webhooks et compte.

Second module du connecteur `signwell` (cf. `tools/signwell.py` pour les
documents et modèles, `signwell_socle.py` pour le socle commun).

- **`signwell_bulk_send(op="create")` est un APERÇU par défaut.** Un envoi groupé
  écrit à une personne par ligne du CSV : sans `dry_run=False` explicite, l'outil
  fait valider le CSV par SignWell (`validate_csv`, sans effet de bord) et rend ce
  qui partirait. Même logique que les autres envois de masse du catalogue.
- **Le CSV se passe en clair** (`csv`) ou encodé (`csv_base64`) ; l'outil encode.
- **Les listes sont projetées** : les documents d'un envoi groupé sont rendus en
  vue resserrée (`signwell_socle.vue_document`) ; `full=True` rend la page brute.
- **oto n'est pas un récepteur de webhooks** : `signwell_webhook` enregistre une URL
  que l'utilisateur contrôle. SignWell y poste TOUS les événements du compte.
"""
from __future__ import annotations

import base64
from typing import List, Literal, Optional

from fastmcp import FastMCP

from .signwell_socle import _bad, _client, _hors_op, _need, _run, vue_document


def _csv_b64(csv: Optional[str], csv_base64: Optional[str]) -> str:
    if (csv is None) == (csv_base64 is None):
        raise _bad("Passe le CSV en clair (`csv`) OU encodé (`csv_base64`), l'un des deux.")
    if csv is not None:
        return base64.b64encode(csv.encode("utf-8")).decode("ascii")
    return csv_base64


def _shape_documents_page(page: object, full: bool) -> object:
    """Page de documents d'un envoi groupé : chaque document en vue resserrée, les
    clés de pagination intactes. `full=True` rend la page telle que servie."""
    if full or not isinstance(page, dict):
        return page
    out = {k: v for k, v in page.items() if k != "documents"}
    out["documents"] = [vue_document(d) for d in page.get("documents") or []]
    out["projection"] = {"documents": "vue resserrée (état, destinataires, lien)",
                         "hint": "full=True rend les documents entiers"}
    return out


def register(mcp: FastMCP) -> None:

    @mcp.tool()
    def signwell_bulk_send(
        op: Literal["list", "get", "documents", "csv_template", "validate_csv",
                    "create"] = "list",
        bulk_send_id: Optional[str] = None,
        template_ids: Optional[List[str]] = None,
        csv: Optional[str] = None,
        csv_base64: Optional[str] = None,
        name: Optional[str] = None,
        subject: Optional[str] = None,
        message: Optional[str] = None,
        skip_row_errors: Optional[bool] = None,
        apply_signing_order: Optional[bool] = None,
        custom_requester_name: Optional[str] = None,
        custom_requester_email: Optional[str] = None,
        api_application_id: Optional[str] = None,
        user_email: Optional[str] = None,
        page: Optional[int] = None,
        limit: Optional[int] = None,
        full: bool = False,
        dry_run: Optional[bool] = None,
    ) -> dict:
        """SignWell bulk sends — one document per CSV row, built from templates.

        Args:
            op: which action.
                · "list" — bulk sends (`page`, `limit`, `user_email`,
                  `api_application_id`).
                · "get" — one bulk send (`bulk_send_id`).
                · "documents" — the documents a bulk send produced, as compact
                  views (`full=True` for raw).
                · "csv_template" — the CSV header to fill for `template_ids`.
                · "validate_csv" — have SignWell check a filled CSV, sends nothing.
                · "create" — ⚠️ emails one person per row. PREVIEW BY DEFAULT: it
                  validates the CSV and returns what would go out; pass
                  `dry_run=False` to actually send.
            csv: the filled CSV as plain text (or `csv_base64`, already encoded).
            full: op="documents" only — raw SignWell documents instead of compact views.
            dry_run: op="create" only; defaults to True there.
        """
        reglages = dict(name=name, subject=subject, message=message,
                        skip_row_errors=skip_row_errors,
                        apply_signing_order=apply_signing_order,
                        custom_requester_name=custom_requester_name,
                        custom_requester_email=custom_requester_email)
        if op != "create" and dry_run is not None:
            raise _bad(f"op='{op}' ne prend pas `dry_run` : seul op='create' envoie.")

        if op == "list":
            _hors_op(op, bulk_send_id=bulk_send_id, template_ids=template_ids, csv=csv,
                     csv_base64=csv_base64, full=full, **reglages)
            res = _run(lambda: _client().list_bulk_sends(
                page=page, limit=limit, user_email=user_email,
                api_application_id=api_application_id))
            return res if isinstance(res, dict) else {"bulk_sends": res}

        if op in ("get", "documents"):
            _need(op, bulk_send_id=bulk_send_id)
            _hors_op(op, template_ids=template_ids, csv=csv, csv_base64=csv_base64,
                     user_email=user_email, api_application_id=api_application_id,
                     **reglages)
            if op == "get":
                _hors_op(op, page=page, limit=limit, full=full)
                return {"bulk_send": _run(lambda: _client().get_bulk_send(bulk_send_id))}
            res = _run(lambda: _client().get_bulk_send_documents(
                bulk_send_id, page=page, limit=limit))
            shaped = _shape_documents_page(res, full)
            return shaped if isinstance(shaped, dict) else {"documents": shaped}

        if op == "csv_template":
            _need(op, template_ids=template_ids)
            _hors_op(op, bulk_send_id=bulk_send_id, csv=csv, csv_base64=csv_base64,
                     page=page, limit=limit, user_email=user_email,
                     api_application_id=api_application_id, full=full, **reglages)
            res = _run(lambda: _client().get_bulk_send_csv_template(template_ids, base64=True))
            data = res.get("data") if isinstance(res, dict) else None
            texte = base64.b64decode(data).decode("utf-8", errors="replace") if data else None
            return {"template_ids": template_ids, "csv": texte,
                    "hint": "remplis une ligne par envoi, puis op='validate_csv'."}

        if op in ("validate_csv", "create"):
            _need(op, template_ids=template_ids)
            _hors_op(op, bulk_send_id=bulk_send_id, page=page, limit=limit,
                     user_email=user_email, full=full)
            b64 = _csv_b64(csv, csv_base64)
            if op == "validate_csv":
                _hors_op(op, api_application_id=api_application_id, **reglages)
                return {"validation": _run(
                    lambda: _client().validate_bulk_send_csv(template_ids, b64))}
            if dry_run is not False:
                validation = _run(lambda: _client().validate_bulk_send_csv(template_ids, b64))
                lignes = max(len([l for l in (csv or base64.b64decode(b64).decode(
                    "utf-8", errors="replace")).splitlines() if l.strip()]) - 1, 0)
                return {"dry_run": True, "rows": lignes, "validation": validation,
                        "settings": {k: v for k, v in reglages.items() if v is not None},
                        "next_step": "rien n'est parti. Relance avec dry_run=False pour "
                                     f"envoyer {lignes} document(s)."}
            body = {k: v for k, v in {**reglages,
                                      "api_application_id": api_application_id}.items()
                    if v is not None}
            return {"bulk_send": _run(lambda: _client().create_bulk_send(
                template_ids, b64, **body))}

        raise _bad(f"op inconnu : {op!r}")

    @mcp.tool()
    def signwell_webhook(
        op: Literal["list", "create", "delete"] = "list",
        webhook_id: Optional[str] = None,
        callback_url: Optional[str] = None,
        api_application_id: Optional[str] = None,
        dry_run: bool = False,
    ) -> dict:
        """SignWell webhooks — a URL you control receives every event of the account.

        Events include document_sent, document_viewed, document_signed,
        document_completed, document_declined, document_expired,
        document_bounced. Each delivery carries `event.hash` =
        HMAC-SHA256(key = webhook id, data = "<event.type>@<event.time>") to
        verify it came from SignWell. oto itself does not receive webhooks.

        Args:
            op: "list" | "create" (`callback_url`) | "delete" (`webhook_id`).
            dry_run: on create/delete, preview without writing.
        """
        if op == "list":
            _hors_op(op, webhook_id=webhook_id, callback_url=callback_url,
                     api_application_id=api_application_id, dry_run=dry_run)
            res = _run(lambda: _client().list_webhooks())
            return {"webhooks": res if isinstance(res, list) else (res or [])}

        if op == "create":
            _need(op, callback_url=callback_url)
            _hors_op(op, webhook_id=webhook_id)
            if not callback_url.startswith("https://"):
                raise _bad("`callback_url` doit être une URL https:// que tu contrôles.")
            if dry_run:
                return {"dry_run": True, "would_create": {
                    "callback_url": callback_url, "api_application_id": api_application_id}}
            return {"webhook": _run(lambda: _client().create_webhook(
                callback_url, api_application_id))}

        if op == "delete":
            _need(op, webhook_id=webhook_id)
            _hors_op(op, callback_url=callback_url, api_application_id=api_application_id)
            if dry_run:
                existants = _run(lambda: _client().list_webhooks()) or []
                cible = next((w for w in existants
                              if isinstance(w, dict) and w.get("id") == webhook_id), None)
                return {"dry_run": True, "would_delete": cible, "known": cible is not None}
            _run(lambda: _client().delete_webhook(webhook_id))
            return {"deleted": True, "webhook_id": webhook_id}

        raise _bad(f"op inconnu : {op!r}")

    @mcp.tool()
    def signwell_account(
        op: Literal["me", "api_application", "delete_api_application"] = "me",
        application_id: Optional[str] = None,
        dry_run: bool = False,
    ) -> dict:
        """SignWell account behind the key, and its API applications.

        Args:
            op: "me" — user, account and plan behind the key (the probe that
                tells a bad key from anything else) · "api_application" — one
                API application (`application_id`) · "delete_api_application".
            dry_run: on delete, preview without deleting.
        """
        if op == "me":
            _hors_op(op, application_id=application_id, dry_run=dry_run)
            return {"me": _run(lambda: _client().get_me())}

        _need(op, application_id=application_id)
        if op == "api_application":
            _hors_op(op, dry_run=dry_run)
            return {"api_application": _run(
                lambda: _client().get_api_application(application_id))}

        if op == "delete_api_application":
            if dry_run:
                return {"dry_run": True, "would_delete": _run(
                    lambda: _client().get_api_application(application_id))}
            _run(lambda: _client().delete_api_application(application_id))
            return {"deleted": True, "application_id": application_id}

        raise _bad(f"op inconnu : {op!r}")
