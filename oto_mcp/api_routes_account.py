"""Handlers du COMPTE — ce que le dashboard lit de la personne connectée.

- `GET /api/me`                → profil + org/équipe EFFECTIVES (seam `current_org`,
                                 ADR 0023) + rôle + statut des connecteurs + flags
- `GET /api/me/calls`          → journal des appels MCP de l'appelant, dans SON org
- `GET /api/me/activity-summary` → agrégats du même flux, fenêtre `?days=`

Ces deux dernières sont scopées `(sub, org active)` : un membre voit SA propre
activité dans l'org chargée — à ne pas confondre avec `/api/admin/monitoring/*` et
`/api/orgs/{id}/monitoring/*`, qui agrègent tout le monde et sont, eux, des
CAPACITÉS (`capabilities/monitoring.py`, `capabilities/org_monitoring.py` : mêmes
chemins, consoles MCP `oto_admin_monitoring` / `oto_org_monitoring`).

La table de routes (chemins, méthodes, ORDRE) reste assemblée dans
`api_routes.make_routes` ; ce module ne porte que les handlers.
"""
from __future__ import annotations

from fastmcp.server.auth.providers.jwt import JWTVerifier
from starlette.requests import Request
from starlette.responses import JSONResponse

from . import access, billing, db, group_store, org_store
from .api_routes_base import _authenticate, _json


async def me(request: Request, *, verifier: JWTVerifier) -> JSONResponse:
    sub, err = await _authenticate(request, verifier)
    if err:
        return err
    user = db.get_user(sub) or {}
    status = access.status_for(sub)
    # `active_org` = org EFFECTIVE (ADR 0023) : via `current_org` elle reflète
    # la consultation view-as (header X-Oto-Org) si posée, sinon la maison. Le
    # front scope ses vues là-dessus. `home_org` (ci-dessous) = le défaut brut.
    active_org = access.current_org(sub)
    active_org_name = None
    active_org_logo_url = None
    org_role = None
    active_org_require_mfa = False
    if active_org is not None:
        o = org_store.get_org(active_org)
        active_org_name = o["name"] if o else None
        # Logo EFFECTIF (upload > dérivé logo.dev du domaine déclaré).
        active_org_logo_url = org_store.effective_logo_url(o) if o else None
        org_role = org_store.get_org_role(active_org, sub)
        # MFA obligatoire de l'org (2ᵉ facteur imposé au login des membres,
        # enforcé par Logto via l'org miroir — cf. mfa_mirror).
        active_org_require_mfa = org_store.get_org_mfa(active_org)["require_mfa"]
    # Consultation d'une org tierce EN LECTURE SEULE par un opérateur plateforme :
    # org active posée (par X-Oto-Org) mais aucun rôle réel dans cette org. Le front
    # affiche un bandeau + traite l'écran en lecture (le backend rejette déjà toute
    # mutation — GET-only au middleware). Un membre a toujours un rôle → False.
    active_org_readonly = (
        active_org is not None and org_role is None
        and access.is_platform_operator(sub)
    )
    # Org perso (espace privé mono-membre) : le front adapte son vocabulaire
    # (principe 9 du CDC connecteurs — un « solo » ne lit jamais « org »/« équipe »).
    active_org_is_personal = (
        active_org is not None and org_store.is_personal_org(active_org))
    # Org MAISON (défaut persistant, colonne) — exposée distinctement pour que
    # le front affiche « ton défaut » et l'action « définir comme maison ».
    home_org = org_store.get_active_org(sub)
    home_org_name = None
    if home_org is not None and home_org != active_org:
        ho = org_store.get_org(home_org)
        home_org_name = ho["name"] if ho else None
    elif home_org is not None:
        home_org_name = active_org_name
    # Sous-palier groupe (ADR 0012) : équipe EFFECTIVE (consultation ?? maison,
    # ADR 0023) + rôle effectif (escalade). `home_group` = défaut persistant.
    active_group = access.current_group(sub)
    active_group_name = None
    group_role = None
    if active_group is not None:
        from . import roles
        g = group_store.get_group(active_group)
        active_group_name = g["name"] if g else None
        group_role = roles.effective_group_role(sub, active_group)
    home_group = group_store.get_active_group(sub)
    home_group_name = None
    if home_group is not None and home_group != active_group:
        hg = group_store.get_group(home_group)
        home_group_name = hg["name"] if hg else None
    elif home_group is not None:
        home_group_name = active_group_name
    return _json(request, {
        "sub": sub,
        "email": user.get("email"),
        "name": user.get("name"),
        "avatar_url": user.get("avatar_url"),
        # Préférence de langue de l'UI dashboard ('en'|'fr'), NULL = non définie
        # (le front retombe sur la langue du navigateur). Écrite via PUT /api/me/locale.
        "locale": user.get("locale"),
        "role": status["role"],
        "active_org": active_org,
        "active_org_name": active_org_name,
        "active_org_logo_url": active_org_logo_url,
        "org_role": org_role,
        "active_org_readonly": active_org_readonly,
        "active_org_is_personal": active_org_is_personal,
        "active_org_require_mfa": active_org_require_mfa,
        "home_org": home_org,
        "home_org_name": home_org_name,
        "active_group": active_group,
        "active_group_name": active_group_name,
        "group_role": group_role,
        "home_group": home_group,
        "home_group_name": home_group_name,
        # Feature flags par-déploiement (dark launch) : le dashboard dérive sa
        # nav de l'effet backend (ex. billing masqué en prod tant que le PSP
        # n'est pas live) — une seule source, pas de flag front dupliqué.
        "features": {"billing": billing.is_enabled()},
        # crunchbase = connecteur `personal_session` standard → exposé dans
        # `providers` (comme brevo), plus de bloc dédié (ADR 0026).
        "providers": status["providers"],
    })


def _monitoring_days(request: Request, default: int = 7) -> int:
    try:
        return int(request.query_params.get("days", str(default)))
    except ValueError:
        return default


async def me_activity_summary(request: Request, *, verifier: JWTVerifier) -> JSONResponse:
    """Activité de CE workspace pour l'utilisateur courant (MES appels dans l'org
    active), fenêtre `?days=` (défaut 7). Scopé (org active, self) → l'overview
    d'un workspace ne montre plus l'activité plateforme-wide ni celle des autres
    membres/orgs (oto/#5.2). Pas de gate admin : chacun voit sa propre activité.
    Un workspace neuf sans appel → agrégats vides (comportement attendu)."""
    sub, err = await _authenticate(request, verifier)
    if err:
        return err
    active_org = access.current_org(sub)
    return _json(request, db.tool_call_stats(
        since_days=_monitoring_days(request), org_id=active_org, sub=sub))


async def my_calls(request: Request, *, verifier: JWTVerifier) -> JSONResponse:
    """Journal des appels MCP de l'utilisateur courant (sa propre activité).
    Filtres `?limit=`/`?tool=`/`?errors=1`/`?days=`. Scopé au sub du token ET à
    l'**org active** (consultation `X-Oto-Org` ?? maison, seam `current_org`, ADR 0023)
    — un user ne voit QUE ses propres appels DANS l'org chargée (≠ /api/admin/monitoring
    qui agrège tout le monde et reste admin-only)."""
    sub, err = await _authenticate(request, verifier)
    if err:
        return err
    qp = request.query_params
    try:
        limit = int(qp.get("limit", "200"))
    except ValueError:
        limit = 200
    since_days: int | None = None
    if qp.get("days"):
        try:
            since_days = int(qp["days"])
        except ValueError:
            since_days = None
    calls = db.list_tool_calls(
        limit=limit,
        sub=sub,
        org_id=access.current_org(sub),
        tool_name=qp.get("tool") or None,
        errors_only=qp.get("errors") in ("1", "true"),
        since_days=since_days,
    )
    return _json(request, {"calls": calls})
