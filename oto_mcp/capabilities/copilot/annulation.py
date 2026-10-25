"""Undo for a table change the in-app agent made: once, before it expires, and only on
what still holds what the agent wrote.

**Rows: from the revision journal** (`datastore_row_revisions`, `db/historique.py`). Each
call of the agent runs under a GESTE of its own, which the journal stamps on every row it
writes. Undo reads that geste's revisions back (from a mark taken just before the call)
and, row by row:

- a row the agent CREATED is deleted — only if every field it wrote still holds that value;
- a row the agent DELETED is written back, with the values it had;
- a field the agent CHANGED gets its previous value back — only if it still holds what
  the agent wrote. A cell someone changed since is left alone, and counted as skipped.

No revision under the geste (the journal switched off, nothing written): no Undo.

**Tables and fields: by snapshot**, the journal does not cover them. A table the agent
created is deleted only while it is EMPTY; a field change is undone only if the stored
schema is still the one the agent left.

Only `data_*` writes get an Undo. A send, a launch, a third-party write: none — undoing
the table side of an email that left would claim more than it does.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from ... import geste
from ...auth import hooks as auth_hooks
from ...datastore.core import make_store
from ...db import copilot as store
from ...db import historique

logger = logging.getLogger(__name__)

#: How long an Undo is offered.
DUREE_S = 3600
#: Beyond this many revisions under one geste, no Undo (a bulk change is not undone
#: blind, cell by cell).
MAX_REVISIONS = 1000
_ABSENT = object()


@dataclass
class Preparation:
    target: str
    geste_id: str
    repere: int = 0
    ns_id: Optional[int] = None
    schema_avant: object = None
    actif: bool = True
    extra: dict = field(default_factory=dict)


def preparer(sub: str, target: str, args: dict, geste_id: str) -> Optional[Preparation]:
    """BEFORE a write. Sync (call it off the loop). None = this call gets no Undo."""
    if not target.startswith("data_"):
        return None
    prep = Preparation(target, geste_id, repere=historique.dernier_id())
    if target == "data_patch_schema" and args.get("datastore") is not None:
        try:
            with auth_hooks.sub_override(sub):
                prep.ns_id = make_store(sub)._resolve(str(args["datastore"]), write=True)
            prep.schema_avant = store.schema_stocke(prep.ns_id)
        except Exception:
            logger.warning("copilot : instantané de schéma impossible — pas d'annulation "
                           "pour ce geste", exc_info=True)
            prep.ns_id = None
    return prep


def offre(prep: Optional[Preparation], resultat) -> Optional[tuple[str, dict]]:
    """AFTER a successful write: `(kind, payload)` of the Undo to offer, or None. Sync."""
    if prep is None:
        return None
    if prep.target == "data_create_datastore":
        ns = (resultat or {}).get("ns_id") if isinstance(resultat, dict) else None
        return ("table", {"ns_id": int(ns)}) if isinstance(ns, int) else None
    if prep.target == "data_patch_schema":
        if prep.ns_id is None or prep.schema_avant is None:
            return None
        return ("schema", {"ns_id": prep.ns_id, "avant": prep.schema_avant,
                           "apres": store.schema_stocke(prep.ns_id)})
    revs = historique.revisions_du_geste(prep.geste_id, prep.repere,
                                         limit=MAX_REVISIONS + 1)
    if not revs or len(revs) > MAX_REVISIONS:
        return None
    return ("rows", {"geste_id": prep.geste_id, "repere": prep.repere})


def _bilan(revs: list[dict]) -> dict:
    """Per (ns_id, row_id): created?, deleted at the end?, each field's value before the
    first revision and after the last."""
    lignes: dict = {}
    for r in revs:
        cle = (int(r["ns_id"]), str(r["row_id"]))
        diff = r.get("diff") or {}
        l = lignes.get(cle)
        if l is None:
            creee = (not r.get("suppression") and int(r.get("rev") or 0) == 0
                     and all("avant" not in d for d in diff.values()))
            l = lignes[cle] = {"creee": creee, "supprimee": False, "avant": {}, "apres": {}}
        for champ, d in diff.items():
            if champ not in l["avant"]:
                l["avant"][champ] = d.get("avant", _ABSENT)
            l["apres"][champ] = d.get("apres", _ABSENT)
        l["supprimee"] = bool(r.get("suppression"))
    return lignes


def _defaire_lignes(sub: str, payload: dict) -> dict:
    revs = historique.revisions_du_geste(payload["geste_id"], int(payload["repere"]),
                                         limit=MAX_REVISIONS + 1)
    from ... import db
    s = make_store(sub)
    faits = laisses = 0
    for (ns_id, row_id), l in _bilan(revs).items():
        if l["creee"] and l["supprimee"]:
            continue
        actuelle = db.datastore_get_row(ns_id, row_id)
        donnees = (actuelle or {}).get("data") or {}
        if l["supprimee"]:
            if actuelle is not None:
                laisses += 1
                continue
            valeurs = {k: v for k, v in l["avant"].items()
                       if v is not _ABSENT and not str(k).startswith("_")}
            s.write_rows(str(ns_id), [valeurs], origine_override=True)
            faits += 1
            continue
        if actuelle is None:
            laisses += 1
            continue
        if l["creee"]:
            if all(donnees.get(k, _ABSENT) == v for k, v in l["apres"].items()):
                s.delete_row(str(ns_id), row_id)
                faits += 1
            else:
                laisses += 1
            continue
        patch = {}
        for champ, apres in l["apres"].items():
            if donnees.get(champ, _ABSENT) == apres:
                avant = l["avant"].get(champ, _ABSENT)
                patch[champ] = None if avant is _ABSENT else avant
            else:
                laisses += 1
        if patch:
            s.update_row(str(ns_id), row_id, patch, origine_override=True)
            faits += 1
    return {"undone": faits, "skipped": laisses}


def _defaire_table(sub: str, payload: dict) -> dict:
    ns_id = int(payload["ns_id"])
    if not store.tableau_vide(ns_id):
        return {"undone": 0, "skipped": 1}
    make_store(sub).delete_datastore(str(ns_id))
    return {"undone": 1, "skipped": 0}


def _defaire_schema(sub: str, payload: dict) -> dict:
    ns_id = int(payload["ns_id"])
    if store.schema_stocke(ns_id) != payload.get("apres"):
        return {"undone": 0, "skipped": 1}
    avant = payload.get("avant") or {}
    cles = lambda sch: {f.get("key") for f in (sch or {}).get("fields") or []  # noqa: E731
                        if isinstance(f, dict)}
    retires = sorted(k for k in cles(payload.get("apres")) - cles(avant) if k)
    make_store(sub).set_schema(str(ns_id), avant, retraits_annonces=retires)
    return {"undone": 1, "skipped": 0}


_DEFAIRE = {"rows": _defaire_lignes, "table": _defaire_table, "schema": _defaire_schema}


def defaire(sub: str, ligne: dict) -> dict:
    """Undoes one offered change, as the person, under a geste of its own (the undo is
    journaled too). Sync. Raises on a refusal of the store (rights, validation)."""
    with auth_hooks.sub_override(sub), geste.portee(geste.AGENT, sub):
        return _DEFAIRE[ligne["kind"]](sub, ligne.get("payload") or {})
