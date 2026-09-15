"""Le réglage « clé de modèle exigée » EN BASE : il se relit, et l'org l'emporte.

Une doublure ne prouve ni la table, ni la clé primaire, ni la précédence réelle entre
les deux portées. Patron de base éphémère repris de `test_runner_workers_db.py`.
"""
from __future__ import annotations

import os
import uuid

import pytest


def test_rien_de_pose_rien_d_exige(live):
    from oto_mcp.capabilities import _cle_exigee as CE
    from oto_mcp.db import connector_settings as store
    assert store.get_connector_setting("platform", "platform", "anthropic",
                                       CE.CLE_REGLAGE) is None
    assert CE.cle_exigee(7001, "anthropic") is False


def test_la_plateforme_exige_et_l_org_exemptee_ne_l_est_pas(live):
    from oto_mcp.capabilities import _cle_exigee as CE
    from oto_mcp.db import connector_settings as store
    store.set_connector_setting("platform", "platform", "anthropic", CE.CLE_REGLAGE, "true")
    store.set_connector_setting("org", "7002", "anthropic", CE.CLE_REGLAGE, "false")

    assert CE.cle_exigee(7001, "anthropic") is True, "la plateforme vaut pour tous"
    assert CE.cle_exigee(7002, "anthropic") is False, "l'org l'emporte"
    assert CE.cle_exigee(7001, "mistral") is False, "par fournisseur"


def test_une_org_sans_depot_est_listee_manquante(live):
    from oto_mcp.capabilities import _cle_exigee as CE
    assert CE.manquantes(7001) == ["anthropic"]
    assert CE.manquantes(7002) == [], "exemptée, rien ne manque"
