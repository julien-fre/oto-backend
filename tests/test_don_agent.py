"""Un propriétaire DONNE son agent : `oto_trigger op=give` (04/10/2026).

La propriété se donne, elle ne se prend pas : `take_over` ne vaut plus que pour
l'agent d'un membre parti, et un propriétaire présent transfère le sien par ce
geste. Ce que ces bancs tiennent :

1. seul le PROPRIÉTAIRE donne (`403 trigger_owner_required`) — un éditeur, un admin
   d'org non plus ; qui ne voit pas l'agent ne sait pas qu'il existe (`404`) ;
2. le destinataire est UNE personne, membre RÉEL de l'org (`org_members`) — pas
   `everyone`, pas un super admin hors de l'org (`404 give_not_org_member`) ;
3. le transfert est celui de la reprise : le destinataire devient le propriétaire,
   les travaux en attente le suivent, le retour dit qui possédait l'agent ;
4. un agent ALLUMÉ sur un abonnement passe la garde de pose jugée sur le
   DESTINATAIRE — refusée, rien n'est écrit ; éteint, il se donne librement ;
5. se donner son propre agent ne fait rien.

Le transfert en base (déclencheur + travaux en attente, une transaction) est celui
de `take_over` : `test_reprise_agent_db.py`.
"""
from __future__ import annotations

import pytest

from oto_mcp.capabilities import runner_triggers as RT
from oto_mcp.capabilities._types import AuthzDenied, ResolvedCtx

_ORG = 196
_PROPRIETAIRE = "exemple:proprietaire"
_COLLEGUE = "exemple:collegue"
_EDITEUR = "exemple:editeur"
_ADMIN = "exemple:admin"
_SUPER_ADMIN_DEHORS = "exemple:super-admin"


def _agent(**extra):
    return {"id": 1, "org_id": _ORG, "sub": _PROPRIETAIRE, "kind": "schedule",
            "procedure": "daily-brain-ingestion", "model": "claude-sonnet-5",
            "enabled": True, **extra}


@pytest.fixture
def monde(monkeypatch):
    """L'org et ses membres RÉELS, qui voit l'agent, et la TRACE de ce qui s'écrit."""
    etat = {"membres": {_PROPRIETAIRE, _COLLEGUE, _EDITEUR, _ADMIN},
            "niveaux": {_PROPRIETAIRE: "owner", _EDITEUR: "editor", _ADMIN: "admin"},
            "adresses": {"collegue@exemple.test": [_COLLEGUE],
                         "super@exemple.test": [_SUPER_ADMIN_DEHORS]},
            "agent": _agent(), "transferts": [], "gardes": []}

    monkeypatch.setattr(RT.org_store, "get_org_role",
                        lambda org_id, sub: "org_member"
                        if org_id == _ORG and sub in etat["membres"] else None)
    monkeypatch.setattr(RT._acces_agent, "niveau",
                        lambda sub, org_id, agent: etat["niveaux"].get(sub))
    monkeypatch.setattr(RT.db, "get_users_by_email",
                        lambda email: [{"sub": s} for s in etat["adresses"].get(email, [])])
    monkeypatch.setattr(RT.db, "get_trigger",
                        lambda tid, oid: dict(etat["agent"])
                        if etat["agent"] and tid == etat["agent"]["id"] and oid == _ORG
                        else None)

    def _transferer(tid, oid, sub):
        etat["transferts"].append((tid, oid, sub))
        ancien = etat["agent"]["sub"]
        etat["agent"] = {**etat["agent"], "sub": sub}
        return dict(etat["agent"]), ancien, 2

    monkeypatch.setattr(RT.db, "reprendre_trigger", _transferer)

    def _garde(sub, proprietaire, famille, *, flotte=False, org_id=None):
        etat["gardes"].append((sub, proprietaire, famille, org_id))
        if etat.get("refus"):
            raise AuthzDenied(400, etat["refus"], "refusé")

    monkeypatch.setattr(RT._abonnement, "exiger_a_la_pose", _garde)
    return etat


def _donner(sub=_PROPRIETAIRE, trigger_id=1, **cible):
    if not cible:
        cible = {"share_with_sub": _COLLEGUE}
    return RT._triggers_sync(ResolvedCtx(sub=sub, org_id=_ORG),
                             RT.TriggerInput(op="give", trigger_id=trigger_id, **cible))


def _refus(**kw) -> AuthzDenied:
    with pytest.raises(AuthzDenied) as e:
        _donner(**kw)
    return e.value


def test_le_destinataire_DEVIENT_le_proprietaire_et_le_retour_le_dit(monde):
    rep = _donner()
    assert monde["transferts"] == [(1, _ORG, _COLLEGUE)]
    assert rep["trigger"]["sub"] == _COLLEGUE
    assert rep["previous_owner"] == _PROPRIETAIRE
    assert rep["jobs_moved"] == 2


def test_le_destinataire_se_designe_aussi_par_son_adresse(monde):
    _donner(share_with_email="collegue@exemple.test")
    assert monde["transferts"] == [(1, _ORG, _COLLEGUE)]


@pytest.mark.parametrize("qui", [_EDITEUR, _ADMIN])
def test_seul_le_PROPRIETAIRE_donne(monde, qui):
    """Un éditeur modifie l'agent, un admin le gouverne : ni l'un ni l'autre ne
    dispose de sa propriété."""
    e = _refus(sub=qui)
    assert (e.status, e.code) == (403, "trigger_owner_required")
    assert monde["transferts"] == []


def test_qui_ne_voit_pas_l_agent_ne_sait_pas_qu_il_existe(monde):
    e = _refus(sub=_COLLEGUE)
    assert (e.status, e.code) == (404, "trigger_not_found")


def test_un_agent_inconnu_rend_404(monde):
    e = _refus(trigger_id=999)
    assert (e.status, e.code) == (404, "trigger_not_found")


@pytest.mark.parametrize("cible", [{"share_with_sub": _SUPER_ADMIN_DEHORS},
                                   {"share_with_email": "super@exemple.test"},
                                   {"share_with_email": "inconnu@exemple.test"}])
def test_un_agent_ne_se_donne_qu_a_un_membre_REEL_de_l_org(monde, cible):
    """Un super admin a un rôle effectif dans toute org : ce n'est pas un membre."""
    e = _refus(**cible)
    assert (e.status, e.code) == (404, "give_not_org_member")
    assert monde["transferts"] == []


@pytest.mark.parametrize("cible", [{"everyone": True},
                                   {"share_with_sub": None},
                                   {"share_with_sub": _COLLEGUE,
                                    "share_with_email": "collegue@exemple.test"}])
def test_un_agent_se_donne_a_UNE_personne(monde, cible):
    e = _refus(**cible)
    assert (e.status, e.code) == (400, "give_target_required")


def test_une_adresse_qui_designe_deux_membres_est_ambigue(monde):
    monde["adresses"]["collegue@exemple.test"] = [_COLLEGUE, _EDITEUR]
    e = _refus(share_with_email="collegue@exemple.test")
    assert e.code == "ambiguous_email"


def test_se_donner_SON_agent_ne_fait_rien(monde):
    rep = _donner(share_with_sub=_PROPRIETAIRE)
    assert monde["transferts"] == []
    assert rep["previous_owner"] == _PROPRIETAIRE and rep["jobs_moved"] == 0


def test_un_agent_ALLUME_sur_un_abonnement_passe_la_garde_du_DESTINATAIRE(monde):
    monde["agent"] = _agent(model="sub:sonnet")
    _donner()
    assert monde["gardes"] == [(_COLLEGUE, None, "claude_subscription", _ORG)]


def test_la_garde_refusee_n_ecrit_RIEN(monde):
    """L'agent reste à son propriétaire, sur son forfait : il l'éteint, le donne,
    et le destinataire le rallume sur le sien."""
    monde["agent"] = _agent(model="sub:sonnet")
    monde["refus"] = "subscription_not_connected"
    e = _refus()
    assert e.code == "subscription_not_connected"
    assert monde["transferts"] == []


def test_un_agent_ETEINT_sur_un_abonnement_se_donne_librement(monde):
    monde["agent"] = _agent(model="sub:sonnet", enabled=False)
    monde["refus"] = "subscription_not_connected"
    _donner()
    assert monde["gardes"] == []
    assert monde["transferts"] == [(1, _ORG, _COLLEGUE)]
