"""Capacité « relire le schéma d'un tableau » — le pendant lecture de `data_set_schema`.

Un schéma se posait sans pouvoir se relire. Pour connaître l'existant il fallait
`data_list_namespaces` puis filtrer soi-même sur l'id — une jointure imposée à
l'appelant, et toute la liste ramenée en contexte pour un seul tableau.

Ce n'est pas qu'une gêne : `set_schema` pose le schéma **entier**, il ne fusionne
pas. Ajouter un champ sans avoir lu l'existant efface le reste en silence — et la
partie la plus coûteuse à perdre est `schema.key`, la clé métier, qui porte un index
UNIQUE partiel : la re-poster absente lève la contrainte sans que rien ne le dise.
La lecture est donc la condition d'une modification sûre, pas un confort.

Née CAPACITÉ et non tool écrit à la main (ADR 0042 §Convergence des surfaces) : le
dashboard édite déjà les schémas, il lui faut la même lecture, et une seconde
implémentation REST est exactement ce que la convergence combat. Les deux faces
sortent d'un descripteur unique, avec une seule autz.

Autz `SUB_ONLY` au seuil : le vrai gate est le droit de LECTURE sur le tableau, résolu
par le store (org active + ownership), jamais par le nom passé en path — un tableau
hors périmètre répond 404, comme partout ailleurs dans le datastore.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from .. import access
from ..datastore import NamespaceNotFound, make_store
from ._authz import SUB_ONLY
from ._types import AuthzDenied, Capability, ResolvedCtx, RestBinding
from .registry import CAPABILITIES


class GetSchemaInput(BaseModel):
    namespace: str


class SchemaOut(BaseModel):
    # Le champ s'appelle `schema` sur le fil — c'est le nom de la colonne, du paramètre
    # de `data_set_schema` et de ce que lisent les consommateurs. Mais `schema` masque
    # une méthode héritée de `BaseModel` (l'ancienne API v1), ce que pydantic signale à
    # la définition de la classe. D'où le nom python décalé + alias : le schéma OpenAPI
    # est généré `by_alias`, donc la face publique reste bien `schema`.
    model_config = ConfigDict(populate_by_name=True)

    namespace: str
    # `None` = aucun schéma déclaré. C'est l'état NORMAL d'un namespace (le datastore
    # est schema-free par défaut) — d'où un champ nullable plutôt qu'un 404, qui ne
    # saurait pas distinguer « pas de schéma » de « tableau inconnu ».
    declared_schema: Optional[dict] = Field(default=None, alias="schema",
                                            serialization_alias="schema")


def _get_schema(ctx: ResolvedCtx, inp: GetSchemaInput) -> dict:
    # `slot:<nom>` accepté comme partout dans le datastore (ADR 0035 B3) : sans cette
    # résolution la référence passait pour un nom littéral et rendait 404 — une lecture
    # refusée là où tous les tools `data_*` l'acceptent. Le nom RÉSOLU est renvoyé :
    # l'appelant doit voir sur quel tableau il vient de lire.
    namespace = access.resolve_namespace_ref(inp.namespace)
    try:
        schema = make_store(ctx.sub).get_schema(namespace)
    except NamespaceNotFound:
        raise AuthzDenied(404, "namespace_not_found")
    return {"namespace": namespace, "schema": schema}


CAPABILITIES += [
    Capability(
        key="me.datastore.get_schema",
        handler=_get_schema,
        Input=GetSchemaInput,
        Output=SchemaOut,
        authz=SUB_ONLY,
        mcp="data_get_schema",
        rest=RestBinding(verb="GET", path="/api/datastore/namespaces/{namespace}/schema"),
        description=(
            "Read a namespace's declared TYPED schema (the one `data_set_schema` posts). "
            "Returns `{namespace, schema}` — `schema` is null when none is declared, "
            "which is a normal state, not an error. Read it BEFORE amending: "
            "`data_set_schema` posts the schema WHOLE, it does not merge, so adding one "
            "field means re-posting the existing definition plus that field."
        ),
    ),
]
