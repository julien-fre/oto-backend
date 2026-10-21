"""`runner.jobs op=forfait` : la SONDE du worker, portée sur le forfait, et le verdict.
Et le prédicat unique de la pause qui freine la réservation."""
from datetime import datetime, timedelta, timezone

import pytest

from oto_mcp.capabilities import runner_jobs as RJ
from oto_mcp.capabilities._types import AuthzDenied
from oto_mcp.db import user_subscriptions as US

_FAMILLE = "claude_subscription"
_RAPPORT = {"abonnement": {"etat": "rejected", "fenetre": "five_hour", "echeance": 1790029800,
                           "fenetres": {"five_hour": {"utilization": 1.0,
                                                      "resetsAt": 1790029800}}}}


class _Ctx:
    def __init__(self, platform_worker=True, sub="worker-1"):
        self.platform_worker, self.sub = platform_worker, sub


@pytest.fixture
def _monde(monkeypatch):
    etat = {"tenu": True, "ligne": {"statut": US.CONNECTE}, "rendus": [], "rapports": []}
    monkeypatch.setattr(RJ.db, "porteur_et_famille", lambda job_id: {
        "sub": "demandeur", "org_id": 7, "model_family": _FAMILLE, "abonnement": "demandeur"})
    monkeypatch.setattr(RJ.db, "tenu_par", lambda job_id, sub: etat["tenu"])
    monkeypatch.setattr(RJ._abonnement, "noter_rapport",
                        lambda conclu, ok, res: etat["rapports"].append(res))
    monkeypatch.setattr(RJ._abonnement, "en_pool", lambda org, famille: False)
    monkeypatch.setattr(RJ.user_subscriptions, "get_subscription",
                        lambda sub, famille: etat["ligne"])
    monkeypatch.setattr(RJ.db, "rendre_a_la_file",
                        lambda job_id, sub, raison, delai_s=60:
                        etat["rendus"].append((job_id, sub, delai_s)) or True)
    return etat


def _forfait(ctx=None, result=_RAPPORT):
    return RJ._trancher_le_forfait(ctx or _Ctx(), RJ.JobsInput(op="forfait", job_id=11,
                                                                result=result))


def test_un_forfait_en_pause_rend_le_travail_SANS_tentative(_monde):
    reprise = datetime.now(timezone.utc) + timedelta(hours=2)
    _monde["ligne"] = {"statut": US.PLAFOND, "limit_reset_at": reprise}
    out = _forfait()
    assert out["servable"] is False and out["limit_reset_at"] == reprise.isoformat()
    assert _monde["rapports"] == [_RAPPORT]
    assert _monde["rendus"] == [(11, "worker-1", US.VERIFICATION_S)]


def test_un_forfait_servable_laisse_partir(_monde):
    assert _forfait() == {"ok": True, "servable": True}
    assert _monde["rendus"] == []


def test_une_echeance_passee_n_est_pas_une_pause(_monde):
    _monde["ligne"] = {"statut": US.PLAFOND,
                       "limit_reset_at": datetime.now(timezone.utc) - timedelta(minutes=1)}
    assert _forfait()["servable"] is True


def test_un_membre_ne_met_personne_en_pause(_monde):
    with pytest.raises(AuthzDenied) as e:
        _forfait(_Ctx(platform_worker=False))
    assert e.value.status == 403 and _monde["rapports"] == []


def test_on_ne_parle_que_d_un_travail_qu_on_tient(_monde):
    _monde["tenu"] = False
    with pytest.raises(AuthzDenied) as e:
        _forfait()
    assert e.value.status == 404 and _monde["rapports"] == []


def test_sans_rapport_rien_n_est_porte(_monde):
    with pytest.raises(AuthzDenied) as e:
        _forfait(result={"autre": 1})
    assert e.value.status == 400


def test_la_pause_ne_vieillit_que_pour_le_worker_qui_sonde():
    assert "updated_at" not in US.pause_en_vigueur("ab")
    sonde = US.pause_en_vigueur("ab", sonde=True)
    assert f"make_interval(secs => {US.VERIFICATION_S})" in sonde
    assert sonde.startswith("COALESCE(") and sonde.endswith(", false)")
