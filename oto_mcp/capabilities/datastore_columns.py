"""Capacités de FORME d'un tableau : purger une colonne (#296), patcher le schéma
par clé (#388).

Purger une colonne (oto-backend#296).

Retirer un champ du schéma le sort de la VUE ; la clé, elle, reste dans le blob de
chaque ligne — donc elle se rend encore à la lecture, et elle attire les écritures.
Après un renommage (`actualite_sociale` → `analyse1`), l'ancien nom décrit souvent
le contenu mieux que le nouveau : trois agents successifs ont écrit dedans en
croyant viser juste, la valeur partant dans une colonne que l'interface ne lit pas.
Écrire `null` n'efface rien (une clé nulle reste une clé). Ce geste est le seul qui
fasse disparaître la colonne, et donc le piège.

Une CAPACITÉ, pas un `@mcp.tool()` (ADR 0042 §Convergence des surfaces) : le verbe
appartient à la plateforme, et le jour où le cockpit affiche « supprimer cette
colonne » — l'endroit naturel, puisque la colonne morte trompe aussi l'humain qui
relit une fiche — la face REST est une ligne `rest=` ici, pas une seconde
implémentation avec sa propre autz à tenir en phase. `rest=None` en attendant,
opt-out explicite : une route destructive que rien n'appelle est une surface qu'on
ne teste pas.

Les gardes vivent dans le STORE (`DatastorePg.drop_column`), pas ici : `confirm`,
le refus d'une clé encore déclarée au schéma et celui des colonnes de plateforme
valent pour toute surface, présente ou future. Autz `SUB_ONLY` au seuil ; le vrai
gate est le droit d'ÉCRITURE sur le tableau, résolu par le store (org active +
ownership) — un tableau hors périmètre répond 404, comme partout dans le datastore.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from .. import access
from ..datastore.core import NamespaceNotFound, NamespaceReadOnly, make_store
from ._authz import SUB_ONLY
from ._types import AuthzDenied, Capability, ResolvedCtx, RestBinding
from .registry import CAPABILITIES


class DropColumnInput(BaseModel):
    namespace: str
    key: str
    # Défaut False, jamais True : la confirmation est le garde-fou du geste, elle
    # doit être posée par l'appelant à chaque appel.
    confirm: bool = False


class DropColumnResult(BaseModel):
    namespace: str
    key: str
    # Lignes qui PORTAIENT la colonne (0 = elle n'existait dans aucune).
    rows: int


def _drop_column(ctx: ResolvedCtx, inp: DropColumnInput) -> dict:
    # `slot:<nom>` = le tableau bindé par le projet actif. À résoudre ICI : la
    # référence de slot est le nom qu'un agent manipule couramment, et sans ça la
    # capacité la traitait comme un nom littéral → `namespace_not_found` (seize fois
    # de suite sur une purge réelle). Le tool `@mcp.tool()` que cette capacité
    # remplace le faisait ; la conversion l'avait perdu.
    namespace = access.resolve_namespace_ref(inp.namespace)
    try:
        return make_store(ctx.sub).drop_column(
            namespace, inp.key, confirm=bool(inp.confirm))
    except NamespaceNotFound:
        raise AuthzDenied(404, "namespace_not_found")
    except NamespaceReadOnly:
        raise AuthzDenied(403, "namespace_read_only")
    except ValueError as e:
        raise AuthzDenied(400, "invalid_drop_column", str(e))


class PatchSchemaInput(BaseModel):
    namespace: str
    # Fusion PAR CLÉ : chaque entrée complète le field de même `key` (les propriétés
    # fournies écrasent, les autres sont préservées) ou l'ajoute s'il est inconnu.
    fields: Optional[list] = None
    # Le pendant obligé de la fusion : sans retrait explicite, plus de nettoyage.
    remove: Optional[list] = None
    strict: Optional[bool] = None
    key: Optional[str] = None


class PatchSchemaResult(BaseModel):
    # Même contorsion que `SchemaOut` (capabilities/datastore_schema.py) : le champ
    # s'appelle `schema` sur le fil, mais ce nom masque une méthode héritée de
    # `BaseModel` — d'où le nom python décalé + alias, le schéma OpenAPI étant généré
    # `by_alias`.
    model_config = ConfigDict(populate_by_name=True)

    namespace: str
    # Le schéma RÉSULTANT, tel qu'il est désormais en base.
    declared_schema: Optional[dict] = Field(default=None, alias="schema",
                                            serialization_alias="schema")
    added: list = []
    updated: list = []
    removed: list = []
    # #389 : hérité de `set_schema`, par lequel le patch repasse.
    enforced: list = []
    # #388 : ce que cette pose vient de RETIRER, avec les valeurs perdues — la
    # réponse en est la seule copie. Clé distincte de `warning` : les autres décrivent
    # une configuration douteuse et réparable, celle-ci nomme ce qui n'est plus.
    declarations_effacees: list = []
    declarations_effacees_hint: Optional[str] = None
    # Avertissements héréités de la pose du schéma (file de travail sans état
    # terminal, bornes posées sur des données hors borne, colonnes orphelines).
    warning: Optional[str] = None


def _patch_schema(ctx: ResolvedCtx, inp: PatchSchemaInput) -> dict:
    namespace = access.resolve_namespace_ref(inp.namespace)
    try:
        return make_store(ctx.sub).patch_schema(
            namespace, fields=inp.fields, remove=inp.remove,
            strict=inp.strict, key=inp.key)
    except NamespaceNotFound:
        raise AuthzDenied(404, "namespace_not_found")
    except NamespaceReadOnly:
        raise AuthzDenied(403, "namespace_read_only")
    except ValueError as e:
        raise AuthzDenied(400, "invalid_patch_schema", str(e))


CAPABILITIES += [
    Capability(
        key="me.datastore.drop_column",
        handler=_drop_column,
        Input=DropColumnInput,
        Output=DropColumnResult,
        authz=SUB_ONLY,
        mcp="data_drop_column",
        rest=None,  # cf. en-tête : une ligne à poser quand le cockpit l'affichera
        description=(
            "DESTRUCTIVE — erase a column from EVERY row of a namespace (`confirm=True` "
            "required). Removing a field from the schema takes it out of the view, but "
            "the key stays in each row: it still shows up on read, and keeps attracting "
            "writes. Writing `null` does not erase it either. Use it after RENAMING "
            "fields — the old names often describe the content better than the new ones, "
            "so an agent re-reading a row writes into them believing it aims right; purge "
            "them once instead of warning every agent forever. A key still DECLARED in "
            "the schema is refused: take it out of the schema first (`data_set_schema`). "
            "Returns `{rows}` = how many rows carried it."),
    ),
    Capability(
        key="me.datastore.patch_schema",
        handler=_patch_schema,
        Input=PatchSchemaInput,
        Output=PatchSchemaResult,
        authz=SUB_ONLY,
        mcp="data_patch_schema",
        # Le cockpit affiche « change le type de cette colonne » (text→select) —
        # c'est exactement l'appel anticipé par le commentaire d'en-tête. Même
        # chemin que get/set_schema (`{namespace}/schema`), distingué par le
        # verbe : PATCH = fusion par clé, PUT = remplacement entier.
        rest=RestBinding(verb="PATCH", path="/api/datastore/namespaces/{namespace}/schema"),
        description=(
            "Change a namespace's schema BY KEY, without rewriting the whole field "
            "list. Prefer this over `data_set_schema` for any EDIT: `set` REPLACES, so "
            "rebuilding the list from what you know silently drops the per-field "
            "settings you did not restate (labels, help, max_length, pattern, width, "
            "options) — same call, same success, no way to tell. `fields` merges by "
            "`key`: listed properties overwrite, unlisted ones are PRESERVED, unknown "
            "keys are appended. `remove: [\"key\", …]` is the explicit deletion (a "
            "wrong key is refused, never silently ignored) — it takes the field out of "
            "the SCHEMA; to erase the column from the rows' DATA, that is "
            "`data_drop_column`. `strict`/`key` change the head keys, untouched when "
            "omitted. Field ORDER is never reshuffled. Returns the resulting schema "
            "plus `{added, updated, removed}` and any `warning` the schema raises."),
    ),
]
