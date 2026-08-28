"""Les JETONS API `oto_`, et les CLÉS PLATEFORME — deux surfaces qui émettent ou
détiennent un secret.

Neuf routes écrites à la main jusqu'au 2026-08-27, portées en capacités (ADR 0009) —
mêmes chemins, mêmes codes, même corps sur le fil :

- `GET|POST /api/me/tokens` + `DELETE …/{token_id}`              → MES jetons
- `GET|POST /api/admin/users/{sub}/tokens` + `DELETE …/{token_id}` → jetons émis POUR UN TIERS
- `GET|POST /api/admin/platform-keys` + `DELETE …/{provider}/{label}` → clés plateforme

⚠️ **Les six routes de jetons portent `allow_api_token=False`, et c'est LA raison pour
laquelle elles étaient restées écrites à la main** : `_rest_adapter` ne savait pas
exprimer ce cran. Un jeton `oto_` ne peut ni lister, ni créer, ni révoquer de jeton —
sinon une fuite s'auto-entretient : révoquer le jeton fuité ne suffit plus, l'attaquant
s'en est fait un second, non expirant. Émettre un jeton reste un acte humain. Le cran
est désormais un champ du BINDING (`RestBinding.allow_api_token`), donc déclaré au même
endroit que le chemin, et vérifié par test sur les six.

**Aucune face MCP** (`mcp=None`) sur les neuf. Pour les jetons : la garde ci-dessus
n'aurait aucun sens si un outil pouvait faire le même geste. Pour les clés plateforme :
`api_key` est un secret brut, il ne passe pas en argument d'outil.

⚠️ **Trois asymétries entre le palier membre et le palier admin, toutes conservées** —
elles sont servies telles quelles et « harmoniser » casserait un appelant :
- `POST /api/me/tokens` rend **201**, `POST /api/admin/users/{sub}/tokens` rend **200** ;
- le `DELETE` membre rend `{ok}`, l'admin rend `{ok, id}` ;
- seul le palier MEMBRE refuse un tableau que l'émetteur ne voit pas ; le palier admin
  n'a pas ce garde-fou (il émet pour quelqu'un d'autre, dont le catalogue n'est pas le
  sien), et n'accepte en revanche un `ttl_days` que le palier membre ignore.
"""
from __future__ import annotations

from typing import Any, Optional, Union

from pydantic import BaseModel

from .. import access, credentials_store, db, token_scopes
from ._authz import SUB_ONLY, SUPER_ADMIN
from ._types import AuthzDenied, Capability, ResolvedCtx, RestBinding
from .registry import CAPABILITIES

_ME = "/api/me/tokens"
_ADMIN = "/api/admin/users/{sub}/tokens"
_KEYS = "/api/admin/platform-keys"
_CIBLE = {"sub": "target_sub"}          # {sub} = le sub VISÉ, pas l'appelant


# --- Entrées ----------------------------------------------------------------

class TokenListInput(BaseModel):
    """Aucun paramètre."""


class TokenCreateInput(BaseModel):
    # Absent ⇒ 'cli'. ⚠️ La réponse rend le libellé BRUT, alors que le jeton est écrit
    # avec un libellé nettoyé (`strip()[:32]`) : c'est la forme servie, on la garde.
    label: Optional[str] = None
    # Document de portée, validé par `token_scopes.parse` (jamais côté porteur). Absent
    # ⇒ jeton NON PORTÉ : il EST le sub, pleins pouvoirs. Sa forme est libre, elle est
    # décrite par `token_scopes` et refusée nommément si elle ne tient pas.
    scopes: Any = None


class TokenDeleteInput(BaseModel):
    # Texte, pas entier : la route rend `400 invalid_id`, pas le `invalid_input` de
    # pydantic. On convertit dans le handler pour garder le code servi.
    token_id: str = ""


class AdminTokenListInput(BaseModel):
    target_sub: str


class AdminTokenCreateInput(BaseModel):
    target_sub: str
    label: Optional[str] = None
    # Accepté en nombre OU en texte, et IGNORÉ s'il n'est pas un entier positif écrit en
    # chiffres (`str(x).isdigit()`) : un `-1` ou un booléen donnent « pas d'expiration »,
    # ce qui est le comportement servi.
    ttl_days: Union[int, str, None] = None
    scopes: Any = None


class AdminTokenDeleteInput(BaseModel):
    target_sub: str
    token_id: str = ""


class PlatformKeyListInput(BaseModel):
    """Aucun paramètre."""


class PlatformKeyCreateInput(BaseModel):
    provider: str = ""
    label: str = ""
    # ⚠️ Le seul secret en CLAIR de ce module. Il est chiffré au coffre et ne ressort
    # jamais : la réponse ne porte que l'identité de la clé.
    api_key: str = ""


class PlatformKeyDeleteInput(BaseModel):
    provider: str
    label: str


# --- Sorties ----------------------------------------------------------------

class ApiToken(BaseModel):
    """Un jeton, SANS son secret : celui-ci n'est rendu qu'UNE FOIS, à la création.
    `scopes: null` = jeton non porté (pleins pouvoirs du sub). `expires_at: null` =
    jeton sans expiration."""
    id: int
    label: Optional[str] = None
    created_at: Optional[Any] = None
    last_used_at: Optional[Any] = None
    expires_at: Optional[Any] = None
    scopes: Optional[dict] = None


class ApiTokenList(BaseModel):
    tokens: list[ApiToken]


class ApiTokenCreated(BaseModel):
    """⚠️ **`token` est le secret en clair, rendu UNE SEULE FOIS.** Il n'est jamais
    relisible : il n'est stocké que haché. Un client qui ne le garde pas doit en émettre
    un autre. `scopes: null` = jeton non porté."""
    token: str
    label: Optional[str] = None
    scopes: Optional[dict] = None


class AdminApiTokenCreated(ApiTokenCreated):
    """Le palier admin ajoute `ttl_days` (absent du palier membre) — `null` = pas
    d'expiration."""
    ttl_days: Optional[int] = None


class TokenDeleted(BaseModel):
    ok: bool


class AdminTokenDeleted(BaseModel):
    """Le palier admin rend l'id retiré, le palier membre non. Asymétrie historique."""
    ok: bool
    id: int


class PlatformKey(BaseModel):
    """L'IDENTITÉ d'une clé plateforme, jamais son secret : il n'est ni déchiffré ni
    rendu par cette surface."""
    provider: str
    label: Optional[str] = None
    set_at: Optional[Any] = None


class PlatformKeyList(BaseModel):
    platform_keys: list[PlatformKey]


class PlatformKeyCreated(BaseModel):
    provider: str
    label: str


class PlatformKeyDeleted(BaseModel):
    ok: bool
    provider: str
    label: str


# --- Helpers partagés -------------------------------------------------------

def _entier(brut: str) -> int:
    """`400 invalid_id` — le code servi, là où pydantic dirait `invalid_input`."""
    try:
        return int(brut)
    except (TypeError, ValueError):
        raise AuthzDenied(400, "invalid_id")


def _portee(brut: Any) -> Optional[dict]:
    try:
        return token_scopes.parse(brut)
    except token_scopes.ScopeError as e:
        raise AuthzDenied(400, "invalid_scopes", str(e))


def _cible_connue(target_sub: str) -> str:
    if not db.get_user(target_sub):
        raise AuthzDenied(404, "unknown_user")
    return target_sub


# --- Handlers : MES jetons --------------------------------------------------

def _my_list(ctx: ResolvedCtx, inp: TokenListInput) -> dict:
    return {"tokens": db.list_api_tokens(ctx.sub)}


def _my_create(ctx: ResolvedCtx, inp: TokenCreateInput) -> dict:
    label = inp.label or "cli"
    scopes = _portee(inp.scopes)
    if scopes is not None:
        # Refuser un tableau que l'ÉMETTEUR ne voit pas : le jeton ne peut de toute façon
        # pas dépasser les droits du sub, mais une faute de frappe produirait un jeton
        # muet qu'on croirait branché. Ce garde-fou n'existe qu'ici — au palier admin, le
        # catalogue visé n'est pas celui de l'émetteur.
        from ..datastore.core import make_store
        visible = {n["namespace"] for n in make_store(ctx.sub).list_namespaces()}
        missing = sorted(set(scopes["namespaces"]) - visible)
        if missing:
            raise AuthzDenied(400, "unknown_namespace",
                              f"Tableaux inconnus dans l'org active : {missing}")
    token = db.create_api_token(ctx.sub, label=label.strip()[:32], scopes=scopes)
    return {"token": token, "label": label, "scopes": scopes}


def _my_delete(ctx: ResolvedCtx, inp: TokenDeleteInput) -> dict:
    if not db.delete_api_token(ctx.sub, _entier(inp.token_id)):
        raise AuthzDenied(404, "unknown_token")
    return {"ok": True}


# --- Handlers : jetons émis POUR UN TIERS -----------------------------------

def _admin_list(ctx: ResolvedCtx, inp: AdminTokenListInput) -> dict:
    return {"tokens": db.list_api_tokens(_cible_connue(inp.target_sub))}


def _admin_create(ctx: ResolvedCtx, inp: AdminTokenCreateInput) -> dict:
    cible = _cible_connue(inp.target_sub)
    label = inp.label or "cli"
    ttl = inp.ttl_days
    ttl_days = int(ttl) if isinstance(ttl, (int, str)) and str(ttl).isdigit() else None
    scopes = _portee(inp.scopes)
    token = db.create_api_token(cible, label=label.strip()[:32], ttl_days=ttl_days,
                                scopes=scopes)
    return {"token": token, "label": label, "ttl_days": ttl_days, "scopes": scopes}


def _admin_delete(ctx: ResolvedCtx, inp: AdminTokenDeleteInput) -> dict:
    cible = inp.target_sub
    token_id = _entier(inp.token_id)
    if not db.delete_api_token(cible, token_id):
        raise AuthzDenied(404, "unknown_token")
    return {"ok": True, "id": token_id}


# --- Handlers : clés plateforme (ADR 0044 §F) -------------------------------

def _keys_list(ctx: ResolvedCtx, inp: PlatformKeyListInput) -> dict:
    # Instances scope PLATFORM du coffre unifié (plus de table `platform_keys`). Le
    # secret n'est JAMAIS déchiffré/renvoyé — identité seulement.
    return {"platform_keys": credentials_store.list_platform_credentials()}


def _keys_create(ctx: ResolvedCtx, inp: PlatformKeyCreateInput) -> dict:
    provider = (inp.provider or "").strip()
    label = (inp.label or "").strip()
    api_key = (inp.api_key or "").strip()
    if provider not in db.KEY_PROVIDERS:
        raise AuthzDenied(400, "invalid_provider")
    if not label or not api_key:
        raise AuthzDenied(400, "missing_fields")
    try:
        credentials_store.set_credential(credentials_store.PLATFORM, label, provider,
                                         api_key, set_by=ctx.sub)
    except ValueError as e:
        raise AuthzDenied(400, "invalid_platform_provider", str(e))
    return {"provider": provider, "label": label}


def _keys_delete(ctx: ResolvedCtx, inp: PlatformKeyDeleteInput) -> dict:
    provider = (inp.provider or "").strip()
    label = (inp.label or "").strip()
    # Les grants de l'instance vivent sur SA ligne (`share_down`/`meta`) → ils partent
    # avec elle, pas d'orphelin.
    if not credentials_store.clear_credential(credentials_store.PLATFORM, label, provider):
        raise AuthzDenied(404, "unknown_key")
    return {"ok": True, "provider": provider, "label": label}


_D_LIST = ("Mes jetons API, sans leur secret — il n'est rendu qu'à la création et n'est "
           "stocké que haché. `scopes: null` = jeton non porté (pleins pouvoirs de mon "
           "compte). Réservé à une session interactive : un jeton ne peut pas lister les "
           "jetons.")
_D_CREATE = ("Émet un jeton API. ⚠️ Le secret n'est rendu QU'UNE FOIS. `scopes` le BORNE "
             "à des tableaux ou projets nommés — la forme à confier à une intégration "
             "tierce ; absent, le jeton a tous mes droits. Un tableau que je ne vois pas "
             "est refusé (`unknown_namespace`) : sinon le jeton serait muet et on le "
             "croirait branché. Réservé à une session interactive.")
_D_DELETE = ("Révoque un de mes jetons. Réservé à une session interactive — un jeton ne "
             "peut pas en révoquer, sinon un attaquant couperait les jetons légitimes.")
_D_A_LIST = "Les jetons émis pour un compte TIERS. Réservé à une session interactive."
_D_A_CREATE = ("Émet un jeton POUR UN COMPTE TIERS. ⚠️ Le secret n'est rendu qu'une "
               "fois. `ttl_days` borne sa durée de vie (absent = pas d'expiration). Pas "
               "de contrôle de visibilité des tableaux ici : le catalogue visé n'est pas "
               "celui de l'émetteur. Réservé à une session interactive.")
_D_A_DELETE = "Révoque un jeton d'un compte tiers. Réservé à une session interactive."
_D_K_LIST = ("Les clés PLATEFORME posées, sans leur secret : provider, libellé, date de "
             "pose. Le secret n'est ni déchiffré ni rendu par cette surface.")
_D_K_CREATE = ("Pose une clé plateforme. Elle est chiffrée au coffre et ne ressort "
               "jamais. Refus nommés : `invalid_provider` (hors registre), "
               "`missing_fields`, `invalid_platform_provider`.")
_D_K_DELETE = ("Retire une clé plateforme. Ses grants vivent sur sa ligne : ils partent "
               "avec elle, sans orphelin.")

CAPABILITIES += [
    Capability(
        key="me.token.list", handler=_my_list, Input=TokenListInput, authz=SUB_ONLY,
        Output=ApiTokenList, description=_D_LIST, mcp=None,
        rest=RestBinding("GET", _ME, allow_api_token=False),
    ),
    Capability(
        key="me.token.create", handler=_my_create, Input=TokenCreateInput,
        authz=SUB_ONLY, Output=ApiTokenCreated, description=_D_CREATE, mcp=None,
        # 201 : forme historique de CE palier — l'admin, lui, rend 200.
        rest=RestBinding("POST", _ME, status=201, allow_api_token=False),
    ),
    Capability(
        key="me.token.delete", handler=_my_delete, Input=TokenDeleteInput,
        authz=SUB_ONLY, Output=TokenDeleted, description=_D_DELETE, mcp=None,
        rest=RestBinding("DELETE", _ME + "/{token_id}", allow_api_token=False),
    ),
    Capability(
        key="platform.token.list", handler=_admin_list, Input=AdminTokenListInput,
        authz=SUPER_ADMIN, Output=ApiTokenList, description=_D_A_LIST, mcp=None,
        rest=RestBinding("GET", _ADMIN, path_map=_CIBLE, allow_api_token=False),
    ),
    Capability(
        key="platform.token.create", handler=_admin_create,
        Input=AdminTokenCreateInput, authz=SUPER_ADMIN, Output=AdminApiTokenCreated,
        description=_D_A_CREATE, mcp=None,
        rest=RestBinding("POST", _ADMIN, path_map=_CIBLE, allow_api_token=False),
    ),
    Capability(
        key="platform.token.delete", handler=_admin_delete,
        Input=AdminTokenDeleteInput, authz=SUPER_ADMIN, Output=AdminTokenDeleted,
        description=_D_A_DELETE, mcp=None,
        rest=RestBinding("DELETE", _ADMIN + "/{token_id}", path_map=_CIBLE,
                         allow_api_token=False),
    ),
    Capability(
        key="platform.key.list", handler=_keys_list, Input=PlatformKeyListInput,
        authz=SUPER_ADMIN, Output=PlatformKeyList, description=_D_K_LIST, mcp=None,
        rest=RestBinding("GET", _KEYS),
    ),
    Capability(
        key="platform.key.create", handler=_keys_create, Input=PlatformKeyCreateInput,
        authz=SUPER_ADMIN, Output=PlatformKeyCreated, description=_D_K_CREATE,
        mcp=None,   # `api_key` est un secret brut : jamais un argument d'outil
        rest=RestBinding("POST", _KEYS),
    ),
    Capability(
        key="platform.key.delete", handler=_keys_delete, Input=PlatformKeyDeleteInput,
        authz=SUPER_ADMIN, Output=PlatformKeyDeleted, description=_D_K_DELETE, mcp=None,
        rest=RestBinding("DELETE", _KEYS + "/{provider}/{label}"),
    ),
]
