"""Une clé plateforme naît OUVERTE à toutes les orgs, palier gratuit ou non.

Poser une clé plateforme depuis la console admin, c'est la partager : l'usage se paie
par la facturation de l'instance, pas par une ligne fermée. `set_platform_key` ne ferme
plus la ligne d'un connecteur sans palier gratuit (`platform_key_open=False`) ; seul un
accord (`platform_grant`) la restreint à ses bénéficiaires, et une rotation garde ces
accords. Une ligne posée fermée par le passé, sans aucun accord, se rouvre à la pose.
"""
from __future__ import annotations

import os

import pytest

from _base_jetable import base_jetable


@pytest.fixture
def base(pg_dsn, monkeypatch):
    monkeypatch.setenv("OTO_MCP_MASTER_KEY", os.urandom(32).hex())
    with base_jetable(pg_dsn):
        from oto_mcp.db import init_db
        init_db()
        yield


def _instance(provider: str) -> dict:
    from oto_mcp import credentials_store

    inst, = credentials_store.list_platform_instances(provider)
    return inst


def test_une_cle_payante_sert_toutes_les_orgs_des_la_pose(base):
    from oto_mcp import credentials_store, providers
    from oto_mcp.access.platform_grant import _platform_instance_usable

    assert not providers.REGISTRY["forager"].platform_key_open
    credentials_store.set_platform_key("partenaire", "forager", "fg-raw-key", set_by="op")
    inst = _instance("forager")
    assert inst["share_mode"] == "open"
    assert _platform_instance_usable("quelconque", 77, inst)


def test_un_accord_restreint_la_cle_a_ses_beneficiaires(base):
    from oto_mcp import credentials_store
    from oto_mcp.access.platform_grant import _platform_instance_usable

    credentials_store.set_platform_key("partenaire", "forager", "fg-raw-key", set_by="op")
    credentials_store.platform_grant("forager", "org:42", daily_quota=50)
    inst = _instance("forager")
    assert _platform_instance_usable("membre", 42, inst)
    assert not _platform_instance_usable("membre", 77, inst)


def test_une_rotation_garde_les_accords(base):
    from oto_mcp import credentials_store
    from oto_mcp.access.platform_grant import _platform_instance_usable

    credentials_store.set_platform_key("partenaire", "forager", "fg-1", set_by="op")
    credentials_store.platform_grant("forager", "org:42")
    credentials_store.set_platform_key("partenaire", "forager", "fg-2", set_by="op")
    inst = _instance("forager")
    assert inst["share_mode"] == "closed"
    assert _platform_instance_usable("membre", 42, inst)
    assert not _platform_instance_usable("membre", 77, inst)


def test_une_cle_posee_fermee_sans_accord_se_rouvre_a_la_pose(base):
    from oto_mcp import credentials_store
    from oto_mcp.access.platform_grant import _platform_instance_usable

    credentials_store.set_platform_key("partenaire", "forager", "fg-1", set_by="op")
    with credentials_store._connect() as conn:
        conn.execute(
            "UPDATE connector_credentials SET share_mode = 'closed' "
            "WHERE entity_type = %s AND connector = 'forager'",
            (credentials_store.PLATFORM,))
    assert not _platform_instance_usable("quelconque", 77, _instance("forager"))

    credentials_store.set_platform_key("partenaire", "forager", "fg-2", set_by="op")
    inst = _instance("forager")
    assert inst["share_mode"] == "open"
    assert _platform_instance_usable("quelconque", 77, inst)


def test_un_palier_gratuit_garde_sa_cle_ouverte(base):
    from oto_mcp import credentials_store, providers
    from oto_mcp.access.platform_grant import _platform_instance_usable

    assert providers.REGISTRY["serper"].platform_key_open
    credentials_store.set_platform_key("free", "serper", "sk-1", set_by="op")
    inst = _instance("serper")
    assert inst["share_mode"] == "open"
    assert _platform_instance_usable("quelconque", 77, inst)
