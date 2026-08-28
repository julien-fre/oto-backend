"""`DynamicInstructionsMiddleware` — le contexte guide injecté dans la surface LLM."""
from __future__ import annotations

import logging

from fastmcp.server.middleware import Middleware
from starlette.concurrency import run_in_threadpool

from ..auth.hooks import current_user_sub_from_token

logger = logging.getLogger(__name__)


_GUIDE_GET_TOOL = "oto_procedure"
_GUIDE_TOOL = "oto_guide"

# « Cette session n'est pas un endpoint de projet publié » — distinct de « projet
# publié SANS prose » (None), qui court-circuite quand même le socle plateforme.
_PAS_DE_PROJET_PUBLIE = object()


def _published_project_instructions():
    """Prose du projet publié servi à la session courante, ou `_PAS_DE_PROJET_PUBLIE`.

    **Sync (DB) — à appeler via `run_in_threadpool`** (serveur mono-loop). Lit une
    ContextVar (le projet anonyme du sous-domaine) : `run_in_threadpool` propage le
    contexte, même patron que `_reachable_suffix`."""
    from .. import instructions, subdomain_project
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
    from .. import access, instructions
    return instructions.compose_session(sub, access.current_org(sub))


class DynamicInstructionsMiddleware(Middleware):
    """Injecte le contexte guide de l'org dans la surface vue par le LLM, par-(sub,
    org), au lieu de dépendre d'un appel volontaire de lecture de guide (canal fragile,
    otomata-private#49, amende ADR 0014). Deux points d'injection, selon la NATURE :

    - **artefact composé** (blocs A/C, #50) → `on_initialize` REMPLACE
      `result.instructions` par `instructions.compose_session(sub, org)`
      (le « cheval de Troie », relu par session ; Claude rehandshake par conversation).
    - **index des guides NOMMÉS** (skills) → `on_list_tools` enrichit la
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
        # noqa: SILENT — dette déclarée : sub avalé, la requête devient anonyme sans dire pourquoi (#424, verdict C)
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
        # noqa: SILENT — dette déclarée : sub avalé, la requête devient anonyme sans dire pourquoi (#424, verdict C)
        except Exception:
            sub = None
        if not sub:
            return tools
        try:
            from .. import access, instructions, guide_store
            org_id = access.current_org(sub)
            # Deux loaders de prose on-demand, même canal de découverte : l'index
            # per-(sub, org) enrichit la description de l'outil qui les charge.
            extra = {
                _GUIDE_GET_TOOL: instructions.skills_index_md(org_id),
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
            logger.warning("enrichissement d'index (procédures/guides) échoué pour sub=%s "
                           "(fail-open)", sub, exc_info=True)
            return tools
