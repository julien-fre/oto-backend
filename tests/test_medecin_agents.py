"""Soigner un agent avant qu'on le remarque (`medecin_agents.py`) — sans base.

Ce qui fait que la boucle répare sans nuire : la classe se lit sur les motifs RÉELS de
la file ; seule une panne de la PLATEFORME se relance, une fois par travail mort,
`RELANCES_MAX` fois par épisode, jamais par-dessus une occurrence qui attend ni au-delà
de `AGE_MAX` ; la trace s'écrit AVANT l'enfilage, et un refus de la trace n'enfile rien ;
une panne du titulaire s'escalade sans geste."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from oto_mcp import medecin_agents as M

_MAINTENANT = datetime(2026, 10, 27, 10, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("motif, attendu", [
    # Les motifs relevés en production (forme exacte, tronquée comme la file la garde).
    ("ce travail demande un modèle `anthropic`, et les agents `anthropic` ne tournent ",
     ("famille_non_servie", "plateforme")),
    ("Aucun worker ne sert la famille `mistral` (aucun worker vu)",
     ("famille_non_servie", "plateforme")),
    ("fin_anormale (cle_sans_workspace, HTTP 400 : clé d'organisation Anthropic : rens",
     ("cle_refusee", "proprietaire")),
    ('RuntimeError : agent de la ferme : 422 {"erreur": "clé d\'API invalide : une clé ',
     ("cle_refusee", "proprietaire")),
    ("HTTP 401 invalid x-api-key", ("cle_refusee", "proprietaire")),
    ("identité invalide — le porteur a quitté l'org", ("identite", "proprietaire")),
    ("HTTP 529 overloaded_error", ("refus_passager", "plateforme")),
    ("HTTP 503 Service Unavailable", ("refus_passager", "plateforme")),
    # Un 429 de quota épuisé n'est PAS passager : la règle de la file le dit déjà.
    ("HTTP 429 insufficient_quota", ("cle_refusee", "proprietaire")),
    # L'ancien libellé d'un refus du fournisseur : jamais relancé d'office.
    ("fin_anormale (success)", ("inconnu", "inconnu")),
    (None, ("inconnu", "inconnu")),
])
def test_la_classe_se_lit_sur_les_motifs_reels(motif, attendu):
    assert M.classer(motif) == attendu


def _panne(**kw):
    p = {"org_id": 7, "trigger_id": 55, "model": "claude-sonnet-5", "job_id": 900,
         "kind": "start", "payload": {"trigger_id": 55, "_plateforme": {"x": 1}},
         "sub": "porteur", "dernier": _MAINTENANT - timedelta(minutes=20), "travaux": 1,
         "motif": "HTTP 529 overloaded_error"}
    p.update(kw)
    return p


def _episode(**kw):
    e = {"id": 3, "org_id": 7, "trigger_id": 55, "classe": "refus_passager",
         "cote": "plateforme", "statut": "ouvert", "reparations": []}
    e.update(kw)
    return e


@pytest.fixture()
def file(monkeypatch):
    from oto_mcp import db
    from oto_mcp.db import episodes_agents

    etat = {"enfiles": [], "notes": [], "poses": [], "attente": {"pending": 0, "held": 0},
            "garde": True, "familles": ["anthropic"]}
    monkeypatch.setattr(db, "file_du_declencheur", lambda t, o: dict(etat["attente"]))
    monkeypatch.setattr(db, "enqueue_job", lambda org, kind, payload=None, sub=None, **k:
                        etat["enfiles"].append((org, kind, payload, sub)) or {"id": 1})
    monkeypatch.setattr(db, "runner_arme", lambda o: {"families": etat["familles"]})

    def _noter(eid, **kw):
        etat["notes"].append((eid, kw))
        return {"id": eid} if etat["garde"] else None
    monkeypatch.setattr(episodes_agents, "noter_reparation", _noter)
    monkeypatch.setattr(episodes_agents, "poser",
                        lambda eid, **kw: etat["poses"].append((eid, kw)) or {"id": eid})
    return etat


def test_un_refus_passager_se_relance_une_fois_trace_d_abord(file):
    assert M._soigner(_panne(), _episode(), _MAINTENANT) == "relance"
    assert file["notes"] == [(3, {"action": "relance", "acteur": "medecin", "job_id": 900,
                                  "detail": "relance du travail 900 (refus_passager)",
                                  "maximum": M.RELANCES_MAX})]
    org, kind, payload, sub = file["enfiles"][0]
    assert (org, kind, sub) == (7, "start", "porteur")
    assert payload["trigger_id"] == 55, "la même charge, rendue à `enqueue_job` qui l'épure"


def test_un_travail_deja_relance_ne_l_est_pas_deux_fois(file):
    ep = _episode(reparations=[{"action": "relance", "job_id": 900}])
    assert M._soigner(_panne(), ep, _MAINTENANT) is None
    assert file["enfiles"] == [] and file["notes"] == []


def test_une_occurrence_qui_attend_bloque_la_relance(file):
    file["attente"] = {"pending": 1, "held": 0}
    assert M.relancer(_panne(), _episode(), maintenant=_MAINTENANT) == \
        "une occurrence de l'agent attend déjà dans la file"
    assert file["enfiles"] == []


def test_un_travail_mort_trop_ancien_ne_se_relance_pas(file):
    vieux = _panne(dernier=_MAINTENANT - M.AGE_MAX - timedelta(minutes=1))
    assert "trop ancien" in M.relancer(vieux, _episode(), maintenant=_MAINTENANT)
    assert file["enfiles"] == []


def test_la_garde_de_l_episode_refuse_donc_rien_n_est_enfile(file):
    # Plafond atteint (ou un autre processus a gagné) : la trace refuse, le geste n'a pas lieu,
    # et l'épisode passe à un humain — chez nous, pas chez le titulaire.
    file["garde"] = False
    assert M._soigner(_panne(), _episode(), _MAINTENANT) is None
    assert file["enfiles"] == []
    assert file["poses"] == [(3, {"statut": "escalade",
                                  "diagnostic": f"{M.RELANCES_MAX} relances sans guérison"})]


def test_une_famille_non_servie_attend_qu_un_worker_la_serve(file):
    ep = _episode(classe="famille_non_servie")
    file["familles"] = []
    assert M._soigner(_panne(), ep, _MAINTENANT) is None
    assert file["enfiles"] == []
    file["familles"] = ["anthropic"]
    assert M._soigner(_panne(), ep, _MAINTENANT) == "relance"


def test_une_panne_du_titulaire_s_escalade_sans_geste(file):
    ep = _episode(classe="cle_refusee", cote="proprietaire")
    assert M._soigner(_panne(), ep, _MAINTENANT) is None
    assert file["enfiles"] == [] and file["notes"] == []
    assert file["poses"] == [(3, {"statut": "escalade"})]


def test_une_panne_inconnue_reste_ouverte_pour_l_agent_medecin(file):
    ep = _episode(classe="inconnu", cote="inconnu")
    assert M._soigner(_panne(), ep, _MAINTENANT) is None
    assert file["enfiles"] == [] and file["poses"] == []


def test_un_passage_ne_s_arrete_pas_sur_une_panne_illisible(monkeypatch, file):
    from oto_mcp.db import episodes_agents

    monkeypatch.setattr(episodes_agents, "clore_les_gueris",
                        lambda: [{"id": 1, "reparations": [{"action": "relance"}]}])
    monkeypatch.setattr(episodes_agents, "pannes_en_cours",
                        lambda: [_panne(trigger_id=1), _panne(trigger_id=2)])
    appels = []

    def _ouvrir(**kw):
        appels.append(kw["trigger_id"])
        if kw["trigger_id"] == 1:
            raise RuntimeError("ligne illisible")
        return _episode(trigger_id=2)
    monkeypatch.setattr(episodes_agents, "ouvrir_ou_rafraichir", _ouvrir)
    bilan = M.balayer(_MAINTENANT)
    assert appels == [1, 2]
    assert bilan == {"pannes": 2, "relances": 1, "gueris": 1, "gueris_apres_soin": 1,
                     "erreurs": 1}
