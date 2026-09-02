"""Relance des comptes qui n'ont jamais rien fait — console plateforme.

La plateforme savait COMPTER ses comptes inactifs (`monitoring op=funnel` rend
`never_active`) sans jamais pouvoir les NOMMER ni leur écrire. Cette capacité ferme
l'écart, et l'essentiel de ce qu'elle porte n'est pas l'envoi : c'est ce qui l'encadre.

**Cinq garde-fous, tous mécaniques.** Aucun ne dépend de ce que l'opérateur pense à
faire, parce qu'une consigne qu'on se rappelle d'appliquer sera oubliée le jour où
quelqu'un ajoutera un critère :

1. **Le tenant partenaire est écarté dans la REQUÊTE** (`db/outreach.py`), en amont de
   tout critère d'activité. Ses comptes sont les clients d'un tiers ; leur écrire, c'est
   parler par-dessus lui, dans son produit. `tests/test_outreach_audience.py` rougit si
   un compte de partenaire entre dans une sélection.
2. **On ne relance pas deux fois** : l'index unique `(campaign, sub)` de
   `outreach_sends`, et l'écriture PRÉCÈDE l'envoi.
3. **On n'envoie pas ce qu'on n'a pas vu arriver dans sa propre boîte** : `op=send`
   refuse tant qu'un `op=test` n'a pas été reçu pour CETTE empreinte de contenu et
   pour CHAQUE langue que l'envoi va servir. Retoucher une virgule invalide l'essai.
4. **Un geste qui touche N personnes dit N avant de partir** : `op=send` sans
   `confirm` refuse en annonçant N ; un `confirm` qui ne colle pas refuse aussi.
   Plafond dur à `db_outreach.MAX_ENVOI`.
5. **Le refus se respecte** : chaque mail porte un lien de désinscription signé, et un
   compte désinscrit quitte toute audience, pour toute campagne.

**Sur la langue, on ne devine pas.** Le seul signal déclaré est `users.locale`, la
préférence d'UI du dashboard — posée sur 2 des 40 comptes de l'audience au 2026-09-02.
Rien d'autre n'existe : `billing_identities.country_code` est à ZÉRO ligne en prod, et
le TLD d'une adresse ne dit pas la langue d'une personne (un `.com` peut être français,
un `.fr` une filiale). La capacité sert donc `users.locale` quand il est posé, et
`default_locale` — que l'opérateur CHOISIT — pour tout le reste, en disant combien de
comptes tombent dans chaque cas. Deviner aurait été plus confortable et faux.
"""
from __future__ import annotations

import hashlib
import json
from typing import Literal, Optional

from pydantic import BaseModel, Field

from .. import db, email as mailer, outreach_optout
from ..db import outreach as db_outreach
from ._authz import ADMIN_BY_OP, PLATFORM_ADMIN, SUPER_ADMIN
from ._types import (AuthzDenied, Capability, DeclaredError, ResolvedCtx,
                     RestBinding, cap_limit)
from .registry import CAPABILITIES

_LANGUES = ("fr", "en")


class OutreachInput(BaseModel):
    op: Literal["audience", "preview", "test", "send", "journal", "optouts",
                "optout_clear"] = "audience"
    # La campagne est la clé du « une seule fois par personne ». Un slug qu'on choisit,
    # pas un identifiant qu'on subit : deux relances distinctes = deux slugs.
    campaign: Optional[str] = None
    status: Literal["never_active", "dormant"] = "never_active"
    dormant_days: int = db_outreach.DEFAULT_SILENCE_DAYS
    limit: int = db_outreach.MAX_ENVOI

    # Le contenu, une version par langue. Rédigé par l'opérateur, jamais généré.
    subject_fr: Optional[str] = None
    body_fr: Optional[str] = None
    subject_en: Optional[str] = None
    body_en: Optional[str] = None
    cta_label_fr: Optional[str] = None
    cta_label_en: Optional[str] = None
    cta_url: Optional[str] = None

    # La langue servie aux comptes SANS préférence déclarée. Obligatoire dès qu'on
    # rend ou qu'on envoie : sans elle, il faudrait la deviner.
    default_locale: Literal["fr", "en"] = "fr"

    # Le compte à annoncer avant de partir. Absent ⟹ `op=send` refuse en donnant N.
    confirm: Optional[int] = None
    # `op=optout_clear` : le compte qu'on ré-inscrit, à sa demande explicite.
    target: Optional[str] = None

    only: list[str] = Field(default_factory=list)   # restreindre à ces emails/subs


# ── forme SERVIE (ADR 0059) ──────────────────────────────────────────────────

class AudienceRow(BaseModel):
    sub: str
    email: Optional[str] = None
    name: Optional[str] = None
    created_at: Optional[object] = None
    calls: int = 0
    last_seen_at: Optional[object] = None
    previous_outreach: int = 0
    # `locale` = la préférence DÉCLARÉE (null = aucune) ; `served_locale` = la langue
    # qui serait réellement servie. Les deux, séparément : confondre la préférence
    # d'une personne avec le défaut d'une campagne ferait passer un choix d'opérateur
    # pour une donnée de compte.
    locale: Optional[str] = None
    served_locale: str
    locale_source: Literal["declared", "default"]
    email_domain: Optional[str] = None
    sent: Optional[bool] = None
    reason: Optional[str] = None


class OutreachOutput(BaseModel):
    op: str
    campaign: Optional[str] = None
    # L'audience TELLE QU'ELLE EST au moment de l'appel — la liste servie EST
    # l'audience, il n'y a pas de reste invisible derrière une pagination.
    recipients: list[AudienceRow] = Field(default_factory=list)
    # `total` = l'audience ENTIÈRE (sans plafond ni `only`) ; `selected` = ce que
    # cette réponse porte vraiment. Les deux, parce que la troncature est le seul
    # écart qu'un opérateur ne peut pas voir : 200 lignes servies ne disent pas s'il
    # en reste 3 ou 3 000, et il croirait sa campagne finie.
    total: int = 0
    selected: int = 0
    truncated: bool = False
    with_declared_locale: int = 0
    with_default_locale: int = 0
    sent: int = 0
    # `op=optout_clear` seulement : le refus a-t-il été levé ? Un champ à lui plutôt
    # qu'un `sent` détourné — « 1 envoyé » pour une désinscription retirée se lirait
    # comme un mail parti.
    cleared: bool = False
    preview_html: dict = Field(default_factory=dict)   # locale -> HTML rendu
    fingerprint: Optional[str] = None
    tested_locales: list[str] = Field(default_factory=list)
    log: list = Field(default_factory=list)
    optouts: list = Field(default_factory=list)


def _campaign(inp: OutreachInput) -> str:
    c = (inp.campaign or "").strip()
    if not c:
        raise AuthzDenied(400, "campaign_required",
                          "`campaign` est obligatoire : c'est la clé qui garantit qu'une "
                          "personne n'est relancée qu'une fois. Choisis un slug, ex. "
                          "'onboarding-2026-09'.")
    return c


def _contenu(inp: OutreachInput, langues) -> dict:
    """Le contenu par langue, validé pour les seules langues qui seront servies.

    Exiger la version anglaise quand aucun destinataire ne la recevra ferait écrire un
    texte que personne ne lit — et un texte que personne ne lit finit bâclé, donc prêt
    à partir le jour où quelqu'un le recevra vraiment."""
    out = {}
    for lg in langues:
        sujet = (getattr(inp, f"subject_{lg}") or "").strip()
        corps = (getattr(inp, f"body_{lg}") or "").strip()
        if not sujet or not corps:
            raise AuthzDenied(400, "content_required",
                              f"`subject_{lg}` et `body_{lg}` sont requis : des comptes de "
                              f"cette audience recevront la version « {lg} ».")
        out[lg] = {"subject": sujet, "body": corps,
                   "cta_label": (getattr(inp, f"cta_label_{lg}") or "").strip() or None,
                   "cta_url": (inp.cta_url or "").strip() or None}
    return out


def _empreinte(contenu: dict) -> str:
    """sha256 du contenu servi, toutes langues. Ce qui lie l'essai à l'envoi."""
    canon = json.dumps(contenu, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def _langue(row: dict, defaut: str) -> tuple:
    """(langue servie, provenance). La préférence du compte prime ; à défaut, le choix
    de l'opérateur — jamais une déduction depuis l'adresse (cf. l'en-tête du module)."""
    declaree = (row.get("locale") or "").strip().lower()
    if declaree in _LANGUES:
        return declaree, "declared"
    return defaut, "default"


def _domaine(adresse: Optional[str]) -> Optional[str]:
    """Le domaine de l'adresse, servi comme INDICATION à l'œil de l'opérateur. Il
    n'entre dans aucune décision : le TLD d'une adresse ne dit pas la langue d'une
    personne."""
    a = (adresse or "").strip().lower()
    return a.split("@", 1)[1] if "@" in a else None


def _fiche(row: dict, defaut: str) -> dict:
    lg, source = _langue(row, defaut)
    return {"sub": row["sub"], "email": row.get("email"), "name": row.get("name"),
            "created_at": row.get("created_at"), "calls": int(row.get("appels") or 0),
            "last_seen_at": row.get("last_seen_at"),
            "previous_outreach": int(row.get("relances_deja_recues") or 0),
            "locale": row.get("locale"), "served_locale": lg, "locale_source": source,
            "email_domain": _domaine(row.get("email")),
            "sent": None, "reason": None}


def _audience(inp: OutreachInput, campaign: str) -> list[dict]:
    statut = "jamais_actif" if inp.status == "never_active" else "silencieux"
    rows = db_outreach.audience(
        campaign=campaign, statut=statut,
        silence_days=cap_limit(inp.dormant_days, 3650,
                               default=db_outreach.DEFAULT_SILENCE_DAYS),
        cap=cap_limit(inp.limit, db_outreach.MAX_ENVOI, default=db_outreach.MAX_ENVOI))
    fiches = [_fiche(r, inp.default_locale) for r in rows]
    cible = {str(x).strip().lower() for x in (inp.only or []) if str(x).strip()}
    if cible:
        fiches = [f for f in fiches
                  if {str(f["sub"]).lower(), str(f.get("email") or "").lower()} & cible]
    return fiches


def _total(inp: OutreachInput, campaign: str) -> int:
    return db_outreach.taille_audience(
        campaign=campaign,
        statut="jamais_actif" if inp.status == "never_active" else "silencieux",
        silence_days=cap_limit(inp.dormant_days, 3650,
                               default=db_outreach.DEFAULT_SILENCE_DAYS))


def _compte(fiches: list[dict], total: int) -> dict:
    return {"total": total, "selected": len(fiches),
            "truncated": total > len(fiches),
            "with_declared_locale": sum(1 for f in fiches if f["locale_source"] == "declared"),
            "with_default_locale": sum(1 for f in fiches if f["locale_source"] == "default")}


def _rendu(contenu: dict, lg: str, sub: Optional[str]) -> str:
    """Le HTML servi à un destinataire, avec SON lien de désinscription.

    `sub=None` (aperçu) rend la page sans lien : fabriquer un jeton pour personne
    donnerait un lien mort dans un aperçu, ce qui se lit comme un bug du pied de page.
    """
    c = contenu[lg]
    return mailer.render_composed_email(
        c["body"], cta_text=c.get("cta_label"), cta_url=c.get("cta_url"),
        locale=lg, brand="oto",
        unsubscribe_url=outreach_optout.lien(sub) if sub else None)


# ── les ops ──────────────────────────────────────────────────────────────────

def _op_audience(inp: OutreachInput) -> dict:
    campaign = _campaign(inp)
    fiches = _audience(inp, campaign)
    return {"op": inp.op, "campaign": campaign, "recipients": fiches,
            **_compte(fiches, _total(inp, campaign))}


def _op_preview(inp: OutreachInput) -> dict:
    """N'envoie RIEN. Rend l'audience ET le HTML de chaque langue qui sera servie."""
    campaign = _campaign(inp)
    fiches = _audience(inp, campaign)
    langues = sorted({f["served_locale"] for f in fiches}) or [inp.default_locale]
    contenu = _contenu(inp, langues)
    empreinte = _empreinte(contenu)
    return {"op": inp.op, "campaign": campaign, "recipients": fiches,
            **_compte(fiches, _total(inp, campaign)),
            "preview_html": {lg: _rendu(contenu, lg, None) for lg in langues},
            "fingerprint": empreinte,
            "tested_locales": sorted(db_outreach.locales_essayees(
                campaign=campaign, fingerprint=empreinte))}


def _op_test(ctx: ResolvedCtx, inp: OutreachInput) -> dict:
    """L'essai part chez L'APPELANT, et nulle part ailleurs.

    Le destinataire n'est pas un paramètre : un `to` ouvrirait la porte à « je teste
    sur un vrai compte », qui n'est plus un test. Une langue par mail, pour chaque
    langue que l'envoi servira — voir deux versions dans un même corps ne prouve pas
    qu'elles arrivent toutes les deux."""
    campaign = _campaign(inp)
    fiches = _audience(inp, campaign)
    langues = sorted({f["served_locale"] for f in fiches}) or [inp.default_locale]
    contenu = _contenu(inp, langues)
    empreinte = _empreinte(contenu)
    moi = db.get_user(ctx.sub) or {}
    adresse = (moi.get("email") or "").strip()
    if not adresse:
        raise AuthzDenied(400, "no_operator_email",
                          "Ton compte n'a pas d'adresse email : l'essai n'a nulle part où "
                          "aller, et sans essai reçu l'envoi restera refusé.")
    envois, lignes = 0, []
    for lg in langues:
        c = contenu[lg]
        ok = mailer.send_composed_email(
            adresse, c["subject"], c["body"], cta_text=c.get("cta_label"),
            cta_url=c.get("cta_url"), locale=lg, brand="oto",
            unsubscribe_url=outreach_optout.lien(ctx.sub))
        if ok:
            envois += 1
            db_outreach.enregistre_envoi(
                campaign=campaign, sub=ctx.sub, to_email=adresse, locale=lg,
                fingerprint=empreinte, kind="test", sent_by=ctx.sub)
        lignes.append({"locale": lg, "to": adresse, "sent": bool(ok)})
    return {"op": inp.op, "campaign": campaign, "recipients": fiches,
            **_compte(fiches, _total(inp, campaign)),
            "sent": envois, "fingerprint": empreinte, "log": lignes,
            "tested_locales": sorted(db_outreach.locales_essayees(
                campaign=campaign, fingerprint=empreinte))}


def _op_send(ctx: ResolvedCtx, inp: OutreachInput) -> dict:
    """L'envoi réel. Trois refus avant la première ligne partie."""
    campaign = _campaign(inp)
    fiches = _audience(inp, campaign)
    total = _total(inp, campaign)
    langues = sorted({f["served_locale"] for f in fiches})
    if not fiches:
        return {"op": inp.op, "campaign": campaign, "recipients": [],
                **_compte(fiches, total)}
    contenu = _contenu(inp, langues)
    empreinte = _empreinte(contenu)

    # Le plafond se juge sur l'audience ENTIÈRE, jamais sur la page servie : sinon il
    # serait inatteignable (la lecture tronque déjà à MAX_ENVOI) et l'opérateur
    # enverrait à 200 personnes en croyant en avoir couvert 3 000.
    if total > db_outreach.MAX_ENVOI:
        raise AuthzDenied(400, "audience_too_large",
                          f"{total} destinataires, plafond {db_outreach.MAX_ENVOI}. Découpe "
                          "en plusieurs campagnes (`only`, ou un critère plus étroit) — un "
                          "envoi qu'on ne peut pas relire ne se rattrape pas.")
    # ① L'essai. Chaque langue qui va partir doit avoir été reçue par l'opérateur,
    # sur CE contenu exact.
    manquantes = set(langues) - db_outreach.locales_essayees(
        campaign=campaign, fingerprint=empreinte)
    if manquantes:
        raise AuthzDenied(409, "test_send_required",
                          f"Aucun essai reçu pour ce contenu en {', '.join(sorted(manquantes))}. "
                          "Lance `op=test` (il t'écrit à toi), lis le mail, puis reviens. "
                          "Toute retouche du texte invalide l'essai.")
    # ② Le compte annoncé avant de partir.
    if inp.confirm is None:
        raise AuthzDenied(409, "confirmation_required",
                          f"{len(fiches)} personnes recevront ce message "
                          f"({', '.join(langues)}). Rappelle `op=send` avec "
                          f"`confirm={len(fiches)}` pour l'envoyer.")
    if int(inp.confirm) != len(fiches):
        raise AuthzDenied(409, "confirmation_mismatch",
                          f"`confirm={inp.confirm}` ne correspond pas à l'audience actuelle "
                          f"({len(fiches)} personnes). Elle a bougé depuis ton dernier "
                          "aperçu — relis-la avant de confirmer.")

    envois = 0
    for f in fiches:
        lg = f["served_locale"]
        c = contenu[lg]
        # La trace AVANT l'envoi : c'est l'index unique qui empêche le doublon, il
        # doit donc se refermer avant que le mail parte. Un `False` ici = quelqu'un
        # d'autre vient d'écrire à cette personne.
        if not db_outreach.enregistre_envoi(
                campaign=campaign, sub=f["sub"], to_email=f["email"], locale=lg,
                fingerprint=empreinte, kind="send", sent_by=ctx.sub):
            f["sent"], f["reason"] = False, "déjà relancé sur cette campagne"
            continue
        ok = mailer.send_composed_email(
            f["email"], c["subject"], c["body"], cta_text=c.get("cta_label"),
            cta_url=c.get("cta_url"), locale=lg, brand="oto",
            unsubscribe_url=outreach_optout.lien(f["sub"]))
        f["sent"] = bool(ok)
        if ok:
            envois += 1
        else:
            # Rien n'est parti : retirer la trace, sinon la personne serait exclue de
            # toute audience future sans avoir jamais rien reçu.
            db_outreach.annule_envoi(campaign=campaign, sub=f["sub"])
            f["reason"] = "le mailer a refusé l'envoi — la personne reste à relancer"
    return {"op": inp.op, "campaign": campaign, "recipients": fiches,
            **_compte(fiches, total), "sent": envois, "fingerprint": empreinte}


def _outreach(ctx: ResolvedCtx, inp: OutreachInput) -> dict:
    if inp.op == "audience":
        return _op_audience(inp)
    if inp.op == "preview":
        return _op_preview(inp)
    if inp.op == "test":
        return _op_test(ctx, inp)
    if inp.op == "send":
        return _op_send(ctx, inp)
    if inp.op == "journal":
        return {"op": inp.op, "campaign": inp.campaign,
                "log": db_outreach.journal(campaign=(inp.campaign or "").strip() or None,
                                           cap=cap_limit(inp.limit, 500, default=200))}
    if inp.op == "optouts":
        return {"op": inp.op, "optouts": db_outreach.desinscrits(
            cap=cap_limit(inp.limit, 500, default=200))}
    # optout_clear
    cible = (inp.target or "").strip()
    if not cible:
        raise AuthzDenied(400, "target_required",
                          "`target` (sub ou email) est requis : on ne ré-inscrit quelqu'un "
                          "que sur sa demande, donc en le nommant.")
    if "@" in cible:
        u = db.get_user_by_email(cible)
        if not u:
            raise AuthzDenied(404, "unknown_user", f"Aucun compte avec l'email {cible!r}.")
        cible = u["sub"]
    return {"op": inp.op, "cleared": db_outreach.reinscrire(cible),
            "optouts": db_outreach.desinscrits(cap=50)}


CAPABILITIES += [
    Capability(
        key="admin.outreach", handler=_outreach, Input=OutreachInput,
        Output=OutreachOutput,
        # Lire l'audience et les refus = lentille de supervision (PLATFORM_ADMIN) ;
        # tout ce qui fait PARTIR un mail sous notre marque, ou lève le refus de
        # quelqu'un, est un acte de plateforme (SUPER_ADMIN). L'essai en fait partie :
        # il part vraiment, et c'est lui qui débloque l'envoi.
        authz=ADMIN_BY_OP({"audience": PLATFORM_ADMIN, "preview": PLATFORM_ADMIN,
                           "journal": PLATFORM_ADMIN, "optouts": PLATFORM_ADMIN,
                           "test": SUPER_ADMIN, "send": SUPER_ADMIN,
                           "optout_clear": SUPER_ADMIN}),
        errors=(
            DeclaredError(400, "campaign_required",
                          "aucun `campaign` : c'est la clé du « une seule fois par personne »"),
            DeclaredError(400, "content_required",
                          "`subject_<lg>`/`body_<lg>` manquant pour une langue que "
                          "l'audience va recevoir"),
            DeclaredError(400, "audience_too_large",
                          "l'audience dépasse le plafond d'un envoi en masse"),
            DeclaredError(400, "no_operator_email",
                          "op=test alors que le compte appelant n'a pas d'adresse"),
            DeclaredError(400, "target_required", "op=optout_clear sans `target`"),
            DeclaredError(404, "unknown_user", "l'email de `target` ne désigne aucun compte"),
            DeclaredError(409, "test_send_required",
                          "op=send avant qu'un essai de CE contenu ait été reçu, "
                          "pour chaque langue servie"),
            DeclaredError(409, "confirmation_required",
                          "op=send sans `confirm` : le nombre de destinataires est "
                          "annoncé, l'envoi ne part pas"),
            DeclaredError(409, "confirmation_mismatch",
                          "`confirm` ne correspond plus à l'audience du moment"),
        ),
        description=(
            "[platform admin] Follow-up emails to accounts that never used oto. "
            "Accounts of a PARTNER TENANT are excluded by the query itself, always: "
            "they are that partner's customers, not ours. Counted per ACCOUNT, never "
            "per organisation — most organisations are personal spaces created at "
            "signup, so an org-level count would write to people about a workspace "
            "they never asked for. "
            "op=audience (`campaign`, `status`='never_active' (default: no MCP tool "
            "call ever) or 'dormant' (called, then nothing for `dormant_days`)) → the "
            "recipients, each with `locale` (the DECLARED dashboard-language "
            "preference, often null), `served_locale` and `locale_source` "
            "('declared'|'default'). Language is NOT guessed from the email domain: "
            "`default_locale` ('fr'|'en') is your choice for everyone with no declared "
            "preference, and the counts tell you how many that is. "
            "op=preview → the same list plus the rendered HTML per language; sends "
            "NOTHING. op=test → sends the message TO YOURSELF, one email per language "
            "the campaign will serve; this is what unlocks op=send. op=send "
            "(`confirm`=the exact recipient count) → actually sends; it refuses "
            "without a test of this exact content, refuses without `confirm`, and "
            "refuses above the hard cap. Any edit to the text invalidates the test. "
            "Every message carries a signed unsubscribe link; an account that "
            "unsubscribes leaves every audience, for every campaign. "
            "op=journal (`campaign`) → who was contacted, when, in which language. "
            "op=optouts → who refused. op=optout_clear (`target`) → lift a refusal, on "
            "that person's explicit request only. `only` = restrict to these "
            "emails/subs, to roll out in stages."),
        mcp="oto_admin_outreach",
        rest=RestBinding("POST", "/api/admin/outreach"),
    ),
]
