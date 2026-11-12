"""La révision `20261112_1200_connecteur_api` suit le renommage du connecteur Custom API
(`http` → `api`) dans les références enregistrées.

Les lignes qui nomment le connecteur changent de nom, le reste tel quel ; le kit d'une org
garde sa place dans la liste ; les listes d'outils reçoivent le même outil sous son nouveau
nom, qu'elles autorisent ou masquent ; un travail terminé n'est pas réécrit. Un doublon de
même sens fusionne, un doublon de sens différent arrête la révision. Le retour arrière
rétablit l'état d'avant. Joué sur une vraie base, estampillée à la révision d'avant.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parent.parent
_PRECEDENTE = "20261111_1930_dataforseo_decrit"
_REVISION = "20261112_1200_connecteur_api"


def _alembic():
    from alembic.config import Config
    cfg = Config(str(RACINE / "alembic.ini"))
    cfg.set_main_option("script_location", str(RACINE / "oto_mcp" / "db" / "migrations"))
    return cfg


def _module():
    chemin = RACINE / "oto_mcp" / "db" / "migrations" / "versions" / f"{_REVISION}.py"
    spec = importlib.util.spec_from_file_location("revision_connecteur_api", chemin)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_chaque_outil_garde_son_suffixe_des_deux_cotes():
    autorise, masque = _module().correspondances()
    attendu = {"http": ("api",), "http_act": ("api_act",), "http_get": ("api_get",),
               "http_post": ("api_post",), "http_doc": ("api_doc",)}
    assert autorise == masque == attendu
    descente, _ = _module().correspondances("descente")
    assert descente == {v[0]: (k,) for k, v in attendu.items()}


def _semer(c):
    c.execute("INSERT INTO orgs (id, name, default_connectors) VALUES "
              "(95001, 'o1', ARRAY['serper','http','fr']), "
              "(95002, 'o2', ARRAY['http','serper','api'])")
    c.execute("INSERT INTO user_selected_connectors (sub, org_id, connector, state) VALUES "
              "('u1', 95001, 'http', 'paused'), ('u1', 95001, 'serper', 'active'), "
              "('u2', 95001, 'http', 'active'), ('u2', 95001, 'api', 'active')")
    c.execute("INSERT INTO connector_selection_removed (sub, org_id, connector) VALUES "
              "('u3', 95001, 'http')")
    c.execute("INSERT INTO connector_availability (scope_type, scope_id, connector, enabled) "
              "VALUES ('org', '95001', 'http', true)")
    c.execute("INSERT INTO connector_settings (scope_type, scope_id, connector, key, value) "
              "VALUES ('org', '95001', 'http', 'k', 'v')")
    c.execute("INSERT INTO projects (id, owner_id, name, mcp_tools) VALUES "
              "(95001, 'u1', 'p', ARRAY['http_get','serper_search','http_act'])")
    c.execute("INSERT INTO runner_fleets (id, org_id, sub, label, procedure, tools) "
              "VALUES (95001, 95001, 'u1', 'f', 'p', %s::jsonb)",
              (json.dumps(["http", "fr_get"]),))
    for jid, statut in ((95001, "pending"), (95002, "failed")):
        c.execute("INSERT INTO runner_jobs (id, org_id, kind, status, payload) VALUES "
                  "(%s, 95001, 'start', %s, %s::jsonb)",
                  (jid, statut, json.dumps({"tools": ["http_post"], "procedure": "p"})))
    c.execute("INSERT INTO user_disabled_tools (sub, org_id, tool_name) VALUES "
              "('u1', 0, 'http_doc')")


@pytest.fixture(scope="module")
def base_montee(live, pg_module_dsn):
    import psycopg
    from alembic import command

    command.stamp(_alembic(), _PRECEDENTE)
    with psycopg.connect(pg_module_dsn, autocommit=True) as c:
        _semer(c)
    command.upgrade(_alembic(), _REVISION)
    return pg_module_dsn


def _lire(dsn, sql, *args):
    import psycopg
    with psycopg.connect(dsn) as c:
        return c.execute(sql, args).fetchall()


def test_les_lignes_du_connecteur_changent_de_nom(base_montee):
    assert _lire(base_montee, "SELECT sub, connector, state FROM user_selected_connectors "
                              "WHERE org_id = 95001 ORDER BY sub, connector") == [
        ("u1", "api", "paused"), ("u1", "serper", "active"), ("u2", "api", "active")]
    for table in ("connector_selection_removed", "connector_availability",
                  "connector_settings"):
        assert _lire(base_montee, f"SELECT DISTINCT connector FROM {table} "
                                  "WHERE connector IN ('http', 'api')") == [("api",)], table


def test_le_kit_garde_sa_place_sans_doublon(base_montee):
    assert _lire(base_montee, "SELECT id, default_connectors FROM orgs "
                              "WHERE id IN (95001, 95002) ORDER BY id") == [
        (95001, ["serper", "api", "fr"]), (95002, ["api", "serper"])]


def test_les_listes_d_outils_suivent(base_montee):
    assert _lire(base_montee, "SELECT mcp_tools FROM projects WHERE id = 95001") == [
        (["api_get", "serper_search", "api_act"],)]
    assert _lire(base_montee, "SELECT tools FROM runner_fleets WHERE id = 95001") == [
        (["api", "fr_get"],)]
    assert _lire(base_montee, "SELECT tool_name FROM user_disabled_tools "
                              "WHERE sub = 'u1' AND org_id = 0") == [("api_doc",)]


def test_un_travail_termine_n_est_pas_reecrit(base_montee):
    assert _lire(base_montee, "SELECT id, payload->'tools' FROM runner_jobs "
                              "WHERE id IN (95001, 95002) ORDER BY id") == [
        (95001, ["api_post"]), (95002, ["http_post"])]


def test_le_retour_arriere_retablit_les_noms(base_montee):
    from alembic import command

    command.downgrade(_alembic(), _PRECEDENTE)
    try:
        assert _lire(base_montee, "SELECT sub, connector, state FROM "
                                  "user_selected_connectors WHERE org_id = 95001 "
                                  "ORDER BY sub, connector") == [
            ("u1", "http", "paused"), ("u1", "serper", "active"), ("u2", "http", "active")]
        assert _lire(base_montee, "SELECT default_connectors FROM orgs WHERE id = 95001") \
            == [(["serper", "http", "fr"],)]
        assert _lire(base_montee, "SELECT mcp_tools FROM projects WHERE id = 95001") == [
            (["http_get", "serper_search", "http_act"],)]
        assert _lire(base_montee, "SELECT tool_name FROM user_disabled_tools "
                                  "WHERE sub = 'u1' AND org_id = 0") == [("http_doc",)]
    finally:
        command.upgrade(_alembic(), _REVISION)


def test_un_doublon_de_sens_different_arrete_la_revision(base_montee):
    import psycopg
    from alembic import command

    command.downgrade(_alembic(), _PRECEDENTE)
    with psycopg.connect(base_montee, autocommit=True) as c:
        c.execute("INSERT INTO user_selected_connectors (sub, org_id, connector, state) "
                  "VALUES ('u1', 95001, 'api', 'active')")
    try:
        with pytest.raises(Exception, match="user_selected_connectors.*sens différent"):
            command.upgrade(_alembic(), _REVISION)
        assert _lire(base_montee, "SELECT connector FROM connector_availability "
                                  "WHERE scope_id = '95001'") == [("http",)], \
            "rien n'est écrit : la révision est annulée en entier"
    finally:
        with psycopg.connect(base_montee, autocommit=True) as c:
            c.execute("DELETE FROM user_selected_connectors WHERE sub = 'u1' "
                      "AND org_id = 95001 AND connector = 'api'")
        command.upgrade(_alembic(), _REVISION)
