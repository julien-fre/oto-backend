"""Révoquer sur un connecteur CHAÎNÉ coupe le connecteur, pas une de ses clés.

`grants_chain.revoke` sans libellé n'archivait que les arêtes de la clé la plus récente.
`platform_rung` parcourt toutes les clés du connecteur et sert la première arête
vivante : l'arête d'une AUTRE clé servait donc l'appelant révoqué au tour suivant.
Même sémantique que le chemin non chaîné (`_legacy_platform_grant_meta`) : un refus
vaut pour le connecteur entier, et une clé posée après la révocation ne la lève pas.
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


def test_revoquer_coupe_l_arete_vivante_d_une_autre_cle(base):
    from oto_mcp import credentials_store, grants_chain
    from oto_mcp.access.platform_grant import _platform_grant_meta

    assert grants_chain.is_chained("serper")
    credentials_store.set_platform_key("ancienne", "serper", "sk-1", set_by="op")
    credentials_store.platform_grant("serper", "org:42", label="ancienne")
    credentials_store.set_platform_key("recente", "serper", "sk-2", set_by="op")
    assert _platform_grant_meta("membre", "serper", 42)["label"] == "ancienne"

    credentials_store.platform_revoke("serper", "org:42")

    assert _platform_grant_meta("membre", "serper", 42) is None


def test_une_cle_posee_apres_la_revocation_ne_la_leve_pas(base):
    from oto_mcp import credentials_store
    from oto_mcp.access.platform_grant import _platform_grant_meta

    credentials_store.set_platform_key("ancienne", "serper", "sk-1", set_by="op")
    credentials_store.platform_grant("serper", "org:42", label="ancienne")
    credentials_store.platform_revoke("serper", "org:42")
    credentials_store.set_platform_key("neuve", "serper", "sk-3", set_by="op")

    assert _platform_grant_meta("membre", "serper", 42) is None

    credentials_store.platform_grant("serper", "org:42")
    assert _platform_grant_meta("membre", "serper", 42) is not None


def test_un_accord_individuel_ne_rouvre_pas_la_revocation_de_l_org(base):
    """La révocation prime sur tout accord, quel que soit le grain (comme
    `_platform_denied`) ; seul un grant sur le scope révoqué la lève."""
    from oto_mcp import credentials_store
    from oto_mcp.access.platform_grant import _platform_grant_meta

    credentials_store.set_platform_key("commune", "serper", "sk-1", set_by="op")
    credentials_store.platform_grant("serper", "user:membre", daily_quota=500)
    credentials_store.platform_revoke("serper", "org:42")

    assert _platform_grant_meta("membre", "serper", 42) is None
    assert _platform_grant_meta("membre", "serper", 77) == {
        "label": "commune", "daily_quota": 500,
        "edge_counter": "platform:serper:commune"}   # le compteur du refus (R6)

    credentials_store.platform_grant("serper", "org:42")
    assert _platform_grant_meta("membre", "serper", 42) is not None
