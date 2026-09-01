"""Vider le DERNIER élément d'une liste — l'effet, ou le refus, jamais le silence (#724).

Mesuré en production le 2026-09-01 entre 04:16 et 04:20 : dix `data_write(id=…,
row={'contacts': []})` sur des fiches clientes, dix 200, zéro retrait. Le geste
était le seul du payload — rien d'autre à poser — donc l'appel n'a strictement rien
fait et a répondu comme un succès. Découvert en relisant les fiches.

⚠️ **Le voisinage qu'il ne faut PAS casser.** Le même journal, la même nuit, montre
le geste DOMINANT de la flotte : la fiche entière réémise, où `contacts: []` veut
dire « l'enrichissement n'a rien trouvé » et non « efface » — c'est exactement ce que
#608 protège, et le protéger reste juste. La ligne de partage n'est donc pas le TYPE
de la valeur mais l'EFFET du geste : une écriture qui pose autre chose est un gabarit
à demi peuplé (on préserve, on le dit) ; une écriture qui ne pose plus RIEN après
arbitrage est une demande d'effacement qui ne peut pas aboutir (on refuse, on nomme
le geste qui marche).

Les deux faces sont éprouvées, sur la table de routes réelle et sur le tool monté :
une garde vraie d'un seul côté est une garde absente.
"""
from __future__ import annotations

import uuid

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient


class _Claims:
    def __init__(self, sub: str):
        self.claims = {"sub": sub, "email": f"{sub}@vide.invalid", "name": sub}


class _Verifier:
    async def verify_token(self, token: str):
        return _Claims(token)


SUB = "usr_vide"


def _h() -> dict:
    return {"Authorization": f"Bearer {SUB}"}


@pytest.fixture(scope="module")
def live(pg_dsn):
    import os

    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    name = "oto_vide_" + uuid.uuid4().hex[:8]
    root = psycopg.connect(pg_dsn, autocommit=True)
    root.execute(f'CREATE DATABASE "{name}"')
    dsn = pg_dsn.rsplit("/", 1)[0] + "/" + name
    previous_url, previous_pool = os.environ.get("DATABASE_URL"), dbconn._pool
    os.environ["DATABASE_URL"] = dsn
    dbconn._pool = None
    try:
        from oto_mcp.db import init_db
        init_db()
        from oto_mcp import db
        db.upsert_user(SUB, email=f"{SUB}@vide.invalid", name=SUB)
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


@pytest.fixture
def fiche(live):
    """Une fiche dont l'unique interlocuteur est à retirer — le cas de #724."""
    from oto_mcp import db
    from oto_mcp.datastore.core import make_store

    ns = "vivier-" + uuid.uuid4().hex[:6]
    ns_id = db.create_datastore_namespace("user", SUB, ns)
    row = make_store(SUB).append_row(
        ns, {"siren": "552032534", "contacts": ["a@exemple.invalid"], "statut": "nouveau"})
    return ns, ns_id, row["_id"]


async def _data_write(monkeypatch):
    """Le tool MCP tel qu'il est MONTÉ (`register` + `.fn`) — pas le store appelé à
    la main : ce qu'on éprouve est la face servie aux agents."""
    from fastmcp import FastMCP

    from oto_mcp import access
    from oto_mcp.tools import datastore as tools_ds
    monkeypatch.setattr(access, "current_user_sub_from_token", lambda: SUB)
    mcp = FastMCP("test")
    tools_ds.register(mcp)
    return (await mcp.get_tool("data_write")).fn


def _blob(ns_id: int, row_id: str) -> dict:
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        r = conn.execute("SELECT data FROM datastore_rows WHERE ns_id = %s AND row_id = %s",
                         (ns_id, row_id)).fetchone()
    return dict((r or {}).get("data") or {})


# ── le geste de #724 : un vide SEUL, qui ne pouvait rien faire ────────────────

@pytest.mark.asyncio
async def test_face_MCP_une_liste_vide_SEULE_est_refusee_en_nommant_le_geste(
        fiche, client, monkeypatch):
    """Le geste exact des dix retraits ratés. Il ne pose rien : il doit se solder
    par un refus qui NOMME la colonne et le geste qui vide, jamais par un 200."""
    from oto_mcp.mcp_errors import McpError

    data_write = await _data_write(monkeypatch)
    ns, ns_id, rid = fiche
    with pytest.raises(McpError) as e:
        data_write(namespace=ns, id=rid, row={"contacts": []})
    message = str(e.value)
    assert "contacts" in message, message
    assert "null" in message, \
        f"le refus doit dire PAR QUOI vider pour de bon : {message}"
    assert _blob(ns_id, rid)["contacts"] == ["a@exemple.invalid"], \
        "un refus n'efface rien"


def test_face_REST_une_liste_vide_SEULE_rend_400_et_pas_200(client, fiche):
    ns, ns_id, rid = fiche
    r = client.patch(f"/api/datastore/namespaces/{ns}/rows/{rid}", headers=_h(),
                     json={"contacts": []})
    assert r.status_code == 400, r.text
    corps = r.json()
    assert "contacts" in corps.get("detail", ""), corps
    assert "null" in corps.get("detail", ""), corps
    assert _blob(ns_id, rid)["contacts"] == ["a@exemple.invalid"]


def test_la_chaine_vide_et_lobjet_vide_SEULS_sont_refuses_pareil(client, fiche):
    """Le défaut n'est pas propre aux listes : tout vide non-`null` qui reste seul
    décrit la même intention impossible."""
    ns, _ns_id, rid = fiche
    for valeur in ("", {}):
        r = client.patch(f"/api/datastore/namespaces/{ns}/rows/{rid}", headers=_h(),
                         json={"statut": valeur})
        assert r.status_code == 400, (valeur, r.text)


def test_le_geste_qui_vide_pour_de_bon_marche_toujours(client, fiche):
    """Le refus n'a de sens que si la porte qu'il désigne est ouverte."""
    ns, ns_id, rid = fiche
    r = client.patch(f"/api/datastore/namespaces/{ns}/rows/{rid}", headers=_h(),
                     json={"contacts": None})
    assert r.status_code == 200, r.text
    assert _blob(ns_id, rid)["contacts"] is None
    assert r.json().get("valeurs_effacees"), \
        "un effacement réel se dit aussi — c'est lui qui a détruit quelque chose"


# ── le voisinage : ce que le refus ne doit PAS emporter ───────────────────────

@pytest.mark.asyncio
async def test_la_fiche_ENTIERE_reemise_avec_un_vide_passe_et_preserve(
        fiche, client, monkeypatch):
    """Le geste DOMINANT de la flotte (payload relevé en production le 01/09) :
    la fiche entière, où `contacts: []` est une source muette. Il pose autre chose,
    donc il fait quelque chose : il passe, la valeur survit, et le relevé le dit."""
    data_write = await _data_write(monkeypatch)
    ns, ns_id, rid = fiche
    out = data_write(namespace=ns, id=rid, row={
        "qualification": {"valeur": "indetermine", "comment": "registre — rien trouvé"},
        "contacts": [],
        "notes_verification": "registre consulté ; aucune page au nom de la structure",
    })
    assert out["contacts"] == ["a@exemple.invalid"], out
    assert out.get("valeurs_ignorees"), \
        "préserver en silence serait le défaut de #608 retourné"
    blob = _blob(ns_id, rid)
    assert blob["contacts"] == ["a@exemple.invalid"]
    assert blob["notes_verification"].startswith("registre consulté")


def test_reecrire_la_valeur_IDENTIQUE_reste_un_no_op_accepte(client, fiche):
    """Le round-trip qui réémet une valeur inchangée n'est pas une écriture sans
    effet au sens de ce refus : il POSE une valeur, elle se trouve être la même.
    Le refuser arrêterait la flotte (#623 → #625)."""
    ns, _ns_id, rid = fiche
    r = client.patch(f"/api/datastore/namespaces/{ns}/rows/{rid}", headers=_h(),
                     json={"statut": "nouveau"})
    assert r.status_code == 200, r.text


def test_un_vide_sur_une_colonne_DEJA_vide_sécrit_toujours(client, live):
    """Rien à perdre, rien à refuser : poser `[]` là où il n'y avait rien reste le
    chemin normal de création depuis un gabarit."""
    from oto_mcp import db
    from oto_mcp.datastore.core import make_store

    ns = "neuve-" + uuid.uuid4().hex[:6]
    ns_id = db.create_datastore_namespace("user", SUB, ns)
    rid = make_store(SUB).append_row(ns, {"siren": "552032534"})["_id"]
    r = client.patch(f"/api/datastore/namespaces/{ns}/rows/{rid}", headers=_h(),
                     json={"contacts": []})
    assert r.status_code == 200, r.text
    assert _blob(ns_id, rid)["contacts"] == []


def test_un_LOT_qui_porte_la_cle_metier_nest_jamais_refuse_par_cette_garde(live):
    """Le lot dédouble par la clé : elle est TOUJOURS posée, donc la fusion pose
    quelque chose. Un import de 500 lignes ne peut pas casser sur cette garde —
    c'est l'objection qui avait fait écarter un refus dur dans #608."""
    from oto_mcp import db
    from oto_mcp.datastore.core import make_store

    ns = "lot-" + uuid.uuid4().hex[:6]
    ns_id = db.create_datastore_namespace("user", SUB, ns)
    store = make_store(SUB)
    store.set_schema(ns, {"key": "siren", "fields": [{"key": "siren", "type": "text"}]})
    store.write_rows(ns, [{"siren": "552032534", "contacts": ["a@exemple.invalid"]}])
    out = store.write_rows(ns, [{"siren": "552032534", "contacts": []}])
    assert out["updated"] == 1, out
    rid = out["ids"][0]
    assert _blob(ns_id, rid)["contacts"] == ["a@exemple.invalid"], \
        "le lot préserve (#608), il ne refuse pas"
