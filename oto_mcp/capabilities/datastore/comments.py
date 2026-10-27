"""Capacité « commentaires d'une ligne » : ce que les personnes disent d'une ligne, à
côté de ses valeurs (`docs/datastore.md` §Commentaires).

`GET|POST /api/datastores/{datastore}/rows/{row_id}/comments`, `DELETE
…/comments/{comment_id}` ; sur la face agent `data_row_comments` (lire) et
`data_row_comment` (écrire). La source est `datastore_row_comments`
(`db/datastore_commentaires.py`) — PAS une colonne de la ligne : commenter ne fait
avancer ni sa `rev` ni son journal.

**Accès.** Lire ET écrire un commentaire, c'est l'accès de LECTURE de la ligne : le
tableau se résout par le store (org active, ownership, périmètre d'un endpoint partagé),
comme `GET …/rows/{row_id}`. Un lecteur à qui l'on a partagé un tableau peut donc en
commenter les lignes — c'est l'usage visé : une personne qui ne change pas la donnée dit
ce qu'elle en pense. Un tableau hors périmètre est un 404.

**Une ligne supprimée garde ses commentaires**, lisibles par qui GOUVERNE le tableau
seulement (même règle et même raison que l'historique, `history.py`) ; personne n'y
écrit plus : `404 row_not_found`.

**Retirer** un commentaire : son auteur, ou qui gouverne le tableau. Le retrait est
doux (`deleted_at`) et le commentaire ne se sert plus.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field, field_validator

from ... import db, ownership
from ...datastore import identite
from ...datastore.core import DatastoreNotFound, make_store
from ...datastore.identite import Adresse
from .._authz import SUB_ONLY
from .._types import AuthzDenied, Capability, DeclaredError, ResolvedCtx, RestBinding
from ..registry import CAPABILITIES
from ._refus import _JETON_MAL_PLACE
from .common import EntreeDatastore, HORODATAGE, ns_not_found
from .rows import _adresse

LIMITE_DEFAUT = 50
LIMITE_MAX = 200
LONGUEUR_MAX = 5000


class _Ligne(EntreeDatastore):
    datastore: Adresse
    row_id: str


class RowCommentsInput(_Ligne):
    limit: int = Field(default=LIMITE_DEFAUT, description=(
        f"Comments per page, the most recent page first ({LIMITE_MAX} at most)."))
    before_id: Optional[int] = Field(default=None, description=(
        "Read the next (older) page: the previous answer's `next_before_id`."))


class RowCommentInput(_Ligne):
    body: str = Field(description=f"The comment, plain text, 1 to {LONGUEUR_MAX} characters.")

    @field_validator("body", mode="after")
    @classmethod
    def _corps(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("`body` is empty")
        if len(v) > LONGUEUR_MAX:
            raise ValueError(f"`body` is longer than {LONGUEUR_MAX} characters")
        return v


class DeleteRowCommentInput(_Ligne):
    comment_id: int


class Comment(BaseModel):
    id: int
    author: Optional[str] = Field(default=None, description="The author's sub.")
    author_email: Optional[str] = Field(default=None, description=(
        "Resolved at read time; absent for an unknown account."))
    body: str
    at: Optional[str] = Field(default=None, description=HORODATAGE)
    mine: bool = Field(description="Written by the caller.")
    can_delete: bool = Field(description="The caller may remove it (author or governor).")


class RowComments(BaseModel):
    datastore: Optional[str] = None
    ns_id: Optional[int] = Field(default=None, description=identite.DESCRIPTION)
    row_id: str
    comments: list[Comment] = Field(description="Oldest first within the page.")
    next_before_id: Optional[int] = Field(default=None, description=(
        "Pass as `before_id` to read the older page. `null` = nothing older."))


MAX_IDS = 100


class CommentCountsInput(EntreeDatastore):
    datastore: Adresse
    # Même forme que `fields`/`versions` des lignes (#367) : une URL ne transporte que
    # des chaînes — `?row_ids=a,b` et/ou `?row_ids=a&row_ids=b`, les deux se combinent.
    row_ids: list[str] | str = Field(description=(
        f"The rows to count, at most {MAX_IDS}: comma-separated, or the parameter "
        "repeated."))

    @field_validator("row_ids", mode="after")
    @classmethod
    def _ids_en_liste(cls, v):
        brut = v if isinstance(v, list) else str(v).split(",")
        ids = list(dict.fromkeys(m for m in (str(x).strip() for x in brut) if m))
        if not ids:
            raise ValueError("`row_ids` is empty: name the rows to count")
        if len(ids) > MAX_IDS:
            raise ValueError(f"`row_ids` names {len(ids)} rows, at most {MAX_IDS}")
        return ids


class LatestComment(BaseModel):
    id: int
    body: str = Field(description=(
        f"The first {db.datastore_commentaires.APERCU} characters; the whole text is "
        "served by the row's comment list."))
    body_truncated: bool
    author: Optional[str] = None
    author_email: Optional[str] = None
    at: Optional[str] = Field(default=None, description=HORODATAGE)


class CommentCounts(BaseModel):
    datastore: Optional[str] = None
    ns_id: Optional[int] = Field(default=None, description=identite.DESCRIPTION)
    counts: dict[str, int] = Field(description=(
        "`{row_id: n}` live comments per requested row that still exists; rows "
        "without any are omitted."))
    total: int
    latest: dict[str, LatestComment] = Field(description=(
        "`{row_id: comment}` each counted row's newest live comment."))


class Deleted(BaseModel):
    deleted: bool


def _ligne(ctx: ResolvedCtx, inp: _Ligne, *, ecrire: bool = False):
    """Le tableau et la ligne, sous l'accès de LECTURE de l'appelant. Rend `(store, ns,
    ns_id, row_id, gouverne, absente)`."""
    ns, row_id = _adresse(inp.datastore, inp.row_id)
    store = make_store(ctx.sub)
    try:
        ns_id = store.resolve_ns_id(ns)
    except DatastoreNotFound:
        raise ns_not_found(ctx.sub, ns)
    gouverne = ownership.can_govern(ctx.sub, ownership.TYPE_RESSOURCE_DATASTORE, str(ns_id))
    absente = db.datastore_get_row(ns_id, row_id) is None
    if absente and (ecrire or not gouverne):
        raise AuthzDenied(404, "row_not_found")
    return store, ns, ns_id, row_id, gouverne, absente


def _servi(c: dict, sub: Optional[str], gouverne: bool, emails: dict) -> dict:
    auteur = c["auteur_sub"]
    a_moi = auteur is not None and auteur == sub
    return {
        "id": c["id"],
        "author": auteur,
        "author_email": emails.get(auteur),
        "body": c["body"],
        "at": c["created_at"],
        "mine": a_moi,
        "can_delete": a_moi or gouverne,
    }


def _row_comments(ctx: ResolvedCtx, inp: RowCommentsInput) -> dict:
    store, ns, ns_id, row_id, gouverne, absente = _ligne(ctx, inp)
    limit = max(1, min(LIMITE_MAX, inp.limit))
    lus = db.datastore_commentaires.lister(ns_id, row_id, avant_id=inp.before_id,
                                           limit=limit)
    # Comme l'historique : une ligne absente sans rien à montrer est une ligne
    # inconnue, pas une ligne sans commentaire.
    if absente and not lus and inp.before_id is None:
        raise AuthzDenied(404, "row_not_found")
    suite = lus[limit - 1]["id"] if len(lus) > limit else None
    page = list(reversed(lus[:limit]))
    emails = db.emails_by_subs([c["auteur_sub"] for c in page])
    return {
        **identite.de_releve(store.dernier_tableau, ns),
        "row_id": row_id,
        "comments": [_servi(c, ctx.sub, gouverne, emails) for c in page],
        "next_before_id": suite,
    }


def _comment_counts(ctx: ResolvedCtx, inp: CommentCountsInput) -> dict:
    ns, _ = _adresse(inp.datastore)
    store = make_store(ctx.sub)
    try:
        ns_id = store.resolve_ns_id(ns)
    except DatastoreNotFound:
        raise ns_not_found(ctx.sub, ns)
    lignes = db.datastore_commentaires.comptes(ns_id, inp.row_ids)
    emails = db.emails_by_subs([l["auteur_sub"] for l in lignes])
    return {
        **identite.de_releve(store.dernier_tableau, ns),
        "counts": {l["row_id"]: int(l["n"]) for l in lignes},
        "total": sum(int(l["n"]) for l in lignes),
        "latest": {l["row_id"]: {
            "id": l["id"], "body": l["apercu"], "body_truncated": l["tronque"],
            "author": l["auteur_sub"],
            "author_email": emails.get(l["auteur_sub"]), "at": l["created_at"],
        } for l in lignes},
    }


def _row_comment(ctx: ResolvedCtx, inp: RowCommentInput) -> dict:
    _, _, ns_id, row_id, gouverne, _ = _ligne(ctx, inp, ecrire=True)
    c = db.datastore_commentaires.ajouter(ns_id, row_id, auteur_sub=ctx.sub, body=inp.body)
    return _servi(c, ctx.sub, gouverne, db.emails_by_subs([ctx.sub]))


def _delete_row_comment(ctx: ResolvedCtx, inp: DeleteRowCommentInput) -> dict:
    _, _, ns_id, row_id, gouverne, _ = _ligne(ctx, inp)
    c = db.datastore_commentaires.lire(ns_id, row_id, inp.comment_id)
    if c is None:
        raise AuthzDenied(404, "comment_not_found")
    if not gouverne and (ctx.sub is None or c["auteur_sub"] != ctx.sub):
        raise AuthzDenied(403, "comment_not_yours")
    db.datastore_commentaires.retirer(c["id"])
    return {"deleted": True}


_CHEMIN = "/api/datastores/{datastore}/rows/{row_id}/comments"
_TABLEAU_INCONNU = DeclaredError(404, "datastore_not_found",
                                 "le tableau ne se voit pas depuis l'org de l'appel")

CAPABILITIES += [
    Capability(
        key="me.datastore.row_comments",
        handler=_row_comments,
        Input=RowCommentsInput,
        Output=RowComments,
        authz=SUB_ONLY,
        mcp="data_row_comments",
        rest=RestBinding(verb="GET", path=_CHEMIN, provisoire=True),
        errors=(
            _TABLEAU_INCONNU,
            DeclaredError(404, "row_not_found",
                          "aucune ligne de cet `_id` dans ce tableau ; les commentaires "
                          "d'une ligne SUPPRIMÉE ne se lisent que par qui gouverne le "
                          "tableau"),
            _JETON_MAL_PLACE,
        ),
        description=(
            "Read the comments people left on ONE datastore row, oldest first within a "
            "page; `before_id` (= the previous `next_before_id`) reads older ones. "
            "Comments are not row values: they never change the row or its history."
        ),
    ),
    # REST seule : le front la lit une fois par chargement de tableau (icônes, colonne
    # « Commentaires ») ; un agent lit le fil d'une ligne, il n'a pas besoin du compte.
    Capability(
        key="me.datastore.comment_counts",
        handler=_comment_counts,
        Input=CommentCountsInput,
        Output=CommentCounts,
        authz=SUB_ONLY,
        rest=RestBinding(verb="GET", path="/api/datastores/{datastore}/comments/counts",
                         provisoire=True),
        errors=(_TABLEAU_INCONNU, _JETON_MAL_PLACE),
        description=(
            "Count the live comments of the named rows of a datastore (`row_ids`, at "
            f"most {MAX_IDS}), with each row's newest as a preview; rows that no longer "
            "exist or have no comment are omitted."
        ),
    ),
    Capability(
        key="me.datastore.row_comment",
        handler=_row_comment,
        Input=RowCommentInput,
        Output=Comment,
        authz=SUB_ONLY,
        mcp="data_row_comment",
        rest=RestBinding(verb="POST", path=_CHEMIN, status=201, provisoire=True),
        errors=(
            _TABLEAU_INCONNU,
            DeclaredError(404, "row_not_found",
                          "aucune ligne de cet `_id` dans ce tableau (une ligne "
                          "supprimée ne se commente plus)"),
            _JETON_MAL_PLACE,
        ),
        description=(
            "Add a comment to ONE datastore row, as the caller. Anyone who can read the "
            "row can comment. It does not change the row: to change a value, write it."
        ),
    ),
    Capability(
        key="me.datastore.delete_row_comment",
        handler=_delete_row_comment,
        Input=DeleteRowCommentInput,
        Output=Deleted,
        authz=SUB_ONLY,
        rest=RestBinding(verb="DELETE", path=_CHEMIN + "/{comment_id}", provisoire=True),
        errors=(
            _TABLEAU_INCONNU,
            DeclaredError(404, "row_not_found", "aucune ligne de cet `_id` dans ce tableau"),
            DeclaredError(404, "comment_not_found",
                          "aucun commentaire vivant de cet `id` sur cette ligne"),
            DeclaredError(403, "comment_not_yours",
                          "seuls son auteur et qui gouverne le tableau retirent un "
                          "commentaire"),
            _JETON_MAL_PLACE,
        ),
        description="Remove one comment from a datastore row (its author or the table's governor).",
    ),
]
