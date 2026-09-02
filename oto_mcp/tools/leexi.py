"""Outils Leexi — appels et réunions enregistrés, transcripts, notes.

Wrappe `oto.tools.leexi.client.LeexiClient` (API v1, Basic `KEY_ID:KEY_SECRET`).
Cinq outils, un par famille de l'API amont : appels, notes, réunions,
utilisateurs, équipes.

Deux portées gouvernent ce que l'organisation voit, et il faut les distinguer
pour lire un résultat vide sans se tromper de diagnostic :

- la **portée d'accès aux appels** est attachée à la clé côté Leexi (toute
  l'entreprise / l'accès d'un utilisateur / des règles d'accès). Hors périmètre,
  un appel n'est pas listé, et demandé en direct il répond 404. Une liste vide
  peut donc être un réglage parfaitement valide ;
- les **scopes de permission** (`read_calls`, `write_users`…) décident des
  endpoints atteignables. Sans le scope, c'est un 403, et le message le dit.

⚠️ **Une clé neuve ne porte que `read_calls`** : les écritures d'utilisateur et
d'équipe — qui engagent les LICENCES FACTURÉES du client — demandent des scopes
qu'un admin Leexi doit accorder explicitement. Ce connecteur ne contourne pas ce
cran, il le NOMME quand l'amont refuse. C'est aussi pourquoi la sonde de
connexion interroge `/calls` et pas `/users` : sonder ailleurs ferait passer une
clé saine mais restreinte pour une clé morte.

Les appels au client sont écrits en clair (`_client().list_calls(…)`) : c'est ce
qui les rend vérifiables par la sonde version-skew
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
    """Traduit un refus de Leexi en message actionnable.

    Les codes de cette API sont inhabituellement parlants (402 = abonnement,
    409 = doublon, 422 = état incompatible) : les rendre tels quels priverait
    l'agent de la seule information qui distingue « réessaie » de « change
    quelque chose ».
    """
    status = e.status_code
    if status == 401:
        return ("Leexi a rejeté la clé (401) — vérifie l'API Key ID et le Key "
                "Secret configurés sur ce connecteur (Leexi : Settings → "
                "Company Settings → API Keys).")
    if status == 402:
        return ("Leexi : abonnement inactif (402) — la clé est bonne, mais le "
                "compte Leexi n'est pas en règle. Rien à corriger côté oto.")
    if status == 403:
        return ("Leexi a refusé l'accès (403) — la clé existe mais il lui "
                "manque le scope de cette opération. Une clé neuve ne porte "
                "que `read_calls` : les autres scopes, et surtout "
                "`write_users`/`write_teams` (qui engagent les licences "
                "facturées), s'accordent par un admin Leexi.")
    if status == 404:
        return ("Leexi : introuvable (404). ⚠️ Sur un appel, cela peut aussi "
                "vouloir dire « hors de la portée de cette clé » — la portée "
                "d'accès aux appels se règle côté Leexi, pas ici.")
    if status == 405:
        return ("Leexi : action impossible pour cet événement (405) — réunion "
                "passée, ou sans URL exploitable.")
    if status == 409:
        return ("Leexi : conflit (409) — déjà existant. Un email d'utilisateur "
                "ou un nom d'équipe déjà pris, une réunion déjà déclarée, ou "
                "un assistant déjà lancé.")
    if status == 422:
        return (f"Leexi : la demande est valide mais la ressource ne peut pas "
                f"changer ainsi (422) — par exemple supprimer une équipe qui "
                f"porte encore des utilisateurs ou des appels : {e.body}")
    if status == 429:
        return ("Leexi : trop de requêtes (429) — 50/minute, et seulement "
                "10/minute pour la création d'appel. Réessaie dans un instant.")
    if status in (500, 502, 503, 504):
        return f"Leexi est momentanément indisponible (HTTP {status}) — réessaie plus tard."
    return f"Leexi a refusé la requête (HTTP {status}): {e.body}"


def _verify(fields: dict, config: dict | None = None) -> None:  # noqa: ARG001
    """Sonde « tester la connexion » : une lecture réelle sur `/calls`.

    ⚠️ Sonder `/users` serait le réflexe naturel et il serait FAUX : une clé
    neuve ne porte que `read_calls`, donc une clé parfaitement valide y
    répondrait 403, et le bouton afficherait rouge sur une configuration saine.
    `/calls` est le seul endpoint qu'une clé par défaut peut honorer.

    Une liste vide n'est PAS un échec : c'est une clé dont la portée d'accès ne
    couvre aucun appel, ce qui est un réglage valide côté Leexi.
    """
    from oto.tools.leexi.client import LeexiClient
    client = LeexiClient(key_id=fields["key_id"], key_secret=fields["key_secret"])
    client.probe()


def register(mcp: FastMCP) -> None:
    from oto.tools.common.errors import UpstreamHTTPError
    from oto.tools.leexi.client import LeexiClient

    connector_verify.register("leexi", _verify)

    def _client() -> LeexiClient:
        creds = access.resolve_credential_fields("leexi")
        return LeexiClient(key_id=creds["key_id"],
                           key_secret=creds["key_secret"])

    def _run(fn):
        """Traduit un refus de Leexi en erreur d'outil actionnable."""
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

    # --- appels --------------------------------------------------------------

    @mcp.tool()
    def leexi_calls(
        op: Literal["search", "get", "create", "presign"] = "search",
        call_uuid: Optional[str] = None,
        owner_uuid: Optional[list[str]] = None,
        participating_user_uuid: Optional[list[str]] = None,
        customer_email_address: Optional[list[str]] = None,
        customer_phone_number: Optional[list[str]] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        date_filter: Optional[str] = None,
        order: Optional[str] = None,
        with_transcript: bool = False,
        fields: Optional[dict] = None,
        extension: Optional[str] = None,
        page: Optional[int] = None,
        items: int = 10,
    ) -> Any:
        """Leexi — les appels et réunions enregistrés, et leurs transcripts.

        C'est ici qu'on retrouve ce qui s'est DIT : `op='get'` rend l'appel avec
        ses topics et son transcript (au paragraphe et au mot), là où
        `op='search'` ne rend que les métadonnées.

        `op`:
        - `search` — liste les appels de la portée de la clé. Filtres par
          propriétaire, participant, email ou téléphone du client, et fenêtre de
          dates. ⚠️ Une liste vide peut simplement vouloir dire que la clé n'a
          accès à aucun appel : la portée se règle côté Leexi.
        - `get` — un appel AVEC son transcript (`call_uuid`).
        - `create` — enregistre un appel importé (`fields`). Requiert que le
          fichier soit déjà téléversé via `op='presign'` ; la création est
          asynchrone et le résumé n'arrive que plusieurs minutes après.
        - `presign` — demande l'URL de téléversement d'un enregistrement
          (`extension`), premier temps d'un import.

        ⚠️ Un 404 sur `op='get'` ne veut pas dire « n'existe pas » : un appel
        hors de la portée de la clé répond 404, exprès.

        Args:
            op: search | get | create | presign.
            call_uuid: op='get' — l'appel à lire.
            owner_uuid: op='search' — filtre par propriétaire(s).
            participating_user_uuid: op='search' — filtre par participant(s).
            customer_email_address: op='search' — filtre par email(s) client.
            customer_phone_number: op='search' — filtre par téléphone(s) client.
            date_from: op='search' — début de la fenêtre (ISO 8601).
            date_to: op='search' — fin de la fenêtre (ISO 8601).
            date_filter: op='search' — champ borné : created_at | performed_at | updated_at.
            order: op='search' — tri, ex. 'performed_at desc'.
            with_transcript: op='search' — joint le transcript paragraphe (réponse lourde).
            fields: op='create' — corps de l'appel (direction, external_id,
                performed_at, recording_s3_key, user_uuid requis).
            extension: op='presign' — extension du fichier, ex. 'mp3'.
            page: numéro de page.
            items: lignes par page (1-100, défaut 10).
        """
        if op == "search":
            return _run(lambda: _client().list_calls(
                page=page, items=items, order=order, date_filter=date_filter,
                date_from=date_from, date_to=date_to,
                owner_uuid=owner_uuid,
                participating_user_uuid=participating_user_uuid,
                customer_email_address=customer_email_address,
                customer_phone_number=customer_phone_number,
                with_simple_transcript=with_transcript or None))
        if op == "get":
            _need(call_uuid, "call_uuid", op)
            return _run(lambda: _client().get_call(call_uuid))
        if op == "create":
            _need(fields, "fields", op)
            return _run(lambda: _client().create_call(fields))
        if op == "presign":
            _need(extension, "extension", op)
            return _run(lambda: _client().presign_recording_url(extension))
        raise _bad(f"`op` invalide : {op!r} (attendu : search | get | create | presign).")

    # --- notes ---------------------------------------------------------------

    @mcp.tool()
    def leexi_notes(
        op: Literal["list", "get", "update", "delete"] = "list",
        call_uuid: Optional[str] = None,
        note_uuid: Optional[str] = None,
        prompt_uuid: Optional[str] = None,
        locale: Optional[str] = None,
        text: Optional[str] = None,
        page: Optional[int] = None,
        items: int = 10,
    ) -> Any:
        """Leexi — les notes produites sur un appel (résumés, comptes rendus).

        Ce sont les sorties des prompts Leexi : c'est là que vit le compte rendu
        d'un rendez-vous, plutôt que dans le transcript brut.

        `op`:
        - `list` — notes d'un appel (`call_uuid` requis : l'API n'expose pas de
          liste globale). ⚠️ Seules les notes de catégorie `summary` ou `text`
          existent pour cette API — l'absence des autres n'est pas un défaut.
        - `get` — une note (`note_uuid`).
        - `update` — REMPLACE le texte d'une langue (`locale` + `text`) ; ce
          n'est pas une fusion, le contenu précédent de cette langue est perdu.
        - `delete` — supprime une note, sans corbeille.

        Args:
            op: list | get | update | delete.
            call_uuid: op='list' — l'appel dont on lit les notes (requis).
            note_uuid: op='get'/'update'/'delete' — la note visée.
            prompt_uuid: op='list' — ne garder que les notes de ce prompt.
            locale: op='update' — langue de la note réécrite.
            text: op='update' — le nouveau texte (remplace).
            page: numéro de page.
            items: lignes par page (1-100, défaut 10).
        """
        if op == "list":
            _need(call_uuid, "call_uuid", op)
            return _run(lambda: _client().list_call_notes(
                call_uuid, page=page, items=items, prompt_uuid=prompt_uuid))
        if op == "get":
            _need(note_uuid, "note_uuid", op)
            return _run(lambda: _client().get_call_note(note_uuid))
        if op == "update":
            _need(note_uuid, "note_uuid", op)
            _need(locale, "locale", op)
            _need(text, "text", op)
            return _run(lambda: _client().update_call_note(note_uuid, locale, text))
        if op == "delete":
            _need(note_uuid, "note_uuid", op)
            return _run(lambda: _client().delete_call_note(note_uuid))
        raise _bad(f"`op` invalide : {op!r} (attendu : list | get | update | delete).")

    # --- réunions ------------------------------------------------------------

    @mcp.tool()
    def leexi_meetings(
        op: Literal["list", "get", "create", "delete", "launch_bot"] = "list",
        meeting_uuid: Optional[str] = None,
        origin: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        date_filter: Optional[str] = None,
        order: Optional[str] = None,
        fields: Optional[dict] = None,
        stop_task: Optional[bool] = None,
        page: Optional[int] = None,
        items: int = 10,
    ) -> Any:
        """Leexi — les réunions connues, et l'assistant qu'on y envoie.

        Une réunion (« meeting event ») est un rendez-vous que Leexi connaît,
        venu du calendrier, d'une saisie manuelle ou de l'API — distinct d'un
        appel, qui est un enregistrement déjà traité. L'assistant se lance sur la
        première et produit le second.

        `op`:
        - `list` — réunions connues, filtrables par origine et fenêtre de dates.
        - `get` — une réunion (`meeting_uuid`).
        - `create` — déclare une réunion (`fields`). `to_record=True` demande
          l'enregistrement.
        - `delete` — retire une réunion.
        - `launch_bot` — ⚠️ **envoie l'assistant DANS la réunion**, où les
          participants le verront rejoindre. `stop_task=True` fait l'inverse et
          retire un assistant déjà en cours : c'est le même endpoint amont pour
          les deux sens.

        Args:
            op: list | get | create | delete | launch_bot.
            meeting_uuid: la réunion visée (get, delete, launch_bot).
            origin: op='list' — calendar | manual | api.
            date_from: op='list' — début de la fenêtre (ISO 8601).
            date_to: op='list' — fin de la fenêtre (ISO 8601).
            date_filter: op='list' — champ borné : start_time | end_time.
            order: op='list' — tri, ex. 'start_time desc'.
            fields: op='create' — corps (end_time, internal, meeting_url,
                organizer, owned, start_time, to_record, user_uuid requis).
            stop_task: op='launch_bot' — True retire l'assistant au lieu de l'envoyer.
            page: numéro de page.
            items: lignes par page (1-100, défaut 10).
        """
        if op == "list":
            return _run(lambda: _client().list_meeting_events(
                page=page, items=items, order=order, origin=origin,
                date_filter=date_filter, date_from=date_from, date_to=date_to))
        if op == "get":
            _need(meeting_uuid, "meeting_uuid", op)
            return _run(lambda: _client().get_meeting_event(meeting_uuid))
        if op == "create":
            _need(fields, "fields", op)
            return _run(lambda: _client().create_meeting_event(fields))
        if op == "delete":
            _need(meeting_uuid, "meeting_uuid", op)
            return _run(lambda: _client().delete_meeting_event(meeting_uuid))
        if op == "launch_bot":
            _need(meeting_uuid, "meeting_uuid", op)
            return _run(lambda: _client().launch_meeting_assistant(
                meeting_uuid, stop_task=stop_task))
        raise _bad(f"`op` invalide : {op!r} "
                   "(attendu : list | get | create | delete | launch_bot).")

    # --- utilisateurs --------------------------------------------------------

    @mcp.tool()
    def leexi_users(
        op: Literal["list", "get", "create", "update", "deactivate"] = "list",
        user_uuid: Optional[str] = None,
        fields: Optional[dict] = None,
        page: Optional[int] = None,
        items: int = 10,
    ) -> Any:
        """Leexi — les utilisateurs de l'espace de travail, et leurs licences.

        Sert surtout à résoudre un `user_uuid` (celui qu'exige la création d'un
        appel) et à voir qui consomme une licence.

        ⚠️ **Les écritures d'ici engagent la facturation du client** : créer un
        utilisateur consomme une licence, le réactiver aussi. Elles exigent le
        scope `write_users`, qu'une clé neuve n'a PAS — un admin Leexi doit
        l'accorder, et c'est le garde-fou réel. Un refus 403 dit exactement cela.

        ⚠️ `deactivate` ne supprime rien : les appels et l'historique restent,
        les sessions tombent, la licence se libère. Le verbe HTTP amont dit
        « delete », l'effet est une désactivation. Réactiver = `update` avec
        `{"active": true}`.

        `op`: `list` | `get` | `create` (fields) | `update` (fields) | `deactivate`.

        Args:
            op: list | get | create | update | deactivate.
            user_uuid: l'utilisateur visé (get, update, deactivate).
            fields: op='create' — email, name, team_uuid requis ; roles,
                license, send_welcome_email optionnels. op='update' — champs à
                changer, dont active.
            page: numéro de page.
            items: lignes par page (1-100, défaut 10).
        """
        if op == "list":
            return _run(lambda: _client().list_users(page=page, items=items))
        if op == "get":
            _need(user_uuid, "user_uuid", op)
            return _run(lambda: _client().get_user(user_uuid))
        if op == "create":
            _need(fields, "fields", op)
            return _run(lambda: _client().create_user(fields))
        if op == "update":
            _need(user_uuid, "user_uuid", op)
            _need(fields, "fields", op)
            return _run(lambda: _client().update_user(user_uuid, fields))
        if op == "deactivate":
            _need(user_uuid, "user_uuid", op)
            return _run(lambda: _client().deactivate_user(user_uuid))
        raise _bad(f"`op` invalide : {op!r} "
                   "(attendu : list | get | create | update | deactivate).")

    # --- équipes -------------------------------------------------------------

    @mcp.tool()
    def leexi_teams(
        op: Literal["list", "get", "create", "update", "delete"] = "list",
        team_uuid: Optional[str] = None,
        fields: Optional[dict] = None,
        page: Optional[int] = None,
        items: int = 10,
    ) -> Any:
        """Leexi — les équipes de l'espace de travail.

        Une équipe porte les utilisateurs et leurs appels ; son `uuid` est requis
        pour créer un utilisateur.

        ⚠️ Écritures sous scope `write_teams`, qu'une clé neuve n'a pas.
        ⚠️ `delete` ne passe QUE sur une équipe sans utilisateur ni appel (sinon
        422) : pour toutes les autres, la désactiver avec
        `op='update' fields={"active": false}`, ce que l'éditeur recommande.

        `op`: `list` | `get` | `create` (fields) | `update` (fields) | `delete`.

        Args:
            op: list | get | create | update | delete.
            team_uuid: l'équipe visée (get, update, delete).
            fields: op='create' — name requis, active optionnel.
                op='update' — name et/ou active.
            page: numéro de page.
            items: lignes par page (1-100, défaut 10).
        """
        if op == "list":
            return _run(lambda: _client().list_teams(page=page, items=items))
        if op == "get":
            _need(team_uuid, "team_uuid", op)
            return _run(lambda: _client().get_team(team_uuid))
        if op == "create":
            _need(fields, "fields", op)
            return _run(lambda: _client().create_team(fields))
        if op == "update":
            _need(team_uuid, "team_uuid", op)
            _need(fields, "fields", op)
            return _run(lambda: _client().update_team(team_uuid, fields))
        if op == "delete":
            _need(team_uuid, "team_uuid", op)
            return _run(lambda: _client().delete_team(team_uuid))
        raise _bad(f"`op` invalide : {op!r} "
                   "(attendu : list | get | create | update | delete).")
