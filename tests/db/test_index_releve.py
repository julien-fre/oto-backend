"""`idx_tool_calls_org_tool_ok` : construire soi-même seulement sur une petite table.

oto-backend#1145. Mesuré en production : 172 s de construction pour environ 12 M
lignes, au-delà des 120 s de la fenêtre de démarrage. Le verdict partagé par la
révision 0032 et le démarrage (`oto_mcp/db/index_releve.py`) refuse donc, en le
NOMMANT, de construire sur une grosse table, et de prendre un index invalide pour fait.
"""
from __future__ import annotations

import pytest

from oto_mcp.db import index_releve as ir


def _faux(*, valide, trop_grosse=False):
    vus: list[str] = []

    def scalaire(sql: str):
        vus.append(sql)
        if sql == ir.SQL_VALIDITE:
            return valide
        assert sql == ir.sql_table_trop_grosse(ir.CONSTRUCTION_MAX_LIGNES)
        return trop_grosse
    return scalaire, vus


def test_absent_sur_une_petite_table_se_construit():
    scalaire, _ = _faux(valide=None, trop_grosse=False)
    assert ir.a_construire(scalaire) is True


def test_absent_sur_une_grosse_table_renvoie_au_geste_manuel():
    scalaire, _ = _faux(valide=None, trop_grosse=True)
    with pytest.raises(ir.ConstructionManuelleRequise) as e:
        ir.a_construire(scalaire)
    assert "§5.1" in str(e.value) and "CONCURRENTLY" in str(e.value)


def test_invalide_n_est_ni_pris_pour_fait_ni_reconstruit():
    scalaire, vus = _faux(valide=False)
    with pytest.raises(ir.IndexInvalide) as e:
        ir.a_construire(scalaire)
    assert "DROP INDEX CONCURRENTLY" in str(e.value)
    # La taille n'est même pas lue : il n'y a rien à construire ici.
    assert vus == [ir.SQL_VALIDITE]


def test_valide_ne_fait_rien():
    scalaire, vus = _faux(valide=True)
    assert ir.a_construire(scalaire) is False
    assert vus == [ir.SQL_VALIDITE]


def test_le_verdict_contre_une_vraie_base(live):
    """Le SQL réel : l'index que le démarrage a posé sur la base neuve est valide ; une
    fois retiré, il est « à construire » sous le seuil et refusé au-dessus."""
    from oto_mcp.db._conn import _connect

    with _connect() as conn:
        scalaire = ir.scalaire_de(conn)
        assert scalaire(ir.SQL_VALIDITE) is True
        assert ir.a_construire(scalaire) is False

        conn.execute(f"DROP INDEX {ir.INDEX}")
        conn.execute("INSERT INTO tool_calls (tool) SELECT 'essai' FROM generate_series(1, 3)")
        assert ir.a_construire(scalaire) is True
        with pytest.raises(ir.ConstructionManuelleRequise):
            ir.a_construire(scalaire, max_lignes=1)
        conn.rollback()
