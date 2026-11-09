"""La panne qui ne meurt pas : un agent qui conclut `done` APRÈS un refus de capacité
(`db/episodes_agents.fins_malgre_refus`, classe `fini_malgre_refus`).

Ce qu'il faut prouver : les fragments lus dans le journal tiennent dans les VRAIS
messages de refus (pas une copie qui divergerait en silence) ; le relevé ne regarde que le
DERNIER travail réussi de l'agent, compte les autres, lit aussi un refus rendu en donnée
(le reçu d'une recette) ; un épisode de travaux morts garde la main ; l'épisode guérit au
premier succès SANS refus, et pas au succès suivant refusé. Joué sur une vraie base pour
le relevé et la guérison.
"""
from __future__ import annotations

import fnmatch
import json
import uuid

import pytest

ORG = 8420


def _comme_like(motif: str, texte: str) -> bool:
    return fnmatch.fnmatchcase(texte, motif.replace("%", "*"))


def test_les_fragments_tiennent_dans_les_vrais_messages():
    from oto_mcp import outils_du_travail
    from oto_mcp.capabilities import _procedure_du_travail as P
    from oto_mcp.db.episodes_agents import REFUS_DE_CAPACITE_TEXTES

    procedure = str(P._refus("`x` is neither this job's procedure (`y`) nor one it names.",
                             {"y": "org"}))
    # Le message tel que le journal le garde : enveloppé par fastmcp.
    procedure = f"Error calling tool 'oto_procedure': {procedure}"
    outil = ("Error calling tool 'oto_call': " + (
        "`fr_siret` is not in this hosted agent's tool list (job 1). A job only calls "
        "the tools it declares"))
    assert outils_du_travail.CODE == "tool_not_in_job"
    for texte in (procedure, outil):
        assert any(_comme_like(m, texte) for m in REFUS_DE_CAPACITE_TEXTES), texte


def test_le_fragment_outil_suit_le_message_du_refus(monkeypatch):
    """Le message de `outils_du_travail.refus`, produit par le code, pas recopié."""
    from oto_mcp import outils_du_travail, verrou_org
    from oto_mcp.db.episodes_agents import REFUS_DE_CAPACITE_TEXTES

    class _V:
        job_id = 1
    monkeypatch.setattr(verrou_org, "travail_de_la_requete", lambda: _V())
    monkeypatch.setattr(outils_du_travail, "_charge", lambda j: {"tools": ["a"]})
    texte = outils_du_travail.refus("fr_siret")
    assert texte and any(_comme_like(m, texte) for m in REFUS_DE_CAPACITE_TEXTES)


def test_la_forme_du_recu_d_une_recette_refusee():
    from oto_mcp import calllog
    from oto_mcp.db.episodes_agents import REFUS_DE_CAPACITE_FORMES
    from oto_mcp.recipes.code import refus_hors_liste

    recu = refus_hors_liste({"missing": ["fr_siret"], "trigger_id": 4, "job_id": 1,
                             "tools": ["a"]})

    class _R:
        structured_content = recu
        content = []
    assert calllog.forme_servie(_R()) in REFUS_DE_CAPACITE_FORMES


# --- en base -------------------------------------------------------------------------

_PROC = ("Error calling tool 'oto_procedure': `b` is neither this job's procedure (`a`) "
         "nor one it names. A hosted agent reads only its own job's procedure and the "
         "procedures it names in backticks.")


def _travail(tid, il_y_a_h, refus=None, status="done", forme=None, org=ORG):
    """Un travail conclu il y a `il_y_a_h` heures, son run, et ses appels : un appel
    réussi, plus un refus levé (`refus`) ou rendu en donnée (`forme`)."""
    from oto_mcp.db._conn import _connect
    run_id = uuid.uuid4().hex
    with _connect() as conn:
        conn.execute("INSERT INTO runs (run_id, sub, org_id, label) VALUES (%s, 'jane', "
                     "%s, 'job')", (run_id, org))
        job = conn.execute(
            "INSERT INTO runner_jobs (org_id, kind, payload, status, sub, run_id, "
            "finished_at) VALUES (%s, 'start', %s::jsonb, %s, 'jane', %s, "
            "NOW() - make_interval(secs => %s)) RETURNING id",
            (org, json.dumps({"trigger_id": tid}), status, run_id, il_y_a_h * 3600)
        ).fetchone()["id"]
        conn.execute("INSERT INTO tool_calls (tool, ok, run_id, org_id) "
                     "VALUES ('data_rows', TRUE, %s, %s)", (run_id, org))
        if refus:
            conn.execute("INSERT INTO tool_calls (tool, ok, error, run_id, org_id) "
                         "VALUES ('oto_procedure', FALSE, %s, %s, %s)",
                         (refus, run_id, org))
        if forme:
            conn.execute("INSERT INTO tool_calls (tool, ok, result_shape, run_id, org_id) "
                         "VALUES ('oto_recipe', TRUE, %s, %s, %s)", (forme, run_id, org))
    return job


def _fins():
    from oto_mcp.db import episodes_agents
    return {f["trigger_id"]: f for f in episodes_agents.fins_malgre_refus()}


@pytest.fixture()
def agent(live):
    from oto_mcp.db import runner_triggers as rt
    t = rt.create_trigger(ORG, "jane", procedure="sourcing", tz="UTC", tools=["a"],
                          cron="0 6 * * *", next_due="2999-01-01 00:00:00+00",
                          label="Sourcing")
    return t["id"]


def test_le_dernier_succes_refuse_est_releve_et_les_autres_comptes(agent):
    _travail(agent, 3, refus=_PROC)
    _travail(agent, 2)                      # un succès propre entre les deux
    dernier = _travail(agent, 1, refus=_PROC)
    f = _fins()[agent]
    assert (f["job_id"], f["travaux"], f["libelle"]) == (dernier, 2, "Sourcing")
    assert "reads only its own job's procedure" in f["motif"]


def test_un_dernier_succes_propre_n_est_pas_releve(agent):
    _travail(agent, 2, refus=_PROC)
    _travail(agent, 1)
    assert agent not in _fins()


def test_un_refus_rendu_en_donnee_compte(agent):
    _travail(agent, 1, forme="refused(tool_not_in_job)")
    assert _fins()[agent]["motif"] == "refused(tool_not_in_job)"


def test_un_autre_refus_ne_compte_pas(agent):
    _travail(agent, 1, refus="HTTP 404 not found")
    _travail(agent, 1, forme="refused(not_found)")
    assert agent not in _fins()


def test_hors_fenetre_ou_agent_eteint(agent):
    from oto_mcp.db import runner_triggers as rt
    from oto_mcp.db.episodes_agents import FENETRE_FINS_H
    _travail(agent, FENETRE_FINS_H + 1, refus=_PROC)
    assert agent not in _fins()
    _travail(agent, 1, refus=_PROC)
    rt.update_trigger(agent, ORG, {"enabled": False})
    assert agent not in _fins()


def _ouvrir(agent, job):
    from oto_mcp.db import episodes_agents
    return episodes_agents.ouvrir_fin_malgre_refus(
        org_id=ORG, trigger_id=agent, motif="refus", job_id=job, travaux=1)


def test_ouvert_une_fois_puis_rafraichi(agent):
    a = _ouvrir(agent, 1)
    b = _ouvrir(agent, 2)
    assert (a["neuf"], b["neuf"], a["id"]) == (True, False, b["id"])
    assert (b["classe"], b["cote"], b["statut"], b["dernier_job_id"]) == (
        "fini_malgre_refus", "inconnu", "ouvert", 2)


def test_un_episode_de_travaux_morts_garde_la_main(agent):
    from oto_mcp.db import episodes_agents
    mort = episodes_agents.ouvrir_ou_rafraichir(
        org_id=ORG, trigger_id=agent, classe="refus_passager", cote="plateforme",
        motif="HTTP 529", job_id=1, travaux=1)
    assert _ouvrir(agent, 2) is None
    assert episodes_agents.lire(mort["id"])["classe"] == "refus_passager"


def test_guerit_au_premier_succes_sans_refus_seulement(agent):
    from oto_mcp.db import episodes_agents
    ep = _ouvrir(agent, 1)
    _travail(agent, -0.01, refus=_PROC)     # conclu APRÈS l'ouverture, encore refusé
    assert ep["id"] not in {g["id"] for g in episodes_agents.clore_les_gueris()}
    _travail(agent, -0.02)                  # conclu après, sans refus
    clos = {g["id"]: g for g in episodes_agents.clore_les_gueris()}
    assert clos[ep["id"]]["resolution"] == "gueri"


def test_un_episode_de_travaux_morts_guerit_toujours_au_premier_succes(agent):
    """La règle d'avant ne change pas pour les autres classes : un succès, même refusé,
    guérit un épisode de travaux morts (le travail ne meurt plus)."""
    from oto_mcp.db import episodes_agents
    ep = episodes_agents.ouvrir_ou_rafraichir(
        org_id=ORG, trigger_id=agent, classe="refus_passager", cote="plateforme",
        motif="HTTP 529", job_id=1, travaux=1)
    _travail(agent, -0.01, refus=_PROC)
    assert ep["id"] in {g["id"] for g in episodes_agents.clore_les_gueris()}


@pytest.mark.parametrize("classe, statut_du_travail", [
    ("fini_malgre_refus", "done"),
    # Toute autre classe dont le dernier travail n'est pas mort : même refus.
    ("inconnu", "done"),
])
def test_op_rerun_ne_rejoue_jamais_un_travail_reussi(monkeypatch, classe,
                                                    statut_du_travail):
    """`oto_admin_agent_repair op=rerun` est un autre chemin que la boucle : sur un
    épisode qui désigne un travail RÉUSSI, il refuse — rejouer referait ses effets."""
    from types import SimpleNamespace

    from oto_mcp import db, medecin_agents
    from oto_mcp.capabilities import agent_repair as A

    ep = {"id": 1, "org_id": 7, "trigger_id": 4, "classe": classe, "cote": "inconnu",
          "statut": "ouvert", "dernier_job_id": 40, "reparations": []}
    monkeypatch.setattr(A, "_episode", lambda inp: ep)
    monkeypatch.setattr(db, "get_job", lambda j, o: {"id": j, "status": statut_du_travail,
                                                     "payload": {"trigger_id": 4}})
    monkeypatch.setattr(db, "get_trigger", lambda t, o: {"id": t, "enabled": True})
    monkeypatch.setattr(medecin_agents, "relancer",
                        lambda *a, **k: pytest.fail("rien ne doit être relancé"))
    r = A._agent_repair(SimpleNamespace(sub="op"),
                        A.AgentRepairInput(op="rerun", episode_id=1))
    assert r["relance"] is False and "a réussi" in r["refus"]
