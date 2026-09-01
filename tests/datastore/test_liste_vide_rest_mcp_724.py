"""oto-backend#724 — même mécanisme des deux côtés, pas de divergence par table.

Le signalement décrivait un second défaut, en plus du no-op silencieux sur
`contacts: []` : `PATCH` REST viderait la liste sur un tableau NEUF mais
l'ignorerait sur `edition-vivier` (org 226, tableau existant). Ce banc rejoue le
même geste (une valeur réelle, puis `[]`) sur les DEUX formes de tableau, par les
DEUX faces d'écriture (`store.update_row` = le chemin que suit `data_write` MCP,
et la vraie chaîne REST via `_rest_adapter`) — pour départager une divergence
RÉELLE d'un artefact de repro.

Conclusion mesurée ici : il n'y en a qu'UNE, et REST/MCP l'exposent identiquement,
sur un tableau `strict` comme sur un tableau libre. `update_row` et la capacité
`me.datastore.update_row` appellent le MÊME store — rien ne les fait diverger.
Ce qui ressemblait à « ça vide sur une table neuve » est le court-circuit « rien à
perdre » d'`arbitrer_les_vides` : sur une colonne qui n'a JAMAIS porté de valeur,
n'importe quelle écriture — y compris `[]` — passe sans arbitrage, parce qu'il n'y
a rien à préserver. Ce n'est pas une preuve que `[]` vide une valeur RÉELLE ; c'est
un cas qui ne l'a jamais mise à l'épreuve."""
from __future__ import annotations

import uuid

import pytest

from _datastore_rest import call, stub_authz


@pytest.fixture(scope="module")
def live(pg_dsn):
    import os
    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    name = "oto_724_" + uuid.uuid4().hex[:8]
    root = psycopg.connect(pg_dsn, autocommit=True)
    root.execute(f'CREATE DATABASE "{name}"')
    dsn = pg_dsn.rsplit("/", 1)[0] + "/" + name

    prev_url, prev_pool = os.environ.get("DATABASE_URL"), dbconn._pool
    os.environ["DATABASE_URL"] = dsn
    dbconn._pool = None
    try:
        from oto_mcp.db import init_db
        init_db()
        yield
    finally:
        if dbconn._pool is not None:
            dbconn._pool.close()
        dbconn._pool = prev_pool
        if prev_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = prev_url
        root.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        root.close()


def _store():
    from oto_mcp.datastore.core import make_store
    return make_store("sub-724")


def _donnees(ns_id: int, row_id: str) -> dict:
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        r = conn.execute(
            "SELECT data FROM datastore_rows WHERE ns_id = %s AND row_id = %s",
            (ns_id, row_id)).fetchone()
    return dict((r or {}).get("data") or {})


STRICT_SCHEMA = {
    "key": "siren", "strict": True,
    "fields": [{"key": "siren", "type": "text"},
              {"key": "contacts", "type": "list", "of": {"type": "text"}}],
}


def _rest_patch(monkeypatch, store, ns: str, row_id: str, patch: dict):
    """Le geste `PATCH /api/datastore/namespaces/{ns}/rows/{row_id}` — vraie
    chaîne REST (auth → corps → handler), store réel derrière."""
    from oto_mcp.capabilities.datastore import rows as dsr
    stub_authz(monkeypatch)
    monkeypatch.setattr(dsr, "make_store", lambda sub: store)
    monkeypatch.setattr(dsr.datastore_journal, "record", lambda *a, **k: None)
    return call("me.datastore.update_row",
               path_params={"namespace": ns, "row_id": row_id}, body=patch)


@pytest.mark.parametrize("strict", [True, False], ids=["strict-edition-vivier", "libre-neuf"])
def test_rest_et_mcp_saccordent_sur_une_valeur_reelle(live, monkeypatch, strict):
    """Le point du signalement : `edition-vivier` (`strict`) contre un tableau
    LIBRE fraîchement créé — même geste, même valeur de départ RÉELLE."""
    from oto_mcp import db
    ns = "t-" + uuid.uuid4().hex[:6]
    ns_id = db.create_datastore_namespace("user", "sub-724", ns)
    st = _store()
    if strict:
        st.set_schema(ns, STRICT_SCHEMA)
    row = st.append_row(ns, {"siren": "383959897", "contacts": ["Jean Dupont"]} if strict
                        else {"contacts": ["Jean Dupont"]})
    rid = row["_id"]
    assert _donnees(ns_id, rid).get("contacts") == ["Jean Dupont"]

    # Face MCP (`data_write` appelle exactement `store.update_row`) :
    mcp_out = st.update_row(ns, rid, {"contacts": []})
    apres_mcp = _donnees(ns_id, rid)

    # Remise en place pour rejouer le MÊME point de départ côté REST :
    st.update_row(ns, rid, {"contacts": ["Jean Dupont"]})
    assert _donnees(ns_id, rid).get("contacts") == ["Jean Dupont"]

    # Face REST (vraie chaîne `_rest_adapter`, même store) :
    code, rest_out = _rest_patch(monkeypatch, st, ns, rid, {"contacts": []})
    apres_rest = _donnees(ns_id, rid)

    assert code == 200
    assert apres_mcp.get("contacts") == apres_rest.get("contacts") == [], \
        "les deux faces vident la MÊME liste réelle — aucune divergence par table"
    assert mcp_out.get("contacts") == rest_out.get("contacts") == [], \
        "la réponse reflète l'état PERSISTÉ (vidé), pas un écho du payload soumis"


def test_lartefact_de_repro_une_colonne_jamais_peuplee(live, monkeypatch):
    """Ce que « ça vide sur une table neuve » mesurait en réalité : sur une
    colonne qui n'a JAMAIS porté de valeur, `[]` passe sans jugement (rien à
    perdre) — indiscernable, à l'œil, d'un vrai effacement. Rejouer EXACTEMENT
    ce protocole (créer la ligne SANS `contacts`, puis PATCH `contacts: []`)
    reproduit le faux positif, sur un tableau libre comme sur un strict."""
    from oto_mcp import db
    ns = "t-" + uuid.uuid4().hex[:6]
    ns_id = db.create_datastore_namespace("user", "sub-724", ns)
    st = _store()
    row = st.append_row(ns, {"nom": "sans contact au depart"})
    rid = row["_id"]
    assert "contacts" not in _donnees(ns_id, rid)

    code, out = _rest_patch(monkeypatch, st, ns, rid, {"contacts": []})

    assert code == 200
    assert _donnees(ns_id, rid).get("contacts") == []
    assert "valeurs_effacees" not in out and "valeurs_ignorees" not in out, \
        "rien n'a été arbitré : il n'y avait rien à perdre, pas une preuve que " \
        "`[]` efface une valeur réelle"
