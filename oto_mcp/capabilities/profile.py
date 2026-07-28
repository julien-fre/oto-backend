"""Fiche « situation avec oto » de l'utilisateur — qui il est, son métier, ses objectifs,
son CRM, les connecteurs voulus, ses préférences de ton.

Persistée dans `user_account_profile.profile` (data model LIBRE, shallow-merge), **relue à
chaque session** (injectée au handshake, bloc C) : l'agent personnalise son adresse, ses
workflows et son style à partir d'elle. Ce n'est PAS un mode d'accueil scripté (l'onboarding
est un projet, ADR 0032 §7) — l'agent l'entretient au fil de l'eau, notamment depuis le
projet « Découverte ».

**Une capacité, deux faces (ADR 0009, motif `platform.instructions`)** : `oto_profile(op=…)`
côté MCP + `GET`/`PUT /api/me/profile` côté dashboard, **mêmes handlers**. C'était jusqu'au
2026-07-28 un tool écrit à la main (`tools/profile.py`) DOUBLÉ d'une capacité REST — deux
contrats sur une donnée (ADR 0042 §Convergence des surfaces, Décision 4/6).

La fiche reste un primitif DISTINCT du guide (ADR 0042 Décision 6) : seul contenu structuré
(`PROFILE_FIELDS` + `missing`), et son auteur nominal est l'agent — là où la note user
(guide `delivery=init`) est la voix verbatim de l'utilisateur.

`SUB_ONLY` → chacun voit/édite la sienne.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel

from .. import db
from ._authz import SUB_ONLY
from ._types import AuthzDenied, Capability, ResolvedCtx, RestBinding
from .registry import CAPABILITIES

# Data model de la fiche : chaque champ = une clé persistée dans `profile`. `question`
# guide l'agent pour la remplir naturellement ; `why` explique à quoi sert la donnée.
# Les clés libres hors de cette liste sont acceptées (data model ouvert).
PROFILE_FIELDS: list[dict] = [
    {"key": "full_name",
     "question": "Comment s'appelle l'utilisateur (prénom/nom) ?",
     "why": "Personnaliser l'adresse et signer les emails/messages."},
    {"key": "role",
     "question": "Quel est son rôle / poste ?",
     "why": "Adapter le niveau et les workflows (commercial, fondateur, ops…)."},
    {"key": "company",
     "question": "Pour quelle entreprise / structure travaille-t-il, et dans quel secteur ?",
     "why": "Contextualiser la prospection et les recherches d'entreprise."},
    {"key": "goals",
     "question": "Qu'est-ce qu'il veut accomplir avec Oto (2-3 cas d'usage concrets) ?",
     "why": "Prioriser les connecteurs et proposer les bons workflows."},
    {"key": "crm",
     "question": "Quel CRM utilise-t-il (Attio, Folk, HubSpot, Pennylane, aucun…) ?",
     "why": "Router les écritures CRM vers le bon connecteur."},
    {"key": "connectors_wanted",
     "question": "Quels connecteurs/outils sont prioritaires pour lui (LinkedIn, email, "
                 "données entreprise FR, messagerie…) ?",
     "why": "Guider la configuration des clés et de la visibilité des outils."},
    {"key": "tone",
     "question": "Y a-t-il des préférences de ton/langue ou des contraintes à respecter ?",
     "why": "Aligner le style de rédaction et les gardes-fous."},
]


class _NoInput(BaseModel):
    pass


class SetProfileInput(BaseModel):
    # Shallow-mergé dans le JSONB `profile` (valeur vide = efface la valeur, pas la clé).
    fields: dict = {}


class ProfileOpInput(BaseModel):
    op: Literal["get", "update"] = "get"
    fields: Optional[dict] = None


def _missing(profile: dict) -> list[str]:
    return [f["key"] for f in PROFILE_FIELDS if not str(profile.get(f["key"]) or "").strip()]


def _view(state: dict) -> dict:
    """Forme UNIQUE des deux faces : la fiche + le schéma suggéré + ce qui manque."""
    profile = state.get("profile") or {}
    return {"profile": profile, "updated_at": state.get("updated_at"),
            "fields": PROFILE_FIELDS, "missing": _missing(profile)}


def _get_profile(ctx: ResolvedCtx, inp: _NoInput) -> dict:
    return _view(db.get_account_profile(ctx.sub))


def _set_profile(ctx: ResolvedCtx, inp: SetProfileInput) -> dict:
    """Écrit TEL QUEL (voix humaine : le dashboard doit pouvoir vider un champ)."""
    return _view(db.update_account_profile(ctx.sub, inp.fields or {}))


def _profile_op(ctx: ResolvedCtx, inp: ProfileOpInput) -> dict:
    if inp.op == "get":
        return _get_profile(ctx, _NoInput())
    if not isinstance(inp.fields, dict) or not inp.fields:
        raise AuthzDenied(400, "missing_fields",
                          "`fields` (objet clé→valeur non vide) requis pour op=update.")
    # Face AGENT : on ne persiste que des valeurs non vides — un agent n'efface pas la
    # fiche par mégarde (le dashboard, lui, passe par `_set_profile` sans filtre).
    clean = {k: v for k, v in inp.fields.items() if v not in (None, "", [])}
    if not clean:
        raise AuthzDenied(400, "missing_fields", "aucune valeur à enregistrer.")
    return _set_profile(ctx, SetProfileInput(fields=clean))


CAPABILITIES += [
    # Face MCP op-aware (surface consolidée ADR 0047).
    Capability(
        key="me.profile", handler=_profile_op, Input=ProfileOpInput, authz=SUB_ONLY,
        description=(
            "The user's « situation with oto » profile — who they are, their job, goals, "
            "CRM, wanted connectors, tone preferences. This card is re-read into EVERY "
            "session to personalise your help; keep it up to date as you learn something "
            "useful about the user (notably in the « Découverte » project). op=get "
            "(default) → the card + `fields` (suggested schema) + `missing` (keys still "
            "empty); op=update (`fields`: a key→value object, shallow-merged; free keys "
            "accepted). Record ONLY what the user actually told you — never invent."),
        mcp="oto_profile",
    ),
    # Faces REST par-verbe (édition manuelle dans la section Context du dashboard).
    Capability(
        key="me.profile.get", handler=_get_profile, Input=_NoInput, authz=SUB_ONLY,
        description="The user's « situation with oto » profile plus the suggested field schema.",
        rest=RestBinding("GET", "/api/me/profile"),
    ),
    Capability(
        key="me.profile.set", handler=_set_profile, Input=SetProfileInput, authz=SUB_ONLY,
        description="Shallow-merge `fields` into the user's profile (empty string clears a value).",
        rest=RestBinding("PUT", "/api/me/profile"),
    ),
]
