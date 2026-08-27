"""Credential PERSONNEL d'un connecteur : le poser, en lire l'état, le retirer.

Trois routes écrites à la main jusqu'au 2026-08-27, portées en capacités (ADR 0009)
— mêmes chemins, mêmes codes, même corps sur le fil. Ce qui change est ce que la
surface DIT d'elle-même : c'est par ici que tout le monde branche ses clés, et
l'OpenAPI dérivé n'en décrivait rien (`_legacy`, « forme du corps non dérivable »),
donc un intégrateur ne pouvait pas savoir qu'on pose un second compte avec `account`.

**Pas de face MCP** (`mcp=None`) : un secret brut ne passe jamais en argument d'outil,
il transiterait dans le contexte du modèle. C'est la règle du repo, pas un oubli.

⚠️ **Le corps du POST est LIBRE par nature** : ses clés sont les `credential_fields`
du connecteur visé (`GET /api/connectors` les publie), plus `account`. Aucun `Input`
statique ne peut les énumérer — d'où `body_field="fields"` : le corps ENTIER devient
la valeur d'un champ déclaré, et la garde « champ inconnu » continue de couvrir la
query string et les paramètres de chemin.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict

from .. import access, connectors, credentials_store, db, roles
from ._authz import SUB_ONLY
from ._types import AuthzDenied, Capability, ResolvedCtx, RestBinding
from .registry import CAPABILITIES

_PATH = "/api/settings/api-keys/{provider}"


# --- Entrées ----------------------------------------------------------------

class CredentialGetInput(BaseModel):
    provider: str


class CredentialSetInput(BaseModel):
    provider: str
    # Le corps entier (cf. `body_field`) : les champs déclarés par le connecteur,
    # plus `account` (nom du compte visé, absent = le compte mono historique).
    fields: dict[str, str] = {}


class CredentialClearInput(BaseModel):
    provider: str
    # Niveau de l'instance à retirer — `org`/`group` exigent d'être admin du palier.
    scope: str = "member"
    # Compte NOMMÉ précis ; vide = le compte mono historique.
    account: str = ""


# --- Sorties ----------------------------------------------------------------

class CredentialState(BaseModel):
    """État du credential personnel. **Aucun secret n'en sort** : seuls les champs
    déclarés `reveal` (une clé d'API, pour la recopier) ou non secrets (un email)
    sont rendus — jamais un mot de passe, jamais un jeton."""
    model_config = ConfigDict(extra="allow")   # les champs révélables varient par connecteur
    provider: str
    configured: bool


class CredentialSaved(BaseModel):
    """Credential posé et chiffré. ⚠️ `verified: false` ne veut pas dire « cassé » :
    le connecteur n'a peut-être aucune sonde, ou la pose est volontairement
    incomplète (connexion en deux temps — `pending_action` dit alors quoi faire)."""
    ok: bool
    provider: str
    org_id: int
    # Le compte posé ('' = compte mono). Un connecteur qui ne résout pas les comptes
    # nommés REFUSE un `account` non vide (400 `single_account_connector`).
    account: str
    verified: bool
    pending_action: Optional[str] = None


class CredentialCleared(BaseModel):
    ok: bool
    provider: str
    account: str
    scope: str


# --- Garde partagée ---------------------------------------------------------

def _credentialable(provider: str):
    """Connecteur qui accepte un credential PERSONNEL saisi (registre, jamais une
    liste en dur) : `byo_user` avec un schéma de saisie. Les flux dédiés (session
    navigateur, OAuth) n'ont pas de formulaire et passent ailleurs."""
    c = connectors.connector_for_provider(provider)
    if c is None or not connectors.is_byo_user(provider) or not c.secret_fields:
        return None
    return c


def _org_of(sub: str) -> int:
    org_id = access.current_org(sub)
    if org_id is None:
        raise AuthzDenied(400, "no_org_context", "Aucune org de contexte.")
    return org_id


# --- Handlers ---------------------------------------------------------------

def _get(ctx: ResolvedCtx, inp: CredentialGetInput) -> dict:
    c = _credentialable(inp.provider)
    if c is None:
        raise AuthzDenied(404, "unknown_provider", f"Connecteur inconnu : `{inp.provider}`.")
    org_id = access.current_org(ctx.sub)
    secret = (credentials_store.get_credential(
                  credentials_store.MEMBER,
                  credentials_store.member_id(org_id, ctx.sub), inp.provider)
              if org_id is not None else None)
    if not secret:
        raise AuthzDenied(404, "not_configured", "Aucun credential posé pour toi.")
    fields = credentials_store.unpack_secret(inp.provider, secret)
    out: dict = {"provider": inp.provider, "configured": True}
    for f in c.secret_fields:
        if f.reveal or not f.secret:
            out[f.name] = fields.get(f.name)
    return out


async def _set(ctx: ResolvedCtx, inp: CredentialSetInput) -> dict:
    from mcp.shared.exceptions import McpError
    from .. import connector_verify, status_hints

    c = _credentialable(inp.provider)
    if c is None:
        raise AuthzDenied(404, "unknown_provider", f"Connecteur inconnu : `{inp.provider}`.")
    # RBAC connecteur (ADR 0025) : aligner la POSE sur l'USAGE — un membre non autorisé
    # sur un connecteur RESTREINT dans son org ne peut pas poser de clé perso (sinon une
    # clé inerte serait posable hors UI). Même seam que la résolution.
    try:
        access.require_connector_access(inp.provider, ctx.sub)
    except McpError as e:
        raise AuthzDenied(403, "connector_restricted", e.error.message)

    body = inp.fields
    # Chaque champ `required` doit être non vide ; un champ facultatif (connecteur
    # « ET/OU » type slack) peut être omis, mais il faut au moins un champ au total.
    fields: dict[str, str] = {}
    missing: list[str] = []
    for f in c.secret_fields:
        val = credentials_store.clean_field_value(f, body.get(f.name))
        if not val:
            if f.required:
                missing.append(f.label or f.name)
            continue
        fields[f.name] = val
    # NOMMER le champ manquant : un « missing_credentials » sec oblige à deviner lequel
    # des cinq champs bloque — vécu 28/07, un `data_center` vide a fait échouer six
    # tentatives de pose sans que rien ne le dise.
    if missing:
        raise AuthzDenied(400, "missing_credentials",
                          "champ(s) requis vide(s) : " + ", ".join(missing))
    if not fields:
        raise AuthzDenied(400, "missing_credentials", "aucun champ renseigné.")

    db.upsert_user(ctx.sub)
    account = (body.get("account") or "").strip()
    # Scope MEMBRE (ADR 0033) : la clé est posée DANS l'org de contexte — poser en
    # consultant une org, c'est scoper cette org.
    org_id = _org_of(ctx.sub)
    eid = credentials_store.member_id(org_id, ctx.sub)
    # Garde de pose (#409, source unique des trois surfaces déclaratives) : cohérence
    # des noms si le connecteur est multi-compte, refus nommé s'il est mono.
    try:
        credentials_store.guard_account_write(
            credentials_store.MEMBER, eid, inp.provider, account)
    except credentials_store.NamedAccountRequired as e:
        raise AuthzDenied(409, "account_required", str(e))
    except credentials_store.SingleAccountConnector as e:
        raise AuthzDenied(400, "single_account_connector", str(e))

    # Connexion en DEUX temps : le formulaire ne collecte que les PRÉREQUIS, le champ
    # décisif (refresh_token) arrive par le consentement. Sans reprise, une simple
    # correction de champ après connexion repackerait un blob SANS lui — l'UI dirait
    # « enregistré » et le connecteur casserait au 1er appel d'outil.
    if status_hints.credential_state(inp.provider, fields) is not None:
        declared = {f.name for f in c.secret_fields}
        prior = credentials_store.get_credential_with_meta(
            credentials_store.MEMBER, eid, inp.provider, account=account) or {}
        if prior.get("secret"):
            fields = {**{k: v for k, v in
                         credentials_store.unpack_secret(inp.provider, prior["secret"]).items()
                         if k not in declared and v},
                      **fields}

    # Verify-avant-persist (#106) : un credential qui n'authentifie pas n'est jamais
    # persisté — l'erreur remonte à la SAISIE, pas au premier appel d'outil.
    # ⚠️ SAUF pose volontairement incomplète (l'app OAuth posée, le consentement à
    # venir) : la sonde échouerait PAR CONSTRUCTION et créerait un blocage circulaire
    # (vécu 28/07, six poses Zoho rejetées sans chemin de sortie).
    st = status_hints.credential_state(inp.provider, fields)
    pending = st is not None and not st.complete
    verified = False
    if connector_verify.supports(inp.provider) and not pending:
        try:
            await connector_verify.run(inp.provider, fields)
        except McpError as e:
            raise AuthzDenied(400, "verify_failed", e.error.message)
        except Exception as e:  # noqa: BLE001 — l'échec d'auth EST le résultat
            raise AuthzDenied(400, "verify_failed", str(e))
        verified = True

    secret = credentials_store.pack_secret(inp.provider, fields)
    meta = None
    if verified:
        from datetime import datetime, timezone
        meta = {"verified_at": datetime.now(timezone.utc).isoformat()}
    credentials_store.set_credential(
        credentials_store.MEMBER, eid, inp.provider, secret, set_by=ctx.sub,
        account=account, meta=meta)
    return {"ok": True, "provider": inp.provider, "org_id": org_id, "account": account,
            "verified": verified,
            "pending_action": st.next_action if pending else None}


def _clear(ctx: ResolvedCtx, inp: CredentialClearInput) -> dict:
    # Effacer est générique : tout connecteur `byo_user`, y compris une session
    # navigateur sans champ de saisie (brevo/crunchbase) — on ne dépend donc PAS de
    # `secret_fields` comme la lecture et la pose.
    c = connectors.connector_for_provider(inp.provider)
    if c is None or not connectors.is_byo_user(inp.provider):
        raise AuthzDenied(404, "unknown_provider", f"Connecteur inconnu : `{inp.provider}`.")
    org_id = _org_of(ctx.sub)
    account = (inp.account or "").strip()
    scope = (inp.scope or "member").strip()

    if scope == "org":
        if not roles.is_org_admin(ctx.sub, org_id):
            raise AuthzDenied(403, "forbidden", "Admin d'org requis.")
        credentials_store.clear_credential(credentials_store.ORG, str(org_id),
                                           inp.provider, account=account)
    elif scope == "group":
        group_id = access.current_group(ctx.sub)
        if group_id is None:
            raise AuthzDenied(400, "no_group_context", "Aucune équipe de contexte.")
        if not roles.can_admin_group(ctx.sub, group_id):
            raise AuthzDenied(403, "forbidden", "Admin d'équipe requis.")
        credentials_store.clear_credential("group", str(group_id), inp.provider,
                                           account=account)
    else:
        scope = "member"
        credentials_store.clear_credential(
            credentials_store.MEMBER, credentials_store.member_id(org_id, ctx.sub),
            inp.provider, account=account)
    return {"ok": True, "provider": inp.provider, "account": account, "scope": scope}


_DOC_SET = (
    "Pose (ou remplace) TON credential pour un connecteur, dans l'org de contexte. "
    "Le corps est un objet plat dont les clés sont les `credential_fields` du "
    "connecteur — publiés par `GET /api/connectors` — plus, optionnellement, "
    "`account` : le NOM du compte visé quand le connecteur en porte plusieurs (un "
    "workspace Slack, une organisation Zoho ; le mot d'usage est dans "
    "`auth.account_noun`). Sans `account`, c'est le compte unique. Au premier compte "
    "nommé, le compte anonyme existant est renommé ; ensuite une pose anonyme est "
    "refusée (409 `account_required`), et un compte nommé sur un connecteur "
    "mono-compte l'est aussi (400 `single_account_connector`). Le credential est "
    "testé AVANT d'être écrit quand le connecteur expose une sonde."
)
_DOC_GET = (
    "L'état de TON credential pour un connecteur : posé ou non, et les seuls champs "
    "révélables (une clé d'API à recopier, un email). Un secret ne se relit jamais."
)
_DOC_CLEAR = (
    "Retire un credential. `scope` : `member` (le tien, défaut), `org` ou `group` — "
    "ces deux-là exigent d'être admin du palier. `account` cible un compte nommé "
    "précis ; vide = le compte unique."
)

CAPABILITIES += [
    Capability(
        key="me.credential.get", handler=_get, Input=CredentialGetInput,
        authz=SUB_ONLY, Output=CredentialState, description=_DOC_GET,
        mcp=None,   # un secret ne passe pas en argument d'outil
        rest=RestBinding("GET", _PATH),
    ),
    Capability(
        key="me.credential.set", handler=_set, Input=CredentialSetInput,
        authz=SUB_ONLY, Output=CredentialSaved, description=_DOC_SET,
        mcp=None,
        rest=RestBinding("POST", _PATH, body_field="fields"),
    ),
    Capability(
        key="me.credential.clear", handler=_clear, Input=CredentialClearInput,
        authz=SUB_ONLY, Output=CredentialCleared, description=_DOC_CLEAR,
        mcp=None,
        rest=RestBinding("DELETE", _PATH),
    ),
]
