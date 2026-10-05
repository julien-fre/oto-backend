"""oto#124 — le format déclaré fait contrat sur TOUS les tableaux, préavis au 21/10/2026.

Décidé le 05/10/2026 : toujours refuser, plus aucun réglage. La validation complète
(`options` de tête, forme de `text`/`url`/`object`/`list`/`enum`, couches inconnues,
sous-records fermés) ne dépend plus d'`unknown_columns` : elle s'applique partout à la
date. D'ici là, sur un tableau qui ne la portait pas, l'écriture passe et `notices` dit
chaque faute (colonne, valeur, règle), la date et le geste — sur toutes les faces.

**On juge ce que le geste ÉCRIT, jamais ce que la ligne porte déjà** : une ligne en
faute s'écrit sur ses autres colonnes, avant comme après la date ; sa faute se dit
(`hors_type`), elle ne refuse rien. Les données des tenants ne sont pas touchées.

AVANT = la veille (gréée par le `conftest`) ; APRÈS = le LENDEMAIN de la date. On lit
la BASE, pas seulement ce que l'appel a bien voulu rendre.
"""
from __future__ import annotations

import datetime as dt
import time
import uuid

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from oto_mcp.datastore import schema as S
from oto_mcp.datastore import validation_complete as vc

SUB = "usr_validation124"
SCHEMA = {"fields": [
    {"key": "nom", "type": "text"},
    {"key": "statut", "type": "enum", "options": ["a", "b"]},
    {"key": "etape", "type": "text", "options": ["x", "y"]},
    {"key": "site", "type": "url"},
    {"key": "fiche", "type": "object", "fields": [{"key": "ville", "type": "text"}]},
    {"key": "tags", "type": "list", "of": {"type": "text"}},
    {"key": "contacts", "type": "list",
     "of": {"type": "object", "fields": [{"key": "nom", "type": "text"}]}},
    {"key": "autre", "type": "text"},
]}

#: Une faute par famille : `(famille, colonne posée, valeur, ce que la faute dit)`.
FAUTES = [
    ("options d'un enum", "statut", "z", "valeur 'z' hors options (a, b)"),
    ("options d'un texte", "etape", "w", "valeur 'w' hors options (x, y)"),
    ("forme text", "nom", 42, "nom: attendu text, reçu int"),
    ("forme url", "site", "www.x.fr", "site: attendu une URL http(s), reçu 'www.x.fr'"),
    ("forme object", "fiche", "Lyon", "fiche: attendu object, reçu str"),
    ("forme list", "tags", "a,b", "tags: attendu list, reçu str"),
    ("forme enum", "statut", ["a"], "statut: attendu une valeur d'énumération"),
    ("couche inconnue", "autre", {"valeur": "v", "commentaire": "c"},
     "autre: sous-champ(s) inconnu(s) 'commentaire'"),
    ("sous-record fermé", "contacts", [{"nom": "A", "perso": "x"}],
     "contacts[0].perso: attribut non déclaré"),
]
IDS = [f[0] for f in FAUTES]
#: La ligne qui porte TOUTES les fautes à la fois (une colonne par famille).
#: Sans la couche inconnue : une couche mal nommée est déjà refusée à l'écriture sur
#: TOUS les tableaux (`couches`, « n'est pas une couche ») — elle ne vit qu'en base.
EN_FAUTE = {"statut": "z", "etape": "w", "nom": 42, "site": "www.x.fr", "fiche": "Lyon",
            "tags": "a,b", "contacts": [{"nom": "A", "perso": "x"}]}


@pytest.fixture
def lendemain(monkeypatch):
    """La bascule passée d'un jour : le lendemain de la date, épinglé."""
    monkeypatch.setattr(vc, "_aujourdhui",
                        lambda: vc.VALIDATION_COMPLETE_LE + dt.timedelta(days=1))


def _preavis(schema, row, **kw):
    out: list = []
    return S.validate_row(schema, row, preavis=out, **kw), out


# ── la règle, sans base ──────────────────────────────────────────────────────

def test_la_date_le_reglage_et_un_reglage_illisible(monkeypatch):
    assert vc.VALIDATION_COMPLETE_LE.isoformat() == "2026-10-21"
    assert not vc.bascule_faite()
    monkeypatch.setenv(vc.ENV_VALIDATION_COMPLETE_LE, "2026-01-01")
    assert vc.bascule_faite()
    monkeypatch.setenv(vc.ENV_VALIDATION_COMPLETE_LE, "21 octobre")
    with pytest.raises(ValueError, match=vc.ENV_VALIDATION_COMPLETE_LE):
        vc.bascule_faite()


@pytest.mark.parametrize("famille,col,valeur,dit", FAUTES, ids=IDS)
def test_avant_chaque_famille_passe_et_se_dit(famille, col, valeur, dit):
    errs, preavis = _preavis(SCHEMA, {col: valeur})
    assert errs == [], (famille, errs)
    assert len(preavis) == 1 and dit in preavis[0], (famille, preavis)


@pytest.mark.parametrize("famille,col,valeur,dit", FAUTES, ids=IDS)
def test_au_lendemain_chaque_famille_est_refusee(famille, col, valeur, dit, lendemain):
    errs, preavis = _preavis(SCHEMA, {col: valeur})
    assert any(dit in e for e in errs), (famille, errs)
    assert preavis == [], "la règle est en vigueur : plus de préavis"


def test_un_tableau_sans_colonne_n_a_ni_preavis_ni_refus(lendemain):
    assert _preavis({"fields": []}, {"x": 1}) == ([], [])
    assert not vc.complete(None) and not vc.en_preavis({"fields": []})


@pytest.mark.parametrize("famille,col,valeur,dit", FAUTES, ids=IDS)
def test_une_ligne_en_faute_s_ecrit_sur_une_AUTRE_colonne(famille, col, valeur, dit,
                                                          lendemain):
    """LA règle : on juge ce que le geste écrit. La faute en place part dans `gelees`
    (servie en `hors_type`) — ni refus, ni silence."""
    gelees: list = []
    ligne = {"autre" if col != "autre" else "nom": "ok", col: valeur}
    ecrite = "autre" if col != "autre" else "nom"
    errs = S.validate_row(SCHEMA, ligne, written={ecrite}, gelees=gelees,
                          en_place=dict(ligne))
    assert errs == [], (famille, errs)
    if famille != "sous-record fermé":   # la fermeture ne vaut que pour ce qu'on écrit
        assert any(g["champ"].startswith(col) for g in gelees), (famille, gelees)


def test_une_couche_inconnue_en_place_ne_bloque_plus_sur_un_tableau_report():
    """Le cas qui était FAUX avant ce lot : sous un format qui faisait déjà contrat,
    une couche inconnue en place refusait l'écriture d'une autre colonne."""
    schema = {"unknown_columns": "report", "fields": SCHEMA["fields"]}
    gelees: list = []
    errs = S.validate_row(schema, {"autre": {"valeur": "v", "commentaire": "c"},
                                   "nom": "ok"}, written={"nom"}, gelees=gelees)
    assert errs == [] and [g["champ"] for g in gelees] == ["autre"]


def test_hors_options_ne_releve_que_ce_que_le_geste_pose():
    """L'autre défaut corrigé : une valeur hors options DÉJÀ en place entrait dans le
    relevé `hors` (ce que l'écriture peut ÉCARTER), et faussait l'écartement de la
    valeur que le geste pose, lui."""
    schema = {"unknown_columns": "report", "fields": SCHEMA["fields"]}
    hors: list = []
    errs = S.validate_row(schema, {"statut": "z", "etape": "w"}, written={"etape"},
                          hors=hors)
    assert [h["champ"] for h in hors] == ["etape"] and len(errs) == 1


def test_le_texte_servi_est_derive_de_la_date(monkeypatch):
    avant = vc.description_ecriture()
    assert "From 2026-10-21 on" in avant and "Until then" in avant
    assert "data_patch_schema" in avant and "OTHER columns" in avant
    assert "unknown_columns` was REMOVED" in vc.description_schema()
    monkeypatch.setenv(vc.ENV_VALIDATION_COMPLETE_LE, "2026-01-01")
    apres = vc.description_ecriture()
    assert "CONTRACT on every table" in apres and "Until then" not in apres


def test_la_description_servie_dit_la_regle():
    import asyncio

    from fastmcp import FastMCP

    from oto_mcp.capabilities.datastore import columns
    from oto_mcp.capabilities.registry import CAPABILITIES
    from oto_mcp.tools import datastore as tools_ds
    mcp = FastMCP("t")
    tools_ds.register(mcp)
    for nom in ("data_set_schema", "data_write"):
        desc = asyncio.run(mcp.get_tool(nom)).description
        assert "the declared format is a contract on every table" in desc.lower(), nom
        assert '"unknown_columns"?' not in desc, nom
    patch = next(c for c in columns.CAPABILITIES if c.key == "me.datastore.patch_schema")
    assert "unknown_columns` was REMOVED" in patch.description
    for cle in ("me.datastore.append_row", "me.datastore.write_rows",
                "me.datastore.update_row"):
        cap = next(c for c in CAPABILITIES if c.key == cle)
        assert "declared format is a CONTRACT" in cap.description, cle


# ── la base ──────────────────────────────────────────────────────────────────

class _Claims:
    def __init__(self, sub: str):
        self.claims = {"sub": sub, "email": f"{sub}@v124.invalid", "name": sub}


class _Verifier:
    async def verify_token(self, token: str):
        return _Claims(token)


def _h() -> dict:
    return {"Authorization": f"Bearer {SUB}"}


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
        db.upsert_user(SUB, email=f"{SUB}@v124.invalid", name=SUB)
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
    from oto_mcp import db
    from oto_mcp.datastore.core import make_store
    ns = "t-" + uuid.uuid4().hex[:6]
    ns_id = db.create_datastore("user", SUB, ns)
    store = make_store(SUB)
    store.set_schema(ns, schema)
    ids = [store.append_row(ns, dict(ligne))["_id"] for ligne in lignes]
    return ns, ns_id, ids


def _base(ns_id: int) -> list[dict]:
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        rows = conn.execute("SELECT row_id, data FROM datastore_rows WHERE ns_id = %s "
                            "ORDER BY created_at, row_id", (ns_id,)).fetchall()
    return [{"_id": r["row_id"], **r["data"]} for r in rows]


def _preavis_servi(rep: dict) -> str:
    return next(n for n in rep.get("notices") or [] if "format déclaré" in n)


def _toutes_dites(texte: str) -> None:
    for _, col, _, dit in FAUTES:
        if dit.startswith("statut: attendu") or col == "autre":
            continue                  # une faute par case ; la couche, cf. EN_FAUTE
        assert dit in texte, dit
    assert "21 octobre 2026" in texte and "data_patch_schema" in texte


def _sans_id(lignes: list[dict]) -> list[dict]:
    return [{k: v for k, v in ligne.items() if k != "_id"} for ligne in lignes]


# ── AVANT : toutes les faces passent, et le disent ───────────────────────────

def test_avant_mcp_et_rest_ecrivent_et_disent_chaque_faute(client, data_write):
    for face in ("mcp", "rest"):
        ns, ns_id, _ = _table()
        if face == "mcp":
            rep = data_write(datastore=ns, row=dict(EN_FAUTE))
        else:
            r = client.post(f"/api/datastores/{ns}/rows", headers=_h(), json=EN_FAUTE)
            assert r.status_code == 201, r.text
            rep = r.json()
        texte = _preavis_servi(rep)
        _toutes_dites(texte)
        assert "(+" in texte or texte.count(";") >= 7, "chaque faute est citée"
        assert _sans_id(_base(ns_id)) == [EN_FAUTE], "la donnée est écrite telle quelle"


def test_avant_le_lot_dit_une_seule_phrase(client, data_write):
    ns, ns_id, _ = _table()
    lot = [{"nom": "A", "statut": "z"}, {"nom": "B", "site": "www.x.fr"}]
    rep = data_write(datastore=ns, rows=lot)
    phrases = [n for n in rep["notices"] if "format déclaré" in n]
    assert len(phrases) == 1 and "'z'" in phrases[0] and "www.x.fr" in phrases[0]
    r = client.post(f"/api/datastores/{ns}/rows/batch", headers=_h(), json={"rows": lot})
    assert r.status_code == 200, r.text
    assert len([n for n in r.json()["notices"] if "format déclaré" in n]) == 1
    assert len(_base(ns_id)) == 4


def test_avant_le_patch_par_id_est_averti(client, data_write):
    ns, _, ids = _table(lignes=[{"nom": "A"}])
    r = client.patch(f"/api/datastores/{ns}/rows/{ids[0]}", headers=_h(),
                     json={"site": "www.x.fr"})
    assert r.status_code == 200, r.text
    assert "www.x.fr" in _preavis_servi(r.json())
    rep = data_write(datastore=ns, id=ids[0], row={"statut": "z"})
    assert "'z' hors options" in _preavis_servi(rep)


def test_avant_l_upload_signe_et_l_import_avertissent(live, monkeypatch):
    from oto_mcp import upload_tokens
    ns, ns_id, _ = _table()
    corps = b'{"nom": "A", "statut": "z"}\n{"nom": "B", "site": "www.x.fr"}\n'
    cible = {"kind": "datastore", "ns_id": ns_id, "namespace": ns, "format": "ndjson"}
    rep = upload_tokens.materialize(SUB, cible, corps, "application/x-ndjson")
    assert rep["inserted"] == 2 and "'z' hors options" in _preavis_servi(rep)
    monkeypatch.setattr(upload_tokens, "IMPORT_SLICE", 1)
    rep = upload_tokens.import_rows(SUB, cible, [{"nom": "C", "etape": "w"},
                                                 {"nom": "D", "tags": "a,b"}],
                                    deadline=time.monotonic() + 60)
    phrases = [n for n in rep["notices"] if "format déclaré" in n]
    assert rep["done"] and len(phrases) == 1
    assert "'w' hors options" in phrases[0] and "tags: attendu list" in phrases[0]
    assert len(_base(ns_id)) == 4


def test_avant_un_tableau_qui_faisait_deja_contrat_refuse_toujours(client):
    """Le réglage STOCKÉ `report` garde la validation complète d'ici à la date."""
    from oto_mcp import db
    ns, ns_id, _ = _table()
    db.set_datastore_schema(ns_id, {**SCHEMA, "unknown_columns": "report"})
    r = client.post(f"/api/datastores/{ns}/rows", headers=_h(),
                    json={"nom": "A", "site": "www.x.fr"})
    assert r.status_code == 400 and r.json()["error"] == "row_invalid", r.text
    assert _base(ns_id) == []


# ── APRÈS : refusé, rien n'est écrit ; la ligne en faute reste écrivable ─────

def test_au_lendemain_une_faute_est_refusee_sur_toutes_les_faces(client, data_write,
                                                                  lendemain):
    from oto_mcp.mcp_errors import McpError
    ns, ns_id, _ = _table()
    with pytest.raises(McpError) as e:
        data_write(datastore=ns, row={"nom": "A", "site": "www.x.fr"})
    assert "attendu une URL" in e.value.error.message
    r = client.post(f"/api/datastores/{ns}/rows", headers=_h(),
                    json={"nom": "A", "fiche": "Lyon"})
    assert r.status_code == 400 and r.json()["error"] == "row_invalid", r.text
    assert _base(ns_id) == []


def test_au_lendemain_une_valeur_hors_options_seule_est_ecartee(client, lendemain):
    """Le régime des tableaux qui faisaient contrat, désormais celui de tous (#667)."""
    ns, ns_id, _ = _table()
    r = client.post(f"/api/datastores/{ns}/rows", headers=_h(),
                    json={"nom": "A", "statut": "z"})
    assert r.status_code == 201, r.text
    assert r.json()["valeurs_ecartees"][0]["champ"] == "statut"
    assert _sans_id(_base(ns_id)) == [{"nom": "A"}]


def test_au_lendemain_la_ligne_en_faute_s_ecrit_sur_ses_autres_colonnes(
        client, data_write, monkeypatch):
    """LA règle, sur la vraie base : la ligne porte une faute de CHAQUE famille, écrite
    la veille sous préavis ; au lendemain, écrire une autre colonne passe, la ligne
    n'est pas corrigée, et ses fautes se disent."""
    ns, ns_id, ids = _table(lignes=[dict(EN_FAUTE)])          # la veille : écrite
    monkeypatch.setattr(vc, "_aujourdhui",
                        lambda: vc.VALIDATION_COMPLETE_LE + dt.timedelta(days=1))
    r = client.patch(f"/api/datastores/{ns}/rows/{ids[0]}", headers=_h(),
                     json={"nom": "Acme"})
    assert r.status_code == 200, r.text
    signale = set(r.json().get("hors_type") or {})
    for col in ("statut", "etape", "site", "fiche", "tags"):
        assert any(c.startswith(col) for c in signale), (col, signale)
    rep = data_write(datastore=ns, id=ids[0], row={"nom": "Acme bis"})
    assert not any("format déclaré" in n for n in rep.get("notices") or [])
    ligne = _sans_id(_base(ns_id))[0]
    assert ligne == {**EN_FAUTE, "nom": "Acme bis"}, "les données du tenant restent"
    # Réécrire la colonne fautive avec la MÊME faute, en revanche, est refusé.
    r = client.patch(f"/api/datastores/{ns}/rows/{ids[0]}", headers=_h(),
                     json={"site": "www.y.fr"})
    assert r.status_code == 400 and r.json()["error"] == "row_invalid", r.text


def test_avant_la_ligne_en_faute_ne_preavise_pas_ce_qu_on_n_ecrit_pas(client):
    ns, _, ids = _table(lignes=[dict(EN_FAUTE)])
    r = client.patch(f"/api/datastores/{ns}/rows/{ids[0]}", headers=_h(),
                     json={"nom": "Acme"})
    assert r.status_code == 200, r.text
    assert not any("format déclaré" in n for n in r.json().get("notices") or [])


# ── `unknown_columns` : refusé à la pose et au patch, sur la face REST ───────

def test_unknown_columns_est_refuse_a_la_pose_et_au_patch(client):
    ns, ns_id, _ = _table()
    r = client.put(f"/api/datastores/{ns}/schema", headers=_h(),
                   json={"schema": {**SCHEMA, "unknown_columns": "report"}})
    assert r.status_code == 400 and r.json()["error"] == "invalid_schema", r.text
    assert "plus aucun réglage" in r.json()["detail"]
    r = client.patch(f"/api/datastores/{ns}/schema", headers=_h(),
                     json={"unknown_columns": "reject"})
    assert r.status_code == 400 and r.json()["error"] == "unknown_fields", r.text
    assert "plus aucun réglage" in r.json()["detail"]
    from oto_mcp import db
    assert "unknown_columns" not in db.get_datastore_by_id(ns_id)["schema"]


def test_unknown_columns_stocke_ne_bloque_pas_le_patch(client):
    """Entre le déploiement et le retrait du stocké, patcher un tableau qui le porte
    passe : le réglage inchangé est reconduit, et la lecture le dit."""
    from oto_mcp import db
    ns, ns_id, _ = _table()
    db.set_datastore_schema(ns_id, {**SCHEMA, "unknown_columns": "reject"})
    r = client.patch(f"/api/datastores/{ns}/schema", headers=_h(),
                     json={"fields": [{"key": "ville", "type": "text"}]})
    assert r.status_code == 200, r.text
    assert "plus aucun réglage" in (r.json().get("warning") or "")
    assert db.get_datastore_by_id(ns_id)["schema"]["unknown_columns"] == "reject"
    r = client.get(f"/api/datastores/{ns}/schema", headers=_h())
    assert r.status_code == 200 and r.json()["reglages"] == {"new_rows": "create"}
