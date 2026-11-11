"""`me.datastore.column_sources` — GET …/column-sources : QUI a trouvé chaque colonne.

L'en-tête d'une colonne dit d'où viennent ses valeurs : « Jev », « Hunter 62 %,
FullEnrich 30 % », « à la main ». Lu dans le journal des révisions de ligne, qui
estampille chaque écriture de sa face (`source`), de l'outil qui l'a faite (`outil`) et
du fournisseur derrière la valeur (`fournisseur`, `geste.par_fournisseur`) — jamais
déclaré à côté, donc jamais périmé, et jamais deviné (`db/historique.
sources_des_colonnes`).

REST seule : c'est une lecture d'écran ; l'agent a `data_row_history`.

Autz : `SUB_ONLY` au seuil, le vrai gate est la LECTURE du tableau, résolue par le
store (org active + propriété) — un tableau hors périmètre est un 404, comme partout
dans le datastore.

⚠️ Une lecture d'agrégat sur le journal (bornée : `statement_timeout`, au plus
`SOURCES_MAX_REVISIONS` révisions, route lourde). Gardée `_TTL_S` secondes par tableau
dans ce processus : l'en-tête est relu à chaque ouverture du tableau, la provenance d'une
colonne ne change pas d'une seconde à l'autre.
"""
from __future__ import annotations

import threading
import time
from typing import Optional

from pydantic import BaseModel, Field

from ...datastore.core import DatastoreNotFound, make_store
from ...datastore.identite import Adresse
from ...db import historique
from ...db.journal_revisions import retention_jours
from .._authz import SUB_ONLY
from .._lecture_bornee import bornee
from .._types import AuthzDenied, Capability, RestBinding, ResolvedCtx
from ..registry import CAPABILITIES
from .common import EntreeDatastore

_TTL_S = 120.0
_MAX_ENTREES = 512
_cache: dict[int, tuple[float, dict]] = {}
_verrou = threading.Lock()


class ColumnSourcesInput(EntreeDatastore):
    datastore: Adresse


class SourceGroup(BaseModel):
    source: Optional[str] = Field(default=None, description=(
        "La face qui a écrit : `agent`, `console` (à la main), `api`, `import`, "
        "`upload`, `system` ; `null` = écrit hors du serveur ou avant l'estampille."))
    tool: Optional[str] = Field(default=None, description=(
        "L'outil qui a écrit la valeur (`jev_rows`, `waterfall_email_act`, l'outil "
        "qu'un `data_write(via=…)` a prouvé). `data_write` seul = un agent a écrit, "
        "sans dire où il l'a trouvé. `null` = pas connu."))
    provider: Optional[str] = Field(default=None, description=(
        "Le fournisseur derrière la valeur (`hunter`, `fullenrich`…), posé par une "
        "cascade ; `null` ailleurs."))
    cells: int


class ColumnSources(BaseModel):
    columns: dict[str, list[SourceGroup]] = Field(description=(
        "Par colonne, les cellules TENUES aujourd'hui groupées par ce qui a écrit leur "
        "valeur actuelle, du plus fourni au moins fourni. Une colonne absente n'a "
        "aucune valeur écrite dans la fenêtre lue."))
    revisions_read: int
    complete: bool = Field(description=(
        "`false` = le tableau a plus de révisions que la fenêtre lue : les cellules "
        "écrites avant ne sont pas comptées."))
    retention_days: int
    since: str = Field(description="La mise en service du journal (" +
                       historique.MISE_EN_SERVICE + ") : rien avant n'est connu.")


def _lire(ns_id: int) -> dict:
    maintenant = time.monotonic()
    with _verrou:
        garde = _cache.get(ns_id)
        if garde and maintenant - garde[0] < _TTL_S:
            return garde[1]
    brut = historique.sources_des_colonnes(ns_id)
    servi = {
        "columns": {
            champ: [{"source": g["source"], "tool": g["outil"],
                     "provider": g["fournisseur"], "cells": g["cellules"]}
                    for g in groupes]
            for champ, groupes in brut["colonnes"].items()},
        "revisions_read": brut["revisions_lues"],
        "complete": brut["complet"],
        "retention_days": retention_jours(),
        "since": historique.MISE_EN_SERVICE,
    }
    with _verrou:
        if len(_cache) >= _MAX_ENTREES:
            _cache.clear()
        _cache[ns_id] = (maintenant, servi)
    return servi


def _column_sources(ctx: ResolvedCtx, inp: ColumnSourcesInput) -> dict:
    store = make_store(ctx.sub)
    try:
        ns_id = store.resolve_ns_id(inp.datastore)
    except DatastoreNotFound:
        raise AuthzDenied(404, "datastore_not_found")
    return _lire(int(ns_id))


CAPABILITIES += [
    Capability(
        key="me.datastore.column_sources",
        handler=bornee(_column_sources),
        Input=ColumnSourcesInput,
        Output=ColumnSources,
        authz=SUB_ONLY,
        mcp=None,  # lecture d'écran ; l'agent a `data_row_history`
        rest=RestBinding(
            verb="GET",
            path="/api/datastores/{datastore}/column-sources",
        ),
        description="D'où viennent les valeurs de chaque colonne (outil, fournisseur, face).",
    ),
]
