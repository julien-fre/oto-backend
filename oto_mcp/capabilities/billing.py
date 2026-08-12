"""Capacités billing (ADR 0043, B2) — abonnement par org, REST-only.

Pas de face MCP par choix d'ADR : payer est un acte humain (dashboard), on ne
fait pas transiter d'URL de paiement dans un contexte LLM. Souscrire/confirmer/
résilier = org_admin ; consulter = tout membre de l'org active.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Optional

from pydantic import BaseModel, Field, field_validator

from .. import billing
from ..mollie_client import MollieError
from ._authz import ORG_ADMIN, ORG_MEMBER, SUB_ONLY, SUPER_ADMIN
from ._types import AuthzDenied, Capability, ResolvedCtx, RestBinding
from .registry import CAPABILITIES


class NoInput(BaseModel):
    pass


class SubscribeInput(BaseModel):
    plan: str
    return_url: str          # URL de retour du dashboard (page billing)
    method: str = "card"     # 'card' | 'sepa' (prélèvement) — restreint la page Mollie
    # Pas de champs IBAN/mobile : le flux Mollie unifié collecte le moyen de
    # paiement (carte ou IBAN + mandat) sur la page de checkout hébergée.


class PaymentsInput(BaseModel):
    limit: int = 20

    @field_validator("limit")
    @classmethod
    def _cap(cls, v):
        # Patron `SearchInput._cap` : la valeur part telle quelle en `LIMIT %s`. Sans
        # borne, un `limit` énorme sérialise tout l'historique de l'org et un NÉGATIF
        # fait échouer Postgres (« LIMIT must not be negative ») en 500 opaque.
        return max(1, min(int(v), 100))


class AdminPlanInput(BaseModel):
    org_id: int
    plan: str | None = None   # None / omis = retirer le plan comp forcé


# ── formes de réponse (ADR 0009 : `Output` DÉCRIT la 200, ne la valide pas) ───
# Le vocabulaire de la facturation est piégeux : un montant, une date de fin et un
# état d'abonnement ne veulent pas dire la même chose selon la branche. Les
# subtilités vivent dans les `description=` — c'est ce que lit l'intégrateur dans
# `/api/openapi.json`, et ce qui lui évite de les découvrir en production.

class Plan(BaseModel):
    """Un palier du catalogue. Le catalogue est un mapping en CODE (`billing.PLANS`),
    pas une table : il n'a pas d'id de base et peut changer entre deux déploiements."""
    plan: str = Field(description="Identifiant technique du palier ('standard', "
                                  "'premium', 'business', 'enterprise') — c'est CETTE "
                                  "valeur qu'attend billing.subscribe, pas le label.")
    label: str = Field(description="Libellé d'affichage ('Standard', 'Entreprise'…).")
    amount: int = Field(description="Prix HT mensuel en CENTIMES (1900 = 19,00 €). "
                                    "Jamais un décimal, jamais TTC.")
    currency: str = Field(description="Code devise ISO en minuscules ('eur').")
    interval: str = Field(description="Période facturée : 'month' | 'year'. "
                                      "L'échéance suivante est calendaire (31/01 + 1 "
                                      "mois → 28/02), jamais +30 jours.")
    unipile_accounts: Optional[int] = Field(
        default=None,
        description="Plafond de comptes de messagerie ouvert par le palier. "
                    "`null` = ILLIMITÉ (surtout pas « zéro compte ») — aujourd'hui "
                    "TOUS les paliers valent null : on ne facture plus au nombre de "
                    "comptes, les 4 paliers débloquent la même chose et ne diffèrent "
                    "que par le prix.")
    custom: bool = Field(description="Palier « sur devis ». billing.subscribe le REFUSE "
                                     "(400 `custom_plan`) : il s'ouvre par un admin "
                                     "plateforme en abonnement offert (comp).")


class PlansView(BaseModel):
    """Catalogue public des paliers — état de l'org NON inclus (c'est billing.status)."""
    plans: list[Plan]


class BillingStatus(BaseModel):
    """État d'abonnement de l'org active — servi aussi bien par billing.status que
    par billing.cancel (qui rend l'état APRÈS la demande de résiliation).

    ⚠️ DEUX formes selon qu'une ligne d'abonnement existe : sans abonnement, la
    réponse se réduit à `{subscribed: false, plans: […]}` et TOUS les autres champs
    sont absents (pas nuls) ; avec abonnement, `plans` disparaît. Tester `subscribed`,
    jamais la présence de `plan`."""
    subscribed: bool = Field(
        description="L'org a-t-elle un droit d'accès ouvert ? True pour un abonnement "
                    "`active` MAIS AUSSI `past_due` (impayé en cours de relance : "
                    "l'accès court encore). Donc subscribed=True n'implique ni « à "
                    "jour de paiement », ni « payé » (cf. `comp`).")
    plans: Optional[list[Plan]] = Field(
        default=None,
        description="Le catalogue, joint UNIQUEMENT quand l'org n'a aucun abonnement "
                    "— de quoi peindre la page de souscription sans second appel. "
                    "Absent dès qu'un abonnement existe : son absence n'est pas une "
                    "erreur.")
    plan: Optional[str] = Field(default=None, description="Palier souscrit (clé de "
                                                          "catalogue).")
    label: Optional[str] = Field(
        default=None,
        description="Libellé du palier, relu du CATALOGUE COURANT — donc `null` si le "
                    "palier stocké a disparu du code depuis la souscription (idem "
                    "amount/currency/interval).")
    amount: Optional[int] = Field(
        default=None,
        description="Prix courant du palier au catalogue, en CENTIMES. Ce n'est PAS un "
                    "montant facturé : un abonnement offert (comp) affiche le prix du "
                    "palier alors que rien n'a jamais été encaissé. Les montants "
                    "réellement passés au PSP se lisent sur billing.payments.")
    currency: Optional[str] = Field(default=None, description="Devise du palier ('eur').")
    interval: Optional[str] = Field(default=None, description="'month' | 'year'.")
    status: Optional[str] = Field(
        default=None,
        description="État du miroir local : 'incomplete' (souscription ouverte, jamais "
                    "de droit), 'active', 'past_due' (impayé, droit maintenu pendant la "
                    "relance), 'canceled' (fini). C'est LA source de vérité du cycle, "
                    "PSP-agnostique.")
    method: Optional[str] = Field(
        default=None,
        description="Moyen de paiement du mandat : 'card' | 'sepa' | 'comp' (aucun — "
                    "abonnement offert par un admin).")
    comp: bool = Field(
        default=False,
        description="Abonnement OFFERT, forcé par un admin plateforme : accès ouvert, "
                    "aucun PSP derrière, aucune échéance tirée, `amount` purement "
                    "indicatif. Un comp=True + subscribed=True ne signifie donc aucun "
                    "encaissement (billing.payments sera vide).")
    current_period_end: Optional[str] = Field(
        default=None,
        description="Borne de l'accès : fin de la période couverte. Format 'YYYY-MM-DD "
                    "HH:MM:SS' UTC (normalisé par la couche DB) — pas de l'ISO 8601, à "
                    "la différence de billing.confirm qui rend un horodatage à offset. "
                    "`null` sur un abonnement offert (aucune période).")
    next_billing_at: Optional[str] = Field(
        default=None,
        description="Prochaine échéance à tirer. `null` = plus RIEN ne sera tiré — "
                    "abonnement offert, ou résilié (canceled_at posé) — surtout pas "
                    "« pas encore programmé ».")
    grace_until: Optional[str] = Field(
        default=None,
        description="Fin du délai de grâce d'un impayé (`past_due`) : au-delà, la "
                    "relance cesse et l'abonnement bascule. `null` hors impayé.")
    canceled_at: Optional[str] = Field(
        default=None,
        description="Horodatage de la DEMANDE de résiliation, pas de la fin d'accès : "
                    "le statut reste 'active' et subscribed=True jusqu'à "
                    "current_period_end. Une résiliation se lit donc ici, JAMAIS sur "
                    "`status`.")


class SubscribeStarted(BaseModel):
    """Souscription OUVERTE, pas conclue : à ce stade rien n'est débité, aucun miroir
    d'abonnement n'existe et l'org n'a aucun droit ouvert. Le geste se finit sur la
    page de checkout hébergée, puis billing.confirm constate l'encaissement."""
    checkout_url: str = Field(
        description="Page de checkout HÉBERGÉE Mollie où le payeur finit (3DS carte, "
                    "ou saisie IBAN + acceptation du mandat SEPA). À ouvrir dans un "
                    "navigateur : rien ne se passe côté serveur tant qu'elle n'est pas "
                    "parcourue, et elle expire.")
    payment_intent_id: str = Field(description="Identifiant Mollie du PREMIER paiement "
                                               "('tr_…') — la trace à rapprocher de "
                                               "billing.payments (kind='initial').")
    plan: str = Field(description="Palier demandé (écho de l'entrée). Il voyage dans la "
                                  "metadata du paiement : c'est de là que confirm le "
                                  "relit, aucun état serveur pendant le checkout.")
    method: str = Field(description="'card' | 'sepa' — écho de l'entrée, il RESTREINT la "
                                    "page de checkout. Ne présume pas du moyen "
                                    "finalement enregistré : le `method` réel se lit sur "
                                    "confirm/status.")


class ConfirmResult(BaseModel):
    """Avancement du premier paiement (POLLING au retour du payeur, et en
    réconciliation). Enveloppe commune à quatre branches, discriminées par `status` ;
    les champs propres à une branche sont simplement absents des autres.

    ⚠️ `status` décrit la SOUSCRIPTION, pas le paiement — le brut du PSP est
    `payment_status`. Appel idempotent : re-confirmer un abonnement déjà actif rend
    `{status:'active', plan}` seul, sans method ni current_period_end ; leur absence
    n'est donc pas une anomalie."""
    status: str = Field(
        description="'active' = encaissé, mandat récupéré, miroir posé, accès OUVERT. "
                    "'pending' = pas encore encaissé (le payeur est peut-être encore "
                    "sur la page) : re-poller. 'failed' = paiement failed/canceled/"
                    "expired, l'org n'est PAS abonnée et il faut re-souscrire (aucune "
                    "reprise possible sur ce paiement).")
    plan: Optional[str] = Field(default=None, description="Palier activé — relu de la "
                                                          "metadata du paiement. Présent "
                                                          "sur 'active' seulement.")
    method: Optional[str] = Field(
        default=None,
        description="Moyen RÉELLEMENT enregistré ('card' | 'sepa'), déduit du paiement "
                    "Mollie et non de ce qui avait été demandé. Absent sur le no-op "
                    "idempotent.")
    current_period_end: Optional[str] = Field(
        default=None,
        description="Fin de la première période, ISO 8601 avec offset (⚠️ format "
                    "DIFFÉRENT de billing.status, qui rend 'YYYY-MM-DD HH:MM:SS'). "
                    "Absent sur le no-op idempotent.")
    payment_status: Optional[str] = Field(
        default=None,
        description="État BRUT du paiement chez Mollie (open, pending, authorized, "
                    "paid, failed, canceled, expired). Porté par les branches 'pending' "
                    "et 'failed' uniquement.")


class Payment(BaseModel):
    """Une TENTATIVE de paiement journalisée, pas une facture ni un reçu : une ligne
    peut n'avoir rien encaissé. Seul `status='paid'` atteste un encaissement."""
    id: int = Field(description="Identifiant de la ligne de journal LOCALE (séquence), "
                                "pas l'identifiant Mollie.")
    kind: str = Field(description="'initial' (premier paiement d'une souscription, celui "
                                  "qui crée le mandat) | 'renewal' (échéance rejouée sur "
                                  "le mandat existant).")
    amount: int = Field(description="Montant de la tentative en CENTIMES, figé au moment "
                                    "de la tentative : il peut différer du prix courant "
                                    "du palier rendu par billing.status.")
    currency: str = Field(description="Code devise ISO en minuscules ('eur').")
    status: str = Field(
        description="État repris du PSP : 'processing'/'open'/'pending'/'authorized' = "
                    "en vol (re-pollé) ; 'paid' | 'failed' | 'canceled' | 'expired' = "
                    "terminal. Une ligne non terminale ne se conclura pas d'elle-même "
                    "dans cette réponse : c'est le runner qui la fera bouger.")
    attempt: int = Field(description="Rang de la tentative pour une MÊME échéance "
                                     "(relance d'impayé) : plusieurs lignes de même "
                                     "`kind` et même montant ne sont pas des doubles "
                                     "débits, elles se distinguent par ce rang.")
    created_at: str = Field(description="Création de la TENTATIVE ('YYYY-MM-DD HH:MM:SS' "
                                        "UTC), pas la date d'encaissement — qui n'est "
                                        "pas exposée ici.")


class PaymentsView(BaseModel):
    """Journal des tentatives, plus récentes d'abord, borné par `limit`. Une liste vide
    est normale sur un abonnement offert (comp) : rien n'y transite par le PSP."""
    payments: list[Payment]


def _domain(fn, *args):
    """Traduit les erreurs domaine/PSP en refus neutres (jamais un 500 nu) :
    ValueError = état/entrée (`code: détail`), MollieError = amont PSP (502),
    RuntimeError = config/invariant (MOLLIE_API_KEY absente, mandat manquant)."""
    try:
        return fn(*args)
    except ValueError as e:
        msg = str(e)
        code = msg.split(":", 1)[0].strip() if ":" in msg else "billing_error"
        raise AuthzDenied(409 if code in ("already_subscribed",) else 400, code, msg)
    except MollieError as e:
        raise AuthzDenied(502, "psp_error", e.detail)
    except RuntimeError as e:
        msg = str(e)
        code = msg.split(":", 1)[0].strip() if ":" in msg else ""
        # Tout n'est pas transitoire. `no_mandate` (encaissé sans mandat réutilisable)
        # et `bad_metadata` sont DÉFINITIFS : les annoncer « facturation indisponible »
        # invite à retenter — sur un état où le client a DÉJÀ été débité et où il faut
        # une investigation manuelle. Un 409 dit la vérité : l'état empêche d'aboutir,
        # réessayer n'y changera rien.
        if code in ("no_mandate", "bad_metadata"):
            raise AuthzDenied(409, code, msg)
        raise AuthzDenied(503, "billing_unavailable", msg)


def _plans(ctx: ResolvedCtx, inp: NoInput) -> dict:
    return {"plans": billing.plans()}


def _status(ctx: ResolvedCtx, inp: NoInput) -> dict:
    return _domain(billing.status, ctx.org_id)


def _subscribe(ctx: ResolvedCtx, inp: SubscribeInput) -> dict:
    def call():
        return billing.subscribe(ctx.org_id, inp.plan, inp.return_url,
                                 method=inp.method)

    return _domain(call)


def _confirm(ctx: ResolvedCtx, inp: NoInput) -> dict:
    return _domain(billing.confirm, ctx.org_id)


def _cancel(ctx: ResolvedCtx, inp: NoInput) -> dict:
    return _domain(billing.cancel, ctx.org_id)


def _admin_set_plan(ctx: ResolvedCtx, inp: AdminPlanInput) -> dict:
    if inp.plan:
        return _domain(lambda: billing.admin_set_plan(
            inp.org_id, inp.plan, granted_by=ctx.sub))
    return _domain(lambda: billing.admin_clear_plan(inp.org_id))


def _payments(ctx: ResolvedCtx, inp: PaymentsInput) -> dict:
    from ..db import billing as db_billing

    rows = db_billing.list_billing_payments(ctx.org_id, inp.limit)
    return {"payments": [
        {k: r.get(k) for k in ("id", "kind", "amount", "currency", "status",
                               "attempt", "created_at")}
        for r in rows
    ]}


_BILLING_CAPS = [
    Capability(
        key="billing.plans", handler=_plans, Input=NoInput, authz=SUB_ONLY,
        Output=PlansView,
        rest=RestBinding("GET", "/api/billing/plans"),
    ),
    Capability(
        key="billing.status", handler=_status, Input=NoInput, authz=ORG_MEMBER,
        Output=BillingStatus,
        rest=RestBinding("GET", "/api/me/billing"),
    ),
    Capability(
        key="billing.subscribe", handler=_subscribe, Input=SubscribeInput,
        authz=ORG_ADMIN, Output=SubscribeStarted,
        rest=RestBinding("POST", "/api/me/billing/subscribe"),
    ),
    Capability(
        key="billing.confirm", handler=_confirm, Input=NoInput,
        authz=ORG_ADMIN, Output=ConfirmResult,
        rest=RestBinding("POST", "/api/me/billing/confirm"),
    ),
    # Résiliation à fin de période : rend le MÊME état que billing.status (avec
    # canceled_at posé, statut encore 'active') — d'où l'Output partagé.
    Capability(
        key="billing.cancel", handler=_cancel, Input=NoInput,
        authz=ORG_ADMIN, Output=BillingStatus,
        rest=RestBinding("POST", "/api/me/billing/cancel"),
    ),
    Capability(
        key="billing.payments", handler=_payments, Input=PaymentsInput,
        authz=ORG_MEMBER, Output=PaymentsView,
        rest=RestBinding("GET", "/api/me/billing/payments"),
    ),
    # Admin : forcer un plan sur une org SANS paiement (abonnement comp) ou le
    # retirer (plan=null). Ouvre l'entitlement (options + plafond messagerie du
    # plan) immédiatement. Sert pilotes/partenaires + palier « sur devis ».
    Capability(
        key="billing.admin_set_plan", handler=_admin_set_plan, Input=AdminPlanInput,
        authz=SUPER_ADMIN,
        description="[super admin] Force a plan on an org WITHOUT payment (comp "
                    "subscription): unlocks the plan's options + messaging seat cap "
                    "immediately, no PSP, never charged. Pass plan=null to remove a "
                    "comp plan (refuses to touch a PAID subscription). For pilots, "
                    "partners and the custom 'enterprise' tier.",
        mcp="oto_admin_set_plan",
        rest=RestBinding("POST", "/api/admin/orgs/{org_id}/plan", {"org_id": "org_id"}),
    ),
]

# Feature flag (ADR 0043, dark launch) : billing dormant tant que OTO_BILLING_ENABLED
# n'est pas posé (prod off / canari on). Les descripteurs restent au registre
# (introspection, tests, catalogue admin) ; SEULE la surface est gatée au montage.
CAPABILITIES += [replace(_cap, gate=billing.is_enabled) for _cap in _BILLING_CAPS]
