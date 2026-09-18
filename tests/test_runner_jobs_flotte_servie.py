"""On n'enfile pas une exécution sur une flotte qui ne la sert pas (oto-backend#996).

Le trou, mesuré sur le code le 18/09/2026 : le driver d'oto-runner (`fleet.py`)
appelle `launch` puis `take` et ne fait que JOURNALISER leurs échecs. Refusé
(`no_runner_armed`, `model_not_served`…), la flotte reste `draft` ; le driver
enfile pourtant ses travaux avec `fleet_id`, et l'enfilement ne vérifiait que
l'appartenance. Or `stop` refuse une flotte `draft` (`not_stoppable`) : des
exécutions qui tournent et dépensent, qu'aucun geste ne peut plus arrêter.

La garde tient CÔTÉ SERVEUR, quel que soit le client : `op=enqueue fleet_id=`
exige `armed`/`running` — exactement les états que `stop` sait arrêter —, lu
sous verrou dans la transaction de l'INSERT (le verrou lui-même est éprouvé sur
une vraie base : `tests/api/test_runner_jobs_fleet_rest.py`)."""
from __future__ import annotations

from contextlib import contextmanager

import pytest

from oto_mcp.capabilities import runner_jobs as RJ
from oto_mcp.capabilities._types import AuthzDenied, ResolvedCtx


@pytest.fixture(autouse=True)
def _cle_de_modele_non_exigee(monkeypatch):
    monkeypatch.setattr("oto_mcp.db.connector_settings.get_connector_setting",
                        lambda *a, **k: None)


@pytest.fixture
def file(monkeypatch):
    """La transaction, le verrou et l'INSERT doublés — et ce que chacun a vu."""
    vu = {"statut": "armed", "verrous": [], "enfiles": []}
    transaction = object()

    @contextmanager
    def _connect():
        yield transaction

    def _verrou(conn, fleet_id, org_id):
        vu["verrous"].append((conn, fleet_id, org_id))
        return vu["statut"]

    def _enqueue(org_id, kind, payload=None, run_id=None, max_attempts=3,
                 fleet_id=None, sub=None, conn=None, **_):
        vu["enfiles"].append({"org": org_id, "fleet": fleet_id, "conn": conn})
        return {"id": 7, "status": "pending", "due_at": "2026-09-18",
                "fleet_id": fleet_id, "sub": sub}

    monkeypatch.setattr(RJ.db, "_connect", _connect)
    monkeypatch.setattr(RJ.db, "verrouiller_la_flotte", _verrou)
    monkeypatch.setattr(RJ.db, "enqueue_job", _enqueue)
    vu["transaction"] = transaction
    return vu


def _enfiler(**kw):
    return RJ._jobs(ResolvedCtx(sub="usr_demandeur", org_id=226), RJ.JobsInput(
        op="enqueue", kind="start", payload={"procedure": "p"}, **kw))


@pytest.mark.parametrize("statut", ["draft", "stopping", "stopped", "done", "failed"])
def test_une_flotte_qui_ne_sert_pas_refuse_l_execution(file, statut):
    file["statut"] = statut
    with pytest.raises(AuthzDenied) as e:
        _enfiler(fleet_id=12)
    assert (e.value.status, e.value.code) == (409, "fleet_not_serving")
    # Le refus dit l'état ACTUEL et le geste qui débloque.
    assert f"`{statut}`" in e.value.message
    assert "op=launch" in e.value.message and "op=take" in e.value.message
    assert file["enfiles"] == [], "un refus n'enfile rien"


@pytest.mark.parametrize("statut", ["armed", "running"])
def test_une_flotte_qui_sert_accepte_l_execution(file, statut):
    file["statut"] = statut
    out = _enfiler(fleet_id=12)
    assert out["fleet_id"] == 12


def test_le_verrou_et_l_insert_partagent_la_transaction(file):
    """La garde n'est vraie sous concurrence que si l'état est lu DANS la
    transaction de l'INSERT : un `stop` ne peut alors pas passer entre les deux."""
    _enfiler(fleet_id=12)
    assert file["verrous"] == [(file["transaction"], 12, 226)]
    assert file["enfiles"][0]["conn"] is file["transaction"]


def test_la_flotte_d_une_autre_org_reste_un_404_sans_oracle(file):
    """L'appartenance passe AVANT l'état : une flotte étrangère ne dit pas le sien."""
    file["statut"] = None
    with pytest.raises(AuthzDenied) as e:
        _enfiler(fleet_id=12)
    assert (e.value.status, e.value.code) == (404, "fleet_not_found")
    assert file["enfiles"] == []


def test_sans_flotte_rien_n_est_verrouille(file):
    """Un déclencheur ou un appel direct n'appartient à aucun passage."""
    out = _enfiler()
    assert out["fleet_id"] is None
    assert file["verrous"] == [] and file["enfiles"][0]["conn"] is None
