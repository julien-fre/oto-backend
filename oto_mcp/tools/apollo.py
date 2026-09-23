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
  non métrés. ⚠️ **Quota épuisé = refus NOMMÉ, jamais un fallback silencieux**
  (oto-backend#710, signaux #311/#312/#313) : `resolve_api_key` lève une McpError
  qui dit le compteur (`used/limit`) et qu'il repart à minuit — et
  `apollo_match_person`, seul débiteur de ce quota, échote `platform_quota`
  (`access.platform_quota_hint`, `oto_mcp/access/resolve.py`) dans sa réponse
  QUAND la clé est plateforme, pour qu'un worker batch arbitre AVANT le refus au
  lieu de le découvrir au milieu d'un lead.
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
- **Les REVEALS** (`apollo_reveal_phone`, et `apollo_match_person(reveal_personal_emails=True)`) :
  byo-only aussi, mais pour un TROISIÈME motif — le COÛT, pas la frontière de
  données. Ils interrogent bien la base partagée, donc rien n'empêcherait la clé
  commune ; ce qui l'empêche est que le compteur (`record_platform_usage`) débite
  1 unité par appel, le prix d'un match nu, alors qu'Apollo facture un supplément
  par-dessus (~9 crédits pour un téléphone ; barème selon plan, non mesuré, pour
  les emails personnels). `platform_quota` mentirait d'un facteur qu'on ne sait
  pas nommer, et c'est le seul chiffre sur lequel un worker batch s'arrête.

⚠️ Doc Apollo (pas vérifié depuis cet environnement, pas de clé disponible ici) :
`add_contact_ids` et `/emailer_messages/{id}/activities` (stats email) exigent
une clé « Master » et 403 sinon — à confirmer avec une clé réelle.
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

from .. import access, output_projection, session_org


def _bad(msg: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=msg))


def register(mcp: FastMCP) -> None:
    from oto.tools.apollo.client import ApolloClient, ApolloError

    def _client(units: int = 1) -> tuple[ApolloClient, bool]:
        # `units` : taille du lot, pour que le quota de la clé commune soit vérifié
        # pour tout le lot avant l'appel (oto#168).
        key, is_platform = access.resolve_api_key("apollo", units=units)
        return ApolloClient(api_key=key), is_platform

    _BYO_ESPACE_PRIVE = (
        "ceci ne concerne QUE les séquences/emails/conversations (tes propres "
        "données) : la recherche et l'enrichissement Apollo restent utilisables "
        "sans ta propre clé.")

    # Les REVEALS d'Apollo — téléphone ET emails personnels — sont byo-only pour
    # une raison DIFFÉRENTE de tout le reste de ce module : ce n'est pas une
    # frontière de données, c'est le COÛT. Un reveal fait facturer à Apollo un
    # SUPPLÉMENT par-dessus le match, pendant que le compteur plateforme
    # (`record_platform_usage`) débite 1 unité par appel — le prix d'un match nu,
    # quoi qu'il arrive. Or `platform_quota` existe précisément pour qu'un worker
    # batch s'arrête AVANT le mur (oto-backend#710) : le laisser mentir casserait
    # la seule mesure sur laquelle il s'appuie.
    #
    # ⚠️ La MÊME règle pour les deux, délibérément. Ce qui les sépare est la
    # taille de l'écart, pas sa nature : pour le téléphone il est mesuré (~9
    # crédits là où un match nu en coûte 1) ; pour les emails personnels il ne
    # l'est pas — Apollo les facture sur un pot distinct dont le barème dépend du
    # plan, et aucune de nos mesures ne le chiffre. Un facteur inconnu n'est pas
    # un facteur nul : ouvrir la clé commune au seul reveal dont on ignore le
    # multiplicateur reviendrait à dire que le compteur ment moins quand on ne
    # sait pas de combien. Le jour où l'écart est mesuré ET où le compteur sait
    # le débiter, c'est là que la règle peut changer — pas avant.
    _BYO_REVEAL_TELEPHONE = (
        "le reveal de téléphone ne passe JAMAIS par la clé plateforme (Apollo le "
        "facture ~9 crédits quand un match nu en coûte 1) : pose ta propre clé "
        "Apollo. La recherche et `apollo_match_person` continuent de marcher sans.")
    _BYO_REVEAL_EMAILS_PERSO = (
        "`reveal_personal_emails=True` ne passe JAMAIS par la clé plateforme : "
        "Apollo facture ce reveal EN PLUS du match, sur un pot dont le barème "
        "dépend du plan, alors que notre compteur ne sait débiter qu'un match nu "
        "— pose ta propre clé Apollo. Sans elle `apollo_match_person` marche "
        "toujours, il ne rend simplement pas les emails personnels.")

    def _client_byo(precision: str = _BYO_ESPACE_PRIVE) -> ApolloClient:
        """Client résolu SANS palier plateforme — pour tout appel qui écrit
        (enrôlement, envoi), lit des données sensibles (conversations) ou
        engage une dépense hors barème (les reveals : téléphone, emails
        personnels).

        Apollo est le premier connecteur à mélanger les deux régimes dans le
        MÊME module (recherche/enrichissement = platform_key_open, tout le
        reste = byo-only) : le message générique de `resolve_credential`
        (« Aucun credential configuré pour toi ») serait trompeur pour un
        user qui voit déjà apollo_search_organizations fonctionner via la clé
        plateforme et ne comprendrait pas pourquoi CET appel-ci le refuse.
        D'où `precision` : le motif du refus n'est pas le même partout, et
        servir « tes propres données » à qui bute sur un mur de COÛT l'enverrait
        chercher au mauvais endroit."""
        try:
            return ApolloClient(api_key=access.resolve_credential("apollo", want="byo").key)
        except McpError as e:
            msg = e.error.message or ""
            if "Aucun credential" in msg:
                # Seul CE message générique (absence totale de credential BYO) est
                # ambigu ici — les autres (multi-compte, compte introuvable) sont
                # déjà précis et n'ont rien à voir avec platform vs byo.
                raise McpError(ErrorData(
                    code=INVALID_PARAMS, message=f"{msg} — {precision}"))
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
        fields: Optional[list[str]] = None,
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
            fields: keep ONLY these keys on each organization (e.g.
                ["name", "primary_domain", "linkedin_url"]); the envelope
                (pagination, totals) is kept. Use it when paginating: a full page
                at per_page=100 is ~113 000 characters, past some clients' cap.
        """
        client, _ = _client()
        found = client.search_organizations(
            name=name, domain=domain, country=country, per_page=per_page, page=page,
            employee_ranges=employee_ranges, revenue_min=revenue_min,
            revenue_max=revenue_max, locations=locations, keywords=keywords,
            technologies=technologies, org_ids=org_ids)
        # Projection OPT-IN (`fields` omis ⇒ payload inchangé). Le défaut n'est pas
        # touché ici : le choisir demanderait de mesurer quelles clés d'une fiche
        # organisation ne servent jamais, comme `_CONTACT_NOISE` l'a été plus haut.
        # Ce que ce lot corrige est l'ABSENCE de sortie : sans `fields`, une page à
        # per_page=100 (~113 000 c.) dépasse la limite de sortie de certains clients
        # MCP et l'appel devient inexploitable — signal #645.
        if not fields:
            return found
        # `mixed_companies/search` rend DEUX listes — `organizations` et `accounts`
        # (les sociétés déjà présentes dans le compte Apollo). Projeter la seule
        # première laisserait `fields` sans effet visible sur la moitié du payload,
        # ce qui est pire que pas de paramètre du tout. `project` est pur et tolère
        # un chemin absent : le chaînage est sûr dans les deux sens.
        out = output_projection.project(found, items_path="organizations",
                                        fields=fields)
        return output_projection.project(out, items_path="accounts", fields=fields)

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

    # Le poids d'un match tient dans la fiche ORGANISATION imbriquée, et dans
    # CINQ de ses clés : mesuré sur un match réel le 2026-09-11, `organization`
    # pèse 55 404 caractères sur 60 701, dont `current_technologies` 32 321 à lui
    # seul. L'appel DÉPASSAIT la limite de sortie d'un client MCP pour UNE seule
    # personne — même mode de panne que le signal #645 sur
    # `apollo_search_organizations`, et sur l'outil que toute construction de
    # liste appelle en boucle. Sans ça, sourcer 50 contacts = 3 M de caractères.
    #
    # DENYLIST nommée, jamais une allowlist : `name`, `primary_domain`, `phone`,
    # `industry`, `estimated_num_employees`, `short_description` restent, et une
    # clé qu'Apollo ajouterait demain reste visible (leçon `fr_get`/`liste_idcc`).
    # ⚠️ Et `organization` ne se retire PAS en bloc, contrairement à
    # `_CONTACT_NOISE` : `people/match` ne rend aucun `organization_name` au
    # premier niveau (vérifié le 2026-09-11), donc la retirer entière perdrait le
    # nom de la boîte — ce que la fiche contact, elle, garde.
    _MATCH_ORG_NOISE = ("current_technologies", "technology_names",
                        "funding_events", "suborganizations", "keywords")

    def _light_org(person):
        """Une fiche personne dont l'organisation a perdu ses blocs de masse.

        Rend `(fiche, allégée?)` — le booléen dit s'il y avait quelque chose à
        retirer, pour ne pas annoncer une projection qui n'a rien fait."""
        if not isinstance(person, dict):
            return person, False
        org = person.get("organization")
        if not isinstance(org, dict):
            return person, False
        allege = {k: v for k, v in org.items() if k not in _MATCH_ORG_NOISE}
        if len(allege) == len(org):
            return person, False
        return {**person, "organization": allege}, True

    # Le LOT a sa propre mesure, et ce n'est pas celle de l'unitaire. Mesuré le
    # 2026-09-11 sur l'exemple de réponse que documente Apollo pour
    # `people/bulk_match` (forme réelle, fiches répétées jusqu'à 10) : 88 740 c.
    # servis bruts, 85 941 après la seule coupe de l'organisation — au-dessus des
    # 60 693 c. qui débordaient déjà un client MCP pour UNE personne. Une fiche de lot
    # pèse ~6 300 c., dont `employment_history` 2 525 et `account` 1 756 (la fiche
    # SOCIÉTÉ du CRM Apollo de l'appelant, qui double `organization`). Ce qu'une
    # construction de liste vient chercher — le nom révélé, l'intitulé, l'email, le
    # LinkedIn, l'employeur — n'est dans aucun des deux. Un lot tronqué par le client
    # est un lot perdu, et payé. DENYLIST nommée, comme au-dessus ; `full=True` rend tout.
    _LOT_PERSON_NOISE = ("employment_history", "account")

    def _light_match(person):
        """Une fiche de LOT : l'organisation allégée (`_light_org`), puis les deux blocs
        qui font le poids d'un lot. Rend `(fiche, allégée?)`, comme `_light_org`."""
        person, allegee = _light_org(person)
        if not isinstance(person, dict):
            return person, allegee
        reste = {k: v for k, v in person.items() if k not in _LOT_PERSON_NOISE}
        return (reste, True) if len(reste) < len(person) else (person, allegee)

    def _projection_bloc(lot: bool = False) -> dict:
        dropped = [f"organization.{k}" for k in _MATCH_ORG_NOISE]
        why = ("blocs de masse de la fiche entreprise — 91 % du payload, et "
               "l'appel dépassait la limite de sortie pour UNE personne")
        if lot:
            dropped = list(_LOT_PERSON_NOISE) + dropped
            why = ("historique d'emploi, fiche société du CRM Apollo et blocs de masse "
                   "de l'employeur — un lot de 10 dépassait la limite de sortie d'un "
                   "client MCP")
        return {"dropped": dropped, "why": why, "how_to_get_everything": "full=True"}

    def _light_person(payload: dict) -> dict:
        """Allège `person.organization` des cinq blocs de masse, et le DIT."""
        person, allegee = _light_org(payload.get("person"))
        if not allegee:
            return payload
        out = {**payload, "person": person}
        out["projection"] = _projection_bloc()
        return out

    # ⚠️ Le reveal servait sa fiche ENTIÈRE — il était le seul des trois à ne pas
    # être allégé, et c'est l'outil du signalement client. Mesuré sur un appel RÉEL
    # en production le 2026-09-11 (org sur clé payante, une personne) : **65 374
    # caractères**, dont `person.organization` 59 244 et `employment_history` 4 396.
    # Le client MCP a REFUSÉ la réponse (`exceeds maximum allowed tokens`) : l'appel
    # a coûté ses crédits, les numéros étaient commandés, et l'agent n'a rien pu
    # lire — exactement la panne que la projection existe pour empêcher, sur le seul
    # outil qui l'avait manquée. Allégé : 5 457 c., soit 92 % de moins.
    #
    # ⚠️ Et `phone_enrichment.request_id` N'EST PAS l'identifiant de sondage : Apollo
    # en rend DEUX (`6aa46cf…` interne, et le `request_id` signé 64 bits au premier
    # niveau) et son propre message dit d'employer « the top-level `request_id` ».
    # Deux identifiants dont un seul marche, dans la même réponse, c'est un piège —
    # on retire celui qui ne sonde rien et on le NOMME, le message d'Apollo restant
    # là pour expliquer lequel vaut.
    _REVEAL_TRAP = ("phone_enrichment.request_id",)

    def _light_reveal(payload: dict) -> dict:
        person, allegee = _light_match(payload.get("person"))
        out = {**payload, "person": person} if allegee else dict(payload)

        piege = False
        pe = out.get("phone_enrichment")
        if isinstance(pe, dict) and "request_id" in pe:
            out["phone_enrichment"] = {k: v for k, v in pe.items() if k != "request_id"}
            piege = True

        if not (allegee or piege):
            return payload
        bloc = _projection_bloc(lot=True)
        bloc["dropped"] = ([*_REVEAL_TRAP] if piege else []) + (
            bloc["dropped"] if allegee else [])
        bloc["why"] = ("fiche entreprise, historique d'emploi et fiche société du CRM "
                       "Apollo — 65 374 c. mesurés sur un reveal réel, refusés par le "
                       "client ; plus l'identifiant de `phone_enrichment`, qui ne "
                       "sonde RIEN (c'est `request_id` au premier niveau qui sonde)")
        out["projection"] = bloc
        return out

    # ⚠️ Le SONDAGE rend les mêmes fiches que le reveal, mais PAS sous la même forme :
    # l'enveloppe du webhook, avec un tableau `webhook_result.people[]`, et non un
    # `person` au premier niveau. Réappliquer `_light_reveal` tel quel ne mordrait
    # sur rien (il lit `payload["person"]`) et passerait pour un correctif — la
    # réponse resterait entière, ~15 000 c. par personne, et un lot de 50 ne tenait
    # dans aucun contexte (otomata-tech/oto#186). On projette donc CHAQUE élément de
    # `people[]` avec la coupe du lot, et on le DIT, sur le chemin réel.
    def _light_reveal_result(result: dict) -> tuple[dict, Optional[dict]]:
        """`(enveloppe, bloc de projection | None)` — `None` : rien n'a été retiré."""
        wr = result.get("webhook_result")
        people = wr.get("people") if isinstance(wr, dict) else None
        if not isinstance(people, list):
            return result, None
        allegees = [_light_match(p) for p in people]
        if not any(a for _, a in allegees):
            return result, None
        out = {**result, "webhook_result": {**wr, "people": [p for p, _ in allegees]}}
        bloc = _projection_bloc(lot=True)
        bloc["dropped"] = [f"result.webhook_result.people[].{d}" for d in bloc["dropped"]]
        return out, bloc

    def _stringify_request_id(payload: dict) -> dict:
        """`request_id` en CHAÎNE — Apollo en rend un à CHAQUE match, reveal ou pas.

        ⚠️ C'est un entier SIGNÉ 64 bits (~7,2e17, souvent négatif) : il dépasse
        la précision d'un nombre JavaScript, et la réponse d'un outil traverse du
        JSON jusqu'à des clients qui en sont faits. Mesuré en prod le 2026-09-11,
        un match nu rendait `-4604290848231370000` — quatre zéros de queue, une
        valeur que le float64 a déjà réécrite. Un agent qui repasse cet id à
        `apollo_reveal_phone_result` sonde un identifiant qui n'existe pas.
        `apollo_reveal_phone` le sérialisait déjà ; le match, non.
        """
        rid = payload.get("request_id")
        if rid is None or isinstance(rid, str):
            return payload
        return {**payload, "request_id": str(rid)}

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
        reveal_personal_emails: Optional[bool] = None,
        full: bool = False,
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

        ⚠️ ON THE SHARED PLATFORM KEY, the response carries `platform_quota`
        (`used`/`limit`/`remaining` TODAY, on THIS key) — check it while working
        through a batch to stop BEFORE the next call hits the wall, instead of
        finding out mid-lead. Absent on a BYO key (no ceiling applies) or when the
        platform quota is unlimited for your org. Once `remaining` reaches 0, the
        NEXT call fails outright (no partial/degraded match) — either pose your own
        key, or fall back for THIS lead to hunter_email_finder (email) and
        kaspr_enrich_linkedin / fullenrich_enrich_linkedin (phone, LinkedIn history):
        different source, no credit burned on a call that would just fail.

        ⚠️ NO MOBILE OR DIRECT DIAL HERE: Apollo never returns those synchronously.
        apollo_reveal_phone orders them, on your own Apollo key.

        ⚠️ `reveal_personal_emails=True` needs YOUR OWN Apollo key too: Apollo bills
        that reveal ON TOP of the match, and the shared key's meter can only charge a
        plain match. Same rule as apollo_reveal_phone.

        The employer's heaviest blocks (tech stack, funding, sub-orgs, keywords) are
        dropped by default — they were 91% of the payload and overflowed MCP output
        limits on ONE person. `projection` names them; `full=True` returns them.

        Args:
            reveal_personal_emails: also return PERSONAL emails (your own Apollo key;
                withheld in GDPR regions, so empty is an answer, not a failure).
            full: return Apollo's payload untouched, tech stack and all. Costs the
                same — this is about size, not data you are missing.
        """
        # Un reveal ne part jamais sur la clé commune — cf. `_BYO_REVEAL_*`
        # ci-dessus. C'est le GESTE qui bascule, pas l'outil : `apollo_match_person`
        # reste ouvert au palier plateforme tant qu'on ne demande aucun reveal.
        if reveal_personal_emails:
            client, is_platform = _client_byo(_BYO_REVEAL_EMAILS_PERSO), False
        else:
            client, is_platform = _client()
        try:
            result = client.match_person(
                person_id=person_id, linkedin_url=linkedin_url, email=email,
                first_name=first_name, last_name=last_name, name=name,
                domain=domain, org_name=org_name,
                reveal_personal_emails=reveal_personal_emails) or {}
        except ValueError as e:
            raise McpError(ErrorData(code=INVALID_PARAMS, message=str(e)))
        if is_platform:
            access.record_platform_usage("apollo")
            quota = access.platform_quota_hint("apollo")
            if quota is not None:
                result = {**result, "platform_quota": quota}
        result = _stringify_request_id(result)
        return result if full else _light_person(result)

    # ------------------------------------------------------------------
    # Téléphone direct — le seul geste de ce module qui ne rend PAS son
    # résultat. Apollo vérifie les numéros de son côté et les POSTe à une URL
    # quelques minutes plus tard ; la réponse immédiate ne porte qu'un
    # `request_id`. Le sondage (`webhook_result/{id}`, 0 crédit, 30 jours)
    # est ce qui permet de rendre le numéro À L'AGENT sans qu'oto héberge le
    # moindre receveur — donc sans route entrante non authentifiée, et sans
    # que le numéro ne quitte la boucle de travail.
    #
    # Forme submit + poll : celle de `fullenrich_enrich_linkedin`/
    # `fullenrich_result`, née du signal #252 (un sondage in-process de 131-147 s
    # survivait à aucun client MCP, et les crédits étaient déjà dépensés).
    # ------------------------------------------------------------------

    def _webhook_destination(url: str) -> str:
        """Refuse une `webhook_url` à laquelle Apollo ne livrera jamais.

        ⚠️ Ce n'est PAS la garde SSRF de `web.check_url_public`, et le risque
        n'est pas le même : ici c'est APOLLO qui émet la requête, pas nous —
        notre réseau n'est pas la cible. Ce qui se joue est le COÛT : le reveal
        est facturé même quand le POST n'arrive nulle part, donc une URL
        manifestement inatteignable se refuse AVANT les ~9 crédits, pas après.
        """
        import ipaddress
        import os
        import socket
        from urllib.parse import urlsplit

        parts = urlsplit((url or "").strip())
        if parts.scheme != "https":
            raise _bad(
                "`webhook_url` must be https:// — Apollo only delivers to HTTPS "
                f"endpoints, and would never POST to `{parts.scheme or url}`.")
        host = parts.hostname
        if not host:
            raise _bad("`webhook_url` has no host.")

        # oto n'est pas un receveur de webhook (même règle que `tally_webhook`) :
        # pointer Apollo vers nous jette les numéros, et le crédit avec.
        notres = {"mcp.oto.cx", "mcp.oto.ninja"}
        base = urlsplit(os.environ.get("OTO_MCP_PUBLIC_URL", "")).hostname
        if base:
            notres.add(base)
        h = host.lower()
        if any(h == n or h.endswith(f".{n}") for n in notres):
            raise _bad(
                "oto is not itself a webhook receiver — pointing Apollo at "
                f"`{host}` drops the numbers. Give a URL YOU control (an n8n or "
                "Make endpoint, your own service), then read the result back "
                "with apollo_reveal_phone_result.")

        try:
            ips = [ipaddress.ip_address(i[4][0])
                   for i in socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)]
        except OSError as e:
            raise _bad(f"`{host}` does not resolve ({e}) — Apollo could not "
                       "deliver there, and the reveal would be billed anyway.")
        for ip in ips:
            if not ip.is_global:
                raise _bad(
                    f"`{host}` resolves to {ip}, a non-public address — Apollo "
                    "delivers from the internet and would never reach it. The "
                    "reveal would be billed with nothing to collect.")
        return url.strip()

    @mcp.tool()
    def apollo_reveal_phone(
        webhook_url: str,
        person_id: Optional[str] = None,
        linkedin_url: Optional[str] = None,
        email: Optional[str] = None,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
        name: Optional[str] = None,
        domain: Optional[str] = None,
        org_name: Optional[str] = None,
        full: bool = False,
    ) -> dict:
        """Order someone's phone numbers, mobile and direct dial included (ASYNC).

        Same identifiers as apollo_match_person — the surest is `person_id` from
        apollo_search_people.

        ⚠️ THE NUMBERS ARE NOT IN THIS RESPONSE. Apollo POSTs them to `webhook_url`
        minutes later; what comes back here is a `request_id`. Pass it to
        apollo_reveal_phone_result to read the same payload back, free, for 30 days
        — and KEEP IT where the next agent will look (a datastore row, the run
        journal). Lose it and the credits are spent with nothing to collect.

        ⚠️ Your own Apollo key only: a reveal costs ~9 Apollo credits where a plain
        match costs 1, so it never runs on the shared platform key.

        Args:
            webhook_url: HTTPS endpoint Apollo POSTs to — mandatory on its side.
                oto is not a webhook receiver: give a URL YOU control. You need not
                read it, apollo_reveal_phone_result returns the same payload.
            full: keep the employer's tech stack, the employment history and the
                Apollo CRM account record. Off by default: a real reveal came back
                at 65 374 characters and the client refused it outright. Same price.
        """
        client = _client_byo(_BYO_REVEAL_TELEPHONE)
        destination = _webhook_destination(webhook_url)
        try:
            out = client.match_person(
                person_id=person_id, linkedin_url=linkedin_url, email=email,
                first_name=first_name, last_name=last_name, name=name,
                domain=domain, org_name=org_name,
                reveal_phone_number=True, webhook_url=destination) or {}
        except ValueError as e:
            raise McpError(ErrorData(code=INVALID_PARAMS, message=str(e)))

        # ⚠️ APOLLO N'A TROUVÉ PERSONNE ≠ APOLLO A ACCEPTÉ LE REVEAL. Le client
        # oto-core traduit le 404 en `None` (et un corps peut revenir sans
        # `person`) : sans cette branche, les deux cas tombaient dans le même
        # « accepté, mais sans request_id » — un mensonge dans le sens le plus
        # cher, celui qui RASSURE. L'agent attendrait un POST qui ne partira
        # jamais, rendrait le lead pour traité, et ne réessaierait pas avec un
        # identifiant plus fort. Même règle que partout ici : introuvable se dit
        # « introuvable », jamais un repli silencieux.
        #
        # ⚠️ Et ce refus n'affirme RIEN sur le coût. Apollo facture l'APPEL, pas le
        # résultat : il facture la coquille vide qu'il fabrique lui-même (~12
        # crédits pour zéro donnée, oto-core `44acc08`), et rien n'a jamais montré
        # qu'un non-match soit remboursé. Annoncer « no credit spent » puis
        # « retry » présentait un SECOND appel payant comme offert — le texte servi
        # pilote l'agent, et celui-là le poussait à repayer en croyant rattraper
        # une erreur gratuite. Ici on ne mesure pas la facturation, donc on ne
        # promet pas : on dit que ce n'est pas gratuit et on chiffre le réessai.
        if not out.get("person"):
            return {"matched": False, "next_step": (
                "No Apollo match for these identifiers: no numbers will ever arrive "
                "— not on your webhook, not through apollo_reveal_phone_result. Do "
                "NOT read that as free: Apollo bills the CALL, not the result (it "
                "charges for the empty records it mints itself), and nothing has "
                "ever shown a non-match to be refunded. Trying again is a SECOND "
                "billed call — only worth it with a genuinely stronger identifier "
                "(person_id from apollo_search_people), never the same one again.")}

        # `request_id` est un entier signé 64 bits (~7,2e17) : il DÉPASSE la
        # précision d'un nombre JavaScript (2^53), et la réponse d'un tool
        # traverse du JSON jusqu'à des clients qui en sont faits. Le rendre en
        # nombre, c'est le rendre faux d'une unité ou deux sans que rien ne le
        # dise — et un id faux ne sonde rien. On le sert en CHAÎNE.
        rid = out.get("request_id")
        result = {k: v for k, v in out.items() if k != "request_id"}
        if rid is None:
            # Apollo a bien rendu une personne, mais pas d'id : le sondage est
            # alors impossible et les numéros n'arriveront QUE sur le webhook.
            # On le dit — un `next_step` qui promet un outil inutilisable est
            # pire que pas de `next_step` du tout.
            result["next_step"] = (
                "Apollo matched this person and accepted the reveal, but returned "
                "no request_id: the numbers will only reach your webhook_url. "
                "Nothing to poll.")
            return result if full else _light_reveal(result)
        result["request_id"] = str(rid)
        result["next_step"] = (
            f"Reveal ordered. Call apollo_reveal_phone_result('{rid}') in ~1-2min "
            "(0 Apollo credits per check, result kept 30 days).")
        return result if full else _light_reveal(result)

    @mcp.tool()
    def apollo_reveal_phone_result(request_id: str, full: bool = False) -> dict:
        """Collect the numbers ordered with apollo_reveal_phone. 0 Apollo credits.

        `done: false` carries `retry_after_seconds` — wait that long, call again.
        When done, the numbers are at `result.webhook_result.people[].phone_numbers[]`
        (nested: `result` is Apollo's envelope, and `result.failure_reason` says why
        if it never delivered). Each number carries `sanitized_number`, `type_cd`
        ("mobile"/"work_direct") and `dnc_status_cd` (do-not-call: read it before
        dialling). Apollo keeps a result 30 DAYS, then it is gone. Your own Apollo
        key only.

        Args:
            request_id: the id from apollo_reveal_phone, AS A STRING — a signed
                64-bit integer, too large for a JSON number to carry exactly.
            full: keep, on each `people[]` record, the employer's tech stack, the
                employment history and the Apollo CRM account record. Off by
                default (same cut as apollo_reveal_phone): ~15 000 characters per
                person otherwise, and a batch of 50 fits in no context. Numbers
                and person identity are kept either way. Still 0 credits.
        """
        client = _client_byo(_BYO_REVEAL_TELEPHONE)
        try:
            out = client.poll_webhook_result(request_id)
        except ValueError as e:
            raise McpError(ErrorData(code=INVALID_PARAMS, message=str(e)))
        if not out.get("done"):
            wait = out.get("retry_after_seconds")
            return {
                "done": False,
                "retry_after_seconds": wait,
                "next_step": ("Still verifying — call apollo_reveal_phone_result "
                              f"again in ~{wait or 10}s."),
            }
        # ⚠️ Apollo RÉ-ÉCHOTE l'identifiant dans son enveloppe, en NOMBRE — et il
        # arrive donc abîmé, comme partout ailleurs. Mesuré sur un sondage réel en
        # production le 2026-09-12 : sondé avec `-8351464734221602674`, l'enveloppe
        # rendait `-8351464734221603000` — 326 d'écart, la signature du float64.
        # `_stringify_request_id` ne couvrait que le PREMIER niveau des réponses de
        # `match`/`reveal` ; l'écho niché du sondage lui échappait. Un agent qui
        # relit `result.request_id` (pour re-sonder plus tard, ou pour le ranger
        # dans une ligne de tableau) range un identifiant qui ne sonde rien.
        # Troisième fois que le même piège se présente à un niveau différent : il
        # se ferme là où la valeur SORT, pas là où on l'a vue la dernière fois.
        result = _stringify_request_id(out.get("result") or {})
        if full:
            return {"done": True, "result": result}
        result, bloc = _light_reveal_result(result)
        return ({"done": True, "result": result, "projection": bloc} if bloc
                else {"done": True, "result": result})

    _BYO_REVEAL_LOT = (
        "un lot qui RÉVÈLE (emails personnels ou téléphones) ne passe jamais par "
        "la clé plateforme : Apollo facture ces reveals en plus du match, et par "
        "PERSONNE — pose ta propre clé Apollo. Sans elle, le lot marche toujours, "
        "il rend simplement les fiches sans ces reveals.")

    @mcp.tool()
    def apollo_bulk_match(
        people: list[dict],
        reveal_personal_emails: bool = False,
        reveal_phone_number: bool = False,
        webhook_url: Optional[str] = None,
        full: bool = False,
    ) -> dict:
        """Match UP TO 10 people in one call — the way a list actually gets built.

        apollo_search_people returns hundreds of people with obfuscated last names
        and no email. This is how you resolve them: 10 per call instead of one, so
        300 people cost 30 calls, not 300.

        ⚠️ A LOT DOES NOT SAVE CREDITS — Apollo bills PER PERSON, exactly as if you
        had called apollo_match_person ten times. What it saves is calls, and the
        rate limit that comes with them. Ask only for the reveals you need.

        `matches` comes back one entry PER PERSON, in the order you sent them, with
        `null` where nothing matched. An entry carrying `_stub: true` is an empty
        record Apollo minted and CHARGED for — count it as a failure, not as data.

        Each match drops `employment_history`, the Apollo CRM `account` record and
        the employer's heaviest blocks by default — a lot of 10 overflowed MCP output
        limits. `projection` names them; `full=True` returns everything.

        ⚠️ Phone numbers are not in this response: with `reveal_phone_number` Apollo
        POSTs them to `webhook_url` minutes later and hands back a `request_id` —
        pass it to apollo_reveal_phone_result. Either reveal needs your own Apollo
        key; a plain match works on the shared one.

        Args:
            people: 1-10 entries. Each takes the same identifiers as
                apollo_match_person — `id` (surest, from apollo_search_people),
                `email`, `linkedin_url`, or a FULL name (`first_name` +
                `last_name`) with `domain`/`organization_name`. A weak entry is
                refused by INDEX before the whole lot is billed.
            reveal_personal_emails: also return PERSONAL emails (your own key).
            reveal_phone_number: order phone numbers (your own key). Needs
                `webhook_url`; the numbers arrive there, not here.
            webhook_url: HTTPS endpoint Apollo POSTs the numbers to. oto is not a
                webhook receiver — a URL YOU control. You need not read it,
                apollo_reveal_phone_result returns the same payload.
            full: return every match untouched (employment history, CRM account,
                employer tech stack). Same price — this is about size.
        """
        revele = bool(reveal_personal_emails) or bool(reveal_phone_number)
        if revele:
            client = _client_byo(_BYO_REVEAL_LOT)
            is_platform = False
        else:
            client, is_platform = _client(units=len(people))
        destination = _webhook_destination(webhook_url) if webhook_url else None
        try:
            out = client.bulk_match_people(
                people,
                reveal_personal_emails=reveal_personal_emails or None,
                reveal_phone_number=reveal_phone_number or None,
                webhook_url=destination) or {}
        except ValueError as e:
            raise McpError(ErrorData(code=INVALID_PARAMS, message=str(e)))

        # Le compteur plateforme débite ce qu'APOLLO a facturé : sa réponse porte
        # `credits_consumed` (0 si rien de facturable n'a été trouvé, pas de crédit
        # pour une personne sans correspondance). Jamais 1 pour l'appel, jamais
        # `len(people)` quand l'amont dit son chiffre (oto#168). Repli sur
        # `len(people)` seulement si la réponse ne le porte pas : l'amont est alors
        # muet et le plus sûr est de compter ce qu'on a soumis. 0 ne débite rien
        # (`record_platform_usage` plancherait à 1).
        if is_platform:
            credits = out.get("credits_consumed")
            if not isinstance(credits, int) or isinstance(credits, bool):
                credits = len(people)
            if credits > 0:
                access.record_platform_usage("apollo", credits)
            quota = access.platform_quota_hint("apollo")
            if quota is not None:
                out = {**out, "platform_quota": quota}
        # La ligne FACTURÉE (`tool_calls.quantity`, lue par la lentille d'usage et par
        # le facturier d'un partenaire) est un AUTRE compteur que le quota ci-dessus.
        # Inconditionnelle, clé commune OU propre, comme `fullenrich` : c'est
        # `key_mode`, posé par le résolveur, qui dit s'il y a quelque chose à facturer.
        # Sans elle `quantity` reste NULL, que le consommateur lit 1 : un lot de 10 se
        # facturait 1. Compte les personnes SOUMISES, comme le débit de quota.
        session_org.note_call_trace(quantity=len(people))

        out = _stringify_request_id(out)
        matches = out.get("matches")
        if not full and isinstance(matches, list):
            allegees = [_light_match(m) for m in matches]
            if any(flag for _, flag in allegees):
                out = {**out, "matches": [m for m, _ in allegees],
                       "projection": _projection_bloc(lot=True)}
        if reveal_phone_number:
            rid = out.get("request_id")
            out["next_step"] = (
                f"Phone reveal ordered for {len(people)} people. Call "
                f"apollo_reveal_phone_result('{rid}') in ~1-2min."
                if rid else
                "Apollo accepted the reveal but returned no request_id: the numbers "
                "will only reach your webhook_url. Nothing to poll.")
        return out

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
