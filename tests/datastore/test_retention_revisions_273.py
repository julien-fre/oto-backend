"""La RÉTENTION du journal des révisions de ligne (oto#273, décision du 23/09/2026).

Sur une vraie base : on vieillit des révisions à la main (`at` reculé), on joue la purge
par son vrai chemin (`oto-mcp maintenance revisions`), et on relit la table.

1. **la règle** — au-delà de la rétention, une révision part ; en deçà, elle reste ;
2. **l'exception** — une révision `import` d'une ligne vivante reste, quel que soit son
   âge ; ligne supprimée, ou supprimée puis recréée sous le même `row_id`, elle part ;
3. **les lots** — la purge avance par lots bornés sur la clé primaire, s'arrête au
   premier lot sans révision assez vieille, et un plafond de lots la laisse incomplète
   sans rien casser ;
4. **le réglage** — `OTO_JOURNAL_REVISIONS_RETENTION_DAYS`, 90 par défaut, LÈVE s'il
   est illisible ; le travail est dans le passage quotidien, et son mode à blanc compte.
"""
from __future__ import annotations

import uuid

import pytest

SUB = "sub-retention-273"


def _sql(requete: str, *params):
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        cur = conn.execute(requete, params or None)
        return cur.fetchall() if cur.description else None


@pytest.fixture(scope="module")
def compte(live):
    from oto_mcp import db
    db.upsert_user(SUB, email=f"{SUB}@retention.invalid", name=SUB)
    return SUB


def _table() -> int:
    from oto_mcp import db
    return db.create_datastore("user", SUB, "retention-" + uuid.uuid4().hex[:6])


def _vieillir(ns_id: int, jours: int, row_id: str | None = None) -> None:
    _sql("UPDATE datastore_row_revisions SET at = now() - make_interval(days => %s) "
         "WHERE ns_id = %s AND (%s::text IS NULL OR row_id = %s)",
         jours, ns_id, row_id, row_id)


def _restantes(ns_id: int) -> list[tuple]:
    return [(r["row_id"], r["rev"], r["source"], r["suppression"]) for r in _sql(
        "SELECT row_id, rev, source, suppression FROM datastore_row_revisions "
        "WHERE ns_id = %s ORDER BY id", ns_id)]


def _purger(**kw) -> dict:
    from oto_mcp.db import retention_revisions
    return retention_revisions.purger_revisions(90, **kw)


# ── 1. la règle ────────────────────────────────────────────────────────────────

def test_au_dela_de_la_retention_une_revision_part_en_deca_elle_reste(compte):
    from oto_mcp import db
    ns_id = _table()
    db.datastore_insert_row(ns_id, "r1", {"a": 1})
    db.datastore_upsert_row(ns_id, "r1", {"a": 2})
    db.datastore_insert_row(ns_id, "r2", {"a": 1})
    _vieillir(ns_id, 91, "r1")
    _vieillir(ns_id, 89, "r2")
    _purger()
    assert _restantes(ns_id) == [("r2", 0, "system", False)]
    assert db.datastore_get_row(ns_id, "r1"), "la LIGNE reste : seul son journal part"


def test_une_revision_de_suppression_part_aussi(compte):
    from oto_mcp import db
    ns_id = _table()
    db.datastore_insert_row(ns_id, "r1", {"a": 1})
    db.datastore_delete_row(ns_id, "r1")
    _vieillir(ns_id, 120)
    _purger()
    assert _restantes(ns_id) == []


# ── 2. l'exception de l'import ─────────────────────────────────────────────────

def _importee(ns_id: int, row_id: str) -> None:
    from oto_mcp import db
    db.datastore_insert_row(ns_id, row_id, {"a": 1})
    _sql("UPDATE datastore_row_revisions SET source = 'import' "
         "WHERE ns_id = %s AND row_id = %s", ns_id, row_id)


def test_l_import_d_une_ligne_vivante_reste_quel_que_soit_son_age(compte):
    from oto_mcp import db
    ns_id = _table()
    _importee(ns_id, "vivante")
    db.datastore_upsert_row(ns_id, "vivante", {"a": 2})
    _vieillir(ns_id, 400)
    _purger()
    assert _restantes(ns_id) == [("vivante", 0, "import", False)], \
        "l'origine reste, la modification qui la suit part"


def test_l_import_d_une_ligne_supprimee_rend_a_la_regle_commune(compte):
    from oto_mcp import db
    ns_id = _table()
    _importee(ns_id, "partie")
    _importee(ns_id, "recreee")
    _importee(ns_id, "absente")
    db.datastore_delete_row(ns_id, "partie")
    db.datastore_delete_row(ns_id, "recreee")
    db.datastore_insert_row(ns_id, "recreee", {"a": 9})
    # Supprimée HORS journal (coupé, ou avant les révisions de suppression) : seule
    # l'absence de la ligne le dit.
    _sql("DELETE FROM datastore_rows WHERE ns_id = %s AND row_id = 'absente'", ns_id)
    _sql("DELETE FROM datastore_row_revisions WHERE ns_id = %s AND row_id = 'absente' "
         "AND suppression", ns_id)
    _vieillir(ns_id, 100)
    _sql("UPDATE datastore_row_revisions SET at = now() WHERE ns_id = %s "
         "AND row_id = 'recreee' AND NOT suppression AND source IS DISTINCT FROM 'import'",
         ns_id)
    _purger()
    assert _restantes(ns_id) == [("recreee", 0, "system", False)], \
        "seule reste la recréation, récente ; les trois imports sont partis"


# ── 3. les lots ────────────────────────────────────────────────────────────────

def test_la_purge_avance_par_lots_et_s_arrete_a_la_zone_recente(compte):
    from oto_mcp import db
    _sql("DELETE FROM datastore_row_revisions")
    ns_id = _table()
    for i in range(7):
        db.datastore_insert_row(ns_id, f"v{i}", {"i": i})
    _vieillir(ns_id, 91)
    _importee(ns_id, "origine")             # vieille, gardée : relue, jamais purgée
    _vieillir(ns_id, 91, "origine")
    for i in range(5):
        db.datastore_insert_row(ns_id, f"n{i}", {"i": i})

    out = _purger(lot=2)
    # 8 vieilles (7 + l'import) sur 4 lots, puis 1 lot de récentes qui arrête tout.
    assert out == {"purgees": 7, "lots": 5, "complet": True}
    assert [r[0] for r in _restantes(ns_id)] == ["origine"] + [f"n{i}" for i in range(5)]
    assert _purger(lot=2) == {"purgees": 0, "lots": 2, "complet": True}, \
        "rejouée, elle ne relit que l'import gardé et le premier lot récent"


def test_le_plafond_de_lots_laisse_la_purge_incomplete(compte):
    from oto_mcp import db
    _sql("DELETE FROM datastore_row_revisions")
    ns_id = _table()
    for i in range(5):
        db.datastore_insert_row(ns_id, f"v{i}", {"i": i})
    _vieillir(ns_id, 91)
    assert _purger(lot=2, max_lots=1) == {"purgees": 2, "lots": 1, "complet": False}
    assert len(_restantes(ns_id)) == 3
    assert _purger(lot=2)["purgees"] == 3, "le passage suivant reprend"


def test_une_table_vide_ne_coute_qu_un_lot(compte):
    _sql("DELETE FROM datastore_row_revisions")
    assert _purger() == {"purgees": 0, "lots": 1, "complet": True}


# ── 4. le réglage et le travail ────────────────────────────────────────────────

def test_la_retention_vaut_90_jours_par_defaut(monkeypatch):
    from oto_mcp.db import journal_revisions
    monkeypatch.delenv("OTO_JOURNAL_REVISIONS_RETENTION_DAYS", raising=False)
    assert journal_revisions.retention_jours() == 90
    monkeypatch.setenv("OTO_JOURNAL_REVISIONS_RETENTION_DAYS", " 30 ")
    assert journal_revisions.retention_jours() == 30


@pytest.mark.parametrize("valeur", ["", "abc", "0", "-3", "90j", "1.5"])
def test_une_retention_illisible_leve(monkeypatch, valeur):
    from oto_mcp.db import journal_revisions
    monkeypatch.setenv("OTO_JOURNAL_REVISIONS_RETENTION_DAYS", valeur)
    with pytest.raises(ValueError, match="OTO_JOURNAL_REVISIONS_RETENTION_DAYS"):
        journal_revisions.retention_jours()


def test_le_travail_est_quotidien_compte_a_blanc_et_echoue_sur_un_reglage_illisible(
        compte, monkeypatch):
    from oto_mcp import db, maintenance
    assert "revisions" in maintenance._ALL, "hors du passage quotidien, il ne tourne pas"
    _sql("DELETE FROM datastore_row_revisions")
    ns_id = _table()
    db.datastore_insert_row(ns_id, "r1", {"a": 1})
    db.datastore_insert_row(ns_id, "r2", {"a": 1})
    _vieillir(ns_id, 31, "r1")
    monkeypatch.setenv("OTO_JOURNAL_REVISIONS_RETENTION_DAYS", "30")
    assert maintenance.revisions(dry_run=True) == {"retention_days": 30, "purgeables": 1}
    assert len(_restantes(ns_id)) == 2, "à blanc, rien ne part"
    # Un lot qui contient une vieille révision, puis la fin de table.
    assert maintenance.revisions() == {"retention_days": 30, "purgees": 1, "lots": 2,
                                       "complet": True}
    assert [r[0] for r in _restantes(ns_id)] == ["r2"]
    monkeypatch.setenv("OTO_JOURNAL_REVISIONS_RETENTION_DAYS", "trois mois")
    assert maintenance.run(["revisions"], strict=True) == 1
    assert maintenance.main(["revisions"]) == 0, "fail-open hors `--strict`"
