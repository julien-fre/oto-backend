"""PayFit — logiciel de paie et RH. **Tout ce que l'API Partner documente**, en
lecture et en écriture.

Wrappe `oto.tools.payfit.PayfitClient` (Bearer, « Partner API » v1). keyed
`api_key`, BYO (membre ou org) : une clé API PayFit est créée par un admin de
l'entreprise et ne donne accès qu'à cette entreprise — il n'y a pas de clé
plateforme. L'id d'entreprise n'est jamais demandé : le client le lit par
introspection de la clé. Hôtes fixes (`partner-api.payfit.com`,
`oauth.payfit.com`) : aucun champ ne désigne une destination, donc pas de garde
d'egress (`oto_mcp/egress.py`) à poser ici.

## Ce que ce connecteur sert, depuis le 17/09/2026

Tout : l'entreprise, l'annuaire, les contrats (dont la variante FR : nature DSN,
convention collective, forfait jours, motif de rupture, statut cadre dirigeant), les
absences, les bulletins (métadonnées et PDF), les écritures comptables de paie et
leur export, le fichier de virement, l'état du cycle de paie, le temps de travail
réalisé, les titres-restaurant, la mutuelle et la prévoyance, les documents. Et
l'écriture : créer un collaborateur, un contrat, une absence, l'annuler, affilier un
contrat à une mutuelle ou une prévoyance, demander une régularisation.

Il n'y avait pas de choix à faire entre « servir la paie » et « protéger les
personnes » : ce sont deux mécanismes différents.

⚠️ **La protection ne passe plus par un retrait en dur.** Elle passe par les
**filtres de champs par org** (ADR 0009/0015) et un **défaut serveur protecteur**
pour ce connecteur (`field_filter_defaults.SERVER_DEFAULTS["payfit"]`) : NIR (et
NTT), IBAN/BIC et `absence_type` sont masqués tant qu'un org_admin ne lève pas la
règle. Tout le reste sort : rémunérations portées par les écritures comptables,
bulletins, coût employeur, contrats complets, temps de travail, mutuelle,
titres-restaurant, coordonnées, naissance, nationalité, ancienneté, manager.

⚠️ **Les DOCUMENTS (bulletin PDF, export comptable, fichier de virement, document
fiscal) sont verrouillés** : un filtre ne lit pas l'intérieur d'un fichier, donc ils
ne sortent que si la politique effective de l'org pour `payfit` ne masque rien —
sinon refus nommé, et fail-closed si elle est illisible (`payfit_garde.serve_document`).

⚠️ **Une seule clé est renommée, et c'est mécanique** : le type d'une absence sort
sous `absence_type`, parce que `FieldFilter` matche par nom de feuille et qu'une
règle sur `type` toucherait aussi `emails[].type` et quatre autres champs anodins.
`absence_category` (`ordinary_leave` | `restricted`) l'accompagne et reste lisible
quand le type est masqué. Le pourquoi complet : `payfit_socle`.

## Surface (ADR 0047), verbe en `op`, défaut toujours en lecture

Ce module :
- `payfit_company` — l'entreprise de la clé ; `fr=True` ajoute SIREN/SIRET.
- `payfit_collaborator` — list | get | create.
- `payfit_contract` — list | get | create ; `fr=True` → variante FR.
- `payfit_absence` — list | create | cancel.

Modules frères (même clé, même client, montés par `Connector.modules`) :
`payfit_paie` (bulletins, comptabilité et virements, état du cycle, temps de
travail, titres-restaurant), `payfit_social` (mutuelle, prévoyance, documents).

**Aucun argument n'est retenu au silence** (`is not None`) → `payfit_garde`.
**Toute écriture est en `dry_run=True` par défaut** : l'appel rend ce qu'il ferait
et n'écrit rien tant que `dry_run=False` n'est pas passé délibérément.

Dérivé de la documentation et de la spec OpenAPI publiques (lues le 2026-09-17).
**Aucun appel réel** : pas de clé disponible — ni la forme exacte des réponses, ni
les effets de bord d'une écriture (PayFit notifie-t-il le salarié ? une absence
créée est-elle immédiatement en paie ?) ne sont vérifiés.
"""
from __future__ import annotations

from typing import Literal, Optional

from fastmcp import FastMCP

from . import payfit_socle as S
from .payfit_garde import (_client, is_dry, limit_or_default, need, preview,
                           refuse_ignored, refuse_unknown_op, register_probe, run)


def register(mcp: FastMCP) -> None:
    register_probe()

    @mcp.tool()
    def payfit_company(fr: Optional[bool] = None) -> dict:
        """The PayFit company the API key belongs to — name, country, registration
        number, address, number of active contracts.

        Its `country` tells whether the French variants apply: `payfit_contract(
        fr=True)`, the meal vouchers, the worked time and the insurance tools are
        French-only.

        Args:
            fr: French variant — adds `siren`, `siret`, the legal address and the
                health-insurance proration method. France only.
        """
        c = _client()
        return S.one(run(lambda: c.get_company(fr=bool(fr))), "company")

    @mcp.tool()
    def payfit_collaborator(
        op: Literal["list", "get", "create"] = "list",
        collaborator_id: Optional[str] = None,
        email: Optional[str] = None,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
        personal_email: Optional[str] = None,
        other_name: Optional[str] = None,
        social_security_number: Optional[str] = None,
        personal_address: Optional[dict] = None,
        birth_information: Optional[dict] = None,
        personal_phone_number: Optional[str] = None,
        number_of_children: Optional[int] = None,
        gender: Optional[str] = None,
        invite_collaborator: Optional[bool] = None,
        dry_run: Optional[bool] = None,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
        fields: Optional[list] = None,
    ) -> dict:
        """The PayFit employee directory — read it, or hire into it.

        A collaborator carries what the API key's scopes allow: names, matricule,
        professional and personal emails, phones, addresses, manager, team,
        contracts (id, dates, status), birth date and country, nationality, gender,
        termination date, and — masked by the server default — the social security
        number and the bank details.

        `op`:
        - **"list"** (default): the collaborators, optionally the one whose contract
          carries `email`. Paginated: pass back `next_cursor` as `cursor`.
        - **"get"**: one collaborator (`collaborator_id`).
        - **"create"**: hires a person into PayFit. `first_name`, `last_name` and
          `personal_email` are required. ⚠️ Creates a REAL person in a payroll
          system, and `invite_collaborator=True` EMAILS them. A collaborator
          created here has no contract yet — `payfit_contract(op="create")` is what
          puts them on the payroll.

        ⚠️ `dry_run` DEFAULTS TO TRUE on create: the call returns what it would
        send and writes nothing. Pass `dry_run=False` deliberately to act.

        Args:
            op: list (default) | get | create.
            collaborator_id: op="get".
            email: op="list" — exact contract email (not the login email).
            first_name / last_name / personal_email: op="create", required.
            other_name: op="create" — nom d'usage (FR), middle name (UK).
            social_security_number: op="create" — length by country (FR 15, ES 14,
                GB 12).
            personal_address: op="create" — `{streetNumber, addressFirstLine,
                addressSecondLine, city, state, postCode, country}` (country as a
                2-letter ISO code).
            birth_information: op="create" — `{birthDate, birthPlace,
                birthCountry}`, birth date as YYYY-MM-DD in the past.
            personal_phone_number: op="create".
            number_of_children: op="create" — 0 to 20.
            gender: op="create" — `MALE` or `FEMALE` (PayFit's closed set).
            invite_collaborator: op="create" — sends the invitation email.
            dry_run: op="create" — default True.
            limit: op="list" — 1..50 (default 50).
            cursor: op="list" — `next_cursor` of the previous page.
            fields: op="list" — keep only these keys per row (`id` always kept);
                omitted or `["*"]` = the full view.
        """
        creation = dict(
            first_name=first_name, last_name=last_name, personal_email=personal_email,
            other_name=other_name, social_security_number=social_security_number,
            personal_address=personal_address, birth_information=birth_information,
            personal_phone_number=personal_phone_number,
            number_of_children=number_of_children, gender=gender,
            invite_collaborator=invite_collaborator)
        if op == "list":
            refuse_ignored(op, collaborator_id=collaborator_id, dry_run=dry_run,
                           **creation)
            c = _client()
            return S.page(run(lambda: c.list_collaborators(
                limit=limit_or_default(limit), cursor=cursor, email=email)),
                "collaborators", "id", fields=fields, redaction=S.REDACTION)
        if op == "get":
            need(op, collaborator_id=collaborator_id)
            refuse_ignored(op, email=email, limit=limit, cursor=cursor, fields=fields,
                           dry_run=dry_run, **creation)
            c = _client()
            return S.one(run(lambda: c.get_collaborator(collaborator_id)),
                         "collaborator", redaction=S.REDACTION)
        if op == "create":
            need(op, first_name=first_name, last_name=last_name,
                 personal_email=personal_email)
            refuse_ignored(op, collaborator_id=collaborator_id, email=email,
                           limit=limit, cursor=cursor, fields=fields)
            if is_dry(dry_run):
                return preview(op, collaborator={k: v for k, v in creation.items()
                                                 if v is not None})
            c = _client()
            return S.one(run(lambda: c.create_collaborator(**creation)), "created")
        raise refuse_unknown_op(op, "list", "get", "create")

    @mcp.tool()
    def payfit_contract(
        op: Literal["list", "get", "create"] = "list",
        contract_id: Optional[str] = None,
        collaborator_id: Optional[str] = None,
        job_title: Optional[str] = None,
        start_date: Optional[str] = None,
        fr: Optional[bool] = None,
        include_in_progress: Optional[bool] = None,
        dry_run: Optional[bool] = None,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
        fields: Optional[list] = None,
    ) -> dict:
        """Employment contracts in PayFit: job title, status, start/end dates,
        probation end date, weekly hours, full-time equivalent, collaborator id.

        `fr=True` (French companies only) reads a DIFFERENT collection, and it is
        the only one that carries the contract nature (`natureContratDsn`: 01 CDI,
        02 CDD…), the conventional status, the collective agreement (`idcc`), the
        termination reason (`motifRuptureDeContratDsn`), the working time modality
        (`standard`, `forfait_heures`, `forfait_jours`…), the "cadre dirigeant"
        flag, and the linked health-insurance and provident-fund contract ids.
        For a French company, read `fr=True`.

        ⚠️ `op="list"` returns active, pending and LAST YEAR's archived contracts —
        not the company's full history. And there is no amendment history anywhere
        in this API: a contract is served as it stands today.

        `op`:
        - **"list"** (default): paginated; pass back `next_cursor` as `cursor`.
        - **"get"**: one contract (`contract_id`).
        - **"create"**: creates a contract for an EXISTING collaborator
          (`collaborator_id`, `job_title`, `start_date`). ⚠️ This is what puts a
          real person on the payroll. France only.

        ⚠️ `dry_run` DEFAULTS TO TRUE on create.

        Args:
            op: list (default) | get | create.
            contract_id: op="get".
            collaborator_id: op="create" — the person the contract belongs to.
            job_title: op="create".
            start_date: op="create" — YYYY-MM-DD.
            fr: op="list"/"get" — French variant (default False).
            include_in_progress: op="list" — also contracts still being created.
            dry_run: op="create" — default True.
            limit: op="list" — 1..50 (default 50).
            cursor: op="list" — `next_cursor` of the previous page.
            fields: op="list" — keep only these keys per row (`contractId` always
                kept); omitted or `["*"]` = the full view.
        """
        if op == "list":
            refuse_ignored(op, contract_id=contract_id, collaborator_id=collaborator_id,
                           job_title=job_title, start_date=start_date, dry_run=dry_run)
            c = _client()
            return S.page(run(lambda: c.list_contracts(
                limit=limit_or_default(limit), cursor=cursor,
                include_in_progress=include_in_progress, fr=bool(fr))),
                "contracts", "contractId", fields=fields, redaction=S.REDACTION)
        if op == "get":
            need(op, contract_id=contract_id)
            refuse_ignored(op, collaborator_id=collaborator_id, job_title=job_title,
                           start_date=start_date, include_in_progress=include_in_progress,
                           limit=limit, cursor=cursor, fields=fields, dry_run=dry_run)
            c = _client()
            return S.one(run(lambda: c.get_contract(contract_id, fr=bool(fr))),
                         "contract", redaction=S.REDACTION)
        if op == "create":
            need(op, collaborator_id=collaborator_id, job_title=job_title,
                 start_date=start_date)
            refuse_ignored(op, contract_id=contract_id, fr=fr,
                           include_in_progress=include_in_progress, limit=limit,
                           cursor=cursor, fields=fields)
            if is_dry(dry_run):
                return preview(op, contract={"collaborator_id": collaborator_id,
                                             "job_title": job_title,
                                             "start_date": start_date})
            c = _client()
            return S.one(run(lambda: c.create_contract(
                collaborator_id, job_title=job_title, start_date=start_date)),
                "created")
        raise refuse_unknown_op(op, "list", "get", "create")

    @mcp.tool()
    def payfit_absence(
        op: Literal["list", "create", "cancel"] = "list",
        absence_id: Optional[str] = None,
        contract_id: Optional[str] = None,
        absence_type: Optional[str] = None,
        begin_date: Optional[str] = None,
        end_date: Optional[str] = None,
        start_moment: Optional[str] = None,
        end_moment: Optional[str] = None,
        comment: Optional[str] = None,
        status: Optional[list] = None,
        dry_run: Optional[bool] = None,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
        fields: Optional[list] = None,
    ) -> dict:
        """Absences in PayFit: contract id, start and end (date + moment of day),
        status, and the type.

        The type is served as **`absence_type`** (not `type`), alongside
        **`absence_category`**: `ordinary_leave` for paid leave, RTT, rest, unpaid
        leave, remote work or school; `restricted` for everything else — sick
        leave, work accident, maternity, bereavement, marriage… The server default
        MASKS `absence_type` (health data, GDPR art. 9) and leaves
        `absence_category` readable, so absence planning works without reading a
        medical reason. An org_admin can lift that mask for the whole connector.
        A `••••` is a masked value, not a missing one — do not infer the reason.

        ⚠️ This API has **no leave balance and no counter**: acquired or remaining
        paid leave, RTT left, seniority-based days are nowhere in it. Do not
        compute a balance from this list and present it as PayFit's — it is not.

        `op`:
        - **"list"** (default): paginated; pass back `next_cursor` as `cursor`.
          There is no single-absence read upstream.
        - **"create"**: records an absence that is **already approved** — there is
          no approval workflow in this API, so this goes straight into payroll.
        - **"cancel"**: cancels `absence_id`, optionally with a `comment`.

        ⚠️ `dry_run` DEFAULTS TO TRUE on create and cancel.

        Args:
            op: list (default) | create | cancel.
            absence_id: op="cancel".
            contract_id: op="list" (filter) / op="create" (required).
            absence_type: op="create" — PayFit's `CreateAbsenceType` value
                (`fr_conges_payes`, `fr_rtt`, `fr_sans_solde`,
                `fr_maladie_ordinaire`…). The creatable set is NOT the readable
                set: maternity and work accidents cannot be created here.
            begin_date / end_date: op="list" — YYYY-MM-DD, absences overlapping the
                window; op="create" — the absence's own start and end.
            start_moment / end_moment: op="create" — `beginning-of-day`,
                `middle-of-day` or `end-of-day` (defaults cover full days).
            comment: op="cancel" — recorded on the cancellation.
            status: op="list" — approved (PayFit's default) | pending_approval |
                declined | cancelled | pending_cancellation | all.
            dry_run: op="create"/"cancel" — default True.
            limit: op="list" — 1..50 (default 50).
            cursor: op="list" — `next_cursor` of the previous page.
            fields: op="list" — keep only these keys per row (`id` always kept);
                omitted or `["*"]` = the full view.
        """
        if op == "list":
            refuse_ignored(op, absence_id=absence_id, absence_type=absence_type,
                           start_moment=start_moment, end_moment=end_moment,
                           comment=comment, dry_run=dry_run)
            c = _client()
            return S.page(run(lambda: c.list_absences(
                limit=limit_or_default(limit), cursor=cursor, contract_id=contract_id,
                status=status, begin_date=begin_date, end_date=end_date)),
                "absences", "id", fields=fields, shape=S.absence,
                redaction=S.REDACTION)
        if op == "create":
            need(op, contract_id=contract_id, absence_type=absence_type,
                 begin_date=begin_date, end_date=end_date)
            refuse_ignored(op, absence_id=absence_id, comment=comment, status=status,
                           limit=limit, cursor=cursor, fields=fields)
            moments = {k: v for k, v in
                       (("start_moment", start_moment), ("end_moment", end_moment))
                       if v is not None}
            if is_dry(dry_run):
                return preview(op, absence={"contract_id": contract_id,
                                            "absence_type": absence_type,
                                            "begin_date": begin_date,
                                            "end_date": end_date, **moments})
            c = _client()
            return S.one(run(lambda: c.create_absence(
                contract_id=contract_id, absence_type=absence_type,
                start_date=begin_date, end_date=end_date, **moments)), "created")
        if op == "cancel":
            need(op, absence_id=absence_id)
            refuse_ignored(op, contract_id=contract_id, absence_type=absence_type,
                           begin_date=begin_date, end_date=end_date,
                           start_moment=start_moment, end_moment=end_moment,
                           status=status, limit=limit, cursor=cursor, fields=fields)
            if is_dry(dry_run):
                return preview(op, absence_id=absence_id, comment=comment)
            c = _client()
            run(lambda: c.cancel_absence(absence_id, comment=comment))
            return {"cancelled": True, "absence_id": absence_id}
        raise refuse_unknown_op(op, "list", "create", "cancel")
