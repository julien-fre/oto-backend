"""Un tableau NAÎT avec son schéma (oto#124, suite).

**Le fait.** À partir du 21/10/2026, une écriture dans une colonne non déclarée est
refusée. Un tableau neuf naît sans colonne : sa première écriture est refusée, et il
fallait un `data_patch_schema` préalable — deux gestes pour le plus banal du produit.
`data_create_datastore` et `POST /api/datastores` prennent donc `schema`, le MÊME objet
que `data_set_schema` / `PUT …/schema`, posé par le même chemin (`_poser_schema`).

**Atomique pour l'appelant** : un schéma refusé ne laisse aucun tableau derrière lui —
sinon la reprise, sous le même nom, prendrait `datastore_exists`.

Joué sur les deux faces contre un vrai PostgreSQL, en lisant la BASE ; la date du refus
est reculée par le RÉGLAGE, jamais par l'horloge.
"""
from __future__ import annotations

import uuid

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from oto_mcp.datastore import colonnes_non_declarees as cnd

PASSEE = "2026-01-01"
SUB = "usr_creation124"
SCHEMA = {"fields": [{"key": "siren", "type": "text"}, {"key": "nom", "type": "text"}],
          "key": "siren"}
# Une clé que le vocabulaire fermé n'admet pas : refusée par `validate_schema_def`.
REFUSE = {"fields": [{"key": "nom", "type": "text", "read_only": True}]}
# Les clés d'une création sans schéma, telles qu'elles étaient servies avant.
CLES_SANS_SCHEMA = {"datastore", "id", "ns_id", "url", "owner_type", "owner_id",
                    "is_personal"}


class _Claims:
    def __init__(self, sub: str):
        self.claims = {"sub": sub, "email": f"{sub}@creation.invalid", "name": sub}


class _Verifier:
    async def verify_token(self, token: str):
        return _Claims(token)


def _h() -> dict:
    return {"Authorization": f"Bearer {SUB}"}


def _nom() -> str:
    return "c-" + uuid.uuid4().hex[:8]


# ── le texte servi (sans base) ───────────────────────────────────────────────

def test_le_texte_servi_dit_que_c_est_le_geste_normal_et_le_date(monkeypatch):
    import asyncio

    from fastmcp import FastMCP

    from oto_mcp.capabilities.registry import CAPABILITIES
    from oto_mcp.tools import datastore as tools_ds
    mcp = FastMCP("t")
    tools_ds.register(mcp)
    outil = asyncio.run(mcp.get_tool("data_create_datastore"))
    assert "schema" in outil.parameters["properties"]
    cap = next(c for c in CAPABILITIES if c.key == "me.datastore.create_datastore")
    assert "schema" in cap.Input.model_fields
    for texte in (outil.description, cap.description):
        assert "<<" not in texte
        assert "From 2026-10-21 on" in texte and "born with NO column" in texte
        assert "pass `schema`" in texte and "`data_set_schema`" in texte
        assert "normal way to create a table you fill" in texte
        assert "the table is NOT created" in texte
    monkeypatch.setenv(cnd.ENV_COLONNE_NON_DECLAREE_REFUSEE_LE, PASSEE)
    apres = cnd.description_creation()
    assert "From " not in apres and "is REFUSED" in apres and "pass `schema`" in apres


# ── la base ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def live(pg_module_dsn):
    import os

    from oto_mcp.db import _conn as dbconn
    previous_url, previous_pool = os.environ.get("DATABASE_URL"), dbconn._pool
    os.environ["DATABASE_URL"] = pg_module_dsn
    dbconn._pool = None
    try:
        from oto_mcp.db import init_db
        init_db()
        from oto_mcp import db
        db.upsert_user(SUB, email=f"{SUB}@creation.invalid", name=SUB)
        yield
    finally:
        if dbconn._pool is not None:
            dbconn._pool.close()
        dbconn._pool = previous_pool
        if previous_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous_url


@pytest.fixture(scope="module")
def client(live):
    from oto_mcp.api import routes as api_routes
    return TestClient(Starlette(routes=api_routes.make_routes(_Verifier(), mcp_instance=None)))


@pytest.fixture
def outils(live, monkeypatch):
    import asyncio

    from fastmcp import FastMCP

    from oto_mcp import access
    from oto_mcp.tools import datastore as tools_ds
    monkeypatch.setattr(access, "current_user_sub_from_token", lambda: SUB)
    mcp = FastMCP("test")
    tools_ds.register(mcp)
    return {nom: asyncio.run(mcp.get_tool(nom)).fn
            for nom in ("data_create_datastore", "data_write")}


@pytest.fixture
def apres(monkeypatch):
    monkeypatch.setenv(cnd.ENV_COLONNE_NON_DECLAREE_REFUSEE_LE, PASSEE)


def _en_base(nom: str):
    from oto_mcp import db
    ligne = db.get_datastore("user", SUB, nom)
    return None if ligne is None else db.get_datastore_by_id(int(ligne["id"]))


def _creer(face, outils, client, nom, schema=None, *, avec_cle=True):
    """Crée par la face demandée ; rend (code, corps). `avec_cle=False` n'envoie pas
    `schema` du tout — la forme de tous les appelants d'avant."""
    if face == "mcp":
        kw = {"datastore": nom, **({"schema": schema} if avec_cle else {})}
        try:
            return 201, outils["data_create_datastore"](**kw)
        except Exception as e:  # McpError : le message est le corps
            return 400, {"message": e.error.message}
    corps = {"datastore": nom, **({"schema": schema} if avec_cle else {})}
    r = client.post("/api/datastores", headers=_h(), json=corps)
    return r.status_code, r.json()


@pytest.mark.parametrize("face", ["mcp", "rest"])
def test_le_tableau_nait_avec_son_schema_et_sa_cle(face, outils, client, apres):
    from oto_mcp import db
    nom = _nom()
    code, corps = _creer(face, outils, client, nom, SCHEMA)
    assert code == 201, corps
    en_base = _en_base(nom)
    assert en_base is not None and en_base["schema"] == SCHEMA
    # La réponse parle comme `data_set_schema`, l'identité reste celle de la création.
    assert corps["schema"] == SCHEMA and corps["enforced"]
    assert corps["ns_id"] == corps["id"] == en_base["id"] and corps["is_personal"]
    # La clé métier déclarée porte son index UNIQUE dès la naissance.
    assert db.datastore_has_key_index(int(en_base["id"]))
    # Le refus des colonnes non déclarées est ARMÉ : la première écriture passe quand
    # même, parce que ses colonnes sont déclarées.
    if face == "mcp":
        rep = outils["data_write"](datastore=str(en_base["id"]),
                                   row={"siren": "123456789", "nom": "A"})
        assert not any("non déclarée" in n for n in rep.get("notices") or [])
    else:
        r = client.post(f"/api/datastores/{en_base['id']}/rows", headers=_h(),
                        json={"siren": "123456789", "nom": "A"})
        assert r.status_code == 201, r.text


@pytest.mark.parametrize("face", ["mcp", "rest"])
def test_un_schema_refuse_ne_cree_pas_le_tableau(face, outils, client):
    nom = _nom()
    code, corps = _creer(face, outils, client, nom, REFUSE)
    assert code == 400, corps
    texte = corps.get("message") or corps.get("detail") or str(corps)
    assert "read_only" in texte and "readonly" in texte, corps
    if face == "rest":
        assert corps["error"] == "invalid_schema", corps
    else:
        assert "was NOT created" in texte
    assert _en_base(nom) is None
    # Rien de laissé derrière : la reprise, corrigée, sous le MÊME nom, passe.
    code, corps = _creer(face, outils, client, nom, SCHEMA)
    assert code == 201, corps
    assert _en_base(nom)["schema"] == SCHEMA


@pytest.mark.parametrize("face", ["mcp", "rest"])
@pytest.mark.parametrize("avec_cle", [False, True], ids=["absent", "null"])
def test_sans_schema_rien_ne_change(face, avec_cle, outils, client):
    nom = _nom()
    code, corps = _creer(face, outils, client, nom, None, avec_cle=avec_cle)
    assert code == 201, corps
    assert set(corps) == CLES_SANS_SCHEMA, corps
    assert _en_base(nom)["schema"] is None


def test_le_nom_pris_reste_un_conflit_et_ne_touche_pas_l_existant(outils, client):
    nom = _nom()
    assert _creer("rest", outils, client, nom, SCHEMA)[0] == 201
    code, corps = _creer("rest", outils, client, nom, {"fields": [{"key": "autre"}]})
    assert code == 409 and corps["error"] == "datastore_exists", corps
    # Le conflit tombe AVANT la pose : le schéma du tableau en place est intact.
    assert _en_base(nom)["schema"] == SCHEMA
