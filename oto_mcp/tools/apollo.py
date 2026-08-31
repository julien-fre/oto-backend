"""Apollo.io — B2B prospection (organizations, people, job postings, contacts,
sequences, one-off emails, conversations).

Wrappe `oto.tools.apollo.ApolloClient`. Deux régimes de clé selon ce qu'un
endpoint interroge, PAS selon lecture/écriture :

- **Base partagée Apollo** (`mixed_companies/search`, `mixed_people/api_search`,
  `people/match`, `organizations/*`) : `access.resolve_api_key("apollo")` — user
  key (`/account`) prioritaire, sinon clé plateforme (free-tier, quota daily =
  `default_quota` par user/jour). N'importe quelle clé rend la MÊME base (~28M
  entreprises) → une clé plateforme mutualisée y est sans risque. Le quota
  plateforme métré = les **crédits Apollo** (`people/match`, qui révèle un
  contact) ; recherche org/people et job postings ne consomment pas de crédit →
  non métrés.
- **Espace de travail DU PROPRIÉTAIRE de la clé** (contacts, séquences, emails,
  boîtes connectées, conversations — TOUT ce qui a été ajouté dans ce module) :
  `access.resolve_credential("apollo", want="byo")`, JAMAIS `resolve_api_key`.
  Ce n'est pas une distinction lecture/écriture — `apollo_email(op="search")` en
  lecture rend `body_html`/`body_text` des emails ENVOYÉS PAR le propriétaire de
  la clé ; `apollo_email_accounts` rend SES boîtes (signature HTML, score de
  délivrabilité). Une clé plateforme mutualisée y exposerait les données privées
  de son propriétaire à n'importe quel autre user d'oto. Et pour l'écriture
  spécifiquement (enrôler des contacts, envoyer un email) : un envoi sur cette
  clé partirait en plus depuis SA boîte, vers SES contacts — même verrou que
  Lightfield `send_email` (oto-core 97c53ce, autorisé par le mainteneur le
  19/08/2026 à deux conditions : le connecteur n'existe que si une org pose SA
  clé, et l'envoi part d'une boîte que le propriétaire de cette clé a lui-même
  connectée — condition #2 portée ici par le verrou local
  `send_email_from_email_account_id` côté client oto-core). Les conversations
  (transcripts d'appels/visios réels) sont byo-only pour la même raison
  d'espace privé, plus un coût crédit conditionnel (1 si insights IA, 0 sinon)
  pas métrable a priori côté quota plateforme. Les **contacts** (`apollo_contact`)
  sont le cas le plus net de cette règle : un contact est le carnet d'adresses de
  l'équipe qui pose la clé — d'où byo-only sur les TROIS ops, LECTURES COMPRISES,
  alors qu'aucune ne coûte de crédit. Ne pas les confondre avec les `people/*`,
  qui interrogent la base partagée : une personne trouvée là n'est un contact ici
  que si l'équipe l'a enregistrée.

⚠️ Doc Apollo (pas vérifié depuis cet environnement, pas de clé disponible ici) :
`add_contact_ids` et `/emailer_messages/{id}/activities` (stats email) exigent
une clé « Master » et 403 sinon — à confirmer côté Julien avec une clé réelle.
La même exigence est documentée sur les TROIS endpoints contacts
(`typed_custom_fields`, `contacts/{id}` en lecture et en PATCH) : là, plutôt que
d'attendre, `apollo_contact` traduit le 403 en message qui NOMME le prérequis, et
son écriture reste possible sans le catalogue (validation dégradée, annoncée).
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from fastmcp import FastMCP
from ..mcp_errors import McpError
from mcp.types import ErrorData, INVALID_PARAMS

from .. import access, output_projection


def _bad(msg: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=msg))


def register(mcp: FastMCP) -> None:
    from oto.tools.apollo.client import ApolloClient, ApolloError

    def _client() -> tuple[ApolloClient, bool]:
        key, is_platform = access.resolve_api_key("apollo")
        return ApolloClient(api_key=key), is_platform

    def _client_byo() -> ApolloClient:
        """Client résolu SANS palier plateforme — pour tout appel qui écrit
        (enrôlement, envoi) ou lit des données sensibles (conversations).

        Apollo est le premier connecteur à mélanger les deux régimes dans le
        MÊME module (recherche/enrichissement = platform_key_open, tout le
        reste = byo-only) : le message générique de `resolve_credential`
        (« Aucun credential configuré pour toi ») serait trompeur pour un
        user qui voit déjà apollo_search_organizations fonctionner via la clé
        plateforme et ne comprendrait pas pourquoi CET appel-ci le refuse."""
        try:
            return ApolloClient(api_key=access.resolve_credential("apollo", want="byo").key)
        except McpError as e:
            msg = e.error.message or ""
            if "Aucun credential" in msg:
                # Seul CE message générique (absence totale de credential BYO) est
                # ambigu ici — les autres (multi-compte, compte introuvable) sont
                # déjà précis et n'ont rien à voir avec platform vs byo.
                raise McpError(ErrorData(
                    code=INVALID_PARAMS,
                    message=(
                        f"{msg} — ceci ne concerne QUE les séquences/emails/"
                        "conversations (tes propres données) : la recherche et "
                        "l'enrichissement Apollo restent utilisables sans ta propre clé.")))
            raise

    @mcp.tool()
    def apollo_search_organizations(
        name: Optional[str] = None,
        domain: Optional[str] = None,
        country: Optional[str] = None,
        employee_ranges: Optional[list[str]] = None,
        revenue_min: Optional[int] = None,
        revenue_max: Optional[int] = None,
        locations: Optional[list[str]] = None,
        keywords: Optional[list[str]] = None,
        technologies: Optional[list[str]] = None,
        org_ids: Optional[list[str]] = None,
        per_page: int = 10,
        page: int = 1,
    ) -> dict:
        """Find companies by firmographics — the CHEAP way to qualify a list.

        Costs 1 Apollo credit per PAGE (up to 100 results), where enrichment costs
        1 credit per COMPANY: filter here, enrich only what you keep.

        ⚠️ Results carry revenue and headcount GROWTH, but NOT the headcount itself
        — that's why you filter by `employee_ranges` instead of reading a number.
        For the exact headcount and its per-department split, enrich (see
        apollo_enrich_organization / apollo_bulk_enrich_organizations).

        Args:
            name: company name.
            domain: company domain.
            country: HQ country (shorthand for `locations`).
            employee_ranges: headcount brackets "min,max", e.g. ["11,50", "51,200"].
            revenue_min / revenue_max: annual revenue bounds.
            locations: HQ cities/regions/countries.
            keywords: activity keywords.
            technologies: technology uids in use, e.g. ["salesforce"].
            org_ids: Apollo organization ids.
            per_page: results per page (≤100). page: page number.
        """
        client, _ = _client()
        return client.search_organizations(
            name=name, domain=domain, country=country, per_page=per_page, page=page,
            employee_ranges=employee_ranges, revenue_min=revenue_min,
            revenue_max=revenue_max, locations=locations, keywords=keywords,
            technologies=technologies, org_ids=org_ids)

    @mcp.tool()
    def apollo_enrich_organization(domain: str) -> dict:
        """Enrich a company from its domain (firmographics, size, industry…).

        Returns the exact `estimated_num_employees`, its per-department split
        (`departmental_head_count`), 6/12/24-month headcount growth, revenue,
        founding year and tech stack. Costs 1 Apollo credit. For several companies
        at once, prefer apollo_bulk_enrich_organizations (same cost, 10× fewer calls).
        """
        client, _ = _client()
        return client.enrich_organization(domain)

    @mcp.tool()
    def apollo_bulk_enrich_organizations(domains: list[str]) -> dict:
        """Enrich UP TO 10 companies in a single call — same fields as
        apollo_enrich_organization (headcount, per-department split, growth, revenue).

        Costs 1 Apollo credit per company (a batch saves CALLS, not credits: the
        enrich rate limit is 600/h, so batching divides your call budget by 10).
        Over 10 domains, split into batches yourself — the API refuses more.
        """
        client, _ = _client()
        return client.bulk_enrich_organizations(domains)

    @mcp.tool()
    def apollo_search_people(
        domains: Optional[list[str]] = None,
        org_ids: Optional[list[str]] = None,
        titles: Optional[list[str]] = None,
        seniorities: Optional[list[str]] = None,
        person_locations: Optional[list[str]] = None,
        organization_locations: Optional[list[str]] = None,
        per_page: int = 25,
        page: int = 1,
    ) -> dict:
        """Search people by company domains/ids, titles, seniorities, location (net-new).

        Returns identities WITHOUT email/phone — reveal a contact with
        apollo_match_person (which costs an Apollo credit).

        ⚠️ LAST NAMES COME BACK OBFUSCATED here ("Vi***l"). To reveal someone you
        found, pass the `id` of the result as `person_id` to apollo_match_person —
        NEVER first name + company, which matches nobody: Apollo then mints an empty
        record and charges the credit anyway.

        ⚠️ A DOMAIN IS WORLDWIDE. On a subsidiary of an international group, the
        domain is shared across every country: franke.com returns 1887 profiles,
        verifone.com 3282, sonova.com 3147 — targeting the French entity by domain
        alone means revealing at random, one credit each, mostly on the wrong
        country. Add `person_locations=["France"]`. Same for the reverse case: a
        French head office with expatriates is `organization_locations`.

        Args:
            domains: company domains, e.g. ["acme.com"].
            org_ids: Apollo organization ids (from apollo_enrich_organization).
            titles: job-title keywords, e.g. ["directeur financier", "CFO"].
            seniorities: e.g. ["c_suite", "founder", "owner", "director", "manager"].
            person_locations: where the PERSON is — country, region or city as
                Apollo spells it, e.g. ["France"], ["Paris, France"]. THE filter
                for a national subsidiary of a global domain.
            organization_locations: where their EMPLOYER's site is (≠ the person's
                own location: a French-based employee of a German site matches
                person_locations=["France"], not organization_locations).
        """
        client, _ = _client()
        return client.search_people(
            domains=domains, org_ids=org_ids,
            titles=titles, seniorities=seniorities,
            person_locations=person_locations,
            organization_locations=organization_locations,
            per_page=per_page, page=page)

    @mcp.tool()
    def apollo_match_person(
        person_id: Optional[str] = None,
        linkedin_url: Optional[str] = None,
        email: Optional[str] = None,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
        name: Optional[str] = None,
        domain: Optional[str] = None,
        org_name: Optional[str] = None,
    ) -> dict:
        """Match a single person (enrichment). Returns {} if no match.

        Pass the strongest identifier you have. Coming from apollo_search_people, that
        is `person_id` = the `id` of the search result — search obfuscates last names,
        so the id is the ONLY reliable handle on someone you just found. Otherwise:
        email or linkedin_url, or a FULL name (first + last) with the company.

        ⚠️ Costs 1 Apollo credit per call, charged even when nothing matches: a weak
        identifier (first name + company) makes Apollo mint an EMPTY record rather than
        return nothing. Such an answer carries `person._stub: true` — treat it as a
        failure, not as data. Calls with no usable identifier are refused before the
        credit is spent.
        """
        client, is_platform = _client()
        try:
            result = client.match_person(
                person_id=person_id, linkedin_url=linkedin_url, email=email,
                first_name=first_name, last_name=last_name, name=name,
                domain=domain, org_name=org_name) or {}
        except ValueError as e:
            raise McpError(ErrorData(code=INVALID_PARAMS, message=str(e)))
        if is_platform:
            access.record_platform_usage("apollo")
        return result

    @mcp.tool()
    def apollo_job_postings(org_id: str) -> dict:
        """List active job postings for an Apollo organization id (hiring signal)."""
        client, _ = _client()
        return client.get_job_postings(org_id)

    # ------------------------------------------------------------------
    # Contacts — la frontière de données bascule ICI. Tout ce qui précède
    # interroge la base PARTAGÉE Apollo (`mixed_*`, `people/match`,
    # `organizations/*`) ; un CONTACT est une personne enregistrée dans
    # l'espace de travail DU PROPRIÉTAIRE de la clé, avec les valeurs que SON
    # équipe y a écrites (stage, propriétaire, listes, champs personnalisés).
    # Même règle que les séquences et les emails, donc : `_client_byo()` sur
    # les TROIS ops, y compris les deux lectures. Une clé plateforme
    # mutualisée rendrait ici le carnet d'adresses de quelqu'un d'autre.
    #
    # Les trois endpoints coûtent 0 crédit — c'est précisément ce qui rend
    # `op="get"` utile : relire un contact qu'on possède déjà n'a aucune
    # raison de repayer le crédit d'`apollo_match_person`.
    # ------------------------------------------------------------------

    # Écarté de la vue par défaut du catalogue de champs : plomberie de sync CRM
    # et d'affichage. DENYLIST nommée, jamais une allowlist — une clé qu'Apollo
    # ajouterait demain doit rester visible, pas disparaître en silence
    # (leçon `fr_get`/`liste_idcc`, docs/conventions.md).
    _FIELD_NOISE = (
        "finder_view_ids", "finder_views", "icon_class", "project_workspace_id",
        "mapped_crm_field", "additional_mapped_crm_field",
        "is_readonly_mapped_crm_field", "picklist_options_last_synced_at",
        "picklist_value_set_id", "context", "group", "meta", "parent",
    )

    # Vue de LISTE d'`op="search"` : deux blocs IMBRIQUÉS qu'Apollo recopie dans
    # CHAQUE fiche et qui, à 25 lignes, pèsent plus que tout le reste réuni. Ce
    # qui sert à choisir (`organization_name`, `account_id`, `title`, `email`,
    # `typed_custom_fields`) reste — et `full=True` rend le brut. DENYLIST nommée :
    # une clé qu'Apollo ajouterait demain reste visible (leçon `fr_get`).
    _CONTACT_NOISE = ("organization", "account")

    # Ce que veut dire un 422 DÉPEND de l'op, et se tromper de leçon est pire que
    # ne rien dire : sur une lecture il ne peut désigner que l'id ; sur un PATCH il
    # désigne aussi bien une VALEUR refusée (un stage inexistant, une date mal
    # formée). Servir « ce n'est pas un id de contact » à qui vient d'écrire une
    # mauvaise valeur l'envoie chercher au mauvais endroit.
    _WRONG_ID_422 = (
        "Apollo ne trouve pas ce contact dans ton espace de travail (inexistant, "
        "supprimé, ou appartenant à une autre équipe). ⚠️ Un id rendu par "
        "apollo_search_people/apollo_match_person est un id de PERSONNE de la base "
        "partagée, PAS un id de contact : une personne que ton équipe n'a jamais "
        "enregistrée n'a pas de contact ici.")
    _REFUSED_WRITE_422 = (
        "Apollo a refusé cette modification. Deux causes possibles, et le message "
        "ci-dessus tranche : soit une VALEUR est invalide (contact_stage_id "
        "inconnu, date mal formée, option de liste de choix inexistante), soit "
        "`contact_id` ne désigne pas un contact de ton espace de travail — un id "
        "d'apollo_search_people est un id de PERSONNE, pas de contact.")

    def _contact_run(fn, *, on_422: str = _WRONG_ID_422):
        """Traduit les deux refus PRÉVISIBLES de cette famille en erreur actionnable.

        Le module n'a pas de table d'erreurs globale (ApolloError remonte tel quel,
        message amont inclus) et c'est très bien pour la recherche. Ici deux statuts
        ont une cause précise, que le message amont ne dit pas :

        - **403** = clé Apollo non-Master. C'est le cas NORMAL d'une clé scopée, pas
          une panne — et « Apollo 403 sur contacts/… » n'apprend rien à qui ne sait
          pas que ces endpoints ont ce prérequis.
        - **422** = cf. `on_422`, qui dépend de l'op (lecture vs écriture).

        Tout le reste remonte INTACT : le message amont d'Apollo nomme le champ
        refusé, et c'est ce qui rend un 400 corrigeable.
        """
        try:
            return fn()
        except ValueError as e:
            raise _bad(str(e))
        except ApolloError as e:
            if e.status_code == 403:
                raise _bad(
                    f"{e} — ces endpoints (champs personnalisés, lecture et écriture "
                    "d'un contact) exigent une clé Apollo **Master**, ou le scope "
                    "nommé correspondant. Une clé Apollo standard authentifie mais "
                    "rend 403 ici. Régénère-la en Master dans Apollo → Settings → "
                    "Integrations → API.")
            if e.status_code == 422:
                raise _bad(f"{e} — {on_422}")
            raise

    def _custom_fields() -> dict:
        """Catalogue des champs personnalisés de CETTE équipe Apollo (0 crédit).

        ⚠️ Passe par `typed_custom_fields`, qu'Apollo marque déprécié au profit
        de `GET /fields` — **sciemment**. Les deux ne rendent pas la même forme
        d'id : celui-ci rend l'ObjectId NU, la clé exacte que `PATCH /contacts`
        attend ; `/fields` rend un id PRÉFIXÉ de sa modalité
        (`"account.6940…"`), qu'aucune doc n'autorise à découper. Le catalogue
        « moderne » ferait donc écrire des clés qu'Apollo ignore en rendant 200.
        """
        return _client_byo().list_typed_custom_fields() or {}

    def _field_index(catalog: Any) -> Optional[dict]:
        """`{id: définition}` des champs du catalogue — **`None` si la forme
        surprend**, `{}` si l'équipe n'en déclare aucun.

        Les deux ne se valent pas et les confondre coûte cher dans les deux sens.
        Forme illisible = on ne SAIT rien : refuser bloquerait une écriture
        légitime au premier changement d'Apollo. Catalogue lu et vide = on sait
        que l'id envoyé n'existe pas : laisser passer, c'est laisser Apollo
        avaler l'écriture en rendant 200."""
        rows = catalog.get("typed_custom_fields") if isinstance(catalog, dict) else None
        if not isinstance(rows, list):
            return None
        return {r["id"]: r for r in rows
                if isinstance(r, dict) and isinstance(r.get("id"), str)}

    def _is_contact_field(definition: dict) -> bool:
        """Un champ sans `modality` déclarée est traité comme un champ de contact
        — même défaut permissif partout, sinon la liste des ids « valides » et le
        contrôle qui refuse ne parlent pas du même ensemble."""
        return (definition.get("modality") or "contact") == "contact"

    def _check_custom_field_ids(values: dict) -> Optional[str]:
        """Refuse un id de champ que cette équipe ne déclare pas — en nommant les
        ids valides, sinon l'agent réessaie au hasard.

        Rend une NOTE quand la validation n'a pas pu avoir lieu (catalogue
        illisible), jamais None en silence : `GET typed_custom_fields` exige une
        clé Master et rend 403 sinon, donc « pas validé » est le cas NORMAL
        d'une clé scopée — et une écriture qui se dit vérifiée sans l'être est
        pire que pas de vérification du tout.
        """
        try:
            index = _field_index(_custom_fields())
        except McpError:
            raise
        # noqa: SILENT — l'avertissement « ids non vérifiés » est rendu à l'agent
        except Exception as e:  # noqa: BLE001 — le catalogue est un CONFORT, pas un verrou
            return (f"ids non vérifiés : le catalogue des champs n'a pas pu être lu "
                    f"({type(e).__name__}: {e}). `GET typed_custom_fields` demande "
                    "une clé Apollo Master ; l'écriture, elle, est partie telle "
                    "quelle — relis le contact avec op=\"get\" pour voir ce qui a "
                    "réellement été enregistré.")
        if index is None:
            return ("ids non vérifiés : le catalogue des champs n'a pas la forme "
                    "attendue (Apollo a pu la changer). L'écriture est partie telle "
                    'quelle — relis le contact avec op="get" pour la vérifier.')

        def _describe(i: str) -> str:
            d = index[i]
            return f'{i} ("{d.get("name") or d.get("label")}")'

        unknown = sorted(set(values) - set(index))
        if unknown:
            valid = [{"id": i, "name": d.get("name") or d.get("label"),
                      "type": d.get("type")}
                     for i, d in index.items() if _is_contact_field(d)]
            raise _bad(
                f"champs personnalisés inconnus de cette équipe Apollo : {unknown}. "
                f"Champs de CONTACT valides : {valid or 'aucun'}. "
                "`typed_custom_fields` est keyé par ID (pas par nom) — lis-les avec "
                'apollo_contact(op="fields").')

        misfiled = sorted(i for i in values if not _is_contact_field(index[i]))
        if misfiled:
            detail = [f'{_describe(i)} → {index[i].get("modality")}' for i in misfiled]
            raise _bad(
                f"ces champs n'appartiennent pas à l'objet contact : {detail}. "
                "Un champ personnalisé est attaché à UN objet Apollo ; posé sur un "
                "contact il n'est pas « presque bon », il est ignoré sans erreur.")

        # Une liste de choix n'accepte que l'ID d'une de ses options. Envoyer le
        # LIBELLÉ est le piège que la description de l'outil nomme comme « la seule
        # erreur qu'Apollo avale en silence » — le catalogue qu'on vient de lire
        # porte déjà de quoi la refuser, ne pas s'en servir serait la documenter
        # sans la fermer. On ne contrôle QUE ce que le catalogue déclare : une
        # picklist dont les options sont absentes n'est pas contrôlée, pas refusée.
        wrong: list[str] = []
        for i, value in values.items():
            d = index[i]
            if d.get("type") not in ("picklist", "multi_select"):
                continue
            opts = d.get("picklist_values")
            if not isinstance(opts, list) or not opts:
                continue
            ids = {o.get("id") for o in opts if isinstance(o, dict)}
            if not ids:
                continue
            given = value if isinstance(value, list) else [value]
            off = [v for v in given if v is not None and v not in ids]
            if off:
                names = [{"id": o.get("id"), "name": o.get("name")}
                         for o in opts if isinstance(o, dict)]
                wrong.append(f'{_describe(i)} : {off} — options valides {names}')
        if wrong:
            raise _bad(
                "valeurs de liste de choix invalides : " + " ; ".join(wrong) + ". "
                "Une picklist Apollo s'écrit avec l'`id` de l'option, jamais avec "
                "son libellé — le libellé est accepté en apparence puis ignoré.")
        return None

    @mcp.tool()
    def apollo_contact(
        op: Literal["fields", "create_field", "search", "get", "update"],
        contact_id: Optional[str] = None,
        label: Optional[str] = None,
        field_type: str = "string",
        max_length: Optional[int] = None,
        q_keywords: Optional[str] = None,
        contact_stage_ids: Optional[list[str]] = None,
        contact_label_ids: Optional[list[str]] = None,
        sort_by_field: Optional[str] = None,
        sort_ascending: Optional[bool] = None,
        per_page: int = 25,
        page: int = 1,
        typed_custom_fields: Optional[dict] = None,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
        title: Optional[str] = None,
        email: Optional[str] = None,
        organization_name: Optional[str] = None,
        account_id: Optional[str] = None,
        website_url: Optional[str] = None,
        label_names: Optional[list[str]] = None,
        contact_stage_id: Optional[str] = None,
        present_raw_address: Optional[str] = None,
        direct_phone: Optional[str] = None,
        corporate_phone: Optional[str] = None,
        mobile_phone: Optional[str] = None,
        home_phone: Optional[str] = None,
        other_phone: Optional[str] = None,
        modality: Literal["contact", "account", "opportunity", "all"] = "contact",
        dry_run: bool = False,
        full: bool = False,
    ) -> dict:
        """Read and edit a CONTACT — a person saved in YOUR Apollo workspace, with
        the values your team wrote on them. BYO key only, all three ops, and all
        three cost 0 Apollo credits.

        ⚠️ A CONTACT IS NOT A PERSON, AND THE TWO IDS ARE DIFFERENT OBJECTS.
        `apollo_search_people` returns PERSON ids, which every op here REJECTS. Two
        things hand you a real contact id: `apollo_match_person`, nested at
        `person.contact.id` — present once that person is a contact of your
        workspace, and free because you already paid for the match — and op="search"
        below. Never pass a person id: it fails, it does not fall back.

        ⚠️ op="get" IS THE CHEAP WAY TO READ SOMEONE BACK. `apollo_match_person`
        costs a credit and returns the SHARED record — not your team's stage, owner,
        lists or custom values. To re-read a contact you already own, use this.

        ⚠️ CUSTOM FIELDS ARE KEYED BY ID, NEVER BY NAME. `typed_custom_fields` looks
        like `{"60c39ed82bd02f01154c470a": "2026-08-07"}` — call op="fields" FIRST to
        get the ids. Ids are validated against your team's catalogue before the write,
        and an unknown one is refused with the valid ids named.

        ⚠️ FOR A PICKLIST FIELD, THE VALUE IS THE OPTION'S ID, NOT ITS LABEL. op="fields"
        returns `picklist_values` for those — send `picklist_values[].id`. Sending the
        human label is the one mistake Apollo swallows silently.

        ⚠️ A NEW FIELD MEANT TO HOLD A SENTENCE MUST BE `field_type="textarea"`.
        `string` is length-capped (120 characters by default) and Apollo truncates
        past it without complaining — a personalised opener would arrive cut mid-word.

        ⚠️ THE THREE ENDPOINTS NEED AN APOLLO **MASTER** API KEY (or the matching
        scope) and answer 403 otherwise. When op="fields" cannot be read, op="update"
        still writes — but it says so in `field_validation`, it never pretends the ids
        were checked.

        ⚠️ `label_names` REPLACES list membership instead of adding to it — sending
        one list removes the contact from every other. Every other field is a true
        PATCH: what you omit is left untouched.

        Args by op:
        - `fields`: the custom field definitions of your team — `id` (the bare id the
          write expects), `name`, `type`, `modality` and, for picklists,
          `picklist_values`. `modality` filters which object's fields you get
          (default "contact"; "account", "opportunity", or "all" for every object).
          `full=True` returns the raw catalogue instead of the projected one.
        - `create_field`: declare a NEW custom field — the API equivalent of Apollo's
          Settings → Custom Fields, for when you have no access to that UI. `label`
          (required), `field_type` (`string`, `textarea`, `number`, `date`,
          `datetime`, `boolean`), `max_length`, `modality`. Returns the field with its
          id, ready to use as a `typed_custom_fields` key. `dry_run=True` echoes
          without creating. A SETUP gesture — run it once per field, not per lead.
        - `search`: find the contact ids you need. `q_keywords` (name, title,
          employer or email), `contact_stage_ids`, `contact_label_ids`,
          `sort_by_field` (`contact_last_activity_date`,
          `contact_email_last_opened_at`, `contact_email_last_clicked_at`,
          `contact_created_at`, `contact_updated_at`) + `sort_ascending`,
          `per_page` (Apollo caps at 100), `page` (Apollo stops at 500 pages —
          past 50 000 records, filter instead of paging). Searches YOUR saved
          contacts only, never Apollo's database.
        - `get`: `contact_id` (required). Returns the contact and its labels.
        - `update`: `contact_id` (required) + at least one field among
          `typed_custom_fields`, `first_name`, `last_name`, `title`, `email`,
          `organization_name`, `account_id`, `website_url`, `label_names`,
          `contact_stage_id`, `present_raw_address`, `direct_phone`,
          `corporate_phone`, `mobile_phone`, `home_phone`, `other_phone`.
          `dry_run=True` validates the custom field ids and echoes the exact payload
          without writing.
        """
        if op not in ("fields", "create_field", "search", "get", "update"):
            raise _bad(f'op inconnu "{op}" — attendu: fields, create_field, search, '
                       'get, update')

        if op == "fields":
            catalog = _contact_run(_custom_fields)
            if full:
                return catalog
            index = _field_index(catalog)
            if index is None:
                raise _bad(
                    "le catalogue des champs personnalisés n'a pas la forme attendue "
                    f"(Apollo a pu la changer) — brut : {str(catalog)[:400]}")
            rows = [r for r in index.values()
                    if modality == "all"
                    or (r.get("modality") or "contact") == modality]
            return {
                "fields": output_projection.project(
                    {"fields": rows}, items_path="fields",
                    item_drop=_FIELD_NOISE)["fields"],
                "count": len(rows),
                "modality": modality,
                "projection": {
                    "dropped": list(_FIELD_NOISE),
                    "filtered_on": f"modality={modality}",
                    "how_to_get_all_columns": "full=True (rend le catalogue brut)",
                    "how_to_get_all_objects": 'modality="all"',
                },
                "how_to_use": ('les `id` ci-dessus sont les clés de '
                               '`typed_custom_fields` sur op="update"'),
            }

        if op == "create_field":
            if not (label or "").strip():
                raise _bad('op=create_field : `label` requis (le nom du champ).')
            # Apollo ne déduplique PAS sur le libellé : un second appel crée un
            # second champ homonyme, sans rien signaler. Les deux sortent au
            # catalogue, la variable d'une séquence en désigne UN, et les écritures
            # qui visent l'autre n'apparaissent nulle part. On regarde d'abord — et
            # si le catalogue est illisible (clé non-Master), on ne bloque pas : on
            # le DIT, comme partout ailleurs ici.
            existing, dup_note = [], None
            try:
                index = _field_index(_custom_fields())
                if index is None:
                    dup_note = ("doublons non vérifiés : catalogue illisible.")
                else:
                    existing = [
                        {"id": i, "name": d.get("name") or d.get("label"),
                         "type": d.get("type")}
                        for i, d in index.items()
                        if (d.get("name") or d.get("label")) == label
                        and (d.get("modality") or "contact") == modality]
            except McpError:
                raise
            # noqa: SILENT — l'avertissement « doublons non vérifiés » est rendu à l'agent
            except Exception as e:  # noqa: BLE001
                dup_note = (f"doublons non vérifiés : le catalogue n'a pas pu être "
                            f"lu ({type(e).__name__}: {e}).")
            if existing:
                raise _bad(
                    f'un champ « {label} » existe déjà sur cet objet : {existing}. '
                    "Apollo en créerait un SECOND, homonyme, que rien ne distingue — "
                    "et une écriture qui viserait le mauvais n'apparaîtrait nulle "
                    "part. Réutilise l'id ci-dessus, ou choisis un autre libellé.")
            if dry_run:
                out = {"dry_run": True, "action": "create_field", "label": label,
                       "modality": modality, "field_type": field_type,
                       "max_length": max_length}
                if dup_note:
                    out["field_validation"] = dup_note
                return out
            created = _contact_run(lambda: _client_byo().create_custom_field(
                label=label, modality=modality, field_type=field_type,
                max_length=max_length))
            if dup_note and isinstance(created, dict):
                created = {**created, "field_validation": dup_note}
            return created

        if op == "search":
            found = _contact_run(lambda: _client_byo().search_contacts(
                q_keywords=q_keywords, contact_stage_ids=contact_stage_ids,
                contact_label_ids=contact_label_ids, sort_by_field=sort_by_field,
                sort_ascending=sort_ascending, per_page=per_page, page=page))
            if full or not isinstance(found, dict):
                return found
            out = output_projection.project(
                found, items_path="contacts", item_drop=_CONTACT_NOISE)
            out["projection"] = {
                "dropped": list(_CONTACT_NOISE),
                "why": ("deux blocs imbriqués qui pèsent plus que la fiche entière ; "
                        "`organization_name` et `account_id` restent, de quoi "
                        "rattacher sans les recharger"),
                "how_to_get_everything": "full=True, ou op=\"get\" sur un id",
            }
            return out

        if not contact_id:
            raise _bad(f"contact_id requis pour op={op}")

        if op == "get":
            return _contact_run(lambda: _client_byo().get_contact(contact_id))

        payload: dict[str, Any] = {
            k: v for k, v in (
                ("first_name", first_name), ("last_name", last_name),
                ("title", title), ("email", email),
                ("organization_name", organization_name),
                ("account_id", account_id), ("website_url", website_url),
                ("label_names", label_names),
                ("contact_stage_id", contact_stage_id),
                ("present_raw_address", present_raw_address),
                ("direct_phone", direct_phone), ("corporate_phone", corporate_phone),
                ("mobile_phone", mobile_phone), ("home_phone", home_phone),
                ("other_phone", other_phone),
                ("typed_custom_fields", typed_custom_fields),
            # `{}` et `[]` sont écartés comme `None` : un `typed_custom_fields`
            # VIDE n'exprime aucune modification, et le laisser passer produisait
            # un PATCH sans effet rendu comme une écriture réussie.
            ) if v is not None and v != {} and v != []
        }
        if not payload:
            raise _bad(
                "op=update : aucun champ à modifier — passe au moins un champ "
                '(typed_custom_fields, title, email, contact_stage_id…). Les ids de '
                'champs personnalisés se lisent avec apollo_contact(op="fields").')
        if typed_custom_fields is not None and not isinstance(typed_custom_fields, dict):
            raise _bad("typed_custom_fields doit être un objet {id_du_champ: valeur}, "
                       'keyé par les ids rendus par apollo_contact(op="fields").')

        note = _check_custom_field_ids(typed_custom_fields) if typed_custom_fields else None

        if dry_run:
            out = {"dry_run": True, "action": "update", "contact_id": contact_id,
                   "payload": payload}
            if note:
                out["field_validation"] = note
            return out
        result = _contact_run(
            lambda: _client_byo().update_contact(contact_id, **payload),
            on_422=_REFUSED_WRITE_422)
        if not note:
            return result
        # La note ne doit pas dépendre de la FORME du retour d'Apollo : « je n'ai
        # pas pu vérifier » est une information sur l'appel, pas sur la réponse.
        return ({**result, "field_validation": note} if isinstance(result, dict)
                else {"result": result, "field_validation": note})

    # ------------------------------------------------------------------
    # Email accounts & schedules — prérequis en lecture, 0 crédit, mais BYO
    # ONLY : la liste rend les boîtes/plannings DU PROPRIÉTAIRE de la clé
    # (signature HTML, score de délivrabilité, seuils quotidiens...) — la clé
    # plateforme appartient à quelqu'un d'autre, sa boîte n'a rien à faire là.
    # ------------------------------------------------------------------

    @mcp.tool()
    def apollo_email_accounts() -> dict:
        """List the mailboxes connected to THIS Apollo account (BYO key only —
        this is the key owner's own inbox configuration, never the platform key's).

        Get the `id` to pass as `send_email_from_email_account_id` to
        apollo_sequence_contacts(op="add").
        """
        client = _client_byo()
        return client.list_email_accounts()

    @mcp.tool()
    def apollo_email_schedules() -> dict:
        """List the send schedules configured on THIS Apollo team (BYO key only).

        Get the `id` to pass as `emailer_schedule_id` to
        apollo_sequence(op="create") — required, creation fails without it.
        """
        client = _client_byo()
        return client.list_email_schedules()

    # ------------------------------------------------------------------
    # Sequences
    # ------------------------------------------------------------------

    @mcp.tool()
    def apollo_sequence(
        op: Literal["search", "create", "update", "activate", "deactivate", "archive"],
        sequence_id: Optional[str] = None,
        name: Optional[str] = None,
        emailer_schedule_id: Optional[str] = None,
        active: Optional[bool] = None,
        label_names: Optional[list[str]] = None,
        folder_id: Optional[str] = None,
        max_emails_per_day: Optional[int] = None,
        cc_emails: Optional[str] = None,
        bcc_emails: Optional[str] = None,
        emailer_steps: Optional[list[dict]] = None,
        per_page: int = 25,
        page: int = 1,
        dry_run: bool = False,
    ) -> dict:
        """Manage Apollo sequences (search/create/update/activate/deactivate/archive).

        Args by op:
        - `search`: `name` (partial match), `per_page`, `page`. BYO key only — this
          lists the key owner's OWN sequences (names + open/reply rates).
        - `create`: `name` + `emailer_schedule_id` REQUIRED (get one from
          apollo_email_schedules — the API refuses creation without it). Optional
          `active` (default False — prefer op="activate" once steps/templates are
          reviewed), `label_names`, `folder_id`, `max_emails_per_day`, `emailer_steps`
          (nested step/template definitions — see Apollo docs). BYO key only.
        - `update`: `sequence_id` + any of `name`, `active`, `emailer_schedule_id`,
          `label_names`, `max_emails_per_day`, `cc_emails`, `bcc_emails`,
          `emailer_steps` (include `id` per step to MODIFY it, omit to CREATE one).
          BYO key only.
        - `activate` / `deactivate`: `sequence_id`. BYO key only.
        - `archive`: `sequence_id`. BYO key only. Supports `dry_run=True` (no
          get-by-id exists on this endpoint — the preview cannot show a real diff,
          just echoes the intended action).

        `dry_run=True` on `create`/`update`/`activate`/`deactivate`/`archive`
        validates but skips the actual API call.
        """
        if op not in ("search", "create", "update", "activate", "deactivate", "archive"):
            raise _bad(f'op inconnu "{op}" — attendu: search, create, update, activate, '
                       'deactivate, archive')

        client = _client_byo()
        if op == "search":
            return client.search_sequences(name=name, per_page=per_page, page=page)

        try:
            if op == "create":
                if not name or not emailer_schedule_id:
                    raise ValueError("name et emailer_schedule_id requis pour créer une séquence")
                if dry_run:
                    return {"dry_run": True, "action": "create", "name": name,
                            "emailer_schedule_id": emailer_schedule_id,
                            "active": bool(active), "label_names": label_names or []}
                return client.create_sequence(
                    name=name, emailer_schedule_id=emailer_schedule_id,
                    active=bool(active), label_names=label_names, folder_id=folder_id,
                    max_emails_per_day=max_emails_per_day, emailer_steps=emailer_steps)

            if not sequence_id:
                raise ValueError("sequence_id requis")

            if op == "update":
                fields: dict[str, Any] = {}
                if name is not None:
                    fields["name"] = name
                if active is not None:
                    fields["active"] = active
                if emailer_schedule_id is not None:
                    fields["emailer_schedule_id"] = emailer_schedule_id
                if label_names is not None:
                    fields["label_names"] = label_names
                if max_emails_per_day is not None:
                    fields["max_emails_per_day"] = max_emails_per_day
                if cc_emails is not None:
                    fields["cc_emails"] = cc_emails
                if bcc_emails is not None:
                    fields["bcc_emails"] = bcc_emails
                if emailer_steps is not None:
                    fields["emailer_steps"] = emailer_steps
                if dry_run:
                    return {"dry_run": True, "action": "update", "sequence_id": sequence_id,
                            "changes": fields, "current_available": False}
                return client.update_sequence(sequence_id, **fields)

            if op == "activate":
                if dry_run:
                    return {"dry_run": True, "action": "activate", "sequence_id": sequence_id}
                return client.activate_sequence(sequence_id)

            if op == "deactivate":
                if dry_run:
                    return {"dry_run": True, "action": "deactivate", "sequence_id": sequence_id}
                return client.deactivate_sequence(sequence_id)

            if op == "archive":
                if dry_run:
                    return {"dry_run": True, "action": "archive", "sequence_id": sequence_id,
                            "current_available": False}
                return client.archive_sequence(sequence_id)
        except ValueError as e:
            raise _bad(str(e))

    @mcp.tool()
    def apollo_sequence_contacts(
        op: Literal["add", "update_status", "activity"],
        sequence_id: Optional[str] = None,
        contact_ids: Optional[list[str]] = None,
        label_names: Optional[list[str]] = None,
        send_email_from_email_account_id: Optional[str] = None,
        send_email_from_email_address: Optional[str] = None,
        status: Optional[str] = None,
        emailer_campaign_ids: Optional[list[str]] = None,
        mode: Optional[Literal["mark_as_finished", "remove", "stop"]] = None,
        contact_id: Optional[str] = None,
        per_page: int = 50,
        dry_run: bool = False,
    ) -> dict:
        """Enroll/update/inspect contacts in Apollo sequences. BYO key only for
        `add`/`update_status` (starts or stops an automated campaign to real
        people); `activity` (read) accepts the platform key.

        Args by op:
        - `add`: `sequence_id` + `send_email_from_email_account_id` (id of a
          CONNECTED mailbox — get one from apollo_email_accounts; REQUIRED, refused
          locally without it) + `contact_ids` or `label_names` (at least one). The
          HIGHEST-RISK call here: it starts a multi-step automated campaign to real
          people, not a single send. Supports `dry_run=True` (echoes the request,
          no get-by-id available to show a real diff).
        - `update_status`: `emailer_campaign_ids` + `contact_ids` + `mode`
          (`mark_as_finished`, `remove`, or `stop`). Supports `dry_run=True`.
        - `activity`: `contact_id` (required) + optional `sequence_id` filter +
          `per_page` (1-50, most recent events, not pagination). 0 credit, but BYO
          key only — the events are the key owner's OWN sequences.
        """
        if op not in ("add", "update_status", "activity"):
            raise _bad(f'op inconnu "{op}" — attendu: add, update_status, activity')

        if op == "activity":
            if not contact_id:
                raise _bad("contact_id requis pour op=activity")
            client = _client_byo()
            try:
                return client.get_contact_sequence_activity(
                    contact_id, sequence_id=sequence_id, per_page=per_page)
            except ValueError as e:
                raise _bad(str(e))

        client = _client_byo()
        try:
            if op == "add":
                if not sequence_id:
                    raise ValueError("sequence_id requis")
                if not send_email_from_email_account_id:
                    raise ValueError(
                        "send_email_from_email_account_id requis — obtiens-le via "
                        "apollo_email_accounts()")
                if not contact_ids and not label_names:
                    raise ValueError("contact_ids ou label_names requis")
                if dry_run:
                    return {"dry_run": True, "action": "add", "sequence_id": sequence_id,
                            "send_email_from_email_account_id": send_email_from_email_account_id,
                            "contact_ids": contact_ids or [], "label_names": label_names or []}
                return client.add_contacts_to_sequence(
                    sequence_id, send_email_from_email_account_id,
                    contact_ids=contact_ids, label_names=label_names,
                    send_email_from_email_address=send_email_from_email_address,
                    status=status)

            if op == "update_status":
                if not emailer_campaign_ids:
                    raise ValueError("emailer_campaign_ids requis")
                if not contact_ids:
                    raise ValueError("contact_ids requis")
                if mode not in ("mark_as_finished", "remove", "stop"):
                    raise ValueError('mode doit être "mark_as_finished", "remove" ou "stop"')
                if dry_run:
                    return {"dry_run": True, "action": "update_status", "mode": mode,
                            "emailer_campaign_ids": emailer_campaign_ids,
                            "contact_ids": contact_ids}
                return client.update_sequence_contact_status(
                    emailer_campaign_ids, contact_ids, mode)
        except ValueError as e:
            raise _bad(str(e))

    # ------------------------------------------------------------------
    # One-off emails
    # ------------------------------------------------------------------

    @mcp.tool()
    def apollo_email(
        op: Literal["draft", "send", "status", "search", "content", "stats"],
        contact_id: Optional[str] = None,
        subject: Optional[str] = None,
        body_html: Optional[str] = None,
        recipients: Optional[list[dict]] = None,
        in_response_to_emailer_message_id: Optional[str] = None,
        emailer_template_id: Optional[str] = None,
        attachment_ids: Optional[list[str]] = None,
        enable_tracking: Optional[bool] = None,
        outreach_task_id: Optional[str] = None,
        message_id: Optional[str] = None,
        surface: Optional[str] = None,
        stats: Optional[list[str]] = None,
        reply_classes: Optional[list[str]] = None,
        sequence_ids: Optional[list[str]] = None,
        exclude_sequence_ids: Optional[list[str]] = None,
        keywords: Optional[str] = None,
        date_range_mode: Optional[str] = None,
        date_min: Optional[str] = None,
        date_max: Optional[str] = None,
        per_page: int = 25,
        page: int = 1,
        ids: Optional[list[str]] = None,
        body_format: str = "plain",
        dry_run: bool = False,
    ) -> dict:
        """One-off emails (outside sequences). `draft` and `send` are ALWAYS two
        distinct steps — never collapsed — so nothing confuses "prepare" and "send".
        BYO key only on EVERY op: `search`/`content`/`stats` read the key owner's OWN
        sent mailbox (bodies included), not Apollo's shared database — unlike
        organization/people search, a platform key here would leak someone else's inbox.

        Args by op:
        - `draft`: `contact_id` required UNLESS `in_response_to_emailer_message_id`
          is set (thread reply). Optional `subject`, `body_html`, `recipients`
          (`[{"email":, "contact_id":, "recipient_type_cd": "to"|"cc"|"bcc"}]`),
          `emailer_template_id`, `attachment_ids`, `enable_tracking`,
          `outreach_task_id`. Does NOT send.
        - `send`: `message_id` (from `draft`'s response) + optional `surface`.
          IRREVERSIBLE — the only gesture here that reaches a real person by direct
          email. Supports `dry_run=True` (validates message_id is set, does not call).
        - `status`: `message_id` — poll send status.
        - `search`: `stats`, `reply_classes`, `sequence_ids`, `exclude_sequence_ids`,
          `keywords`, `date_range_mode` (`due_at`/`completed_at`), `date_min`/`date_max`
          (`YYYY-MM-DD`), `per_page`, `page`.
        - `content`: `ids` (up to 10 SENT email ids) + `body_format` (`plain`/`html`).
        - `stats`: `message_id` — opens/clicks. ⚠️ Apollo doc says this needs a
          Master API key, not verified from this environment.
        """
        if op not in ("draft", "send", "status", "search", "content", "stats"):
            raise _bad(f'op inconnu "{op}" — attendu: draft, send, status, search, '
                       'content, stats')

        client = _client_byo()
        try:
            if op == "draft":
                if not contact_id and not in_response_to_emailer_message_id:
                    raise ValueError(
                        "contact_id requis, sauf en réponse à un fil "
                        "(in_response_to_emailer_message_id)")
                return client.create_email_draft(
                    contact_id=contact_id, subject=subject, body_html=body_html,
                    recipients=recipients,
                    in_response_to_emailer_message_id=in_response_to_emailer_message_id,
                    emailer_template_id=emailer_template_id,
                    attachment_ids=attachment_ids, enable_tracking=enable_tracking,
                    outreach_task_id=outreach_task_id)

            if op == "send":
                if not message_id:
                    raise ValueError("message_id requis")
                if dry_run:
                    return {"dry_run": True, "action": "send", "message_id": message_id}
                return client.send_email_now(message_id, surface=surface)

            if op == "status":
                if not message_id:
                    raise ValueError("message_id requis")
                return client.check_email_send_status(message_id)
            if op == "search":
                return client.search_emails(
                    stats=stats, reply_classes=reply_classes, sequence_ids=sequence_ids,
                    exclude_sequence_ids=exclude_sequence_ids, keywords=keywords,
                    date_range_mode=date_range_mode, date_min=date_min, date_max=date_max,
                    per_page=per_page, page=page)
            if op == "content":
                if not ids:
                    raise ValueError("ids requis (au moins un)")
                return client.get_email_content(ids, body_format=body_format)
            if op == "stats":
                if not message_id:
                    raise ValueError("message_id requis")
                return client.get_email_stats(message_id)
        except ValueError as e:
            raise _bad(str(e))

    # ------------------------------------------------------------------
    # Conversations — byo-only sur TOUS les ops : transcripts d'appels/visios
    # réels + coût crédit conditionnel (imprévisible, pas métrable a priori).
    # ------------------------------------------------------------------

    @mcp.tool()
    def apollo_conversation(
        op: Literal["search", "get", "export", "export_status"],
        conversation_id: Optional[str] = None,
        conversation_type: Optional[str] = None,
        account_id: Optional[str] = None,
        contact_ids: Optional[list[str]] = None,
        tag_ids: Optional[list[str]] = None,
        tracker_ids: Optional[list[str]] = None,
        organization_ids: Optional[list[str]] = None,
        date_range: Optional[dict] = None,
        per_page: int = 25,
        page: int = 1,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        email: Optional[str] = None,
        export_id: Optional[str] = None,
    ) -> dict:
        """Recorded conversations (calls/video meetings) — transcripts, recordings,
        participants. BYO key only (all ops): real recorded conversations, and the
        credit cost is conditional (1 if the conversation has AI insights, 0
        otherwise) so it can't be metered against a platform quota up front.

        Args by op:
        - `search`: `conversation_type` (`video_conference`/`phone_call`),
          `account_id`, `contact_ids`, `tag_ids`, `tracker_ids`, `organization_ids`,
          `date_range` (`{"start":, "end":}` ISO 8601), `per_page`, `page`.
        - `get`: `conversation_id`. ⚠️ 1 Apollo credit IF the conversation has AI
          insights, 0 otherwise — not knowable before the call.
        - `export`: `start_time` + `end_time` (ISO 8601, GMT) + `email` (team member
          to notify). ASYNC — returns `export_id`; does not wait for completion.
          Poll with `export_status`.
        - `export_status`: `export_id` (from `export`) — returns `redirect_url`
          once ready.
        """
        if op not in ("search", "get", "export", "export_status"):
            raise _bad(f'op inconnu "{op}" — attendu: search, get, export, export_status')

        client = _client_byo()
        try:
            if op == "search":
                return client.search_conversations(
                    conversation_type=conversation_type, account_id=account_id,
                    contact_ids=contact_ids, tag_ids=tag_ids, tracker_ids=tracker_ids,
                    organization_ids=organization_ids, date_range=date_range,
                    per_page=per_page, page=page)
            if op == "get":
                if not conversation_id:
                    raise ValueError("conversation_id requis")
                return client.get_conversation(conversation_id)
            if op == "export":
                if not start_time or not end_time or not email:
                    raise ValueError("start_time, end_time et email requis")
                return client.export_conversations(start_time, end_time, email)
            if op == "export_status":
                if not export_id:
                    raise ValueError("export_id requis")
                return client.get_conversations_export(export_id)
        except ValueError as e:
            raise _bad(str(e))
