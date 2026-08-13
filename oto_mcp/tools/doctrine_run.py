"""Runs — verbes de cycle de vie d'un déroulé (ADR 0017, barreau 2).

`run_start` ouvre un run (mint un `run_id`, le pousse dans l'état de session) ;
chaque appel d'outil jusqu'à `run_finish` est **attribué à ce run** par le sink
calllog (corrélation côté serveur, l'agent ne thread rien). Un run avec `doctrine`
= l'exécution d'une doctrine nommée (répétable) ; sans `doctrine` = un run one-shot
(ad-hoc), même trace. Le chargement d'une doctrine reste `oto_procedure(op='get')`
(inchangé). Spine plateforme : chargé explicitement dans `register_all`, hors gate
d'activation.
"""
from __future__ import annotations

import asyncio
import logging

from fastmcp import Context, FastMCP
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData, INVALID_PARAMS

from .. import doctrine_run as dr, run_status

logger = logging.getLogger(__name__)

# Source UNIQUE du vocabulaire (ADR 0058-D5) : le tool, sa docstring — donc le schéma
# que lit l'agent — et toute surface qui valide une issue lisent la même liste. Elle a
# divergé de la prose du bloc A pendant des mois, ce qui est la façon la plus discrète
# de mentir à un agent : le schéma dit une chose, l'instruction une autre.
_OUTCOMES = run_status.OUTCOMES


def _procedure_version(sub: str | None, slug: str) -> int | None:
    """Version COURANTE de la procédure `slug`, lue dans l'ordre où
    `oto_procedure(op='get')` la sert : l'org active d'abord, l'équipe active en
    complément. None si le slug ne désigne aucune procédure — un run ad-hoc, une
    doctrine d'un autre foyer, ou un slug inventé.

    Lecture DB ⇒ appelée HORS boucle (`asyncio.to_thread`)."""
    from .. import access, group_store, org_store
    org_id = access.current_org(sub) if sub else None
    if org_id is not None:
        row = org_store.get_instruction(int(org_id), slug)
        if row and row.get("version") is not None:
            return int(row["version"])
    gid = access.current_group(sub) if sub else None
    if gid is not None:
        row = group_store.get_group_instruction(int(gid), slug)
        if row and row.get("version") is not None:
            return int(row["version"])
    return None


async def _note_procedure_version(doctrine: str | None) -> int | None:
    """L'EMPREINTE du run : QUELLE version de la procédure il exécute.

    `runs.doctrine` ne porte qu'un **slug**, alors que les procédures sont versionnées
    (`org_instructions.version`, snapshot par version dans `org_instruction_revisions`).
    Un run n'enregistrait donc pas ce qu'il a réellement déroulé : rejouer « la même
    procédure » trois semaines plus tard, c'est en jouer une autre sans le savoir.
    ADR 0055-D10 / 0058-D1 : le gel de version EST l'empreinte du run.

    Elle atterrit dans le JOURNAL, à côté du slug tapé par l'agent — le relevé d'appel
    (`session_org.note_call_trace`, allowlist `server._TRACED_ARGS`) verse la valeur
    dans les args de CETTE ligne `run_start`. C'est le domicile cohérent avec le verdict
    du 12/08 : le run est ses faits, pas sa ligne d'index.

    Best-effort, comme tout ce qui entoure un run : une version indisponible n'empêche
    jamais un déroulé de s'ouvrir."""
    if not doctrine:
        return None
    try:
        from .. import session_org
        from ..auth_hooks import current_user_sub_from_token
        sub = current_user_sub_from_token()
        version = await asyncio.to_thread(_procedure_version, sub, doctrine)
    except Exception:
        logger.warning("version de procédure indisponible pour %r (best-effort)",
                       doctrine, exc_info=True)
        return None
    session_org.note_call_trace(doctrine_version=version)
    return version


async def _persist_open(run_id: str, label: str, doctrine: str | None) -> None:
    """Trace durable de l'ouverture (best-effort, off-loop). La pile session reste
    la source du run actif ; ceci ne fait qu'ajouter label/doctrine en base."""
    try:
        from .. import access, db
        from ..auth_hooks import current_user_sub_from_token
        sub = current_user_sub_from_token()
        org_id = access.current_org(sub) if sub else None
        project_id = access.current_project() if sub else None  # projet actif gelé (ADR 0032 B3)
        await asyncio.to_thread(
            db.insert_run, run_id, sub=sub, org_id=org_id, label=label,
            doctrine=doctrine, project_id=project_id)
    except Exception:
        logger.warning("persistance run_start échouée pour run_id=%s (best-effort)",
                       run_id, exc_info=True)


async def _persist_close(run_id: str, outcome: str, note: str | None) -> None:
    try:
        from .. import db
        from ..auth_hooks import current_user_sub_from_token
        # Scope par sub : on ne clôt QUE son propre run (un run_id d'autrui — session
        # réutilisée, #108 — ne peut pas être fermé). No-op si run_id/sub ne matchent pas.
        await asyncio.to_thread(db.finish_run, run_id, outcome, note,
                                sub=current_user_sub_from_token())
    except Exception:
        logger.warning("persistance run_finish échouée pour run_id=%s (best-effort)",
                       run_id, exc_info=True)


def register(mcp: FastMCP) -> None:

    @mcp.tool()
    async def run_start(ctx: Context, label: str, doctrine: str | None = None) -> dict:
        """Open a run (a tracked 'déroulé') so a procedure can be reviewed later.
        Returns a `run_id` — keep it and pass it to `run_finish` when you're done.
        Every tool call until then is automatically attributed to this run.

        Use it for a repeatable doctrine/skill (pass `doctrine`) AND for any one-shot
        procedure worth logging (omit `doctrine`).

        Args:
            label: short human description of what this run does (always logged).
            doctrine: optional — the doctrine/skill slug being executed (as passed to
                oto_procedure op=get). Omit for a one-shot/ad-hoc run.

        Returns `doctrine_version` — the version of that procedure frozen for this run
        (null for an ad-hoc run, or if the slug matches no procedure you can read).
        """
        run_id = dr.new_run_id()
        await dr.push_run(ctx, run_id, label, doctrine)
        # Axe d'appel run_id (#108) : pose SANS reset — la ContextVar meurt avec la
        # requête, mais stampe le tool_call de run_start lui-même sous son run, et
        # amorce l'axe pour l'agent (qui le repasse ensuite via `_run_id=`).
        from .. import session_org
        session_org.set_call_run(run_id)
        version = await _note_procedure_version(doctrine)
        await _persist_open(run_id, label, doctrine)
        return {"run_id": run_id, "label": label, "doctrine": doctrine,
                "doctrine_version": version}

    @mcp.tool()
    async def run_finish(
        ctx: Context, run_id: str, outcome: str, note: str | None = None,
    ) -> dict:
        """Close a run opened with `run_start`.

        Args:
            run_id: the id returned by run_start.
            outcome: one of done | failed | blocked.
            note: optional — what worked, where it broke, what was missing.
        """
        if outcome not in _OUTCOMES:
            raise McpError(ErrorData(
                code=INVALID_PARAMS,
                message=f"outcome must be one of {', '.join(_OUTCOMES)}",
            ))
        # La clôture appartient au déroulé qu'elle clôt : sans ce stamp, `tool_calls.
        # run_id` reste NULL sur cette ligne (l'axe `_run_id=` n'est pas advertisé sur
        # les verbes de run) et la timeline d'un run — `get_run`, qui filtre sur la
        # colonne — n'affiche jamais sa propre fin, pendant que l'issue est lue de
        # cette ligne-là. Même geste que `run_start`, symétrique et sans reset.
        from .. import session_org
        session_org.set_call_run(run_id)
        removed = await dr.pop_run(ctx, run_id)
        await _persist_close(run_id, outcome, note)
        # TROISIÈME voie de libération du verrou de file (#317) : un run qui se
        # termine ne travaille plus, donc ne tient plus rien — quel que soit son
        # issue. C'est la réponse au cas mesuré : un worker disparu laissait sa ligne
        # bloquée jusqu'à expiration du bail, soit 18 jours sur la seule ligne
        # réservée qu'ait portée la production, sans que personne ne le voie.
        # Best-effort et HORS de la boucle : libérer est un service rendu, jamais une
        # condition de la fermeture du run — un run doit pouvoir se clore même si la
        # base tousse.
        liberees = 0
        try:
            from starlette.concurrency import run_in_threadpool
            from .. import db
            liberees = await run_in_threadpool(db.datastore_release_by_run, run_id)
        except Exception:  # noqa: BLE001
            logger.warning("libération des lignes du run %s échouée (best-effort)", run_id)
        out = {"ok": True, "run_id": run_id, "outcome": outcome,
               "was_open": removed is not None}
        if liberees:
            # Dit, parce qu'une ligne rendue sans qu'on l'ait demandé doit se voir :
            # l'agent saura que son travail en cours a été relâché.
            out["rows_released"] = liberees
        return out
