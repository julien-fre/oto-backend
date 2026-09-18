"""oto-backend#980 (suite) — page, total et compteurs d'écart de tri en UNE
seule requête, plutôt que trois (chacune reconstruisant sa propre CTE mince
introduite par #1009/#1011 et re-scannant le jeu filtré entier).

Mesuré en production par oto cd, après #1011 : page/`COUNT`/`order_health`
coûtent chacun ~400-500 ms sur le vivier réel (8 910 lignes) — trois passages
de détoastage sur les MÊMES lignes amincies. `datastore_page_with_stats`
calcule les trois sur une seule CTE `f` (le jeu mince ET filtré), lue une
seule fois : `agg` (total + compteurs) et `p` (la page) en dérivent toutes
les deux, jamais `s`/`datastore_rows` directement.

Piège vérifié explicitement : `offset` au-delà du total. `agg` (un `COUNT`
sans `GROUP BY`) porte TOUJOURS exactement une ligne — le combiné part donc de
`agg LEFT JOIN p ON TRUE`, jamais l'inverse, pour que le total survive même
quand la page est vide.

⚠️ Ce banc n'affirme rien sur une durée : il compare des BLOCS lus
(`EXPLAIN BUFFERS`) et des VALEURS, jamais un seuil de millisecondes.
"""
from __future__ import annotations

import json
import os
import uuid

import pytest

pytest.importorskip("psycopg")

SUB = "usr_page_stats_980"
PRIORITES = ["1", "2", "3", "4", "5"]
CATEGORIES = ["nord", "sud", "est", "ouest"]


def _remplissage_incompressible(n_octets: int) -> str:
    return os.urandom(n_octets).hex()


@pytest.fixture(scope="module")
def vivier(live):
    """Un tableau réel, 600 lignes, chacune assez grosse pour être TOASTée —
    échelle réduite par rapport à `test_thin_read_cte_980.py` (6 000) : ce
    banc-ci compare des FORMES de requête entre elles (fusionnée vs séparées),
    pas la CTE mince contre l'ancien chemin sans CTE — la taille n'a pas besoin
    d'être aussi grande pour que la différence se voie."""
    from oto_mcp import db
    from oto_mcp.db._conn import _connect

    db.upsert_user(SUB, email=f"{SUB}@page980.invalid", name=SUB)
    ns = "t980ps-" + uuid.uuid4().hex[:6]
    ns_id = db.create_datastore("user", SUB, ns)
    with _connect() as conn:
        for i in range(600):
            row_id = f"r-{i:04d}"
            data = {
                "priorite": PRIORITES[i % len(PRIORITES)],
                "categorie": CATEGORIES[i % len(CATEGORIES)],
                "padding": _remplissage_incompressible(2000),
            }
            conn.execute(
                "INSERT INTO datastore_rows (ns_id, row_id, data) VALUES (%s, %s, %s::jsonb)",
                (ns_id, row_id, json.dumps(data)),
            )
    return ns_id


def _relations_buffers(sql: str, params) -> int:
    from oto_mcp.db._conn import _connect

    with _connect() as conn:
        row = conn.execute(
            "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + sql, params
        ).fetchone()
    plan = list(row.values())[0][0]["Plan"]
    total = 0

    def _walk(node):
        nonlocal total
        total += (node.get("Shared Hit Blocks", 0) or 0)
        total += (node.get("Shared Read Blocks", 0) or 0)
        for enfant in node.get("Plans", []) or []:
            _walk(enfant)

    _walk(plan)
    return total


# ── identité : mêmes valeurs, fusionné vs séparé ─────────────────────────────

def test_page_total_et_health_identiques_aux_trois_appels_separes(vivier):
    from oto_mcp.db import datastore as dsdb

    filtre = [{"field": "categorie", "op": "in", "value": ["nord", "sud"]}]

    # Chemin séparé (celui que `page_rows` utilise en repli quand la fusion
    # n'est pas applicable, et qu'utilisaient #1009/#1011 avant ce lot).
    lignes_separe = dsdb.datastore_list_rows(
        vivier, offset=0, limit=50, order_by="priorite", order_dir="asc",
        filters=filtre, order_type="enum", order_options=PRIORITES)
    total_separe = dsdb.datastore_count_rows(vivier, filters=filtre)
    health_separe = dsdb.datastore_order_health(
        vivier, order_by="priorite", order_type="enum", order_options=PRIORITES,
        filters=filtre)

    # Chemin fusionné.
    combined = dsdb.datastore_page_with_stats(
        vivier, offset=0, limit=50, order_by="priorite", order_dir="asc",
        filters=filtre, order_type="enum", order_options=PRIORITES)
    assert combined is not None, "priorite+categorie sont amincissables"
    lignes_fusion, total_fusion, off_type, empty = combined

    assert [r["row_id"] for r in lignes_fusion] == [r["row_id"] for r in lignes_separe]
    assert [r["data"] for r in lignes_fusion] == [r["data"] for r in lignes_separe]
    assert total_fusion == total_separe
    assert (off_type, empty) == (health_separe["off_type"], health_separe["empty"])


def test_sans_tri_type_page_et_total_seuls_sont_fusionnes(vivier):
    """Pas de tri typé → pas de compteur d'écart à calculer (même geste que
    `_order_health`, qui ne s'appelle même pas dans ce cas) : `off_type`/
    `empty` valent 0 par construction, sans coût de calcul superflu."""
    from oto_mcp.db import datastore as dsdb

    filtre = [{"field": "categorie", "op": "in", "value": ["est", "ouest"]}]
    total_separe = dsdb.datastore_count_rows(vivier, filters=filtre)
    lignes_separe = dsdb.datastore_list_rows(vivier, offset=0, limit=50, filters=filtre)

    combined = dsdb.datastore_page_with_stats(vivier, offset=0, limit=50, filters=filtre)
    assert combined is not None
    lignes_fusion, total_fusion, off_type, empty = combined
    assert total_fusion == total_separe
    assert {r["row_id"] for r in lignes_fusion} == {r["row_id"] for r in lignes_separe}
    assert (off_type, empty) == (0, 0)


# ── le piège explicite : offset au-delà du total ─────────────────────────────

def test_offset_au_dela_du_total_rend_une_page_vide_et_un_total_juste(vivier):
    """Le piège nommé par oto cd : `agg` (COUNT sans GROUP BY) porte toujours
    une ligne, `p` peut être vide — le combiné doit partir de `agg LEFT JOIN p`
    pour que le total survive même quand la page ne rend rien."""
    from oto_mcp.db import datastore as dsdb

    filtre = [{"field": "categorie", "op": "in", "value": ["nord", "sud"]}]
    total_reel = dsdb.datastore_count_rows(vivier, filters=filtre)
    assert total_reel > 0

    combined = dsdb.datastore_page_with_stats(
        vivier, offset=total_reel + 1000, limit=50, filters=filtre)
    assert combined is not None
    lignes, total, off_type, empty = combined
    assert lignes == []
    assert total == total_reel  # ⚠️ c'est LE piège : ne doit jamais retomber à 0
    assert (off_type, empty) == (0, 0)


def test_offset_au_dela_avec_tri_type_garde_aussi_le_total_et_les_compteurs(vivier):
    from oto_mcp.db import datastore as dsdb

    total_reel = dsdb.datastore_count_rows(vivier)
    combined = dsdb.datastore_page_with_stats(
        vivier, offset=total_reel + 500, limit=50, order_by="priorite",
        order_dir="asc", order_type="enum", order_options=PRIORITES)
    assert combined is not None
    lignes, total, off_type, empty = combined
    assert lignes == []
    assert total == total_reel


def test_espace_totalement_vide_rend_zero_partout(vivier):
    """Un `ns_id` filtré sur RIEN (aucune ligne ne matche) : `agg` elle-même ne
    porte plus de ligne (le `FROM f` de `agg` est vide, mais `COUNT(*)` sur un
    jeu vide reste UNE ligne à 0 — vérifié par construction SQL ; ce test
    couvre le filtre qui exclut tout, pas un `ns_id` inexistant)."""
    from oto_mcp.db import datastore as dsdb

    filtre = [{"field": "categorie", "op": "in", "value": ["jamais-vu"]}]
    combined = dsdb.datastore_page_with_stats(vivier, offset=0, limit=50, filters=filtre)
    assert combined == ([], 0, 0, 0)


# ── le gain : moins de blocs qu'en appelant les trois séparément ────────────

def test_la_fusion_lit_moins_de_blocs_que_les_trois_appels_separes(vivier):
    from oto_mcp.db.query import thin_read_cte_sql, typed_order_sql, order_health_sql
    from oto_mcp.db.paths import field_read_sql

    filtre = [{"field": "categorie", "op": "in", "value": ["nord", "sud"]}]
    thin = thin_read_cte_sql(vivier, None, filtre, "priorite")
    cte_sql, cte_params, where_sql, where_params = thin
    _v, _vp = field_read_sql("priorite")
    order_sql, order_params = typed_order_sql(_v, _vp, "enum", PRIORITES, "ASC")
    health_proj, health_params = order_health_sql(_v, _vp, "enum", PRIORITES)

    # Trois requêtes séparées, chacune sa propre CTE mince (forme #1009/#1011).
    sql_page = (
        f"WITH {cte_sql}, "
        f"p AS (SELECT row_id, row_number() OVER (ORDER BY {order_sql}) AS rn "
        f"FROM s {where_sql} ORDER BY rn LIMIT 50 OFFSET 0) "
        "SELECT dr.row_id, dr.data FROM p JOIN datastore_rows dr "
        "ON dr.row_id = p.row_id AND dr.ns_id = %s ORDER BY p.rn"
    )
    blocs_page = _relations_buffers(
        sql_page, tuple(cte_params + order_params + where_params + [vivier]))
    sql_count = f"WITH {cte_sql} SELECT COUNT(*) AS n FROM s {where_sql}"
    blocs_count = _relations_buffers(sql_count, tuple(cte_params + where_params))
    sql_health = f"WITH {cte_sql} SELECT {health_proj} FROM s {where_sql}"
    blocs_health = _relations_buffers(
        sql_health, tuple(cte_params + health_params + where_params))
    blocs_separes = blocs_page + blocs_count + blocs_health

    # La requête fusionnée telle que `datastore_page_with_stats` la construit.
    from oto_mcp.db import datastore as dsdb
    sql_fusion = (
        f"WITH {cte_sql}, "
        f"f AS (SELECT * FROM s {where_sql}), "
        f"agg AS (SELECT COUNT(*) AS total, {health_proj} FROM f), "
        f"p AS (SELECT row_id, row_number() OVER (ORDER BY {order_sql}) AS rn "
        f"FROM f ORDER BY rn LIMIT 50 OFFSET 0) "
        "SELECT agg.total, agg.off_type, agg.empty, p.rn, dr.data "
        "FROM agg LEFT JOIN p ON TRUE "
        "LEFT JOIN datastore_rows dr ON dr.row_id = p.row_id AND dr.ns_id = %s "
        "ORDER BY p.rn"
    )
    params_fusion = (cte_params + where_params + health_params
                     + order_params + [vivier])
    blocs_fusion = _relations_buffers(sql_fusion, tuple(params_fusion))

    assert blocs_fusion < blocs_separes, (
        f"la fusion devait lire moins de blocs que les trois passages séparés : "
        f"séparés={blocs_separes} (page={blocs_page}, count={blocs_count}, "
        f"health={blocs_health}), fusion={blocs_fusion}")


# ── repli : non amincissable (recherche plein texte) ─────────────────────────

def test_recherche_plein_texte_n_est_pas_amincissable_rend_none(vivier):
    """`q` a besoin de `data` ENTIER (oto-backend#980, doc de `thin_read_cte_sql`) :
    `datastore_page_with_stats` doit rendre `None`, comme `thin_read_cte_sql`
    lui-même — l'appelant (`page_rows`) retombe alors sur les trois appels
    historiques, inchangés."""
    from oto_mcp.db import datastore as dsdb

    combined = dsdb.datastore_page_with_stats(vivier, offset=0, limit=50, q="nord")
    assert combined is None
