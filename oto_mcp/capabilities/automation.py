"""Capacité « déclencher une automatisation » — le seam `dispatch()` d'oto-backend#268.

Une automatisation, c'est une **routine Claude Code** : un agent autonome hébergé par
Anthropic, avec son prompt figé et le connecteur oto branché. Cette capacité est le
seul point d'où oto la déclenche.

Née capacité et non tool écrit à la main (ADR 0042) : le dashboard voudra un bouton
« déclencher », et un `@mcp.tool()` aurait forcé une seconde implémentation REST avec
sa propre autz. Deux faces, un descripteur.

**Le credential porte la routine.** Une instance = une routine (registre `routine`,
`routine_id` + `token`), donc `_instance=` choisit l'automatisation, et un projet qui
binde son instance déclenche la sienne sans que l'appelant ait à la nommer. C'est ce
qui rend « une routine par automatisation » adressable sans registre parallèle.

**`text` est une RÉFÉRENCE, pas la donnée.** Anthropic enveloppe le texte dans un bloc
`<routine-fire-payload>` étiqueté non fiable, que le prompt doit explicitement opter
pour lire. Y verser l'enregistrement complet serait doublement mauvais : le payload est
plafonné, et l'agent doit de toute façon relire la donnée fraîche par le MCP au moment
où il agit, pas au moment où l'événement est né.

Autz `ORG_MEMBER` : le gate réel est la résolution du credential (cascade membre >
groupe > org), qui décide déjà qui atteint quelle routine.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from .. import access, routine_fire
from ._authz import ORG_MEMBER
from ._types import AuthzDenied, Capability, ResolvedCtx, RestBinding
from .registry import CAPABILITIES


class FireInput(BaseModel):
    # Contexte du run, libre et facultatif. Une routine planifiée n'en a pas besoin ;
    # un déclenchement sur événement y met de quoi retrouver la cible.
    text: Optional[str] = None
    # Sélecteur d'instance : quelle routine. Omis = l'instance par défaut résolue par
    # la cascade (ou celle bindée au projet actif).
    account: Optional[str] = None


class FireOut(BaseModel):
    fired: bool
    # Ids rendus par Anthropic. `session_url` est la supervision : c'est là qu'on lit
    # ce que l'agent a fait — l'appel ne rend PAS le résultat du run.
    session_id: Optional[str] = None
    session_url: Optional[str] = None


def _fire(ctx: ResolvedCtx, inp: FireInput) -> dict:
    try:
        creds = access.resolve_credential_fields("routine", account=inp.account)
    except Exception as e:  # noqa: BLE001 — provider inconnu, credential absent…
        raise AuthzDenied(400, "routine_not_configured", str(e))
    if not creds:
        raise AuthzDenied(
            400, "routine_not_configured",
            "aucune routine configurée — pose le credential `routine` "
            "(`routine_id` + jeton de déclenchement) sur ton compte ou ton org, "
            "une instance par automatisation")
    try:
        return routine_fire.fire(creds.get("routine_id") or "",
                                 creds.get("token") or "",
                                 text=inp.text)
    except routine_fire.RoutineFireError as e:
        # 502 sur un amont en vrac (l'appelant peut réessayer), 400 sur un credential
        # ou une routine en faute (réessayer ne changera rien).
        raise AuthzDenied(502 if e.retryable else 400, "routine_fire_failed", str(e))


CAPABILITIES += [
    Capability(
        key="me.automation.fire",
        handler=_fire,
        Input=FireInput,
        Output=FireOut,
        authz=ORG_MEMBER,
        mcp="routine_fire",
        rest=RestBinding(verb="POST", path="/api/me/automations/fire"),
        description=(
            "Fire a Claude Code routine — an autonomous agent that runs on Anthropic's "
            "infrastructure with the oto connector attached. Returns immediately with "
            "the session id and URL; the run's RESULT is read in that session, not "
            "here. `text` is optional run context and reaches the agent as UNTRUSTED "
            "data (the routine's own prompt decides whether to act on it), so pass a "
            "REFERENCE the agent can reload through oto — a row id, a project — not "
            "the record itself. `account` selects which routine when several are "
            "configured (one instance per automation)."
        ),
    ),
]
