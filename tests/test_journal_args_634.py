"""#634 — les arguments d'un appel se LISENT : le détail les rend tels que journalisés,
la liste en rend les clés, et le contrat servi le dit.

Constat de la campagne (29/08/2026, 443 lectures de `GET /api/orgs/{id}/monitoring/
calls/{call_id}` en douze minutes) : le lecteur a conclu « arguments: {} » sur des
appels dont la colonne `tool_calls.args` est non vide. Rejoué ici sur la route servie
(table de routes réelle, adaptateur de capacités, vrai PostgreSQL) : la fiche porte
`call.args` — la clé journalisée, celle de `op=call` sur les deux consoles — et aucune
clé `arguments`. Un lecteur qui cherche `arguments` avec un défaut `{}` fabrique lui-même
l'objet vide. Ce que le contrat ne disait pas, il le dit désormais : `OrgCall.call` est
typé, `args` y est déclaré avec sa sémantique (tronqué, masqué, `null` = l'appel n'en
portait pas), et la liste porte `arg_keys` — les clés seulement, jamais le contenu —
pour que « cet appel portait-il un numéro d'entreprise ? » se réponde sans ouvrir chaque
fiche. Ce qu'on garde vert :

  1. le détail REST rend `args` tel que journalisé (tronqué à l'écriture, #582 masqué),
     par le même chemin que `oto_org_monitoring op=call` et `oto_admin_monitoring op=call` ;
  2. la liste rend `arg_keys` (clés triées, `[]` quand l'appel n'avait pas d'argument) et
     jamais `args` ;
  3. un secret masqué à l'écriture ne réapparaît sur aucune des deux vues ;
  4. le contrat servi (OpenAPI) déclare `args` et `arg_keys`, et aucun `arguments`.
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
CODE = "ABCDEFG"          # 7 caractères, la forme d'un code d'invitation


class _Claims:
    def __init__(self, sub: str):
        self.claims = {"sub": sub, "email": f"{sub}@journal-634.invalid", "name": sub}


class _Verifier:
    async def verify_token(self, token: str):
        return _Claims(token)


def _h(sub: str) -> dict:
    return {"Authorization": f"Bearer {sub}"}


@pytest.fixture(scope="module")
def live(pg_dsn):
    """Une base JETABLE et le vrai `init_db()` — jamais la base partagée du conteneur."""
    import os

    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    name = "oto_args634_" + uuid.uuid4().hex[:8]
    root = psycopg.connect(pg_dsn, autocommit=True)
    root.execute(f'CREATE DATABASE "{name}"')
    dsn = pg_dsn.rsplit("/", 1)[0] + "/" + name
    previous_url, previous_pool = os.environ.get("DATABASE_URL"), dbconn._pool
    os.environ["DATABASE_URL"] = dsn
    dbconn._pool = None
    try:
        from oto_mcp.db import init_db
        init_db()
        yield
    finally:
        if dbconn._pool is not None:
            dbconn._pool.close()
        dbconn._pool = previous_pool
        if previous_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous_url
        root.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        root.close()


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
    _ecrit("oto_org", {"op": "accept_invite", "code": CODE})   # `code` sera masqué
    _ecrit("slack_list_channels", {})                          # sans argument → NULL

    ids = {r["tool_name"]: r["id"] for r in db.list_tool_calls(org_id=oid, run_id=run)}
    assert set(ids) == {"fr_directors", "data_write", "oto_org", "slack_list_channels"}
    return {"org": oid, "admin": admin, "run": run, "ids": ids}


def _detail(client, journal, tool: str) -> dict:
    r = client.get(f"/api/orgs/{journal['org']}/monitoring/calls/{journal['ids'][tool]}",
                   headers=_h(journal["admin"]))
    assert r.status_code == 200, r.text
    return r.json()


# ── 1. le détail rend les arguments tels que journalisés ──────────────────────

def test_le_detail_rest_rend_args_tel_que_journalise_et_aucun_arguments(client, journal):
    call = _detail(client, journal, "fr_directors")["call"]
    assert call["args"] == {"siren": SIREN}, call
    assert "arguments" not in call, "la clé journalisée est `args` — pas d'alias qui vaudrait {}"
    assert call["tool"] == "fr_directors" and call["org_id"] == journal["org"]

    # Tronqué À L'ÉCRITURE : la fiche montre ce que le journal porte, pas le payload.
    call = _detail(client, journal, "data_write")["call"]
    assert call["args"]["namespace"] == "fiches" and call["args"]["id"] == "row-1"
    assert isinstance(call["args"]["row"], str) and call["args"]["row"].endswith("…")

    # Un appel sans argument : `null`, la valeur journalisée — jamais un `{}` fabriqué.
    call = _detail(client, journal, "slack_list_channels")["call"]
    assert "args" in call and call["args"] is None, call


def test_les_trois_faces_du_detail_lisent_le_meme_chemin(client, journal, monkeypatch):
    rest = _detail(client, journal, "fr_directors")["call"]
    cid, oid = journal["ids"]["fr_directors"], journal["org"]
    ctx = ResolvedCtx(sub=journal["admin"], org_id=oid)
    org_face = om._console(ctx, om.OrgMonitoringInput(org_id=oid, op="call", call_id=cid))
    admin_face = mon._monitoring(ctx, mon.MonitoringInput(op="call", call_id=cid))
    assert org_face["call"]["args"] == admin_face["call"]["args"] == rest["args"]

    # Et la route REST plateforme, servie elle aussi (opérateur stubbé, règle réelle).
    from oto_mcp.capabilities import _authz
    monkeypatch.setattr(_authz.access, "is_platform_operator", lambda s: True)
    r = client.get(f"/api/admin/monitoring/calls/{cid}", headers=_h(journal["admin"]))
    assert r.status_code == 200, r.text
    assert r.json()["call"]["args"] == rest["args"]


# ── 2. la liste rend les CLÉS des arguments, jamais le contenu ────────────────

def test_la_liste_rest_rend_arg_keys_jamais_le_contenu(client, journal):
    r = client.get(f"/api/orgs/{journal['org']}/monitoring/calls",
                   params={"run_id": journal["run"]}, headers=_h(journal["admin"]))
    assert r.status_code == 200, r.text
    rows = {c["tool_name"]: c for c in r.json()["calls"]}
    assert rows["fr_directors"]["arg_keys"] == ["siren"], rows["fr_directors"]
    assert rows["data_write"]["arg_keys"] == ["id", "namespace", "row"]
    assert rows["oto_org"]["arg_keys"] == ["code", "op"]
    assert rows["slack_list_channels"]["arg_keys"] == [], "sans argument : une liste vide, un FAIT"
    assert all("args" not in c for c in rows.values()), "la liste ne porte pas le contenu"
    assert SIREN not in r.text and "fiches" not in r.text and "row-1" not in r.text


def test_la_liste_mcp_rend_les_memes_cles(journal):
    ctx = ResolvedCtx(sub=journal["admin"], org_id=journal["org"])
    out = om._console(ctx, om.OrgMonitoringInput(org_id=journal["org"], op="calls",
                                                 run_id=journal["run"]))
    cles = {c["tool_name"]: c["arg_keys"] for c in out["calls"]}
    assert cles == {"fr_directors": ["siren"], "data_write": ["id", "namespace", "row"],
                    "oto_org": ["code", "op"], "slack_list_channels": []}, cles


# ── 3. un secret masqué à l'écriture ne réapparaît sur aucune vue (#582) ─────

def test_un_secret_masque_reste_masque_sur_les_deux_vues(client, journal):
    call = _detail(client, journal, "oto_org")["call"]
    assert call["args"]["op"] == "accept_invite"
    assert call["args"]["code"].startswith("#") and CODE not in call["args"]["code"], call
    r = client.get(f"/api/orgs/{journal['org']}/monitoring/calls",
                   params={"run_id": journal["run"]}, headers=_h(journal["admin"]))
    assert CODE not in r.text


# ── 4. le contrat servi déclare `args` et `arg_keys`, et aucun `arguments` ───

def test_le_contrat_openapi_declare_args_et_arg_keys():
    """Ce qu'un intégrateur lit AVANT d'appeler : la 200 du détail nomme `args`, celle
    de la liste nomme `arg_keys` — et ni l'une ni l'autre ne promet un `arguments`."""
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
    assert "args" in detail and "arguments" not in detail, sorted(detail)
