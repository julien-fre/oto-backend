"""Lot L5 — le SQL de la chaîne de grants, contre un VRAI PostgreSQL.

Ce que seul un vrai serveur peut dire, et que la suite statique ne prouverait pas :

- la **migration de boot** tourne dans le vrai `init_db()`, sur le vrai schéma, et
  rejouée elle est un **no-op** (l'idempotence n'est pas une intention, c'est un
  deuxième `init_db()`) ;
- l'**index de comptage est NON PARTIEL dans la base**, pas seulement dans le source :
  c'est la condition mesurée du banc L0 (0,035 ms contre 73,8), et la seule preuve qui
  vaille est `pg_indexes` ;
- la lecture de quota de D7 **somme les arêtes archivées comprises** — donc une
  révocation ne remet pas le compteur du client à zéro ;
- la FK `grant_counters → grants` **sans CASCADE** rend impossible d'effacer une
  consommation en supprimant son arête ;
- le CHECK de vocabulaire des contraintes **refuse vraiment** une clé inconnue.

`pg_dsn` (conftest) : `OTO_TEST_PG_DSN`, sinon un conteneur jetable, sinon skip.
"""
from __future__ import annotations

import uuid

import pytest


@pytest.fixture(scope="module")
def live(pg_dsn):
    """Une base JETABLE, le VRAI `init_db()`, le vrai pool. Rend la fonction
    `init_db` pour pouvoir la REJOUER (c'est la moitié des tests d'ici)."""
    import os

    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    name = "oto_l5_" + uuid.uuid4().hex[:8]
    root = psycopg.connect(pg_dsn, autocommit=True)
    root.execute(f'CREATE DATABASE "{name}"')
    dsn = pg_dsn.rsplit("/", 1)[0] + "/" + name

    previous_url, previous_pool = os.environ.get("DATABASE_URL"), dbconn._pool
    os.environ["DATABASE_URL"] = dsn
    dbconn._pool = None
    try:
        from oto_mcp.db import init_db
        init_db()
        yield init_db
    finally:
        if dbconn._pool is not None:
            dbconn._pool.close()
        dbconn._pool = previous_pool
        if previous_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous_url
        root.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        root.close()


def _rows(sql, params=()):
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def _exec(sql, params=()):
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        conn.execute(sql, params)


def _seed_prod_shaped_platform_key():
    """La ligne du coffre TELLE QU'ELLE EST EN PROD pour fullenrich (relevé du
    12/08/2026) : free-tier ouverte, deux quotas nominatifs dans `rate_limit_by`,
    `share_down` VIDE — c'est justement pourquoi la migration doit lire les deux
    sources de scopes et pas seulement `share_down`."""
    _exec("DELETE FROM grant_counters")
    _exec("DELETE FROM grants")
    _exec("DELETE FROM connector_credentials WHERE connector = 'fullenrich'")
    _exec("INSERT INTO connector_credentials (entity_type, entity_id, connector, "
          "account, secret_enc, share_mode, share_down, meta) VALUES "
          "('platform', 'env', 'fullenrich', '', 'x', 'open', '[]'::jsonb, %s::jsonb)",
          ('{"rate_limit": 5, "rate_limit_by": {"user:granted-sub": 200, '
           '"org:42": 1000, "group:7": 9}}',))


def _edges():
    return _rows("SELECT * FROM grants ORDER BY grantee_kind, grantee_id")


# ── La migration de boot ───────────────────────────────────────────────────────

def test_migration_maps_every_existing_grant_to_an_edge(live):
    _seed_prod_shaped_platform_key()
    live()                                    # rejoue le VRAI init_db
    edges = _edges()
    assert [(e["grantee_kind"], e["grantee_id"]) for e in edges] == [
        ("org", "42"), ("user", "granted-sub")], edges
    for e in edges:
        assert e["resource_kind"] == "connector_instance"
        assert e["resource_id"] == "platform:fullenrich:env"
        assert (e["grantor_kind"], e["grantor_id"]) == ("platform", "platform")
        assert e["parent_id"] is None, "chaîne de profondeur 1 : racine du propriétaire"
        assert e["source"] == "manual", (
            "posés par un humain : le réconciliateur billing (L9) ne diffe QUE ses "
            "propres grants (0053-D6) et ne doit jamais reprendre ceux-ci")
        assert e["created_by"] == "migration:l5"
        assert e["revoked_at"] is None
    by = {e["grantee_id"]: e["constraints"] for e in edges}
    assert by["granted-sub"] == {"quota": 200}, "quota nominal de rate_limit_by"
    assert by["42"] == {"quota": 1000}


def test_migration_skips_scopes_the_old_path_cannot_resolve(live):
    """`group:7` est dans `rate_limit_by` mais l'ancien chemin ne le résout JAMAIS
    sur une clé plateforme (`_platform_grantee_scope` ne connaît que user et org).
    Le migrer inventerait un accès qui n'existe pas."""
    _seed_prod_shaped_platform_key()
    live()
    assert not [e for e in _edges() if e["grantee_kind"] == "group"]


def test_replaying_the_migration_is_a_no_op(live):
    _seed_prod_shaped_platform_key()
    live()
    before = _edges()
    live()
    live()
    assert _edges() == before


def test_replay_never_resurrects_a_revoked_edge(live):
    """Le piège de l'idempotence naïve : chercher les arêtes VIVANTES pour décider
    d'insérer rendrait, à chaque boot, un accès retiré à la main entre-temps."""
    _seed_prod_shaped_platform_key()
    live()
    _exec("UPDATE grants SET revoked_at = NOW() WHERE grantee_id = 'granted-sub'")
    live()
    rows = _rows("SELECT * FROM grants WHERE grantee_id = 'granted-sub'")
    assert len(rows) == 1 and rows[0]["revoked_at"] is not None


def test_migration_leaves_the_vault_row_untouched(live):
    """La réversibilité tient à ça : rien de l'existant n'est supprimé ni réécrit,
    donc retirer les arêtes ramène EXACTEMENT l'ancien comportement."""
    _seed_prod_shaped_platform_key()
    before = _rows("SELECT * FROM connector_credentials WHERE connector = 'fullenrich'")
    live()
    assert _rows("SELECT * FROM connector_credentials WHERE connector = 'fullenrich'") == before


def test_migration_seeds_a_wave2_connector(live):
    """Vague 2 (23/08) : une clé plateforme `serper` avec des grants produit ses
    arêtes au boot — `share_down` ∪ `rate_limit_by`, quota compris. C'est l'ancien
    test différentiel INVERSÉ : serper est basculé, le seed le couvre."""
    _seed_prod_shaped_platform_key()
    _exec("DELETE FROM connector_credentials WHERE connector = 'serper'")
    _exec("INSERT INTO connector_credentials (entity_type, entity_id, connector, "
          "account, secret_enc, share_mode, share_down, meta) VALUES "
          "('platform', 'env', 'serper', '', 'x', 'open', '[\"user:a\"]'::jsonb, "
          "'{\"rate_limit_by\": {\"user:a\": 50}}'::jsonb)")
    live()
    arete = [e for e in _edges() if e["resource_id"] == "platform:serper:env"]
    assert len(arete) == 1
    assert (arete[0]["grantee_kind"], arete[0]["grantee_id"]) == ("user", "a")
    assert arete[0]["constraints"].get("quota") == 50


def test_migration_ignores_an_unchained_connector(live):
    """Test différentiel, côté données : une clé plateforme `unipile` (hors chaîne —
    son mode plateforme est gouverné par option comp + comptes opérés) ne produit
    AUCUNE arête, quels que soient ses grants."""
    _seed_prod_shaped_platform_key()
    _exec("DELETE FROM connector_credentials WHERE connector = 'unipile'")
    _exec("INSERT INTO connector_credentials (entity_type, entity_id, connector, "
          "account, secret_enc, share_mode, share_down, meta) VALUES "
          "('platform', 'env', 'unipile', '', 'x', 'open', '[\"user:a\"]'::jsonb, "
          "'{\"rate_limit_by\": {\"user:a\": 50}}'::jsonb)")
    live()
    assert not [e for e in _edges() if "unipile" in e["resource_id"]]


# ── Le comptage (0053-D7) ──────────────────────────────────────────────────────

def test_quota_read_sums_archived_edges_too(live):
    """D7 : « un quota se lit en SOMMANT les arêtes » de la même (instance,
    bénéficiaire, fenêtre), **archivées comprises** — sinon une bascule de plan
    (remplacement de grant) remet la consommation du client à zéro sans que personne
    ne le voie, et multiplier les chemins d'accès doublerait le quota."""
    from oto_mcp.db import grants as db_grants

    _seed_prod_shaped_platform_key()
    live()
    ref = "platform:fullenrich:env"
    old = _rows("SELECT id FROM grants WHERE grantee_id = 'granted-sub'")[0]["id"]
    db_grants.bump_counter(old, 3)
    _exec("UPDATE grants SET revoked_at = NOW() WHERE id = %s", (old,))
    new = db_grants.insert_grant(
        resource_id=ref, grantor_kind="platform", grantor_id="platform",
        grantee_kind="user", grantee_id="granted-sub", constraints={"quota": 200})
    db_grants.bump_counter(new, 2)
    assert db_grants.counter_sum_today(ref, "user", "granted-sub") == 5


def test_bump_counter_is_a_single_upsert(live):
    from oto_mcp.db import grants as db_grants

    _seed_prod_shaped_platform_key()
    live()
    gid = _rows("SELECT id FROM grants WHERE grantee_id = 'granted-sub'")[0]["id"]
    for _ in range(4):
        db_grants.bump_counter(gid)
    rows = _rows("SELECT calls FROM grant_counters WHERE grant_id = %s", (gid,))
    assert rows == [{"calls": 4}], "une ligne par (arête, jour), pas une par appel"


def test_a_counted_edge_cannot_be_deleted(live):
    """Pas de `ON DELETE CASCADE`, et c'est délibéré (0053-D7 : un grant s'archive,
    il ne se supprime jamais). La règle est tenue par la BASE, pas par la mémoire du
    lecteur : supprimer une arête déjà comptée échoue au lieu d'effacer l'historique
    de consommation en silence. Conséquence opérationnelle : un rollback du lot
    supprime les compteurs AVANT les arêtes."""
    import psycopg
    from oto_mcp.db import grants as db_grants

    _seed_prod_shaped_platform_key()
    live()
    gid = _rows("SELECT id FROM grants WHERE grantee_id = 'granted-sub'")[0]["id"]
    db_grants.bump_counter(gid)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        _exec("DELETE FROM grants WHERE id = %s", (gid,))


def test_removing_the_edges_restores_the_old_path(live):
    """La réversibilité, exercée : compteurs puis arêtes supprimés ⟹ la chaîne
    redevient MUETTE et l'ancien chemin (la ligne du coffre, intacte) répond seul."""
    from oto_mcp import access, grants_chain

    _seed_prod_shaped_platform_key()
    live()
    assert grants_chain.platform_rung("granted-sub", "fullenrich", None) is not None
    _exec("DELETE FROM grant_counters")
    _exec("DELETE FROM grants")
    assert grants_chain.platform_rung("granted-sub", "fullenrich", None) is None
    assert access._platform_grant_meta("granted-sub", "fullenrich", None) == {
        "label": "env", "daily_quota": 200}


# ── Les conditions du socle, vérifiées DANS la base ────────────────────────────

def test_counting_index_is_not_partial_in_the_database(live):
    """Le test statique lit le source ; celui-ci lit `pg_indexes`. Les deux, parce
    qu'une base qui a vécu peut porter un index qui ne ressemble plus au DDL."""
    defs = {r["indexname"]: r["indexdef"]
            for r in _rows("SELECT indexname, indexdef FROM pg_indexes "
                           "WHERE tablename = 'grants'")}
    counting = defs["idx_grants_resource_grantee"]
    assert "WHERE" not in counting.upper(), (
        f"index de comptage devenu PARTIEL en base : {counting}. Mesuré au banc L0 : "
        "73,8 ms sans lui contre 0,035 ms avec, sur le chemin chaud de chaque appel "
        "compté d'un serveur mono-loop.")
    assert "(resource_id, grantee_kind, grantee_id)" in counting
    assert "WHERE (revoked_at IS NULL)" in defs["idx_grants_grantee"]


def test_constraint_vocabulary_check_really_refuses(live):
    """Le vocabulaire fermé de 0053-D4 n'est pas une convention : c'est un CHECK."""
    import psycopg
    from oto_mcp.db import grants as db_grants

    with pytest.raises(psycopg.errors.CheckViolation):
        db_grants.insert_grant(
            resource_id="platform:fullenrich:env", grantor_kind="platform",
            grantor_id="platform", grantee_kind="user", grantee_id="x",
            constraints={"perimetre": ["fullenrich_result"]})
