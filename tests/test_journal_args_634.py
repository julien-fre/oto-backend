"""#634 — les arguments d'un appel se LISENT : le détail les rend tels que journalisés,
la liste en rend les clés, et le contrat servi le dit.

Constat de la campagne (29/08/2026, 443 lectures de `GET /api/orgs/{id}/monitoring/
calls/{call_id}` en douze minutes) : le lecteur a conclu « arguments: {} » sur des
appels dont la colonne `tool_calls.args` est non vide. Rejoué ici sur la route servie
(table de routes réelle, adaptateur de capacités, vrai PostgreSQL) : la fiche porte
`call.args` — la clé journalisée — et aucune clé `arguments`. Un lecteur qui cherche
`arguments` avec un défaut `{}` fabrique lui-même l'objet vide.

⚠️ **Périmètre resserré le 15/09/2026** (oto-backend#563, décision d'Alexis) : `args`
n'est plus lu QUE par la supervision PLATEFORME (`oto_admin_monitoring op=call`,
`GET /api/admin/monitoring/calls/{call_id}`) — la face ORG (`oto_org_monitoring
op=call`, `GET /api/orgs/{id}/monitoring/calls/{call_id}`) ne les rend PLUS DU TOUT,
volontairement (les arguments peuvent porter des PII). Ce fichier vérifie donc le
comportement #634 (troncature, masquage, `null` ≠ `{}`) sur la face qui les garde, et
vérifie EN PLUS que la face org n'en rend aucune trace. Ce qu'on garde vert :

  1. le détail PLATEFORME (MCP + REST) rend `args` tel que journalisé (tronqué à
     l'écriture, #582 masqué) — les deux faces plateforme concordent ;
  2. le détail ORG (MCP + REST) ne rend PLUS `args` du tout, sur aucune des deux faces ;
  3. la liste (org, seule face qui existe pour ce grain) rend `arg_keys` (clés triées,
     `[]` quand l'appel n'avait pas d'argument) et jamais `args` ;
  4. un secret masqué à l'écriture ne réapparaît ni sur la fiche plateforme ni sur la
     liste org ;
  5. le contrat servi (OpenAPI) : la fiche ORG ne déclare plus `args` (la route admin
     n'a jamais eu de schéma structuré — fait préexistant, pas une régression de ce
     lot), la liste déclare `arg_keys` et aucun `arguments`.
"""
from __future__ import annotations

import uuid

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from oto_mcp.capabilities import monitoring as mon
from oto_mcp.capabilities import org_monitoring as om
from oto_mcp.capabilities._types import ResolvedCtx

SIREN = "106974637"
# oto-backend#560 (15/09/2026) : le code court d'invitation a été retiré, seul le
# token subsiste comme paramètre sensible d'`oto_org op=accept_invite`.
TOKEN = "inv_tok_abcdef0123456789"


class _Claims:
    def __init__(self, sub: str):
        self.claims = {"sub": sub, "email": f"{sub}@journal-634.invalid", "name": sub}


class _Verifier:
    async def verify_token(self, token: str):
        return _Claims(token)


def _h(sub: str) -> dict:
    return {"Authorization": f"Bearer {sub}"}


@pytest.fixture(scope="module")
def client(live):
    from oto_mcp.api import routes as api_routes
    return TestClient(Starlette(routes=api_routes.make_routes(_Verifier(), mcp_instance=None)))


@pytest.fixture(scope="module")
def journal(live):
    """Quatre appels d'un même run, écrits comme le sink les écrit : `truncated_args`
    (tronqué + masqué par la déclaration de l'outil) puis `insert_tool_call`."""
    from oto_mcp import calllog, db, org_store

    admin = "usr_634_admin"
    db.upsert_user(admin, email=f"{admin}@journal-634.invalid", name=admin)
    oid = org_store.create_org("Org 634", created_by=admin)
    org_store.add_org_member(oid, admin, "org_admin")
    org_store.set_active_org(admin, oid)
    run = uuid.uuid4().hex

    def _ecrit(tool: str, arguments: dict) -> None:
        db.insert_tool_call({"sub": admin, "kind": "mcp", "ok": True, "tool": tool,
                             "args": calllog.truncated_args(arguments, tool=tool),
                             "org_id": oid, "run_id": run})

    _ecrit("fr_directors", {"siren": SIREN})
    _ecrit("data_write", {"namespace": "fiches", "id": "row-1",
                          "row": {"nom": "x" * 400}})          # `row` sera tronqué
    _ecrit("oto_org", {"op": "accept_invite", "token": TOKEN})  # `token` sera masqué
    _ecrit("slack_list_channels", {})                          # sans argument → NULL

    ids = {r["tool_name"]: r["id"] for r in db.list_tool_calls(org_id=oid, run_id=run)}
    assert set(ids) == {"fr_directors", "data_write", "oto_org", "slack_list_channels"}
    return {"org": oid, "admin": admin, "run": run, "ids": ids}


def _detail(client, journal, tool: str) -> dict:
    """Fiche ORG — ne rend plus `args` depuis le 15/09/2026 (oto-backend#563)."""
    r = client.get(f"/api/orgs/{journal['org']}/monitoring/calls/{journal['ids'][tool]}",
                   headers=_h(journal["admin"]))
    assert r.status_code == 200, r.text
    return r.json()


def _detail_plateforme(client, journal, tool: str, monkeypatch) -> dict:
    """Fiche PLATEFORME — seule face qui rend encore `args` (oto-backend#563)."""
    from oto_mcp.capabilities import _authz
    monkeypatch.setattr(_authz.access, "is_platform_operator", lambda s: True)
    r = client.get(f"/api/admin/monitoring/calls/{journal['ids'][tool]}",
                   headers=_h(journal["admin"]))
    assert r.status_code == 200, r.text
    return r.json()


# ── 1. le détail PLATEFORME rend les arguments tels que journalisés ───────────

def test_le_detail_plateforme_rend_args_tel_que_journalise_et_aucun_arguments(client, journal, monkeypatch):
    call = _detail_plateforme(client, journal, "fr_directors", monkeypatch)["call"]
    assert call["args"] == {"siren": SIREN}, call
    assert "arguments" not in call, "la clé journalisée est `args` — pas d'alias qui vaudrait {}"
    assert call["tool"] == "fr_directors" and call["org_id"] == journal["org"]

    # Tronqué À L'ÉCRITURE : la fiche montre ce que le journal porte, pas le payload.
    call = _detail_plateforme(client, journal, "data_write", monkeypatch)["call"]
    assert call["args"]["namespace"] == "fiches" and call["args"]["id"] == "row-1"
    assert isinstance(call["args"]["row"], str) and call["args"]["row"].endswith("…")

    # Un appel sans argument : `null`, la valeur journalisée — jamais un `{}` fabriqué.
    call = _detail_plateforme(client, journal, "slack_list_channels", monkeypatch)["call"]
    assert "args" in call and call["args"] is None, call


def test_le_detail_org_ne_rend_plus_args_du_tout(client, journal):
    """oto-backend#563 : la garde réelle est dans le handler (`_call`), pas dans le
    modèle Output= (qui ne filtre rien à l'exécution) — vérifié ici sur la VRAIE route
    servie, pas juste sur le modèle Pydantic isolé (cf. test_org_monitoring.py pour
    cette seconde preuve, plus rapide mais qui ne passe pas par le vrai PostgreSQL)."""
    call = _detail(client, journal, "fr_directors")["call"]
    assert "args" not in call, call


def test_les_deux_faces_plateforme_concordent_et_la_face_org_n_a_plus_rien(client, journal, monkeypatch):
    cid, oid = journal["ids"]["fr_directors"], journal["org"]
    ctx = ResolvedCtx(sub=journal["admin"], org_id=oid)

    # Les deux faces PLATEFORME (MCP + REST) rendent le même `args`.
    admin_face = mon._monitoring(ctx, mon.MonitoringInput(op="call", call_id=cid))
    rest_admin = _detail_plateforme(client, journal, "fr_directors", monkeypatch)["call"]
    assert admin_face["call"]["args"] == rest_admin["args"] == {"siren": SIREN}

    # Les deux faces ORG (MCP + REST) n'ont plus `args` du tout.
    org_face = om._console(ctx, om.OrgMonitoringInput(org_id=oid, op="call", call_id=cid))
    assert "args" not in org_face["call"], org_face["call"]
    rest_org = _detail(client, journal, "fr_directors")["call"]
    assert "args" not in rest_org, rest_org


# ── 2. la liste rend les CLÉS des arguments, jamais le contenu ────────────────

def test_la_liste_rest_rend_arg_keys_jamais_le_contenu(client, journal):
    r = client.get(f"/api/orgs/{journal['org']}/monitoring/calls",
                   params={"run_id": journal["run"]}, headers=_h(journal["admin"]))
    assert r.status_code == 200, r.text
    rows = {c["tool_name"]: c for c in r.json()["calls"]}
    assert rows["fr_directors"]["arg_keys"] == ["siren"], rows["fr_directors"]
    assert rows["data_write"]["arg_keys"] == ["id", "namespace", "row"]
    assert rows["oto_org"]["arg_keys"] == ["op", "token"]
    assert rows["slack_list_channels"]["arg_keys"] == [], "sans argument : une liste vide, un FAIT"
    assert all("args" not in c for c in rows.values()), "la liste ne porte pas le contenu"
    assert SIREN not in r.text and "fiches" not in r.text and "row-1" not in r.text


def test_la_liste_mcp_rend_les_memes_cles(journal):
    ctx = ResolvedCtx(sub=journal["admin"], org_id=journal["org"])
    out = om._console(ctx, om.OrgMonitoringInput(org_id=journal["org"], op="calls",
                                                 run_id=journal["run"]))
    cles = {c["tool_name"]: c["arg_keys"] for c in out["calls"]}
    assert cles == {"fr_directors": ["siren"], "data_write": ["id", "namespace", "row"],
                    "oto_org": ["op", "token"], "slack_list_channels": []}, cles


# ── 3. un secret masqué à l'écriture ne réapparaît sur aucune vue (#582) ─────

def test_un_secret_masque_reste_masque_sur_les_deux_vues(client, journal, monkeypatch):
    call = _detail_plateforme(client, journal, "oto_org", monkeypatch)["call"]
    assert call["args"]["op"] == "accept_invite"
    assert call["args"]["token"].startswith("#") and TOKEN not in call["args"]["token"], call
    r = client.get(f"/api/orgs/{journal['org']}/monitoring/calls",
                   params={"run_id": journal["run"]}, headers=_h(journal["admin"]))
    assert TOKEN not in r.text


# ── 4. le contrat servi ────────────────────────────────────────────────────

def test_le_contrat_openapi_ne_declare_plus_args_cote_org(monkeypatch):
    """La fiche ORG (seule route de ce grain à porter un schéma OpenAPI structuré —
    la route admin n'en a jamais eu, fait préexistant, pas une régression de #563) ne
    déclare plus `args` du tout. La liste continue de déclarer `arg_keys`, jamais
    `args` ni `arguments`."""
    from oto_mcp import openapi
    doc = openapi.build()
    schemas = doc["components"]["schemas"]

    def _200(path: str) -> dict:
        return doc["paths"][path]["get"]["responses"]["200"]["content"]["application/json"]["schema"]

    liste = _200("/api/orgs/{id}/monitoring/calls")["properties"]["calls"]["items"]
    ligne = schemas[liste["$ref"].rsplit("/", 1)[1]]["properties"]
    assert "arg_keys" in ligne and "args" not in ligne, sorted(ligne)

    fiche = _200("/api/orgs/{id}/monitoring/calls/{call_id}")["properties"]["call"]
    assert "$ref" in fiche, f"`call` doit être typé, pas un objet opaque : {fiche}"
    detail = schemas[fiche["$ref"].rsplit("/", 1)[1]]["properties"]
    assert "args" not in detail and "arguments" not in detail, sorted(detail)
