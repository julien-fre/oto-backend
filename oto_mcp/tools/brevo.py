"""Brevo — emailing & CRM via l'API PUBLIQUE v3 (clé `api-key`).

Wrappe `oto.tools.brevo.BrevoClient`. Clé résolue par appel via
`access.resolve_api_key("brevo")` — byo (clé du membre ou credential partagé de
l'org). Pas de clé plateforme : un compte Brevo = les contacts de son propriétaire.

⚠️ **Distinct du connecteur `brevoauto`** (automations, API privée + session
navigateur). Même éditeur, surfaces disjointes : la clé v3 n'ouvre pas l'authoring
d'automations, et la session navigateur n'ouvre pas ces tools.

**Écritures dangereuses volontairement absentes** : envoi d'une campagne
(`sendNow` / statut `sent`), suppression de contact / liste / campagne / template,
purge des hard bounces. On conçoit, on mesure, on s'envoie un test — le départ d'un
envoi de masse et les suppressions restent dans l'UI Brevo. `brevo_send_email` reste
exposé : c'est du transactionnel unitaire, destinataires explicites.

**Surface consolidée (ADR 0047 §Amendement, appliqué au connecteur brevo)** : un
tool par OBJET métier, le verbe en paramètre `op` — `brevo_contact`, `brevo_list`
(listes + dossiers + segments), `brevo_template`, `brevo_campaign`,
`brevo_transactional`. Le défaut d'`op` est TOUJOURS une lecture : ce connecteur
envoie de vrais emails, un appel sans `op` ne doit rien déclencher.

Trois tools restent SEULS, leurs paramètres ne recouvrant pas ceux de leurs voisins :
- `brevo_send_email` — 11 paramètres de rédaction (`to`/`cc`/`bcc`/`sender`/
  `html_content`/`scheduled_at`…) dont un seul (`template_id`) existe ailleurs ;
- `brevo_import_contacts` / `brevo_export_contacts` — jobs **asynchrones** rendant
  un `{"processId"}` (pas des données), sur des paramètres de lot (`contacts`,
  `file_url`, `new_list`, `contact_filter`, `export_attributes`) qu'aucune autre
  op n'utilise. Les fusionner ne ferait qu'empiler des variantes disjointes.
`brevo_account` reste seul aussi : un seul booléen, c'est la fiche du compte.
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from fastmcp import FastMCP
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData, INVALID_PARAMS

from .. import access
from ..connectors import verify as connector_verify


def _verify(fields: dict, config: dict | None = None) -> None:  # noqa: ARG001 (config: contrat de sonde, non utilisé ici)
    """Sonde « tester la connexion » : la clé authentifie-t-elle vraiment ?

    `GET /account` est sans effet de bord et refusé (401) par une clé invalide.
    Lève — le message remonte tel quel à l'UI.
    """
    from oto.tools.brevo import BrevoClient
    BrevoClient(api_key=fields["key"]).get_account()


def register(mcp: FastMCP) -> None:
    from oto.tools.brevo import BrevoClient

    connector_verify.register("brevo", _verify)

    def _client() -> BrevoClient:
        key, _ = access.resolve_api_key("brevo")
        return BrevoClient(api_key=key)

    def _bad(msg: str) -> McpError:
        return McpError(ErrorData(code=INVALID_PARAMS, message=msg))

    def _need(value, name: str, op: str):
        """Argument obligatoire pour CET op — erreur actionnable, jamais de fallback."""
        if value is None:
            raise _bad(f"op='{op}' requiert {name}")
        return value

    # --- Compte ---------------------------------------------------------------

    @mcp.tool()
    def brevo_account(senders: bool = True) -> dict:
        """Compte Brevo : société, plan, crédits email/SMS restants.

        Args:
            senders: joindre les expéditeurs vérifiés — leur `email` est requis
                pour envoyer un email ou créer une campagne.
        """
        client = _client()
        out: dict[str, Any] = {"account": client.get_account()}
        if senders:
            out["senders"] = client.list_senders()
        return out

    # --- Contacts -------------------------------------------------------------

    @mcp.tool()
    def brevo_contact(
        op: Literal["list", "get", "stats", "attributes", "upsert",
                    "update"] = "list",
        identifier: Optional[str] = None,
        identifier_type: Optional[str] = None,
        email: Optional[str] = None,
        attributes: Optional[dict] = None,
        list_ids: Optional[list[int]] = None,
        unlink_list_ids: Optional[list[int]] = None,
        email_blacklisted: Optional[bool] = None,
        update_enabled: bool = True,
        ext_id: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
        segment_id: Optional[int] = None,
        modified_since: Optional[str] = None,
        created_since: Optional[str] = None,
        filter: Optional[str] = None,
        sort: Optional[str] = None,
    ) -> dict:
        """Un contact Brevo — lister, lire, créer/mettre à jour, statistiques, schéma.

        `op` :
        - **"list"** (défaut) : liste les contacts (paginé, **max 1000 par appel**).
        - **"get"** : fiche d'un contact (attributs, listes, statistiques d'envoi).
        - **"stats"** : statistiques de campagnes d'un contact (ouvertures, clics,
          bounces).
        - **"attributes"** : attributs de contact déclarés au compte (nom, catégorie,
          type). **À lire avant d'écrire des `attributes`** : un attribut inconnu est
          refusé. Aucun paramètre.
        - **"upsert"** : crée un contact, ou le met à jour s'il existe
          (`update_enabled`). Renvoie `{"id": …}` à la création, un objet vide sur
          une mise à jour.
        - **"update"** : met à jour un contact **existant**. Renvoie un objet vide au
          succès. La voie pour **désinscrire d'une liste** (`unlink_list_ids`) ou
          **blacklister** (`email_blacklisted=True`, le contact ne recevra plus rien).

        Import/export de masse = `brevo_import_contacts` / `brevo_export_contacts`
        (jobs asynchrones). Lire les contacts bloqués =
        `brevo_transactional(op="blocked")`.

        Args:
            op: list (défaut) | get | stats | attributes | upsert | update.
            identifier: op="get"/"stats"/"update" — email par défaut ; sinon id,
                téléphone ou EXT_ID.
            identifier_type: `email_id` | `contact_id` | `phone_id` | `ext_id`.
            email: op="upsert" — l'email du contact à créer/mettre à jour.
            attributes: op="upsert"/"update" — attributs Brevo en MAJUSCULES
                (`{"PRENOM": "Alex", "NOM": "Laporte"}`) — ils doivent exister au
                compte (cf. op="attributes").
            list_ids: op="upsert"/"update" — listes auxquelles inscrire le contact ;
                op="list" — restreindre à des listes. **Exclusif avec `segment_id`.**
            unlink_list_ids: op="update" — listes desquelles le désinscrire.
            email_blacklisted: op="update" — `True` = blacklister.
            update_enabled: op="upsert" — mettre à jour si le contact existe déjà.
            ext_id: op="upsert" — identifiant externe.
            limit: op="list" — taille de page (max 1000).
            offset: op="list" — pagination.
            segment_id: op="list" — restreindre à un segment. **Exclusif avec
                `list_ids`.**
            modified_since: op="list" — ISO 8601 UTC (`2026-01-31T00:00:00.000Z`).
            created_since: op="list" — ISO 8601 UTC.
            filter: op="list" — filtre sur attributs, opérateur `equals` SEULEMENT —
                ex. `equals(FIRSTNAME,"Alex")`. Pas de `contains` ni `>`.
            sort: op="list" — `asc` | `desc` (défaut `desc`, par date de création).
        """
        client = _client()

        if op == "list":
            return client.list_contacts(
                limit=limit, offset=offset, list_ids=list_ids, segment_id=segment_id,
                modified_since=modified_since, created_since=created_since,
                filter=filter, sort=sort)
        if op == "get":
            return client.get_contact(_need(identifier, "identifier", op),
                                      identifier_type=identifier_type)
        if op == "stats":
            return client.contact_campaign_stats(_need(identifier, "identifier", op))
        if op == "attributes":
            return client.list_attributes()
        if op == "upsert":
            return client.upsert_contact(
                email=_need(email, "email", op), attributes=attributes,
                list_ids=list_ids, update_enabled=update_enabled, ext_id=ext_id)
        if op == "update":
            return client.update_contact(
                _need(identifier, "identifier", op), attributes=attributes,
                list_ids=list_ids, unlink_list_ids=unlink_list_ids,
                identifier_type=identifier_type, email_blacklisted=email_blacklisted)
        raise _bad("op doit être 'list', 'get', 'stats', 'attributes', 'upsert' "
                   "ou 'update'")

    @mcp.tool()
    def brevo_import_contacts(
        contacts: Optional[list[dict]] = None,
        list_ids: Optional[list[int]] = None,
        file_url: Optional[str] = None,
        update_existing_contacts: bool = True,
        new_list: Optional[dict] = None,
    ) -> dict:
        """Import de masse **asynchrone**. Renvoie `{"processId": …}` (pas les contacts).

        **La voie au-delà de 150 contacts** — `brevo_list(op="add")` plafonne là.

        Args:
            contacts: `[{"email": …, "attributes": {…}}, …]`.
            file_url: alternative — CSV distant (séparateur `;`).
            new_list: `{"listName": …, "folderId": …}` pour créer la liste au vol.
        """
        return _client().import_contacts(
            json_body=contacts, list_ids=list_ids, file_url=file_url,
            update_existing_contacts=update_existing_contacts, new_list=new_list)

    @mcp.tool()
    def brevo_export_contacts(contact_filter: Optional[dict] = None,
                              export_attributes: Optional[list[str]] = None) -> dict:
        """Export **asynchrone** des contacts. Renvoie `{"processId": …}`, pas les données.

        Args:
            contact_filter: `{"listIds": [1]}` | `{"segmentId": 2}` |
                `{"emailBlacklisted": true}`. Défaut = tous les contacts actifs.

        Pour lire des contacts directement, préférer `brevo_contact(op="list")` (paginé).
        """
        return _client().export_contacts(
            contact_filter=contact_filter, export_attributes=export_attributes)

    # --- Listes, dossiers, segments -------------------------------------------

    @mcp.tool()
    def brevo_list(
        op: Literal["list", "get", "contacts", "create", "update", "add",
                    "remove", "folders", "segments"] = "list",
        list_id: Optional[int] = None,
        name: Optional[str] = None,
        folder_id: Optional[int] = None,
        emails: Optional[list[str]] = None,
        ids: Optional[list[int]] = None,
        all_contacts: bool = False,
        modified_since: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        """Une liste de contacts Brevo — et les dossiers/segments qui l'entourent.

        `op` :
        - **"list"** (défaut) : listes de contacts du compte, ou d'un dossier si
          `folder_id`.
        - **"get"** : détail d'une liste — nom, dossier, nombre de contacts et de
          blacklistés.
        - **"contacts"** : contacts d'une liste (paginé, **max 500 par appel**).
        - **"create"** : crée une liste. `folder_id` est **obligatoire**
          (cf. op="folders").
        - **"update"** : renomme une liste, ou la déplace vers un autre dossier.
        - **"add"** / **"remove"** : ajoute ou retire des contacts **EXISTANTS**
          d'une liste. ⚠️ **Max 150 contacts par appel**, et un SEUL type
          d'identifiant (`emails` OU `ids`) — au-delà, l'API refuse : utiliser
          `brevo_import_contacts`, qui crée aussi les contacts absents. Renvoie
          `{contacts: {success: [...], failure: [...]}}` — **lire `failure`**, un
          contact inconnu échoue sans faire échouer l'appel. `all_contacts=True`
          (op="remove" seulement) vide la liste entière.
        - **"folders"** : dossiers de listes. Leur `id` est requis pour op="create".
        - **"segments"** : segments (listes dynamiques définies par un filtre).
          Lecture seule, et l'`id` d'un segment se passe à
          `brevo_contact(op="list", segment_id=…)`.

        Args:
            op: list (défaut) | get | contacts | create | update | add | remove |
                folders | segments.
            list_id: op="get"/"contacts"/"update"/"add"/"remove" — la liste ciblée.
            name: op="create"/"update" — nom de la liste.
            folder_id: op="create" (obligatoire) / "update" (déplacement) /
                "list" (filtrer sur un dossier).
            emails: op="add"/"remove" — contacts par email (max 150).
            ids: op="add"/"remove" — contacts par id Brevo (max 150). Exclusif
                avec `emails`.
            all_contacts: op="remove" — vide la liste entière.
            modified_since: op="contacts" — ISO 8601 UTC.
            limit: taille de page (list, contacts, folders, segments).
            offset: pagination (list, contacts, folders, segments).
        """
        client = _client()

        if op == "list":
            return client.list_lists(limit=limit, offset=offset, folder_id=folder_id)
        if op == "get":
            return client.get_list(_need(list_id, "list_id", op))
        if op == "contacts":
            return client.list_contacts_of_list(
                _need(list_id, "list_id", op), limit=limit, offset=offset,
                modified_since=modified_since)
        if op == "create":
            return client.create_list(_need(name, "name", op),
                                      _need(folder_id, "folder_id", op))
        if op == "update":
            return client.update_list(_need(list_id, "list_id", op), name=name,
                                      folder_id=folder_id)
        if op == "add":
            return client.add_to_list(_need(list_id, "list_id", op), emails=emails,
                                      ids=ids)
        if op == "remove":
            return client.remove_from_list(_need(list_id, "list_id", op),
                                           emails=emails, ids=ids,
                                           all_contacts=all_contacts)
        if op == "folders":
            return client.list_folders(limit=limit, offset=offset)
        if op == "segments":
            return client.list_segments(limit=limit, offset=offset)
        raise _bad("op doit être 'list', 'get', 'contacts', 'create', 'update', "
                   "'add', 'remove', 'folders' ou 'segments'")

    # --- Email transactionnel --------------------------------------------------

    @mcp.tool()
    def brevo_send_email(
        to: list[dict],
        subject: Optional[str] = None,
        html_content: Optional[str] = None,
        sender: Optional[dict] = None,
        template_id: Optional[int] = None,
        params: Optional[dict] = None,
        cc: Optional[list[dict]] = None,
        bcc: Optional[list[dict]] = None,
        reply_to: Optional[dict] = None,
        tags: Optional[list[str]] = None,
        scheduled_at: Optional[str] = None,
    ) -> dict:
        """**Envoie réellement** un email transactionnel. Renvoie `{"messageId": …}`.

        Deux modes exclusifs :
        - **template** : `template_id` + `params` (variables `{{params.NOM}}`) ;
        - **direct** : `subject` + `html_content` + `sender`.

        Args:
            to: `[{"email": "a@b.c", "name": "Alex"}]` — max 99 destinataires.
            sender: `{"email": …, "name": …}`. Doit être un expéditeur **vérifié**
                du compte (cf. `brevo_account`), sinon Brevo refuse l'envoi.
            scheduled_at: ISO 8601 UTC, jusqu'à 72 h dans le futur.

        Pour un envoi de masse à une liste, c'est une campagne — pas ce tool.
        """
        return _client().send_email(
            to=to, subject=subject, html_content=html_content, sender=sender,
            template_id=template_id, params=params, cc=cc, bcc=bcc,
            reply_to=reply_to, tags=tags, scheduled_at=scheduled_at)

    @mcp.tool()
    def brevo_transactional(
        op: Literal["logs", "content", "events", "report", "blocked",
                    "blocked_domains"] = "logs",
        email: Optional[str] = None,
        template_id: Optional[int] = None,
        message_id: Optional[str] = None,
        uuid: Optional[str] = None,
        event: Optional[str] = None,
        days: Optional[int] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        by_day: bool = False,
        tag: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        """L'email transactionnel envoyé et sa délivrabilité (routes `/smtp/*`).

        `op` :
        - **"logs"** (défaut) : emails transactionnels envoyés. Dates au format
          `YYYY-MM-DD`. Pour savoir ce qu'un email est DEVENU (délivré, ouvert,
          bounce), c'est op="events".
        - **"content"** : le contenu HTML d'un envoi précis (`uuid`, renvoyé par
          op="logs").
        - **"events"** : événements de délivrabilité — **la source de vérité par
          email**.
        - **"report"** : compteurs agrégés du transactionnel (envoyés, délivrés,
          ouverts, clics…). `by_day=False` (défaut) = un total sur la période ;
          `by_day=True` = une ligne par jour.
        - **"blocked"** : contacts bloqués (hard bounce, plainte spam,
          désinscription). Diagnostic de délivrabilité : **un contact bloqué ne
          reçoit plus rien, silencieusement.**
        - **"blocked_domains"** : les domaines bloqués du compte (liste simple,
          sans pagination).

        Args:
            op: logs (défaut) | content | events | report | blocked | blocked_domains.
            email: op="logs"/"events" — filtrer sur un destinataire.
            template_id: op="logs"/"events" — filtrer sur un template.
            message_id: op="logs" — filtrer sur un message.
            uuid: op="content" — l'uuid de l'envoi dont on veut le HTML.
            event: op="events" — `delivered` | `opened` | `clicks` | `hardBounces` |
                `softBounces` | `spam` | `blocked` | `unsubscribed` | `invalid` |
                `deferred` | `requests` | `error`. Omis = tous.
            days: op="events"/"report" — fenêtre glissante en jours (alternative
                aux dates).
            start_date: `YYYY-MM-DD`.
            end_date: `YYYY-MM-DD`.
            by_day: op="report" — une ligne par jour au lieu d'un total.
            tag: op="report" — restreindre à un tag d'envoi.
            limit: taille de page (logs, events, blocked).
            offset: pagination (logs, events, blocked).
        """
        client = _client()

        if op == "logs":
            return client.list_transactional_emails(
                email=email, template_id=template_id, message_id=message_id,
                start_date=start_date, end_date=end_date, limit=limit, offset=offset)
        if op == "content":
            return client.get_transactional_email_content(_need(uuid, "uuid", op))
        if op == "events":
            return client.transactional_events(
                event=event, email=email, template_id=template_id, days=days,
                start_date=start_date, end_date=end_date, limit=limit, offset=offset)
        if op == "report":
            return client.transactional_report(
                by_day=by_day, days=days, start_date=start_date, end_date=end_date,
                tag=tag)
        if op == "blocked":
            return client.list_blocked(domains=False, limit=limit, offset=offset)
        if op == "blocked_domains":
            return client.list_blocked(domains=True)
        raise _bad("op doit être 'logs', 'content', 'events', 'report', 'blocked' "
                   "ou 'blocked_domains'")

    @mcp.tool()
    def brevo_template(
        op: Literal["list", "create", "update"] = "list",
        template_id: Optional[int] = None,
        template_name: Optional[str] = None,
        subject: Optional[str] = None,
        sender: Optional[dict] = None,
        html_content: Optional[str] = None,
        reply_to: Optional[str] = None,
        tag: Optional[str] = None,
        is_active: Optional[bool] = None,
        active_only: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        """Un template transactionnel Brevo.

        `op` :
        - **"list"** (défaut) : les templates du compte. `template_id` → un seul,
          avec son HTML.
        - **"create"** : crée un template. Renvoie `{"id": …}`.
        - **"update"** : met à jour un template (champs fournis seulement).

        Args:
            op: list (défaut) | create | update.
            template_id: op="list" — n'en renvoyer qu'un (avec son HTML) ;
                op="update" — le template ciblé (obligatoire).
            template_name: op="create" (obligatoire) / "update" — le nom.
            subject: op="create" (obligatoire) / "update" — l'objet de l'email.
            sender: op="create" (obligatoire) / "update" — `{"email": …, "name": …}`,
                expéditeur **vérifié** du compte (cf. `brevo_account`).
            html_content: HTML du corps. Variables : `{{params.NOM}}`,
                `{{contact.PRENOM}}`.
            reply_to: op="create" — adresse de réponse.
            tag: op="create" — tag du template.
            is_active: op="create" (défaut `True`) / "update" — actif ou non.
            active_only: op="list" — ne lister que les templates actifs.
            limit: op="list" — taille de page.
            offset: op="list" — pagination.
        """
        client = _client()

        if op == "list":
            return client.list_templates(
                template_id=template_id, active_only=active_only or None,
                limit=limit, offset=offset)
        if op == "create":
            return client.create_template(
                template_name=_need(template_name, "template_name", op),
                subject=_need(subject, "subject", op),
                sender=_need(sender, "sender", op),
                html_content=html_content, reply_to=reply_to, tag=tag,
                is_active=True if is_active is None else is_active)
        if op == "update":
            return client.update_template(
                _need(template_id, "template_id", op), template_name=template_name,
                subject=subject, sender=sender, html_content=html_content,
                is_active=is_active)
        raise _bad("op doit être 'list', 'create' ou 'update'")

    # --- Campagnes email --------------------------------------------------------

    @mcp.tool()
    def brevo_campaign(
        op: Literal["list", "create", "update", "test", "report",
                    "ab_test"] = "list",
        campaign_id: Optional[int] = None,
        status: Optional[str] = None,
        statistics: Optional[str] = None,
        name: Optional[str] = None,
        sender: Optional[dict] = None,
        subject: Optional[str] = None,
        html_content: Optional[str] = None,
        template_id: Optional[int] = None,
        recipients: Optional[dict] = None,
        preview_text: Optional[str] = None,
        reply_to: Optional[str] = None,
        fields: Optional[dict] = None,
        email_to: Optional[list[str]] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        """Une campagne email Brevo — concevoir, mesurer, s'envoyer un test.

        `op` :
        - **"list"** (défaut) : les campagnes email. `campaign_id` → une seule
          campagne. **Le HTML est exclu des réponses** (volume) ; il reste lisible
          dans l'UI.
        - **"create"** : crée une campagne en **brouillon**. Renvoie `{"id": …}`.
          Ne l'envoie pas : l'envoi (`sendNow`) n'est volontairement pas exposé —
          le départ se déclenche depuis l'UI Brevo, après relecture. Utiliser
          op="test" pour se l'envoyer à soi d'abord.
        - **"update"** : met à jour une campagne **non encore envoyée**.
        - **"test"** : ⚠️ **envoie réellement** un test de la campagne aux adresses
          données (pas aux destinataires). Ces adresses doivent **exister comme
          contacts** du compte Brevo, sinon l'API refuse.
        - **"report"** : URL publique de partage d'une campagne envoyée.
        - **"ab_test"** : résultat d'A/B test d'une campagne.

        Args:
            op: list (défaut) | create | update | test | report | ab_test.
            campaign_id: op="list" (une seule campagne) / "update" / "test" /
                "report" / "ab_test" — la campagne ciblée.
            status: op="list" — `draft` | `sent` | `queued` | `suspended` |
                `archive` | `inProcess`.
            statistics: op="list" — `globalStats` | `linksStats` | `statsByDomain` |
                `statsByDevice` | `statsByBrowser` — joint les stats.
            name: op="create" (obligatoire) — nom de la campagne.
            sender: op="create" (obligatoire) — `{"email": …, "name": …}`,
                expéditeur **vérifié** (cf. `brevo_account`).
            subject: op="create" — l'objet de l'email.
            html_content: op="create" — le HTML du corps.
            template_id: op="create" — partir d'un template plutôt que d'un
                `html_content`.
            recipients: op="create" — `{"listIds": [1,2], "exclusionListIds": [3]}`.
            preview_text: op="create" — le pré-header.
            reply_to: op="create" — adresse de réponse.
            fields: op="update" — clés camelCase Brevo : `name`, `subject`,
                `htmlContent`, `sender`, `recipients`, `previewText`.
            email_to: op="test" — les adresses du test (contacts existants).
            limit: op="list" — taille de page.
            offset: op="list" — pagination.
        """
        client = _client()

        if op == "list":
            if campaign_id is not None:
                return client.get_campaign(campaign_id, statistics=statistics)
            return client.list_campaigns(
                status=status, statistics=statistics, limit=limit, offset=offset)
        if op == "create":
            return client.create_campaign(
                name=_need(name, "name", op), sender=_need(sender, "sender", op),
                subject=subject, html_content=html_content, template_id=template_id,
                recipients=recipients, preview_text=preview_text, reply_to=reply_to)
        if op == "update":
            return client.update_campaign(_need(campaign_id, "campaign_id", op),
                                          **_need(fields, "fields", op))
        if op == "test":
            return client.send_campaign_test(_need(campaign_id, "campaign_id", op),
                                             _need(email_to, "email_to", op))
        if op == "report":
            return client.campaign_shared_url(_need(campaign_id, "campaign_id", op))
        if op == "ab_test":
            return client.campaign_ab_test_result(
                _need(campaign_id, "campaign_id", op))
        raise _bad("op doit être 'list', 'create', 'update', 'test', 'report' "
                   "ou 'ab_test'")
