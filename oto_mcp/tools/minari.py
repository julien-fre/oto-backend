"""Minari — prospection téléphonique : appels transcrits, listes, analytics.

Wrappe `oto.tools.minari.client.MinariClient` (API publique v1, Bearer). La clé
se crée dans **Settings → API & webhook** et porte les droits de TOUTE
l'entreprise — pas d'une personne. C'est pourquoi le connecteur est **byo-only,
sans clé plateforme** : le journal d'appels d'un client est le sien, et une clé
Minari partagée entre orgs n'aurait aucun sens (même principe que `stripe`).

**Écrit sur contrat, PAS vérifié en live** (2026-08-31) : tout vient de l'OpenAPI
3.1 publié et du guide LLM de l'éditeur, aucune sonde contre un vrai compte. Les
gardes ci-dessous sont donc des lectures du contrat, à confirmer au premier
compte branché — d'où la sonde `_verify`, qui est le premier vrai test.

**Six tools, un par objet métier** (ADR 0047), verbe en `op=`. Aucun paramètre
n'est retenu au silence : un `op` qui n'utilise pas un argument fourni REFUSE
(patron `_refuse_ignored`, silae/granola/stripe).

**Trois budgets, parce que trois réponses peuvent exploser** — et c'est la seule
raison pour laquelle ce module ne se contente pas de relayer :

1. ⚠️ **La fiche d'appel embarque le transcript intégral.** `GET /calls/{id}`
   rend `CallDetail` = tout `CallSummary` PLUS chaque réplique d'un appel qui
   peut durer 45 minutes. `op="get"` le RETIRE donc et le remplace par
   `transcript_utterances` (le compte) ; le texte s'obtient par `op="transcript"`,
   qui est justement l'endpoint que Minari a séparé pour cette raison. Rien n'est
   perdu en silence : la clé retirée est nommée dans la réponse.
2. ⚠️ **Une liste rend ses 1500 contacts d'un bloc**, sans pagination. `op="get"`
   sur `minari_list` s'arrête donc à `max_contacts` (100 par défaut) et DIT le
   total ainsi que la troncature, au lieu de rendre un mur.
3. `op="transcript"` plafonne à `max_utterances` (200 par défaut) et le dit.

⚠️ **Le piège n°1 du connecteur : `minari_list` ne voit que les listes CSV.**
Les endpoints listes/contacts de Minari ne couvrent QUE la source import CSV ;
un compte dont les contacts arrivent de HubSpot ou Salesforce a des listes bien
réelles qu'ils ne rendent jamais. Un `op="list"` vide se lit donc « pas de liste
CSV », JAMAIS « pas de liste » — et la vue toutes sources est
`minari_analytics(op="lists")`. Le message de la réponse vide le dit, parce que
c'est exactement le cas où un agent conclurait à tort que le compte est vide.

Les appels et les analytics, eux, couvrent toutes les sources.

**Ce module n'invente rien** : Minari n'expose ni déclenchement d'appel, ni
modification de contact, ni gestion d'utilisateurs. Ce qui manque ici manque à
l'API — et le client oto-core ne porte pas ces méthodes, donc les ajouter
demanderait une PR oto-core, pas une ligne ici.
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional
from urllib.parse import parse_qs, urlparse

from fastmcp import FastMCP
from mcp.types import ErrorData, INVALID_PARAMS

from .. import access
from ..mcp_errors import McpError
from ..connectors import verify as connector_verify

# Bornes de rendu — Minari fixe ses pages côté serveur (50, et 10 pour
# `analytics/lists`), mais rien ne borne une fiche de liste ni un transcript.
_DEFAULT_MAX_CONTACTS = 100
_DEFAULT_MAX_UTTERANCES = 200
# Plafonds DURS : un `max_contacts=1500` demandé de bonne foi rendrait jusqu'à
# 1500 contacts portant chacun une note de 5 000 caractères — plusieurs méga-
# octets dans le contexte. Le plafond est annoncé dans la réponse quand il mord,
# jamais appliqué en silence.
_CEILING_MAX_CONTACTS = 500
_CEILING_MAX_UTTERANCES = 2000


def _bad(msg: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=msg))


def _refuse_ignored(op: str, hint: str, **provided) -> None:
    """Un argument fourni que CET op n'utilise pas est une erreur d'intention.
    Sinon `minari_call(op="list", call_id=…)` rendrait TOUS les appels en
    laissant croire qu'on en a ciblé un."""
    for name, value in provided.items():
        if value is not None:
            raise _bad(f"op={op!r} n'utilise pas `{name}` — {hint}")


def _upstream_message(e) -> str:
    """Traduit le refus de Minari en quelque chose d'actionnable.

    Le contrat d'erreur est `{"error": {"code", "message", "details"}}` ; le code
    amont est plus parlant que le statut, on le garde.
    """
    status = getattr(e, "status_code", None)
    raw = getattr(e, "body", None)
    # `raw` n'est pas toujours du JSON : un 502 de proxy rend du HTML, et le
    # réduire à `{}` effacerait la seule information disponible.
    body = raw if isinstance(raw, dict) else {}
    err = body.get("error") if isinstance(body.get("error"), dict) else {}
    code = err.get("code")
    detail = err.get("message") or body.get("detail") or ""
    reste = detail or (raw if raw not in (None, "", {}) else "")
    if status == 401:
        return ("Minari a rejeté la clé (401"
                + (f" {code}" if code else "") + ") — elle est absente, invalide "
                "ou révoquée. Elle se recrée dans Minari → Settings → API & webhook, "
                "puis se repose sur la fiche du connecteur.")
    if status == 404:
        return (f"Minari ne trouve pas la cible (404{' ' + code if code else ''}) — "
                f"vérifie l'identifiant. {reste}".strip())
    if status == 409:
        return (f"Minari refuse : l'identifiant existe déjà (409"
                f"{' ' + code if code else ''}). {reste}".strip())
    if status == 429:
        return (f"Limite d'appels Minari atteinte (429) — 60 requêtes/minute pour "
                f"TOUTE l'entreprise, donc partagées avec les autres automatisations "
                f"sous la même clé. {reste}".strip())
    if status == 400:
        return (f"Minari refuse la requête (400{' ' + code if code else ''}) : "
                f"{reste}").strip()
    return f"Minari HTTP {status}{' ' + code if code else ''}: {reste}".strip()


def _next_cursor(envelope: Any) -> Optional[str]:
    """Le curseur de la page suivante, extrait de `next_url`.

    Minari rend une URL absolue ; la repasser telle quelle obligerait l'agent à
    la parser (ou à nous la renvoyer et nous à la valider comme une URL amont).
    Le curseur est la seule partie qui l'intéresse.
    """
    if not isinstance(envelope, dict):
        return None
    url = envelope.get("next_url")
    if not url:
        return None
    try:
        values = parse_qs(urlparse(url).query).get("cursor") or []
    except Exception:  # noqa: SILENT — un `next_url` amont illisible coûte la pagination, pas la réponse : on rend la page sans curseur plutôt que de faire échouer un appel qui a réussi
        return None
    return values[0] if values else None


def _with_note(payload: Any, texte: str) -> Any:
    """Attache une remarque hors-bande sous `note` (la clé de la maison), en
    CUMULANT si une autre y est déjà — deux remarques valent mieux qu'une écrasée."""
    if not isinstance(payload, dict):
        return payload
    ancienne = payload.get("note")
    return {**payload, "note": f"{ancienne} · {texte}" if ancienne else texte}


def _paged(envelope: Any) -> Any:
    """Ajoute `next_cursor` à une enveloppe paginée, sans rien retirer.

    ⚠️ Le curseur de Minari est une POSITION, pas une requête : l'exemple du
    contrat se décode en `{"s": "<started_at>", "c": <call_id>}` — il ne porte
    aucun filtre. Les filtres, eux, vivent dans la query string de `next_url`.
    Un agent qui rejouerait `cursor` SEUL recevrait donc la page suivante du
    journal ENTIER, non filtrée, et la fondrait dans une réponse qu'il croit
    filtrée — faux sans la moindre erreur. D'où la remarque systématique : le
    seul moment où elle est lue est celui où l'on s'apprête à tourner la page.
    """
    cursor = _next_cursor(envelope)
    if cursor and isinstance(envelope, dict):
        return _with_note(
            {**envelope, "next_cursor": cursor},
            "page suivante : repasse `cursor` AVEC les mêmes filtres — le "
            "curseur est une position, il ne les porte pas")
    return envelope


def _verify(fields: dict, config: dict | None = None) -> None:  # noqa: ARG001
    """Sonde « tester la connexion ».

    `GET /users` plutôt qu'un journal d'appels : c'est la lecture la plus légère
    (une poignée d'objets, aucun filtre), elle est servie par la même clé
    d'entreprise que tout le reste, et une clé qui la passe peut lire le reste —
    Minari n'a pas de scopes par endpoint.

    ⚠️ **Un 200 suffit, même si la liste est vide.** La sonde tourne
    AVANT la persistance (#106) : tout ce qu'elle refuse n'est jamais
    enregistré. Une version antérieure levait sur un annuaire vide, en croyant y
    lire « clé prise dans le mauvais espace » — raisonnement faux (la clé d'un
    autre espace rend les membres de CET espace, pas une liste vide) dont le
    coût, lui, était réel : elle empêchait d'ENREGISTRER une clé qui marche.
    Une sonde répond « cette clé authentifie-t-elle ? », rien de plus.
    """
    from oto.tools.minari.client import MinariClient
    MinariClient(api_key=fields["key"]).list_users()


def register(mcp: FastMCP) -> None:
    from oto.tools.common.errors import UpstreamHTTPError
    from oto.tools.minari.client import MinariClient

    connector_verify.register("minari", _verify)

    def _client() -> MinariClient:
        key, _ = access.resolve_api_key("minari")
        return MinariClient(api_key=key)

    def _run(fn):
        try:
            return fn()
        except ValueError as e:
            raise _bad(str(e))
        except UpstreamHTTPError as e:
            raise _bad(_upstream_message(e))

    # ================================================================
    # Appels — le cœur du produit
    # ================================================================

    @mcp.tool()
    def minari_call(
        op: Literal["list", "get", "transcript", "recording"] = "list",
        call_id: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        user_id: Optional[List[int]] = None,
        status: Optional[List[Literal["connected", "missed", "voicemail",
                                      "left-voicemail", "canceled", "busy",
                                      "failed", "no-answer",
                                      "meeting-booked"]]] = None,
        direction: Optional[Literal["incoming", "outgoing"]] = None,
        min_duration: Optional[int] = None,
        search: Optional[str] = None,
        transcript_search: Optional[str] = None,
        language: Optional[str] = None,
        contact_id: Optional[int] = None,
        list_id: Optional[List[str]] = None,
        cursor: Optional[str] = None,
        max_utterances: Optional[int] = None,
    ) -> object:
        """Calls made and received in Minari, with AI summaries and objections.

        Covers ALL sources (CSV imports and CRM-synced contacts alike), unlike
        `minari_list`.

        "get" deliberately OMITS the transcript and reports
        `transcript_utterances` instead — a 45-minute call would otherwise bury
        the summary and objections you asked for. Use "transcript" for the text.

        Search two different ways: `search` matches the contact (name, company,
        phone number); `transcript_search` matches what was SAID. Transcript
        search is a literal substring match, words AND-ed, never semantic and
        never translated — so a French call will not match English terms. Each
        row carries `language`; re-issue in that language (or scope with
        `language="fr"`) to search across locales. Both need 3+ characters.

        "recording" returns availability and size, NOT the audio — an MP3 has no
        useful form in a tool result. For something a human can open, a call
        carries `public_call_link`, a shareable page needing no key — but it is
        null when the company has external call sharing switched off, so check
        it rather than promising someone a link.

        Args:
            op: "list" browses; "get" is one call without its transcript;
                "transcript" is the text; "recording" reports whether audio
                exists (it never returns audio bytes — see above).
            call_id: REQUIRED by "get", "transcript" and "recording".
            start_date: "list" only — ISO 8601, calls started at or after.
            end_date: "list" only — ISO 8601, calls started at or before.
            user_id: "list" only — filter by team member id(s), from
                `minari_user`.
            status: "list" only — one or more call statuses. "meeting-booked"
                is a FILTER-only value: it is how you ask for calls that led to
                a meeting, but a returned row never carries it as its `status`
                (rows use the other eight, plus a `meeting_booked` boolean).
            direction: "list" only — omit to get both directions.
            min_duration: "list" only — minimum duration in seconds.
            search: "list" only — contact name, company or phone (3+ chars).
            transcript_search: "list" only — what was said (3+ chars).
            language: "list" only — ISO 639-1 of the AI content, e.g. "fr".
            contact_id: "list" only — filter to one contact.
            list_id: "list" only — filter by list id(s).
            cursor: "list" only — `next_cursor` from a previous response.
                RE-SEND YOUR FILTERS WITH IT: the cursor is a position
                (`started_at` + call id), not a saved query, so a cursor on its
                own pages the whole journal and silently drops your filters.
                Pages are fixed at 50 and cannot be enlarged.
            max_utterances: "transcript" only — cap on returned lines
                (default 200). The response states the true total and whether
                it was truncated.
        """
        client = _client()

        if op == "list":
            _refuse_ignored(op, 'ces arguments visent UN appel — utilise op="get"',
                            call_id=call_id, max_utterances=max_utterances)
            return _run(lambda: _paged(client.list_calls(
                start_date=start_date, end_date=end_date, user_id=user_id,
                status=status, direction=direction, min_duration=min_duration,
                search=search, transcript_search=transcript_search,
                language=language, contact_id=contact_id, list_id=list_id,
                cursor=cursor)))

        _refuse_ignored(op, 'ces filtres ne valent que pour op="list"',
                        start_date=start_date, end_date=end_date, user_id=user_id,
                        status=status, direction=direction, min_duration=min_duration,
                        search=search, transcript_search=transcript_search,
                        language=language, contact_id=contact_id, list_id=list_id,
                        cursor=cursor)
        if not call_id:
            raise _bad(f'op={op!r} requiert `call_id`')

        if op == "get":
            _refuse_ignored(op, 'le plafond ne vaut que pour op="transcript"',
                            max_utterances=max_utterances)

            def _get():
                out = client.get_call(call_id)
                data = out.get("data") if isinstance(out, dict) else None
                if not isinstance(data, dict):
                    return out
                lines = data.get("transcript")
                trimmed = {k: v for k, v in data.items() if k != "transcript"}
                if isinstance(lines, list) and lines:
                    trimmed["transcript_utterances"] = len(lines)
                    note = ('transcript retiré de op="get" pour tenir le budget de '
                            'réponse — appelle op="transcript" avec le même call_id')
                else:
                    # `null` ≠ zéro réplique, et un appel muet n'a rien à aller
                    # chercher : annoncer « transcript retiré » enverrait l'agent
                    # dépenser un second appel — sur un budget de 60/minute
                    # partagé par toute l'entreprise — pour recevoir du vide.
                    trimmed["transcript_utterances"] = 0
                    note = ("aucun transcript — appel non connecté ou "
                            'transcription en cours ; op="transcript" ne rendra '
                            "rien de plus")
                return {**out, "data": trimmed, "note": note}

            return _run(_get)

        if op == "transcript":
            demande = (_DEFAULT_MAX_UTTERANCES if max_utterances is None
                       else int(max_utterances))
            if demande < 1:
                raise _bad("`max_utterances` doit être au moins 1")
            cap = min(demande, _CEILING_MAX_UTTERANCES)

            def _transcript():
                out = client.get_call_transcript(call_id)
                data = out.get("data") if isinstance(out, dict) else None
                if not isinstance(data, dict):
                    return out
                lines = data.get("transcript")
                if not isinstance(lines, list):
                    # `null` = appel non abouti ou transcription en cours. Ce
                    # n'est pas une erreur, et le dire évite une relance inutile.
                    return {**out, "note": "aucun transcript — appel non connecté "
                                           "ou transcription en cours"}
                total = len(lines)
                bloc = {**data, "transcript": lines[:cap],
                        "transcript_utterances": total, "truncated": total > cap}
                res = {**out, "data": bloc}
                if total > cap:
                    res["note"] = (f"{cap} répliques sur {total} — relance avec un "
                                   "`max_utterances` plus haut si le reste compte")
                return res

            return _run(_transcript)

        _refuse_ignored(op, 'le plafond ne vaut que pour op="transcript"',
                        max_utterances=max_utterances)
        return _run(lambda: client.call_recording_status(call_id))

    # ================================================================
    # Équipe — le résolveur d'identifiants de tout le reste
    # ================================================================

    @mcp.tool()
    def minari_user() -> object:
        """Active team members of the Minari company.

        Their `id` is what every `user_id` filter expects, and what
        `minari_list(op="create")` needs as `assigned_to`. Only members who
        accepted their invitation appear.
        """
        return _run(lambda: _client().list_users())

    # ================================================================
    # Listes de contacts — source CSV UNIQUEMENT
    # ================================================================

    @mcp.tool()
    def minari_list(
        op: Literal["list", "get", "create", "delete"] = "list",
        list_id: Optional[str] = None,
        name: Optional[str] = None,
        assigned_to: Optional[int] = None,
        contacts: Optional[List[Dict[str, Any]]] = None,
        update_existing_contacts: Optional[bool] = None,
        cursor: Optional[str] = None,
        max_contacts: Optional[int] = None,
    ) -> object:
        """Contact lists — the call lists reps work through.

        SCOPE WARNING: these endpoints see the CSV-import source ONLY. A company
        whose contacts come from HubSpot or Salesforce has real lists that never
        appear here, so an empty result means "no CSV list", not "no list". The
        all-sources view is `minari_analytics(op="lists")`.

        "create" is how you push a prospecting list into the dialer: build the
        contacts elsewhere, assign the list to a rep, and it appears in their
        Minari. A contact already known to the account is ADDED to the list
        rather than duplicated; its stored fields stay untouched unless
        `update_existing_contacts=True` (a blank value never overwrites).

        A list holds at most 1500 contacts, and at most 1500 can be sent per
        request.

        "delete" is PERMANENT and takes the list's contacts out of the rep's
        queue. It cannot be undone from this tool or from Minari.

        Args:
            op: "list" browses lists; "get" is one list with its contacts;
                "create" makes a list and imports contacts; "delete" destroys
                a list permanently.
            list_id: REQUIRED by "get" and "delete".
            name: REQUIRED by "create" — the list name (255 chars max).
            assigned_to: REQUIRED by "create" — the team member id that will own
                the list, from `minari_user`.
            contacts: REQUIRED by "create" — up to 1500 objects. Each needs at
                least one of `firstName`, `lastName`, `email`. Also accepts
                `company`, `title`, `companyDomain`, `linkedinUrl`,
                `description`, `phoneNumber1`…`phoneNumber5`, `note` (5000
                chars, attached as a note), and `customFields` keyed by ids
                registered through `minari_custom_field`.
            update_existing_contacts: "create" only — overwrite stored fields of
                contacts that already exist. Defaults to false.
            cursor: "list" only — `next_cursor` from a previous response; it
                is a position, not a saved query.
            max_contacts: "get" only — cap on contacts returned (default 100).
                A list returns all 1500 at once otherwise; the response states
                the true total and whether it was truncated.
        """
        client = _client()

        if op == "list":
            _refuse_ignored(op, 'ces arguments visent UNE liste ou sa création',
                            list_id=list_id, name=name, assigned_to=assigned_to,
                            contacts=contacts,
                            update_existing_contacts=update_existing_contacts,
                            max_contacts=max_contacts)

            def _browse():
                out = _paged(client.list_lists(cursor=cursor))
                rows = out.get("data") if isinstance(out, dict) else None
                # Sur la PREMIÈRE page seulement (le curseur dit qu'on continue),
                # et quel que soit le nombre de lignes : la note n'explique pas un
                # vide, elle énonce une PORTÉE. Le cas partiel — quelques listes
                # CSV à côté de beaucoup de listes CRM — sous-déclare tout autant,
                # et lui ne se signale par rien.
                if isinstance(rows, list) and not cursor:
                    return _with_note(
                        out,
                        "ces endpoints ne voient que les listes issues d'un "
                        "import CSV ; les listes synchronisées depuis un CRM n'y "
                        "apparaissent pas, même partiellement. Vue toutes "
                        'sources : minari_analytics(op="lists").')
                return out

            return _run(_browse)

        _refuse_ignored(op, 'la pagination ne vaut que pour op="list"', cursor=cursor)

        if op == "get":
            _refuse_ignored(op, 'ces arguments ne valent que pour op="create"',
                            name=name, assigned_to=assigned_to, contacts=contacts,
                            update_existing_contacts=update_existing_contacts)
            if not list_id:
                raise _bad('op="get" requiert `list_id`')
            demande = _DEFAULT_MAX_CONTACTS if max_contacts is None else int(max_contacts)
            if demande < 1:
                raise _bad("`max_contacts` doit être au moins 1")
            cap = min(demande, _CEILING_MAX_CONTACTS)

            def _one():
                out = client.get_list(list_id)
                data = out.get("data") if isinstance(out, dict) else None
                if not isinstance(data, dict):
                    return out
                rows = data.get("contacts")
                if not isinstance(rows, list):
                    return out
                total = len(rows)
                bloc = {**data, "contacts": rows[:cap], "total_contacts": total,
                        "truncated": total > cap}
                res = {**out, "data": bloc}
                if demande > cap:
                    res["note"] = (
                        f"`max_contacts={demande}` ramené à {cap} — une note de "
                        "contact pèse jusqu'à 5 000 caractères")
                elif total > cap:
                    res["note"] = f"{cap} contacts sur {total}"
                return res

            return _run(_one)

        if op == "create":
            _refuse_ignored(op, 'op="create" crée la liste, il ne cible pas une liste existante',
                            list_id=list_id, max_contacts=max_contacts)
            if not name:
                raise _bad('op="create" requiert `name`')
            if assigned_to is None:
                raise _bad('op="create" requiert `assigned_to` — un id de membre, '
                           "rendu par minari_user")
            if not contacts:
                raise _bad('op="create" requiert `contacts` (au moins un)')
            return _run(lambda: client.create_list(
                name=name, assigned_to=assigned_to, contacts=contacts,
                update_existing_contacts=bool(update_existing_contacts)))

        _refuse_ignored(op, 'op="delete" ne prend que `list_id`',
                        name=name, assigned_to=assigned_to, contacts=contacts,
                        update_existing_contacts=update_existing_contacts,
                        max_contacts=max_contacts)
        if not list_id:
            raise _bad('op="delete" requiert `list_id`')
        return _run(lambda: client.delete_list(list_id))

    # ================================================================
    # Contacts d'une liste
    # ================================================================

    @mcp.tool()
    def minari_contact(
        op: Literal["add", "remove"],
        list_id: str,
        contacts: Optional[List[Dict[str, Any]]] = None,
        contact_ids: Optional[List[int]] = None,
        update_existing_contacts: Optional[bool] = None,
    ) -> object:
        """Add contacts to, or remove them from, an existing Minari list.

        Same CSV-only scope as `minari_list`.

        "add" tops up a list a rep is already working. Up to 1500 per request,
        and a list caps at 1500 total: contacts beyond the cap are SILENTLY
        skipped and counted in `skippedCount` — the request still succeeds, so
        read that number rather than assuming everything landed.

        "remove" takes contacts out of the list by contact id (the `contactId`
        of `minari_list(op="get")`). Minari refuses to empty a list this way —
        that is `minari_list(op="delete")`.

        Args:
            op: "add" or "remove".
            list_id: the list to modify.
            contacts: REQUIRED by "add" — same shape as
                `minari_list(op="create")`.
            contact_ids: REQUIRED by "remove" — ids to take out of the list.
            update_existing_contacts: "add" only — overwrite stored fields of
                contacts that already exist. Defaults to false.
        """
        client = _client()
        if op == "add":
            _refuse_ignored(op, 'op="add" prend des contacts, pas des ids',
                            contact_ids=contact_ids)
            if not contacts:
                raise _bad('op="add" requiert `contacts` (au moins un)')
            return _run(lambda: client.add_contacts(
                list_id, contacts,
                update_existing_contacts=bool(update_existing_contacts)))

        _refuse_ignored(op, 'op="remove" prend des ids, pas des contacts',
                        contacts=contacts,
                        update_existing_contacts=update_existing_contacts)
        if not contact_ids:
            raise _bad('op="remove" requiert `contact_ids`')
        return _run(lambda: client.remove_contacts(list_id, contact_ids))

    # ================================================================
    # Champs personnalisés
    # ================================================================

    @mcp.tool()
    def minari_custom_field(
        op: Literal["list", "create", "delete"] = "list",
        field_id: Optional[str] = None,
        label: Optional[str] = None,
    ) -> object:
        """Custom contact fields — the metadata keys an import may carry.

        A key must be registered here BEFORE it can be used in the
        `customFields` of an imported contact. The contract states the
        requirement but not how a breach fails (rejected request? key dropped?
        contact skipped?) — so register first rather than relying on the error.

        "delete" removes the field from future imports and from the UI.

        Args:
            op: "list" shows registered fields; "create" registers one;
                "delete" removes one.
            field_id: REQUIRED by "create" and "delete" — the stable key used
                inside `customFields`, e.g. "industry". Must be unique.
            label: REQUIRED by "create" — the display name, e.g. "Industry".
        """
        client = _client()
        if op == "list":
            _refuse_ignored(op, 'op="list" ne prend aucun argument',
                            field_id=field_id, label=label)
            return _run(client.list_custom_fields)
        if op == "create":
            if not field_id:
                raise _bad('op="create" requiert `field_id`')
            if not label:
                raise _bad('op="create" requiert `label`')
            return _run(lambda: client.create_custom_field(field_id=field_id, label=label))
        _refuse_ignored(op, 'op="delete" ne prend que `field_id`', label=label)
        if not field_id:
            raise _bad('op="delete" requiert `field_id`')
        return _run(lambda: client.delete_custom_field(field_id))

    # ================================================================
    # Analytics — répondre sans télécharger les appels
    # ================================================================

    @mcp.tool()
    def minari_analytics(
        op: Literal["overview", "users", "objections", "lists"] = "overview",
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        user_id: Optional[List[int]] = None,
        list_id: Optional[List[str]] = None,
        conversation_threshold: Optional[Literal[0, 30, 60, 90, 120]] = None,
        period: Optional[Literal["day", "week", "month", "all"]] = None,
        call_limit: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> object:
        """Aggregated calling metrics — answer "what's our connect rate" with
        one call instead of downloading and summing calls yourself.

        DEFAULT WINDOWS DIFFER, and getting this wrong silently returns the
        wrong period: "overview" and "users" default to TODAY; "objections"
        defaults to the LAST 7 DAYS. Always pass `start_date` and `end_date`
        together when you mean a specific range. Analytics are day-granular,
        and the resolved window comes back in `period` next to the data.

        Rates are percentages (0-100) and are null when their denominator is 0.
        The per-user rows sum to the overview totals for the same filters.

        "lists" is the all-sources view of list progress (CSV, HubSpot, …) —
        use it, not `minari_list`, to answer "which lists are stalled".

        Args:
            op: "overview" is the company total; "users" breaks the same
                metrics down per rep (rank by connect rate); "objections" is
                what prospects push back on and how well reps handle it;
                "lists" is per-list completion and health.
            start_date: "overview", "users" and "objections" only — ISO 8601,
                sent together with `end_date`. "lists" has NO date window; its
                window is `period`.
            end_date: "overview", "users" and "objections" only — ISO 8601, sent
                together with `start_date`.
            user_id: restrict to team member id(s), from `minari_user`.
            list_id: restrict to calls made within these list id(s).
            conversation_threshold: "overview" and "users" only — seconds a
                connected call must last to count as a conversation. One of
                0, 30, 60, 90, 120 (default 30).
            period: REQUIRED by "lists" — the dial window counted:
                "day", "week", "month" or "all".
            call_limit: REQUIRED by "lists" — 1 to 10, the attempts after which
                an unconnected contact counts as completed. With `period` it
                DEFINES what "completed" means, so two different values are two
                different questions, not a contradiction.
            cursor: "lists" only — `next_cursor` from a previous response.
                Re-send `period`, `call_limit` and any filters with it — the
                cursor is a position, not a saved query. Pages are 10, not 50.
        """
        client = _client()
        if (start_date is None) != (end_date is None):
            raise _bad("`start_date` et `end_date` vont ensemble — Minari refuse "
                       "l'une sans l'autre.")

        if op == "lists":
            _refuse_ignored(op, 'le seuil de conversation ne vaut que pour '
                                'op="overview"/"users"',
                            conversation_threshold=conversation_threshold)
            # `analytics/lists` n'a PAS de fenêtre en dates : la sienne est
            # `period`. Les accepter en silence rendrait un « depuis janvier »
            # calculé sur la semaine, sans que rien ne le signale.
            _refuse_ignored(op, 'op="lists" n\'a pas de fenêtre en dates — la '
                                "sienne est `period` (day/week/month/all)",
                            start_date=start_date, end_date=end_date)
            if not period:
                raise _bad('op="lists" requiert `period` — il définit la fenêtre '
                           "de comptage des appels (day/week/month/all)")
            if call_limit is None:
                raise _bad('op="lists" requiert `call_limit` (1-10) — il définit '
                           "après combien de tentatives un contact jamais joint "
                           "compte comme épuisé")
            return _run(lambda: _paged(client.analytics_lists(
                period=period, call_limit=call_limit, user_id=user_id,
                list_id=list_id, cursor=cursor)))

        _refuse_ignored(op, 'ces arguments ne valent que pour op="lists"',
                        period=period, call_limit=call_limit, cursor=cursor)

        if op == "objections":
            _refuse_ignored(op, 'le seuil de conversation ne vaut que pour '
                                'op="overview"/"users"',
                            conversation_threshold=conversation_threshold)
            return _run(lambda: client.analytics_objections(
                start_date=start_date, end_date=end_date, user_id=user_id,
                list_id=list_id))

        fn = client.analytics_overview if op == "overview" else client.analytics_users
        return _run(lambda: fn(
            start_date=start_date, end_date=end_date, user_id=user_id,
            list_id=list_id, conversation_threshold=conversation_threshold))
