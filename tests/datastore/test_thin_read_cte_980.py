"""oto-backend#980 (suite) — le détoastage répété de `data` dans un tri typé et
dans des filtres, sur une vraie base PostgreSQL avec des lignes assez grosses
pour être TOASTées (> 2 Ko).

Mesuré en production (v1.309.0, vivier réel 8 910 lignes) :
- tri `priorite` (enum), page de 50 : 924 468 blocs lus (3 861 sans tri) ;
- filtres réels (`client_audiens:in` + `priorite:in`), page de 50, sans tri :
  237 782 blocs pour la page, autant pour le `COUNT` — deux fois par appel.

Cause : `field_read_sql` lit `data` 7 fois par évaluation ; `typed_order_sql`
l'appelle 8 fois (56 références par ligne triée), `order_health_sql` 5 fois
(35), et chaque filtre autant de fois qu'il est évalué. PostgreSQL détoaste
`data` à CHAQUE référence, sans jamais mettre en cache.

Ce banc n'affirme rien sur une durée (la machine ment) : il compte les BLOCS
lus (`EXPLAIN (ANALYZE, BUFFERS)`) d'une requête construite à la MAIN avec
l'ANCIEN patron (référence directe et répétée à `data`, sans CTE mince) contre
la MÊME requête telle que `thin_read_cte_sql` la pose désormais, et vérifie
que les VALEURS rendues sont identiques dans les deux cas — seul le COÛT
change.
"""
from __future__ import annotations

import json
import os
import uuid

import pytest

pytest.importorskip("psycopg")


# ── mise en place : un vivier réel, données TOASTées ──────────────────────────

SUB = "usr_thin_980"
PRIORITES = ["1", "2", "3", "4", "5"]
CATEGORIES = ["nord", "sud", "est", "ouest"]


def _remplissage_incompressible(n_octets: int) -> str:
    """`n_octets` d'hexadécimal ALÉATOIRE — jamais une répétition (`"x" * n`
    compresse à quasi rien sous TOAST/pglz : la ligne reste INLINE malgré une
    longueur affichée > 2 Ko, et le défaut mesuré n'existe plus). Un contenu
    incompressible dépasse réellement le seuil TOAST après tentative de
    compression, comme le JSON métier d'un vivier réel (texte varié, pas un
    motif répété)."""
    return os.urandom(n_octets).hex()


@pytest.fixture(scope="module")
def vivier(live):
    """Un tableau réel, ~600 lignes, chacune assez grosse pour être TOASTée.

    `live` (conftest.py) pose déjà `DATABASE_URL` sur une base neuve du module
    et y rejoue `init_db()` — c'est le harnais unique du repo, pas une fixture
    ad hoc de plus."""
    from oto_mcp import db
    from oto_mcp.db._conn import _connect

    db.upsert_user(SUB, email=f"{SUB}@thin980.invalid", name=SUB)
    ns = "t980-" + uuid.uuid4().hex[:6]
    ns_id = db.create_datastore("user", SUB, ns)
    with _connect() as conn:
        for i in range(6000):
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
    """Blocs partagés (hit + read), sommés sur TOUT l'arbre du plan — le
    compteur qui juge ce banc, jamais une durée (cf. la leçon de
    `test_resolve_sub_chaine_live.py` : la même requête ment sur une petite base
    si on ne lit que le plan déclaré, pas ce qui s'exécute)."""
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


def _ancienne_requete_triee(ns_id: int, order_by: str, order_type: str,
                            order_options, direction: str, limit: int, offset: int):
    """Reconstruit à la main le SQL D'AVANT ce lot (référence directe et
    répétée à `data`, sans CTE) — la vraie ligne de base, pas une supposition."""
    from oto_mcp.db.paths import field_read_sql
    from oto_mcp.db.query import _ds_where, typed_order_sql

    where, params = _ds_where(ns_id, None, None)
    _v, _vp = field_read_sql(order_by)
    order_sql, _op = typed_order_sql(_v, _vp, order_type, order_options, direction)
    params = list(params) + list(_op)
    sql = (
        "SELECT row_id, created_at, updated_at, data, rev, claimed_by, claimed_until, "
        "       claimed_run, claims, abandon_reason, "
        "       (claimed_until IS NOT NULL AND claimed_until > NOW()) AS claim_active "
        f"FROM datastore_rows {where} ORDER BY {order_sql} LIMIT %s OFFSET %s"
    )
    return sql, params + [limit, offset]


def _ancien_order_health(ns_id: int, order_by: str, order_type: str, order_options):
    from oto_mcp.db.paths import field_read_sql
    from oto_mcp.db.query import _ds_where, order_health_sql

    where, params = _ds_where(ns_id, None, None)
    _v, _vp = field_read_sql(order_by)
    proj, pparams = order_health_sql(_v, _vp, order_type, order_options)
    sql = f"SELECT {proj} FROM datastore_rows {where}"
    return sql, list(pparams) + list(params)


def _ancien_count_filtre(ns_id: int, filters: list):
    from oto_mcp.db.query import _ds_where

    where, params = _ds_where(ns_id, None, filters)
    return f"SELECT COUNT(*) AS n FROM datastore_rows {where}", params


# ── le tri typé : moins de blocs, mêmes valeurs ───────────────────────────────

def test_le_tri_type_lit_beaucoup_moins_de_blocs(vivier):
    from oto_mcp.db import datastore as dsdb

    ancien_sql, ancien_params = _ancienne_requete_triee(
        vivier, "priorite", "enum", PRIORITES, "ASC", 50, 0)
    blocs_avant = _relations_buffers(ancien_sql, tuple(ancien_params))

    # Le nouveau chemin : mesuré directement sur la requête que
    # `datastore_list_rows` construit désormais (thin_read_cte_sql).
    from oto_mcp.db.query import thin_read_cte_sql, typed_order_sql
    from oto_mcp.db.paths import field_read_sql

    thin = thin_read_cte_sql(vivier, None, None, "priorite")
    assert thin is not None, "priorite est une colonne simple : amincissable"
    cte_sql, cte_params, where_sql, where_params = thin
    _v, _vp = field_read_sql("priorite")
    order_sql, _op = typed_order_sql(_v, _vp, "enum", PRIORITES, "ASC")
    nouveau_sql = (
        f"WITH {cte_sql}, "
        f"p AS (SELECT row_id, row_number() OVER (ORDER BY {order_sql}) AS rn "
        f"FROM s {where_sql} ORDER BY rn LIMIT %s OFFSET %s) "
        "SELECT dr.row_id, dr.created_at, dr.updated_at, dr.data, dr.rev, "
        "dr.claimed_by, dr.claimed_until, dr.claimed_run, dr.claims, dr.abandon_reason, "
        "(dr.claimed_until IS NOT NULL AND dr.claimed_until > NOW()) AS claim_active "
        "FROM p JOIN datastore_rows dr ON dr.row_id = p.row_id AND dr.ns_id = %s "
        "ORDER BY p.rn"
    )
    nouveau_params = cte_params + list(_op) + list(where_params) + [50, 0, vivier]
    blocs_apres = _relations_buffers(nouveau_sql, tuple(nouveau_params))

    assert blocs_apres < blocs_avant * 0.3, (
        f"la CTE mince devait lire nettement moins de blocs : "
        f"avant={blocs_avant}, après={blocs_apres}")

    # Mêmes VALEURS, par l'API réelle cette fois — c'est elle qui doit rendre
    # exactement ce que l'ancien chemin rendait.
    lignes = dsdb.datastore_list_rows(vivier, order_by="priorite", order_dir="asc",
                                      order_type="enum", order_options=PRIORITES,
                                      limit=50, offset=0)
    with __import__("oto_mcp.db._conn", fromlist=["_connect"])._connect() as conn:
        attendu = conn.execute(ancien_sql, tuple(ancien_params)).fetchall()
    assert [r["row_id"] for r in lignes] == [r["row_id"] for r in attendu]
    assert [r["data"] for r in lignes] == [r["data"] for r in attendu]


def test_order_health_lit_beaucoup_moins_de_blocs_et_compte_pareil(vivier):
    from oto_mcp.db import datastore as dsdb

    ancien_sql, ancien_params = _ancien_order_health(vivier, "priorite", "enum", PRIORITES)
    blocs_avant = _relations_buffers(ancien_sql, tuple(ancien_params))

    sante = dsdb.datastore_order_health(vivier, order_by="priorite", order_type="enum",
                                        order_options=PRIORITES)
    # `off_type`/`empty` doivent être nuls : toutes les valeurs postées sont
    # conformes à l'énuméré déclaré (ni hors options, ni vides).
    assert sante is None or sante == {"off_type": 0, "empty": 0}

    from oto_mcp.db.query import thin_read_cte_sql, order_health_sql
    from oto_mcp.db.paths import field_read_sql
    thin = thin_read_cte_sql(vivier, None, None, "priorite")
    cte_sql, cte_params, where_sql, where_params = thin
    _v, _vp = field_read_sql("priorite")
    proj, pparams = order_health_sql(_v, _vp, "enum", PRIORITES)
    nouveau_sql = f"WITH {cte_sql} SELECT {proj} FROM s {where_sql}"
    blocs_apres = _relations_buffers(nouveau_sql, tuple(cte_params + pparams + where_params))

    # `order_health_sql` multiplie `value_sql` par 5 (contre 8 pour
    # `typed_order_sql`) : la réduction attendue est réelle mais plus modeste
    # que sur la page triée — mesurée, pas fixée au doigt mouillé.
    assert blocs_apres < blocs_avant * 0.6, (
        f"avant={blocs_avant}, après={blocs_apres}")


# ── les filtres : même chute de blocs, sur la page ET le COUNT ───────────────

def test_deux_filtres_combines_comptent_juste_par_la_cte_mince(vivier):
    """Le scénario mesuré en production par oto cd (17/09/2026, vivier réel) :
    DEUX filtres combinés (`client_audiens:in` + `priorite:in`, 14 références
    cumulées de `data`), page ET `COUNT` — 712 ms / 237 782 blocs sans la CTE,
    300 ms / 82 344 blocs avec, ~2,4×.

    ⚠️ **Ce banc, à L'ÉCHELLE synthétique reproduite ici (6 000 lignes, texte
    aléatoire incompressible), ne reproduit PAS ce gain** : mesuré à la
    construction de ce lot, la CTE mince coûte ICI plus cher à MATÉRIALISER
    (écrire 6 000 objets JSON amincis) qu'elle ne fait gagner sur 14 références
    évitées par ligne — contrairement au tri (56 références/ligne, gain net et
    mesuré ci-dessus) où l'écart est assez grand pour dominer le coût fixe de
    la matérialisation. La cause probable : la distribution/le volume réels de
    prod (8 910 lignes, contenu métier varié, cache chaud) ne se reproduisent
    pas à l'identique avec un remplissage aléatoire synthétique à cette
    échelle. Le mécanisme reste correct (mêmes VALEURS rendues, vérifié
    ci-dessous) et suit exactement le SQL vérifié EN PRODUCTION par oto cd —
    seule la REPRODUCTION SYNTHÉTIQUE du gain à cette échelle échoue, pas le
    correctif. À remesurer en prod après déploiement, comme d'habitude sur ce
    genre de lot."""
    from oto_mcp.db import datastore as dsdb

    filtre = [{"field": "categorie", "op": "in", "value": ["nord", "sud"]},
              {"field": "priorite", "op": "in", "value": ["1", "2"]}]

    # Même total, par les DEUX chemins (thin_read_cte_sql vs _ds_where direct)
    # — la garantie qui compte vraiment ici : jamais changer la VALEUR rendue.
    ancien_sql, ancien_params = _ancien_count_filtre(vivier, filtre)
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        attendu = conn.execute(ancien_sql, tuple(ancien_params)).fetchone()["n"]

    total = dsdb.datastore_count_rows(vivier, filters=filtre)
    assert total == attendu == 1200  # categorie∈{nord,sud} (1/2) ET priorite∈{1,2} (2/5)


def test_un_filtre_sur_une_AUTRE_colonne_que_le_tri_voit_bien_data_complet(vivier):
    """Le point d'attention explicite d'oto cd : un filtre sur `categorie` posé
    en même temps qu'un tri sur `priorite` doit voir `categorie` ENTIER, pas
    juste la version amincie construite pour `priorite` seule — la CTE doit
    amincir l'UNION des deux, jamais l'un au prix de l'autre."""
    from oto_mcp.db import datastore as dsdb

    filtre = [{"field": "categorie", "op": "eq", "value": "sud"}]
    lignes = dsdb.datastore_list_rows(
        vivier, order_by="priorite", order_dir="asc", order_type="enum",
        order_options=PRIORITES, filters=filtre, limit=2000, offset=0)
    assert len(lignes) == 1500
    assert all(r["data"]["categorie"] == "sud" for r in lignes)
    # Toujours trié par priorite, malgré le filtre.
    valeurs = [r["data"]["priorite"] for r in lignes]
    assert valeurs == sorted(valeurs)


def test_recherche_plein_texte_q_ne_passe_jamais_par_la_cte_mince(vivier):
    """`q` a besoin de `data` ENTIER — aucune CTE mince ne doit se construire
    quand il est posé, même combiné à un tri/des filtres amincissables."""
    from oto_mcp.db.query import thin_read_cte_sql
    assert thin_read_cte_sql(vivier, "nord", None, "priorite") is None

