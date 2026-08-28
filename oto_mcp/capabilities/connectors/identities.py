"""Capacités « sélecteur d'identité connectée » (ADR 0024) — surface unifiée,
co-déclarée MCP + REST, per-membre (`SUB_ONLY`). Backend par-connecteur dans
`connector_identities` (Google = comptes du coffre ; Unipile = identités distantes
d'une clé BYO). Le dashboard pose dessus le picker (liste + défaut)."""
from __future__ import annotations

import inspect
import logging
from typing import Optional

from pydantic import BaseModel, ConfigDict

from ...connectors import identities as connector_identities
from .._authz import SUB_ONLY
from .._types import AuthzDenied, Capability, ResolvedCtx, RestBinding

logger = logging.getLogger(__name__)


class IdentitiesInput(BaseModel):
    connector: str                       # nom de connecteur (path {connector})
    # Phase 2 (2026-08-25) : `org` / `group` = les comptes nommés du palier partagé
    # (backend keyed générique seulement). Défaut : les tiens.
    scope: str = "member"


class SetIdentityInput(BaseModel):
    connector: str                       # path {connector}
    identity_id: str                     # body — id renvoyé par connectors.identities
    scope: str = "member"                # `org`/`group` : admin du palier requis


class IdentityOwner(BaseModel):
    """Propriétaire d'un compte ACCORDÉ (#55) — présent seulement sur une identité
    qu'on opère sans la posséder."""
    sub: str
    email: Optional[str] = None
    name: Optional[str] = None
    org: Optional[int] = None               # org sous laquelle le owner a connecté le compte
    org_name: Optional[str] = None


class Identity(BaseModel):
    """Contrat commun `Identity` des trois backends (Google = comptes du coffre,
    Unipile = identités distantes d'une clé, keyed générique = lignes du coffre).
    L'unification est au niveau SURFACE, pas stockage."""
    # Opaque, et de nature différente selon le backend : email Google, handle
    # distant Unipile, nom de compte du coffre. Ne jamais le parser.
    id: str
    label: Optional[str] = None
    # `ok` par défaut. Sur un compte Unipile hébergé, le statut est confirmé par
    # une sonde de liveness (users/me) et rétrogradé en `disconnected` — le statut
    # de compte remonté par le fournisseur peut rester « OK » alors que la session
    # est morte (#236). Fail-soft : un incident de sonde laisse `ok`.
    status: Optional[str] = None
    # Identité effectivement opérée sur SON canal. Plusieurs entrées peuvent donc
    # être `is_default` en même temps sur un connecteur multi-canal (une par canal).
    is_default: bool
    # `null` hors multi-canal (Google) — fuite assumée du modèle Unipile, qui est
    # par-canal là où Google est par-service.
    channel: Optional[str] = None
    granted: Optional[bool] = None          # présent (true) si le compte est ACCORDÉ (#55)
    owner: Optional[IdentityOwner] = None   # le prêteur, présent avec `granted`


class ConnectorIdentities(BaseModel):
    """Identités joignables par le credential résolu du caller pour un connecteur."""
    connector: str
    # `false` = ce connecteur n'a AUCUN sélecteur d'identité. Mais l'inverse ne
    # tient pas : `supported:true` avec `identities: []` est normal (clé plateforme
    # Unipile → passer par la connexion hébergée, ou aucun compte connecté). Un slug
    # INCONNU, lui, ne rend jamais ce payload — il lève un 404 (feedback #162 :
    # `{supported:false, identities:[]}` rendait un nom bidon indiscernable d'un vrai
    # connecteur sans identités).
    supported: bool
    identities: list[Identity]
    # Le MOT du fournisseur pour un compte de ce connecteur — « workspace » chez
    # Slack, « organisation » chez Zoho, « site » pour le navigateur connecté,
    # « compte » par défaut. Servi ici parce que c'est la réponse que lit celui qui
    # CHOISIT : parler de « compte » pour un espace Slack l'oblige à traduire, et ni
    # l'agent ni l'écran n'ont de moyen de deviner le vocabulaire du fournisseur.
    noun: str = "compte"
    # ── Pourquoi la liste est VIDE (signal #504) ── présents SEULEMENT sur `[]`.
    # `no_credential` | `paid_option_off` | `over_quota` (une couche manque, cf.
    # `connectors/readiness.py`) | `no_identity_connected` (tout est en place, il
    # reste à en connecter un). Le défaut de #504 n'était pas le CONTENU de la
    # liste — vérifié sur la prod le 28/08, elle était vide parce qu'il n'y avait
    # rien à lister — c'était son SILENCE : `[]` ne disait pas s'il n'y avait aucun
    # compte, aucune clé, ou une clé qui ne voit rien, et l'appelant a inventé la
    # mauvaise cause pendant quatre jours.
    reason: Optional[str] = None
    next_step: Optional[str] = None         # le geste, rendu tel quel


class SelectedIdentity(BaseModel):
    """L'identité choisie, telle que la rend le backend du connecteur. Les clés
    varient selon la branche empruntée : un compte ACCORDÉ porte `granted`, le
    backend keyed générique renvoie un `label`, les autres ni l'un ni l'autre —
    d'où l'ouverture aux champs additionnels."""
    model_config = ConfigDict(extra="allow")

    connector: str
    id: str
    is_default: bool                        # toujours true — c'est l'effet du verbe
    # `null` pour un connecteur hors multi-canal. Sur Unipile, sélectionner
    # SON PROPRE compte efface le pointeur « identité opérée » du canal (retour à
    # soi) ; le retour est le même dans les deux cas.
    channel: Optional[str] = None
    label: Optional[str] = None
    granted: Optional[bool] = None


# Handlers async : un backend d'identités enregistré (`connector_identities.register`)
# peut être async (Browserbase — pennylaneged) ; les deux adaptateurs (MCP/REST)
# awaitent les handlers awaitable, on relaie ici.
def _require_known_connector(name: str) -> None:
    """Slug hors catalogue → erreur explicite, jamais le même payload qu'un
    connecteur connu sans identités (feedback #162 : `linkedin` rendait
    `{supported:false, identities:[]}` comme un nom bidon — faux négatif
    silencieux pour l'agent qui s'est trompé de slug).

    ⚠️ `linkedin` reste un alias piégeux même depuis que ce slug DÉSIGNE un vrai
    connecteur (#231 : recherche B2B via AI Ark, clé app credits SEULE, aucune
    notion de compte connecté — distinct d'`aiark`, qui garde son BYO) : un agent
    qui tape `linkedin` pense quasi toujours à SON compte LinkedIn personnel, qui
    vit sous `unipile`. On garde donc le hint AVANT le check registre — sinon la
    même confusion renaît sous une forme différente (`{supported:false}` au lieu
    de 404)."""
    from ... import providers
    if name == "linkedin":
        raise AuthzDenied(
            404, "unknown_connector",
            "Connecteur inconnu pour les identités : `linkedin` (recherche B2B, "
            "clé app credits partagée, aucun compte perso) n'a pas d'identités. "
            "Ton compte LinkedIn personnel passe par le connecteur `unipile`. "
            "Slugs valides : `oto_connector(op='list')`.")
    if name in providers.REGISTRY:
        return
    raise AuthzDenied(
        404, "unknown_connector",
        f"Connecteur inconnu : `{name}`. Slugs valides : `oto_connector(op='list')`.")


def _require_scope(ctx: ResolvedCtx, scope: str, *, write: bool) -> None:
    """`member` : toujours. `org` / `group` : membre de l'org (lecture) ; admin du
    palier pour choisir le défaut (écriture) — la clé partagée est celle de tous."""
    if scope not in connector_identities.SCOPES:
        raise AuthzDenied(400, "bad_scope", f"scope inconnu : `{scope}`.")
    if scope == "member" or not write:
        return
    from ... import access, roles
    org = access.current_org(ctx.sub)
    if org is None:
        raise AuthzDenied(400, "no_org_context", "Aucune org de contexte.")
    if scope == "org" and not roles.is_org_admin(ctx.sub, org):
        raise AuthzDenied(403, "forbidden", "Admin d'org requis pour choisir le compte d'org.")
    if scope == "group":
        gid = access.current_group(ctx.sub)
        if gid is None:
            raise AuthzDenied(400, "no_group_context", "Aucune équipe de contexte.")
        if not roles.can_admin_group(ctx.sub, gid):
            raise AuthzDenied(403, "forbidden", "Admin d'équipe requis pour choisir le compte d'équipe.")


async def _list(ctx: ResolvedCtx, inp: IdentitiesInput) -> dict:
    _require_known_connector(inp.connector)
    _require_scope(ctx, inp.scope, write=False)
    ids = connector_identities.list_identities(ctx.sub, inp.connector, inp.scope)
    if inspect.isawaitable(ids):
        ids = await ids
    from ... import access
    noun = access.account_noun(inp.connector)
    out = {
        "connector": inp.connector,
        "supported": connector_identities.supports(inp.connector),
        "noun": noun,
        "identities": ids,
    }
    if not ids:
        out.update(_why_empty(ctx, inp.connector, noun))
    return out


def _why_empty(ctx: ResolvedCtx, connector: str, noun: str) -> dict:
    """Le POURQUOI d'une liste vide (#504) — jamais un `[]` muet.

    Ce que le signal affirmait : « `oto_identity(op=list, connector=unipile)` renvoie
    `identities:[]` alors que le compte LinkedIn est connecté et opérationnel ».
    Ce que la prod montre (vérifié le 28/08/2026) : les trois lectures de ce compte
    datent du 14/08 à 14:00:44, 14:00:55 et 14:02:09 — le compte, lui, a été lié à
    **14:03:30**. Rejouée aujourd'hui sur le même sub, la liste rend bien le compte.
    Elle était vide parce qu'il n'y avait rien à lister ; le défaut RÉEL est le
    silence, qui a laissé conclure au bug pendant quatre jours.

    Même famille que #476 (cf. `connectors/readiness.py`), et même seam : une couche
    manquante se nomme ici comme là-bas. `[]` sans aucune couche manquante veut dire
    exactement une chose — rien n'est encore connecté — et ça se dit aussi.

    Fail-VISIBLE : si le diagnostic ne se lit pas, on le DIT (`reason:"unknown"`)
    plutôt que de rendre à nouveau une liste vide sans explication."""
    from ... import access
    from ...connectors import readiness as connector_readiness
    try:
        diag = connector_readiness.diagnose(
            ctx.sub, connector, org=ctx.org_id, group=access.current_group(ctx.sub))
    except Exception:
        logger.warning("diagnostic d'identités vide indisponible pour %s (fail-visible)",
                       connector, exc_info=True)
        return {"reason": "unknown",
                "next_step": (f"Liste vide, et l'état des couches (clé, option) n'a "
                              f"pas pu être lu — vérifie `{connector}` avec "
                              f"oto_connector(op='list', name='{connector}').")}
    # `pending_step` ⟹ c'est bien « aucun compte lié » : on le nomme dans le
    # vocabulaire de CETTE surface, en relayant le geste du connecteur tel quel.
    if diag is None or diag.reason == connector_readiness.PENDING_STEP:
        return {"reason": "no_identity_connected",
                "next_step": (diag.next_step if diag is not None
                              else connector_readiness.no_identity_step(
                                  ctx.sub, connector, noun))}
    return {"reason": diag.reason, "next_step": diag.next_step}


async def _set_default(ctx: ResolvedCtx, inp: SetIdentityInput) -> dict:
    _require_known_connector(inp.connector)
    _require_scope(ctx, inp.scope, write=True)
    try:
        res = connector_identities.select_identity(ctx.sub, inp.connector, inp.identity_id, inp.scope)
        if inspect.isawaitable(res):
            res = await res
    except ValueError as e:
        raise AuthzDenied(404, "unknown_identity", str(e))
    return {"connector": inp.connector, **res}


CAPABILITIES_DOC_LIST = (
    "List the connected identities/accounts your credential can act as for a connector "
    "(e.g. the LinkedIn accounts under your Unipile key, or your Google accounts), with "
    "which one is currently the default. An EMPTY list always says why: `reason` is "
    "no_credential / paid_option_off / over_quota (a layer is missing) or "
    "no_identity_connected (everything resolves, nothing linked yet), with `next_step`. "
    "Never read an empty list as a bug before reading its reason."
)
CAPABILITIES_DOC_SET = (
    "Choose which connected identity/account to act as for a connector (identity_id from "
    "connectors.identities). Unipile → picks the LinkedIn (or other channel) account; "
    "Google → sets the default account. Rejects an id not reachable by your credential."
)

from ..registry import CAPABILITIES  # noqa: E402

CAPABILITIES += [
    Capability(
        key="connectors.identities", handler=_list, Input=IdentitiesInput, authz=SUB_ONLY,
        Output=ConnectorIdentities,
        description=CAPABILITIES_DOC_LIST,
        rest=RestBinding("GET", "/api/connectors/{connector}/identities"),
    ),
    Capability(
        key="connectors.set_default_identity", handler=_set_default, Input=SetIdentityInput,
        authz=SUB_ONLY, Output=SelectedIdentity, description=CAPABILITIES_DOC_SET,
        rest=RestBinding("PUT", "/api/connectors/{connector}/identities/default"),
    ),
]
