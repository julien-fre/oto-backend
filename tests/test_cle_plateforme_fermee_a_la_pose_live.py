"""Une clé plateforme payante naît FERMÉE (revue de oto-backend#1179, D2).

`/api/admin/platform-keys` posait la ligne en `share_mode='open'` (défaut de la
colonne) avec un `share_down` vide, que `access.platform_grant` lit « ouvert à tous » :
entre la pose et le premier accord, n'importe quel compte sans clé à lui consommait la
clé payante, sans plafond. `credentials_store.set_platform_key` la ferme dès la pose
quand le connecteur n'a pas de palier gratuit (`platform_key_open=False`).
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


def test_une_cle_payante_ne_sert_personne_avant_l_accord_puis_l_org_accordee(base):
    from oto_mcp import credentials_store
    from oto_mcp.access.platform_grant import _platform_instance_usable

    credentials_store.set_platform_key("partenaire", "forager", "fg-raw-key", set_by="op")
    inst = _instance("forager")
    assert inst["share_mode"] == "closed"
    assert not _platform_instance_usable("quelconque", 77, inst)

    credentials_store.platform_grant("forager", "org:42", daily_quota=50)
    inst = _instance("forager")
    assert _platform_instance_usable("membre", 42, inst)
    assert not _platform_instance_usable("membre", 77, inst)


def test_une_rotation_laisse_fermee_une_cle_deja_accordee(base):
    from oto_mcp import credentials_store
    from oto_mcp.access.platform_grant import _platform_instance_usable

    credentials_store.set_platform_key("partenaire", "forager", "fg-1", set_by="op")
    credentials_store.platform_grant("forager", "org:42")
    credentials_store.set_platform_key("partenaire", "forager", "fg-2", set_by="op")
    inst = _instance("forager")
    assert inst["share_mode"] == "closed"
    assert _platform_instance_usable("membre", 42, inst)
    assert not _platform_instance_usable("membre", 77, inst)


def test_un_palier_gratuit_garde_sa_cle_ouverte(base):
    from oto_mcp import credentials_store, providers
    from oto_mcp.access.platform_grant import _platform_instance_usable

    assert providers.REGISTRY["serper"].platform_key_open
    credentials_store.set_platform_key("free", "serper", "sk-1", set_by="op")
    inst = _instance("serper")
    assert inst["share_mode"] == "open"
    assert _platform_instance_usable("quelconque", 77, inst)
