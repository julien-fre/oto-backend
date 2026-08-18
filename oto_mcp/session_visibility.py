"""Calcul + application de la visibilité des tools d'une session MCP.

Extrait de `middleware.UserDisabledToolsMiddleware` (ADR 0009/0011/0015) pour
être **rejoué après une bascule de profil** (org/groupe actif) sans dupliquer la
logique — « derive don't duplicate ».

Deux appelants :
- **handshake** : le middleware appelle `apply_session_visibility(ctx, sub)` à
  `on_initialize` (reset=False — comportement historique : juste poser la denylist).
- **bascule à chaud** : l'adaptateur capacité (`capabilities._mcp_adapter`) l'appelle
  après `oto_use_org`/`oto_clear_org`/… avec `reset=True` pour repartir de l'état
  « tout visible » puis re-poser la denylist de la NOUVELLE org. fastmcp émet alors
  `tools/list_changed` à la session courante (cf. `disable_components`/`reset_visibility`).
"""
from __future__ import annotations

import logging

from fastmcp.server.transforms.visibility import disable_components, reset_visibility

from . import (access, connector_activation, connector_selection, connectors,
               credentials_store, db, org_store, providers)
from .error_taxonomy import _is_client_disconnect
from .tool_visibility import (
    DEFAULT_HIDDEN_TOOLS,
    effective_disabled,
    is_protected,
    namespace_of,
)

logger = logging.getLogger(__name__)

# Sentinelle « dérive l'org de current_org » (défaut) — distincte de org=None/0
# (perso/global), qui est une valeur LÉGITIME.
_DERIVE_ORG = object()


async def compute_hidden_tools(ctx, sub: str, *, org=_DERIVE_ORG) -> set[str]:
    """Ensemble effectif des tools à masquer pour `(sub, org active)`.

    Profil de visibilité = (sub, org active) ; 0 = perso/global (ADR 0015). Lit
    l'org active à CHAQUE appel → après `set_active_org`, recalcule pour la
    nouvelle org. `ctx` = `Context` fastmcp (pour `ctx.fastmcp.list_tools`).

    `org` = org de scope EXPLICITE (défaut = dérive de `current_org(sub)`, le
    comportement handshake/bascule à chaud MCP). À passer quand la vue doit
    refléter une org CONSULTÉE précise plutôt que re-dériver le contexte de
    l'acteur — ex. la carte contexte du dashboard, qui affichait sinon la
    sélection GLOBALE (org 0) au lieu de l'org consultée (oto/#5.3)."""
    try:
        # Les toggles perso sont scopés par org → on lit ceux de l'org active.
        active_org = access.current_org(sub) if org is _DERIVE_ORG else org
        prof_org = active_org or 0
        disabled = set(db.list_user_disabled_tools(sub, prof_org))
        enabled_override = set(db.list_user_enabled_tools(sub, prof_org))
        is_admin = access.is_super_admin(sub)
    except Exception as e:
        # Sur erreur DB : repli neutre (rien de désactivé). La sécurité d'accès ne
        # dépend PAS de cette visibilité — elle est gardée au call-time (credential
        # + require_connector_access ADR 0025 + activation + remote credential).
        logger.warning("Cannot read tool visibility for %s: %s", sub, e)
        disabled, enabled_override, is_admin = set(), set(), False
        active_org, prof_org = None, 0
    try:
        all_tools = await ctx.fastmcp.list_tools(run_middleware=False)
        all_names = {t.name for t in all_tools}
    except Exception as e:
        logger.warning("Cannot list tools for %s: %s", sub, e)
        # repli FAIL-CLOSED : disabled explicites + masqués-par-défaut
        # (sinon ils resteraient visibles, denylist incomplète).
        all_names = disabled | DEFAULT_HIDDEN_TOOLS
    # Denylist ADMIN (org + équipe active) : gouvernance de visibilité au grain
    # TOOL, PAS une barrière de sécurité (ADR 0031) — l'override perso positif lu
    # ci-dessus (`enabled_override`) la lève toujours, `effective_disabled` en
    # décide via `is_tool_visible`. Fail-OPEN INDÉPENDANT par palier (miroir de
    # `require_connector_access`) : un hoquet sur l'équipe ne doit pas priver
    # l'org de son denylist, et inversement.
    admin_hidden: set[str] = set()
    try:
        admin_hidden |= access.org_admin_hidden_tools(active_org)
    except Exception as e:
        logger.warning("org tool denylist skipped for %s (fail-open): %s", sub, e)
    try:
        admin_hidden |= access.group_admin_hidden_tools(access.current_group(sub))
    except Exception as e:
        logger.warning("group tool denylist skipped for %s (fail-open): %s", sub, e)
    to_hide = effective_disabled(all_names, disabled, enabled_override, frozenset(admin_hidden))
    # Activation (ADR 0011) : masque les tools d'un connecteur non activé pour
    # l'org de la session — à chaud, per-org. Fail-OPEN (gouvernance d'exposition,
    # pas une barrière de sécurité ; le grant-only reste fail-closed ci-dessus).
    # Les tools plateforme (oto/data/doctrine) n'ont pas de connecteur au
    # registre → jamais gatés.
    try:
        exposed = connector_activation.exposed_connectors(active_org)
        # Tier ÉQUIPE (ADR 0012, restrict-only) : l'équipe active peut COUPER un
        # connecteur pour ses membres — on retranche ses coupures de l'exposé (jamais
        # d'ajout : invariant monotone). Même régime fail-open que l'org.
        active_group = access.current_group(sub)
        if active_group is not None:
            exposed = connector_activation.effective_for_group(
                exposed, connector_activation.group_cut_connectors(active_group))
        to_hide |= {
            n for n in all_names
            if (c := connectors.connector_for_namespace(namespace_of(n))) is not None
            and c.name not in exposed
        }
    except Exception as e:
        logger.warning("activation visibility skipped for %s (fail-open): %s", sub, e)
    # (La règle dédiée « bridges remote per-namespace » a été retirée — ADR 0034 B4 :
    # le connecteur `bridge` universel suit le régime commun ci-dessus ; sans
    # credential, l'exécution lève proprement.)
    # RBAC connecteur interne à l'org (ADR 0025) : un connecteur RESTREINT dans
    # l'org active est masqué pour un membre non autorisé (département/user). Le
    # backstop DUR est au call-time (`resolve_credential` → `require_connector_access`) ;
    # ici = ergonomie (best-effort, fail-OPEN sur glitch — le call-time garantit).
    # Seam unique `rbac_denied_connectors` (escalade super_admin + org_admin incluse).
    try:
        deny = access.rbac_denied_connectors(sub, active_org)
        if deny:
            to_hide |= {
                n for n in all_names
                if (c := connectors.connector_for_namespace(namespace_of(n))) is not None
                and c.name in deny
            }
    except Exception as e:
        logger.warning("org connector RBAC visibility skipped for %s (fail-open): %s", sub, e)
    # RBAC connecteur au grain ÉQUIPE (ADR 0012 B2) : l'équipe ACTIVE peut réserver un
    # connecteur à un sous-ensemble de ses membres — masqué pour les autres (narrowing
    # de l'org). Backstop DUR au call-time (`require_connector_access`) ; ici ergonomie
    # (best-effort, fail-OPEN).
    try:
        g_deny = access.group_rbac_denied_connectors(sub, access.current_group(sub))
        if g_deny:
            to_hide |= {
                n for n in all_names
                if (c := connectors.connector_for_namespace(namespace_of(n))) is not None
                and c.name in g_deny
            }
    except Exception as e:
        logger.warning("group connector RBAC visibility skipped for %s (fail-open): %s", sub, e)
    # Sélection marketplace (ADR 0019/0050) : régime NOMINAL « non-sélectionné =
    # masqué ». Un connecteur en PAUSE ou non-installé masque ses tools. Le seed
    # de la 1re session d'un (sub, org) installe le socle `default_active` ∩ exposé
    # — VIDE depuis le 16/07 : un nouveau compte démarre SANS connecteurs installés,
    # l'agent guide depuis les tools spine + le catalogue injecté (bloc A). Les
    # pairs pré-0050 ont été backfillés avec leur visible d'alors (db._init).
    # Depuis peu : la baseline PLATEFORME est complétée par la baseline PROPRE à
    # l'org active (`org_store.get_org_default_connectors`, ex-« recommended »,
    # posée par `connectors.recommend`) — un org_admin peut donc faire démarrer
    # SES nouveaux membres avec un socle non-vide, sans toucher au socle plateforme.
    # Fail-OPEN sur glitch (ergonomie, jamais une barrière : les gates call-time
    # restent) ; `oto_call` = échappatoire d'appel ponctuel d'un tool non listé
    # (ADR 0036).
    try:
        if not connector_selection.is_seeded(sub, prof_org):
            org_defaults = set(org_store.get_org_default_connectors(active_org) or []) if active_org else set()
            connector_selection.seed_active(
                sub,
                (providers.DEFAULT_ACTIVE_CONNECTORS | org_defaults)
                & connector_activation.exposed_connectors(active_org),
                prof_org)
        _sel = connector_selection.list_selection(sub, prof_org)
        to_hide |= {
            n for n in all_names
            if (c := connectors.connector_for_namespace(namespace_of(n))) is not None
            and _sel.get(c.name) != connector_selection.ACTIVE
        }
    except Exception as e:
        logger.warning("selection visibility skipped for %s (fail-open): %s", sub, e)
    # Tools réservés au platform admin (`oto_admin_*`) : masqués aux non-admins.
    # Inutiles à un user normal (l'autz les refuse à l'appel) → ils ne font
    # qu'alourdir le contexte. Visibilité seulement ; l'autz PLATFORM_ADMIN reste
    # enforced au call-time (jamais une barrière ici).
    if not is_admin:
        to_hide |= {n for n in all_names if n.startswith("oto_admin_")}
    # Garde anti-lockout STRUCTUREL (signal d’usage #213) : AUCUN bloc de gating ci-dessus
    # (connecteur/RBAC/sélection/admin) ne peut masquer un tool SPINE/protégé. Jusqu'ici
    # le spine n'était sauvé que parce que son namespace ne résolvait aucun connecteur
    # (effet de bord fragile : un connecteur déclarant `oto`/`data` aurait tout évincé).
    # Ici c'est explicite et robuste — source unique `is_protected`.
    to_hide -= {n for n in all_names if is_protected(n)}
    return to_hide


def _log_visibility_failure(quoi: str, sub: str, e: BaseException) -> None:
    """Journalise un échec de pose de visibilité au bon NIVEAU.

    Poser la visibilité, c'est pousser une notification `tools/list_changed` sur le
    stream de la session. Quand le client a déjà fermé (nos workers runner ferment le
    POST sitôt le corps lu), le push lève une déconnexion — **attendu, rien à corriger** :
    337 de ces warnings en 2 h le 15/08 (#352), aucun actionnable, et ils noyaient le
    reste du journal. Cette classe passe donc en `debug`.

    Toute AUTRE cause reste un `warning` : un échec de visibilité pour une vraie raison
    (registre, DB, bug) est un fait à voir — la session tourne alors avec une toolbox
    plus large que prévu."""
    if _is_client_disconnect(e):
        logger.debug("Failed to %s tool visibility for %s (client parti): %s", quoi, sub, e)
    else:
        logger.warning("Failed to %s tool visibility for %s: %s", quoi, sub, e)


async def apply_session_visibility(ctx, sub: str, *, reset: bool = False) -> None:
    """Calcule la denylist de `(sub, org active)` et la pose sur la session `ctx`.

    `reset=False` (handshake) : pose seulement la denylist (comportement
    historique). `reset=True` (bascule à chaud) : remet d'abord tout visible
    (`reset_visibility`) pour effacer la denylist de l'ANCIENNE org, puis re-pose
    celle de la nouvelle — fastmcp émet `tools/list_changed` à la session."""
    to_hide = await compute_hidden_tools(ctx, sub)
    if reset:
        try:
            await reset_visibility(ctx)
        except Exception as e:
            _log_visibility_failure("reset", sub, e)
    if not to_hide:
        return
    try:
        await disable_components(ctx, names=to_hide, components={"tool"})
    except Exception as e:
        _log_visibility_failure("apply", sub, e)
