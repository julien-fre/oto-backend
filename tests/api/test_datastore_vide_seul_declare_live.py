"""Le sens d'une liste vide dépend de CE QUI L'ACCOMPAGNE (#724, arbitré le 01/09/2026).

Deux lectures s'affrontaient, chacune vraie sur une moitié de la population. La mesure
du journal de production (30 jours glissants au 2026-09-01) les départage :

  · **574** écritures unitaires portent une liste vide, sur 43 444 ;
  · **562 (98 %)** réémettent la fiche ENTIÈRE — plusieurs colonnes dans le même
    appel — et **12 (2 %)** ne portent que la liste vide, seule ;
  · sur les 324 couples (appel, colonne) résolubles en base, **105 visaient une
    colonne encore peuplée** — donc auraient effacé. Décomposés : **104 sont des
    fiches réémises, 1 seule est une liste vide seule.**

D'où la règle, qui n'est ni « le vide efface » ni « le vide n'efface jamais » :

  **liste vide SEULE** (rien d'autre posé) ⟹ **déclaration** — « on a cherché, il n'y
  a personne ». Elle PREND EFFET. Personne n'écrit `{contacts: []}` tout seul par
  accident, et c'est le besoin d'origine : retirer le dernier contact d'une fiche.

  **liste vide DANS une fiche réémise** (d'autres colonnes posées) ⟹ « rien trouvé »,
  ce que rend une source muette. Elle NE DÉPLACE PAS une valeur en place (#608). C'est
  cette moitié-là qui protège les 104 — le test qui la garde vaut 104 appels par mois.

L'effacement, quand il a lieu, est DIT (`valeurs_effacees` : la colonne et ce qu'elle
portait). ⚠️ C'est une TRACE, pas un garde-fou : on vient d'établir qu'un relevé logé
dans le corps d'une réponse réussie n'oblige personne à le lire. Le geste est
légitime ; ce qu'on doit, c'est de quoi le retrouver.

Les deux faces sont éprouvées, sur la table de routes réelle et sur le tool monté :
un comportement vrai d'un seul côté est un comportement absent.
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


# ── le cas SEUL : une déclaration, qui prend effet ────────────────────────────

@pytest.mark.asyncio
async def test_face_MCP_une_liste_vide_SEULE_efface_et_le_dit(fiche, client, monkeypatch):
    """Le geste des dix retraits perdus. Il ne pose rien d'autre : l'intention est
    établie, donc il aboutit — et il nomme ce qu'il a emporté."""
    data_write = await _data_write(monkeypatch)
    ns, ns_id, rid = fiche

    out = data_write(namespace=ns, id=rid, row={"contacts": []})

    assert _blob(ns_id, rid)["contacts"] == [], \
        "retirer le dernier contact d'une fiche DOIT aboutir — c'est l'issue elle-même"
    effaces = out.get("valeurs_effacees")
    assert effaces, f"un effacement muet ne se retrouve pas : {out}"
    assert [(e["champ"], e["valeur"]) for e in effaces] \
        == [("contacts", ["a@exemple.invalid"])], effaces


def test_face_REST_une_liste_vide_SEULE_efface_et_le_dit(client, fiche):
    ns, ns_id, rid = fiche
    r = client.patch(f"/api/datastore/namespaces/{ns}/rows/{rid}", headers=_h(),
                     json={"contacts": []})
    assert r.status_code == 200, r.text
    assert _blob(ns_id, rid)["contacts"] == []
    assert r.json().get("valeurs_effacees"), r.json()


def test_la_chaine_vide_et_lobjet_vide_SEULS_declarent_pareil(client, fiche):
    """La règle porte sur la FORME du geste, pas sur le type de la valeur : un vide
    seul est une déclaration, qu'il soit `[]`, `""` ou `{}`."""
    ns, ns_id, rid = fiche
    r = client.patch(f"/api/datastore/namespaces/{ns}/rows/{rid}", headers=_h(),
                     json={"statut": ""})
    assert r.status_code == 200, r.text
    assert _blob(ns_id, rid)["statut"] == "", r.text


def test_le_null_nomme_efface_toujours(client, fiche):
    """L'autre porte reste ouverte, inchangée."""
    ns, ns_id, rid = fiche
    r = client.patch(f"/api/datastore/namespaces/{ns}/rows/{rid}", headers=_h(),
                     json={"contacts": None})
    assert r.status_code == 200, r.text
    assert _blob(ns_id, rid)["contacts"] is None
    assert r.json().get("valeurs_effacees")


# ── le cas ACCOMPAGNÉ : 104 appels par mois en dépendent ──────────────────────

@pytest.mark.asyncio
async def test_la_fiche_ENTIERE_reemise_avec_un_vide_NE_LEFFACE_PAS(
        fiche, client, monkeypatch):
    """LE test qui vaut 104 appels par mois (mesure du 01/09, 30 j).

    Payload relevé en production : la fiche entière, où `contacts: []` veut dire
    « l'enrichissement n'a rien trouvé ». Élargir l'effacement à cette forme
    détruirait de la donnée cliente — c'est la moitié de l'arbitrage qui ne bouge pas.
    """
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
    assert "valeurs_effacees" not in out, "rien n'a été détruit ici"
    blob = _blob(ns_id, rid)
    assert blob["contacts"] == ["a@exemple.invalid"]
    assert blob["notes_verification"].startswith("registre consulté")


def test_face_REST_la_fiche_reemise_NE_LEFFACE_PAS_non_plus(client, fiche):
    ns, ns_id, rid = fiche
    r = client.patch(f"/api/datastore/namespaces/{ns}/rows/{rid}", headers=_h(),
                     json={"contacts": [], "statut": "traite"})
    assert r.status_code == 200, r.text
    assert _blob(ns_id, rid)["contacts"] == ["a@exemple.invalid"], r.text
    assert r.json().get("valeurs_ignorees"), r.json()


def test_deux_vides_ensemble_et_rien_dautre_restent_une_declaration(client, fiche):
    """« Rien d'autre posé » se juge sur le GESTE entier, pas colonne par colonne :
    deux vides qui ne s'accompagnent que l'un l'autre déclarent tous les deux."""
    ns, ns_id, rid = fiche
    r = client.patch(f"/api/datastore/namespaces/{ns}/rows/{rid}", headers=_h(),
                     json={"contacts": [], "statut": ""})
    assert r.status_code == 200, r.text
    blob = _blob(ns_id, rid)
    assert blob["contacts"] == [] and blob["statut"] == "", blob


# ── le voisinage qui ne doit pas bouger ───────────────────────────────────────

def test_reecrire_la_valeur_IDENTIQUE_reste_un_no_op_accepte(client, fiche):
    """Le round-trip qui réémet une valeur inchangée POSE une valeur : ce n'est pas
    un vide, et le refuser arrêterait la flotte (#623 → #625)."""
    ns, _ns_id, rid = fiche
    r = client.patch(f"/api/datastore/namespaces/{ns}/rows/{rid}", headers=_h(),
                     json={"statut": "nouveau"})
    assert r.status_code == 200, r.text


def test_un_vide_sur_une_colonne_DEJA_vide_ne_dit_rien(client, live):
    """Rien à perdre, rien à annoncer : poser `[]` là où il n'y avait rien reste le
    chemin normal de création depuis un gabarit, et ne produit aucun relevé."""
    from oto_mcp import db
    from oto_mcp.datastore.core import make_store

    ns = "neuve-" + uuid.uuid4().hex[:6]
    ns_id = db.create_datastore_namespace("user", SUB, ns)
    rid = make_store(SUB).append_row(ns, {"siren": "552032534"})["_id"]
    r = client.patch(f"/api/datastore/namespaces/{ns}/rows/{rid}", headers=_h(),
                     json={"contacts": []})
    assert r.status_code == 200, r.text
    assert _blob(ns_id, rid)["contacts"] == []
    assert "valeurs_effacees" not in r.json(), r.json()


def test_un_LOT_qui_porte_la_cle_metier_est_une_REEMISSION_donc_preserve(live):
    """Le lot dédouble par la clé : elle est TOUJOURS posée, donc une row de lot est
    par construction une réémission — jamais une déclaration. C'est ce qui garantit
    qu'un import de 500 lignes ne peut pas vider une colonne par un gabarit."""
    from oto_mcp import db
    from oto_mcp.datastore.core import make_store

    ns = "lot-" + uuid.uuid4().hex[:6]
    ns_id = db.create_datastore_namespace("user", SUB, ns)
    store = make_store(SUB)
    store.set_schema(ns, {"key": "siren", "fields": [{"key": "siren", "type": "text"}]})
    store.write_rows(ns, [{"siren": "552032534", "contacts": ["a@exemple.invalid"]}])
    out = store.write_rows(ns, [{"siren": "552032534", "contacts": []}])
    assert out["updated"] == 1, out
    assert _blob(ns_id, out["ids"][0])["contacts"] == ["a@exemple.invalid"], \
        "le lot préserve (#608) — la clé métier posée en fait une réémission"
