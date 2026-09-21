"""#664 — `rows_released` ne compte que des lignes qui tenaient encore un bail.

Mesuré en prod le 31/08/2026 : `datastore_release_claim` (le rendu ligne à ligne)
effaçait `claimed_by` et `claimed_until` mais PAS `claimed_run`, alors que
`datastore_release_by_run` (la fermeture d'un run) compte tout ce qui porte le run.
Une ligne déjà rendue à la main était donc recomptée par `run_finish` / `complete`.
"""
from __future__ import annotations

import uuid

SUB = "sub-664"


def _table(n: int):
    from oto_mcp import db
    ns_id = db.create_datastore("user", SUB, "file-" + uuid.uuid4().hex[:6])
    for i in range(n):
        db.datastore_insert_row(ns_id, f"r{i}", {"statut": "a_faire"})
    return ns_id


def _reserver(ns_id: int, run_id: str, n: int) -> list[str]:
    from oto_mcp import db
    ids = []
    for _ in range(n):
        row = db.datastore_claim_next(ns_id, worker="w-664", run_id=run_id)
        ids.append(row["row_id"])
    return ids


def test_un_rendu_ligne_a_ligne_n_est_pas_recompte_a_la_fermeture(live):
    from oto_mcp import db
    ns_id, run = _table(3), "run-" + uuid.uuid4().hex[:8]
    ids = _reserver(ns_id, run, 3)

    assert db.datastore_release_claim(ns_id, ids[0], "w-664") is True

    # Deux baux tiennent encore : la fermeture en rend deux, pas trois.
    assert db.datastore_release_by_run(run) == 2


def test_un_rendu_inconditionnel_efface_aussi_le_run(live):
    from oto_mcp import db
    from oto_mcp.db._conn import _connect
    ns_id, run = _table(1), "run-" + uuid.uuid4().hex[:8]
    (rid,) = _reserver(ns_id, run, 1)

    assert db.datastore_release_claim(ns_id, rid, None) is True

    with _connect() as conn:
        row = conn.execute("SELECT claimed_by, claimed_until, claimed_run "
                           "FROM datastore_rows WHERE ns_id = %s AND row_id = %s",
                           (ns_id, rid)).fetchone()
    assert (row["claimed_by"], row["claimed_until"], row["claimed_run"]) == (None,) * 3
