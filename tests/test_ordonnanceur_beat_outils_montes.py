"""`op=beat` journalise, NOMMÉ, une flotte EN VOL dont un outil déclaré n'est
plus monté dans son org — la moitié « déjà en vol » de l'incident du 22/09
(cf. `test_runner_fleets_launch_outils_montes.py` pour la moitié `launch`).

Ce n'est PAS un arrêt : `beat` n'a aucun moyen sûr de savoir si le pas en
cours a besoin de CET outil précis, et couper à l'aveugle romprait des passes
qui n'en avaient pas besoin. Le contrat est donc : un journal, dédupliqué par
process pour ne pas spammer un battement répété, jamais un refus."""
from __future__ import annotations

import logging

from oto_mcp.capabilities import _ordonnanceur_de_campagne as ORD


def _stub(monkeypatch, manquants_par_appel, vivant=True):
    monkeypatch.setattr(ORD.db, "battre", lambda *a, **k: vivant)
    monkeypatch.setattr(ORD.db, "get_fleet", lambda fid, org: {
        "id": fid, "org_id": org, "sub": "pilote", "tools": ["fr_directors"],
        "status": "running", "taken_by": "box-a/oto-fleet"})
    it = iter(manquants_par_appel)
    monkeypatch.setattr(ORD._outils_manquants, "manquants",
                        lambda org, sub, tools: next(it))


def test_beat_journalise_les_outils_absents_une_fois(monkeypatch, caplog):
    _stub(monkeypatch, [["fr_directors"]])
    ORD._deja_journalise.clear()
    with caplog.at_level(logging.WARNING, logger=ORD.logger.name):
        ORD.geste(226, "beat", 1, "box-a/oto-fleet", None)
    messages = [r.message for r in caplog.records]
    assert any("fr_directors" in m and "#1" in m and "226" in m for m in messages)


def test_beat_ne_rejournalise_pas_le_meme_manque(monkeypatch, caplog):
    _stub(monkeypatch, [["fr_directors"], ["fr_directors"]])
    ORD._deja_journalise.clear()
    with caplog.at_level(logging.WARNING, logger=ORD.logger.name):
        ORD.geste(226, "beat", 1, "box-a/oto-fleet", None)
        ORD.geste(226, "beat", 1, "box-a/oto-fleet", None)
    hits = [r for r in caplog.records if "fr_directors" in r.message]
    assert len(hits) == 1, "un manque identique ne se journalise qu'une fois par process"


def test_beat_ne_journalise_rien_quand_tout_est_monte(monkeypatch, caplog):
    _stub(monkeypatch, [[]])
    ORD._deja_journalise.clear()
    with caplog.at_level(logging.WARNING, logger=ORD.logger.name):
        ORD.geste(226, "beat", 1, "box-a/oto-fleet", None)
    assert not any("outils déclarés absents" in r.message for r in caplog.records)


def test_beat_ne_leve_pas_si_le_controle_echoue(monkeypatch, caplog):
    """Un défaut du contrôle ne doit jamais casser le battement — la campagne
    continue d'être suivie même si on ne peut pas dire si ses outils tiennent."""
    monkeypatch.setattr(ORD.db, "battre", lambda *a, **k: True)
    monkeypatch.setattr(ORD.db, "get_fleet", lambda fid, org: {
        "id": fid, "org_id": org, "sub": "pilote", "tools": ["fr_directors"],
        "status": "running", "taken_by": "box-a/oto-fleet"})

    def _casse(org, sub, tools):
        raise RuntimeError("base indisponible")

    monkeypatch.setattr(ORD._outils_manquants, "manquants", _casse)
    ORD._deja_journalise.clear()
    rendu = ORD.geste(226, "beat", 1, "box-a/oto-fleet", None)
    assert rendu["beat_taken"] is True
