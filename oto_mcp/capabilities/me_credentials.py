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

from .. import access, providers, credentials_store, db, roles
from ._authz import SUB_ONLY
from ._types import AuthzDenied, Capability, ResolvedCtx, RestBinding
from .registry import CAPABILITIES

_PATH = "/api/settings/api-keys/{provider}"


# --- Entrées ----------------------------------------------------------------

class CredentialGetInput(BaseModel):
    provider: str
    # Palier lu — `member` (le tien, défaut), `group` ou `org` ; les deux derniers
    # exigent d'être admin du palier, exactement comme pour le retrait.
    scope: str = "member"
    # Compte NOMMÉ précis ; vide = le compte mono historique.
    account: str = ""


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
    """État d'un credential, au palier demandé. **Aucun secret n'en sort** : seuls les
    champs déclarés `reveal` (une clé d'API, pour la recopier) ou non secrets (une
    URL de base, un email) sont rendus — jamais un mot de passe, jamais un jeton.

    ⚠️ C'est ce qui rend une modification PARTIELLE praticable : on relit la config
    non-secrète pour la corriger, et le secret qu'on ne peut pas relire est complété
    côté serveur à l'écriture (#448). L'un sans l'autre était un piège."""
    model_config = ConfigDict(extra="allow")   # les champs révélables varient par connecteur
    provider: str
    configured: bool
    # Palier effectivement lu, et compte visé ('' = compte mono).
    # ⚠️ **Préfixés `read_` À DESSEIN.** Ce corps est PLAT et ses autres clés sont
    # celles du connecteur : `http` déclare un champ nommé `scope` (les scopes
    # oauth2). Un `scope:` d'enveloppe l'aurait écrasé — la valeur révélable serait
    # partie en silence, ou l'enveloppe aurait menti. Toute clé ajoutée ici doit
    # rester impossible à confondre avec un `credential_field`.
    read_scope: str = "member"
    read_account: str = ""


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

def _credentialable(provider: str, scope: str = "member"):
    """Connecteur qui accepte un credential SAISI à ce palier (registre, jamais une
    liste en dur) : un schéma de saisie, plus l'éligibilité du palier — `byo_user`
    au palier membre, org-partageable aux paliers équipe et org. Les flux dédiés
    (session navigateur, OAuth) n'ont pas de formulaire et passent ailleurs.

    ⚠️ L'éligibilité était `byo_user` QUEL QUE SOIT le palier jusqu'au 2026-08-27 :
    un connecteur purement `byo_org` — `http`, donc TOUS les ponts clients (ADR
    0003/0037) — se faisait répondre « connecteur inconnu » en lecture comme en
    retrait à l'échelle équipe ou org. C'est la racine du formulaire vide décrit
    par oto-backend#448."""
    c = providers.connector_for_provider(provider)
    if c is None or not c.secret_fields:
        return None
    eligible = (providers.is_org_shareable(provider) if scope in ("org", "group")
                else providers.is_byo_user(provider))
    return c if eligible else None


def _scoped_entity(ctx: ResolvedCtx, scope: str, org_id) -> tuple[str, str]:
    """L'entité du coffre visée par un palier, et le droit qu'elle exige.

    Source unique de la lecture et du retrait : `member` (soi, aucune condition
    au-delà du contexte d'org), `group` (admin de l'équipe ACTIVE), `org` (admin
    de l'org de contexte). L'org_admin subsume l'admin d'équipe (`can_admin_group`)."""
    if scope == "org":
        if org_id is None:
            raise AuthzDenied(400, "no_org_context", "Aucune org de contexte.")
        if not roles.is_org_admin(ctx.sub, org_id):
            raise AuthzDenied(403, "forbidden", "Admin d'org requis.")
        return credentials_store.ORG, str(org_id)
    if scope == "group":
        group_id = access.current_group(ctx.sub)
        if group_id is None:
            raise AuthzDenied(400, "no_group_context", "Aucune équipe de contexte.")
        if not roles.can_admin_group(ctx.sub, group_id):
            raise AuthzDenied(403, "forbidden", "Admin d'équipe requis.")
        return "group", str(group_id)
    if org_id is None:
        raise AuthzDenied(400, "no_org_context", "Aucune org de contexte.")
    return credentials_store.MEMBER, credentials_store.member_id(org_id, ctx.sub)


def _org_of(sub: str) -> int:
    org_id = access.current_org(sub)
    if org_id is None:
        raise AuthzDenied(400, "no_org_context", "Aucune org de contexte.")
    return org_id


# --- Handlers ---------------------------------------------------------------

_NOT_CONFIGURED = {
    "member": "Aucun credential posé pour toi.",
    "group": "Aucun credential posé pour ton équipe active.",
    "org": "Aucun credential posé pour ton org.",
}


def _get(ctx: ResolvedCtx, inp: CredentialGetInput) -> dict:
    """Les champs RÉVÉLABLES d'un credential, à n'importe quel palier.

    Mêmes règles qu'au palier membre (`reveal` ou non-secret), même coffre : ce qui
    change est l'entité lue et le droit exigé. Jusqu'au 2026-08-27, l'entité était
    `MEMBER` EN DUR — un credential d'équipe ou d'org n'avait donc aucune surface
    capable d'en rendre la `base_url`, alors que le registre la déclare `reveal=True`
    (oto-backend#448). Le formulaire vide n'était pas un choix d'UI : le backend
    n'avait rien à servir."""
    scope = (inp.scope or "member").strip() or "member"
    c = _credentialable(inp.provider, scope)
    if c is None:
        raise AuthzDenied(404, "unknown_provider", f"Connecteur inconnu : `{inp.provider}`.")
    org_id = access.current_org(ctx.sub)
    if org_id is None and scope == "member":
        # Historique : sans org de contexte, la lecture de SA clé dit « rien de posé »
        # plutôt que de refuser — on ne change pas cette réponse-là.
        raise AuthzDenied(404, "not_configured", _NOT_CONFIGURED["member"])
    entity_type, entity_id = _scoped_entity(ctx, scope, org_id)
    account = (inp.account or "").strip()
    secret = credentials_store.get_credential(
        entity_type, entity_id, inp.provider, account=account)
    if not secret:
        raise AuthzDenied(404, "not_configured", _NOT_CONFIGURED.get(scope, _NOT_CONFIGURED["member"]))
    fields = credentials_store.unpack_secret(inp.provider, secret)
    out: dict = {"provider": inp.provider, "configured": True}
    for f in c.secret_fields:
        if f.reveal or not f.secret:
            out[f.name] = fields.get(f.name)
    # Après les champs du connecteur, et sous des noms qui ne peuvent pas en être
    # un — cf. la note de `CredentialState`.
    out["read_scope"], out["read_account"] = scope, account
    return out


async def _set(ctx: ResolvedCtx, inp: CredentialSetInput) -> dict:
    from mcp.shared.exceptions import McpError
    from .. import status_hints
    from ..connectors import verify as connector_verify

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
    db.upsert_user(ctx.sub)
    account = (body.get("account") or "").strip()
    # Scope MEMBRE (ADR 0033) : la clé est posée DANS l'org de contexte — poser en
    # consultant une org, c'est scoper cette org.
    org_id = _org_of(ctx.sub)
    eid = credentials_store.member_id(org_id, ctx.sub)
    # Écriture PARTIELLE (#448) : les champs absents du corps sont complétés par le
    # coffre, côté serveur. Un champ envoyé vide reste vide — c'est un effacement
    # explicite, et le formulaire du dashboard, qui poste tous ses champs, ne change
    # pas de comportement.
    merged = credentials_store.merge_with_existing(
        credentials_store.MEMBER, eid, inp.provider, account, body)
    # Validation de saisie — SOURCE UNIQUE des trois paliers (membre ici, équipe et
    # org via `secret_from_input`) : jeu fermé des valeurs, champs que le
    # discriminant rend pertinents, `required` non vides parmi eux. Un champ
    # facultatif (connecteur « ET/OU » type slack) reste omissible, mais il faut au
    # moins un champ au total.
    try:
        fields = credentials_store.validate_fields(inp.provider, merged)
    except ValueError as e:
        raise AuthzDenied(400, getattr(e, "code", str(e)),
                          getattr(e, "message", "Credential incomplet ou vide."))
    # Garde de pose (#409, source unique des trois surfaces déclaratives) : cohérence
    # des noms si le connecteur est multi-compte, refus nommé s'il est mono.
    try:
        credentials_store.guard_account_write(
            credentials_store.MEMBER, eid, inp.provider, account, org=org_id)
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
    scope = (inp.scope or "member").strip() or "member"
    if scope not in ("member", "group", "org"):
        scope = "member"
    c = providers.connector_for_provider(inp.provider)
    eligible = (providers.is_org_shareable(inp.provider) if scope in ("org", "group")
                else providers.is_byo_user(inp.provider))
    if c is None or not eligible:
        raise AuthzDenied(404, "unknown_provider", f"Connecteur inconnu : `{inp.provider}`.")
    org_id = _org_of(ctx.sub)
    account = (inp.account or "").strip()
    entity_type, entity_id = _scoped_entity(ctx, scope, org_id)
    credentials_store.clear_credential(entity_type, entity_id, inp.provider,
                                       account=account)
    return {"ok": True, "provider": inp.provider, "account": account, "scope": scope}


_DOC_SET = (
    "Pose (ou met à jour) TON credential pour un connecteur, dans l'org de contexte. "
    "Les champs ABSENTS du corps sont complétés par ce qui est déjà au coffre, côté "
    "serveur — changer une URL sans repasser la clé est un geste d'un champ. Un champ "
    "envoyé VIDE est un effacement explicite, pas une omission. "
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
    "L'état d'un credential pour un connecteur : posé ou non, et les seuls champs "
    "révélables (une URL de base à corriger, une clé d'API à recopier, un email). Un "
    "secret ne se relit jamais. `scope` : `member` (le tien, défaut), `group` ou "
    "`org` — ces deux-là exigent d'être admin du palier, comme pour le retrait. "
    "`account` cible un compte nommé précis ; vide = le compte unique."
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
