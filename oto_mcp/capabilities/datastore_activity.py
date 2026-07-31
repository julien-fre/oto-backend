"""Capacités « activité d'un tableau » (journal de travail du datastore, ADR 0046 b4).

Deux lectures, REST-only (le dashboard les rend ; l'agent, lui, a déjà son parcours
dans ses propres réponses `data_*`) :

- `me.datastore.row_activity`  — GET …/rows/{row_id}/activity : ce qui est arrivé à UNE ligne ;
- `me.datastore.activity`      — GET …/activity              : ce qui est arrivé au TABLEAU.

Le journal montre les **deux surfaces** : `kind='mcp'` = appel d'agent, `kind='rest'`
= geste fait au cockpit (posé par `datastore_journal` via `calllog.log_rest_call`).
Avant, seul le MCP était visible — un clic de transition dans le dashboard ne laissait
aucune trace exploitable, d'où l'angle mort « quelle ligne vient de changer d'état ? ».

Autz : `SUB_ONLY` au seuil, le vrai gate est la LECTURE du namespace — résolu par le
store (scopé org active + ownership), jamais par l'id nu passé en path. Un namespace
hors périmètre est un 404 (on ne divulgue pas son existence), comme partout ailleurs
dans le datastore.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from .. import datastore_journal, db
from ..datastore import NamespaceNotFound, RowNotFound, make_store
from ._authz import SUB_ONLY
from ._types import AuthzDenied, Capability, ResolvedCtx, RestBinding
from .registry import CAPABILITIES

# Rétention du calllog (prune 30 j) : un journal de TRAVAIL, pas un audit permanent.
# La surface l'annonce pour que l'UI puisse le dire à l'utilisateur.
RETENTION_DAYS = 30


class RowActivityInput(BaseModel):
    namespace: str
    row_id: str


class NamespaceActivityInput(BaseModel):
    namespace: str
    limit: int = 50


def _store(sub: Optional[str]):
    return make_store(sub)


def _attach_emails(entries: list[dict]) -> list[dict]:
    """Renseigne `email` sur les entrées — l'auteur du geste, en UN lot.

    `tool_calls.email` est NULL en base : ni le sink MCP (`_calllog_identity`, qui ne
    rend que le `sub`) ni `log_rest_call` ne le peuplent. On résout donc à la LECTURE
    (une seule requête pour toute la page, y compris les lignes déjà en base) plutôt
    que d'ajouter une résolution au chemin chaud de chaque geste. Sans ça la colonne
    « qui » du parcours reste vide ou n'affiche qu'un uuid — sur une feature dont
    l'énoncé est précisément « voir l'historique des actions »."""
    subs = [e.get("sub") for e in entries if e.get("sub") and not e.get("email")]
    if not subs:
        return entries
    by_sub = db.emails_by_subs(subs)
    for e in entries:
        if not e.get("email"):
            e["email"] = by_sub.get(e.get("sub"))
    return entries


def _row_activity(ctx: ResolvedCtx, inp: RowActivityInput) -> dict:
    store = _store(ctx.sub)
    try:
        row = store.get_row(inp.namespace, inp.row_id)
    except NamespaceNotFound:
        raise AuthzDenied(404, "namespace_not_found")
    except RowNotFound:
        raise AuthzDenied(404, "row_not_found")
    key = store.declared_key(inp.namespace)
    key_value = row.get(key) if key else None
    nsctx = datastore_journal.context(store, inp.namespace)
    # Le PROPRIÉTAIRE part avec la requête : l'axe « clé métier » est une recherche de
    # sous-chaîne dans les args, il doit être borné au tenant (sinon une clé banale
    # remonterait les gestes d'une autre org).
    activity = db.datastore_row_activity(
        inp.row_id, str(key_value) if key_value is not None else None,
        owner_type=nsctx.owner_type, owner_id=nsctx.owner_id)
    # Toutes ces entrées parlent de CETTE ligne (c'est le critère de la requête) → son
    # libellé les qualifie toutes, y compris celles matchées par clé métier dont les
    # args ne portent pas d'`id`. Pas de relecture : la ligne est déjà là.
    title = row.get(nsctx.title_key) if nsctx.title_key else None
    for entry in activity:
        entry["row_title"] = None if title is None else str(title)
    _attach_emails(activity)
    return {"activity": activity, "key": key, "retention_days": RETENTION_DAYS}


def _activity(ctx: ResolvedCtx, inp: NamespaceActivityInput) -> dict:
    store = _store(ctx.sub)
    try:
        ns_id = store.resolve_ns_id(inp.namespace)
    except NamespaceNotFound:
        raise AuthzDenied(404, "namespace_not_found")
    nsctx = datastore_journal.context(store, inp.namespace, ns_id=ns_id)
    # Le namespace est résolu ICI (une fois) et passé sous ses DEUX formes au journal :
    # les gestes REST y sont enregistrés par `ns_id`, les appels MCP par le nom OU l'id
    # tels que l'agent les a tapés. Le PROPRIÉTAIRE part avec — un nom de tableau n'est
    # unique que par propriétaire, l'axe nom doit être borné au tenant (fuite cross-org
    # sinon, cf. `db.datastore_namespace_activity`).
    activity = db.datastore_namespace_activity(
        ns_id, nsctx.name, owner_type=nsctx.owner_type, owner_id=nsctx.owner_id,
        limit=inp.limit)
    datastore_journal.attach_titles(ns_id, nsctx.title_key, activity)
    _attach_emails(activity)
    return {"activity": activity, "retention_days": RETENTION_DAYS}


CAPABILITIES += [
    Capability(
        key="me.datastore.row_activity",
        handler=_row_activity,
        Input=RowActivityInput,
        authz=SUB_ONLY,
        mcp=None,  # opt-out explicite : lecture de cockpit, l'agent a son propre fil
        rest=RestBinding(
            verb="GET",
            path="/api/datastore/namespaces/{namespace}/rows/{row_id}/activity",
        ),
        description="Parcours d'une ligne du datastore (gestes d'agent et de dashboard).",
    ),
    Capability(
        key="me.datastore.activity",
        handler=_activity,
        Input=NamespaceActivityInput,
        authz=SUB_ONLY,
        mcp=None,
        rest=RestBinding(
            verb="GET",
            path="/api/datastore/namespaces/{namespace}/activity",
        ),
        description="Activité d'un tableau du datastore (qui a touché quoi, depuis quel état).",
    ),
]
