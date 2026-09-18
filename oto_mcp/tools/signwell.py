"""SignWell — signature électronique : documents et modèles.

Wrappe `oto.tools.signwell.client.SignWellClient` (`X-Api-Key`,
`https://www.signwell.com/api/v1`). keyed `api_key`, **byo-only** : la clé agit
au nom du compte qui l'a créée, c'est ce nom qui signe les invitations — une clé
plateforme partagée enverrait des contrats au nom de quelqu'un d'autre.

26 opérations sur CINQ tools, verbe en `op` : `signwell_document` et
`signwell_template` ici ; `signwell_bulk_send`, `signwell_webhook` et
`signwell_account` dans `signwell_envois.py`. Le socle commun (clé, refus, vue
d'un document) vit dans `signwell_socle.py`.

## Ce que la couche tool ajoute au transport

1. **Un document créé ne part PAS par défaut.** Chez SignWell `draft` vaut
   `false` : créer, c'est envoyer un contrat à de vraies personnes dans le même
   appel. Ici `op="create"` crée un BROUILLON sauf `draft=False` explicite, et
   l'envoi reste un geste distinct (`op="send"`). Même règle pour un document
   issu d'un modèle.
2. **La vue d'un document** (`signwell_socle.vue_document`) : un lien de
   signature par destinataire sous une seule clé, un `emailed` explicite, et des
   `notes` sur ce que l'état ne dit pas (test mode détourné vers le titulaire,
   `Sending` qui n'est pas un envoi). `full=True` rend la charge SignWell brute.
3. **Le PDF signé se rend en LIEN**, jamais en octets (`url_only` forcé).
4. **`dry_run` sur toute mutation** : validation identique, aucun appel mutant ;
   là où l'objet se relit, l'aperçu est un vrai diff.
5. **Aucun argument ignoré en silence** : un argument qu'un `op` n'utilise pas,
   ou une clé inconnue dans `options`, est refusé.
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from fastmcp import FastMCP

from ..connectors import verify as connector_verify
from .signwell_socle import (
    OU_CREER_LA_CLE, _bad, _client, _hors_op, _need, _run, destinataires_valides,
    fichiers_valides, options_valides, refus, sans_base64, vue_document,
)

#: Réglages rares de `POST /documents`, passables par `options` (spec, 2026-09-16).
_OPTIONS_CREATE = (
    "send_sms_test_preview_email", "self_sign", "with_signature_page",
    "api_application_id", "embedded_signing_notifications", "custom_requester_name",
    "custom_requester_email", "redirect_url", "allow_decline", "allow_reassign",
    "decline_redirect_url", "language", "conditional_rules", "metadata",
    "attachment_requests", "copied_contacts", "labels", "checkbox_groups",
)
_OPTIONS_SEND = (
    "send_sms_test_preview_email", "api_application_id", "embedded_signing_notifications",
    "custom_requester_name", "custom_requester_email", "redirect_url", "allow_decline",
    "allow_reassign", "decline_redirect_url", "conditional_rules", "metadata", "labels",
    "checkbox_groups",
)
_OPTIONS_TPL_CREATE = (
    "copied_placeholders", "api_application_id", "redirect_url", "allow_decline",
    "allow_reassign", "decline_redirect_url", "language", "conditional_rules", "metadata",
    "attachment_requests", "labels", "checkbox_groups",
)
_OPTIONS_TPL_UPDATE = (
    "api_application_id", "redirect_url", "allow_decline", "allow_reassign",
    "decline_redirect_url", "conditional_rules", "metadata", "labels", "checkbox_groups",
)
_OPTIONS_TPL_DOC = (
    "send_sms_test_preview_email", "exclude_placeholders", "with_signature_page",
    "api_application_id", "embedded_signing_notifications", "custom_requester_name",
    "custom_requester_email", "redirect_url", "allow_decline", "allow_reassign",
    "decline_redirect_url", "language", "conditional_rules", "conditional_rules_mode",
    "metadata", "attachment_requests", "copied_contacts", "labels", "checkbox_groups",
)


def _verify(fields: dict, config: dict | None = None) -> None:  # noqa: ARG001
    """Sonde « tester la connexion » : `GET /me`. Sans effet de bord, et elle échoue
    exactement là où la clé est en cause."""
    from oto.tools.common.errors import UpstreamHTTPError
    from oto.tools.signwell import SignWellClient

    cle = (fields.get("key") or "").strip()
    # Refusée AVANT le client : construit sur une clé vide, il retombe sur le secret
    # `SIGNWELL_API_KEY` du SERVEUR — la sonde validerait alors la clé de quelqu'un
    # d'autre et dirait « connexion OK » à une carte vide.
    if not cle:
        raise ValueError(f"Clé SignWell vide : crée-la sur {OU_CREER_LA_CLE}, puis colle-la.")
    try:
        SignWellClient(api_key=cle).get_me()
    except UpstreamHTTPError as e:
        raise ValueError(refus(e)) from None


def _qui_recoit(recipients: List[Dict[str, Any]], *, test_mode: bool,
                embedded: bool) -> List[Dict[str, Any]]:
    """Aperçu d'envoi : qui recevrait un courriel, avant que quoi que ce soit parte."""
    out = []
    for r in recipients:
        if test_mode:
            emailed = False
        elif embedded:
            emailed = bool(r.get("send_email"))
        else:
            emailed = r.get("delivery_method") != "sms"
        out.append({"id": r.get("id"), "name": r.get("name"), "email": r.get("email"),
                    "emailed": emailed})
    return out


def _vue(payload: Any, full: Optional[bool]) -> dict:
    if full:
        return {"document": payload}
    return vue_document(payload) if isinstance(payload, dict) else {"result": payload}


def register(mcp: FastMCP) -> None:
    connector_verify.register("signwell", _verify)

    @mcp.tool()
    def signwell_document(
        op: Literal["get", "create", "send", "remind", "update_recipients",
                    "update_authentication", "completed_pdf", "nom151_certificate",
                    "delete"] = "get",
        document_id: Optional[str] = None,
        files: Optional[List[dict]] = None,
        recipients: Optional[List[dict]] = None,
        draft: Optional[bool] = None,
        test_mode: Optional[bool] = None,
        name: Optional[str] = None,
        subject: Optional[str] = None,
        message: Optional[str] = None,
        text_tags: Optional[bool] = None,
        fields: Optional[List[List[dict]]] = None,
        apply_signing_order: Optional[bool] = None,
        embedded_signing: Optional[bool] = None,
        expires_in: Optional[int] = None,
        reminders: Optional[bool] = None,
        options: Optional[dict] = None,
        audit_page: Optional[bool] = None,
        file_format: Optional[Literal["pdf", "zip"]] = None,
        full: Optional[bool] = None,
        dry_run: Optional[bool] = None,
    ) -> dict:
        """SignWell documents sent for signature — create, send, follow, fetch the signed PDF.

        There is NO "list documents" endpoint: keep the `id` returned by
        op="create". Reads return a compact view — per recipient ONE
        `signing_link` and an explicit `emailed`, plus `notes` on what the
        status hides; `full=True` returns SignWell's raw payload.

        Args:
            op: which action.
                · "get" — status and recipients of `document_id`.
                · "create" — `files` + `recipients`. ⚠️ Creates a DRAFT unless
                  `draft=False` (SignWell's own default sends immediately). Place
                  fields with `fields` or `text_tags=True` (tags written in the
                  file, syntax: `oto_guide op=read slug="signwell-envoi"`).
                · "send" — send a draft (`document_id`); settings may be updated
                  in the same call.
                · "remind" — remind unsigned recipients; `recipients` =
                  [{email?, name?}] narrows it.
                · "update_recipients" — [{id, name, email}] on a sent document.
                · "update_authentication" — [{id, passcode?, passcode_delivery?}].
                · "completed_pdf" — link to the signed PDF (`audit_page`,
                  `file_format`). The link is a bearer URL.
                · "nom151_certificate" — link to the Mexican NOM-151 certificate.
                · "delete" — delete `document_id`; cancels signing in progress.
            files: [{name, file_url | file_base64}]. `file_url` must be publicly
                fetchable — a project file's signed `download_url`
                (`oto_project_files op="list"`) works, and is itself a bearer URL.
            recipients: [{id, name, email, …}] — `id` is yours ("1", "2"…) and is
                the signer number of fields and text tags. Embedded signing
                only emails a recipient with `send_email: true`.
            draft: op="create" only; default True here.
            test_mode: non-binding document; invitations go to the ACCOUNT OWNER,
                never to the recipients.
            embedded_signing: return signing links instead of relying on
                SignWell's email; anyone holding a link can sign.
            options: rarer spec settings (copied_contacts, labels, metadata,
                redirect_url, allow_decline, language, custom_requester_name…);
                an unknown key is refused.
            full: return SignWell's raw payload.
            dry_run: validate and preview without writing or sending.
        """
        explicites = dict(test_mode=test_mode, name=name, subject=subject, message=message,
                          expires_in=expires_in, reminders=reminders,
                          apply_signing_order=apply_signing_order,
                          embedded_signing=embedded_signing)

        if op == "get":
            _need(op, document_id=document_id)
            _hors_op(op, files=files, recipients=recipients, draft=draft, text_tags=text_tags,
                     fields=fields, options=options, audit_page=audit_page,
                     file_format=file_format, dry_run=dry_run, **explicites)
            return _vue(_run(lambda: _client().get_document(document_id)), full)

        if op == "create":
            _hors_op(op, document_id=document_id, audit_page=audit_page,
                     file_format=file_format)
            fichiers_valides(files)
            destinataires_valides(recipients)
            extra = options_valides(op, options, _OPTIONS_CREATE)
            draft_effectif = True if draft is None else draft
            if (not draft_effectif and not fields and not text_tags
                    and not extra.get("with_signature_page")):
                raise _bad("Un document envoyé (draft=False) doit porter au moins un champ : "
                           "`fields`, `text_tags=True` ou options.with_signature_page.")
            body = {k: v for k, v in {**explicites, "text_tags": text_tags, "fields": fields,
                                      **extra}.items() if v is not None}
            if dry_run:
                return {"dry_run": True, "would_create": {
                    "draft": draft_effectif, "sends_now": not draft_effectif,
                    "files": sans_base64(files), "fields": len(fields or []),
                    "recipients": _qui_recoit(recipients, test_mode=bool(test_mode),
                                              embedded=bool(embedded_signing)),
                    **{k: v for k, v in body.items() if k not in ("fields",)}}}
            doc = _run(lambda: _client().create_document(
                files=files, recipients=recipients, draft=draft_effectif, **body))
            out = _vue(doc, full)
            if draft_effectif and not full:
                out["next_step"] = ("Brouillon : rien n'est parti. Envoie-le avec "
                                    "signwell_document(op=\"send\", document_id=…).")
            return out

        if op == "send":
            _need(op, document_id=document_id)
            _hors_op(op, files=files, recipients=recipients, draft=draft, text_tags=text_tags,
                     fields=fields, audit_page=audit_page, file_format=file_format)
            extra = options_valides(op, options, _OPTIONS_SEND)
            body = {k: v for k, v in {**explicites, **extra}.items() if v is not None}
            if dry_run:
                courant = _run(lambda: _client().get_document(document_id))
                vue = vue_document(courant) if isinstance(courant, dict) else {}
                mode_test = body.get("test_mode", vue.get("test_mode"))
                embarque = body.get("embedded_signing", vue.get("embedded_signing"))
                return {"dry_run": True, "would_send": document_id,
                        "current_status": vue.get("status"), "settings": body,
                        "recipients": _qui_recoit(
                            (courant or {}).get("recipients") or [],
                            test_mode=bool(mode_test), embedded=bool(embarque))}
            return _vue(_run(lambda: _client().send_document(document_id, **body)), full)

        if op == "remind":
            _need(op, document_id=document_id)
            _hors_op(op, files=files, draft=draft, text_tags=text_tags, fields=fields,
                     options=options, audit_page=audit_page, file_format=file_format,
                     **explicites)
            cibles = [{k: r.get(k) for k in ("name", "email") if r.get(k)}
                      for r in recipients or []] or None
            if dry_run:
                courant = _run(lambda: _client().get_document(document_id))
                en_attente = [r for r in (vue_document(courant) or {}).get("recipients", [])
                              if str(r.get("status") or "").lower() not in ("signed", "completed")]
                return {"dry_run": True, "would_remind": cibles or en_attente}
            return _vue(_run(lambda: _client().send_reminder(document_id, cibles)), full)

        if op in ("update_recipients", "update_authentication"):
            _need(op, document_id=document_id, recipients=recipients)
            _hors_op(op, files=files, draft=draft, text_tags=text_tags, fields=fields,
                     options=options, audit_page=audit_page, file_format=file_format,
                     **explicites)
            if op == "update_recipients":
                for i, r in enumerate(recipients):
                    if not all(r.get(k) for k in ("id", "name", "email")):
                        raise _bad(f"recipients[{i}] : `id`, `name` et `email` sont tous requis.")
            else:
                destinataires_valides(recipients, email_requis=False)
            if dry_run:
                courant = _run(lambda: _client().get_document(document_id))
                par_id = {str(r.get("id")): r for r in (courant or {}).get("recipients") or []}
                if op == "update_authentication":
                    # Un code d'accès ne s'échoe jamais, même en aperçu.
                    return {"dry_run": True, "would_update_authentication": [
                        {"id": r.get("id"), "known": str(r.get("id")) in par_id,
                         "passcode": "posé" if r.get("passcode") else "inchangé"}
                        for r in recipients]}
                changes = []
                for r in recipients:
                    avant = par_id.get(str(r.get("id")))
                    changes.append({"id": r.get("id"), "known": avant is not None,
                                    **{k: {"from": (avant or {}).get(k), "to": r.get(k)}
                                       for k in ("name", "email")
                                       if (avant or {}).get(k) != r.get(k)}})
                return {"dry_run": True, "changes": changes}
            if op == "update_recipients":
                return _vue(_run(lambda: _client().update_recipients(document_id, recipients)),
                            full)
            return _vue(_run(lambda: _client().update_authentication(document_id, recipients)),
                        full)

        if op in ("completed_pdf", "nom151_certificate"):
            _need(op, document_id=document_id)
            _hors_op(op, files=files, recipients=recipients, draft=draft, text_tags=text_tags,
                     fields=fields, options=options, dry_run=dry_run, full=full, **explicites)
            if op == "completed_pdf":
                from oto.tools.common.errors import UpstreamHTTPError

                def _pdf():
                    try:
                        return _client().get_completed_pdf(
                            document_id, url_only=True, audit_page=audit_page,
                            file_format=file_format)
                    except UpstreamHTTPError as e:
                        # Relevé en live : un document qui EXISTE mais n'est pas encore
                        # signé par tous rend 404 ici, pas un refus d'état. Jugé sur le
                        # STATUT amont, jamais sur le texte d'un message traduit.
                        if e.status_code != 404:
                            raise
                        raise _bad("SignWell rend 404 pour ce PDF : soit le document n'est "
                                   "pas encore signé par tous (le PDF n'existe qu'une fois "
                                   "« Completed » — vérifie avec op=\"get\"), soit "
                                   "l'identifiant est inconnu.") from None

                res = _run(_pdf)
            else:
                _hors_op(op, audit_page=audit_page, file_format=file_format)
                res = _run(lambda: _client().get_nom151_certificate(document_id, url_only=True))
            url = res.get("file_url") if isinstance(res, dict) else None
            return {"document_id": document_id, "file_url": url,
                    "note": "lien porteur : quiconque le détient télécharge le document "
                            "signé — ne pas le republier."}

        if op == "delete":
            _need(op, document_id=document_id)
            _hors_op(op, files=files, recipients=recipients, draft=draft, text_tags=text_tags,
                     fields=fields, options=options, audit_page=audit_page,
                     file_format=file_format, full=full, **explicites)
            if dry_run:
                courant = _run(lambda: _client().get_document(document_id))
                return {"dry_run": True, "would_delete": vue_document(courant),
                        "warning": "supprimer annule la signature en cours ; irréversible."}
            _run(lambda: _client().delete_document(document_id))
            return {"deleted": True, "document_id": document_id}

        raise _bad(f"op inconnu : {op!r}")

    @mcp.tool()
    def signwell_template(
        op: Literal["get", "create", "update", "delete", "create_document"] = "get",
        template_id: Optional[str] = None,
        template_ids: Optional[List[str]] = None,
        files: Optional[List[dict]] = None,
        placeholders: Optional[List[dict]] = None,
        recipients: Optional[List[dict]] = None,
        template_fields: Optional[List[dict]] = None,
        draft: Optional[bool] = None,
        test_mode: Optional[bool] = None,
        name: Optional[str] = None,
        subject: Optional[str] = None,
        message: Optional[str] = None,
        text_tags: Optional[bool] = None,
        fields: Optional[List[List[dict]]] = None,
        apply_signing_order: Optional[bool] = None,
        embedded_signing: Optional[bool] = None,
        expires_in: Optional[int] = None,
        reminders: Optional[bool] = None,
        options: Optional[dict] = None,
        full: Optional[bool] = None,
        dry_run: Optional[bool] = None,
    ) -> dict:
        """SignWell templates — reusable documents with placeholder roles, and documents made from them.

        Args:
            op: which action.
                · "get" — one template (`template_id`).
                · "create" — `files` + `placeholders` [{id, name}] (roles such as
                  "Client"), fields via `fields` or `text_tags=True`.
                · "update" — settings of `template_id` (name, subject, message,
                  draft, expires_in, reminders, apply_signing_order, options).
                · "delete" — delete `template_id`.
                · "create_document" — a document from `template_id` (or
                  `template_ids`): `recipients` [{id, placeholder_name, name,
                  email}], `template_fields` [{api_id, value}] to prefill.
                  ⚠️ Creates a DRAFT unless `draft=False` (SignWell's default
                  sends); send it with signwell_document(op="send").
            options: rarer spec settings for this op; an unknown key is refused.
            full: return SignWell's raw payload.
            dry_run: validate and preview without writing.
        """
        explicites = dict(test_mode=test_mode, name=name, subject=subject, message=message,
                          expires_in=expires_in, reminders=reminders,
                          apply_signing_order=apply_signing_order,
                          embedded_signing=embedded_signing)

        if op == "get":
            _need(op, template_id=template_id)
            _hors_op(op, template_ids=template_ids, files=files, placeholders=placeholders,
                     recipients=recipients, template_fields=template_fields, draft=draft,
                     text_tags=text_tags, fields=fields, options=options, dry_run=dry_run,
                     full=full, **explicites)
            return {"template": _run(lambda: _client().get_template(template_id))}

        if op == "create":
            _hors_op(op, template_id=template_id, template_ids=template_ids,
                     recipients=recipients, template_fields=template_fields,
                     test_mode=test_mode, embedded_signing=embedded_signing, full=full)
            fichiers_valides(files)
            if not placeholders or any(not (p.get("id") and p.get("name")) for p in placeholders):
                raise _bad("`placeholders` est requis : [{id, name}] (ex. {\"id\": \"1\", "
                           "\"name\": \"Client\"}).")
            extra = options_valides(op, options, _OPTIONS_TPL_CREATE)
            body = {k: v for k, v in {**explicites, "draft": draft, "text_tags": text_tags,
                                      "fields": fields, **extra}.items() if v is not None}
            if dry_run:
                return {"dry_run": True, "would_create_template": {
                    "files": sans_base64(files), "placeholders": placeholders,
                    **{k: v for k, v in body.items() if k != "fields"},
                    "fields": len(fields or [])}}
            tpl = _run(lambda: _client().create_template(files, placeholders, **body))
            return {"template": tpl}

        if op == "update":
            _need(op, template_id=template_id)
            _hors_op(op, template_ids=template_ids, files=files, placeholders=placeholders,
                     recipients=recipients, template_fields=template_fields,
                     text_tags=text_tags, fields=fields, test_mode=test_mode,
                     embedded_signing=embedded_signing, full=full)
            extra = options_valides(op, options, _OPTIONS_TPL_UPDATE)
            patch = {k: v for k, v in {**explicites, "draft": draft, **extra}.items()
                     if v is not None}
            if not patch:
                raise _bad("op='update' : rien à modifier.")
            if dry_run:
                courant = _run(lambda: _client().get_template(template_id)) or {}
                return {"dry_run": True, "changes": {
                    k: {"from": courant.get(k), "to": v} for k, v in patch.items()
                    if courant.get(k) != v}}
            return {"template": _run(lambda: _client().update_template(template_id, **patch))}

        if op == "delete":
            _need(op, template_id=template_id)
            _hors_op(op, template_ids=template_ids, files=files, placeholders=placeholders,
                     recipients=recipients, template_fields=template_fields, draft=draft,
                     text_tags=text_tags, fields=fields, options=options, full=full,
                     **explicites)
            if dry_run:
                return {"dry_run": True,
                        "would_delete": _run(lambda: _client().get_template(template_id))}
            _run(lambda: _client().delete_template(template_id))
            return {"deleted": True, "template_id": template_id}

        if op == "create_document":
            if (template_id is None) == (template_ids is None):
                raise _bad("op='create_document' : passe `template_id` OU `template_ids`.")
            _hors_op(op, files=files, placeholders=placeholders)
            destinataires_valides(recipients)
            extra = options_valides(op, options, _OPTIONS_TPL_DOC)
            draft_effectif = True if draft is None else draft
            body = {k: v for k, v in {**explicites, "template_id": template_id,
                                      "template_ids": template_ids,
                                      "template_fields": template_fields,
                                      "text_tags": text_tags, "fields": fields,
                                      **extra}.items() if v is not None}
            if dry_run:
                return {"dry_run": True, "would_create": {
                    "draft": draft_effectif, "sends_now": not draft_effectif,
                    "recipients": _qui_recoit(recipients, test_mode=bool(test_mode),
                                              embedded=bool(embedded_signing)),
                    **{k: v for k, v in body.items() if k != "fields"}}}
            doc = _run(lambda: _client().create_document_from_template(
                recipients, draft=draft_effectif, **body))
            out = _vue(doc, full)
            if draft_effectif and not full:
                out["next_step"] = ("Brouillon : rien n'est parti. Envoie-le avec "
                                    "signwell_document(op=\"send\", document_id=…).")
            return out

        raise _bad(f"op inconnu : {op!r}")
