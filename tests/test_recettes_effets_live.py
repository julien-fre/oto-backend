"""`async` (soumettre puis collecter) et `push` (créer ou mettre à jour chez un tiers) sur
un vrai store, avec de faux outils aux noms des listes fermées."""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Optional

import pytest

SUB = "sub-recettes-fx"


@pytest.fixture(scope="module")
def compte(live):
    from oto_mcp import db
    db.upsert_user(SUB, email=f"{SUB}@acme.test", name=SUB)
    return SUB


@pytest.fixture
def serveur(compte, monkeypatch):
    from fastmcp import FastMCP

    from oto_mcp import access, session_org
    from oto_mcp.auth import hooks
    monkeypatch.setattr(access, "current_user_sub_or_raise", lambda: SUB)
    monkeypatch.setattr(hooks, "current_user_sub_from_token", lambda: SUB)
    etat = {"pret": False, "appels": [], "crm": {}, "lent": False}
    m = FastMCP("t-recettes-fx")

    @m.tool()
    def dropcontact_enrich(contacts: list[dict]) -> dict:
        etat["appels"].append(("submit", contacts[0].get("first_name")))
        session_org.note_call_trace(quantity=1)
        return {"request_id": f"req-{contacts[0].get('first_name')}"}

    @m.tool()
    def dropcontact_result(request_id: str) -> dict:
        etat["appels"].append(("collect", request_id))
        if not etat["pret"]:
            return {"done": False}
        nom = request_id.removeprefix("req-")
        email = [] if nom == "Nobody" else [{"email": f"{nom.lower()}@acme.test"}]
        return {"done": True, "profiles": [{"email": email}]}

    @m.tool()
    def hubspot_object(op: str = "search", object_type: Optional[str] = None,
                       object_id: Optional[str] = None, properties: Optional[dict] = None,
                       query: Optional[str] = None) -> dict:
        etat["appels"].append((op, dict(properties or {}), object_id, query))
        if etat["lent"] and op == "create":
            raise TimeoutError("upstream took too long")
        if op == "search":
            found = [{"id": i} for i, p in etat["crm"].items() if p.get("email") == query]
            return {"results": found}
        if op == "create":
            nouvel = f"hs{len(etat['crm']) + 1}"
            etat["crm"][nouvel] = dict(properties or {})
            return {"id": nouvel}
        etat["crm"].setdefault(object_id, {}).update(properties or {})
        return {"id": object_id}
    return m, etat


def _tableau(champs, lignes) -> int:
    from oto_mcp import db
    from oto_mcp.datastore.core import make_store
    nom = f"fx-{uuid.uuid4().hex[:6]}"
    ns_id = db.create_datastore("user", SUB, nom)
    store = make_store(SUB)
    store.set_schema(nom, {"fields": [{"key": c, "type": "text"} for c in champs]})
    store.write_rows(nom, list(lignes))
    return ns_id


def _lignes(ns, cle) -> dict:
    from oto_mcp.datastore.core import make_store
    return {l.get(cle): l for l in make_store(SUB).cursor_rows(str(ns), limit=50)["rows"]}


def _executer(m, corps, ns, **kw):
    from oto_mcp.recipes import moteur
    kw.setdefault("budget_s", 600)
    return asyncio.run(moteur.executer(corps, {}, fastmcp=m, sub=SUB, datastore=ns, **kw))


# ── async ────────────────────────────────────────────────────────────────────
def _asyn(**surcharge) -> dict:
    from oto_mcp.recipes import contrat
    corps = {"mode": "per_row", "tool": "dropcontact_enrich",
             "arguments": {"contacts": [{"first_name": "{{row.first_name}}",
                                         "company": "{{row.company}}"}]},
             "rows": {"status_column": "dc_status", "require": ["first_name"]},
             "async": {"id": "request_id", "ready": "done",
                       "collect": {"tool": "dropcontact_result",
                                   "arguments": {"request_id": "{{job.id}}"}}},
             "source": {"items": "profiles"}, "pick": "first",
             "map": {"email": "email[0].email"}, "limits": {"max_units": 20}}
    corps.update(surcharge)
    return contrat.valider(corps)


def test_async_soumet_puis_collecte_sans_jamais_resoumettre(serveur):
    m, etat = serveur
    ns = _tableau(["first_name", "email", "dc_status"],
                  [{"first_name": "Jane"}, {"first_name": "Nobody"}])
    recu = _executer(m, _asyn(), ns)
    assert recu["jobs"]["submitted"] == 2 and recu["next_step"]
    lignes = _lignes(ns, "first_name")
    assert lignes["Jane"]["dc_status"] == "submitted"
    assert json.loads(lignes["Jane"]["dc_status_job"])["id"] == "req-Jane"
    # Pas prêt : rien ne bouge, rien n'est resoumis.
    recu = _executer(m, _asyn(), ns)
    assert recu["jobs"] == {"submitted": 0, "collected": 0, "still_running": 2}
    etat["pret"] = True
    recu = _executer(m, _asyn(), ns)
    assert recu["jobs"]["collected"] == 2 and recu["rows"]["done"] == 1
    lignes = _lignes(ns, "first_name")
    assert (lignes["Jane"]["email"], lignes["Jane"]["dc_status"]) == ("jane@acme.test", "done")
    assert lignes["Nobody"]["dc_status"] == "not_found"
    assert [a for a in etat["appels"] if a[0] == "submit"] == [("submit", "Jane"),
                                                                ("submit", "Nobody")]


def test_un_travail_trop_vieux_est_perdu_sans_appel(serveur):
    m, etat = serveur
    vieux = json.dumps({"id": "req-Jane", "at": int(time.time()) - 7200})
    ns = _tableau(["first_name", "email", "dc_status", "dc_status_job"],
                  [{"first_name": "Jane", "dc_status": "submitted", "dc_status_job": vieux}])
    _executer(m, _asyn(), ns)
    assert _lignes(ns, "first_name")["Jane"]["dc_status"] == "failed:timeout"
    assert etat["appels"] == []


def test_l_epreuve_async_prouve_la_correspondance_sur_un_vrai_resultat(serveur):
    m, etat = serveur
    ns = _tableau(["first_name", "email", "dc_status"],
                  [{"first_name": "Jane"}, {"first_name": "Joe"}])
    recu = _executer(m, _asyn(), ns, ecrire=False)
    assert recu["stopped"] == "job_submitted" and recu["jobs"]["submitted"] == 1
    suite = _executer(m, _asyn(), ns, ecrire=False, reprise=recu["resume"])
    assert suite["stopped"] == "job_running"
    etat["pret"] = True
    fin = _executer(m, _asyn(), ns, ecrire=False, reprise=suite["resume"])
    assert fin["stopped"] is None and fin["rows_built"] == 1 and fin["fill"] == {"email": 1.0}
    assert all(l.get("dc_status") is None and l.get("email") is None
               for l in _lignes(ns, "first_name").values())


def test_async_exige_require_et_le_bon_collecteur():
    from oto_mcp.recipes import contrat
    with pytest.raises(contrat.RecetteInvalide) as e:
        _asyn(rows={"status_column": "dc_status"},
              **{"async": {"id": "request_id", "ready": "done",
                           "collect": {"tool": "fullenrich_result", "arguments": {}}}})
    probs = " ".join(e.value.problemes)
    assert "rows.require" in probs and "collect.tool" in probs


# ── push ─────────────────────────────────────────────────────────────────────
def _pousse(**surcharge) -> dict:
    from oto_mcp.recipes import contrat
    corps = {"mode": "push", "side_effects": True, "tool": "hubspot_object",
             "arguments": {"op": "create", "object_type": "contacts",
                           "properties": {"email": "{{row.email}}",
                                          "phone": "{{row.phone}}"}},
             "rows": {"status_column": "hs_status", "require": ["email"]},
             "id": {"column": "hs_id", "path": "id"}, "limits": {"max_units": 20}}
    corps.update(surcharge)
    return contrat.valider(corps)


CHAMPS = ["email", "phone", "hs_id", "hs_status"]


def test_push_cree_note_l_id_et_ne_cree_jamais_deux_fois(serveur):
    m, etat = serveur
    ns = _tableau(CHAMPS, [{"email": "a@acme.test"}, {"email": "b@acme.test", "phone": "1"}])
    recu = _executer(m, _pousse(), ns)
    assert recu["rows"] == {"failed": 0, "created": 2}
    lignes = _lignes(ns, "email")
    assert lignes["a@acme.test"]["hs_id"] and lignes["a@acme.test"]["hs_status"] == "created"
    # Une case vide n'est pas envoyée : jamais `phone: null` chez le tiers.
    assert etat["appels"][0][1] == {"email": "a@acme.test"}
    _executer(m, _pousse(), ns)
    assert len(etat["crm"]) == 2


def test_push_met_a_jour_une_ligne_qui_porte_son_id(serveur):
    m, etat = serveur
    etat["crm"]["hs9"] = {"email": "old@acme.test"}
    ns = _tableau(CHAMPS, [{"email": "new@acme.test", "hs_id": "hs9"}])
    recu = _executer(m, _pousse(), ns)
    assert recu["rows"]["exists"] == 1 and etat["appels"] == []
    ns = _tableau(CHAMPS, [{"email": "new@acme.test", "hs_id": "hs9"}])
    maj = {"arguments": {"op": "update", "object_type": "contacts",
                         "object_id": "{{row.hs_id}}",
                         "properties": {"email": "{{row.email}}"}}}
    recu = _executer(m, _pousse(update=maj), ns)
    assert recu["rows"]["updated"] == 1 and etat["crm"]["hs9"]["email"] == "new@acme.test"


def test_push_lie_une_fiche_trouvee_et_refuse_l_ambigu(serveur):
    m, etat = serveur
    etat["crm"].update({"hs1": {"email": "a@acme.test"}, "hs2": {"email": "dup@acme.test"},
                        "hs3": {"email": "dup@acme.test"}})
    ns = _tableau(CHAMPS, [{"email": "a@acme.test"}, {"email": "dup@acme.test"}])
    rech = {"arguments": {"op": "search", "object_type": "contacts",
                          "query": "{{row.email}}"}, "items": "results", "id_path": "id"}
    recu = _executer(m, _pousse(lookup=rech), ns)
    lignes = _lignes(ns, "email")
    assert lignes["a@acme.test"]["hs_id"] == "hs1" and lignes["a@acme.test"]["hs_status"] == "linked"
    assert lignes["dup@acme.test"]["hs_status"] == "failed:ambiguous"
    assert len(etat["crm"]) == 3 and recu["rows"]["linked"] == 1


def test_une_creation_a_l_issue_inconnue_n_est_jamais_refaite(serveur):
    m, etat = serveur
    etat["lent"] = True
    ns = _tableau(CHAMPS, [{"email": "a@acme.test"}])
    _executer(m, _pousse(), ns)
    assert _lignes(ns, "email")["a@acme.test"]["hs_status"] == "failed:unknown_outcome"
    etat["lent"] = False
    _executer(m, _pousse(), ns)
    assert [a[0] for a in etat["appels"]] == ["create"]


def test_la_marche_a_blanc_n_appelle_rien(serveur):
    m, etat = serveur
    ns = _tableau(CHAMPS, [{"email": "a@acme.test"}, {"email": "b@acme.test", "hs_id": "x"},
                           {"email": "c@acme.test", "phone": "2"}])
    recu = _executer(m, _pousse(), ns, ecrire=False)
    assert etat["appels"] == [] and recu["rows_built"] == 3
    assert recu["dry_run"]["would_create"] == 2 and recu["dry_run"]["would_skip"] == 1
    assert recu["dry_run"]["arguments_fill"]["properties.email"] == 2
    assert all(l.get("hs_status") is None for l in _lignes(ns, "email").values())


def test_le_contrat_de_push():
    from oto_mcp.recipes import contrat
    for surcharge, attendu in (
            ({"side_effects": None}, "side_effects"),
            ({"arguments": {"op": "delete", "object_type": "contacts"}}, "`delete`"),
            ({"arguments": {"op": "{{row.op}}"}}, "literally"),
            ({"tool": "hubspot_push_rows"}, "not a tool a recipe may push to"),
            ({"rows": {"status_column": "hs_status"}}, "rows.require"),
            ({"tool": "lemlist_create_lead", "arguments": {"campaign_id": "c"}},
             "allow_sending")):
        with pytest.raises(contrat.RecetteInvalide) as e:
            _pousse(**surcharge)
        assert attendu in " ".join(e.value.problemes), (surcharge, e.value.problemes)
    _pousse(tool="lemlist_create_lead", arguments={"campaign_id": "c"}, allow_sending=True)


def test_un_agent_heberge_ne_pilote_ni_push_ni_async(serveur, monkeypatch):
    from oto_mcp.capabilities import recipes
    from oto_mcp.capabilities._types import AuthzDenied, ResolvedCtx
    m, etat = serveur
    monkeypatch.setattr(recipes, "current_token_axes", lambda: {"token_kind": "delegation"})
    monkeypatch.setattr(recipes.tool_registry, "bound_instance", lambda: m)
    for corps in (_pousse(), _asyn()):
        with pytest.raises(AuthzDenied) as e:
            asyncio.run(recipes._executer(ResolvedCtx(sub=SUB, org_id=None),
                                          recipes.RecipeInput(op="run", datastore="1"),
                                          corps, ecrire=True))
        assert e.value.code == "side_effect_recipes_not_in_hosted_agents"
    assert etat["appels"] == []
