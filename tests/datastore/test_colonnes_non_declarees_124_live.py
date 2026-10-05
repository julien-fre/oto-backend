"""Écrire dans une colonne NON DÉCLARÉE ne la crée plus (oto#124) — le préavis daté.

**Le fait.** Une écriture qui nommait une colonne inconnue la créait à la volée, et le
schéma d'un tableau s'étendait au fil des écritures. **Décidé le 05/10/2026 : une seule
règle, plus de modes.** À partir de `COLONNE_NON_DECLAREE_REFUSEE_LE`, une colonne que
le schéma ne déclare pas est REFUSÉE (`unknown_column`), sur tous les tableaux, quel que
soit `unknown_columns`, tableau sans schéma compris ; d'ici là elle est créée et la
réponse l'avertit, datée, avec le geste (`data_patch_schema`).

Chaque face est jouée AVANT (la veille, gréée par le `conftest`) et APRÈS (la date
reculée par le RÉGLAGE, jamais l'horloge), contre un vrai PostgreSQL : on lit la BASE,
pas seulement ce que l'appel a bien voulu rendre.
"""
from __future__ import annotations

import json
import time
import uuid

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from oto_mcp.datastore import colonnes_non_declarees as cnd

PASSEE = "2026-01-01"
SUB = "usr_colonnes124"
SCHEMA = {"fields": [{"key": "nom", "type": "text"}, {"key": "ville", "type": "text"}]}


class _Claims:
    def __init__(self, sub: str):
        self.claims = {"sub": sub, "email": f"{sub}@colonnes.invalid", "name": sub}


class _Verifier:
    async def verify_token(self, token: str):
        return _Claims(token)


def _h() -> dict:
    return {"Authorization": f"Bearer {SUB}"}


@pytest.fixture
def apres(monkeypatch):
    monkeypatch.setenv(cnd.ENV_COLONNE_NON_DECLAREE_REFUSEE_LE, PASSEE)


# ── la date, le réglage, le prédicat, le texte servi (sans base) ─────────────

def test_un_reglage_illisible_leve_au_lieu_de_retomber_sur_le_defaut(monkeypatch):
    monkeypatch.setenv(cnd.ENV_COLONNE_NON_DECLAREE_REFUSEE_LE, "21 octobre")
    with pytest.raises(ValueError, match=cnd.ENV_COLONNE_NON_DECLAREE_REFUSEE_LE):
        cnd.refus_arme()


def test_la_veille_rien_n_est_arme_et_le_reglage_deplace_la_date(monkeypatch):
    assert cnd.COLONNE_NON_DECLAREE_REFUSEE_LE.isoformat() == "2026-10-21"
    assert not cnd.refus_arme()
    monkeypatch.setenv(cnd.ENV_COLONNE_NON_DECLAREE_REFUSEE_LE, PASSEE)
    assert cnd.refus_arme()


def test_le_predicat_juge_ce_qui_est_pose_contre_ce_qui_est_declare():
    # Sans schéma, ou sans colonne déclarée : TOUTE colonne posée est non déclarée.
    assert cnd.non_declarees(None, {"b": 1, "a": 2}) == ["a", "b"]
    assert cnd.non_declarees({"key": "a"}, {"a": 1}) == ["a"]
    # Une couche d'une colonne déclarée n'est pas une colonne.
    assert cnd.non_declarees(SCHEMA, {"nom": "x", "nom.comment": "y", "age": 3}) == ["age"]
    # Le lot : clés BRUTES, base des clés pointées et par rang, `null` et `_…` exclus.
    lot = [{"nom": "a", "contacts[+]": {"x": 1}}, {"site.comment": "c", "_id": "r1"},
           {"ville.comment": "ok", "efface": None, "vide": {}}, "pas un objet"]
    assert cnd.non_declarees_du_lot(SCHEMA, lot) == ["contacts", "site"]


def test_le_texte_servi_est_derive_de_la_date(monkeypatch):
    avant = cnd.description_ecriture()
    assert "From 2026-10-21 on" in avant and "Until then" in avant
    assert "`unknown_column`" in avant and "data_patch_schema" in avant
    assert "from 2026-10-21 on" in cnd.description_schema()
    monkeypatch.setenv(cnd.ENV_COLONNE_NON_DECLAREE_REFUSEE_LE, "2027-03-02")
    assert "From 2027-03-02 on" in cnd.description_ecriture()
    monkeypatch.setenv(cnd.ENV_COLONNE_NON_DECLAREE_REFUSEE_LE, PASSEE)
    apres_ = cnd.description_ecriture()
    assert "is REFUSED" in apres_ and "Until then" not in apres_
    assert "until" not in cnd.description_schema().split("still decides")[0]


def test_la_description_servie_dit_la_regle_et_la_date():
    """`data_write`, `data_set_schema`, `data_patch_schema` et les faces REST d'écriture
    disent la règle et sa date — le texte servi pilote l'agent."""
    import asyncio

    from fastmcp import FastMCP

    from oto_mcp.capabilities.datastore import columns
    from oto_mcp.capabilities.registry import CAPABILITIES
    from oto_mcp.tools import datastore as tools_ds
    mcp = FastMCP("t")
    tools_ds.register(mcp)
    for nom in ("data_set_schema", "data_write"):
        desc = asyncio.run(mcp.get_tool(nom)).description
        assert "<<" not in desc, nom
        assert "2026-10-21" in desc and "does NOT declare is REFUSED" in desc, nom
    patch = next(c for c in columns.CAPABILITIES if c.key == "me.datastore.patch_schema")
    assert "does NOT declare is REFUSED" in patch.description
    assert "2026-10-21" in patch.description
    assert "unknown_columns" not in patch.Input.model_fields, "retiré (oto#124)"
    for cle in ("me.datastore.append_row", "me.datastore.write_rows",
                "me.datastore.update_row"):
        cap = next(c for c in CAPABILITIES if c.key == cle)
        assert "From 2026-10-21 on" in cap.description, cle
        assert "unknown_column" in {e.code for e in cap.errors}, cle


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
        db.upsert_user(SUB, email=f"{SUB}@colonnes.invalid", name=SUB)
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
def data_write(live, monkeypatch):
    import asyncio

    from fastmcp import FastMCP

    from oto_mcp import access
    from oto_mcp.tools import datastore as tools_ds
    monkeypatch.setattr(access, "current_user_sub_from_token", lambda: SUB)
    mcp = FastMCP("test")
    tools_ds.register(mcp)
    return asyncio.run(mcp.get_tool("data_write")).fn


def _table(schema=SCHEMA, lignes=()):
    """Un tableau neuf, ses lignes posées AVANT le geste jugé (la veille, donc sans
    refus) — rend (nom, ns_id, [_id])."""
    from oto_mcp import db
    from oto_mcp.datastore.core import make_store
    ns = "t-" + uuid.uuid4().hex[:6]
    ns_id = db.create_datastore("user", SUB, ns)
    store = make_store(SUB)
    if schema is not None:
        # oto#124 : `unknown_columns` ne se pose plus — un réglage STOCKÉ s'écrit en
        # base, comme il subsiste sur les tableaux d'avant son retrait.
        store.set_schema(ns, {k: v for k, v in schema.items() if k != "unknown_columns"})
        if "unknown_columns" in schema:
            db.set_datastore_schema(ns_id, schema)
    ids = [store.append_row(ns, dict(ligne))["_id"] for ligne in lignes]
    return ns, ns_id, ids


def _base(ns_id: int) -> list[dict]:
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        rows = conn.execute("SELECT row_id, data FROM datastore_rows WHERE ns_id = %s "
                            "ORDER BY created_at, row_id", (ns_id,)).fetchall()
    return [{"_id": r["row_id"], **r["data"]} for r in rows]


def _refus_mcp(data_write, **kw) -> str:
    from oto_mcp.mcp_errors import McpError
    with pytest.raises(McpError) as exc:
        data_write(**kw)
    return exc.value.error.message


def _avertissement(rep: dict) -> str:
    return next(n for n in rep.get("notices") or [] if "non déclarée" in n)


# ── AVANT la date : écrite, et dite ──────────────────────────────────────────

@pytest.mark.parametrize("schema", [None, SCHEMA, {**SCHEMA, "unknown_columns": "create"},
                                    {**SCHEMA, "unknown_columns": "report"}],
                         ids=["sans schéma", "create par défaut", "create explicite",
                              "report"])
def test_avant_la_colonne_est_creee_et_l_avertissement_est_date(
        client, data_write, schema):
    """Tous les réglages, tableau sans schéma compris : la colonne naît, la réponse le
    dit avec la DATE, la colonne et le geste."""
    for face in ("mcp", "rest"):
        ns, ns_id, _ = _table(schema)
        ligne = {"nom": "A", "age": 41}
        if face == "mcp":
            rep = data_write(datastore=ns, row=ligne)
        else:
            r = client.post(f"/api/datastores/{ns}/rows", headers=_h(), json=ligne)
            assert r.status_code == 201, r.text
            rep = r.json()
        texte = _avertissement(rep)
        assert "`age`" in texte and "À partir du 21 octobre 2026" in texte, face
        assert "REFUSÉ (`unknown_column`)" in texte and "data_patch_schema" in texte
        assert '{"key": "age"}' in texte
        # Sans schéma, `nom` n'est pas déclarée non plus : nommée elle aussi.
        assert ("`nom`" in texte) is (schema is None), face
        assert _base(ns_id)[0]["age"] == 41, face


def test_avant_une_colonne_declaree_ne_dit_rien(client, data_write):
    ns, _, _ = _table()
    rep = data_write(datastore=ns, row={"nom": "A", "nom.comment": "source : registre"})
    assert not any("non déclarée" in n for n in rep.get("notices") or [])


def test_avant_le_lot_dit_une_seule_phrase_pour_toutes_ses_colonnes(client, data_write):
    ns, ns_id, _ = _table()
    lot = [{"nom": "A", "age": 1}, {"nom": "B", "taille": 2}, {"nom": "C", "age": 3}]
    rep = data_write(datastore=ns, rows=lot)
    phrases = [n for n in rep["notices"] if "non déclarée" in n]
    assert len(phrases) == 1 and "`age`, `taille`" in phrases[0]
    r = client.post(f"/api/datastores/{ns}/rows/batch", headers=_h(), json={"rows": lot})
    assert r.status_code == 200, r.text
    assert len([n for n in r.json()["notices"] if "non déclarée" in n]) == 1
    assert len(_base(ns_id)) == 6


def test_avant_le_patch_par_id_est_averti(client, data_write):
    ns, _, ids = _table(lignes=[{"nom": "A"}])
    r = client.patch(f"/api/datastores/{ns}/rows/{ids[0]}", headers=_h(),
                     json={"score": 3})
    assert r.status_code == 200, r.text
    assert "`score`" in _avertissement(r.json())
    rep = data_write(datastore=ns, id=ids[0], row={"note": "x"})
    assert "`note`" in _avertissement(rep)


def test_avant_l_upload_signe_et_l_import_avertissent(live, monkeypatch):
    from oto_mcp import upload_tokens
    ns, ns_id, _ = _table()
    corps = b'{"nom": "A", "age": 1}\n{"nom": "B", "age": 2}\n'
    cible = {"kind": "datastore", "ns_id": ns_id, "namespace": ns, "format": "ndjson"}
    rep = upload_tokens.materialize(SUB, cible, corps, "application/x-ndjson")
    assert rep["inserted"] == 2 and "`age`" in _avertissement(rep)
    monkeypatch.setattr(upload_tokens, "IMPORT_SLICE", 1)
    rep = upload_tokens.import_rows(SUB, cible, [{"nom": "C", "rang": 1},
                                                 {"nom": "D", "rang": 2}],
                                    deadline=time.monotonic() + 60)
    assert rep["done"] and len([n for n in rep["notices"] if "non déclarée" in n]) == 1


# ── APRÈS la date : refusée, rien n'est écrit ────────────────────────────────

@pytest.mark.parametrize("schema", [None, SCHEMA, {**SCHEMA, "unknown_columns": "report"},
                                    {**SCHEMA, "unknown_columns": "reject"}],
                         ids=["sans schéma", "create", "report", "reject"])
def test_apres_la_ligne_seule_est_refusee_avec_son_code(client, data_write, apres,
                                                         schema):
    ns, ns_id, _ = _table(schema)
    ligne = {"nom": "A", "age": 41}
    msg = _refus_mcp(data_write, datastore=ns, row=ligne)
    r = client.post(f"/api/datastores/{ns}/rows", headers=_h(), json=ligne)
    assert r.status_code == 400, r.text
    corps = r.json()
    assert corps["error"] == "unknown_column"
    assert "age" in corps["details"]["colonnes"]
    for texte in (msg, corps["detail"]):
        assert "`age`" in texte and "rien n'a été écrit" in texte
        assert "Depuis le 1er janvier 2026" in texte  # la date EN VIGUEUR, réglée
        assert "data_patch_schema" in texte and "PATCH /api/datastores/" in texte
    if schema is None:
        assert "ne déclare encore aucune colonne" in corps["detail"]
    else:
        assert "Colonnes du tableau : `nom`, `ville`" in corps["detail"]
    assert _base(ns_id) == []


def test_apres_declarer_la_colonne_ouvre_l_ecriture(client, data_write, apres):
    ns, ns_id, _ = _table(None)
    r = client.patch(f"/api/datastores/{ns}/schema", headers=_h(),
                     json={"fields": [{"key": "nom"}, {"key": "age", "type": "number"}]})
    assert r.status_code == 200, r.text
    rep = data_write(datastore=ns, row={"nom": "A", "age": 41})
    assert not any("non déclarée" in n for n in rep.get("notices") or [])
    assert _base(ns_id)[0]["age"] == 41


def test_apres_le_lot_est_refuse_entier_avant_sa_premiere_ligne(client, data_write,
                                                                 apres):
    ns, ns_id, _ = _table()
    lot = [{"nom": "A"}, {"nom": "B", "age": 2}]
    msg = _refus_mcp(data_write, datastore=ns, rows=lot)
    r = client.post(f"/api/datastores/{ns}/rows/batch", headers=_h(), json={"rows": lot})
    assert r.status_code == 400 and r.json()["error"] == "unknown_column", r.text
    for texte in (msg, r.json()["detail"]):
        assert "ce lot est refusé ENTIER" in texte and "`age`" in texte
    assert _base(ns_id) == []


def test_apres_le_patch_juge_le_geste_pas_le_passe(client, monkeypatch):
    """Une ligne qui porte une colonne ANCIENNE non déclarée reste écrivable sur ses
    colonnes déclarées, et la colonne ancienne s'efface (`null`) ; la réécrire, non."""
    ns, ns_id, ids = _table(lignes=[{"nom": "A", "vieille": "v"}])   # la veille
    monkeypatch.setenv(cnd.ENV_COLONNE_NON_DECLAREE_REFUSEE_LE, PASSEE)
    url = f"/api/datastores/{ns}/rows/{ids[0]}"
    r = client.patch(url, headers=_h(), json={"ville": "Lyon"})
    assert r.status_code == 200, r.text
    r = client.patch(url, headers=_h(), json={"vieille": "w"})
    assert r.status_code == 400 and r.json()["error"] == "unknown_column", r.text
    r = client.patch(url, headers=_h(), json={"vieille": None})
    assert r.status_code == 200, r.text
    ligne = _base(ns_id)[0]
    assert ligne.get("vieille") is None and ligne["ville"] == "Lyon"


def test_apres_une_couche_mal_ecrite_est_reconnue(client, apres):
    ns, _, _ = _table()
    r = client.post(f"/api/datastores/{ns}/rows", headers=_h(),
                    json={"nom": "A", "nom_comment": "source"})
    assert r.status_code == 400 and r.json()["error"] == "unknown_column", r.text
    assert r.json()["details"]["expected_column"] == "nom.comment"
    assert "COUCHE dont le nom s'écrit avec un POINT" in r.json()["detail"]


def test_apres_l_upload_et_l_import_sont_refuses_avant_la_premiere_ligne(
        live, apres, monkeypatch):
    from oto_mcp import upload_tokens
    ns, ns_id, _ = _table()
    cible = {"kind": "datastore", "ns_id": ns_id, "namespace": ns, "format": "ndjson"}
    corps = b'{"nom": "A"}\n{"nom": "B", "age": 2}\n'
    with pytest.raises(upload_tokens.UploadError) as e:
        upload_tokens.materialize(SUB, cible, corps, "application/x-ndjson")
    assert (e.value.status, e.value.code) == (400, "unknown_column")
    monkeypatch.setattr(upload_tokens, "IMPORT_SLICE", 1)
    with pytest.raises(upload_tokens.UploadError) as e:
        upload_tokens.import_rows(SUB, cible, [{"nom": "A"}, {"nom": "B", "age": 2}],
                                  deadline=time.monotonic() + 60)
    assert (e.value.code, e.value.details["written"]) == ("unknown_column", 0)
    assert _base(ns_id) == []


def test_l_import_ndjson_declare_ses_cles_neuves_typees(live):
    """`oto_import` déclare les en-têtes CSV neufs ; le NDJSON déclare ses clés neuves,
    typées d'après leurs valeurs — la règle du gel (`types_inferes`)."""
    from oto_mcp import upload_tokens
    corps = (b'{"nom": "A", "age": 1, "actif": true, "vu": "2026-01-02"}\n'
             b'{"nom": "B", "age": "?", "x.comment": "c", "_id": "r"}\n')
    parsed = upload_tokens.parse_import(corps, "ndjson", SCHEMA, declare_columns=True)
    assert parsed["new_columns"] == [{"key": "age"}, {"key": "actif", "type": "bool"},
                                     {"key": "vu", "type": "date"}]
    assert upload_tokens.parse_import(corps, "ndjson", SCHEMA)["new_columns"] == []
