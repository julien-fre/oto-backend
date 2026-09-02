"""Outils Productlane — retours clients, roadmap, changelogs, centre d'aide.

Wrappe `oto.tools.productlane.client.ProductlaneClient` (API **v2**, Bearer).
Huit outils, un par famille de l'API amont.

Trois choses à savoir avant de lire un résultat :

- ⚠️ **La roadmap est adossée à Linear.** Projets et issues naissent dans Linear
  puis sont reflétés ici ; `team_id`, `state_id`, `assignee_id` et
  `linear_status_id` sont des identifiants LINEAR. Surtout : une écriture peut
  réussir localement pendant que la synchro Linear échoue — l'éditeur la
  journalise de son côté et ne la remonte pas. Un succès ne prouve donc pas que
  Linear a suivi.

- ⚠️ **Un seul geste écrit à des tiers** : `productlane_changelogs
  op='broadcast'`, qui envoie un email aux contacts abonnés et publie dans
  Slack. Il est en **dry-run par défaut**, comme l'envoi d'email de `lightfield`
  et le lancement de campagne d'`origami` — les trois seuls appels du dépôt qui
  sortent de l'organisation.

- **Pagination par curseur**, jamais par numéro de page : chaque liste rend
  `{data, page:{cursor, has_more}}`, et c'est `has_more` qui dit s'il reste
  quelque chose — pas la taille de `data`, qu'une dernière page peut rendre vide.

Les appels au client sont écrits en clair (`_client().list_threads(…)`) : c'est
ce qui les rend vérifiables par la sonde version-skew
(`test_tools_client_methods_exist`).
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from fastmcp import FastMCP
from ..mcp_errors import McpError
from mcp.types import ErrorData, INVALID_PARAMS

from .. import access
from ..connectors import verify as connector_verify


def _bad(msg: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=msg))


def _upstream_message(e) -> str:
    """Traduit un refus de Productlane en message actionnable.

    L'enveloppe d'erreur v2 porte `{error: {code, message, request_id}}` : le
    `code` et le `request_id` sont ce qui permet au client de retrouver l'appel
    dans ses journaux, donc ils sont remontés tels quels.
    """
    status = e.status_code
    body = e.body if isinstance(e.body, dict) else {}
    err = body.get("error") if isinstance(body.get("error"), dict) else {}
    code = err.get("code") or body.get("code")
    rid = err.get("request_id") or body.get("request_id")
    suffixe = f" [request_id {rid}]" if rid else ""
    if status == 401:
        return ("Productlane a rejeté la clé (401) — vérifie la clé configurée "
                "sur ce connecteur. ⚠️ Une clé **v1** ne marche pas ici : l'API "
                "v2 est distincte, et sa clé se crée à part." + suffixe)
    if status == 403:
        return (f"Productlane a refusé l'accès (403{f', {code}' if code else ''}) "
                "— la clé existe mais il lui manque le scope de cette opération, "
                "ou le plan de l'espace de travail ne la couvre pas (les "
                "extraits et certaines étiquettes demandent Pro ou Scale)."
                + suffixe)
    if status == 404:
        return f"Productlane : ressource introuvable (404) — vérifie l'identifiant.{suffixe}"
    if status in (400, 422):
        if code == "validation_failed":
            return ("Productlane a refusé la demande (validation_failed) — sur "
                    "l'envoi d'un message, cela veut souvent dire que "
                    "l'intégration du canal déduit (email, Slack, Teams) n'est "
                    "pas configurée pour cet espace de travail, pas que le "
                    f"contenu est mauvais.{suffixe}")
        return (f"Productlane a refusé la requête (HTTP {status}"
                f"{f', {code}' if code else ''}) : {e.body}{suffixe}")
    if status == 429:
        return ("Productlane : trop de requêtes (429) — 1000 lectures/minute et "
                "60 écritures/minute par clé. Réessaie dans un instant." + suffixe)
    if status in (500, 502, 503, 504):
        return (f"Productlane est momentanément indisponible (HTTP {status}) — "
                f"réessaie plus tard.{suffixe}")
    return f"Productlane a refusé la requête (HTTP {status}): {e.body}{suffixe}"


def _verify(fields: dict, config: dict | None = None) -> None:  # noqa: ARG001
    """Sonde « tester la connexion » : `GET /me`.

    Appelable par TOUTE clé authentifiée, quels que soient ses scopes — c'est ce
    qui en fait la bonne sonde : elle sépare « clé invalide » (401) de « clé
    valide mais sans le droit demandé » (403 ailleurs). Sonder une ressource
    confondrait les deux et ferait afficher rouge sur une clé saine mais
    volontairement restreinte.

    Elle rend en prime les scopes accordés, donc de quoi expliquer un refus
    AVANT de le provoquer.
    """
    from oto.tools.productlane.client import ProductlaneClient
    ProductlaneClient(api_key=fields["key"]).me()


def register(mcp: FastMCP) -> None:
    from oto.tools.common.errors import UpstreamHTTPError
    from oto.tools.productlane.client import ProductlaneClient

    connector_verify.register("productlane", _verify)

    def _client() -> ProductlaneClient:
        key, _ = access.resolve_api_key("productlane")
        return ProductlaneClient(api_key=key)

    def _run(fn):
        try:
            return fn()
        except ValueError as e:
            raise _bad(str(e))
        except UpstreamHTTPError as e:
            raise _bad(_upstream_message(e))

    def _need(value, nom: str, op: str):
        if not value:
            raise _bad(f"op='{op}' : `{nom}` requis.")
        return value

    def _bad_op(op: str, attendus: str):
        return _bad(f"`op` invalide : {op!r} (attendu : {attendus}).")

    # --- fils ----------------------------------------------------------------

    @mcp.tool()
    def productlane_threads(
        op: Literal["search", "get", "create", "update", "delete",
                    "messages", "send", "comments", "comment",
                    "update_comment", "delete_comment", "link"] = "search",
        thread_id: Optional[str] = None,
        comment_id: Optional[str] = None,
        status: Optional[str] = None,
        tab: Optional[str] = None,
        pain_level: Optional[str] = None,
        origin: Optional[str] = None,
        contact_id: Optional[str] = None,
        company_id: Optional[str] = None,
        assignee_id: Optional[str] = None,
        tag_id: Optional[str] = None,
        issue_ids: Optional[list[str]] = None,
        project_ids: Optional[list[str]] = None,
        expand: Optional[list[str]] = None,
        content: Optional[str] = None,
        fields: Optional[dict] = None,
        created_after: Optional[str] = None,
        created_before: Optional[str] = None,
        cursor: Optional[str] = None,
        limit: int = 50,
    ) -> Any:
        """Productlane — les fils de retour client : ce que les clients ont dit.

        C'est la boîte de réception du produit. Un fil porte une conversation
        (messages, tous canaux fondus), des commentaires internes, une douleur
        (`pain_level`) et des liens vers la roadmap.

        ⚠️ **Deux plans qu'il ne faut pas confondre** : `send` envoie un message
        AU CLIENT, par le canal d'où vient le fil (email, Slack, live chat,
        Teams) — c'est une communication sortante réelle. `comment` écrit une
        note visible de l'équipe seulement. Rien dans la forme des deux appels ne
        le rappelle : c'est le nom de l'`op` qui le dit.

        `op`:
        - `search` — liste les fils (filtres : statut, onglet, douleur, origine,
          contact, entreprise, assigné, étiquette, fenêtre de dates).
        - `get` — un fil ; `expand=['messages','comments']` inline la conversation.
        - `create` — ouvre un fil et **upsert son contact par email**
          (`fields` : text, pain_level, contact_email requis).
        - `update` — met à jour (`fields`). ⚠️ `tag_ids` REMPLACE les étiquettes.
        - `delete` — soft-delete.
        - `messages` — la conversation du fil, du plus ancien au plus récent.
        - `send` — ⚠️ **envoie un message au client** (`content`).
        - `comments` / `comment` / `update_comment` / `delete_comment` —
          les notes INTERNES du fil.
        - `link` — relie le fil à des issues et/ou projets Linear
          (`issue_ids`/`project_ids`) : c'est le geste qui transforme un retour
          en demande tracée sur la roadmap, et qui fait monter le score d'un projet.

        Args:
            op: l'opération, cf. ci-dessus.
            thread_id: le fil visé (toutes les op sauf search et create).
            comment_id: le commentaire visé (update_comment, delete_comment).
            status: op='search'/'create'/'update' — open | snoozed | done.
            tab: op='search' — open | new | needs-response | my | snoozed | done.
            pain_level: UNKNOWN | LOW | MEDIUM | HIGH.
            origin: op='search'/'create' — canal d'origine (email, slack, portal…).
            contact_id: op='search' — filtre par contact.
            company_id: op='search' — filtre par entreprise.
            assignee_id: op='search' — filtre par assigné.
            tag_id: op='search' — filtre par étiquette.
            issue_ids: op='link' — issues Linear à relier.
            project_ids: op='link' — projets Linear à relier.
            expand: op='get' — messages et/ou comments à inliner.
            content: op='send'/'comment'/'update_comment' — le texte.
            fields: op='create'/'update' — le corps du fil.
            created_after: op='search' — borne basse (ISO 8601).
            created_before: op='search' — borne haute (ISO 8601).
            cursor: page suivante (rendu par `page.cursor`).
            limit: lignes par page (1-200, défaut 50).
        """
        c = _client()
        if op == "search":
            return _run(lambda: c.list_threads(
                limit=limit, cursor=cursor, status=status, tab=tab,
                pain_level=pain_level, origin=origin, contact_id=contact_id,
                company_id=company_id, assignee_id=assignee_id, tag_id=tag_id,
                created_after=created_after, created_before=created_before))
        if op == "get":
            _need(thread_id, "thread_id", op)
            return _run(lambda: c.get_thread(thread_id, expand=expand))
        if op == "create":
            _need(fields, "fields", op)
            return _run(lambda: c.create_thread(fields))
        if op == "update":
            _need(thread_id, "thread_id", op)
            _need(fields, "fields", op)
            return _run(lambda: c.update_thread(thread_id, fields))
        if op == "delete":
            _need(thread_id, "thread_id", op)
            return _run(lambda: c.delete_thread(thread_id))
        if op == "messages":
            _need(thread_id, "thread_id", op)
            return _run(lambda: c.list_messages(thread_id, limit=limit,
                                                cursor=cursor))
        if op == "send":
            _need(thread_id, "thread_id", op)
            _need(content, "content", op)
            return _run(lambda: c.send_message(thread_id,
                                               dict(fields or {}, content=content)))
        if op == "comments":
            _need(thread_id, "thread_id", op)
            return _run(lambda: c.list_comments(thread_id, limit=limit,
                                                cursor=cursor))
        if op == "comment":
            _need(thread_id, "thread_id", op)
            _need(content, "content", op)
            return _run(lambda: c.post_comment(thread_id, content))
        if op == "update_comment":
            _need(thread_id, "thread_id", op)
            _need(comment_id, "comment_id", op)
            _need(content, "content", op)
            return _run(lambda: c.update_comment(thread_id, comment_id,
                                                 {"content": content}))
        if op == "delete_comment":
            _need(thread_id, "thread_id", op)
            _need(comment_id, "comment_id", op)
            return _run(lambda: c.delete_comment(thread_id, comment_id))
        if op == "link":
            _need(thread_id, "thread_id", op)
            return _run(lambda: c.link_thread(thread_id, issue_ids=issue_ids,
                                              project_ids=project_ids))
        raise _bad_op(op, "search | get | create | update | delete | messages | "
                          "send | comments | comment | update_comment | "
                          "delete_comment | link")

    # --- contacts -------------------------------------------------------------

    @mcp.tool()
    def productlane_contacts(
        op: Literal["search", "get", "create", "update", "delete",
                    "companies", "add_company", "remove_company",
                    "issues", "projects",
                    "blocked", "block", "unblock"] = "search",
        contact_id: Optional[str] = None,
        company_id: Optional[str] = None,
        blocked_id: Optional[str] = None,
        email: Optional[str] = None,
        name_contains: Optional[str] = None,
        external_id: Optional[str] = None,
        block_type: Optional[str] = None,
        value: Optional[str] = None,
        fields: Optional[dict] = None,
        cursor: Optional[str] = None,
        limit: int = 50,
    ) -> Any:
        """Productlane — les personnes qui ont écrit, et leurs entreprises.

        Un contact relie une conversation à une organisation cliente : c'est ce
        qui permet de dire « ce retour vient d'un client à tel niveau ».

        ⚠️ **Bloquer coupe la communication sans prévenir l'intéressé.** Un
        expéditeur bloqué ne peut plus ouvrir de fil ni écrire sur un fil
        existant, et n'en est pas informé. `block_type='DOMAIN'` coupe **toute une
        organisation** d'un seul appel — à ne pas confondre avec `'EMAIL'`, qui
        ne vise qu'une adresse.

        `op`:
        - `search` / `get` / `create` / `update` / `delete` — le contact.
          ⚠️ `update` avec `is_subscribed: false` **désabonne** des diffusions de
          changelog : c'est une préférence de communication, pas un champ neutre.
        - `companies` / `add_company` / `remove_company` — ses appartenances.
          L'ajout est idempotent et devient la principale s'il n'en avait aucune.
        - `issues` / `projects` — ce à quoi ses fils sont reliés sur la roadmap.
        - `blocked` / `block` / `unblock` — les expéditeurs bloqués.

        Args:
            op: l'opération, cf. ci-dessus.
            contact_id: le contact visé.
            company_id: l'entreprise (add_company, remove_company).
            blocked_id: l'entrée bloquée à retirer (unblock).
            email: op='search' — filtre par email exact.
            name_contains: op='search' — filtre par nom partiel.
            external_id: op='search' — filtre par identifiant externe.
            block_type: op='block'/'blocked' — EMAIL (une adresse) ou DOMAIN (tout un domaine).
            value: op='block' — l'adresse ou le domaine à bloquer.
            fields: op='create'/'update' — le corps du contact (email requis à la création).
            cursor: page suivante.
            limit: lignes par page (1-200, défaut 50).
        """
        c = _client()
        if op == "search":
            return _run(lambda: c.list_contacts(
                limit=limit, cursor=cursor, email=email,
                name_contains=name_contains, company_id=company_id,
                external_id=external_id))
        if op == "get":
            _need(contact_id, "contact_id", op)
            return _run(lambda: c.get_contact(contact_id))
        if op == "create":
            _need(fields, "fields", op)
            return _run(lambda: c.create_contact(fields))
        if op == "update":
            _need(contact_id, "contact_id", op)
            _need(fields, "fields", op)
            return _run(lambda: c.update_contact(contact_id, fields))
        if op == "delete":
            _need(contact_id, "contact_id", op)
            return _run(lambda: c.delete_contact(contact_id))
        if op == "companies":
            _need(contact_id, "contact_id", op)
            return _run(lambda: c.list_contact_companies(contact_id))
        if op == "add_company":
            _need(contact_id, "contact_id", op)
            return _run(lambda: c.add_contact_to_company(
                contact_id, company_id=company_id,
                company_name=(fields or {}).get("company_name"),
                company_external_id=(fields or {}).get("company_external_id")))
        if op == "remove_company":
            _need(contact_id, "contact_id", op)
            _need(company_id, "company_id", op)
            return _run(lambda: c.remove_contact_from_company(contact_id,
                                                              company_id))
        if op == "issues":
            _need(contact_id, "contact_id", op)
            return _run(lambda: c.list_contact_issues(contact_id, limit=limit,
                                                      cursor=cursor))
        if op == "projects":
            _need(contact_id, "contact_id", op)
            return _run(lambda: c.list_contact_projects(contact_id, limit=limit,
                                                        cursor=cursor))
        if op == "blocked":
            return _run(lambda: c.list_blocked_senders(limit=limit,
                                                       cursor=cursor,
                                                       type=block_type))
        if op == "block":
            _need(block_type, "block_type", op)
            _need(value, "value", op)
            return _run(lambda: c.block_sender(block_type, value))
        if op == "unblock":
            _need(blocked_id, "blocked_id", op)
            return _run(lambda: c.unblock_sender(blocked_id))
        raise _bad_op(op, "search | get | create | update | delete | companies | "
                          "add_company | remove_company | issues | projects | "
                          "blocked | block | unblock")

    # --- entreprises ----------------------------------------------------------

    @mcp.tool()
    def productlane_companies(
        op: Literal["search", "get", "create", "update", "delete",
                    "merge", "linear_options"] = "search",
        company_id: Optional[str] = None,
        source_id: Optional[str] = None,
        domain: Optional[str] = None,
        name_contains: Optional[str] = None,
        external_id: Optional[str] = None,
        fields: Optional[dict] = None,
        cursor: Optional[str] = None,
        limit: int = 50,
    ) -> Any:
        """Productlane — les entreprises clientes, jumelées aux customers Linear.

        ⚠️ **Le miroir Linear est asynchrone** : le customer est provisionné une
        fois un domaine posé, les changements d'identité s'y propagent plus tard,
        et une suppression y agit après coup. Ne rien voir côté Linear juste
        après un appel est un délai, pas une panne.

        ⚠️ `merge` est **irréversible, et le sens compte** : l'entreprise
        `company_id` SURVIT, celle de `source_id` est supprimée. Ses fils,
        contacts et votes passent à la survivante, dont seules les propriétés
        VIDES sont complétées.

        `op`: `search` | `get` | `create` | `update` | `delete` (soft) |
        `merge` | `linear_options` (statuts et tiers Linear disponibles —
        rend `null` si Linear n'est pas connecté, ce qui est une réponse et pas
        une erreur).

        Args:
            op: l'opération, cf. ci-dessus.
            company_id: l'entreprise visée — sur merge, celle qui SURVIT.
            source_id: op='merge' — l'entreprise ABSORBÉE (supprimée).
            domain: op='search' — filtre par domaine.
            name_contains: op='search' — filtre par nom partiel.
            external_id: op='search' — filtre par identifiant externe.
            fields: op='create'/'update' — le corps (name requis à la création).
            cursor: page suivante.
            limit: lignes par page (1-200, défaut 50).
        """
        c = _client()
        if op == "search":
            return _run(lambda: c.list_companies(
                limit=limit, cursor=cursor, domain=domain,
                name_contains=name_contains, external_id=external_id))
        if op == "get":
            _need(company_id, "company_id", op)
            return _run(lambda: c.get_company(company_id))
        if op == "create":
            _need(fields, "fields", op)
            return _run(lambda: c.create_company(fields))
        if op == "update":
            _need(company_id, "company_id", op)
            _need(fields, "fields", op)
            return _run(lambda: c.update_company(company_id, fields))
        if op == "delete":
            _need(company_id, "company_id", op)
            return _run(lambda: c.delete_company(company_id))
        if op == "merge":
            _need(company_id, "company_id", op)
            _need(source_id, "source_id", op)
            return _run(lambda: c.merge_company(company_id, source_id))
        if op == "linear_options":
            return _run(lambda: c.linear_customer_options())
        raise _bad_op(op, "search | get | create | update | delete | merge | "
                          "linear_options")

    # --- roadmap --------------------------------------------------------------

    @mcp.tool()
    def productlane_roadmap(
        op: Literal["projects", "project", "create_project", "update_project",
                    "delete_project", "statuses",
                    "issues", "issue", "create_issue", "update_issue",
                    "delete_issue", "workflows"] = "projects",
        project_id: Optional[str] = None,
        issue_id: Optional[str] = None,
        team_id: Optional[str] = None,
        state: Optional[str] = None,
        status: Optional[str] = None,
        name_contains: Optional[str] = None,
        sort: Optional[str] = None,
        fields: Optional[dict] = None,
        cursor: Optional[str] = None,
        limit: int = 50,
    ) -> Any:
        """Productlane — la roadmap : projets et issues, **adossés à Linear**.

        ⚠️ **Linear doit être connecté**, et trois asymétries en découlent :
        la CRÉATION part de Linear (sans lui, elle échoue) ; la mise à jour et la
        suppression réussissent localement **même si la synchro Linear échoue**
        (l'éditeur la journalise et ne la remonte pas) ; et `team_id`,
        `state_id`, `assignee_id`, `linear_status_id` sont des identifiants
        LINEAR, à lire par `op='workflows'` / `op='statuses'` ou via le
        connecteur Linear.

        ⚠️ `sort='total_score'` classe par poids des retours clients rattachés —
        c'est la vraie question « qu'est-ce que nos clients demandent le plus ? »,
        que l'ordre par date ne répond pas.

        ⚠️ Sur une issue, `status` n'est PAS une énumération fixe : ce sont les
        workflow states de l'équipe Linear, propres à chaque espace de travail.
        Les lire par `op='workflows'` plutôt que d'en coder un en dur.

        ⚠️ `priority` suit la numérotation Linear : `0` = aucune, `1` = urgente,
        puis 2, 3, 4 par urgence décroissante. Ce n'est pas une échelle croissante.

        Args:
            op: l'opération, cf. ci-dessus.
            project_id: le projet visé.
            issue_id: l'issue visée.
            team_id: op='workflows' (requis) et création — l'équipe LINEAR.
            state: op='projects'/'create_project' — backlog | planned | started |
                completed | canceled.
            status: op='issues' — workflow state Linear (cf. op='workflows').
            name_contains: filtre par nom partiel.
            sort: created_at | total_score.
            fields: corps de création ou de mise à jour.
            cursor: page suivante.
            limit: lignes par page (1-200, défaut 50).
        """
        c = _client()
        if op == "projects":
            return _run(lambda: c.list_projects(
                limit=limit, cursor=cursor, state=state,
                name_contains=name_contains, sort=sort))
        if op == "project":
            _need(project_id, "project_id", op)
            return _run(lambda: c.get_project(project_id))
        if op == "create_project":
            _need(fields, "fields", op)
            return _run(lambda: c.create_project(fields))
        if op == "update_project":
            _need(project_id, "project_id", op)
            _need(fields, "fields", op)
            return _run(lambda: c.update_project(project_id, fields))
        if op == "delete_project":
            _need(project_id, "project_id", op)
            return _run(lambda: c.delete_project(project_id))
        if op == "statuses":
            return _run(lambda: c.list_project_statuses())
        if op == "issues":
            return _run(lambda: c.list_issues(
                limit=limit, cursor=cursor, project_id=project_id,
                status=status, name_contains=name_contains, sort=sort))
        if op == "issue":
            _need(issue_id, "issue_id", op)
            return _run(lambda: c.get_issue(issue_id))
        if op == "create_issue":
            _need(fields, "fields", op)
            return _run(lambda: c.create_issue(fields))
        if op == "update_issue":
            _need(issue_id, "issue_id", op)
            _need(fields, "fields", op)
            return _run(lambda: c.update_issue(issue_id, fields))
        if op == "delete_issue":
            _need(issue_id, "issue_id", op)
            return _run(lambda: c.delete_issue(issue_id))
        if op == "workflows":
            _need(team_id, "team_id", op)
            return _run(lambda: c.list_workflow_states(team_id))
        raise _bad_op(op, "projects | project | create_project | update_project | "
                          "delete_project | statuses | issues | issue | "
                          "create_issue | update_issue | delete_issue | workflows")

    # --- changelogs -----------------------------------------------------------

    @mcp.tool()
    def productlane_changelogs(
        op: Literal["search", "get", "create", "update", "delete", "broadcast",
                    "tags", "create_tag", "update_tag",
                    "delete_tag"] = "search",
        changelog_id: Optional[str] = None,
        tag_id: Optional[str] = None,
        published: Optional[bool] = None,
        language: Optional[str] = None,
        title_contains: Optional[str] = None,
        fields: Optional[dict] = None,
        email: bool = False,
        slack: bool = False,
        subject: Optional[str] = None,
        message: Optional[str] = None,
        sender_name: Optional[str] = None,
        from_email: Optional[str] = None,
        dry_run: bool = True,
        cursor: Optional[str] = None,
        limit: int = 50,
    ) -> Any:
        """Productlane — les notes de version, et leur diffusion aux abonnés.

        ⚠️ **`op='broadcast'` est le seul appel de ce connecteur qui écrit à des
        tiers** : email aux contacts abonnés et/ou publication dans les canaux
        Slack configurés. Sans annulation ni rappel possible. Il est donc en
        **dry-run par défaut** — `dry_run=false` pour envoyer pour de vrai.

        ⚠️ **Publier et diffuser sont deux gestes distincts**, et l'éditeur est
        formel : la diffusion ne touche JAMAIS `published`. On peut donc diffuser
        un changelog non publié, et les destinataires recevraient un lien vers
        une page invisible. Publier = `op='update'` avec `{"published": true}`.

        `op`: `search` | `get` | `create` | `update` | `delete` (soft) |
        `broadcast` | `tags` | `create_tag` | `update_tag` | `delete_tag`.

        ⚠️ Les étiquettes de changelog ne sont PAS celles des fils
        (`productlane_tags`) : deux familles distinctes côté amont, et l'une
        demande le plan Scale. `delete_tag` est une suppression DURE, qui détache
        aussi l'étiquette de tous les changelogs.

        Args:
            op: l'opération, cf. ci-dessus.
            changelog_id: le changelog visé.
            tag_id: l'étiquette de changelog visée.
            published: op='search' — filtre publiés / non publiés.
            language: op='search'/'get' — sert la ligne de traduction.
            title_contains: op='search' — filtre par titre partiel.
            fields: op='create'/'update'/'update_tag' — le corps.
            email: op='broadcast' — écrire aux contacts abonnés.
            slack: op='broadcast' — publier dans les canaux Slack.
            subject: op='broadcast' — objet de l'email.
            message: op='broadcast' — texte d'accompagnement.
            sender_name: op='broadcast' — nom d'expéditeur affiché.
            from_email: op='broadcast' — adresse d'expédition.
            dry_run: op='broadcast' — True (défaut) décrit l'envoi sans le faire.
            cursor: page suivante.
            limit: lignes par page (1-200, défaut 50).
        """
        c = _client()
        if op == "search":
            return _run(lambda: c.list_changelogs(
                limit=limit, cursor=cursor, published=published,
                language=language, title_contains=title_contains))
        if op == "get":
            _need(changelog_id, "changelog_id", op)
            return _run(lambda: c.get_changelog(changelog_id, language=language))
        if op == "create":
            _need(fields, "fields", op)
            return _run(lambda: c.create_changelog(fields))
        if op == "update":
            _need(changelog_id, "changelog_id", op)
            _need(fields, "fields", op)
            return _run(lambda: c.update_changelog(changelog_id, fields))
        if op == "delete":
            _need(changelog_id, "changelog_id", op)
            return _run(lambda: c.delete_changelog(changelog_id))
        if op == "broadcast":
            _need(changelog_id, "changelog_id", op)
            if not email and not slack:
                raise _bad("op='broadcast' : choisis au moins un canal — "
                           "`email=true` (contacts abonnés) et/ou `slack=true`.")
            if dry_run:
                return {
                    "dry_run": True,
                    "would": "diffuser ce changelog à des tiers",
                    "changelog_id": changelog_id,
                    "canaux": [n for n, v in (("email", email),
                                              ("slack", slack)) if v],
                    "subject": subject, "sender_name": sender_name,
                    "from_email": from_email, "message": message,
                    "avertissement": ("l'envoi est irréversible et ne modifie "
                                      "PAS `published` — un changelog non publié "
                                      "serait diffusé vers une page invisible"),
                    "pour_envoyer": "rappeler avec dry_run=false",
                }
            return _run(lambda: c.broadcast_changelog(
                changelog_id, email=email or None, slack=slack or None,
                message=message, subject=subject, sender_name=sender_name,
                from_email=from_email))
        if op == "tags":
            return _run(lambda: c.list_changelog_tags())
        if op == "create_tag":
            _need(fields, "fields", op)
            return _run(lambda: c.create_changelog_tag(
                fields.get("name"), color=fields.get("color"),
                icon=fields.get("icon")))
        if op == "update_tag":
            _need(tag_id, "tag_id", op)
            _need(fields, "fields", op)
            return _run(lambda: c.update_changelog_tag(tag_id, fields))
        if op == "delete_tag":
            _need(tag_id, "tag_id", op)
            return _run(lambda: c.delete_changelog_tag(tag_id))
        raise _bad_op(op, "search | get | create | update | delete | broadcast | "
                          "tags | create_tag | update_tag | delete_tag")

    # --- centre d'aide --------------------------------------------------------

    @mcp.tool()
    def productlane_docs(
        op: Literal["articles", "article", "create", "update", "delete", "move",
                    "groups", "create_group", "update_group", "delete_group",
                    "drafts", "draft", "create_draft", "accept",
                    "decline"] = "articles",
        article_id: Optional[str] = None,
        group_id: Optional[str] = None,
        draft_id: Optional[str] = None,
        article_ids: Optional[list[str]] = None,
        visibility: Optional[str] = None,
        kind: Optional[str] = None,
        status: Optional[str] = None,
        published: Optional[bool] = None,
        title_contains: Optional[str] = None,
        language: Optional[str] = None,
        fields: Optional[dict] = None,
        cursor: Optional[str] = None,
        limit: int = 50,
    ) -> Any:
        """Productlane — le centre d'aide : articles, groupes, et relecture.

        Deux chemins d'écriture, qui ne servent pas la même chose : l'écriture
        DIRECTE (`create` / `update` / `delete`) applique tout de suite ; le
        BROUILLON (`create_draft` puis `accept` ou `decline`) propose un
        changement à relire.

        ⚠️ **`accept` peut répondre `superseded` au lieu de `accepted`** : le
        brouillon ne s'applique plus proprement parce que l'article a bougé
        sous lui. C'est un succès HTTP qui n'a **rien appliqué** — lire le statut
        rendu, pas seulement l'absence d'erreur.

        ⚠️ La visibilité n'est pas binaire : `public`, `agent` (visible des
        agents IA), `internal`, `unlisted`. `all` n'existe qu'en filtre de liste
        — un article ne peut pas « être » de visibilité `all`.

        ⚠️ `update` avec `allow_image_removal` autorise la réécriture à
        SUPPRIMER des images absentes du nouveau contenu ; sans lui, elles sont
        conservées. C'est un garde-fou de l'éditeur contre une perte par recopie
        partielle — le désactiver est une décision.

        Args:
            op: l'opération, cf. ci-dessus.
            article_id: l'article visé.
            group_id: le groupe visé (ou la cible d'un move ; null dégroupe).
            draft_id: le brouillon visé (draft, accept, decline).
            article_ids: op='move' — les articles à déplacer.
            visibility: public | agent | internal | unlisted (+ all en filtre).
            kind: op='articles' — doc | link | all. op='create_draft' — edit | create | delete.
            status: op='drafts' — draft | open | accepted | rejected | superseded.
            published: op='articles' — filtre publiés / non publiés.
            title_contains: op='articles' — filtre par titre partiel.
            language: sert ou écrit une ligne de traduction.
            fields: corps de création ou de mise à jour (content en markdown).
            cursor: page suivante.
            limit: lignes par page (1-200, défaut 50).
        """
        c = _client()
        if op == "articles":
            return _run(lambda: c.list_articles(
                limit=limit, cursor=cursor, group_id=group_id,
                published=published, visibility=visibility, kind=kind,
                title_contains=title_contains, language=language))
        if op == "article":
            _need(article_id, "article_id", op)
            return _run(lambda: c.get_article(article_id, language=language))
        if op == "create":
            _need(fields, "fields", op)
            return _run(lambda: c.create_article(fields))
        if op == "update":
            _need(article_id, "article_id", op)
            _need(fields, "fields", op)
            return _run(lambda: c.update_article(article_id, fields))
        if op == "delete":
            _need(article_id, "article_id", op)
            return _run(lambda: c.delete_article(article_id))
        if op == "move":
            _need(article_ids, "article_ids", op)
            return _run(lambda: c.move_articles(article_ids, group_id))
        if op == "groups":
            return _run(lambda: c.list_groups())
        if op == "create_group":
            _need(fields, "fields", op)
            return _run(lambda: c.create_group(
                fields.get("name"),
                portal_instance_id=fields.get("portal_instance_id")))
        if op == "update_group":
            _need(group_id, "group_id", op)
            _need(fields, "fields", op)
            return _run(lambda: c.update_group(group_id, fields))
        if op == "delete_group":
            _need(group_id, "group_id", op)
            return _run(lambda: c.delete_group(group_id))
        if op == "drafts":
            return _run(lambda: c.list_drafts(
                limit=limit, cursor=cursor, kind=kind, status=status,
                article_id=article_id, group_id=group_id))
        if op == "draft":
            _need(draft_id, "draft_id", op)
            return _run(lambda: c.get_draft(draft_id))
        if op == "create_draft":
            _need(fields, "fields", op)
            return _run(lambda: c.create_draft(fields))
        if op == "accept":
            _need(draft_id, "draft_id", op)
            return _run(lambda: c.accept_draft(draft_id))
        if op == "decline":
            _need(draft_id, "draft_id", op)
            return _run(lambda: c.decline_draft(draft_id))
        raise _bad_op(op, "articles | article | create | update | delete | move | "
                          "groups | create_group | update_group | delete_group | "
                          "drafts | draft | create_draft | accept | decline")

    # --- étiquettes de fil ----------------------------------------------------

    @mcp.tool()
    def productlane_tags(
        op: Literal["list", "get", "create", "update", "delete",
                    "groups", "group", "create_group", "update_group",
                    "delete_group"] = "list",
        tag_id: Optional[str] = None,
        group_id: Optional[str] = None,
        name_contains: Optional[str] = None,
        fields: Optional[dict] = None,
        cursor: Optional[str] = None,
        limit: int = 50,
    ) -> Any:
        """Productlane — les étiquettes de FIL et leurs groupes.

        ⚠️ Ce ne sont pas les étiquettes de changelog (`productlane_changelogs
        op='tags'`) : deux familles distinctes côté amont, aux règles
        différentes. Celles-ci vivent toujours dans un groupe — `tag_group_id`
        est obligatoire à la création, avec `name`, `color` et `icon`.

        `op`: `list` | `get` | `create` | `update` | `delete` (soft — l'étiquette
        est retirée de tous les fils) | `groups` | `group` | `create_group` |
        `update_group` | `delete_group` (le groupe doit être VIDE).

        Args:
            op: l'opération, cf. ci-dessus.
            tag_id: l'étiquette visée.
            group_id: le groupe d'étiquettes visé.
            name_contains: op='list' — filtre par nom partiel.
            fields: le corps — création d'étiquette : name, color, icon,
                tag_group_id (les quatre requis) ; groupe : name, color.
            cursor: page suivante.
            limit: lignes par page (1-200, défaut 50).
        """
        c = _client()
        f = fields or {}
        if op == "list":
            return _run(lambda: c.list_tags(limit=limit, cursor=cursor,
                                            name_contains=name_contains,
                                            tag_group_id=group_id))
        if op == "get":
            _need(tag_id, "tag_id", op)
            return _run(lambda: c.get_tag(tag_id))
        if op == "create":
            _need(fields, "fields", op)
            return _run(lambda: c.create_tag(
                f.get("name"), f.get("color"), f.get("icon"),
                f.get("tag_group_id") or group_id))
        if op == "update":
            _need(tag_id, "tag_id", op)
            _need(fields, "fields", op)
            return _run(lambda: c.update_tag(tag_id, fields))
        if op == "delete":
            _need(tag_id, "tag_id", op)
            return _run(lambda: c.delete_tag(tag_id))
        if op == "groups":
            return _run(lambda: c.list_tag_groups(limit=limit, cursor=cursor))
        if op == "group":
            _need(group_id, "group_id", op)
            return _run(lambda: c.get_tag_group(group_id))
        if op == "create_group":
            _need(fields, "fields", op)
            return _run(lambda: c.create_tag_group(f.get("name"), f.get("color")))
        if op == "update_group":
            _need(group_id, "group_id", op)
            _need(fields, "fields", op)
            return _run(lambda: c.update_tag_group(group_id, fields))
        if op == "delete_group":
            _need(group_id, "group_id", op)
            return _run(lambda: c.delete_tag_group(group_id))
        raise _bad_op(op, "list | get | create | update | delete | groups | "
                          "group | create_group | update_group | delete_group")

    # --- espace de travail ----------------------------------------------------

    @mcp.tool()
    def productlane_workspace(
        op: Literal["me", "roadmap", "portal", "instances",
                    "snippets", "snippet", "create_snippet", "update_snippet",
                    "delete_snippet", "folders", "import_file"] = "me",
        snippet_id: Optional[str] = None,
        folder_id: Optional[str] = None,
        contact_email: Optional[str] = None,
        language: Optional[str] = None,
        title_contains: Optional[str] = None,
        url: Optional[str] = None,
        file_name: Optional[str] = None,
        fields: Optional[dict] = None,
        cursor: Optional[str] = None,
        limit: int = 50,
    ) -> Any:
        """Productlane — l'identité de la clé, le portail public, les extraits.

        `op='me'` est le point de départ utile : il rend les scopes accordés à
        la clé et la sélection d'équipe Linear de l'espace de travail. C'est de
        quoi expliquer un refus AVANT de le provoquer, et il ne demande aucun
        scope.

        `op`:
        - `me` — identité de la clé, scopes, équipes Linear.
        - `roadmap` — la roadmap PUBLIQUE telle que le portail la rend ;
          `contact_email` la rend du point de vue d'un contact.
        - `portal` — ce qu'un contact voit dans son portail de support
          (`contact_email` requis, plan Scale).
        - `instances` — les instances de portail. ⚠️ Le portail **Main (Root) est
          implicite** : il n'y figure pas, et se désigne ailleurs par un
          `portal_instance_id` nul. Une liste vide ne veut pas dire « pas de portail ».
        - `snippets` / `snippet` / `create_snippet` / `update_snippet` /
          `delete_snippet` / `folders` — les modèles de réponse (plan Pro).
          ⚠️ Leur corps est du **HTML**, pas du markdown.
        - `import_file` — stocke un fichier depuis une URL publique et rend une
          URL CDN réutilisable dans un changelog ou un article.

        Args:
            op: l'opération, cf. ci-dessus.
            snippet_id: l'extrait visé.
            folder_id: op='snippets' — filtre par dossier ; création — le dossier cible.
            contact_email: op='roadmap'/'portal' — le point de vue d'un contact.
            language: op='roadmap' — la langue servie.
            title_contains: op='snippets' — filtre par titre partiel.
            url: op='import_file' — l'URL publique du fichier à stocker.
            file_name: op='import_file' — nom donné au fichier stocké.
            fields: op='create_snippet'/'update_snippet' — title et html.
            cursor: page suivante.
            limit: lignes par page (1-200, défaut 50).
        """
        c = _client()
        f = fields or {}
        if op == "me":
            return _run(lambda: c.me())
        if op == "roadmap":
            return _run(lambda: c.get_roadmap(contact_email=contact_email,
                                              language=language))
        if op == "portal":
            _need(contact_email, "contact_email", op)
            return _run(lambda: c.get_customer_portal(contact_email))
        if op == "instances":
            return _run(lambda: c.list_portal_instances())
        if op == "snippets":
            return _run(lambda: c.list_snippets(limit=limit, cursor=cursor,
                                                title_contains=title_contains,
                                                folder_id=folder_id))
        if op == "snippet":
            _need(snippet_id, "snippet_id", op)
            return _run(lambda: c.get_snippet(snippet_id))
        if op == "create_snippet":
            _need(fields, "fields", op)
            return _run(lambda: c.create_snippet(f.get("title"), f.get("html"),
                                                 folder_id=folder_id))
        if op == "update_snippet":
            _need(snippet_id, "snippet_id", op)
            _need(fields, "fields", op)
            return _run(lambda: c.update_snippet(snippet_id, fields))
        if op == "delete_snippet":
            _need(snippet_id, "snippet_id", op)
            return _run(lambda: c.delete_snippet(snippet_id))
        if op == "folders":
            return _run(lambda: c.list_snippet_folders(limit=limit, cursor=cursor))
        if op == "import_file":
            _need(url, "url", op)
            return _run(lambda: c.import_file(url=url, file_name=file_name))
        raise _bad_op(op, "me | roadmap | portal | instances | snippets | "
                          "snippet | create_snippet | update_snippet | "
                          "delete_snippet | folders | import_file")
