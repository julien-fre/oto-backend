"""Capacité « historique d'une ligne » : ce que chaque révision a changé, valeurs
comprises (oto#273, jalon M3).

`GET /api/datastores/{datastore}/rows/{row_id}/history`, et sur la face agent
`data_row_history`. La source est le journal des révisions (`db/historique.py`), écrit
par PostgreSQL sur toutes les faces d'écriture et estampillé par le serveur (acteur,
run, source, geste).

**Accès.** Celui de la lecture de la ligne : le tableau se résout par le store (org
active, ownership, périmètre d'un endpoint partagé), comme `GET …/rows/{row_id}`. Un
tableau hors périmètre est un 404.

**Une ligne supprimée garde son historique**, et il ne se lit que par qui GOUVERNE le
tableau (`ownership.can_govern` : propriétaire ou escalade, ADR 0030). La suppression
n'étant pas une révision, rien ne distingue dans le journal une ligne supprimée d'un
identifiant jamais vu. Un simple lecteur reçoit donc `404 row_not_found` sur une ligne
absente, qu'elle ait existé ou non : lui ouvrir l'historique d'une ligne disparue
servirait les valeurs d'une donnée que le propriétaire a retirée, et lui dire qu'elle a
existé divulguerait un fait qu'il ne peut plus lire ailleurs. Le gouverneur lit
l'historique, avec `row_deleted: true` ; sans aucune révision, c'est aussi un 404.

**Face agent** : les colonnes masquées aux agents (`agent_access: "none"`) sont retirées
des diffs, et une révision qui ne touchait qu'elles disparaît de la page.

**Couverture** : le journal ne couvre que les écritures faites depuis sa mise en service
(`historique.MISE_EN_SERVICE`). La réponse le dit dans `coverage`, et le texte servi
aussi.
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator

from ... import db, ownership
from ...datastore import acces_agent as aga
from ...datastore import identite
from ...datastore.core import DatastoreNotFound, make_store
from ...datastore.identite import Adresse
from ...db import historique
from .._authz import SUB_ONLY
from .._types import AuthzDenied, Capability, DeclaredError, ResolvedCtx, RestBinding
from ..registry import CAPABILITIES
from ._refus import _JETON_MAL_PLACE
from .common import EntreeDatastore, HORODATAGE, ns_not_found
# `rows` est déjà chargé quand ce module l'est (`capabilities/__init__.py`) : importer
# sa couture d'adresse ne change pas l'ordre d'enregistrement, qui est figé.
from .rows import _adresse

LIMITE_DEFAUT = 50
LIMITE_MAX = 200


class RowHistoryInput(EntreeDatastore):
    datastore: Adresse
    row_id: str
    champ: Optional[str] = Field(default=None, description=(
        "Ne garder que les révisions qui touchent cette colonne, et ne rendre que sa "
        "partie du diff."))
    limit: int = Field(default=LIMITE_DEFAUT, description=(
        f"Révisions par page, la plus récente d'abord ({LIMITE_MAX} au plus)."))
    before_id: Optional[int] = Field(default=None, description=(
        "Lire la page suivante : l'`next_before_id` de la réponse précédente. Omis = "
        "à partir de la révision la plus récente."))

    @field_validator("champ", mode="after")
    @classmethod
    def _champ_non_vide(cls, v):
        # `?champ=` vide n'est pas « tous les champs » dit autrement : c'est un appel
        # mal formé, et le traiter comme absent lui servirait une réponse qu'il n'a pas
        # demandée.
        if v is not None and not v.strip():
            raise ValueError("`champ` vide : nommer une colonne, ou omettre le paramètre")
        return v


class Revision(BaseModel):
    id: int = Field(description="Identifiant de la révision dans le journal, monotone.")
    rev: int = Field(description=(
        "La révision de la ligne après cette écriture. 0 = insertion ; elle revient "
        "si la ligne a été supprimée puis recréée sous le même `row_id`."))
    at: Optional[str] = Field(default=None, description=HORODATAGE)
    acteur: Optional[str] = Field(default=None, description=(
        "Qui a écrit : le sub du porteur réel, `service:<nom>` pour un travail de fond, "
        "`null` quand le serveur ne le sait pas."))
    run_id: Optional[str] = Field(default=None, description="Le run qui portait l'écriture.")
    source: Optional[str] = Field(default=None, description=(
        "`import`, `agent`, `console`, `api`, `upload` ou `system` ; `null` = écrit hors "
        "du serveur (SQL à la main, migration)."))
    geste_id: Optional[str] = Field(default=None, description=(
        "Le geste : le `call_uid` de l'appel d'outil ou de la requête REST, le `jti` "
        "d'un upload signé. Toutes les lignes d'un même lot le partagent."))
    diff: dict[str, Any] = Field(description=(
        "`{colonne: {avant, apres}}`, valeurs entières (couches comprises). Un côté "
        "absent n'a pas sa clé : `{apres}` seul = ajoutée, `{avant}` seul = retirée."))


class Coverage(BaseModel):
    journal_since: str = Field(description=(
        "Le jour de mise en service du journal. Rien de ce qui a été écrit avant n'y "
        "figure."))
    insert_recorded: bool = Field(description=(
        "Vrai si le journal contient une insertion de cette ligne (`rev` 0). Faux : "
        "la ligne existait avant le journal, son historique commence en cours de vie."))
    first_revision_at: Optional[str] = Field(default=None, description=HORODATAGE)
    total_revisions: int = Field(description=(
        "Toutes colonnes confondues, y compris celles masquées aux agents."))
    note: str


class RowHistory(BaseModel):
    datastore: Optional[str] = None
    ns_id: Optional[int] = Field(default=None, description=identite.DESCRIPTION)
    row_id: str
    row_deleted: bool = Field(description=(
        "La ligne n'existe plus : son historique reste lisible par qui gouverne le "
        "tableau."))
    champ: Optional[str] = None
    coverage: Coverage
    revisions: list[Revision]
    next_before_id: Optional[int] = Field(default=None, description=(
        "À repasser en `before_id` pour lire la page suivante (plus ancienne). `null` = "
        "rien de plus ancien."))


def _masquees(ns_id: int) -> frozenset:
    if not aga.appel_d_agent():
        return frozenset()
    ns = db.get_datastore_by_id(ns_id) or {}
    return frozenset(aga.masquees(ns.get("schema")))


def _row_history(ctx: ResolvedCtx, inp: RowHistoryInput) -> dict:
    ns, row_id = _adresse(inp.datastore, inp.row_id)
    store = make_store(ctx.sub)
    try:
        ns_id = store.resolve_ns_id(ns)
    except DatastoreNotFound:
        raise ns_not_found(ctx.sub, ns)
    supprimee = db.datastore_get_row(ns_id, row_id) is None
    if supprimee and not ownership.can_govern(
            ctx.sub, ownership.TYPE_RESSOURCE_DATASTORE, str(ns_id)):
        raise AuthzDenied(404, "row_not_found")
    bilan = historique.bilan_de_ligne(ns_id, row_id)
    if supprimee and not bilan["revisions"]:
        raise AuthzDenied(404, "row_not_found")

    cachees = _masquees(ns_id)
    limit = max(1, min(LIMITE_MAX, inp.limit))
    lues = ([] if inp.champ in cachees else historique.revisions_de_ligne(
        ns_id, row_id, champ=inp.champ, avant_id=inp.before_id, limit=limit))
    suite = lues[limit - 1]["id"] if len(lues) > limit else None
    revisions = []
    for r in lues[:limit]:
        diff = {k: v for k, v in (r["diff"] or {}).items() if k not in cachees}
        # Une révision vide reste (l'insertion d'une ligne sans colonne) ; celle que le
        # masquage a vidée part : elle ne dirait à l'agent que « quelque chose de caché
        # a changé ici ».
        if diff or not r["diff"]:
            revisions.append({**r, "diff": diff})
    return {
        **identite.de_releve(store.dernier_tableau, ns),
        "row_id": row_id,
        "row_deleted": supprimee,
        "champ": inp.champ,
        "coverage": {
            "journal_since": historique.MISE_EN_SERVICE,
            "insert_recorded": bool(bilan["insertion"]),
            "first_revision_at": bilan["premiere"],
            "total_revisions": int(bilan["revisions"]),
            "note": historique.COUVERTURE,
        },
        "revisions": revisions,
        "next_before_id": suite,
    }


CAPABILITIES += [
    Capability(
        key="me.datastore.row_history",
        handler=_row_history,
        Input=RowHistoryInput,
        Output=RowHistory,
        authz=SUB_ONLY,
        mcp="data_row_history",
        rest=RestBinding(
            verb="GET",
            path="/api/datastores/{datastore}/rows/{row_id}/history",
        ),
        errors=(
            DeclaredError(404, "datastore_not_found",
                          "le tableau ne se voit pas depuis l'org de l'appel"),
            DeclaredError(404, "row_not_found",
                          "aucune ligne de cet `_id` dans ce tableau ; une ligne "
                          "SUPPRIMÉE ne se lit que par qui gouverne le tableau, et "
                          "seulement si le journal en a des révisions"),
            _JETON_MAL_PLACE,
        ),
        description=(
            "Read the revision history of ONE datastore row: every write, newest first, "
            "with the values before and after (`diff: {column: {avant, apres}}`), who "
            "wrote it (`acteur`), under which run (`run_id`), through which face "
            "(`source`: import, agent, console, api, upload, system) and in which "
            "gesture (`geste_id`). `champ` keeps only the revisions touching that "
            "column. Page with `limit` and `before_id` (= the previous `next_before_id`). "
            f"⚠️ Coverage: the journal only holds writes made since it went live on "
            f"{historique.MISE_EN_SERVICE}. A row with NO revision is NOT a row that "
            "was never modified: it may simply not have been written since. "
            "`coverage.insert_recorded: false` means the row predates the journal and "
            "its history starts mid-life. A deleted row keeps its history, readable "
            "only by whoever governs the table (`row_deleted: true`); deleting is not "
            "itself a revision."
        ),
    ),
]
