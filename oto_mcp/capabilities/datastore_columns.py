"""Capacité « purger une colonne » d'un tableau (oto-backend#296).

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

from pydantic import BaseModel

from ..datastore import NamespaceNotFound, NamespaceReadOnly, make_store
from ._authz import SUB_ONLY
from ._types import AuthzDenied, Capability, ResolvedCtx
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
    try:
        return make_store(ctx.sub).drop_column(
            inp.namespace, inp.key, confirm=bool(inp.confirm))
    except NamespaceNotFound:
        raise AuthzDenied(404, "namespace_not_found")
    except NamespaceReadOnly:
        raise AuthzDenied(403, "namespace_read_only")
    except ValueError as e:
        raise AuthzDenied(400, "invalid_drop_column", str(e))


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
]
