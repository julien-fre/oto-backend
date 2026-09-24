"""FullEnrich — waterfall multi-provider contact enrichment (phones + emails).

~70% phone hit rate. Async bulk API (POST → poll). Pay-per-result.

⚠️ Surface MCP async assumée (signal #252) : l'ex-tool synchrone pollait
in-process 131-147s → tout client MCP raccroche (~60s), résultat perdu ET crédits
consommés. Désormais : `fullenrich_enrich_linkedin` SOUMET le job (~1s, bulk
jusqu'à 100 contacts) et `fullenrich_result` relève le statut/le résultat —
le polling appartient à l'agent.

Métrage (facturation du partenaire) : des faits distincts, aucun prix. La soumission trace les
contacts SOUMIS. Le relevé d'un job terminé trace :
- en `quantity`, les crédits que FullEnrich a DÉDUITS (`cost.credits`, rendu par
  oto-core en `cost_credits`) — le chiffre de rapprochement avec l'amont ;
- `found_work_emails` / `found_personal_emails` / `found_phones`, le nombre de
  CONTACTS du job où au moins une valeur de chaque sorte a été trouvée — ce que lit un
  prix par résultat qui n'est pas un multiple du barème de l'amont.
Un relevé non terminé trace 0 et aucun compte. Le backend ne porte aucun barème et ne
déduplique rien : un job relevé deux fois trace deux fois ses chiffres, et le
consommateur le compte une fois par son `enrichment_id` (le `job_id` de la lentille
`org.usage.calls`, qui rend aussi les comptes en `found`).
"""
from __future__ import annotations

import datetime as _dt
from typing import Optional

from fastmcp import FastMCP
from ..mcp_errors import McpError
from mcp.types import ErrorData, INVALID_PARAMS

from .. import access, session_org
from ..connectors import verify as connector_verify

_CREDITS_URL = "https://app.fullenrich.com/api/v1/account/credits"

# Relever un job ne doit jamais devenir une boucle sans fin côté agent (signaux
# #990, #1027-#1029). Un statut encore en cours dit QUAND repasser ; un statut qui ne
# changera plus est un refus NOMMÉ ; passé ce plafond depuis la soumission, le relevé
# dit d'arrêter (un job de 100 contacts finit d'ordinaire en moins de 4 minutes).
_REPASSER_S = {"CREATED": 30, "IN_PROGRESS": 30, "RATE_LIMIT": 60}
_PLAFOND_MIN = 20
_TERMINAUX = {
    "CANCELED": ("fullenrich_job_canceled",
                 "le job a été annulé chez FullEnrich : il ne rendra aucun résultat."),
    "NOT_FOUND": ("fullenrich_job_not_found",
                  "FullEnrich ne connaît pas (ou plus) ce job — id erroné ou job expiré."),
}


def _refus(code: str, message: str, **data) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=f"Refus `{code}` : {message}",
                              data={"code": code, "retryable": False, **data}))


def _minutes_depuis(submitted_at: Optional[str]) -> Optional[float]:
    if submitted_at is None:
        return None
    try:
        t = _dt.datetime.fromisoformat(submitted_at)
    except ValueError:
        raise McpError(ErrorData(
            code=INVALID_PARAMS,
            message=("`submitted_at` illisible : repasse tel quel le `submitted_at` "
                     "rendu par fullenrich_enrich_linkedin (ISO 8601 avec fuseau).")))
    if t.tzinfo is None:
        t = t.replace(tzinfo=_dt.timezone.utc)
    return (_dt.datetime.now(_dt.timezone.utc) - t).total_seconds() / 60


def _verify(fields: dict, config: dict | None = None) -> dict:
    """Sonde « tester la connexion » — otomata-tech/oto#69. Couvre `auth+quota`.

    `GET /api/v1/account/credits` (⚠️ v1, PAS le v2 du reste du client — deux
    préfixes de version distincts chez FullEnrich, vérifié). Bearer token,
    lecture de solde sans effet de bord. Aucune mention explicite de
    « gratuit » dans ce qu'on a trouvé — absence de mention, indice, pas une
    preuve, comme Folk et Pennylane.

    Le solde (`balance`) distingue une clé morte d'un compte à sec — recharger
    n'est pas reconnecter.
    """
    import requests
    from oto.tools.fullenrich.client import FullenrichClient

    headers = FullenrichClient(api_key=fields["key"])._headers()
    r = requests.get(_CREDITS_URL, headers=headers, timeout=15)
    r.raise_for_status()
    infos = r.json() or {}
    restant = infos.get("balance")
    if not isinstance(restant, int):
        raise RuntimeError(
            "FullEnrich a répondu sans solde de crédits lisible : "
            f"{str(infos)[:200]}")
    if restant <= 0:
        raise connector_verify.QuotaEpuise(
            "La clé FullEnrich est bonne, mais le compte est à sec (0 crédit "
            "restant). Recharge le compte chez FullEnrich — reconnecter n'y "
            "changerait rien.")
    return {"quota": {"restant": restant, "unite": "crédits"}}


def register(mcp: FastMCP) -> None:
    from oto.tools.fullenrich.client import FullenrichClient

    connector_verify.register("fullenrich", _verify, couvre=connector_verify.AUTH_QUOTA)

    def _client(units: int = 1) -> tuple[FullenrichClient, bool]:
        key, is_platform = access.resolve_api_key("fullenrich", units=units)
        return FullenrichClient(api_key=key), is_platform

    @mcp.tool()
    def fullenrich_enrich_linkedin(
        contacts: list[dict],
        enrich_fields: Optional[list[str]] = None,
    ) -> dict:
        """Submit an ASYNC enrichment job (phones + emails) via FullEnrich (waterfall 20+ providers).

        Returns immediately with an `enrichment_id` — the job runs server-side for
        ~30s to 4min. THEN call `fullenrich_result(enrichment_id)` to collect (first
        poll after ~30s, then every ~20-30s until status FINISHED).

        Args:
            contacts: 1-100 contacts in ONE job (batch friends — one job for a whole
                list beats parallel single calls). Each: {"first_name": str,
                "last_name": str, "linkedin_slug": str (e.g. "alexis-laporte",
                NOT a URL — best matching), "domain": str (company website
                domain), "company_name": str (optional)}. Each contact MUST
                carry linkedin_slug OR domain (FullEnrich rejects the job
                otherwise).
            enrich_fields: subset of ["contact.work_emails", "contact.phones",
                "contact.personal_emails"]. Default: work_emails + phones.
                Only ask what you need — pricing is pay-per-result:
                10 credits/phone, 1/work_email, 3/personal_email.
        """
        client, is_platform = _client(units=len(contacts))
        try:
            enrichment_id = client.submit(contacts, enrich_fields=enrich_fields)
        except ValueError as e:
            raise McpError(ErrorData(code=INVALID_PARAMS, message=str(e)))
        if is_platform:
            # Un job = un contact facturé par contact : la consommation est le
            # NOMBRE de contacts, comptée en un seul geste (l'ancienne boucle faisait
            # une requête par contact — jusqu'à 100 par job).
            # `len(contacts)` et non le coût réel : FullEnrich facture à la donnée
            # trouvée et ne dit rien à la soumission (le coût n'existe qu'au résultat
            # du job, `fullenrich_result`, sur un autre appel) — on ne devine pas.
            access.record_platform_usage("fullenrich", len(contacts))
        # Métrage par unité (facturation du partenaire, 21/08) — INCONDITIONNEL (platform key
        # OU BYO), contrairement à `record_platform_usage` ci-dessus (qui ne compte
        # que le quota interne oto sur la clé plateforme) : `tool_calls.quantity`
        # sert un consommateur EXTERNE (celui du partenaire) qui facture l'org quel que
        # soit le mode de clé. Compte les contacts SOUMIS, pas ceux effectivement
        # enrichis/trouvés — ce dernier chiffre n'existe qu'après coup, dans
        # `fullenrich_result` (job async), une ligne de journal SÉPARÉE.
        session_org.note_call_trace(quantity=len(contacts))
        return {
            "enrichment_id": enrichment_id,
            "submitted": len(contacts),
            "submitted_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
            "next_step": ("Job accepted. Call fullenrich_result(enrichment_id, "
                          "submitted_at) in ~30s (typical completion 30s-4min)."),
        }

    @mcp.tool()
    def fullenrich_result(enrichment_id: str, submitted_at: Optional[str] = None) -> dict:
        """Collect the result of a FullEnrich job submitted with fullenrich_enrich_linkedin.

        Single status check, returns immediately. Returns `{done: false, status,
        retry_after_s, next_step}` while the job runs — call again after
        `retry_after_s` seconds (jobs typically finish in 30s-4min). Pass the
        `submitted_at` the submission returned: past 20 minutes the answer adds
        `verdict: "still_running_after_20_min"` and says to STOP polling — do not
        resubmit the same contacts, it would be billed twice. A job that will never
        finish (canceled, unknown or expired id) is a named refusal, not a status to
        poll. When done, returns `{done: true, status: "FINISHED", profiles}` with one
        entry per submitted contact: {found, linkedin_slug, full_name, title,
        company_name, phones[], work_emails[], personal_emails[], location}.
        Reading a result never consumes the platform quota (the submission does).

        Args:
            enrichment_id: the `enrichment_id` returned by fullenrich_enrich_linkedin.
            submitted_at: the `submitted_at` returned by the same call.
        """
        from oto.tools.fullenrich.client import FullenrichClient
        # Le quota plateforme est débité à la SOUMISSION : le relevé ne consomme rien
        # et ne le vérifie pas — sinon un job déjà payé devenait illisible le jour même
        # que le quota s'épuisait (#943).
        rc = access.resolve_credential("fullenrich", check_usage=False)
        try:
            res = FullenrichClient(api_key=rc.key).fetch(enrichment_id)
        except RuntimeError as e:
            raise McpError(ErrorData(
                code=INVALID_PARAMS,
                message=f"Refus `fullenrich_upstream_error` : {e}",
                data={"code": "fullenrich_upstream_error", "retryable": True}))
        status = res["status"]
        if status != "FINISHED":
            # Un relevé de statut n'a rien consommé chez FullEnrich : un zéro TRACÉ,
            # pas une absence — un consommateur du métrage lit l'absence comme 1.
            session_org.note_call_trace(quantity=0)
            if status not in _REPASSER_S:
                code, pourquoi = _TERMINAUX.get(status, (
                    "fullenrich_job_status_unknown",
                    f"FullEnrich rend le statut « {status} », qui ne mène pas à un résultat."))
                raise _refus(code, f"{pourquoi} Ne relève plus ce job ; resoumets les "
                                   "contacts seulement si tu en as encore besoin (nouvelle "
                                   "facturation).", status=status, enrichment_id=enrichment_id)
            out = {"done": False, "status": status,
                   "retry_after_s": _REPASSER_S[status],
                   "next_step": (f"Still running — call fullenrich_result again in "
                                 f"~{_REPASSER_S[status]}s.")}
            ecoule = _minutes_depuis(submitted_at)
            if ecoule is not None and ecoule >= _PLAFOND_MIN:
                out["verdict"] = f"still_running_after_{_PLAFOND_MIN}_min"
                out["next_step"] = (
                    f"Still running after {int(ecoule)} minutes: stop polling now. "
                    "Report it as blocked (with the enrichment_id) and read the result "
                    "later with the same id — do not resubmit the same contacts, they "
                    "would be billed twice.")
            return out
        # Métrage (facturation du partenaire), INCONDITIONNEL (clé plateforme OU BYO), comme
        # `fullenrich_enrich_linkedin` : le consommateur filtre sur `key_mode`.
        #   • `quantity` = les crédits que FULLENRICH a déduits pour ce job —
        #     `cost_credits`, son `cost.credits` relu par oto-core : le chiffre de
        #     RAPPROCHEMENT. Coût absent (oto-core antérieur au champ, amont muet) →
        #     AUCUNE quantité, jamais une valeur devinée depuis les profils.
        #   • `found_*` = combien de CONTACTS du job ont au moins une valeur de chaque
        #     sorte (un contact à deux e-mails compte UNE fois, un contact vide nulle
        #     part). Des faits lus dans les profils — donc tracés même sans coût
        #     déclaré. C'est ce que lit un prix par résultat qui n'est pas un multiple
        #     du barème de l'amont.
        # Aucun barème ici, ni 1/3/10 ni conversion : un taux est une décision
        # commerciale. ⚠️ Chaque relevé FINISHED d'un même job trace les mêmes
        # chiffres : compter un job une fois (par son `enrichment_id`, que la lentille
        # de facturation rend en `job_id`) appartient au consommateur, pas au backend.
        profiles = res.get("profiles") or []
        trace = {
            "found_work_emails": sum(1 for p in profiles if getattr(p, "work_emails", None)),
            "found_personal_emails": sum(1 for p in profiles
                                         if getattr(p, "personal_emails", None)),
            "found_phones": sum(1 for p in profiles if getattr(p, "phones", None)),
        }
        cost = res.get("cost_credits")
        if isinstance(cost, int) and not isinstance(cost, bool) and cost >= 0:
            trace["quantity"] = cost
        session_org.note_call_trace(**trace)
        return {
            "done": True,
            "status": "FINISHED",
            "profiles": [p.to_dict() for p in res["profiles"]],
        }
