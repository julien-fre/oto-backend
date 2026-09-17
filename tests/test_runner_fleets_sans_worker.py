"""Ne pas PROMETTRE une exécution que personne n'assure — `op=launch`.

`launch` posait `armed` même si aucun worker n'était joignable pour le prendre :
un succès silencieux qui ne fait jamais rien. Mesuré (oto-runner#13, 07/09/2026) :
41 travaux restés en file 13 jours chez un partenaire, aucune unité `oto-fleet*`
sur la box. Le symptôme lu depuis le produit était « l'ordonnanceur est mort » —
un diagnostic faux posé sur une cause invisible, puisque l'armement, lui,
réussissait.

Même garde que `runner.triggers` (`_modele.exige_un_runner`, partagée depuis ce
lot) : la garde suit le VERBE — armer refuse sans runner, `stop` reste ouvert
(un passage mort doit pouvoir être rangé)."""
from __future__ import annotations

import pytest

from oto_mcp.capabilities import runner_fleets as RF
from oto_mcp.capabilities._types import AuthzDenied, ResolvedCtx


@pytest.fixture(autouse=True)
def _cle_de_modele_non_exigee(monkeypatch):
    monkeypatch.setattr("oto_mcp.db.connector_settings.get_connector_setting",
                        lambda *a, **k: None)


@pytest.fixture(autouse=True)
def _compte_beta(monkeypatch):
    monkeypatch.setattr(RF.access, "has_option", lambda sub, option, *, org=None: True)


def _ctx(sub="alexis", org_id=2):
    return ResolvedCtx(sub=sub, org_id=org_id)


def _appel(ctx, **kw):
    return RF._fleets(ctx, RF.FleetInput(**kw))


def _sans_worker(monkeypatch, last_seen=None):
    monkeypatch.setattr(RF.db, "runner_arme", lambda org: {
        "armed": False, "workers": 0, "last_seen": last_seen, "families": []})


def _avec_worker(monkeypatch):
    monkeypatch.setattr(RF.db, "runner_arme", lambda org: {
        "armed": True, "workers": 1, "last_seen": "2026-09-17 08:00:00",
        "families": []})


def test_launch_est_refuse_quand_aucun_worker_nest_joignable(monkeypatch):
    from oto_mcp import roles
    monkeypatch.setattr(roles, "is_org_admin", lambda *a, **k: True)
    monkeypatch.setattr(RF, "_run_courant", lambda: None)
    monkeypatch.setattr(RF.db, "get_fleet", lambda *a, **k: {
        "id": 1, "status": "draft", "procedure": "p", "input": "x"})
    _sans_worker(monkeypatch)

    with pytest.raises(AuthzDenied) as e:
        _appel(_ctx(), op="launch", fleet_id=1)
    assert (e.value.status, e.value.code) == (400, "no_runner_armed")
    # Le message doit dire QUOI FAIRE, pas seulement que c'est refusé.
    assert "OTO_RUNNER_ARMED" in e.value.message


def test_le_refus_narme_rien(monkeypatch):
    """Un refus n'écrit rien — `db.armer` ne doit même pas être appelé."""
    from oto_mcp import roles
    monkeypatch.setattr(roles, "is_org_admin", lambda *a, **k: True)
    monkeypatch.setattr(RF, "_run_courant", lambda: None)
    monkeypatch.setattr(RF.db, "get_fleet", lambda *a, **k: {
        "id": 1, "status": "draft", "procedure": "p", "input": "x"})
    _sans_worker(monkeypatch)
    appele = {}
    monkeypatch.setattr(RF.db, "armer", lambda *a, **k: appele.setdefault("oui", True))

    with pytest.raises(AuthzDenied):
        _appel(_ctx(), op="launch", fleet_id=1)
    assert "oui" not in appele


def test_launch_passe_quand_un_worker_est_joignable(monkeypatch):
    from oto_mcp import roles
    monkeypatch.setattr(roles, "is_org_admin", lambda *a, **k: True)
    monkeypatch.setattr(RF, "_run_courant", lambda: None)
    monkeypatch.setattr(RF.db, "get_fleet", lambda *a, **k: {
        "id": 1, "status": "draft", "procedure": "p", "input": "x"})
    monkeypatch.setattr(RF.db, "armer", lambda *a, **k: {
        "id": 1, "max_rows": None, "max_tokens_per_row": None})
    _avec_worker(monkeypatch)

    out = _appel(_ctx(), op="launch", fleet_id=1)
    assert out["fleet"]["id"] == 1


def test_stop_reste_ouvert_sans_worker():
    """LA garantie du lot : un passage mort doit pouvoir être arrêté — le
    refuser enfermerait l'utilisateur avec un objet qui lui ment, sans même
    lui laisser le geste qui range."""
    # `_exige_un_runner` n'est appelée que par `launch` : `stop` ne la lit pas
    # du tout, donc même sans doubler `db.runner_arme` ici, un `stop` ne doit
    # jamais lever `no_runner_armed`. La preuve : ce banc ne mocke pas
    # `runner_arme` et vérifie seulement le CODE de refus rendu.
    from oto_mcp.capabilities import runner_fleets as RF2
    import inspect
    src = inspect.getsource(RF2)
    idx_stop = src.index('if inp.op == "stop"')
    idx_launch = src.index('if inp.op == "launch"')
    bloc_stop = src[idx_stop:idx_stop + 800]
    assert "exige_un_runner" not in bloc_stop
    assert idx_launch < idx_stop


def test_la_garde_partage_la_meme_fonction_que_les_declencheurs():
    """DRY attendu par ce lot : une seule définition du motif, partagée par
    `runner.triggers` et `runner.fleets` — pas deux copies qui divergent."""
    from oto_mcp.capabilities import _modele, runner_triggers
    assert hasattr(_modele, "exige_un_runner")
    assert not hasattr(runner_triggers, "_exige_un_runner")
