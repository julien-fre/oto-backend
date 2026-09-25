"""Un admin REPREND un agent : `oto_trigger op=take_over` (25/09/2026).

La règle « seul le propriétaire pose son agent sur son abonnement »
(`_abonnement.peut_agir_pour`) laissait un admin sans recours devant l'agent d'un
membre parti, ou d'un autre compte de la même personne. La reprise ne relâche pas la
règle : l'admin DEVIENT le propriétaire. Ce que ces bancs tiennent :

1. seul un admin d'org reprend (`403 org_admin_required`) ;
2. reprendre son propre agent ne fait rien ;
3. un agent ALLUMÉ sur un abonnement passe la garde de pose, jugée sur le REPRENEUR —
   refusée, rien n'est écrit ;
4. éteint, ou sur une clé d'org, il se reprend sans garde d'abonnement ;
5. le retour dit qui possédait l'agent et combien de travaux ont suivi.

Le transfert en base (déclencheur + travaux en attente, une transaction) a son banc :
`test_reprise_agent_db.py`.
"""
from __future__ import annotations

import pytest

from oto_mcp.capabilities import runner_triggers as RT
from oto_mcp.capabilities._types import AuthzDenied, ResolvedCtx

_ORG = 196
_ADMIN = "tulina:admin"
_ANCIEN = "tulina:ancien"


def _agent(**extra):
    return {"id": 1, "org_id": _ORG, "sub": _ANCIEN, "kind": "schedule",
            "procedure": "daily-brain-ingestion", "model": "claude-sonnet-5",
            "enabled": True, **extra}


@pytest.fixture
def monde(monkeypatch):
    """L'org, ses admins, le déclencheur stocké, et la TRACE de ce qui s'écrit."""
    etat = {"admins": {_ADMIN}, "agent": _agent(), "reprises": [], "gardes": []}

    monkeypatch.setattr(RT.roles, "is_org_admin",
                        lambda sub, org_id: org_id == _ORG and sub in etat["admins"])
    monkeypatch.setattr(RT.db, "get_trigger",
                        lambda tid, oid: dict(etat["agent"])
                        if etat["agent"] and tid == etat["agent"]["id"] and oid == _ORG
                        else None)

    def _reprendre(tid, oid, sub):
        etat["reprises"].append((tid, oid, sub))
        ancien = etat["agent"]["sub"]
        etat["agent"] = {**etat["agent"], "sub": sub}
        return dict(etat["agent"]), ancien, 3

    monkeypatch.setattr(RT.db, "reprendre_trigger", _reprendre)

    def _garde(sub, proprietaire, famille, *, flotte=False, org_id=None):
        etat["gardes"].append((sub, proprietaire, famille, org_id))
        if etat.get("refus"):
            raise AuthzDenied(400, etat["refus"], "refusé")

    monkeypatch.setattr(RT._abonnement, "exiger_a_la_pose", _garde)
    return etat


def _reprendre(sub=_ADMIN, trigger_id=1):
    return RT._triggers_sync(ResolvedCtx(sub=sub, org_id=_ORG),
                             RT.TriggerInput(op="take_over", trigger_id=trigger_id))


def test_un_simple_membre_ne_reprend_pas(monde):
    with pytest.raises(AuthzDenied) as e:
        _reprendre(sub="tulina:membre")
    assert (e.value.status, e.value.code) == (403, "org_admin_required")
    assert monde["reprises"] == []


def test_un_agent_inconnu_rend_404(monde):
    with pytest.raises(AuthzDenied) as e:
        _reprendre(trigger_id=999)
    assert (e.value.status, e.value.code) == (404, "trigger_not_found")


def test_l_admin_DEVIENT_le_proprietaire_et_le_retour_le_dit(monde):
    rep = _reprendre()
    assert monde["reprises"] == [(1, _ORG, _ADMIN)]
    assert rep["trigger"]["sub"] == _ADMIN
    assert rep["previous_owner"] == _ANCIEN
    assert rep["jobs_moved"] == 3


def test_reprendre_SON_agent_ne_fait_rien(monde):
    monde["agent"] = _agent(sub=_ADMIN)
    rep = _reprendre()
    assert monde["reprises"] == []
    assert rep["previous_owner"] == _ADMIN and rep["jobs_moved"] == 0


def test_un_agent_ALLUME_sur_un_abonnement_passe_la_garde_du_REPRENEUR(monde):
    monde["agent"] = _agent(model="sub:sonnet")
    _reprendre()
    # Jugée sur l'admin, comme une CRÉATION (propriétaire None : il le devient).
    assert monde["gardes"] == [(_ADMIN, None, "claude_subscription", _ORG)]


def test_la_garde_refusee_n_ecrit_RIEN(monde):
    monde["agent"] = _agent(model="sub:sonnet")
    monde["refus"] = "subscription_not_connected"
    with pytest.raises(AuthzDenied) as e:
        _reprendre()
    assert e.value.code == "subscription_not_connected"
    assert monde["reprises"] == []


def test_un_agent_ETEINT_sur_un_abonnement_se_reprend_librement(monde):
    # Le rallumage rejugera le propriétaire stocké — l'admin, désormais.
    monde["agent"] = _agent(model="sub:sonnet", enabled=False)
    monde["refus"] = "subscription_not_connected"
    _reprendre()
    assert monde["gardes"] == []
    assert monde["reprises"] == [(1, _ORG, _ADMIN)]


def test_un_agent_sur_une_CLE_d_org_n_a_pas_de_garde_d_abonnement(monde):
    _reprendre()
    assert monde["gardes"] == []
