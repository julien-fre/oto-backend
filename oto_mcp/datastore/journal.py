"""Journal des gestes datastore faits depuis le dashboard (REST) + libellés du parcours.

Colle mince entre les routes `/api/datastore/*` et le journal unique (`calllog`,
table `tool_calls`). Deux raisons d'exister, toutes deux hors des handlers :

1. **Écrire le SENS d'un geste.** Un clic « → ecarte » dans le cockpit passait en
   REST et n'était journalisé qu'au grain route (`PATCH /api/datastore/…`) : on
   voyait qu'une écriture avait eu lieu, jamais LAQUELLE ni depuis quel état. On
   pose donc une ligne `kind='rest'` nommée dans le vocabulaire MCP (`data_write`…)
   portant `namespace`/`ns_id`/`id`/`fields`/`from_status`/`to_status`.
   ⚠️ **`from_status` vient de la MUTATION elle-même** (relevé `DatastorePg._trace`),
   jamais d'une relecture faite avant l'appel : c'est lui qui rend l'annulation
   possible, il doit donc être l'état sur lequel la transition a été validée.
2. **Rendre les entrées lisibles.** Le journal cite des `row_id` (uuid) ; le
   parcours affiche le champ `role="title"` de la ligne, résolu EN UN LOT borné.

Tout est **best-effort** : une résolution ratée journalise moins, jamais ne fait
échouer le geste métier.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

from .. import calllog, db
from . import schema as dsv2

logger = logging.getLogger(__name__)

# Vocabulaire commun aux deux surfaces : ces noms sont ceux des tools MCP
# (`tools/datastore.py`), pas des routes — les lectures du journal filtrent dessus.
TOOL_WRITE = "data_write"
TOOL_DELETE = "data_delete_row"
TOOL_RELEASE = "data_release"
TOOL_CLAIM_NEXT = "data_claim_next"
# `data_claim` (réserver une ligne NOMMÉE) n'a pas d'équivalent MCP à ce jour : un
# agent qui draine prend la suivante, un humain qui appelle choisit sa ligne. Le nom
# reste dans le vocabulaire `data_*` — c'est ce que les lectures d'activité filtrent
# (`tool LIKE 'data\_%'`), et le parcours d'une ligne doit montrer sa réservation.
TOOL_CLAIM = "data_claim"


@dataclass(frozen=True)
class NsContext:
    """Ce qu'il faut savoir d'un tableau pour journaliser un geste : son id, son nom
    canonique (l'appelant a pu le nommer par son id), les clés de rôle du schéma et
    son PROPRIÉTAIRE (le nom d'un tableau n'est unique que par propriétaire — la
    lecture d'activité en a besoin pour ne pas matcher l'homonyme d'un autre tenant)."""

    ns_id: Optional[int]
    name: str
    status_key: Optional[str] = None
    title_key: Optional[str] = None
    owner_type: Optional[str] = None
    owner_id: Optional[str] = None


def from_trace(trace: dict, namespace: str) -> NsContext:
    """Contexte de journal issu du RELEVÉ rempli par la mutation elle-même
    (`DatastorePg._trace`) — zéro requête ajoutée, zéro course sur l'état d'avant.
    Un relevé vide (mutation qui a échoué avant de le remplir) dégrade proprement."""
    return NsContext(
        ns_id=trace.get("ns_id"),
        name=trace.get("namespace") or str(namespace),
        status_key=trace.get("status_key"),
        title_key=trace.get("title_key"),
    )


def context(store, namespace: str, ns_id: Optional[int] = None) -> NsContext:
    """Résout le tableau UNE fois pour le journal (id + nom canonique + rôles + owner).

    Chemin de LECTURE seulement (surfaces d'activité) : une écriture, elle, passe par
    `from_trace` — la mutation a déjà tout sous la main. Passe par le store → même gate
    de visibilité que le geste. **Best-effort de bout en bout** : ni la résolution ni la
    lecture de la ligne ne doivent pouvoir faire échouer l'appelant (D2) — un hoquet du
    pool PG rendrait sinon un 500 sur un geste qui aurait parfaitement abouti.
    `ns_id` évite une seconde résolution quand l'appelant l'a déjà (gate d'accès).
    """
    try:
        ns_id = int(ns_id if ns_id is not None else store.resolve_ns_id(namespace))
        ns = db.get_datastore_namespace_by_id(ns_id) or {}
        schema = ns.get("schema")
        return NsContext(
            ns_id=ns_id,
            name=ns.get("namespace") or str(namespace),
            status_key=(dsv2.status_field(schema) or {}).get("key"),
            title_key=(dsv2.title_field(schema) or {}).get("key"),
            owner_type=ns.get("owner_type"),
            owner_id=(None if ns.get("owner_id") is None else str(ns.get("owner_id"))),
        )
    except Exception:  # noqa: BLE001 — le journal ne décide pas de l'accès
        logger.debug("contexte de journal datastore indisponible (%s)", namespace, exc_info=True)
        return NsContext(ns_id=None, name=str(namespace))


def status_of(row: Any, ctx: NsContext) -> Optional[Any]:
    """État porté par une ligne rendue par le store (None hors cycle de vie)."""
    if not ctx.status_key or not isinstance(row, dict):
        return None
    return row.get(ctx.status_key)


def record(tool: str, *, sub: Optional[str], ctx: NsContext, row_id: Optional[str] = None,
           fields: Any = None, from_status: Any = None, to_status: Any = None) -> None:
    """Pose la ligne de journal du geste. Un seul appel par route."""
    calllog.log_rest_call(
        tool,
        sub=sub,
        args={"namespace": ctx.name, "ns_id": ctx.ns_id, "id": row_id,
              "from_status": from_status, "to_status": to_status},
        fields=fields,
    )


def attach_titles(ns_id: Optional[int], title_key: Optional[str],
                  entries: list[dict]) -> list[dict]:
    """Renseigne `row_title` sur les entrées qui citent une ligne — UN lot borné par
    le nombre d'entrées, jamais une requête par entrée. Une ligne supprimée depuis
    (ou un tableau sans champ `role="title"`) laisse simplement `row_title` à None."""
    if not ns_id or not title_key:
        return entries
    ids = {e.get("row_id") for e in entries if e.get("row_id")}
    if not ids:
        return entries
    data_by_id = db.datastore_rows_by_ids(int(ns_id), sorted(ids))
    for e in entries:
        data = data_by_id.get(e.get("row_id"))
        if data is not None:
            value = data.get(title_key)
            e["row_title"] = None if value is None else str(value)
    return entries
