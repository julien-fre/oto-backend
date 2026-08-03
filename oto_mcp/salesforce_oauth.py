"""Salesforce OAuth — live "Connect" flow. THE only way to obtain the
`refresh_token` now — the old manual Postman-style acquisition (paste a
Callback URL that led nowhere on our side, copy an authorization code out of
the browser's address bar, exchange it by hand) is gone: `providers.py`'s
Salesforce entry no longer declares a `refresh_token` field at all, only
`client_id`/`client_secret`/`login_url`.

Unlike every other live-OAuth flow oto has (google/atlassian/folkmcp),
Salesforce's OAuth client — a "Connected App" — is **per-customer**: each
customer creates their own inside their own org, with their own
`client_id`/`client_secret`/`login_url`. There is no platform-wide Salesforce
client oto could register once (Google/Atlassian/Folk all share ONE
Otomata-owned client). So this flow is a hybrid: the customer still saves
`client_id`/`client_secret`/`login_url` through the existing generic
`/api/settings/api-keys/salesforce` form (unchanged — those 3 fields are now
ALL of what that form collects for Salesforce), and this module's `/start`
reads THAT already-saved partial credential to build a per-customer authorize
URL, instead of a module-level constant like `google_oauth.py`'s
`GOOGLE_WORKSPACE_CLIENT_ID`. Que le credential se complète HORS formulaire est dit par `status_hints`
(`register_state` + `pending_action`, dans tools/salesforce.py) — le seam commun,
celui que Zoho utilise déjà — et non par une méthode d'auth dédiée : le jeu de
`auth_method` est fermé et consommé par un switch du dashboard.

State design mirrors `google_oauth.py` specifically (hand-rolled HMAC, not the
shared `oauth2_pkce.make_state`/`verify_state`): the credential is scoped
`(org, sub)` (a MEMBER-entity row, exactly Google's situation), so the state
must carry `org_id` in addition to `sub` — `oauth2_pkce.make_state`'s fixed
`(secret, sub, verifier)` signature has no slot for that, which is the exact
reason `google_oauth.py` itself diverged from the shared helper. This module
also needs a `scope` field ("member" | "org" | "group" — see `build_auth_url`)
that neither Google nor the shared helper need. `oauth2_pkce.pkce_pair()` IS
reused as-is for generating the PKCE pair — that one function is genuinely
generic.

PKCE is included even though Salesforce's confidential client doesn't
strictly require it: it's invisible to the customer, costs nothing, and
pre-empts a real failure mode if a security-conscious admin has already
toggled "Require PKCE" in their Connected App's OAuth policies.

Setup ops:
- Env `OTO_MCP_PUBLIC_URL` / `OTO_MCP_OAUTH_STATE_SECRET` — already used by
  every other oauth module here, no new infra needed.
- Customer-facing Callback URL to register on their Connected App:
  `https://mcp.oto.cx/api/salesforce/oauth/callback` (prod). Our own preprod
  testing uses `mcp.oto.ninja` — never hand that one to a customer.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from . import credentials_store, oauth2_pkce, oauth_flow, org_store

# Audience du state (`oauth_flow.sign_state`) : un state émis pour Salesforce ne vaut
# QUE pour le callback Salesforce. Avant la fabrique, cinq flux signaient au même
# format avec le même secret, sans discriminant — un state d'un flux passait chez un
# autre. C'est exactement ce que ce nom ferme.
_AUD = "salesforce"
_CALLBACK_PATH = "/api/salesforce/oauth/callback"

# `api` = REST API access ; `refresh_token` = long-lived refresh token issuance
# (Salesforce also accepts the synonym `offline_access`, but this is the name
# used in Salesforce's own docs/UI for the "Perform requests at any time"
# scope, so it's what we document to the customer).
SCOPES = "api refresh_token"

def _ctx_org(sub: str) -> int:
    from . import access  # lazy: avoid any import cycle at boot
    org = access.current_org(sub)
    if org is None:
        raise RuntimeError(
            "Aucune org de contexte — impossible de scoper la connexion Salesforce. "
            "Reconnecte-toi et réessaie."
        )
    return org


def _ctx_group(sub: str) -> int:
    from . import access  # lazy: avoid any import cycle at boot
    group = access.current_group(sub)
    if group is None:
        raise RuntimeError(
            "Aucune équipe active — impossible de connecter Salesforce au nom "
            "de ton équipe. Sélectionne une équipe active et réessaie."
        )
    return group


def make_state(sub: str, org_id: int, scope: str, verifier: str,
               group_id: Optional[int] = None) -> str:
    """State signé, LIÉ à l'audience `salesforce` (`oauth_flow.sign_state`).

    Ce que le payload porte de spécifique : `org` (le credential est scopé (org, sub)),
    `scope` ("member" | "org" | "group") et, pour une équipe, `group`. Le callback
    arrive SANS en-tête d'auth : ces valeurs doivent voyager avec lui, pas être
    re-dérivées d'une session vivante. `group` est gelé ici — l'équipe active au clic
    est celle où l'on écrit, même si un autre onglet en change entre-temps."""
    payload = {"sub": sub, "org": org_id, "scope": scope, "v": verifier}
    if group_id is not None:
        payload["group"] = group_id
    return oauth_flow.sign_state(_AUD, payload)


def verify_state(state: str) -> Optional[tuple[str, int, str, str, Optional[int]]]:
    """(sub, org_id, scope, verifier, group_id) si le state est valide, non expiré et
    émis POUR ce flux ; None sinon. `group_id` n'est renseigné qu'en scope `group` —
    et un payload `scope="group"` sans `group` est refusé (on ne devine pas l'équipe
    où écrire un secret)."""
    data = oauth_flow.read_state(_AUD, state)
    if not data:
        return None
    sub, org, scope, verifier, group = (data.get("sub"), data.get("org"), data.get("scope"),
                                        data.get("v"), data.get("group"))
    if (not isinstance(sub, str) or not isinstance(org, int)
            or scope not in ("member", "org", "group") or not isinstance(verifier, str)):
        return None
    if scope == "group" and not isinstance(group, int):
        return None
    return sub, org, scope, verifier, group


def _fields_entity(org_id: int, sub: str, scope: str, group_id: Optional[int] = None) -> tuple[str, str]:
    if scope == "org":
        return "org", str(org_id)
    if scope == "group":
        return "group", str(group_id)
    return credentials_store.MEMBER, credentials_store.member_id(org_id, sub)


def _read_fields(entity_type: str, entity_id: str) -> Optional[dict]:
    """The customer's already-saved partial credential (client_id/client_secret/
    login_url, and — after a first Connect — refresh_token too), or None if
    nothing has been saved yet for this entity."""
    row = credentials_store.get_credential_with_meta(entity_type, entity_id, "salesforce")
    if not row or not row.get("secret"):
        return None
    return credentials_store.unpack_secret("salesforce", row["secret"])


def read_saved_fields(sub: str, org_id: int, scope: str,
                      group_id: Optional[int] = None) -> Optional[dict]:
    """L'application à utiliser pour l'ÉCHANGE du code, au retour de Salesforce.

    Même règle qu'à l'aller (`build_auth_url`) : la ligne de ce scope si elle existe,
    sinon l'application la plus proche en remontant. Les deux DOIVENT s'accorder — le
    code a été émis pour un `client_id` précis, l'échanger avec un autre échoue.
    C'est pour ça que ce point d'entrée n'interroge plus l'entité exacte."""
    entity_type, entity_id = _fields_entity(org_id, sub, scope, group_id)
    champs = _read_fields(entity_type, entity_id)
    if champs and all(champs.get(k) for k in _APP):
        return champs
    return _read_app(org_id, sub, scope, group_id)


_APP = ("client_id", "client_secret", "login_url")


def _entites_montantes(org_id: int, sub: str, scope: str,
                       group_id: Optional[int]) -> list[tuple[str, str]]:
    """Les entités où CHERCHER l'application, du scope demandé vers le haut.

    L'application (client_id/secret/login_url) est une infrastructure d'ORG : un
    admin la pose une fois. Le refresh token, lui, est une IDENTITÉ : il appartient à
    qui consent. Les lire au même endroit obligeait chaque membre à recoller les
    identifiants de l'application de son org pour pouvoir simplement s'authentifier —
    en pratique, à connaître un secret qui ne le regarde pas.
    """
    # Construite PAR SCOPE, sans arithmétique d'index : une version calculée sur des
    # positions supposait l'équipe toujours présente et sortait des bornes sans elle
    # (donc aucune entité, donc « aucune application » sur un cas parfaitement valide).
    org = ("org", str(org_id))
    equipe = ("group", str(group_id)) if group_id else None
    if scope == "org":
        return [org]
    if scope == "group":
        return [e for e in (equipe, org) if e]
    membre = (credentials_store.MEMBER, credentials_store.member_id(org_id, sub))
    return [e for e in (membre, equipe, org) if e]


def _read_app(org_id: int, sub: str, scope: str,
              group_id: Optional[int]) -> Optional[dict]:
    """L'application COMPLÈTE la plus proche, en remontant depuis le scope demandé."""
    for etype, eid in _entites_montantes(org_id, sub, scope, group_id):
        champs = _read_fields(etype, eid)
        if champs and all(champs.get(k) for k in _APP):
            return champs
    return None


def _clean_login_url(login_url: Optional[str]) -> str:
    return (login_url or "").strip().rstrip("/") or "https://login.salesforce.com"


def build_auth_url(sub: str, scope: str = "member") -> str:
    """Authorize URL for THIS customer's Connected App — the client_id/login_url
    come from their own already-saved credential, never a module constant
    (unlike every other oauth module here, whose client is Otomata-owned).

    Raises `LookupError` if client_id/client_secret/login_url aren't saved yet
    (the `/start` route translates this into an actionable 400 the dashboard's
    Connect button can gate on) and `PermissionError` if `scope="org"`/`"group"`
    is requested by a non-admin.
    """
    if scope not in ("member", "org", "group"):
        raise ValueError(f"scope invalide : {scope!r} (attendu 'member', 'org' ou 'group')")
    org_id = _ctx_org(sub)
    group_id: Optional[int] = None
    if scope == "org":
        from . import roles
        if not roles.is_org_admin(sub, org_id):
            raise PermissionError(
                "Seul un org_admin peut connecter Salesforce au nom de toute l'org."
            )
    elif scope == "group":
        from . import roles
        group_id = _ctx_group(sub)
        if not roles.can_admin_group(sub, group_id):
            raise PermissionError(
                "Seul un chef d'équipe peut connecter Salesforce au nom de toute l'équipe."
            )
    # L'application se cherche EN CASCADE (voir `_entites_montantes`) : un membre
    # consent avec l'application de son org sans jamais en connaître les identifiants.
    # Le jeton, lui, sera écrit au scope demandé — c'est toute l'asymétrie.
    fields = _read_app(org_id, sub, scope, group_id)
    if not fields:
        raise LookupError(
            "Aucune application Salesforce n'est enregistrée à ce niveau ni au-dessus. "
            "Un administrateur doit poser le Consumer Key, le Consumer Secret et la "
            "Login URL sur la fiche du connecteur (au niveau org pour que toute "
            "l'équipe en profite), puis relance l'autorisation."
        )
    from urllib.parse import urlencode
    verifier, challenge = oauth2_pkce.pkce_pair()
    login_url = _clean_login_url(fields["login_url"])
    params = {
        "response_type": "code",
        "client_id": fields["client_id"],
        "redirect_uri": oauth_flow.redirect_uri(_CALLBACK_PATH),
        "scope": SCOPES,
        "state": make_state(sub, org_id, scope, verifier, group_id),
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return f"{login_url}/services/oauth2/authorize?{urlencode(params)}"


def exchange_code(code: str, client_id: str, client_secret: str, login_url: str,
                  verifier: str) -> dict:
    """Échange le code contre les tokens sur le endpoint DE CE CLIENT (son `login_url`,
    pas une URL Otomata fixe). La danse elle-même vit dans `oauth_flow.exchange_code`
    (corps form-encodé, jamais de secret en query string, erreur sans URL) ; ici on ne
    garde que le `code_verifier` PKCE et la traduction du message en indice actionnable
    (`_sf_error_hint`, partagé avec la sonde)."""
    from .tools.salesforce import _sf_error_hint
    try:
        return oauth_flow.exchange_code(
            f"{_clean_login_url(login_url)}/services/oauth2/token",
            code=code, client_id=client_id, client_secret=client_secret,
            redirect=oauth_flow.redirect_uri(_CALLBACK_PATH),
            extra={"code_verifier": verifier},
        )
    except oauth_flow.OAuthFlowError as e:
        raise RuntimeError(_sf_error_hint(e)) from e


async def persist_token(sub: str, org_id: int, scope: str, token_response: dict,
                        group_id: Optional[int] = None) -> dict:
    """Read-merge-write: `secret_enc` is one encrypted blob per row (no
    column-level partial update for a multi-field secret exists in
    credentials_store) — so we read back the client_id/client_secret/login_url
    saved before `/start`, merge in the refresh_token this exchange just
    produced, and re-pack the whole thing. `instance_url`/`identity_url` go in
    `meta` (unencrypted, freely mergeable later — e.g. on token refresh —
    without touching the encrypted blob), same pattern as Google's
    access_token/expires_at satellites.
    """
    # Checked before any DB read — matches folk_oauth.py's persist_token order
    # (fail fast on the more obviously wrong input, no unnecessary round trip).
    refresh_token = token_response.get("refresh_token")
    if not refresh_token:
        raise RuntimeError(
            "Salesforce n'a pas renvoyé de refresh_token. Vérifie que le scope "
            "`refresh_token` (ou `offline_access`) est coché dans les OAuth "
            "Scopes de la Connected App (Setup → App Manager → ton app → "
            "Edit Policies)."
        )
    entity_type, entity_id = _fields_entity(org_id, sub, scope, group_id)
    # `existing` = la ligne de CE scope si elle existe (on préserve ce qu'elle porte).
    # Sinon l'application vient de la cascade : c'est le cas d'un membre qui consent
    # avec l'application de son org — il n'a aucune ligne à lui avant ce moment.
    # Les identifiants d'application sont alors COPIÉS dans sa ligne, ce qui est
    # acceptable ici : régénérer le secret de l'application côté Salesforce invalide
    # de toute façon tous les jetons émis, donc impose une reconnexion à chacun.
    existing = _read_fields(entity_type, entity_id) or _read_app(
        org_id, sub, scope, group_id)
    if not existing:
        raise RuntimeError(
            "L'application Salesforce a disparu entre le clic sur Connecter et le "
            "retour de Salesforce — recommence."
        )
    merged = {**existing, "refresh_token": refresh_token}
    secret = credentials_store.pack_secret("salesforce", merged)
    meta = {
        "instance_url": token_response.get("instance_url"),
        "identity_url": token_response.get("id"),
        "connected_at": datetime.now(timezone.utc).isoformat(),
    }
    if scope == "org":
        org_store.set_org_secret(org_id, "salesforce", secret, set_by=sub, meta=meta)
    elif scope == "group":
        from . import group_store
        group_store.set_group_secret(group_id, "salesforce", secret, set_by=sub, meta=meta)
    else:
        credentials_store.set_credential(entity_type, entity_id, "salesforce", secret,
                                         set_by=sub, meta=meta)

    # PAS de sonde post-écriture. Il y en avait une — « best-effort », censée
    # confirmer que le jeton fraîchement obtenu fonctionnait. Sous rotation (RTR,
    # imposée par Salesforce), elle **détruisait** ce qu'elle vérifiait : la sonde
    # consomme le refresh token, Salesforce en renvoie un neuf, et ce chemin-ci
    # n'avait aucun moyen de l'écrire. Mesuré le 31/07, trois fois de suite —
    # jeton posé à 14:36:58.158, sondé avec succès à 14:36:58.679, mort ensuite.
    #
    # Câbler la persistance dans la sonde ne suffit pas ici : on est dans le
    # CALLBACK OAuth, une requête navigateur sans contexte authentifié (le `sub`
    # vient du state signé, pas d'un jeton), donc la sonde ne peut pas résoudre la
    # cascade pour savoir où réécrire.
    #
    # Et le coût n'achetait rien : `verified_at`/`verify_error` n'avaient AUCUN
    # lecteur — ni backend, ni dashboard. Le commentaire d'origine reconnaissait
    # déjà qu'un échec n'était jamais utilisé pour rejeter le jeton. On payait donc
    # la connexion pour un marqueur que personne ne lisait.
    #
    # L'état réel de la connexion se constate au premier usage, ou via la sonde
    # explicite (`oto_instance op=verify`), qui tourne dans un contexte authentifié
    # et persiste, elle, le jeton renouvelé.
    return {"verified": None, "verify_error": None}
