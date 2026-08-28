"""Identité de facturation d'une org (#486) — qui paie, et sous quel régime.

Séparée de `capabilities/billing.py` parce que c'est un autre objet : l'abonnement
est un CYCLE (souscrire, confirmer, résilier, relancer), l'identité est une FICHE
qu'on remplit une fois et qu'on corrige rarement. Elle est pourtant le préalable du
cycle — le pays décide du taux de TVA, donc du montant réellement débité, et
`billing.subscribe` refuse tant qu'elle n'est pas là (409
`billing_identity_required`, qui NOMME les champs manquants).

REST-only, comme toute la famille billing (ADR 0043) : c'est un formulaire humain
d'administrateur, pas un geste d'agent. Consulter = tout membre (le TTC affiché en
dépend) ; écrire = org_admin.

⚠️ Le numéro de TVA est contrôlé en FORME seulement — préfixe du pays et grammaire
nationale (`billing_vat.VAT_FORMATS`). Son EXISTENCE n'est pas vérifiée auprès de
VIES : c'est un appel réseau tiers, laissé en TODO sur l'issue #486. D'ici là, un
numéro bien formé mais inexistant fait passer un client en autoliquidation à tort.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Optional

from pydantic import BaseModel, Field

from .. import billing, billing_vat
from ._authz import ORG_ADMIN, ORG_MEMBER
from ._types import Capability, ResolvedCtx, RestBinding
from .billing import NoInput, _domain
from .registry import CAPABILITIES


class IdentityInput(BaseModel):
    """L'identité de facturation, POSTÉE ENTIÈRE : la capacité remplace, elle ne
    fusionne pas (c'est un formulaire d'une page, pas un patch). Les cinq champs
    requis sont ceux de `billing_vat.REQUIRED_IDENTITY_FIELDS` — même liste que
    celle dont `subscribe` nomme les manquants."""
    legal_name: str
    country_code: str
    address_line: str
    postal_code: str
    city: str
    address_line2: Optional[str] = None
    vat_number: Optional[str] = None
    billing_email: Optional[str] = None


class BillingIdentity(BaseModel):
    """Qui paie, et depuis où. Collectée avant le premier paiement (#486) : le pays
    décide du taux de TVA, donc du montant réellement débité, et la facture (#488)
    ne s'émet pas sans raison sociale ni adresse."""
    legal_name: str = Field(description="Raison sociale, telle qu'elle figurera sur "
                                        "la facture.")
    country_code: str = Field(description="Pays de facturation, code ISO-3166-1 "
                                          "alpha-2 en MAJUSCULES ('FR', 'BE', 'US'…). "
                                          "⚠️ La Grèce est 'GR' ici, alors que son "
                                          "numéro de TVA commence par 'EL'.")
    vat_number: Optional[str] = Field(
        default=None,
        description="Numéro de TVA intracommunautaire NORMALISÉ (sans espaces, "
                    "préfixe pays compris : 'FR12345678901'). `null` = pas de numéro "
                    "déclaré. Contrôlé en FORME seulement — l'existence du numéro "
                    "n'est pas vérifiée auprès de VIES (TODO #486).")
    address_line: Optional[str] = None
    address_line2: Optional[str] = None
    postal_code: Optional[str] = None
    city: Optional[str] = None
    billing_email: Optional[str] = Field(
        default=None,
        description="Destinataire de la facture s'il diffère de l'administrateur.")


class IdentityView(BaseModel):
    """L'identité de l'org active, et si elle suffit à souscrire. Forme commune à
    la lecture et à l'écriture : `set` rend l'état RAFRAÎCHI, pas un accusé."""
    identity: Optional[BillingIdentity] = Field(
        default=None,
        description="`null` tant qu'aucune identité n'a été posée sur cette org.")
    missing: list[str] = Field(
        description="Champs requis encore absents, dans l'ordre du formulaire. Liste "
                    "VIDE = `billing.subscribe` ne refusera pas pour cette raison. "
                    "C'est la même liste que celle nommée par le refus "
                    "`billing_identity_required`.")
    vat_scheme: Optional[str] = Field(
        default=None,
        description="Régime qui s'appliquerait aujourd'hui : 'fr_ttc' (TVA française "
                    "20 %), 'reverse_charge' (autoliquidation intracommunautaire, "
                    "0 %), 'export' (hors UE, 0 %). `null` si l'identité ne permet "
                    "pas encore de trancher — `vat_blocked` dit alors pourquoi.")
    vat_rate_bps: Optional[int] = Field(
        default=None,
        description="Taux applicable en POINTS DE BASE (2000 = 20,00 %, 0 = exonéré). "
                    "Jamais un flottant : le taux sert à calculer un montant en "
                    "centimes, et un flottant y introduirait un arrondi.")
    vat_blocked: Optional[str] = Field(
        default=None,
        description="Pourquoi aucun régime ne peut être servi : "
                    "'billing_identity_required' (identité incomplète) ou "
                    "'vat_consumer_unsupported' (client de l'Union hors France sans "
                    "numéro de TVA — le guichet OSS n'est pas en place, la "
                    "souscription en ligne lui est fermée). `null` = rien ne bloque.")


_IDENTITY_FIELDS = ("legal_name", "country_code", "vat_number", "address_line",
                    "address_line2", "postal_code", "city", "billing_email")


def _identity_view(org_id: int) -> dict:
    """La vue commune à `get` et `set` : l'identité, ce qui lui manque, et le régime
    qu'elle produirait.

    Le régime est calculé sur un montant NEUTRE (0) : ici on ne veut que le
    classement du client, pas un montant. Et il passe par `tax_preview`, la lecture
    qui ne refuse jamais — un formulaire doit pouvoir s'afficher précisément quand
    l'identité est incomplète, c'est même à ce moment-là qu'il sert."""
    from ..db import billing as db_billing

    row = db_billing.get_billing_identity(org_id)
    apercu = billing_vat.tax_preview(0, row)
    return {"identity": {k: row.get(k) for k in _IDENTITY_FIELDS} if row else None,
            "missing": billing_vat.missing_identity_fields(row),
            "vat_scheme": apercu["vat_scheme"],
            "vat_rate_bps": apercu["vat_rate_bps"],
            "vat_blocked": apercu["vat_blocked"]}


def _identity_get(ctx: ResolvedCtx, inp: NoInput) -> dict:
    return _identity_view(ctx.org_id)


def _identity_set(ctx: ResolvedCtx, inp: IdentityInput) -> dict:
    from ..db import billing as db_billing

    def call():
        # Le pays et le numéro sont NORMALISÉS et contrôlés en forme AVANT l'écriture
        # (`country_invalid`, `vat_number_invalid`) : une identité stockée doit être
        # exploitable telle quelle par la règle de TVA, sinon le refus surviendrait au
        # moment du paiement — c'est-à-dire trop tard pour être utile.
        pays = billing_vat.normalize_country(inp.country_code)
        tva = billing_vat.check_vat_number(pays, inp.vat_number)
        db_billing.upsert_billing_identity(
            ctx.org_id, legal_name=inp.legal_name.strip(), country_code=pays,
            vat_number=tva, address_line=inp.address_line.strip(),
            address_line2=inp.address_line2, postal_code=inp.postal_code.strip(),
            city=inp.city.strip(), billing_email=inp.billing_email)
        return _identity_view(ctx.org_id)

    return _domain(call)


# Feature flag (ADR 0043, dark launch) : même gate que le reste du billing —
# l'identité n'a pas de sens si l'abonnement est dormant.
CAPABILITIES += [replace(_cap, gate=billing.is_enabled) for _cap in [
    # Identité de facturation (#486) — le socle de la TVA ET de la facture (#488).
    # Consulter = tout membre (le TTC affiché dépend d'elle) ; écrire = org_admin,
    # comme les autres actes de facturation. REST-only, même raison que le reste de
    # la famille : c'est un formulaire humain, pas un geste d'agent.
    Capability(
        key="me.billing.identity.get", handler=_identity_get, Input=NoInput,
        authz=ORG_MEMBER, Output=IdentityView,
        rest=RestBinding("GET", "/api/me/billing/identity"),
    ),
    Capability(
        key="me.billing.identity.set", handler=_identity_set, Input=IdentityInput,
        authz=ORG_ADMIN, Output=IdentityView,
        rest=RestBinding("PUT", "/api/me/billing/identity"),
    ),
]]
