"""Which model answers: a new conversation starts on the default model and loads most of
its tools on demand; it moves up — once, for good — on the fixed signs of hard work; a
conversation of the first version keeps its model and its tools."""
from __future__ import annotations

import asyncio

from oto_mcp.capabilities.copilot import boucle, cle, depense, modele, palier
from oto_mcp.capabilities.copilot.outils import Boite, a_la_demande

OFFERTS = [{"name": n, "description": n, "input_schema": {"type": "object"}}
           for n in ("oto_search", "oto_call", "oto_tool_schema", "oto_trigger", "data_rows",
                     "data_write", "feedback")]
OFFERTS[-1]["cache_control"] = {"type": "ephemeral"}


def _fil(**k):
    base = {"id": "thr_t", "sub": "u-a", "org_id": 1, "project_id": None, "run_id": "run-1",
            "system": "", "messages": [], "queue": [], "results": [], "pending": None,
            "remembered": [], "client_tools": [], "mode": "ask", "model_calls": 0,
            "model": palier.DEFAUT, "_verrou": "v"}
    base.update(k)
    return base


def _appel(outil, **input_):
    return {"type": "tool_use", "id": f"tu_{outil}", "name": outil, "input": input_}


# --- the tools a conversation sends ------------------------------------------------------

def test_a_la_demande_garde_le_socle_devant_et_differe_le_reste():
    envoyes = a_la_demande(OFFERTS)
    noms = [o["name"] for o in envoyes]
    assert noms[:5] == ["oto_search", "oto_call", "oto_tool_schema", "data_rows", "feedback"]
    assert all(o.get("defer_loading") for o in envoyes[5:])
    assert not any(o.get("defer_loading") for o in envoyes[:5])
    # One breakpoint, on the last tool sent up front — never on a deferred one.
    assert [o["name"] for o in envoyes if o.get("cache_control")] == ["feedback"]
    assert OFFERTS[3].get("defer_loading") is None, "the toolbox itself is left as it was"


def test_la_recherche_d_outils_part_en_tete_seulement_quand_un_outil_est_differe():
    corps = modele.corps_de_requete("s", a_la_demande(OFFERTS), [], "claude-sonnet-5-5")
    assert corps["model"] == "claude-sonnet-5-5"
    assert corps["tools"][0] == modele.RECHERCHE_D_OUTILS
    corps = modele.corps_de_requete("s", OFFERTS, [])
    assert corps["model"] == modele.MODELE
    assert modele.RECHERCHE_D_OUTILS not in corps["tools"]


def test_une_conversation_d_avant_garde_ses_outils_et_son_modele():
    b = Boite(frozenset(), {}, OFFERTS)
    ancien = _fil(model=None)
    assert palier.modele_du_fil(ancien) == palier.HERITE
    assert boucle._outils_du_modele(ancien, b) == OFFERTS
    assert not palier.monter(ancien, "test")
    assert ancien["model"] is None


def test_une_conversation_neuve_differe_ses_outils():
    b = Boite(frozenset(), {}, OFFERTS)
    assert any(o.get("defer_loading") for o in boucle._outils_du_modele(_fil(), b))


# --- the model a conversation runs on ----------------------------------------------------

def test_le_defaut_et_le_fort_se_reglent_et_un_modele_sans_prix_est_ecarte(monkeypatch):
    assert (palier.defaut(), palier.fort()) == ("claude-sonnet-5-5", "claude-opus-5-5")
    monkeypatch.setenv("OTO_COPILOT_MODEL", "claude-opus-5-5")
    assert palier.defaut() == "claude-opus-5-5"
    monkeypatch.setenv("OTO_COPILOT_MODEL", "claude-inconnu")
    assert palier.defaut() == palier.DEFAUT


def test_chaque_modele_choisi_a_un_prix_pour_le_plafond():
    assert {palier.DEFAUT, palier.FORT, palier.HERITE} <= set(depense.PRIX_PAR_MTOK)


def test_monter_est_sans_retour():
    fil = _fil()
    assert palier.monter(fil, "test") and fil["model"] == palier.FORT
    assert not palier.monter(fil, "test") and fil["model"] == palier.FORT


def test_signes_du_travail_difficile():
    sans_note = lambda name: None  # noqa: E731
    creer = {"content": [_appel("oto_trigger", op="create", cron="0 8 * * 1")]}
    assert palier.ecrit_un_lourd(creer, sans_note)
    par_oto_call = {"content": [_appel("oto_call", name="oto_procedure",
                                       arguments={"op": "create", "slug": "x"})]}
    assert palier.ecrit_un_lourd(par_oto_call, sans_note)
    assert not palier.ecrit_un_lourd({"content": [_appel("data_write", datastore=3)]},
                                     sans_note)
    assert palier.touche_une_automatisation({"content": [_appel("oto_trigger", op="list")]})
    charge = {"content": [{"type": "tool_search_tool_result", "tool_use_id": "srv_1",
                           "content": {"type": "tool_search_tool_search_result",
                                       "tool_references": [{"type": "tool_reference",
                                                            "tool_name": "oto_fleet"}]}}]}
    assert palier.touche_une_automatisation(charge)
    assert not palier.touche_une_automatisation(
        {"content": [_appel("oto_guide", op="read", slug="notice")]})
    assert palier.a_parle({"content": [{"type": "text", "text": "Je crée l'agent."}]})
    assert not palier.a_parle({"content": [{"type": "text", "text": "  "}]})


def test_deux_lots_en_echec_de_suite_font_monter():
    fil, etat = _fil(), {}
    echec = [{"type": "tool_result", "tool_use_id": "a", "content": "x", "is_error": True}]
    fil["results"] = list(echec)
    boucle._compter_les_echecs(fil, etat)
    assert fil["model"] == palier.DEFAUT
    fil["results"] = list(echec)
    boucle._compter_les_echecs(fil, etat)
    assert fil["model"] == palier.FORT


def test_une_carte_refusee_fait_monter():
    bloc = {"id": "tu_1", "name": "data_write", "input": {"datastore": 3}}
    fil = _fil(queue=[bloc], pending={"kind": "approval", "block": bloc, "sorte": "write"})
    assert boucle.repondre_carte(fil, "tu_1", False, None, False) is None
    assert fil["model"] == palier.FORT


# --- the loop ----------------------------------------------------------------------------

def _boucle(monkeypatch, reponses):
    """Runs one leg against scripted model answers; returns the models called."""
    appels: list = []
    monkeypatch.setattr(depense, "atteint", lambda sub: False)
    monkeypatch.setattr(depense, "noter", lambda *a: None)
    for f in ("reserver", "clore", "regler", "liberer"):
        monkeypatch.setattr(cle, f, lambda *a: None)
    monkeypatch.setattr(boucle.store, "prolonger", lambda *a: None)
    monkeypatch.setattr(boucle.pieces, "resoudre", lambda messages, *a: messages)

    async def sauver(_):
        return None
    monkeypatch.setattr(boucle, "sauver", sauver)

    async def flux(cle_, ws, corps, releve=None):
        appels.append(corps["model"])
        yield "end", reponses.pop(0)
    monkeypatch.setattr(modele, "flux", flux)
    return appels


def _jouer(fil, b):
    async def go():
        return [e async for e in boucle.conduire(fil, b, cle.Cle("sk", None, None))]
    return asyncio.run(go())


def test_le_fort_reprend_une_etape_qui_cree_un_agent_sans_avoir_parle(monkeypatch):
    b = Boite(frozenset({"oto_trigger"}), {"oto_trigger": object()},
              [{"name": "oto_trigger"}])
    appels = _boucle(monkeypatch, [
        {"content": [_appel("oto_trigger", op="create")], "stop_reason": "tool_use"},
        {"content": [{"type": "text", "text": "Voici l'agent proposé."}],
         "stop_reason": "end_turn"},
    ])
    fil = _fil(messages=[{"role": "user", "content": "Fais-en un agent chaque lundi"}])
    _jouer(fil, b)
    assert appels == [palier.DEFAUT, palier.FORT]
    assert fil["model"] == palier.FORT
    # The weaker model's step was dropped, not kept in the history.
    assert [m["role"] for m in fil["messages"]] == ["user", "assistant"]


def test_un_refus_est_repris_par_le_fort(monkeypatch):
    b = Boite(frozenset(), {}, [])
    appels = _boucle(monkeypatch, [
        {"content": [], "stop_reason": "refusal"},
        {"content": [{"type": "text", "text": "Bien sûr."}], "stop_reason": "end_turn"},
    ])
    fil = _fil(messages=[{"role": "user", "content": "q"}])
    _jouer(fil, b)
    assert appels == [palier.DEFAUT, palier.FORT]


def test_une_pause_du_serveur_relance_l_appel(monkeypatch):
    b = Boite(frozenset(), {}, [])
    appels = _boucle(monkeypatch, [
        {"content": [{"type": "server_tool_use", "id": "srv_1",
                      "name": "tool_search_tool_bm25", "input": {"query": "x"}}],
         "stop_reason": "pause_turn"},
        {"content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn"},
    ])
    fil = _fil(messages=[{"role": "user", "content": "q"}])
    evs = _jouer(fil, b)
    assert appels == [palier.DEFAUT, palier.DEFAUT]
    assert evs[-1]["type"] == "done" and not any(e["type"] == "error" for e in evs)


def test_une_conversation_d_avant_ne_change_jamais_de_modele(monkeypatch):
    b = Boite(frozenset(), {}, [])
    appels = _boucle(monkeypatch, [{"content": [], "stop_reason": "refusal"}])
    fil = _fil(model=None, messages=[{"role": "user", "content": "q"}])
    evs = _jouer(fil, b)
    assert appels == [palier.HERITE]
    assert any(e["type"] == "error" for e in evs)
