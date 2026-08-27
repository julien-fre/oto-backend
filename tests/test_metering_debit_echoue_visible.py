"""B10 de l'inventaire des silences (27/08) : un débit de grant raté se VOIT.

Le fail-open est juste — un compteur qui tombe ne doit pas casser un appel qui a
réussi. C'est le NIVEAU de log qui en faisait un silence : `logger.debug`, invisible
en prod. Résultat : la consommation dépasse le grant plateforme sans trace
exploitable, et « le quota n'a pas bougé » n'est imputable à rien.

On teste le SYSTÈME (ce que le journal porte, ce que l'appelant reçoit), pas
l'intention : le niveau ET les identifiants sont assertés.
"""
from __future__ import annotations

import logging

import pytest

from oto_mcp import grants_chain


@pytest.fixture
def chaine(monkeypatch):
    """Connecteur basculé, arête gagnante trouvée — seul le débit échoue."""
    monkeypatch.setattr(grants_chain, "is_chained", lambda p: True)
    verdict = grants_chain.ChainVerdict(granted=True, grant_id=77, resource_id=5,
                                        grantee=("user", "u-1"))
    monkeypatch.setattr(grants_chain, "platform_rung", lambda *a, **k: verdict)

    def _boum(*a, **k):
        raise RuntimeError("deadlock sur grant_counters")
    monkeypatch.setattr(grants_chain.db_grants, "bump_counter", _boum)


def test_le_debit_echoue_est_un_warning_avec_ses_identifiants(chaine, caplog):
    caplog.set_level(logging.DEBUG, logger=grants_chain.logger.name)

    # Fail-open conservé : l'appel a réussi, `record_usage` ne lève pas.
    assert grants_chain.record_usage("u-1", "apollo", 42, calls=3) is None

    debits = [r for r in caplog.records if "débit" in r.getMessage()]
    assert debits, "l'échec du compteur ne laisse aucune trace"
    r = debits[0]
    assert r.levelno >= logging.WARNING, "un warning, pas un debug invisible en prod"
    msg = r.getMessage()
    # Les identifiants sans lesquels la ligne n'est pas exploitable.
    assert "apollo" in msg and "u-1" in msg and "77" in msg and "42" in msg
