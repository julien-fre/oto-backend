"""Méta-tools — pilotage des préférences de l'user depuis la conversation.

Permet à l'assistant (Claude.ai, Claude Code) de désactiver/réactiver des
tools individuellement sans passer par l'UI /account. La persistance reste
en DB (`user_disabled_tools`), et les changements émettent immédiatement
`tools/list_changed` à la session courante grâce à `disable_components` /
`enable_components` (fastmcp).
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Literal, Optional

from fastmcp import Context, FastMCP
from fastmcp.server.transforms.visibility import (
    disable_components,
    enable_components,
    reset_visibility,
)
from ..mcp_errors import McpError
from mcp.types import ErrorData, INVALID_PARAMS
from pydantic import ValidationError

from .. import (access, call_axes, calllog, db, deprecations, guide_run, outils_retires,
                providers, redaction, run_org, session_org, tool_alias, tool_registry)
from ..auth.hooks import current_user_sub_from_token
from ..tool_visibility import (
    PROTECTED_TOOLS,
    is_default_hidden,
    namespace_of,
)
from . import catalogue

# Méta/spine non dispatchables via `oto_call` (ADR 0036 §4) : déjà toujours visibles,
# aucun intérêt à passer par le dispatch, et anti-boucle (`oto_call` sur lui-même).
# Miroir de `middleware.field_redaction._SPINE_SERVICES`.
_NON_DISPATCHABLE: frozenset[str] = frozenset({"oto", "run", "feedback", "data"})


def _refuser_si_retire(name: str) -> None:
    """Un nom RETIRÉ (`outils_retires`) refuse ici avec le MÊME texte que l'appel direct :
    il nomme le geste qui aboutit. `name` est déjà canonique (préfixe de tenant levé)."""
    retire = outils_retires.retrait(name)
    if retire is not None:
        raise McpError(ErrorData(code=INVALID_PARAMS, message=retire.message))

# Le budget d'une ligne de catalogue vit avec le catalogue (`tools/catalogue.py`).
_CATALOG_BLURB = catalogue.CATALOG_BLURB
# Recherche : borne par défaut. Au-delà, l'agent relit le catalogue entier — c'est le
# signe que la requête était trop large, pas qu'il manque des résultats.
_SEARCH_LIMIT = 40

logger = logging.getLogger(__name__)


def hint_zero_resultat(tb: Optional[dict]) -> str:
    """Le hint d'une recherche d'outils qui ne trouve rien.

    Deux causes possibles, et **la mauvaise réponse coûte un rapport faux**. Sans
    écart de boîte, zéro veut bien dire « reformule » — la recherche est lexicale sur
    des docstrings anglaises. Avec écart (#577), zéro ne dit RIEN de l'existence de
    l'outil : la session a été montée pour l'org maison au handshake, les outils des
    connecteurs de l'org épinglée n'y sont pas listés, et ils restent appelables.

    Servir le premier texte dans le second cas est ce qui a produit le rapport
    « source injoignable » du signal #616, sur un connecteur actif et joignable."""
    if tb:
        return ("Zéro résultat ICI ne veut PAS dire que l'outil n'existe pas : la boîte "
                "de cette session est montée pour une autre org (voir `toolbox_scope`), "
                "donc les outils des connecteurs de l'org épinglée n'y sont pas listés. "
                "Appelle-le par `oto_call(name=..., arguments={...})` avant de conclure "
                "qu'une source est injoignable.")
    return ("Aucun outil ne porte ces mots. La recherche est LEXICALE et les docstrings "
            "sont en ANGLAIS : relance la même intention en anglais avant toute autre "
            "conclusion — mesuré le 08/09/2026, « transférer propriétaire équipe "
            "ressource » rend 0 outil et « transfer ownership resource team » rend "
            "`oto_resource` en tête. Sinon, repère le domaine dans `namespaces`, ou "
            "relance sans `query` pour le catalogue complet.")


def _tool_prefix() -> str:
    """Le préfixe d'outils du tenant courant (`""` = noms canoniques).

    Ces cinq tools prennent un NOM en argument : ce sont les seuls endroits où un nom
    traverse un HANDLER au lieu du bord du protocole, donc les seuls que le
    `ToolAliasMiddleware` ne couvre pas. Sans ce rappel, un compte de tenant tiers
    lisait `acme_doc` dans sa liste et se voyait répondre « Unknown tool » en le
    passant à `oto_tool_schema` — le catalogue et le dispatch auraient parlé deux
    langues."""
    try:
        return tool_alias.prefix_for(current_user_sub_from_token())
    # noqa: SILENT — dette déclarée : préfixe d'outil perdu ⇒ notre identité servie (#424, verdict C)
    except Exception:  # noqa: BLE001 — fail-open : les noms canoniques
        return ""


def _require_sub() -> str:
    sub = None
    try:
        sub = current_user_sub_from_token()
    # noqa: SILENT — dette déclarée : sub avalé (#424, verdict C — seam commun)
    except Exception:
        pass
    if not sub:
        raise McpError(ErrorData(
            code=INVALID_PARAMS,
            message="Auth requise — ces tools ne marchent que sur le transport HTTP authentifié.",
        ))
    return sub


def _active_org(sub: str) -> int:
    """Org de session du sub = scope du profil de visibilité (ADR 0015/0023). 0 = perso/global.
    Toggles perso sont stockés par (sub, org_id) → on lit l'org **de session** via le seam
    unique `access.current_org` (ADR 0023 ; jamais `org_store.get_active_org` en direct, qui
    renverrait l'org maison et désynchroniserait l'UX après `oto_use_org`). ADR 0030 §6 barreau 1."""
    return access.current_org(sub) or 0


async def _resolve_tool(ctx: Context, name: str):
    """Objet Tool FastMCP par nom (ou None), **y compris masqué/désactivé** — on
    énumère le catalogue BRUT du `Provider` parent (« including disabled ones »,
    docstring fastmcp). ⚠️ `list_tools(run_middleware=False)` ne suffit PAS : il
    applique quand même `apply_session_transforms` + filtre `is_enabled` → un tool
    masqué par la visibilité de LA SESSION (connecteur non activé au handshake,
    non-sélectionné) était introuvable au dispatch — l'échappatoire `oto_call`/
    `oto_tool_schema` répondait « Unknown tool » (#186, régression du passage à la
    visibilité native fastmcp)."""
    from fastmcp.server.providers.base import Provider
    tools = await Provider.list_tools(ctx.fastmcp)
    for t in tools:
        if t.name == name:
            return t
    return None


# Les clés du relevé qui FACTURENT : elles vont sur la ligne de la cible, et sur elle
# seule. La ligne d'enveloppe (`tool='oto_call'`) ne doit pas les porter — une même
# consommation écrite deux fois serait facturée deux fois le jour où un consommateur
# ne filtrerait plus par nom d'outil.
# Les compteurs `found_*` (contacts d'un job FullEnrich où une valeur de chaque sorte
# a été trouvée) FACTURENT aussi : ce sont eux qui portent le prix par résultat.
_BILLING_TRACE_KEYS = ("quantity", "key_mode",
                       "found_work_emails", "found_personal_emails", "found_phones")
_UNSET = object()


async def _trace_target_call(sub: Optional[str], name: str, args: dict, ok: bool,
                             error: Optional[str], duration_ms: int, *,
                             trace: Optional[dict] = None,
                             org_id: object = _UNSET,
                             run_id: Optional[str] = None) -> None:
    """Journalise l'appel dispatché SOUS LE NOM CIBLE (ADR 0036 §5 / 0017) : sans ça
    seul `oto_call` apparaît dans `tool_calls` et l'inventaire d'usage devient aveugle
    au catalogue latent. Best-effort — jamais bloquant.

    ⚠️ **Même fabrique d'arguments que le middleware** (`calllog.truncated_args`) : cette
    ligne-ci est servie par les MÊMES surfaces (fiche d'un appel, timeline d'un déroulé),
    qui annoncent toutes deux des arguments tronqués et masqués. Elle posait le
    dictionnaire brut jusqu'au 2026-09-01 — 40 159 lignes en base, dont les 111 seules
    dont une valeur dépassait la borne annoncée. Le nom CIBLE est celui qui déclare ses
    secrets : c'est lui qu'on passe, jamais `oto_call`."""
    try:
        session_id = None
        try:
            from fastmcp.server.dependencies import get_context
            c = get_context()
            session_id = c.session_id
            # Même source que le sink du middleware : le jeton `_run_id=` d'abord (lu
            # par l'appelant AVANT le reset des axes), la pile de session ensuite.
            run_id = run_id or await guide_run.active_run_id(c)
        # noqa: SILENT — dette déclarée : la trace d'appel indirect disparaît (#424, verdict C)
        except Exception:
            pass
        row = {
            "server": "oto", "kind": "mcp", "sub": sub, "tool": name,
            "args": calllog.truncated_args(args, tool=name),
            "ok": ok, "error": error, "duration_ms": duration_ms,
            "session_id": session_id, "run_id": run_id,
            # L'org SOUS LAQUELLE LA CIBLE A RÉSOLU, lue par `oto_call` avant de défaire
            # ses axes — pas l'org maison qu'on relirait après coup.
            "org_id": access.current_org(sub) if org_id is _UNSET else org_id,
        }
        # La même règle que le sink du middleware : sans elle, la cible d'un dispatch
        # n'avait ni `key_mode` ni `quantity`, donc n'était jamais facturée.
        # La liste fermée des clés versées dans `args` vit dans `server` (import au
        # moment de l'appel : le module est déjà chargé en production, et l'outil ne
        # doit pas en recopier une seconde version).
        from .. import server as _server
        calllog.apply_call_trace(row, trace, _server._TRACED_ARGS)
        await asyncio.to_thread(db.insert_tool_call, row)
    except Exception:
        logger.warning("traçage oto_call → %s échoué (non bloquant)", name, exc_info=True)


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def oto_list_my_tools(ctx: Context, op: Optional[Literal["list", "search"]] = None,
                                query: Optional[str] = None, state: Optional[str] = None,
                                limit: Optional[int] = None, full: bool = False) -> dict:
        """The oto tool CATALOG — EVERY tool of the platform (~725), each with its STATE
        for you: `installed` (in your toolbox: call it directly), `installable`
        (callable right now with `oto_call`, installed durably with
        `oto_connector(op='select', name=<connector>)`) or `not_exposed` (NOT
        callable: the connector is not opened to your organization, or the tool is
        beyond your role — an org admin opens it). A tool absent from your toolbox is
        never a missing capability: it is here, with the state that says what to do.

        op=list (default without `query`) → the whole catalog GROUPED by connector:
        `{namespace, connector, label, state, tools: [names]}` (~25k chars in all).
        `full=True` flattens it, one entry per tool with a one-line description
        (~115k chars: prefer `state=` or a search). `state=installed|installable|
        not_exposed` keeps one state.
        op=search (default with `query`) → tools RANKED by how many words of `query`
        match their name, their connector's catalog line and their description.
        LEXICAL, docstrings in ENGLISH: zero result means « rephrase, try English, or
        op=list » — never « oto cannot do this ». 40 entries by default (`limit`),
        one-line descriptions; `full=True` = whole descriptions.

        Entry point of the deferred mode — `oto_list_my_tools` → `oto_tool_schema(name)`
        (the exact arguments, read BEFORE calling) → `oto_call` — the way an agent
        reaches oto without loading ~725 schemas.

        Args:
            op: `list` | `search`; derived from `query` when omitted.
            query: words to search (op=search), e.g. "linkedin message", "invoice".
            state: keep only the tools in this state.
            limit: cap the entries (search: 40 by default; list: none).
            full: more description — list: one line per tool; search: whole docstrings.
        """
        sub = _require_sub()
        if op is None:
            op = "search" if query else "list"
        if op == "search" and not (query or "").strip():
            raise McpError(ErrorData(code=INVALID_PARAMS,
                                     message="op=search : `query` requis (les mots à chercher)."))
        if op == "list" and query:
            raise McpError(ErrorData(code=INVALID_PARAMS,
                                     message="op=list ne filtre pas par `query` — pour chercher, op=search."))
        if state is not None and state not in catalogue.ETATS:
            raise McpError(ErrorData(code=INVALID_PARAMS,
                                     message=f"`state` ∈ {' | '.join(catalogue.ETATS)}."))
        entries = await catalogue.catalogue_avec_etat(ctx, sub, _tool_prefix())
        catalogue_entier = len(entries)
        par_etat = {e: sum(1 for x in entries if x["state"] == e) for e in catalogue.ETATS}
        out: dict = {"op": op, "catalog_total": catalogue_entier,
                     "catalog_by_state": par_etat}
        # L'aveu du décalage de boîte (#577, signaux #616/#639) : la session a été
        # montée pour l'org MAISON au handshake, l'appel épingle peut-être une autre
        # org — les outils de ses connecteurs ne sont alors PAS listés, tout en restant
        # appelables. Il vivait sur `oto_connector op=list` seulement, c'est-à-dire là
        # où on ne va que si on soupçonne déjà quelque chose. Ici est l'endroit où un
        # agent cherche un outil, et où il concluait « indisponible ».
        from ..capabilities.connectors.selection import _toolbox_scope
        tb = _toolbox_scope(sub)
        if tb:
            out["toolbox_scope"] = tb
        if state:
            entries = [e for e in entries if e["state"] == state]
            out["state"] = state
        if op == "search":
            entries = tool_registry.match(query, entries)
            out["query"] = query
            if not entries:
                # Zéro résultat lexical ≠ « oto ne sait pas faire » (le piège que la
                # recherche pourrait CRÉER). On rend la carte des capacités : l'agent
                # repart du domaine au lieu de conclure à une lacune.
                out["namespaces"] = providers.render_namespace_catalog()
                out["hint"] = hint_zero_resultat(tb)
        # oto#42, entrée 1 : `total` décrit le jeu qu'il accompagne — sur une recherche,
        # le nombre de CORRESPONDANCES, jamais le catalogue entier (rendu à côté sous
        # son propre nom, `catalog_total`, pour que « 3 outils » ne se lise pas « oto
        # n'en a que 3 »).
        out["total"] = len(entries)
        cap = limit if limit is not None else (_SEARCH_LIMIT if op == "search" else None)
        shown = entries[:cap] if cap else entries
        out["shown"] = len(shown)
        if len(shown) < len(entries):
            # La branche « trop de résultats » était la seule non traitée : la branche
            # zéro rendait la carte des namespaces, celle-ci ne disait rien.
            out["truncated"] = True
            out["hint_truncated"] = (
                f"{len(entries)} outils correspondent, {len(shown)} rendus. Affine la "
                "recherche, ou relance avec `limit` plus haut pour les voir tous.")
        out["legend"] = catalogue.LEGENDE
        if op == "search":
            cle = "description_full" if full else "description"
            out["tools"] = [{"name": e["name"], "namespace": e["namespace"],
                             "state": e["state"], "description": e[cle]} for e in shown]
        elif full:
            out["tools"] = [{k: e[k] for k in ("name", "namespace", "state", "description")}
                            for e in shown]
        else:
            out["connectors"] = catalogue.grouper_par_connecteur(shown)
            out["projection"] = ("un groupe par connecteur, avec ses outils par nom ; "
                                 "`full=True` rend une ligne de description par outil, "
                                 "`oto_tool_schema(name)` le détail d'un outil.")
        return out

    @mcp.tool()
    async def oto_disable_tool(name: str, ctx: Context) -> dict:
        """Disable a tool for the current user — persistent across sessions.

        The tool disappears from the visible list immediately (the server
        notifies the client via tools/list_changed). Re-enable with
        `oto_enable_tool`.

        Args:
            name: Exact tool name (e.g. `attio_create_deal`, `linkedin_unipile_search`).
        """
        sub = _require_sub()
        # Le nom peut arriver sous la forme du tenant (`acme_doc`) : la denylist,
        # elle, s'écrit en canonique — sinon le même outil s'y retrouverait deux fois,
        # et le toggle ne mordrait plus après un changement de préfixe. Le retour
        # reprend la forme MONTRÉE, celle que l'agent vient de lire dans sa liste.
        # Il peut aussi être un nom DÉPRÉCIÉ (#519) : `tools/list` le sert, donc il
        # doit se résoudre ici — un nom listé et injoignable est pire qu'absent.
        prefix = _tool_prefix()
        name = deprecations.tool_canonique(tool_alias.canonical(name, prefix))
        all_tools = await ctx.fastmcp.list_tools(run_middleware=False)
        known = {t.name for t in all_tools}
        if name not in known:
            raise McpError(ErrorData(
                code=INVALID_PARAMS,
                message=f"Unknown tool `{name}`. Use oto_list_my_tools to see available names.",
            ))
        if name in PROTECTED_TOOLS:
            raise McpError(ErrorData(
                code=INVALID_PARAMS,
                message=f"`{name}` is protected (toolset management, context switching or "
                        "usage loop) — refusing to disable.",
            ))
        org = _active_org(sub)
        db.add_user_disabled_tool(sub, name, org)
        db.remove_user_enabled_tool(sub, name, org)  # lève un éventuel override
        await disable_components(ctx, names={name}, components={"tool"})
        return {"name": tool_alias.public(name, prefix), "enabled": False,
                "persistent": True}

    @mcp.tool()
    async def oto_enable_tool(name: str, ctx: Context) -> dict:
        """Re-enable a previously disabled tool for the current user.

        Args:
            name: Exact tool name to re-enable.
        """
        sub = _require_sub()
        prefix = _tool_prefix()
        name = deprecations.tool_canonique(tool_alias.canonical(name, prefix))
        # SÉCURITÉ — visibilité-only (ADR 0031) : (dés)activer un outil = préférence
        # d'AFFICHAGE, jamais une autorisation. Rendre un outil visible ne donne PAS
        # accès à son credential. L'accès réel d'un connecteur sensible est gardé au
        # call-time, indépendamment de cette visibilité : `resolve_credential` →
        # `require_connector_access` (ADR 0025, réservation par département/membre) +
        # le cran d'activation + la résolution du credential bridge (ADR 0034). Plus
        # de garde « grant-only » ici (concept retiré : `is_grant_only` est mort).
        org = _active_org(sub)
        db.remove_user_disabled_tool(sub, name, org)
        # Override positif requis pour rendre visible un masqué-par-défaut.
        if is_default_hidden(name):
            db.add_user_enabled_tool(sub, name, org)
        await enable_components(ctx, names={name}, components={"tool"})
        return {"name": tool_alias.public(name, prefix), "enabled": True,
                "persistent": True}

    # --- dispatch universel (ADR 0036) --------------------------------------

    @mcp.tool()
    async def oto_tool_schema(name: str, ctx: Context) -> dict:
        """Return the input JSON Schema of ANY oto tool by name — even one that is
        NOT currently listed (hidden by default, connector not activated, FOD…).

        Use this to learn the exact `arguments` shape before calling a latent tool
        with `oto_call`. Tool names come from `oto_list_my_tools`.

        Args:
            name: Exact tool name (e.g. `fr_ccn_search`, `foncier_dpe_adresse`).
        """
        _require_sub()
        prefix = _tool_prefix()
        demande, name = name, deprecations.tool_canonique(
            tool_alias.canonical(name, prefix))
        _refuser_si_retire(name)
        tool = await _resolve_tool(ctx, name)
        if tool is None:
            raise McpError(ErrorData(
                code=INVALID_PARAMS,
                message=f"Unknown tool `{demande}`. Use oto_list_my_tools to see available names."))
        return {
            # Rendu sous le nom que l'appelant VERRA dans sa liste, pas sous le nom
            # interne : il va le recopier dans `oto_call`.
            "name": tool_alias.public(name, prefix),
            "namespace": tool_alias.public_namespace(namespace_of(name), prefix),
            "description": (tool.description or "").strip(),
            "input_schema": getattr(tool, "parameters", None),
            "output_schema": getattr(tool, "output_schema", None),
        }

    @mcp.tool()
    async def oto_call(name: str, arguments: Optional[dict] = None,
                       _org: Optional[int] = None, _run_id: Optional[str] = None,
                       *, ctx: Context):
        """Call ANY oto tool by name — including one that is NOT listed (hidden by
        default, connector not activated, FOD…), for a single call, WITHOUT adding it
        durably to your toolbox.

        Use this when you need a tool that does not appear in your tool list. If the
        tool IS already visible, call it directly — don't wrap it in `oto_call`.
        Discover names and schemas with `oto_list_my_tools` / `oto_tool_schema`.
        META/SPINE tools (`oto_*`, `data_*`, `run_*`, `feedback`) are NOT routable
        here — they are always visible: call them directly.

        This bypasses only the DISPLAY filter, never access control: the target's
        call-time gates (credential, connector RBAC, activation, admin autz) and the
        org field-redaction policy apply exactly as for a direct call (ADR 0036).

        Call-context tokens (ADR 0038) are PREFIXED `_` — `_group`, `_project`,
        `_instance`, `_account`, `_run_id` — and may be included INSIDE `arguments`:
        they route the CALL CONTEXT (which org/team/credential-instance the target
        resolves under), are guarded exactly like on a listed tool, and are stripped
        before the target sees them. E.g. reach a team-scoped connector via
        `arguments={..., "_group": 3}`, or pin an instance via
        `"_instance": "<ref from oto_instance>"`.

        The prefix keeps them out of the tools' own argument space: an unprefixed
        `account`/`org`/`project` in `arguments` is a BUSINESS argument of the target
        (e.g. `aiark_company_search(account=…)` is AI Ark's company filter) and is
        passed through untouched.

        Args:
            name: Exact target tool name (e.g. `fr_ccn_search`).
            arguments: Argument object passed to the target tool. `{}` if none.
            _org: run the target tool under THIS organization (id) — resolves its
                credentials/visibility/data for that org (ADR 0038 call token,
                same membership guard as the flat `_org=` axis). Omit for your
                current org.
            _run_id: correlate this call to an open run, exactly like `_run_id` on a
                listed tool. Accepted here as well as inside `arguments` — same
                token, same effect — so the instruction « pass it on every call »
                never costs a call.
        """
        # Identité ambiante : le sub du JWT porte déjà l'appel (le handler cible
        # résout ses propres credentials dessus). Soft — sur stdio local il n'y a pas
        # de sub et tout le catalogue est déjà accessible.
        sub = None
        try:
            sub = current_user_sub_from_token()
        # noqa: SILENT — sans sub (stdio local) tout le catalogue est déjà accessible
        except Exception:
            pass

        # Le nom vient du catalogue, donc éventuellement sous la forme du tenant. Il
        # redevient canonique AVANT le gate méta/spine : sans ça `acme_doc` résout un
        # namespace inconnu, échappe à `_NON_DISPATCHABLE`, et l'anti-boucle saute.
        demande, name = name, tool_alias.canonical(name, _tool_prefix())
        # Un nom RETIRÉ refuse AVANT le gate méta/spine : `oto_kb` est un nom `oto_*`, et
        # « appelle-le directement » renverrait l'agent vers un nom qui n'existe plus.
        # C'est CE chemin qu'un agent prend quand une procédure nomme un outil absent de
        # sa liste — la notice le lui prescrit.
        _refuser_si_retire(name)
        if namespace_of(name) in _NON_DISPATCHABLE:
            raise McpError(ErrorData(
                code=INVALID_PARAMS,
                message=f"`{demande}` est un outil méta/spine — appelle-le directement, "
                        "pas via oto_call."))

        tool = await _resolve_tool(ctx, name)
        if tool is None:
            raise McpError(ErrorData(
                code=INVALID_PARAMS,
                message=f"Unknown tool `{demande}`. Use oto_list_my_tools to see available names."))

        args = arguments if isinstance(arguments, dict) else {}
        # Axes-contexte d'appel (ADR 0038). oto_call s'exécute HORS middleware → les
        # axes des tools plats (org/group/project/instance/account/run_id) ne sont pas
        # posés pour nous. On rejoue NOUS-MÊMES la boucle applies-gated du middleware
        # plat (`call_axes.axes_for_call` — les axes LUS, pas les seuls ANNONCÉS :
        # `_account=` est accepté sur tout connecteur multi-compte même quand le
        # schéma ne l'advertise pas, sinon `strip_unconsumed_axes` l'avale et l'appel
        # part sur le compte par défaut, sans erreur — review #399 F1 ;
        # ordre AXES → le plus spécifique co-pose son org) :
        # chaque axe présent dans `arguments` (ou le param top-level `_org=`, folded
        # ci-dessous) est GARDÉ+POSÉ puis RETIRÉ des args. Posé AVANT le try de run pour
        # qu'un refus de garde PROPAGE (McpError) au lieu d'être capturé comme une erreur
        # de la cible. Ferme #228 (instance/groupe d'un connecteur injoignable via
        # oto_call — seul `_org=` était honoré).
        if _org is not None:
            args.setdefault("_org", _org)
        # `_run_id=` passé AU NIVEAU D'oto_call plutôt que dans `arguments` : replié
        # comme `_org`, pour la même raison. Le modèle voit `_org` en tête de schéma
        # et range le jeton frère au même endroit ; sans ce repli, l'appel échouait
        # (« Unexpected keyword argument ») alors que la notice lui demande de porter
        # `_run_id` sur CHAQUE appel. `setdefault` : ce qui est déjà dans `arguments`
        # gagne — c'est la forme documentée, elle ne doit pas se faire écraser.
        if _run_id is not None:
            args.setdefault("_run_id", _run_id)
        call_axes.reject_legacy_axis_names(args, getattr(tool, "parameters", None))
        undo: list = []
        try:
            for axis in call_axes.axes_for_call(name):
                if axis.param in args:
                    undo.extend(await axis.pin_for(args.pop(axis.param), name))
            # L'org du RUN (#639), après les axes — même règle que le middleware :
            # sans `_org=`, la cible se résout dans l'org du run, appartenance gardée.
            undo.extend(await run_org.pin_for_call())
        except BaseException:
            for _reset, _tok in reversed(undo):
                _reset(_tok)
            raise
        # Un jeton passé pour un tool qui ne le supporte PAS (ex. `_instance` sur data_*,
        # `_org` sur un tool non org-scopable) = contexte sans effet → écarté des args,
        # pour ne pas casser sa validation. Sûr parce que les jetons sont préfixés `_` :
        # un argument MÉTIER homonyme (aiark `account` = le filtre société) ne porte pas
        # le préfixe et n'est donc jamais touché (issue #250).
        call_axes.strip_unconsumed_axes(args)
        # Relevé PROPRE à la cible. Sans lui, ce que la cible consigne (`key_mode` au
        # résolveur, `quantity` au point où N est connu) tombait dans le relevé de la
        # requête ENVELOPPE, donc sur la ligne `tool='oto_call'` — que la lentille de
        # facturation, qui filtre par nom d'outil, ne lit jamais. Holder MUTABLE posé
        # avant `tool.run` : un handler sync tourne en threadpool sur une copie du
        # contexte, et c'est la mutation de CE dict qui remonte.
        outer_trace = session_org.current_call_trace()
        target_trace: dict = {}
        trace_tok = session_org.set_call_trace(target_trace)
        started = time.monotonic()
        ok, err = True, None
        try:
            # `Tool.run` : injection de `ctx`, validation du schéma, exécution — mais
            # HORS chaîne de middleware (donc hors rédaction) : on la ré-applique plus
            # bas. C'est ce qui permet d'atteindre un outil masqué (la denylist de
            # visibilité ne bloque que le chemin protocole `tools/call`).
            result = await tool.run(args)
        except ValidationError as e:
            ok, err = False, "invalid_arguments"
            raise McpError(ErrorData(
                code=INVALID_PARAMS,
                message=f"Arguments invalides pour `{demande}` — voir `input_schema`.",
                data={"input_schema": getattr(tool, "parameters", None),
                      "errors": e.errors()}))
        # noqa: SILENT — l'échec de l'outil appelé est rendu dans ok/err au demandeur
        except Exception as e:  # noqa: BLE001 — l'erreur de la cible EST un résultat
            ok, err = False, str(e)
            # `tool` reprend le nom DEMANDÉ : l'agent le relit pour réessayer, et un
            # nom qu'il n'a jamais tapé le ferait douter de sa propre requête. Le
            # journal, lui, écrit le canonique (`_trace_target_call` juste dessous).
            return {"tool": demande, "ok": False, "error": str(e)}
        finally:
            # Org et run de la CIBLE, lus AVANT de défaire les axes : après le reset,
            # `current_org` rend l'org maison de l'appelant, pas celle où la cible a
            # résolu ses credentials — la ligne partait sous la mauvaise org.
            target_org: object = _UNSET
            try:
                target_org = access.current_org(sub)
            # noqa: SILENT — best-effort : `_trace_target_call` retombe sur sa propre lecture
            except Exception:
                pass
            target_run = session_org.current_call_run()
            session_org.reset_call_trace(trace_tok)
            # L'écho rendu à l'agent (`resolved_account`/`resolved_connector`, lus par
            # `CallContextMiddleware` dans le relevé ENVELOPPE) doit survivre ; seules
            # les clés qui facturent restent sur la ligne de la cible.
            if outer_trace is not None:
                outer_trace.update({k: v for k, v in target_trace.items()
                                    if k not in _BILLING_TRACE_KEYS})
            for _reset, _tok in reversed(undo):
                _reset(_tok)
            await _trace_target_call(sub, name, args, ok, err,
                                     int((time.monotonic() - started) * 1000),
                                     trace=target_trace, org_id=target_org,
                                     run_id=target_run)

        # Rédaction ré-appliquée (ADR 0036 §2) via la logique PARTAGÉE fail-closed —
        # sinon un connecteur à PII surfacé par oto_call fuiterait (le middleware a vu
        # le service « oto », pas le namespace cible).
        service = namespace_of(name)
        payload = redaction.extract_payload(result)
        try:
            red = redaction.redact_payload(service, payload)
        except redaction.RedactionWithheld:
            return redaction.withheld_result(name)
        return result if red is redaction.PASSTHROUGH else redaction.rebuild_result(result, red)

    # --- admin : grants de namespace sensible -------------------------------

    def _require_admin() -> str:
        sub = _require_sub()
        if not access.is_super_admin(sub):
            raise McpError(ErrorData(
                code=INVALID_PARAMS, message="Réservé au super admin.",
            ))
        return sub

    # Grants de namespace (user + org) fusionnés dans la capacité MCP
    # `oto_admin_namespace_access` (capabilities/namespace_access.py).
    #
    # Clés plateforme (list/set) RETIRÉES de la face MCP (2026-06-25) : poser une
    # clé brute = un secret en clair dans le contexte LLM → dashboard-only. CRUD
    # servi par les routes REST `/api/admin/platform-keys*` (api/routes.py).
