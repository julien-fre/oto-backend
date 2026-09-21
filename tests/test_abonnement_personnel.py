"""Un travail qui tourne sur l'ABONNEMENT d'une personne (OTO-130).

Ce que ces bancs tiennent, et qui n'est pas négociable :

1. **Aucune clé n'est cherchée** pour un travail d'abonnement — ni celle de
   l'org, ni celle de la plateforme. Le travail s'exécute dans le bac à sable de
   son demandeur, où le programme officiel lit la session que cette personne y a
   ouverte elle-même. Un chemin qui irait au coffre serait la preuve que la
   plateforme paie, ou intermédie, ce qu'elle n'a pas le droit d'intermédier.
2. **Rien de ce qui sort d'ici ne ressemble à un secret** : le travail servi
   gagne un `sandbox_id`, et c'est tout.
3. **Un porteur sans connexion ARRÊTE le travail**, raison écrite — jamais une
   remise en file qui tournerait indéfiniment.
4. **Un abonnement ne sert que les agents de son propriétaire** : ni une flotte,
   ni l'agent d'un collègue.
"""
from __future__ import annotations

import pytest

from oto_mcp.capabilities import _abonnement, runner_jobs as RJ
from oto_mcp.db import user_subscriptions as US

_WORKER = "svc-runner-worker"
_PORTEUR = "personne-qui-paie"
_FAMILLE = "claude_subscription"
_BAC = "bac-a-sable-1"


def _travail(sub=_PORTEUR, famille=_FAMILLE, **extra):
    return {"id": 42, "org_id": 7, "sub": sub,
            "payload": {"procedure": "p", "model": "sub:sonnet",
                        "model_family": famille},
            **extra}


@pytest.fixture(autouse=True)
def _refus_sans_base(monkeypatch):
    """Arrêter un travail ÉCRIT sa raison en base (`arreter_definitivement`). Ces
    bancs jugent le verdict, pas l'écriture : elle a son propre banc."""
    arrets: list[tuple] = []
    monkeypatch.setattr(RJ.db, "arreter_definitivement",
                        lambda job_id, appelant, raison: arrets.append(
                            (job_id, appelant, raison)))
    return arrets


@pytest.fixture
def _abonnements(monkeypatch):
    """L'état des abonnements, en mémoire — et la TRACE des lectures du coffre.

    Le coffre est doublé par une levée : si un chemin d'abonnement allait y
    chercher une clé, le banc tomberait au lieu de passer en silence."""
    lignes: dict[tuple[str, str], dict] = {}
    monkeypatch.setattr(US, "get_subscription",
                        lambda sub, famille: lignes.get((sub, famille)))
    monkeypatch.setattr(
        "oto_mcp.capabilities.runner_jobs._cle_de_modele",
        lambda *a, **k: pytest.fail(
            "un travail d'abonnement est allé chercher une CLÉ : la plateforme ne "
            "paie pas ce chemin, et ne détient aucune session"))
    return lignes


def _servi(job, depot=_FAMILLE, appelant=_WORKER):
    return RJ._avec_cle(job, depot, appelant, worker=True, org_key_only=True)


def test_connecte_sert_le_bac_a_sable_et_aucune_cle(_abonnements):
    _abonnements[(_PORTEUR, _FAMILLE)] = {"statut": US.CONNECTE, "sandbox_id": _BAC}
    servi = _servi(_travail())
    assert servi["sandbox_id"] == _BAC
    # Le contrat NÉGATIF, celui qui compte : rien qui puisse porter un secret.
    assert "model_key" not in servi and "delegation_refusee" not in servi


def test_jamais_connecte_arrete_le_travail_en_le_disant(_abonnements, _refus_sans_base):
    servi = _servi(_travail())
    raison = servi["delegation_refusee"]
    assert "abonnement" in raison and "Réglages" in raison
    assert "sandbox_id" not in servi
    # ARRÊTÉ, pas remis en file : sans cette écriture, le worker suivant le
    # reprendrait, indéfiniment, sans que personne n'apprenne pourquoi.
    assert [a[0] for a in _refus_sans_base] == [42]


def test_deconnecte_arrete_le_travail(_abonnements):
    _abonnements[(_PORTEUR, _FAMILLE)] = {"statut": US.A_RECONNECTER,
                                          "sandbox_id": _BAC}
    assert "delegation_refusee" in _servi(_travail())


def test_sans_porteur_rien_a_consommer(_abonnements):
    """Un travail sans demandeur n'a aucun abonnement à consommer — et surtout pas
    celui de quelqu'un d'autre."""
    assert "delegation_refusee" in _servi(_travail(sub=None))


def test_plafond_dont_l_echeance_est_passee_est_retente(_abonnements):
    """La réservation saute déjà les plafonds à échéance FUTURE. Ce qui arrive ici
    est un plafond sans échéance : on tente, le fournisseur tranchera."""
    _abonnements[(_PORTEUR, _FAMILLE)] = {"statut": US.PLAFOND, "sandbox_id": _BAC,
                                          "limit_reset_at": None}
    assert _servi(_travail())["sandbox_id"] == _BAC


def test_une_autre_famille_garde_le_chemin_des_cles(monkeypatch):
    """La garde d'abonnement ne doit rien changer pour un travail ordinaire : le
    chemin de la clé reste celui d'avant, refus compris."""
    monkeypatch.setattr("oto_mcp.capabilities.runner_jobs._cle_de_modele",
                        lambda *a, **k: (None, None))
    monkeypatch.setattr("oto_mcp.db.connector_settings.get_connector_setting",
                        lambda *a, **k: None)
    servi = RJ._avec_cle(_travail(famille="anthropic"), "anthropic", _WORKER,
                         worker=True, org_key_only=True)
    assert "clé" in servi["delegation_refusee"]


class TestPose:
    """Le refus LISIBLE au moment de poser un agent sur un abonnement."""

    def test_flotte_refusee(self, _abonnements):
        with pytest.raises(Exception) as e:
            _abonnement.exiger_a_la_pose(_PORTEUR, _PORTEUR, _FAMILLE, flotte=True)
        assert e.value.code == "subscription_personal_only"

    def test_agent_d_un_collegue_refuse(self, _abonnements):
        with pytest.raises(Exception) as e:
            _abonnement.exiger_a_la_pose(_PORTEUR, "quelqu-un-d-autre", _FAMILLE)
        assert e.value.code == "subscription_personal_only"

    def test_sans_connexion_refuse(self, _abonnements):
        with pytest.raises(Exception) as e:
            _abonnement.exiger_a_la_pose(_PORTEUR, _PORTEUR, _FAMILLE)
        assert e.value.code == "subscription_not_connected"

    def test_connecte_passe(self, _abonnements):
        _abonnements[(_PORTEUR, _FAMILLE)] = {"statut": US.CONNECTE,
                                              "sandbox_id": _BAC}
        _abonnement.exiger_a_la_pose(_PORTEUR, _PORTEUR, _FAMILLE)

    def test_un_modele_ordinaire_ne_passe_pas_par_cette_garde(self, _abonnements):
        """Aucune lecture, aucun refus : une famille qui n'est pas un abonnement
        sort immédiatement."""
        _abonnement.exiger_a_la_pose(_PORTEUR, "quelqu-un-d-autre", "anthropic",
                                     flotte=True)


class TestCablage:
    """La garde est-elle POSÉE là où un agent se déclare ? Le banc de `TestPose`
    juge la fonction ; celui-ci juge qu'elle est appelée — c'est l'oubli qui
    coûterait cher, pas la logique."""

    def _ctx(self, sub=_PORTEUR, org_id=2):
        from oto_mcp.capabilities._types import ResolvedCtx
        return ResolvedCtx(sub=sub, org_id=org_id)

    @pytest.fixture(autouse=True)
    def _plateforme(self, monkeypatch, _abonnements):
        """Un runner armé qui sert la famille, et aucune clé exigée : le seul refus
        qui reste possible est celui de l'abonnement."""
        from oto_mcp.capabilities import runner_fleets as RF
        from oto_mcp.capabilities import runner_triggers as RT
        for module in (RT, RF):
            monkeypatch.setattr(module.db, "runner_arme",
                                lambda org: {"armed": True, "workers": 1,
                                             "last_seen": "2026-09-19 07:00:00",
                                             "families": [_FAMILLE]})
        monkeypatch.setattr("oto_mcp.db.connector_settings.get_connector_setting",
                            lambda *a, **k: None)
        monkeypatch.setattr(RT.db, "triggers_for_procedure", lambda o, p: [])
        # La population « bêta » et les outils de la procédure se lisent en base ;
        # ces bancs ne parlent ni de l'une ni des autres.
        # ⚠️ Par le NOM IMPORTÉ dans chaque module : `access` est une surface plate,
        # et doubler `access.quotas.has_option` ne change pas le nom déjà lié ici.
        monkeypatch.setattr(RF.access, "has_option", lambda *a, **k: True)
        monkeypatch.setattr(RT.access, "has_option", lambda *a, **k: True)
        monkeypatch.setattr(RT, "_outils_de_la_procedure", lambda ctx, p: ["oto_doc"])

    def test_un_declencheur_sur_un_abonnement_non_connecte_est_refuse(self, monkeypatch):
        import asyncio

        from oto_mcp.capabilities import runner_triggers as RT
        monkeypatch.setattr(RT.db, "create_trigger",
                            lambda *a, **k: pytest.fail(
                                "écrit malgré un abonnement non connecté"))
        with pytest.raises(Exception) as e:
            asyncio.run(RT._triggers(self._ctx(), RT.TriggerInput(
                op="create", procedure="p", cron="0 8 * * *", tz="Europe/Paris",
                model="sub:sonnet")))
        assert e.value.code == "subscription_not_connected"

    def test_une_flotte_ne_se_pose_JAMAIS_sur_un_abonnement(self, monkeypatch,
                                                            _abonnements):
        """Même connectée, une flotte est refusée : elle appartient à l'org."""
        import asyncio

        from oto_mcp.capabilities import runner_fleets as RF
        _abonnements[(_PORTEUR, _FAMILLE)] = {"statut": US.CONNECTE,
                                              "sandbox_id": _BAC}
        monkeypatch.setattr(RF.db, "create_fleet",
                            lambda *a, **k: pytest.fail("flotte écrite sur un forfait"))
        with pytest.raises(Exception) as e:
            asyncio.run(RF._fleets(self._ctx(), RF.FleetInput(
                op="create", label="passage", namespace="n", procedure="p",
                tools=["oto_doc"], model="sub:sonnet")))
        assert e.value.code == "subscription_personal_only"
