"""La révision `20261111_1400_ouvertures_par_run` pose `idx_tool_calls_run_start_run`, et
`project_run_stats` rend la MÊME réponse avec ou sans lui.

Un index CONCURRENTLY que le démarrage pose aussi (même déclaration, `OUVERTURE_PAR_RUN`) :
la révision doit le poser VALIDE sur une base d'avant, ne rien faire sur une base que le
démarrage a déjà servie, et le retirer au retour arrière. La lecture de l'inertie choisit
sa forme selon sa présence (sonde par run servie par l'index, sinon la semi-jointure
d'avant) : les deux formes doivent compter la même chose. Joué sur une vraie base.
"""
from __future__ import annotations

from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parent.parent
_PRECEDENTE = "20261111_0920_jetons_echeance"
_REVISION = "20261111_1400_ouvertures_par_run"


def _alembic():
    from alembic.config import Config
    cfg = Config(str(RACINE / "alembic.ini"))
    cfg.set_main_option("script_location", str(RACINE / "oto_mcp" / "db" / "migrations"))
    return cfg


def _validite(dsn):
    """`None` si l'index est absent, sinon sa validité."""
    import psycopg

    from oto_mcp.db.index_releve import OUVERTURE_PAR_RUN
    with psycopg.connect(dsn) as c:
        return c.execute(OUVERTURE_PAR_RUN.sql_validite).fetchone()


def test_la_revision_est_declaree_au_registre_des_index_concurrents():
    from oto_mcp.db import index_concurrent as ic
    from oto_mcp.db.index_releve import OUVERTURE_PAR_RUN, REVISION_OUVERTURE_PAR_RUN

    assert REVISION_OUVERTURE_PAR_RUN == _REVISION and len(_REVISION) <= 32
    assert ic.index_de_revision(_REVISION) == (OUVERTURE_PAR_RUN,)
    assert f"index-concurrents {_REVISION}" in OUVERTURE_PAR_RUN.procedure


def test_le_predicat_de_l_index_est_celui_des_ouvertures():
    """Un index partiel ne sert que la requête qui IMPLIQUE son prédicat."""
    from oto_mcp.db import usage
    from oto_mcp.db.index_releve import OUVERTURE_PAR_RUN, PREDICAT_OUVERTURE

    assert OUVERTURE_PAR_RUN.forme == f"(run_id) WHERE {PREDICAT_OUVERTURE}"
    assert usage._PREDICAT_OUVERTURE_S == "s." + PREDICAT_OUVERTURE.replace(" AND ", " AND s.")


@pytest.fixture(scope="module")
def base_d_avant(live, pg_module_dsn):
    """Une base bootée, ramenée à l'état d'avant : index retiré, estampillée à la
    révision précédente."""
    import psycopg
    from alembic import command

    with psycopg.connect(pg_module_dsn, autocommit=True) as c:
        c.execute("DROP INDEX idx_tool_calls_run_start_run")
    command.stamp(_alembic(), _PRECEDENTE)
    return pg_module_dsn


def test_la_revision_pose_l_index_valide_et_le_retour_arriere_le_retire(base_d_avant):
    from alembic import command

    assert _validite(base_d_avant) is None
    command.upgrade(_alembic(), _REVISION)
    assert _validite(base_d_avant) == (True,)
    command.downgrade(_alembic(), _PRECEDENTE)
    assert _validite(base_d_avant) is None
    command.upgrade(_alembic(), _REVISION)
    assert _validite(base_d_avant) == (True,)


def test_rejouee_sur_une_base_que_le_demarrage_a_servie_elle_ne_fait_rien(base_d_avant):
    """Ordre indifférent avec le code : le démarrage a pu poser l'index avant."""
    from alembic import command

    command.upgrade(_alembic(), _REVISION)
    command.stamp(_alembic(), _PRECEDENTE)
    command.upgrade(_alembic(), _REVISION)    # l'index est là, valide : rien à faire
    assert _validite(base_d_avant) == (True,)


def _journal(conn, run_id: str, project_id, *, doctrine=None, ouvertures=1, autres=3,
             il_y_a_h: int = 1):
    conn.execute("INSERT INTO runs (run_id, sub, org_id, project_id, label, lignes_reservees, "
                 "started_at) VALUES (%s, 'u1', 35, %s, 'x', 0, "
                 "now() - make_interval(hours => %s))", (run_id, project_id, il_y_a_h))
    args = {"doctrine": doctrine} if doctrine else {"label": "sans procédure"}
    for _ in range(ouvertures):
        conn.execute("INSERT INTO tool_calls (server, sub, tool, args, ok, duration_ms, run_id, "
                     "kind) VALUES ('oto', 'u1', 'run_start', %s, true, 1, %s, 'mcp')",
                     (__import__("json").dumps(args), run_id))
    for _ in range(autres):
        conn.execute("INSERT INTO tool_calls (server, sub, tool, args, ok, duration_ms, run_id, "
                     "kind) VALUES ('oto', 'u1', 'data_rows', '{}', true, 1, %s, 'mcp')",
                     (run_id,))


def test_les_deux_formes_de_l_inertie_rendent_la_meme_reponse(live, monkeypatch):
    """Avec l'index : la sonde par run, et l'index la sert. Sans lui : la semi-jointure
    d'avant. Mêmes runs comptés (borne des runs récents comprise, ouverture sans
    procédure, run ouvert deux fois, run d'un autre projet, index sans ouverture), mêmes
    procédures."""
    from oto_mcp.db import usage
    from oto_mcp.db._conn import _connect
    from oto_mcp.db.index_releve import OUVERTURE_PAR_RUN

    monkeypatch.setattr(usage, "PROJET_RUNS_RECENTS", 4)
    with _connect() as c:
        _journal(c, "vieux", 77, doctrine="ancienne", il_y_a_h=50)     # hors borne
        _journal(c, "a", 77, doctrine="prospection", il_y_a_h=5)
        _journal(c, "b", 77, doctrine="relance", ouvertures=2, il_y_a_h=4)
        _journal(c, "c", 77, il_y_a_h=3)                                # sans procédure
        _journal(c, "d", 77, doctrine="prospection", ouvertures=0, il_y_a_h=2)  # index seul
        _journal(c, "voisin", 78, doctrine="autre", il_y_a_h=1)
        c.commit()

    def lire():
        usage.oublier_ouverture_par_run()
        out = usage.project_run_stats(77)
        return out["runs"], sorted(out["doctrines"]), usage._ouverture_par_run_prete()

    assert lire() == (4, ["prospection", "relance"], True)
    with _connect() as c:
        plan = "\n".join(r["QUERY PLAN"] for r in c.execute(
            "EXPLAIN " + usage.sql_project_run_stats(par_run=True), (77, 4)).fetchall())
        assert OUVERTURE_PAR_RUN.nom in plan, plan
        c.execute(f"DROP INDEX {OUVERTURE_PAR_RUN.nom}")
        c.commit()
    try:
        assert lire() == (4, ["prospection", "relance"], False)
    finally:
        with _connect() as c:
            c.execute(OUVERTURE_PAR_RUN.ddl_demarrage)
            c.commit()
        usage.oublier_ouverture_par_run()
