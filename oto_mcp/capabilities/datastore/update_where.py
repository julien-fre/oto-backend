"""Mettre à jour des lignes PAR FILTRE — une règle posée côté serveur (`data_update_where`).

Une CAPACITÉ, pas un `@mcp.tool()` (ADR 0042 §Convergence des surfaces) : la même
règle sert l'agent (face MCP) et un script (face REST), sous la même autz. Le travail
— sélection, précondition de révision, déclaration de la colonne, budget de temps —
vit dans le STORE (`datastore/par_filtre.py`) ; ici, la seule traduction des refus.

`POST …/{datastore}/rows/update_where` : le filtre et `set` sont des objets, que seul
un corps porte sans les encoder en chaîne de query.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from ...datastore.core import DatastoreNotFound, DatastoreReadOnly, make_store
from ...datastore import identite
from ...datastore.identite import Adresse
from .._authz import SUB_ONLY
from .._types import AuthzDenied, Capability, DeclaredError, ResolvedCtx, RestBinding
from ..registry import CAPABILITIES
from ._refus import _JETON_MAL_PLACE, _REFUS_D_ADRESSE
from .common import EntreeDatastore, ns_not_found
from .rows import _adresse, _verifier_contenu, _write_refusal


class UpdateWhereInput(EntreeDatastore):
    datastore: Adresse
    set: dict = Field(description=(
        "The columns to set on every matching row: `{\"tier\": \"Tier 1\"}`. A value may "
        "carry its layers (`{\"valeur\": \"Tier 1\", \"comment\": \"rule: fit>=8\"}`). "
        "A column the schema does not declare yet is DECLARED first (type inferred). "
        "A value that would ERASE (null, \"\", [], `@empty`) is refused."))
    filter: Optional[dict] = Field(default=None, description=(
        "Same grammar as `data_rows`: `{col: value}` or `{col: {op: value}}`."))
    filters: Optional[list] = Field(default=None, description=(
        "Same grammar as `data_rows`: `[{field, op, value}]`, AND-ed with `filter`. "
        "Neither = EVERY row of the table."))
    only_if_empty: bool = Field(default=False, description=(
        "Only rows where every `set` column is empty — the `data_rows` `empty` "
        "predicate: absent, null, \"\" or `@empty`. Run ordered rules with it: first "
        "match wins."))
    dry_run: bool = Field(default=False, description=(
        "Write nothing: return `matched` and a few `sample` rows with from → to."))
    cursor: Optional[str] = Field(default=None, description=(
        "The `next_cursor` of a previous call that ran out of time — resumes after it."))


class UpdateWhereResult(BaseModel):
    """Des COMPTES, jamais des lignes. Les relevés du geste (`notices`,
    `hors_schema`, `valeurs_ecartees`…) sont cumulés sur toutes les lignes ; les plus
    lus sont déclarés, les autres passent (`extra="allow"`), comme sur un lot."""
    model_config = ConfigDict(extra="allow")

    ns_id: Optional[int] = Field(default=None, description=identite.DESCRIPTION)
    # Lignes qui répondent au filtre — APRÈS le curseur quand il est passé.
    matched: int
    dry_run: Optional[bool] = None
    sample: Optional[list] = None
    would_declare: Optional[list] = None
    updated: Optional[int] = None
    # Déjà à la valeur visée : rien à écrire, rien au journal.
    unchanged: Optional[int] = None
    # Changées entre la sélection et l'écriture, ou sous bail d'un autre : SAUTÉES.
    conflicts: Optional[int] = None
    conflicts_sample: Optional[list] = None
    refused: Optional[int] = None
    refused_sample: Optional[list] = None
    declared: Optional[list] = None
    next_cursor: Optional[str] = None
    hors_schema: Optional[list[str]] = None
    hors_schema_hint: Optional[str] = None
    valeurs_ecartees: Optional[list[dict]] = None
    valeurs_ecartees_hint: Optional[str] = None
    valeurs_ecartees_total: Optional[int] = None
    notices: Optional[list[str]] = None


def _update_where(ctx: ResolvedCtx, inp: UpdateWhereInput) -> dict:
    ns, _ = _adresse(inp.datastore)
    _verifier_contenu(inp.set)
    store = make_store(ctx.sub)
    try:
        out = store.update_where(ns, inp.set, filter=inp.filter, filters=inp.filters,
                                 only_if_empty=inp.only_if_empty, dry_run=inp.dry_run,
                                 cursor=inp.cursor)
    except DatastoreNotFound:
        raise ns_not_found(ctx.sub, ns)
    except DatastoreReadOnly:
        raise AuthzDenied(403, "datastore_read_only")
    except ValueError as e:
        raise _write_refusal(e)
    return {**out, **store.off_schema_report(), **identite.numero(store.dernier_tableau)}


CAPABILITIES += [
    Capability(
        key="me.datastore.update_where",
        handler=_update_where,
        Input=UpdateWhereInput,
        Output=UpdateWhereResult,
        authz=SUB_ONLY,
        mcp="data_update_where",
        rest=RestBinding(verb="POST",
                         path="/api/datastores/{datastore}/rows/update_where"),
        errors=_REFUS_D_ADRESSE + (
            _JETON_MAL_PLACE,
            DeclaredError(400, "row_invalid",
                          "la valeur posée sort des `options` déclarées, ou les "
                          "premières lignes tentées sont toutes refusées par le format : "
                          "la règle est refusée entière, rien n'est écrit"),
            DeclaredError(400, "unknown_column",
                          "la colonne visée n'est pas déclarée et n'a pas pu l'être "
                          "(tableau sans schéma, après la date oto#124) : la déclarer "
                          "avec `data_patch_schema`"),
            DeclaredError(400, "invalid_row_input",
                          "`set` ou le filtre est mal formé, ou la règle est fautive "
                          "(valeur qui efface, clé métier, couche `origine`, colonne "
                          "`readonly` hors `only_if_empty`) : le message dit laquelle"),
        ),
        description=(
            "Set columns on EVERY row matching a filter, server-side, without pulling "
            "ids into your context — e.g. derive a tier from several columns. `filter`/"
            "`filters` = the `data_rows` grammar; `set` = `{column: value}`. "
            "`only_if_empty=true` skips rows where the column already holds a value "
            "(`@empty` counts as empty), so "
            "ordered rules (Hot, then Tier 1, 2, 3) run first-match-wins. Start with "
            "`dry_run=true`: it returns `matched` and sample rows (from → to). Each row "
            "goes through the same checks as `data_write(id=…)` and lands in its "
            "`data_row_history`; a row changed since selection is skipped "
            "(`conflicts`), never overwritten. A missing target column is declared "
            "first. Returns counts, not rows. If `next_cursor` is set, the time budget "
            "ran out: call again with `cursor=<next_cursor>`."),
    ),
]
