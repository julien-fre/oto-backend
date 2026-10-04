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
appartient à la plateforme, et le jour où le cockpit afficherait « supprimer cette
colonne » — l'endroit naturel, puisque la colonne morte trompe aussi l'humain qui
relit une fiche — la face REST serait une ligne `rest=` ici, pas une seconde
implémentation avec sa propre autz à tenir en phase. Ce jour est arrivé : le
cockpit pose la corbeille dans le menu ⋯ d'une colonne, la ligne `rest=` est posée
ci-dessous, et l'autz reste la seule, celle de la capacité.

`POST …/{datastore}/drop_column`, et non `DELETE …/columns/{key}`, pour deux
raisons de forme qui tiennent aux données : une clé de colonne peut porter un point
(`site_web.comment` — le store tient exprès à la garder atteignable, cf. son
commentaire sur le diagnostic d'après-purge), donc elle n'a rien à faire dans un
segment de chemin ; et `confirm` est un booléen, que seul un corps porte sans
coercition depuis une chaîne de query — or l'adaptateur ne lit un corps que sur
POST/PUT/PATCH ou `reads_body` (cf. `_rest_adapter`). Le corps porte donc
`{key, confirm}`, le chemin le seul `datastore`. Même parti que `claim_next`, déjà
un verbe en POST sous le datastore.

Les gardes vivent dans le STORE (`DatastorePg.drop_column`), pas ici : `confirm`,
le refus d'une clé encore déclarée au schéma et celui des colonnes de plateforme
valent pour toute surface, présente ou future. Autz `SUB_ONLY` au seuil ; le vrai
gate est le droit d'ÉCRITURE sur le tableau, résolu par le store (org active +
ownership) — un tableau hors périmètre répond 404, comme partout dans le datastore.
"""
from __future__ import annotations

from ...datastore.identite import Adresse
from ...datastore import reglages
from ...datastore import upsert_implicite as upi
from ...datastore.schema_keys import META_MAX_OCTETS as _META_MAX

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from ... import access
from ...datastore.core import DatastoreNotFound, DatastoreReadOnly, make_store
from ...datastore.errors import ColumnAbsent
from .._authz import SUB_ONLY
from .._types import AuthzDenied, Capability, ResolvedCtx, RestBinding
from .common import EntreeDatastore
from ..registry import CAPABILITIES


class DropColumnInput(EntreeDatastore):
    datastore: Adresse
    key: str = Field(description=(
        "The column to erase. A key still DECLARED in the schema is refused — take it "
        "out with `data_set_schema` first. An ANNOTATION (`site_web.comment`) is not a "
        "column: erase it by writing `{\"site_web\": {\"comment\": null}}`."))
    # Défaut False, jamais True : la confirmation est le garde-fou du geste, elle
    # doit être posée par l'appelant à chaque appel.
    confirm: bool = Field(default=False, description=(
        "REQUIRED `true` — the purge is refused without it. It erases the key from "
        "EVERY row, not just from the schema view."))


class DropColumnResult(BaseModel):
    datastore: Adresse
    key: str
    # Lignes qui PORTAIENT la colonne — TOUJOURS >= 1 (#680). Le zéro n'est plus une
    # réponse : une purge qui ne touche rien est un refus, parce que le même `0`
    # valait « la colonne était vide » et « ce nom n'est pas une colonne ».
    rows: int


def _drop_column(ctx: ResolvedCtx, inp: DropColumnInput) -> dict:
    # `slot:<nom>` = le tableau bindé par le projet actif. À résoudre ICI : la
    # référence de slot est le nom qu'un agent manipule couramment, et sans ça la
    # capacité la traitait comme un nom littéral → `datastore_not_found` (seize fois
    # de suite sur une purge réelle). Le tool `@mcp.tool()` que cette capacité
    # remplace le faisait ; la conversion l'avait perdu.
    datastore = access.resolve_datastore_ref(inp.datastore)
    try:
        return make_store(ctx.sub).drop_column(
            datastore, inp.key, confirm=bool(inp.confirm))
    except DatastoreNotFound:
        raise AuthzDenied(404, "datastore_not_found")
    except DatastoreReadOnly:
        raise AuthzDenied(403, "datastore_read_only")
    except ColumnAbsent as e:
        # Code à PART (cf. `ColumnAbsent`) : « rien à purger » est un refus qu'un
        # appelant en deux temps — le cockpit, qui retire d'abord le champ du schéma —
        # doit pouvoir reconnaître comme abouti sans lire la phrase.
        raise AuthzDenied(400, "drop_column_no_rows", str(e))
    except ValueError as e:
        raise AuthzDenied(400, "invalid_drop_column", str(e))


class PatchSchemaInput(EntreeDatastore):
    datastore: Adresse
    # Fusion PAR CLÉ : chaque entrée complète le field de même `key` (les propriétés
    # fournies écrasent, les autres sont préservées) ou l'ajoute s'il est inconnu.
    # Ce que le préambule autorise se répète ICI depuis le 2026-09-01 (#627) : jusque
    # là `apply_flat_signature` ne recopiait qu'annotation et défaut, et une
    # `description` posée sur un champ d'`Input` était acceptée-INERTE.
    fields: Optional[list] = Field(default=None, description=(
        "Merges BY `key`: the properties you list overwrite, the ones you omit are "
        "PRESERVED, an unknown key is appended. Send only what changes."))
    # Le pendant obligé de la fusion : sans retrait explicite, plus de nettoyage.
    remove: Optional[list] = Field(default=None, description=(
        "Keys to take out of the SCHEMA (a key that is not there is refused, never "
        "silently ignored). It does not touch the rows' DATA — that is "
        "`data_drop_column`."))
    # Le troisième geste, arrivé le 07/09/2026 : entre la fusion qui COMPLÈTE et le
    # retrait qui enlève une COLONNE, rien ne savait enlever un ATTRIBUT. Il fallait
    # reposer le schéma entier — le geste qui a détruit 78 notes de champ, puis 52.
    remove_attrs: Optional[dict] = Field(default=None, description=(
        "Attributes to take off columns that STAY: `{\"column\": [\"attr\", …]}`. "
        "Merging only completes, and `remove` drops a whole column — without this, "
        "taking one attribute off meant reposting the entire schema, which silently "
        "drops every declaration you did not resend. An unknown column or attribute "
        "is refused, never silently ignored. `key` cannot be taken off: it is the "
        "column's identity, not one of its properties."))
    key: Optional[str] = None
    # oto#127 : les DEUX réglages de tête, un par axe (`datastore/reglages.py`). Ils
    # se posent ICI — un tableau se ferme quand son schéma est déjà long, et le poser
    # par `set` obligerait à réécrire quatre-vingts champs pour une clé de tête.
    # `str` et pas `Literal` : une valeur hors des crans est refusée par la pose, qui
    # les nomme, au lieu d'un `invalid_input` muet.
    unknown_columns: Optional[str] = Field(default=None, description=(
        "What happens to a column the schema does NOT declare: `\"create\"` (the "
        "default) creates it silently; `\"report\"` creates it and names it back in "
        "`hors_schema` — and makes the declared format a CONTRACT: top-level "
        "`options` are enforced and declared sub-records refuse an undeclared "
        "attribute; `\"reject\"` does the same and refuses the write, storing "
        "nothing (needs at least one declared field). Omitted leaves it untouched."))
    new_rows: Optional[str] = Field(default=None, description=(
        "Whether a NEW row may be born: `\"create\"` (the default) or `\"reject\"` "
        "— CLOSES the table: a write designating no existing row (no `id`, no key "
        "value the table already carries) is refused; needs `key`. Omitted leaves it "
        "untouched."))

    @classmethod
    def refus_champs_retires(cls, data: dict) -> Optional[str]:
        """Les trois anciens paramètres (oto#127), refusés avec l'équivalent exact —
        lu par l'adaptateur REST ; la face MCP sert le même texte depuis
        `error_taxonomy`."""
        return reglages.refus_parametres(data)


class PatchSchemaResult(BaseModel):
    # Même contorsion que `SchemaOut` (capabilities/datastore/schema.py) : le champ
    # s'appelle `schema` sur le fil, mais ce nom masque une méthode héritée de
    # `BaseModel` — d'où le nom python décalé + alias, le schéma OpenAPI étant généré
    # `by_alias`.
    model_config = ConfigDict(populate_by_name=True)

    datastore: Adresse
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
    # oto-backend#479 : ce que les lignes EN PLACE violent déjà du schéma posé, par
    # chemin (`contacts[].email`) — `{rows, blocking_rows?, sample_ids, consequence}`.
    # Absent = rien à juger (aucun champ déclaré, tableau vide) ; `{}` avec
    # `complete: true` = examiné, rien de fautif.
    existing_violations: Optional[dict] = None
    # Ce qui a été examiné : `{rows_examined, rows_total, complete}`. `complete: false`
    # = plafond de lignes atteint, les comptes sont des PLANCHERS.
    existing_violations_scope: Optional[dict] = None
    # Avertissements hérités de la pose du schéma (file de travail sans état
    # terminal, bornes posées sur des données hors borne, colonnes orphelines, clés
    # inconnues encore stockées).
    warning: Optional[str] = None


def _patch_schema(ctx: ResolvedCtx, inp: PatchSchemaInput) -> dict:
    datastore = access.resolve_datastore_ref(inp.datastore)
    try:
        # Une clé inconnue posée ou modifiée par le patch est REFUSÉE par le store :
        # le schéma fusionné repasse par `set_schema`, donc par `validate_schema_def`.
        return make_store(ctx.sub).patch_schema(
            datastore, fields=inp.fields, remove=inp.remove,
            remove_attrs=inp.remove_attrs,
            key=inp.key, unknown_columns=inp.unknown_columns, new_rows=inp.new_rows)
    except DatastoreNotFound:
        raise AuthzDenied(404, "datastore_not_found")
    except DatastoreReadOnly:
        raise AuthzDenied(403, "datastore_read_only")
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
        # Le geste destructif du cockpit — cf. en-tête pour le choix POST + corps.
        rest=RestBinding(
            verb="POST", path="/api/datastores/{datastore}/drop_column"),
        description=(
            "DESTRUCTIVE — erase a column from EVERY row of a datastore (`confirm=True` "
            "required). Removing a field from the schema takes it out of the view, but "
            "the key stays in each row: it still shows up on read, and keeps attracting "
            "writes. Writing `null` does not erase it either. Use it after RENAMING "
            "fields — the old names often describe the content better than the new ones, "
            "so an agent re-reading a row writes into them believing it aims right; purge "
            "them once instead of warning every agent forever. A key still DECLARED in "
            "the schema is refused: take it out of the schema first (`data_set_schema`). "
            "Returns `{rows}` = how many rows carried it, ALWAYS >= 1: a name that no "
            "row carries is REFUSED, never reported as a zero — so a success is always "
            "a removal you can tick off. The refusal says which of the two it is: a "
            "typo (no column of that name), or an ANNOTATION such as `site_web.comment` "
            "— served flat next to its column but stored under it, so a column purge "
            "does not reach it (write `{\"site_web\": {\"comment\": null}}` instead)."),
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
        # chemin que get/set_schema (`{datastore}/schema`), distingué par le
        # verbe : PATCH = fusion par clé, PUT = remplacement entier.
        rest=RestBinding(verb="PATCH", path="/api/datastores/{datastore}/schema"),
        description=(
            "Change a datastore's schema BY KEY, without rewriting the whole field "
            "list. Prefer this over `data_set_schema` for any EDIT: `set` REPLACES, so "
            "rebuilding the list from what you know silently drops the per-field "
            "settings you did not restate (labels, help, max_length, pattern, width, "
            "options) — same call, same success, no way to tell. `fields` merges by "
            "`key`: listed properties overwrite, unlisted ones are PRESERVED, unknown "
            "keys are appended. `remove: [\"key\", …]` is the explicit deletion (a "
            "wrong key is refused, never silently ignored) — it takes the field out of "
            "the SCHEMA; to erase the column from the rows' DATA, that is "
            "`data_drop_column`. `remove_attrs: {\"column\": [\"attr\", …]}` takes "
            "attributes off columns that STAY — merging only completes, so this is the "
            "only way to drop one declaration without reposting the whole schema; an "
            "unknown column or attribute is refused, and `key` cannot be dropped. "
            "`key`/`unknown_columns`/`new_rows` "
            "change the head keys, untouched when omitted — `key` names the BUSINESS "
            "KEY: " + upi.description_cle_schema() + " `new_rows: \"reject\"` "
            "CLOSES the table (a write designating no existing row is refused), "
            "`\"create\"` reopens it. `unknown_columns` decides what happens to a "
            "column the schema does NOT declare: `\"create\"` (the default) creates "
            "it silently, `\"report\"` creates it and names it back in `hors_schema` "
            "— and makes the declared format a contract (top-level `options` "
            "enforced, declared sub-records closed) — while `\"reject\"` refuses the "
            "write and stores nothing; set it on a table that has FINISHED being "
            "explored. `strict`, `unknown_fields` and `key_required` were REPLACED by "
            "these two on 2026-10-02: they are refused with their exact equivalent. "
            "Per field, `readonly: true` locks the value "
            "in place (layers such as `.comment` stay open) — the table's OWNER, or "
            "whoever GOVERNS it, can still replace such a value with "
            "`data_write(readonly_override=true)`, for that one call and journaled, "
            "so locking a column never means nobody can correct it again. "
            "On the field that carries `lifecycle`, the block merges KEY BY KEY and its "
            "`transitions` merge STATE BY STATE: naming one state leaves the others "
            "alone, so declaring a way OUT of a terminal state is one line and costs "
            "nothing else. Taking one out is explicit — `transitions: {\"lost\": null}` "
            "drops that state's exits, `lifecycle: {transitions: null}` drops the whole "
            "table. `labels` (the displayed name of each state) merges STATE BY STATE "
            "too: `lifecycle: {labels: {\"lost\": \"Lost\"}}` names that step and "
            "keeps the others' labels, `{\"lost\": null}` drops that one label. "
            "A key that its level does not admit (head, column, sub-field, `of`, "
            "`lifecycle`) is REFUSED when the patch posts or changes it — the refusal "
            "names the path, the key and the closest known one; an unknown key "
            "ALREADY stored and left untouched does not block a patch of another "
            "column, and `remove_attrs` takes it off. Your own annotations go in "
            "`meta` (an object, at every level, carried as is, never read, at most "
            + str(_META_MAX) + " bytes as JSON; a patch replaces it whole); help "
            "text goes in `description` (`note`, `help`, `hint`, `placeholder` are "
            "refused). Field ORDER "
            "is never reshuffled. Returns the resulting schema "
            "plus `{added, updated, removed}` and any `warning` the schema raises, "
            "and `existing_violations`: per path (`contacts[].email`), how many rows "
            "ALREADY IN PLACE break the resulting schema, a sample of their ids and the "
            "consequence (`blocking_rows` accept no write at all until fixed) — clean "
            "them before arming the guard. `existing_violations_scope.complete: false` "
            "means the row cap was hit: the counts are floors."),
    ),
]
