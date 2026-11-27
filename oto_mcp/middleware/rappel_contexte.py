"""Le rappel du contexte d'org en tête des réponses d'outils (oto-backend#1041).

La règle (quand rappeler, d'où vient le verdict, ses bornes) vit dans
`oto_mcp/rappel_contexte.py`. Ici, la PLOMBERIE — en deux temps, parce que la chaîne
l'impose :

- le verdict a besoin de l'**org de l'appel**, que `CallContextMiddleware` pose
  (`_org=`, projet, run) et retire à sa sortie : il se prend DANS sa portée —
  `ConstatContexteMiddleware`, juste sous lui ;
- la ligne doit survivre à tout ce qui réécrit le canal texte : le rendu du vide
  (`EmptyResult`) et le corps markdown (`MarkdownBody`) remplacent le contenu entier,
  et ils sont PLUS EXTERNES que le contexte d'appel. Elle se pose donc au-dessus d'eux —
  `RappelContexteMiddleware`, juste sous le refus des comptes en pause.

Les deux se parlent par un relevé posé en `ContextVar` par l'externe et rempli par
l'interne (même tâche, même contexte). L'interne sans l'externe ne sert rien ;
l'externe sans l'interne ne sert rien non plus — ni l'un ni l'autre n'échoue.

`oto_call` fait exception : ses axes (`_org=`, `_project=`, … dans `arguments`) ne
sont posés que DANS son handler (`tools/meta.executer_cible`) et retirés avant qu'il
rende. Pris avant l'appel, le verdict lisait l'org par défaut du compte — qui suit le
dernier workspace ouvert — et le rappel nommait une org étrangère à l'appel. Pour lui,
le verdict se prend donc APRÈS, sous l'org que la cible a notée (`noter_org_cible`).
"""
from __future__ import annotations

import contextvars
import logging

from fastmcp.server.middleware import Middleware
from fastmcp.tools.tool import ToolResult
from mcp.types import TextContent
from starlette.concurrency import run_in_threadpool

from .. import rappel_contexte, session_org
from ..db._hors_boucle import HorsBoucle
from .call_context import _cible

logger = logging.getLogger(__name__)

_RELEVE: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "oto_rappel_contexte", default=None)


def noter_org_cible(org_id) -> None:
    """La cible d'un `oto_call` s'est résolue sous `org_id` : noté dans le relevé de
    l'appel en cours, pour que le verdict se prenne sous CETTE org. Écrasé à chaque
    cible : la plus externe finit en dernier (une recette appelée par `oto_call` lance
    ses propres cibles avant), c'est donc la cible directe qui reste. Sans relevé
    (hors appel MCP) ou sans org, rien."""
    releve = _RELEVE.get()
    if releve is not None and isinstance(org_id, int) and not isinstance(org_id, bool):
        releve["org_cible"] = org_id


def avec_lignes_en_tete(result, lignes):
    """`result` avec `lignes` (les vides écartées) EN TÊTE de son canal texte — le
    reste tel quel. Une réponse en erreur, ou sans ligne, passe inchangée."""
    lignes = [t for t in lignes if t]
    if not lignes or getattr(result, "is_error", False):
        return result
    return ToolResult(
        content=[*(TextContent(type="text", text=t) for t in lignes),
                 *(result.content or [])],
        structured_content=getattr(result, "structured_content", None),
        meta=getattr(result, "meta", None),
    )


class RappelContexteMiddleware(Middleware):
    """Pose la ligne de rappel EN TÊTE du canal texte d'une réponse réussie."""

    async def on_call_tool(self, context, call_next):
        releve: dict = {}
        avis: list = []
        jeton = _RELEVE.set(releve)
        jeton_avis = session_org.set_call_avis(avis)
        try:
            result = await call_next(context)
        finally:
            session_org.reset_call_avis(jeton_avis)
            _RELEVE.reset(jeton)
        # Les avis de l'appel (`session_org.noter_avis`) prennent la même place que le
        # rappel, et pour la même raison : rien de ce qui réécrit le canal texte ne
        # passe au-dessus.
        return avec_lignes_en_tete(result, [releve.get("ligne"), *avis])


class ConstatContexteMiddleware(Middleware):
    """Prend le verdict dans la portée du contexte d'appel, hors de la boucle ; éteint
    le rappel quand l'outil servi est `oto_context` lui-même."""

    async def on_call_tool(self, context, call_next):
        releve = _RELEVE.get()
        if releve is None:
            return await call_next(context)
        # `oto_call` dispatche un autre outil (ADR 0036) : c'est la CIBLE qui compte —
        # `oto_call(name="oto_context")` est une lecture, et n'est pas rappelé.
        enveloppe = getattr(context.message, "name", "") or ""
        outil = _cible(enveloppe, getattr(context.message, "arguments", None) or {})
        if enveloppe == "oto_call":
            return await self._apres_la_cible(releve, outil, call_next, context)
        cle = None
        try:
            cle, releve["ligne"] = await run_in_threadpool(rappel_contexte.pour_l_appel, outil)
        except HorsBoucle:
            raise
        except Exception:  # noqa: BLE001 — un rappel ne casse jamais l'appel qu'il accompagne
            logger.warning("rappel du contexte d'org indisponible pour %s", outil,
                           exc_info=True)
        result = await call_next(context)
        if (cle is not None and outil == rappel_contexte.OUTIL_CONTEXTE
                and not getattr(result, "is_error", False)):
            rappel_contexte.constater_lecture(cle)
        return result

    @staticmethod
    async def _apres_la_cible(releve: dict, outil: str, call_next, context):
        """`oto_call` : le verdict APRÈS l'appel, sous l'org notée par la cible. Pas
        d'org notée (la cible refusée avant de se résoudre) = pas de rappel — mieux vaut
        se taire que nommer l'org d'un autre appel."""
        result = await call_next(context)
        org_cible = releve.get("org_cible")
        if org_cible is None or getattr(result, "is_error", False):
            return result
        try:
            cle, releve["ligne"] = await run_in_threadpool(
                rappel_contexte.pour_l_appel, outil, org_cible)
        except HorsBoucle:
            raise
        except Exception:  # noqa: BLE001 — un rappel ne casse jamais l'appel qu'il accompagne
            logger.warning("rappel du contexte d'org indisponible pour %s", outil,
                           exc_info=True)
            return result
        if cle is not None and outil == rappel_contexte.OUTIL_CONTEXTE:
            rappel_contexte.constater_lecture(cle)
        return result
