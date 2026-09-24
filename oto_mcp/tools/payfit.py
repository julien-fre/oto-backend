"""PayFit — logiciel de paie et RH. **Tout ce que l'API Partner documente en
lecture**. Aucune écriture n'est câblée (décision du 24/09/2026).

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
**Aucune écriture** : créer un collaborateur, un contrat, une absence, l'annuler,
affilier un contrat à une mutuelle ou demander une régularisation rendent le refus
nommé `payfit_write_not_wired`, qui dit ce que l'appel aurait fait — rien n'est
envoyé à PayFit, quel que soit l'argument. Ces ops restent dans leur enum pour que
l'agent reçoive ce refus nommé et non « op inconnu ».

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
- `payfit_collaborator` — list | get ; create non câblé.
- `payfit_contract` — list | get ; `fr=True` → variante FR ; create non câblé.
- `payfit_absence` — list ; create et cancel non câblés.

Modules frères (même clé, même client, montés par `Connector.modules`) :
`payfit_paie` (bulletins, comptabilité et virements, état du cycle, temps de
travail, titres-restaurant), `payfit_social` (mutuelle, prévoyance, documents).

**Aucun argument n'est retenu au silence** (`is not None`) → `payfit_garde`.
**Aucune écriture n'est câblée** : `payfit_garde.not_wired`, sans clé ni client.

Dérivé de la documentation et de la spec OpenAPI publiques (lues le 2026-09-17).
**Aucun appel réel** : pas de clé disponible — ni la forme exacte des réponses, ni
les effets de bord d'une écriture (PayFit notifie-t-il le salarié ? une absence
créée est-elle immédiatement en paie ?) ne sont vérifiés.
"""
from __future__ import annotations

from typing import Literal, Optional

from fastmcp import FastMCP

from . import payfit_socle as S
from .payfit_garde import (_client, limit_or_default, need, not_wired,
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
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
        fields: Optional[list] = None,
    ) -> dict:
        """The PayFit employee directory — read only: this connector does not write
        to PayFit.

        A collaborator carries what the API key's scopes allow: names, matricule,
        professional and personal emails, phones, addresses, manager, team,
        contracts (id, dates, status), birth date and country, nationality, gender,
        termination date, and — masked by the server default — the social security
        number and the bank details.

        `op`:
        - **"list"** (default): the collaborators, optionally the one whose contract
          carries `email`. Paginated: pass back `next_cursor` as `cursor`.
        - **"get"**: one collaborator (`collaborator_id`).
        - **"create"**: NOT WIRED — never writes. It answers the named refusal
          `payfit_write_not_wired`, saying what it would have done; nothing is sent
          to PayFit. Hiring is done in PayFit itself.

        Args:
            op: list (default) | get | create.
            collaborator_id: op="get".
            email: op="list" — exact contract email (not the login email).
            first_name / last_name / personal_email / other_name /
                social_security_number / personal_address / birth_information /
                personal_phone_number / number_of_children / gender /
                invite_collaborator: op="create" only — not wired, nothing is sent.
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
            refuse_ignored(op, collaborator_id=collaborator_id, **creation)
            c = _client()
            return S.page(run(lambda: c.list_collaborators(
                limit=limit_or_default(limit), cursor=cursor, email=email)),
                "collaborators", "id", fields=fields, redaction=S.REDACTION)
        if op == "get":
            need(op, collaborator_id=collaborator_id)
            refuse_ignored(op, email=email, limit=limit, cursor=cursor, fields=fields,
                           **creation)
            c = _client()
            return S.one(run(lambda: c.get_collaborator(collaborator_id)),
                         "collaborator", redaction=S.REDACTION)
        if op == "create":
            # Ni le NIR ni l'adresse ne sont repris : ils finiraient au journal.
            autres = sorted(k for k, v in creation.items()
                            if v is not None and k not in ("first_name", "last_name"))
            raise not_wired(op, "créé le collaborateur", first_name=first_name,
                            last_name=last_name, champs_fournis=autres)
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
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
        fields: Optional[list] = None,
    ) -> dict:
        """Employment contracts in PayFit, read only: job title, status, start/end
        dates, probation end date, weekly hours, full-time equivalent, collaborator
        id.

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
        - **"create"**: NOT WIRED — never writes. It answers the named refusal
          `payfit_write_not_wired`, saying what it would have done; nothing is sent
          to PayFit.

        Args:
            op: list (default) | get | create.
            contract_id: op="get".
            collaborator_id / job_title / start_date: op="create" only — not wired,
                nothing is sent.
            fr: op="list"/"get" — French variant (default False).
            include_in_progress: op="list" — also contracts still being created.
            limit: op="list" — 1..50 (default 50).
            cursor: op="list" — `next_cursor` of the previous page.
            fields: op="list" — keep only these keys per row (`contractId` always
                kept); omitted or `["*"]` = the full view.
        """
        if op == "list":
            refuse_ignored(op, contract_id=contract_id, collaborator_id=collaborator_id,
                           job_title=job_title, start_date=start_date)
            c = _client()
            return S.page(run(lambda: c.list_contracts(
                limit=limit_or_default(limit), cursor=cursor,
                include_in_progress=include_in_progress, fr=bool(fr))),
                "contracts", "contractId", fields=fields, redaction=S.REDACTION)
        if op == "get":
            need(op, contract_id=contract_id)
            refuse_ignored(op, collaborator_id=collaborator_id, job_title=job_title,
                           start_date=start_date, include_in_progress=include_in_progress,
                           limit=limit, cursor=cursor, fields=fields)
            c = _client()
            return S.one(run(lambda: c.get_contract(contract_id, fr=bool(fr))),
                         "contract", redaction=S.REDACTION)
        if op == "create":
            raise not_wired(op, "créé un contrat de travail (mise en paie)",
                            collaborator_id=collaborator_id, job_title=job_title,
                            start_date=start_date)
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
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
        fields: Optional[list] = None,
    ) -> dict:
        """Absences in PayFit, read only: contract id, start and end (date + moment of
        day), status, and the type.

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
        - **"create"** and **"cancel"**: NOT WIRED — never write. They answer the
          named refusal `payfit_write_not_wired`, saying what they would have done;
          nothing is sent to PayFit.

        Args:
            op: list (default) | create | cancel.
            contract_id: op="list" — filter.
            begin_date / end_date: op="list" — YYYY-MM-DD, absences overlapping the
                window.
            absence_id / absence_type / start_moment / end_moment / comment:
                op="create"/"cancel" only — not wired, nothing is sent.
            status: op="list" — approved (PayFit's default) | pending_approval |
                declined | cancelled | pending_cancellation | all.
            limit: op="list" — 1..50 (default 50).
            cursor: op="list" — `next_cursor` of the previous page.
            fields: op="list" — keep only these keys per row (`id` always kept);
                omitted or `["*"]` = the full view.
        """
        if op == "list":
            refuse_ignored(op, absence_id=absence_id, absence_type=absence_type,
                           start_moment=start_moment, end_moment=end_moment,
                           comment=comment)
            c = _client()
            return S.page(run(lambda: c.list_absences(
                limit=limit_or_default(limit), cursor=cursor, contract_id=contract_id,
                status=status, begin_date=begin_date, end_date=end_date)),
                "absences", "id", fields=fields, shape=S.absence,
                redaction=S.REDACTION)
        if op == "create":
            # Le motif (`absence_type`) n'est pas repris : donnée de santé, masquée
            # par défaut, qui finirait au journal.
            raise not_wired(op, "enregistré une absence validée (partie en paie)",
                            contract_id=contract_id, begin_date=begin_date,
                            end_date=end_date, start_moment=start_moment,
                            end_moment=end_moment)
        if op == "cancel":
            raise not_wired(op, "annulé l'absence", absence_id=absence_id)
        raise refuse_unknown_op(op, "list", "create", "cancel")
