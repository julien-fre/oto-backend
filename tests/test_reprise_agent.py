"""Un admin REPREND un agent : `oto_trigger op=take_over` (25/09/2026).

La règle « seul le propriétaire pose son agent sur son abonnement »
(`_abonnement.peut_agir_pour`) laissait un admin sans recours devant l'agent d'un
membre parti. La reprise ne relâche pas la règle : l'admin DEVIENT le propriétaire.
Depuis le 04/10/2026, elle ne vaut QUE pour l'agent d'un membre parti : la propriété
se donne, elle ne se prend pas — l'agent d'un membre présent se partage. Ce que ces
bancs tiennent :

1. seul un admin d'org reprend (`403 org_admin_required`) ;
2. reprendre son propre agent ne fait rien ;
2b. l'agent d'un membre toujours dans l'org ne se reprend pas
    (`403 owner_still_member`), et rien n'est écrit ;
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
    # `_ANCIEN` a QUITTÉ l'org : c'est le cas pour lequel la reprise existe.
    etat = {"admins": {_ADMIN}, "membres": {_ADMIN, "tulina:membre"},
            "agent": _agent(), "reprises": [], "gardes": []}

    monkeypatch.setattr(RT.roles, "is_org_admin",
                        lambda sub, org_id: org_id == _ORG and sub in etat["admins"])
    monkeypatch.setattr(RT.org_store, "get_org_role",
                        lambda org_id, sub: "org_member"
                        if org_id == _ORG and sub in etat["membres"] else None)
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


def test_l_agent_d_un_membre_PRESENT_ne_se_reprend_pas(monde):
    """Il se partage (`op=share`) : le prendre retirait son travail à quelqu'un qui
    n'a rien demandé, et le faisait tourner sous une autre identité."""
    monde["membres"].add(_ANCIEN)
    with pytest.raises(AuthzDenied) as e:
        _reprendre()
    assert (e.value.status, e.value.code) == (403, "owner_still_member")
    assert monde["reprises"] == [] and monde["gardes"] == []


def test_l_agent_d_un_SUPER_ADMIN_parti_se_reprend(monde, monkeypatch):
    """L'appartenance se lit dans `org_members`, pas dans le rôle effectif : celui-ci
    escalade un super admin en admin de TOUTE org, et son agent, une fois parti, ne
    se reprendrait jamais."""
    monkeypatch.setattr(RT.roles, "is_platform_admin", lambda sub: sub == _ANCIEN)
    rep = _reprendre()
    assert rep["previous_owner"] == _ANCIEN
    assert monde["reprises"] == [(1, _ORG, _ADMIN)]


def test_un_simple_membre_ne_reprend_pas_meme_l_agent_d_un_parti(monde):
    """L'ordre des refus : `org_admin_required` d'abord — un non-admin n'apprend
    pas si le propriétaire est encore là."""
    monde["membres"].add(_ANCIEN)
    with pytest.raises(AuthzDenied) as e:
        _reprendre(sub="tulina:membre")
    assert e.value.code == "org_admin_required"


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
