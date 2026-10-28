"""Les demandes d'accès, par les VRAIES routes sur une VRAIE base
(`docs/demandes-d-acces.md`).

1. demander : la demande naît dans l'org de l'appel ; redemander rend celle qui attend ;
   ce qu'on a déjà, une cible inconnue ou d'une autre org sont refusés ;
2. qui décide : qui peut déjà accorder la voit dans `review`, les autres non ;
3. approuver ACCORDE, par le geste ordinaire, sous l'identité de qui approuve — pour
   chacun des quatre gestes (ressource, connecteur, outils d'un agent, équipe) ;
4. refuser, annuler, relancer ; une demande close ne se décide plus ; l'échéance.
"""
from __future__ import annotations

import uuid

import pytest

ADMIN = "sub-demandes-admin"
PROPRIO = "sub-demandes-proprio"
MOI = "sub-demandes-moi"
AUTRE = "sub-demandes-autre"
DEHORS = "sub-demandes-dehors"


@pytest.fixture(scope="module")
def monde(live):
    from oto_mcp import db, group_store, org_store
    for sub in (ADMIN, PROPRIO, MOI, AUTRE, DEHORS):
        db.upsert_user(sub, email=f"{sub}@demandes.invalid", name=sub)
    a = org_store.create_org("A demandes", created_by=ADMIN)
    org_store.add_org_member(a, ADMIN, "org_admin")
    for sub in (PROPRIO, MOI, AUTRE):
        org_store.add_org_member(a, sub)
    b = org_store.create_org("B demandes", created_by=DEHORS)
    org_store.add_org_member(b, DEHORS, "org_admin")
    org_store.add_org_member(b, MOI)
    return {"a": a, "b": b, "db": db, "group_store": group_store}


@pytest.fixture(scope="module")
def client(monde):
    from starlette.applications import Starlette
    from starlette.testclient import TestClient

    from oto_mcp.api import routes as api_routes

    class _Claims:
        def __init__(self, sub):
            self.claims = {"sub": sub, "email": f"{sub}@demandes.invalid", "name": sub}

    class _Verifier:
        async def verify_token(self, token):
            return _Claims(token)

    return TestClient(api_routes.ViewAsMiddleware(
        Starlette(routes=api_routes.make_routes(_Verifier(), mcp_instance=None)),
        verifier=_Verifier()))


def _h(sub: str, org: int) -> dict:
    return {"Authorization": f"Bearer {sub}", "X-Oto-Org": str(org)}


def _projet(monde, owner=PROPRIO) -> int:
    return int(monde["db"].create_project("user", owner, "p-" + uuid.uuid4().hex[:6],
                                          created_by=owner, context_org_id=monde["a"]))


def _demander(client, monde, body, sub=MOI, org=None):
    return client.post("/api/me/access-requests", json=body,
                       headers=_h(sub, org or monde["a"]))


def _boite(client, monde, sub, box="review", org=None):
    r = client.get(f"/api/me/access-requests?box={box}", headers=_h(sub, org or monde["a"]))
    assert r.status_code == 200, r.text
    return r.json()


def _geste(client, monde, rid, verbe, sub, body=None, org=None):
    return client.post(f"/api/me/access-requests/{rid}/{verbe}", json=body or {},
                       headers=_h(sub, org or monde["a"]))


# ── 1. demander ──────────────────────────────────────────────────────────────────

def test_demander_puis_redemander_rend_la_meme(client, monde):
    pid = _projet(monde)
    body = {"kind": "resource_access", "resource_type": "project", "resource_id": str(pid),
            "role": "editor", "note": "  pour la démo  "}
    r = _demander(client, monde, body)
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["created"] is True
    req = d["request"]
    assert (req["status"], req["mine"], req["can_decide"]) == ("pending", True, False)
    assert req["asked"] == {"role": "editor"} and req["note"] == "pour la démo"
    # Le nom d'un projet privé ne se lit pas en demandant : à qui décide seulement.
    assert req["target"]["resource_id"] == str(pid) and req["target_label"] is None
    vu = {x["id"]: x for x in _boite(client, monde, PROPRIO)["requests"]}
    assert vu[req["id"]]["target_label"].startswith("p-")
    again = _demander(client, monde, body).json()
    assert again["created"] is False and again["request"]["id"] == req["id"]
    sent = _boite(client, monde, MOI, "sent")
    assert req["id"] in [x["id"] for x in sent["requests"]] and sent["pending_count"] >= 1


def test_ce_qu_on_a_deja_ne_se_demande_pas(client, monde):
    pid = _projet(monde, owner=MOI)
    r = _demander(client, monde, {"kind": "resource_access", "resource_type": "project",
                                  "resource_id": str(pid)})
    assert (r.status_code, r.json().get("error")) == (409, "already_granted"), r.text


def test_une_cible_d_une_autre_org_est_inconnue(client, monde):
    pid = int(monde["db"].create_project("user", DEHORS, "x-" + uuid.uuid4().hex[:6],
                                         created_by=DEHORS, context_org_id=monde["b"]))
    for rid in (str(pid), "999999999"):
        r = _demander(client, monde, {"kind": "resource_access", "resource_type": "project",
                                      "resource_id": rid})
        assert (r.status_code, r.json().get("error")) == (404, "target_not_found"), r.text


def test_un_champ_manquant_est_nomme(client, monde):
    r = _demander(client, monde, {"kind": "team_join"})
    assert (r.status_code, r.json().get("error")) == (400, "missing_fields"), r.text


# ── 2 & 3. qui décide, et approuver accorde ────────────────────────────────────────

def test_ressource_le_proprietaire_decide_et_l_acces_est_accorde(client, monde):
    from oto_mcp import ownership
    pid = _projet(monde)
    req = _demander(client, monde, {"kind": "resource_access", "resource_type": "project",
                                    "resource_id": str(pid), "role": "editor"}).json()["request"]
    # Le propriétaire la voit ; un simple membre, non ; le demandeur ne décide pas la sienne.
    assert req["id"] in [x["id"] for x in _boite(client, monde, PROPRIO)["requests"]]
    assert req["id"] not in [x["id"] for x in _boite(client, monde, AUTRE)["requests"]]
    assert _geste(client, monde, req["id"], "approve", AUTRE).status_code == 403
    assert _geste(client, monde, req["id"], "approve", MOI).status_code == 403
    # Approuvé avec MOINS que demandé : lecteur.
    r = _geste(client, monde, req["id"], "approve", PROPRIO, {"role": "viewer", "note": "ok"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert (out["status"], out["decision"], out["decided_by"]) == (
        "approved", {"role": "viewer"}, PROPRIO)
    assert ownership.can_access(MOI, "project", str(pid), "read")
    assert not ownership.can_access(MOI, "project", str(pid), "write")
    # Accordé : le demandeur lit maintenant le nom dans ses demandes.
    sent = {x["id"]: x for x in _boite(client, monde, MOI, "sent")["requests"]}
    assert sent[req["id"]]["target_label"].startswith("p-")
    # Close : ne se décide plus.
    r = _geste(client, monde, req["id"], "approve", PROPRIO)
    assert (r.status_code, r.json().get("error")) == (409, "request_closed")


def test_connecteur_l_admin_l_active_pour_l_org(client, monde):
    from oto_mcp import providers
    from oto_mcp.connectors import activation as connector_activation
    a = monde["a"]
    nom, ferme = sorted(providers.REGISTRY)[:2]
    connector_activation.set_activation(nom, True, set_by=ADMIN)       # la plateforme l'ouvre
    connector_activation.set_activation(nom, False, org_id=a, set_by=ADMIN)  # l'org le coupe
    connector_activation.set_activation(ferme, False, set_by=ADMIN)    # la plateforme le ferme
    r = _demander(client, monde, {"kind": "connector_activation", "connector": ferme})
    assert (r.status_code, r.json().get("error")) == (409, "cut_above_org"), r.text
    req = _demander(client, monde, {"kind": "connector_activation", "connector": nom}).json()
    req = req["request"]
    assert req["id"] not in [x["id"] for x in _boite(client, monde, PROPRIO)["requests"]]
    assert req["id"] in [x["id"] for x in _boite(client, monde, ADMIN)["requests"]]
    r = _geste(client, monde, req["id"], "approve", ADMIN)
    assert r.status_code == 200, r.text
    assert connector_activation.is_exposed(nom, a)


def test_outils_d_un_agent_son_proprietaire_les_ajoute(client, monde):
    db = monde["db"]
    a = monde["a"]
    tid = db.create_trigger(a, PROPRIO, procedure="p", tz="Europe/Paris",
                            tools=["oto_whoami"], cron="0 9 * * *", label="veille")
    tid = int(tid["id"] if isinstance(tid, dict) else tid)
    from oto_mcp import ownership
    ownership.grant("runner_trigger", str(tid), "user", MOI, "read", granted_by=PROPRIO)
    r = _demander(client, monde, {"kind": "agent_tools", "trigger_id": tid,
                                  "tools": ["oto_whoami", "serper_search", "web_read"]})
    assert r.status_code == 201, r.text
    req = r.json()["request"]
    assert req["asked"] == {"tools": ["serper_search", "web_read"]}
    assert req["target_label"] == "veille"
    # Un éditeur de l'agent décide aussi.
    ownership.grant("runner_trigger", str(tid), "user", AUTRE, "write", granted_by=PROPRIO)
    assert req["id"] in [x["id"] for x in _boite(client, monde, AUTRE)["requests"]]
    r = _geste(client, monde, req["id"], "approve", PROPRIO, {"tools": ["nope"]})
    assert (r.status_code, r.json().get("error")) == (400, "tools_not_asked")
    r = _geste(client, monde, req["id"], "approve", PROPRIO, {"tools": ["serper_search"]})
    assert r.status_code == 200, r.text
    assert db.get_trigger(tid, a)["tools"] == ["oto_whoami", "serper_search"]


def test_equipe_le_chef_y_fait_entrer(client, monde):
    gs = monde["group_store"]
    gid = gs.create_group(monde["a"], "eq-" + uuid.uuid4().hex[:6], created_by=ADMIN)
    gs.add_group_member(gid, PROPRIO, "group_admin")
    req = _demander(client, monde, {"kind": "team_join", "group_id": gid}).json()["request"]
    assert req["id"] in [x["id"] for x in _boite(client, monde, PROPRIO)["requests"]]
    r = _geste(client, monde, req["id"], "approve", PROPRIO)
    assert r.status_code == 200, r.text
    assert gs.get_group_role(gid, MOI) == "group_member"
    r = _demander(client, monde, {"kind": "team_join", "group_id": gid})
    assert (r.status_code, r.json().get("error")) == (409, "already_granted")


# ── 4. refuser, annuler, relancer, échéance, périmètre ──────────────────────────────

def test_refuser_annuler_relancer(client, monde):
    pid = _projet(monde)
    body = {"kind": "resource_access", "resource_type": "project", "resource_id": str(pid)}
    req = _demander(client, monde, body).json()["request"]
    r = _geste(client, monde, req["id"], "remind", MOI)
    assert r.status_code == 200 and r.json() == {"reminded": False}  # trop tôt
    assert _geste(client, monde, req["id"], "cancel", AUTRE).status_code == 403
    r = _geste(client, monde, req["id"], "decline", PROPRIO, {"note": "pas encore"})
    assert r.status_code == 200, r.text
    assert (r.json()["status"], r.json()["decision_note"]) == ("declined", "pas encore")
    # Refusée, elle libère : redemander en crée une neuve, que le demandeur annule.
    again = _demander(client, monde, body).json()
    assert again["created"] is True and again["request"]["id"] != req["id"]
    r = _geste(client, monde, again["request"]["id"], "cancel", MOI)
    assert r.status_code == 200 and r.json()["status"] == "cancelled"


def test_une_demande_echue_ne_se_decide_plus_et_se_redemande(client, monde):
    db = monde["db"]
    pid = _projet(monde)
    body = {"kind": "resource_access", "resource_type": "project", "resource_id": str(pid)}
    req = _demander(client, monde, body).json()["request"]
    with db._connect() as conn:
        conn.execute("UPDATE access_requests SET expires_at = now() - interval '1 minute' "
                     "WHERE id = %s", (req["id"],))
    assert req["id"] not in [x["id"] for x in _boite(client, monde, PROPRIO)["requests"]]
    sent = {x["id"]: x for x in _boite(client, monde, MOI, "sent")["requests"]}
    assert sent[req["id"]]["status"] == "expired"
    r = _geste(client, monde, req["id"], "approve", PROPRIO)
    assert (r.status_code, r.json().get("error")) == (409, "request_closed")
    again = _demander(client, monde, body).json()
    assert again["created"] is True and again["request"]["id"] != req["id"]


def test_une_demande_ne_se_lit_que_depuis_son_org(client, monde):
    pid = _projet(monde)
    req = _demander(client, monde, {"kind": "resource_access", "resource_type": "project",
                                    "resource_id": str(pid)}).json()["request"]
    assert req["id"] not in [x["id"] for x in
                             _boite(client, monde, MOI, "sent", org=monde["b"])["requests"]]
    r = _geste(client, monde, req["id"], "cancel", MOI, org=monde["b"])
    assert (r.status_code, r.json().get("error")) == (404, "request_not_found")


# ── 5. les autres familles de ressource, et l'accès à un agent ──────────────────────

def _approuver_ressource(client, monde, rtype, rid, decideur=PROPRIO):
    r = _demander(client, monde, {"kind": "resource_access", "resource_type": rtype,
                                  "resource_id": str(rid)})
    assert r.status_code == 201, r.text
    req = r.json()["request"]
    vu = {x["id"]: x for x in _boite(client, monde, decideur)["requests"]}
    assert req["id"] in vu, vu
    r = _geste(client, monde, req["id"], "approve", decideur)
    assert r.status_code == 200, r.text
    return vu[req["id"]]


def test_un_tableau_personnel_d_un_collegue(client, monde):
    from oto_mcp import ownership
    ns = "t-" + uuid.uuid4().hex[:6]
    ns_id = monde["db"].create_datastore("user", PROPRIO, ns, context_org_id=monde["a"])
    vu = _approuver_ressource(client, monde, "datastore_namespace", ns_id)
    assert vu["target_label"] == ns
    assert ownership.can_access(MOI, ownership.TYPE_RESSOURCE_DATASTORE, str(ns_id), "read")


def test_une_page_d_un_projet_personnel(client, monde):
    from oto_mcp import ownership
    pid = _projet(monde)
    doc = monde["db"].create_doc(pid, "page-" + uuid.uuid4().hex[:6], created_by=PROPRIO)
    vu = _approuver_ressource(client, monde, "doc", doc)
    assert vu["target_label"].startswith("page-")
    assert ownership.can_access(MOI, "doc", str(doc), "read")


def test_acces_a_un_agent_le_proprietaire_partage(client, monde):
    from oto_mcp import ownership
    db = monde["db"]
    tid = db.create_trigger(monde["a"], PROPRIO, procedure="p", tz="Europe/Paris",
                            tools=["oto_whoami"], cron="0 9 * * *", label="privé")
    tid = int(tid["id"] if isinstance(tid, dict) else tid)
    r = _demander(client, monde, {"kind": "agent_access", "trigger_id": tid})
    assert r.status_code == 201, r.text
    req = r.json()["request"]
    assert (req["asked"], req["target_label"]) == ({"role": "editor"}, None)
    # Gouverner l'agent : son propriétaire et l'admin, pas un simple membre.
    assert req["id"] in [x["id"] for x in _boite(client, monde, ADMIN)["requests"]]
    assert req["id"] not in [x["id"] for x in _boite(client, monde, AUTRE)["requests"]]
    r = _geste(client, monde, req["id"], "approve", PROPRIO, {"role": "manager"})
    assert (r.status_code, r.json().get("error")) == (400, "invalid_role")
    r = _geste(client, monde, req["id"], "approve", PROPRIO)
    assert r.status_code == 200, r.text
    assert r.json()["decision"] == {"role": "editor"}
    assert ownership.can_access(MOI, "runner_trigger", str(tid), "write")
    r = _demander(client, monde, {"kind": "agent_access", "trigger_id": tid})
    assert (r.status_code, r.json().get("error")) == (409, "already_granted")
