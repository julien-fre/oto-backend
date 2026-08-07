"""Capacités « sélection de connecteurs » — marketplace (ADR 0019).

Per-membre, scopé à l'org active (`SUB_ONLY` injecte `ctx.org_id`). Trois faits
distincts (cf. `connector_selection`) : exposition (`connector_activation`, plafond),
proposition (`orgs.default_connectors`), sélection (`user_selected_connectors`).

- `connectors.me` (lecture) = catalogue exposé pour l'org active, fusionné avec
  l'état per-membre (`not_selected` | `active` | `paused`) + `recommended` (baseline org).
  Source unique consommée par le dashboard (library + « mes connecteurs »).
- `connectors.select` / `.pause` / `.unselect` (mutation) = installe / met en pause /
  retire un connecteur. Garde : refuser un connecteur non-exposé pour l'org active
  (le plafond d'exposition `connector_activation` n'est jamais relâché).

Handlers SYNC (les adaptateurs n'awaitent pas). Régime NOMINAL (ADR 0050) :
« non-sélectionné = masqué » — le seed d'un nouveau (sub, org) installe le socle
curé `default_active` ; sélectionner/mettre en pause a un effet de visibilité à la
session suivante (`session_visibility`).
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from .. import access, connector_activation, connector_selection, org_store, providers, tool_registry
from ._authz import ORG_ADMIN_OF, SUB_ONLY
from ._types import AuthzDenied, Capability, ResolvedCtx, RestBinding
from .registry import CAPABILITIES

# Mapping placeholder de route {id} → champ Input `org_id` (routes réelles en {id}).
_ID = {"id": "org_id"}


class MyConnectorsInput(BaseModel):
    """Filtre/projection de `connectors.me`. Défaut = **compact** (identité + état) :
    la vue pleine (doc_sections/auth/credential_fields/…) gonfle le payload à ~90 KB
    pour 55 connecteurs et dépasse le plafond de tokens MCP (oto-backend#109).

    `name` = lecture d'état d'UN connecteur. Il était déclaré sur `oto_connector`
    (pour select/pause/…) mais **ignoré en silence** sur op=list → l'agent qui le
    passait recevait le catalogue entier (~30k tokens en verbose) sans le moindre
    warning (feedback #326). Un `name` qui ne matche rien lève, jamais une liste
    vide : un filtre muet est ce qui a coûté le contexte."""
    verbose: bool = False                # True = payload complet (dashboard / setup credential)
    state: Optional[str] = None          # filtre : not_selected | active | paused
    name: Optional[str] = None           # filtre : UN connecteur (lecture d'état ciblée)


class ConnectorActionInput(BaseModel):
    name: str                            # nom de connecteur (registre providers.py)


class RecommendInput(BaseModel):
    org_id: int                          # injecté du placeholder {id} (ORG_ADMIN_OF)
    connectors: list[str] = []           # baseline proposée ; [] = aucune recommandation


class BulkSelectInput(BaseModel):
    org_id: int
    name: str                            # connecteur (placeholder {name}, auto-mappé)


class UnsetDefaultInput(BaseModel):
    org_id: int
    name: str                            # connecteur (placeholder {name}, auto-mappé)


def _visible_catalog(ctx: ResolvedCtx) -> list[dict]:
    """Catalogue exposé pour l'org active du caller — miroir du filtrage de
    `api_routes.connectors_catalog` : activation (plafond) + RBAC org (ADR 0025).
    L'admin plateforme voit tout l'exposé."""
    exposed = connector_activation.exposed_connectors(ctx.org_id)
    is_admin = access.is_platform_operator(ctx.sub)
    # RBAC connecteur interne à l'org (ADR 0025) : un connecteur restreint dans l'org
    # n'apparaît dans la marketplace du membre que s'il y est autorisé (département/user).
    # Miroir de l'enforcement call-time (seam unique `rbac_denied_connectors`, escalade
    # super_admin + org_admin incluse) → « voir en tant que » reflète l'effet réel.
    denied = access.rbac_denied_connectors(ctx.sub, ctx.org_id)
    out = []
    for c in providers.public_catalog():
        if c["name"] not in exposed:
            continue
        # RBAC org : refusé au membre + pas admin plateforme → masqué.
        if c["name"] in denied and not is_admin:
            continue
        out.append(c)
    return out


def _doctrine_refs_by_ns(org_id: int | None) -> dict[str, set]:
    """namespace → ensemble des doctrines de l'org qui le référencent (`<tool:slug>`).
    Vide si pas d'org. Dérivation pure depuis les bodies de doctrine (posture
    « doctrine-only », ADR 0024) — best-effort, ne fait jamais échouer la lecture."""
    if not org_id:
        return {}
    try:
        refs: dict[str, set[str]] = {}
        for d in org_store.list_instruction_bodies(org_id):
            slug = d.get("slug") or ""
            for ns in tool_registry.namespaces_in(d.get("body_md") or ""):
                refs.setdefault(ns, set()).add(slug)
        return refs
    except Exception:
        return {}


# Champs conservés en mode COMPACT (défaut MCP) : identité + axes de tri + état.
# Les gros champs (doc_sections/auth/credential_fields/description/namespaces) ne
# reviennent qu'en `verbose=True` (dashboard, setup credential). Cf. #109.
_COMPACT_KEYS = ("name", "label", "help", "family", "category", "availability", "logo_url")


def _me(ctx: ResolvedCtx, inp: MyConnectorsInput) -> dict:
    org_id = ctx.org_id or 0
    selection = connector_selection.list_selection(ctx.sub, org_id)
    recommended = set(org_store.get_org_default_connectors(ctx.org_id) or []) if ctx.org_id else set()
    doc_refs = _doctrine_refs_by_ns(ctx.org_id)
    # Découvrabilité : une clé peut exister à portée (équipe dont je suis membre,
    # autre org) sans que la cascade la lise — le connecteur paraît alors « vide »
    # dans la library alors qu'il est utilisable en épinglant. Jusqu'ici l'info
    # n'existait qu'APRÈS un appel raté (erreur « rien ne résout »), donc jamais si
    # le connecteur n'est pas installé (l'appel meurt au dispatch). Une passe
    # BATCHÉE, hors boucle, pour ne pas payer N×M requêtes.
    reach = access.reachable_instances_map(ctx.sub, ctx.org_id)
    catalog = _visible_catalog(ctx)
    if inp.name:
        catalog = [c for c in catalog if c["name"] == inp.name]
        if not catalog:
            # Même verdict que `_require_exposed` (select/pause) : non exposé pour
            # l'org active, restreint par le RBAC d'org, ou nom inconnu — indistinguables
            # côté membre, et tous actionnables par le même message.
            raise AuthzDenied(404, "unknown_connector",
                              f"Connecteur `{inp.name}` inconnu ou indisponible pour ton org active.")
    connectors = []
    for c in catalog:
        state = selection.get(c["name"], "not_selected")
        if inp.state and state != inp.state:
            continue
        refset: set = set()
        for ns in c.get("namespaces") or []:
            refset |= doc_refs.get(ns, set())
        base = c if inp.verbose else {k: c.get(k) for k in _COMPACT_KEYS}
        # URL de retour de consentement — sur la projection AUTHENTIFIÉE seulement.
        # `providers.public_catalog()` alimente aussi `/api/connectors`, servie sans
        # auth : le descripteur public reste sans URL (cf. `connector_flow.describe`).
        # Elle est DÉRIVÉE de l'environnement, jamais écrite : c'est la valeur que le
        # client doit enregistrer chez son fournisseur, et une prose codée en dur ment
        # dès qu'on la lit depuis la preprod (vécu : un `redirect_uri_mismatch`
        # incompréhensible côté client).
        # `app_ready` suit la même règle (réponse propre au DEMANDEUR, donc jamais au
        # catalogue public) : il dit au front s'il reste une app à poser avant de
        # pouvoir consentir. Sans lui, l'écran de connexion promettait le pire cas à
        # tout le monde — « pose d'abord les identifiants de l'application », y compris
        # à qui n'a plus rien à poser depuis qu'oto publie la sienne.
        if inp.verbose and base.get("connect"):
            from .. import connector_flow
            base = {**base, "connect": {
                **base["connect"],
                "callback_url": connector_flow.callback_url(c["name"]),
                "app_ready": connector_flow.app_ready(c["name"], ctx.sub)}}
        # Couche 3 (option payante) sur la surface USER — sans ça le front ne peut pas
        # dire LAQUELLE des 3 conditions manque (le bandeau « État pour toi », ADR 0044) :
        # `mode==forbidden` conflate option/activation/RBAC. `option_ok=True` si aucune
        # option requise. Verbose seulement (compact = catalogue).
        opt = access.paid_option_for(c["name"])
        # `option_ok` = SOURCE UNIQUE `access.option_open` (partagée avec status_for) :
        # pas d'option ⟹ ok ; sinon BYO (clé propre) OU has_option. Un seul endroit
        # décide « utilisable » → plus de divergence carte « clé d'org » + « Bloqué ».
        row = {
            **base,
            "state": state,
            "recommended": c["name"] in recommended,
            "doctrine_ref_count": len(refset),
            "paid_option": opt,
            "option_ok": access.option_open(ctx.sub, c["name"], org=ctx.org_id),
        }
        # Présent SEULEMENT si une instance est à portée → la ligne se distingue au
        # lieu d'ajouter un champ vide sur 40 lignes. Volontairement distinct de
        # `recommended` (= baseline de l'org) : surcharger ce dernier ferait mentir
        # le réglage d'org. C'est de la VISIBILITÉ : le gate dur reste
        # `require_connector_access` à l'appel.
        if reach.get(c["name"]):
            row["reachable_instances"] = reach[c["name"]]
        connectors.append(row)
    return {"connectors": connectors, "verbose": inp.verbose}


def _require_exposed(ctx: ResolvedCtx, name: str) -> None:
    """Plafond : un connecteur non-exposé pour l'org active ne peut être ni
    sélectionné ni mis en pause (deny-by-default jamais relâché)."""
    if name not in connector_activation.exposed_connectors(ctx.org_id):
        raise AuthzDenied(404, "unknown_connector",
                          f"Connecteur `{name}` indisponible pour ton org active.")


# Guidage post-activation (oto-backend#111). Le registre d'outils d'une session MCP est
# FIGÉ à l'ouverture de la conversation : un connecteur activé en cours de session n'y
# monte pas ses outils (le hot-reload `tools/list_changed` n'est pas appliqué par
# claude.ai). Le pont fiable = `oto_call` (dispatch universel, ADR 0036) ; sinon, nouvelle
# conversation. On le DIT à l'agent au moment où il installe, pour qu'il enchaîne sans
# conclure « la capacité n'existe pas ».
def _connector_tools(name: str) -> list[str]:
    """Noms des tools du connecteur, depuis le registre BOOT (immunisé à la
    visibilité de session) — la moitié « découverte » de #186 : le hint ordonnait
    « appelle via oto_call » sans donner UN SEUL nom, et l'introspection de session
    ne voyait pas les tools d'un connecteur fraîchement activé."""
    from .. import providers, tool_registry
    from ..tool_visibility import namespace_of
    con = providers.REGISTRY.get(name)
    ns = set(con.namespaces) if con else {name}
    return [t for t in tool_registry.boot_tool_names() if namespace_of(t) in ns]


def _activation_hint(name: str, tools: list[str]) -> str:
    listing = (f" Ses outils : {', '.join(tools[:12])}." if tools
               else " (noms indisponibles — oto_tool_schema les décrit à la demande).")
    return (f"`{name}` est actif. Ses outils ne sont pas encore montés dans CETTE conversation "
            f"(le registre d'outils est figé à l'ouverture) —{listing} Appelle-les DÈS "
            f"MAINTENANT via `oto_call(name=…, arguments={{…}})` (schéma d'un outil : "
            f"`oto_tool_schema`), ou ouvre une NOUVELLE conversation pour les voir listés.")


def _select(ctx: ResolvedCtx, inp: ConnectorActionInput) -> dict:
    _require_exposed(ctx, inp.name)
    connector_selection.set_state(ctx.sub, inp.name, connector_selection.ACTIVE, ctx.org_id or 0)
    tools = _connector_tools(inp.name)
    return {"connector": inp.name, "state": "active", "tools": tools,
            "hint": _activation_hint(inp.name, tools)}


def _pause(ctx: ResolvedCtx, inp: ConnectorActionInput) -> dict:
    _require_exposed(ctx, inp.name)
    connector_selection.set_state(ctx.sub, inp.name, connector_selection.PAUSED, ctx.org_id or 0)
    return {"connector": inp.name, "state": "paused"}


def _unselect(ctx: ResolvedCtx, inp: ConnectorActionInput) -> dict:
    removed = connector_selection.unselect(ctx.sub, inp.name, ctx.org_id or 0)
    return {"connector": inp.name, "state": "not_selected", "removed": removed}


def _recommend(ctx: ResolvedCtx, inp: RecommendInput) -> dict:
    """« Org propose » : l'org_admin pose la baseline de connecteurs par défaut de
    l'org. Depuis peu ce N'EST PLUS un simple badge consultatif : c'est la source
    RÉELLE du socle de départ de tout NOUVEAU membre (union avec le socle plateforme
    au premier seed, `session_visibility.compute_hidden_tools`). N'affecte JAMAIS un
    membre déjà seedé (existant) — ceux-là restent sur leurs propres choix, sauf
    poussée explicite via `connectors.bulk_select`. [] efface la baseline (retour au
    seul socle plateforme, vide par défaut)."""
    ok = org_store.set_org_default_connectors(inp.org_id, inp.connectors)
    if not ok:
        raise AuthzDenied(404, "unknown_org", f"Org #{inp.org_id} inconnue.")
    return {"org_id": inp.org_id, "recommended": inp.connectors}


def _bulk_select(ctx: ResolvedCtx, inp: BulkSelectInput) -> dict:
    """[org admin] Un seul geste, deux effets — le moins de friction possible pour
    « rendre ce connecteur actif pour toute l'org, maintenant ET pour la suite » :
    (1) push explicite immédiat : active `name` pour tout membre ACTUEL de l'org
    qui n'a encore fait AUCUN choix pour ce connecteur (ni actif ni pause) — ne
    réécrit jamais un choix déjà posé, un membre qui a pause/désinstallé reste sur
    sa décision ; (2) persiste `name` dans la baseline par défaut de l'org
    (`org_store.default_connectors`, fusion additive — n'écrase pas les autres
    entrées déjà présentes) pour que tout NOUVEAU membre le reçoive pré-activé à
    son premier seed, sans repasser par ici."""
    if inp.name not in providers.REGISTRY:
        raise AuthzDenied(404, "unknown_connector", f"Connecteur `{inp.name}` inconnu.")
    if inp.name not in connector_activation.exposed_connectors(inp.org_id):
        raise AuthzDenied(409, "org_disabled",
                          f"Connecteur `{inp.name}` non disponible pour cette org — rien à "
                          f"activer en masse (le plafond d'exposition n'est jamais relâché).")
    activated = 0
    skipped = 0
    for m in org_store.list_org_members(inp.org_id):
        if connector_selection.state_of(m["sub"], inp.name, inp.org_id) is None:
            connector_selection.set_state(m["sub"], inp.name, connector_selection.ACTIVE, inp.org_id)
            activated += 1
        else:
            skipped += 1
    current_defaults = set(org_store.get_org_default_connectors(inp.org_id) or [])
    already_default = inp.name in current_defaults
    if not already_default:
        org_store.set_org_default_connectors(inp.org_id, sorted(current_defaults | {inp.name}))
    return {"org_id": inp.org_id, "connector": inp.name, "activated": activated, "skipped": skipped,
            "added_to_org_defaults": not already_default}


def _unset_default(ctx: ResolvedCtx, inp: UnsetDefaultInput) -> dict:
    """[org admin] Symétrique SOUSTRACTIF de `bulk_select` : retire `name` de la
    baseline par défaut de l'org — plus aucun FUTUR membre ne le recevra
    pré-activé. Ne touche AUCUN membre existant (n'active ni ne désactive
    personne, et surtout ne masque JAMAIS le connecteur du catalogue/recherche —
    contrairement à l'ancien levier d'exposition, un membre reste toujours libre
    de le trouver et de le sélectionner lui-même)."""
    current = set(org_store.get_org_default_connectors(inp.org_id) or [])
    removed = inp.name in current
    if removed:
        org_store.set_org_default_connectors(inp.org_id, sorted(current - {inp.name}))
    return {"org_id": inp.org_id, "connector": inp.name, "removed": removed}


CAPABILITIES += [
    Capability(
        key="connectors.me", handler=_me, Input=MyConnectorsInput, authz=SUB_ONLY,
        description="List every connector available to you (the marketplace catalog) with your "
                    "per-workspace state: not_selected (in the library) / active / paused, plus "
                    "`recommended` when your org proposes it. Source for both the connector "
                    "library and your installed connectors. Returns a COMPACT row per connector "
                    "by default (name/label/family/category/state/…) — pass verbose=true for the "
                    "full card (doc, auth descriptor, credential fields). Filter with state="
                    "active|paused|not_selected, or with name=<connector> to read the state of "
                    "a SINGLE connector (pair it with verbose=true instead of pulling the whole "
                    "catalog).",
        rest=RestBinding("GET", "/api/me/connectors"),
    ),
    Capability(
        key="connectors.select", handler=_select, Input=ConnectorActionInput, authz=SUB_ONLY,
        description="Install a connector into your active workspace (state=active). name = "
                    "connector name from the catalog. Its tools do NOT mount in the current "
                    "conversation (the tool registry is frozen at open) — the response `hint` "
                    "tells you to reach them right away via oto_call, or open a new conversation.",
        rest=RestBinding("POST", "/api/me/connectors/{name}/select"),
    ),
    Capability(
        key="connectors.pause", handler=_pause, Input=ConnectorActionInput, authz=SUB_ONLY,
        description="Pause an installed connector (state=paused): kept installed but its tools "
                    "are hidden. Resume by selecting it again.",
        rest=RestBinding("POST", "/api/me/connectors/{name}/pause"),
    ),
    Capability(
        key="connectors.unselect", handler=_unselect, Input=ConnectorActionInput, authz=SUB_ONLY,
        description="Remove a connector from your workspace (back to the library). Does not touch "
                    "credentials, only your selection.",
        rest=RestBinding("DELETE", "/api/me/connectors/{name}"),
    ),
    Capability(
        key="connectors.recommend", handler=_recommend, Input=RecommendInput,
        authz=ORG_ADMIN_OF("org_id"),
        description="[org admin] Set your org's default connector set. This is the REAL starting "
                    "toolbox for every NEW member (merged with the platform baseline at their "
                    "first session) — not just a library badge. Never retroactively touches an "
                    "existing member; they keep whatever they've already chosen. connectors = "
                    "list of connector names ([] clears back to the platform-only baseline).",
        rest=RestBinding("PUT", "/api/orgs/{id}/default-connectors", _ID),
    ),
    Capability(
        key="connectors.bulk_select", handler=_bulk_select, Input=BulkSelectInput,
        authz=ORG_ADMIN_OF("org_id"),
        description="[org admin] Make a connector active for the whole org, now and going "
                    "forward, in one call: activates it for every CURRENT member who hasn't made "
                    "an explicit choice yet (never overrides a member's own pause/uninstall), AND "
                    "adds it to the org's default set so every member who joins LATER starts with "
                    "it pre-activated too. Requires the org to already expose the connector.",
        rest=RestBinding("POST", "/api/orgs/{id}/connectors/{name}/bulk-select", _ID),
    ),
    Capability(
        key="connectors.unset_default", handler=_unset_default, Input=UnsetDefaultInput,
        authz=ORG_ADMIN_OF("org_id"),
        description="[org admin] Remove a connector from the org's default set — members who "
                    "join later stop getting it pre-activated. Never touches an existing "
                    "member's own active/paused choice, and never hides the connector from "
                    "search/the library (unlike the platform/org exposure ceiling).",
        rest=RestBinding("DELETE", "/api/orgs/{id}/connectors/{name}/bulk-select", _ID),
    ),
]
