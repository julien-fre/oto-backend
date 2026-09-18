"""Le boot ne doit pas RE-DEMANDER l'`AccessExclusiveLock` d'un DDL DÉJÀ
appliqué (oto-backend, incident mesuré 2026-09-18) : `ALTER TABLE … ADD COLUMN
IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS` prennent leur verrou AVANT de
constater qu'ils n'ont rien à faire — le `IF NOT EXISTS` évite l'ERREUR, pas
le VERROU. Sur la base PARTAGÉE préprod/prod, ce verrou pris pour rien met en
file le trafic applicatif qui lit la même table pendant que le boot attend le
verrou SUIVANT de sa transaction unique (`apply_boot_schema`) — jusqu'à ~5 s
par tentative de déploiement, mesuré sur `orgs` et `datastore_rows`.

⚠️ Un test qui TIENT lui-même un verrou et regarde si le boot en redemande un
prouve trop : sur une base déjà migrée, le boot lit encore les DONNÉES de la
table pour ses backfills conditionnels (`docs.position`, `orgs.kb_project_id`)
— une lecture légitime, qui attend forcément derrière un `AccessExclusiveLock`
tenu ailleurs, guard ou pas. Ce que le correctif retire, ce sont les DDL
DEVENUS inutiles, pas ces lectures. La preuve retenue ici OBSERVE le SQL
réellement envoyé (même instrument que `test_boot_order_replay.py`) : sur une
base déjà migrée, aucun `ALTER`/`CREATE INDEX` pour une forme déjà posée ne
doit apparaître dans les ordres exécutés.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

# `tests/` n'est pas un package (pas de `__init__.py`) — un import `from tests.x
# import y` marche en LOCAL (PYTHONPATH=. insère la racine du dépôt) mais PAS en
# CI (pytest insère `tests/` lui-même dans `sys.path`, pas son parent). On importe
# donc `test_boot_order_replay` comme module DE PREMIER NIVEAU, exactement comme
# pytest le fait pour le charger lui-même.
sys.path.insert(0, str(Path(__file__).parent))
from test_boot_order_replay import _OrdresEnregistres, base_bootee  # noqa: E402,F401


def _sql_rejoue(base_bootee) -> list[str]:
    """Les ordres SQL d'un second passage du boot, sur la base déjà bootée par
    le fixture — en transaction ANNULÉE, comme `replay_boot_schema_dry`."""
    from oto_mcp.db._init import apply_boot_schema

    enregistreur = _OrdresEnregistres(base_bootee)
    with base_bootee.transaction(force_rollback=True):
        apply_boot_schema(enregistreur)
    return enregistreur.sql


def test_rejeu_ne_redemande_aucun_ddl_deja_pose_sur_orgs_et_datastore_rows(base_bootee):
    """Aucun `ALTER TABLE orgs|datastore_rows ADD COLUMN` ni `CREATE INDEX` déjà
    posé ne doit être RÉ-ENVOYÉ — c'est cet envoi, à lui seul, qui demandait le
    verrou pour rien (le `IF NOT EXISTS` évite l'erreur APRÈS coup)."""
    sql = _sql_rejoue(base_bootee)

    fautifs = [
        s for s in sql
        if re.search(r"ALTER\s+TABLE\s+(orgs|datastore_rows)\s+(ADD|DROP)\s+COLUMN",
                     s, re.IGNORECASE)
        or re.search(r"CREATE\s+(UNIQUE\s+)?INDEX\s+IF\s+NOT\s+EXISTS\s+"
                     r"(idx_datastore_rows_embed_dirty|uq_orgs_personal_of)\b",
                     s, re.IGNORECASE)
    ]
    assert not fautifs, (
        f"{len(fautifs)} ordre(s) DDL déjà posés ont été renvoyés sur un rejeu — "
        f"donc leur AccessExclusiveLock redemandé pour rien : {fautifs}")


def test_rejeu_dynamique_search_ne_redemande_rien_sur_datastore_rows(base_bootee):
    """Même preuve pour le DDL DYNAMIQUE, généré par `db/search.py`
    (`rank_column_ddl`/`index_ddl`, une boucle sur plusieurs tables dont
    `datastore_rows`) — la forme qui a échappé au premier passage de ce
    correctif, ATTRAPÉE PAR CE BANC (rouge avant le câblage de
    `_executer_ddl_idempotent` dans les deux boucles). Motifs EXACTS produits
    par ce module — pas une recherche large, qui confondrait avec `_SCHEMA`
    (le `CREATE TABLE IF NOT EXISTS` initial, hors du périmètre de ce lot)."""
    sql = _sql_rejoue(base_bootee)
    formes_dynamiques = (
        "ALTER TABLE datastore_rows ADD COLUMN IF NOT EXISTS search_vec",
        "CREATE INDEX IF NOT EXISTS idx_datastore_rows_fts",
        "CREATE INDEX IF NOT EXISTS idx_datastore_rows_trgm",
    )
    fautifs = [s for s in sql if any(s.strip().startswith(f) for f in formes_dynamiques)]
    assert not fautifs, f"DDL dynamique redemandé sur un rejeu : {fautifs}"


def test_colonne_absente_lit_le_catalogue(base_bootee):
    """`_colonne_absente` : lecture pure du catalogue (`information_schema`),
    jamais un accès à la table elle-même — vérifié séparément (voir le test
    ci-dessus, où ce garde n'a jamais empêché la suite du rejeu)."""
    from oto_mcp.db._init import _colonne_absente

    assert _colonne_absente(base_bootee, "orgs", "tenant_id") is False
    assert _colonne_absente(base_bootee, "orgs", "une_colonne_qui_nexiste_pas_xyz") is True


def test_index_absent_lit_le_catalogue(base_bootee):
    from oto_mcp.db._init import _index_absent

    assert _index_absent(base_bootee, "idx_datastore_rows_embed_dirty") is False
    assert _index_absent(base_bootee, "un_index_qui_nexiste_pas_xyz") is True


def test_reboot_ne_reecrit_pas_les_positions_docs_deja_posees(base_bootee):
    """Le garde posé sur l'`UPDATE docs … position` : une base déjà backfillée
    (aucune ligne `position IS NULL`) ne doit plus déclencher le scan/UPDATE à
    chaque boot — vérifié en observant qu'aucune ligne n'est RÉÉCRITE (xmin
    inchangé) sur un rejeu, pas seulement que la valeur reste correcte."""
    from oto_mcp.db import init_db

    row = base_bootee.execute(
        "SELECT id, position, xmin FROM docs ORDER BY id LIMIT 1"
    ).fetchone()
    if row is None:
        pytest.skip("aucune ligne docs sur cette base jetable — rien à observer")
    doc_id, position_avant, xmin_avant = row["id"], row["position"], row["xmin"]
    assert position_avant is not None, "précondition : la base bootée a déjà backfillé position"

    init_db()

    xmin_apres = base_bootee.execute(
        "SELECT xmin FROM docs WHERE id = %s", (doc_id,)
    ).fetchone()["xmin"]
    assert xmin_apres == xmin_avant, (
        "la ligne a été réécrite par l'UPDATE de backfill alors qu'elle était déjà "
        "à jour — le garde `WHERE position IS NULL LIMIT 1` n'a pas sauté l'UPDATE")
