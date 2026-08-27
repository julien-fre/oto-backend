"""Middlewares FastMCP — application des préférences user au boot de session."""
from __future__ import annotations

import logging

from fastmcp.server.middleware import Middleware
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData, INVALID_PARAMS
from starlette.concurrency import run_in_threadpool

from . import (call_axes, doctrine_run, error_taxonomy, redaction, session_org,
               tool_alias)
from .auth_hooks import current_user_sub_from_token
from .session_visibility import apply_session_visibility
from .tool_visibility import namespace_of

logger = logging.getLogger(__name__)


class ToolAliasMiddleware(Middleware):
    """Traduit les noms d'outils de la plateforme au nom du PRODUIT du tenant.

    OUTERMOST, et c'est tout l'intérêt : le nom canonique (`oto_doc`) est rétabli
    AVANT que quoi que ce soit d'autre ne le lise — gates de contexte d'appel
    (`call_axes.axes_for`), politique de rédaction (`namespace_of`), visibilité de
    session, journal `tool_calls`. Rien en aval n'apprend qu'un alias existe, donc
    rien en aval ne peut diverger : les toggles, l'audit et les références
    `<tool:slug>` des procédures restent écrits en canonique, hier comme demain.

    En SORTIE, `on_list_tools` est au contraire le DERNIER à retoucher la liste (une
    chaîne extern→interne se déroule en sens inverse au retour) : le renommage
    s'applique après le filtrage de visibilité et l'enrichissement des descriptions,
    sur la liste réellement servie.

    Deux crans de prudence :

    - un alias qui écraserait un outil RÉEL est abandonné (le nom canonique reste
      servi). Ça ne devrait pas arriver — `tool_alias.normalize_prefix` refuse un
      préfixe qui est un namespace déclaré — mais la liste est ici, la vérification
      est gratuite, et la conséquence d'une collision serait qu'un outil en éclipse
      un autre sans que personne ne le voie ;
    - aucun accès DB : `tool_alias.prefix_for` lit le registre d'émetteurs en
      mémoire. Ce hook s'exécute DANS la boucle (serveur MONO-LOOP).

    Fail-open partout : pas de sub (endpoint anonyme, découverte), pas de préfixe
    déclaré, ou erreur ⟹ les noms canoniques, à l'octet près.
    """

    @staticmethod
    def _prefix() -> str:
        try:
            return tool_alias.prefix_for(current_user_sub_from_token())
        except Exception:  # noqa: BLE001 — un nom d'outil ne casse jamais un appel
            return ""

    async def on_list_tools(self, context, call_next):
        tools = await call_next(context)
        prefix = self._prefix()
        if not prefix:
            return tools
        try:
            pris = {t.name for t in tools}
            out = []
            for t in tools:
                maj = {}
                nom = tool_alias.public(t.name, prefix)
                if nom != t.name and nom not in pris:
                    maj["name"] = nom
                # La DESCRIPTION aussi : c'est sur elle que le modèle choisit, et 27
                # d'entre elles renvoient à un outil voisin (« resolve it with
                # oto_kb »). Laissée en canonique, elle fait rappeler un nom que le
                # client réaffiche sous notre marque — le défaut qu'on corrige, par
                # la porte de derrière. Mesuré à ~2 ms pour les 200 Ko servis, et nul
                # pour un tenant sans préfixe (on sort avant).
                desc = tool_alias.rewrite_prose(t.description or "", prefix)
                if desc != (t.description or ""):
                    maj["description"] = desc
                out.append(t.model_copy(update=maj) if maj else t)
            return out
        except Exception:
            logger.warning("renommage des outils échoué (fail-open, noms canoniques)",
                           exc_info=True)
            return tools

    async def on_initialize(self, context, call_next):
        """`serverInfo` au nom du PRODUIT — le dernier recoin de la classe de défaut
        que ce middleware ferme : les outils disaient `tulina_…` mais le handshake
        annonçait encore `oto`. `name` suit le `tool_prefix` déclaré (l'identifiant,
        cohérent avec les noms d'outils), `title` le nom du tenant (le libellé
        humain). Rien de déclaré ⟹ l'annonce d'avant, à l'octet près (fail-open)."""
        result = await call_next(context)
        if result is None or getattr(result, "serverInfo", None) is None:
            return result
        try:
            name, title = tool_alias.server_identity_for(current_user_sub_from_token())
            if not name and not title:
                return result
            maj = {}
            if name:
                maj["name"] = name
            if title:
                maj["title"] = title
            return result.model_copy(
                update={"serverInfo": result.serverInfo.model_copy(update=maj)})
        except Exception:  # noqa: BLE001 — une identité d'affichage ne casse pas un handshake
            logger.warning("renommage du serverInfo échoué (fail-open)", exc_info=True)
            return result

    async def on_call_tool(self, context, call_next):
        prefix = self._prefix()
        if not prefix:
            return await call_next(context)
        name = getattr(context.message, "name", "") or ""
        canonical = tool_alias.canonical(name, prefix)
        if canonical != name:
            context = context.copy(
                message=context.message.model_copy(update={"name": canonical}))
        try:
            return await call_next(context)
        except McpError as e:
            raise self._erreur_traduite(e, prefix) from e

    @staticmethod
    def _erreur_traduite(erreur: McpError, prefix: str) -> McpError:
        """Le contrat d'erreur (`ErrorEnvelopeMiddleware`, plus interne) NOMME des
        outils dans son message et son `hint` — « appelle-le via oto_call(…) »,
        « installe le connecteur — oto_connector(op='select') ». C'est le PREMIER mur
        que rencontre un agent, donc le texte le plus lu après le socle : le laisser
        en canonique ferait suivre la consigne au nom près, et réafficher notre marque
        à l'écran de quelqu'un qui n'est pas notre client.

        Fail-open : toute difficulté rend l'erreur d'ORIGINE — une erreur à traduire
        ne devient jamais une erreur de traduction.
        """
        try:
            data = getattr(erreur.error, "data", None)
            message = tool_alias.rewrite_prose(erreur.error.message or "", prefix)
            # `data` porte ce que le tool y a mis : l'enveloppe `{oto: {hint}}` du
            # contrat d'erreur, ou tout autre chose (`oto_tool_schema` y met un
            # schéma). On ne touche QUE le hint, et seulement s'il est là.
            oto = data.get("oto") if isinstance(data, dict) else None
            hint = oto.get("hint") if isinstance(oto, dict) else None
            traduit = tool_alias.rewrite_prose(hint, prefix) if hint else hint
            if message == erreur.error.message and traduit == hint:
                return erreur
            if traduit != hint:
                data = {**data, "oto": {**oto, "hint": traduit}}
            return McpError(ErrorData(code=erreur.error.code, message=message, data=data))
        except Exception:  # noqa: BLE001
            logger.warning("traduction du message d'erreur échouée (fail-open)",
                           exc_info=True)
            return erreur


class UserDisabledToolsMiddleware(Middleware):
    """Applique la visibilité des tools du user à sa session MCP.

    Au handshake `initialize`, pour le `sub` JWT courant, on calcule l'ensemble
    effectif des tools à masquer = `user_disabled_tools` ∪ (masqués par défaut non
    activés) ∪ (connecteurs non activés/en pause) ∪ (gates admin/alpha) et on pose
    une visibility rule session-scopée. Le calcul + l'application vivent dans
    `session_visibility` (partagés avec le refresh à chaud post-`oto_use_org`,
    ADR 0009/0011/0015). fastmcp gère nativement filtrage `tools/list`, blocage
    `tools/call` et émission de `tools/list_changed`.

    Pas de sub identifiable (stdio local, discovery non-authentifié) → on ne filtre
    rien : la machine du dev a accès complet, le masquage par défaut ne concerne que
    la surface multi-user authentifiée.
    """

    async def on_initialize(self, context, call_next):
        result = await call_next(context)
        try:
            sub = current_user_sub_from_token()
        except Exception:
            sub = None
        if not sub:
            return result
        ctx = context.fastmcp_context
        if ctx is None:
            logger.warning("fastmcp_context is None at on_initialize for sub=%s", sub)
            return result
        await apply_session_visibility(ctx, sub)
        return result


_DOCTRINE_GET_TOOL = "oto_procedure"
_GUIDE_TOOL = "oto_guide"

# « Cette session n'est pas un endpoint de projet publié » — distinct de « projet
# publié SANS prose » (None), qui court-circuite quand même le socle plateforme.
_PAS_DE_PROJET_PUBLIE = object()


def _published_project_instructions():
    """Prose du projet publié servi à la session courante, ou `_PAS_DE_PROJET_PUBLIE`.

    **Sync (DB) — à appeler via `run_in_threadpool`** (serveur mono-loop). Lit une
    ContextVar (le projet anonyme du sous-domaine) : `run_in_threadpool` propage le
    contexte, même patron que `_reachable_suffix`."""
    from . import instructions, subdomain_project
    pid = subdomain_project.current_anon_project_id()
    if not pid:
        return _PAS_DE_PROJET_PUBLIE
    return instructions.compose_published_project(pid)


def _session_instructions(sub: str) -> str:
    """L'artefact A/C composé pour `sub` (org active incluse).

    **Sync (DB), et LOURD** : `compose_session` marche la cascade de statut de TOUS
    les connecteurs (`access.status_for`), soit plusieurs requêtes par connecteur —
    à appeler via `run_in_threadpool`, JAMAIS dans la boucle. Vécu en production dans
    la nuit du 15/08 : sous ~8 clients lourds, chaque `initialize` gelait l'event loop
    entier pendant la composition (py-spy : `on_initialize` → `compose_session` →
    `walk_cascade` → `psycopg execute` sur le MainThread, 3 relevés sur 6 dont ≥4 s
    consécutives) → 502 en rafale et « ASGI message after response already completed ».
    Cf. `docs/event-loop-perf.md` (mode de gel n°2)."""
    from . import access, instructions
    return instructions.compose_session(sub, access.current_org(sub))


class DynamicInstructionsMiddleware(Middleware):
    """Injecte le contexte doctrine de l'org dans la surface vue par le LLM, par-(sub,
    org), au lieu de dépendre d'un appel volontaire de lecture de doctrine (canal fragile,
    otomata-private#49, amende ADR 0014). Deux points d'injection, selon la NATURE :

    - **artefact composé** (blocs A/C, #50) → `on_initialize` REMPLACE
      `result.instructions` par `instructions.compose_session(sub, org)`
      (le « cheval de Troie », relu par session ; Claude rehandshake par conversation).
    - **index des doctrines NOMMÉES** (skills) → `on_list_tools` enrichit la
      **description de `oto_procedure`** (l'outil qui les charge). Les skills ne sont
      PAS des outils → absents de `tools/list` → ce serait leur seul canal. Co-localisé
      avec le loader plutôt qu'un bloc dans les instructions.

    Fail-open partout : pas de sub (stdio/discovery), pas d'org, ou erreur → surface
    statique inchangée.
    """

    async def on_initialize(self, context, call_next):
        result = await call_next(context)
        if result is None or not getattr(result, "instructions", None):
            return result
        # Endpoint de PROJET publié : le client est un tiers sans compte. Il reçoit la
        # prose du projet, jamais le socle plateforme (feedback #309) — cf.
        # `instructions.compose_published_project`.
        #
        # ⚠️ Les DEUX compositions ci-dessous sont du DB SYNC → `run_in_threadpool`
        # obligatoire : ce hook s'exécute DANS la boucle (un middleware fastmcp est
        # async par contrat), et le serveur est mono-loop. Gel de prod du 15/08.
        try:
            body = await run_in_threadpool(_published_project_instructions)
        except Exception:
            logger.warning("instructions de projet publié échouées (fail-open)",
                           exc_info=True)
        else:
            if body is not _PAS_DE_PROJET_PUBLIE:
                if body:
                    result.instructions = body
                return result
        try:
            sub = current_user_sub_from_token()
        except Exception:
            sub = None
        if not sub:
            return result
        try:
            result.instructions = await run_in_threadpool(_session_instructions, sub)
        except Exception:
            logger.warning("composition des instructions échouée pour sub=%s (fail-open)",
                           sub, exc_info=True)
        return result

    async def on_list_tools(self, context, call_next):
        tools = await call_next(context)
        try:
            sub = current_user_sub_from_token()
        except Exception:
            sub = None
        if not sub:
            return tools
        try:
            from . import access, instructions, guide_store
            org_id = access.current_org(sub)
            # Deux loaders de prose on-demand, même canal de découverte : l'index
            # per-(sub, org) enrichit la description de l'outil qui les charge.
            extra = {
                _DOCTRINE_GET_TOOL: instructions.skills_index_md(org_id),
                _GUIDE_TOOL: guide_store.guides_index_md(sub, org_id),
            }
            if not any(extra.values()):
                return tools
            return [
                t.model_copy(update={"description":
                                     f"{(t.description or '').rstrip()}\n\n{extra[t.name]}"})
                if extra.get(t.name) else t
                for t in tools
            ]
        except Exception:
            logger.warning("enrichissement d'index (doctrine/guide) échoué pour sub=%s "
                           "(fail-open)", sub, exc_info=True)
            return tools


class FieldRedactionMiddleware(Middleware):
    """Redacte les champs sensibles du RÉSULTAT de tout tool, selon la politique de
    rédaction de l'org active (ADR 0009/0015, « la policy gouverne l'exposition »).

    Point d'application unique de la rédaction : remplace le filtrage qui vivait au
    niveau des clients (folk/silae/pennylane) et couvre désormais **tous** les
    connecteurs (unipile, ATS…) sans câblage par tool. La cascade (org → défaut
    serveur → vide) est résolue par `access.resolve_field_filter(<namespace>)` ;
    `FieldFilter` matche par nom de clé feuille, récursivement.

    Doit être enregistré **en dernier** (`add_middleware`) : l'exécution étant en
    ordre inverse, il enveloppe les autres et retouche le **résultat final**.

    Deux canaux à garder cohérents : un tool renvoie son dict en `structured_content`
    ET/OU en `content` (TextContent JSON). On redacte la donnée puis on réémet les
    deux depuis la version redactée — sinon un canal brut fuirait (Claude lit surtout
    `content`).

    **Fail-closed** : si l'application de la rédaction lève alors qu'une politique
    existe (ex. Faker absent pour `pseudonym`), on RETIENT la sortie plutôt que de
    laisser fuiter le brut. Une simple absence de policy (`is_empty`) = passe-through.
    """

    async def on_call_tool(self, context, call_next):
        result = await call_next(context)
        if getattr(result, "is_error", False):
            return result
        name = getattr(context.message, "name", "") or ""
        service = namespace_of(name)
        payload = redaction.extract_payload(result)   # dict | list | None

        # Capture passive du schéma observé (squelette clés+types, JAMAIS de valeurs) :
        # source de vérité du schéma de rédaction. Hors spine/méta. Best-effort.
        if payload is not None and service not in _SPINE_SERVICES:
            _observe_schema(service, payload)

        # Rédaction déléguée à la logique PARTAGÉE (`redaction.py`) — même chemin que
        # `oto_call` (ADR 0036), pour qu'un outil dispatché soit redacté à l'identique.
        try:
            red = redaction.redact_payload(service, payload)
        except redaction.RedactionWithheld:
            return redaction.withheld_result(name)
        if red is redaction.PASSTHROUGH:
            return result
        return redaction.rebuild_result(result, red)


# Spine / méta : pas de capture de schéma (pas des connecteurs ; `data` =
# données arbitraires de l'user → bruit). La rédaction, elle, reste possible partout.
_SPINE_SERVICES = {"oto", "run", "feedback", "data"}


def _observe_schema(service: str, payload) -> None:
    from . import connector_schema_store
    connector_schema_store.observe(service, payload)


def _echo_account(result, tool_name: str):
    """Dit à l'agent SOUS QUEL COMPTE l'appel est parti, quand il en a plusieurs.

    Un agent qui détient deux workspaces Slack en visait un — par défaut posé, par
    épinglage de projet ou par `_account=` — sans que rien ne le lui confirme :
    l'identité effective ne vivait que dans le journal, qu'il ne lit pas. Un envoi
    sous la mauvaise identité ne se rattrape pas ; le minimum est de la nommer.

    Trois gardes, dans cet ordre :
    - **compte NOMMÉ seulement** : en mono-compte la ligne du coffre est anonyme
      (`account=''`) ⟹ aucun écho, aucun bruit ajouté à 99 % des réponses ;
    - **même connecteur que l'outil appelé** : un outil composite peut résoudre un
      credential auxiliaire, et annoncer CE compte-là serait un mensonge ;
    - **payload dict** : une liste ou du texte est rendu tel quel.

    Posé ici plutôt que dans un middleware de plus : c'est le pendant naturel de ce
    que ce middleware fait à l'aller (poser le contexte de l'appel), et il est plus
    EXTERNE que la rédaction — donc l'écho n'est ni redacté, ni observé comme un
    champ du connecteur par la capture de schéma. Best-effort : un écho ne fait
    jamais échouer un appel qui a réussi.
    """
    try:
        if getattr(result, "is_error", False):
            return result
        trace = session_org.current_call_trace() or {}
        account = trace.get("resolved_account") or ""
        if not account or trace.get("resolved_connector") != namespace_of(tool_name):
            return result
        payload = redaction.extract_payload(result)
        if not isinstance(payload, dict) or "_account" in payload:
            return result
        return redaction.rebuild_result(result, {**payload, "_account": account})
    except Exception:  # noqa: BLE001 — un écho ne casse pas un appel réussi
        logger.debug("écho du compte impossible", exc_info=True)
        return result


class CallContextMiddleware(Middleware):
    """Pose le contexte d'appel (`_org=`) AVANT toute la chaîne middleware, pour que la
    résolution du handler ET les hooks post-tool (rédaction de champs, calllog) voient
    la MÊME org que l'appel — pas l'org maison (modèle sans état de session, #108/#112).

    Doit être enregistré **en premier** (`add_middleware` : premier ajouté = plus
    EXTERNE, vérifié empiriquement sur fastmcp `_run_middleware`) → il enveloppe
    `FieldRedactionMiddleware` + `ToolCallLogger`, et la ContextVar `_CALL_ORG` reste
    posée pendant qu'ils relisent `current_org` (sinon reset trop tôt = rédaction/audit
    sous la maison — bug vécu jusqu'au 2026-08-02, le middleware était ajouté en
    dernier donc INNERMOST). ContextVar per-tâche (isolée par appel) ; reset en `finally`.

    Garde d'appartenance au point d'entrée : `_org=` dont le sub n'est pas membre lève un
    McpError **actionnable**, jamais un repli silencieux vers une autre org. Ne s'active
    que pour les tools de capacité, où `_org` est injecté au schéma par l'adaptateur
    (le préfixe `_` écarte toute collision avec un champ métier `org`, issue #250).
    """

    def __init__(self, reserved_org_tools):
        self._org = frozenset(reserved_org_tools)

    async def on_list_tools(self, context, call_next):
        """Advertise les axes-contexte plats (`_account=`, …) dans le schéma des tools
        CONCERNÉS (sélectif, `call_axes.axes_for`) → claude.ai sait les envoyer. Sans
        ça, `additionalProperties:false` ferait rejeter l'axe côté client. Les tools
        de capacité (`_org=`) sont schématisés par `_mcp_adapter`, pas ici."""
        tools = await call_next(context)
        # Axe compte DYNAMIQUE : annoncé sur les connecteurs où l'appelant détient
        # plusieurs clés (une requête, threadpool — chemin inbound mono-loop). Sans sub
        # (endpoint anonyme) → rien de plus que les axes statiques.
        sub = None
        try:
            sub = current_user_sub_from_token()
        except Exception:
            pass
        advertised = await run_in_threadpool(call_axes.account_axis_advertised_for, sub)
        out = []
        for t in tools:
            axes = call_axes.axes_for_listing(t.name, advertised)
            if axes:
                t = t.model_copy(update={
                    "parameters": call_axes.inject_schema(t.parameters, axes)})
            out.append(t)
        return out

    async def on_call_tool(self, context, call_next):
        name = getattr(context.message, "name", "") or ""
        args = getattr(context.message, "arguments", None) or {}
        # Pose chaque axe-contexte fourni pour CE tool, en collectant sa fonction de
        # reset AU MOMENT de la pose → reset LIFO dans le `finally` même si une pose
        # ultérieure lève (les tokens déjà posés sont toujours nettoyés).
        undo: list = []
        try:
            # Relevé de résolution : posé EN PREMIER (donc reset EN DERNIER, LIFO) pour
            # que les seams l'aient pendant tout le handler ET que le calllog le relise
            # après. Inerte si rien ne le remplit — un dict vide n'ajoute aucune ligne.
            undo.append((session_org.reset_call_trace, session_org.set_call_trace({})))
            # Le RUN ACTIF de la session, posé en ContextVar pour les seams SYNC
            # (#317). Sans lui, un agent qui encadre son travail par `run_start` n'est
            # reconnu nulle part : la pile vit dans l'état de session (async), que le
            # store ne peut pas lire. Vécu en production le 15/08 — les lignes
            # n'étaient jamais rattachées à leur run, donc jamais libérées à sa
            # fermeture, et leur propre titulaire se voyait refuser l'écriture.
            #
            # ⚠️ MÊME source que le calllog (`server.py`) : le jeton explicite `_run_id=`
            # d'abord, la pile ensuite. J'avais pris le premier pour le run courant —
            # or il n'est posé que si l'appelant l'a passé, ce qu'un agent ne fait pas.
            # Une seule lecture des deux sources, ici, plutôt qu'une par seam.
            if not session_org.current_call_run():
                actif = await doctrine_run.active_run_id(context)
                if actif:
                    undo.append((session_org.reset_call_run,
                                 session_org.set_call_run(actif)))
            # `_org=` (tools de capacité) : posé ici, retiré des kwargs par `_make_tool`.
            if name in self._org and args.get("_org") is not None:
                undo.append((session_org.reset_call_org, await self._pin_org(args["_org"])))
            # Axes plats (`_account=`, … — connecteurs/data) : lus des args BRUTS, posés,
            # puis RETIRÉS des arguments avant le dispatch (la fonction du tool ne les
            # déclare pas → elle validerait en erreur sinon). Les seams de résolution
            # existants (resolve_credential…) lisent la ContextVar.
            for axis in call_axes.axes_for_call(name):
                if axis.param in args:
                    undo.extend(await axis.pin_for(args.pop(axis.param), name))
            return _echo_account(await call_next(context), name)
        finally:
            for reset, tok in reversed(undo):
                reset(tok)

    @staticmethod
    async def _pin_org(org):
        # Garde partagée (`resolve_org_guarded`) = MÊME résolution qu'`oto_use_org` +
        # McpError propre (ce middleware est outermost → une exception opaque serait
        # invisible à Sentry, vécu prod 2026-07-04). Idem l'axe plat `_org=` et oto_call.
        return session_org.set_call_org(await call_axes.resolve_org_guarded(org))


def _reachable_suffix(connector: str) -> str:
    """Suffixe « des clés existent à portée » pour l'enveloppe d'erreur. Sync (DB) —
    à appeler via `run_in_threadpool`. Réutilise le seam d'`access` (aucune règle
    d'accès recopiée ici) ; chaîne vide si rien à portée ou hors contexte."""
    from . import access
    sub = current_user_sub_from_token()
    if not sub:
        return ""
    return access._reachable_hint(sub, access.current_org(sub), connector)


class ErrorEnvelopeMiddleware(Middleware):
    """Contrat d'erreur uniforme rendu à l'agent (D2, oto-backend#124).

    Toute exception d'un tool est réécrite en `McpError` **scrubbée** (pas de
    stacktrace / route interne / id technique) portant `data.oto = {code, retryable,
    hint}` — l'agent peut alors DÉCIDER (retry / abandon / corriger l'input) au lieu
    de deviner sur un message brut. Les tools qui lèvent déjà une `McpError` curée
    voient leur message conservé (cf. `error_taxonomy.classify`).

    **Outermost** (ajouté AVANT `SentryToolErrorMiddleware`) : la chaîne s'exécute de
    l'extérieur vers l'intérieur, donc Sentry (plus interne) attrape l'exception
    d'ORIGINE en premier (vrai traceback capturé), la re-raise, et cette enveloppe la
    normalise EN DERNIER avant qu'elle ne quitte le serveur. Placer l'enveloppe plus
    interne masquerait le vrai traceback à Sentry.
    """

    async def on_call_tool(self, context, call_next):
        try:
            return await call_next(context)
        except Exception as e:
            info = error_taxonomy.classify(e)
            data = {"code": info.code, "retryable": info.retryable}
            hint = info.hint
            # Outil non monté = le PREMIER mur. Sans ça l'agent installe le
            # connecteur, rappelle, et se prend un SECOND mur (« aucun credential »)
            # qui seul portait l'info utile — alors qu'une clé existe peut-être à
            # portée (équipe dont il est membre, autre org). On remonte les deux
            # d'un coup. DB SYNC → threadpool obligatoire (serveur mono-loop), et
            # best-effort : un hoquet ici ne doit jamais masquer l'erreur d'origine.
            if info.connector:
                try:
                    hint = (hint or "") + await run_in_threadpool(
                        _reachable_suffix, info.connector)
                except Exception:  # noqa: BLE001
                    pass
            if hint:
                data["hint"] = hint
            raise McpError(ErrorData(
                code=error_taxonomy.jsonrpc_code(info),
                message=info.message,
                data={"oto": data},
            )) from e
