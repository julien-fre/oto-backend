"""Le porteur `microsoft` reprend le coffre de la carte `sharepoint` — sur une VRAIE base.

Jusqu'au porteur, les lignes du coffre et les coordonnées de l'application Microsoft
vivaient sous `sharepoint`. Le démarrage les COPIE sous `microsoft`
(`auth/microsoft.copier_depuis_sharepoint`) : re-chiffrées (l'AAD lie le chiffré à son
connecteur, un `UPDATE` de la colonne fabriquerait une ligne illisible), sans toucher
l'original — la base est partagée, le code servi d'avant lit encore `sharepoint`.

Ce que ce banc prouve, sur PostgreSQL :
- la copie se déchiffre sous le porteur, `meta` et instance suivent, l'original reste ;
- rejouée, elle ne fait rien (marque `copied_to`), et ne RESSUSCITE pas un compte
  retiré sous `microsoft` ;
- un compte ajouté sous `sharepoint` après la copie (par le code d'avant) est copié au
  démarrage suivant ;
- les coordonnées de l'application sont copiées une fois, jamais réécrites ensuite ;
- les scopes d'avant le porteur donnent `services_granted == ["sharepoint"]`.

`pg_dsn` (conftest) : `OTO_TEST_PG_DSN`, sinon un conteneur jetable, sinon skip.
"""
from __future__ import annotations

import base64
import os
import uuid

import pytest

SUB = "sub-copie"
ORG = 3
_KEY = base64.b64encode(b"\x22" * 32).decode()
_SCOPES_D_AVANT = ("Files.ReadWrite.All Sites.ReadWrite.All User.Read profile openid "
                   "email offline_access")


@pytest.fixture(scope="module")
def live(pg_dsn):
    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    name = "oto_ms_copie_" + uuid.uuid4().hex[:8]
    root = psycopg.connect(pg_dsn, autocommit=True)
    root.execute(f'CREATE DATABASE "{name}"')
    dsn = pg_dsn.rsplit("/", 1)[0] + "/" + name
    avant_url, avant_pool = os.environ.get("DATABASE_URL"), dbconn._pool
    avant_key = os.environ.get("OTO_MCP_MASTER_KEY")
    os.environ["DATABASE_URL"] = dsn
    os.environ["OTO_MCP_MASTER_KEY"] = _KEY
    dbconn._pool = None
    try:
        from oto_mcp.db import init_db
        init_db()
        yield
    finally:
        if dbconn._pool is not None:
            dbconn._pool.close()
        dbconn._pool = avant_pool
        for cle, valeur in (("DATABASE_URL", avant_url), ("OTO_MCP_MASTER_KEY", avant_key)):
            if valeur is None:
                os.environ.pop(cle, None)
            else:
                os.environ[cle] = valeur
        root.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        root.close()


def _rows(sql, params=()):
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def _exec(sql, params=()):
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        conn.execute(sql, params)


@pytest.fixture(autouse=True)
def table_rase(live):
    _exec("DELETE FROM connector_instances")
    _exec("DELETE FROM connector_credentials")
    _exec("DELETE FROM connector_settings")


def _membre():
    from oto_mcp import credentials_store
    return credentials_store.MEMBER, credentials_store.member_id(ORG, SUB)


def _poser_ancienne(account, secret, meta=None):
    """Une ligne telle que le code d'avant la posait, sous `sharepoint` — par
    l'entonnoir d'écriture (`set_credential` refuse désormais ce nom : un service
    ne porte plus de credential)."""
    from oto_mcp import credentials_store
    from oto_mcp.db._conn import _connect
    et, eid = _membre()
    with _connect() as conn:
        credentials_store._upsert(conn, et, eid, "sharepoint", account, secret, SUB,
                                  {"scopes": _SCOPES_D_AVANT, "microsoft_id": "id-" + account,
                                   **(meta or {})})


def _copier():
    from oto_mcp.auth import microsoft as ms_auth
    return ms_auth.copier_depuis_sharepoint()


def test_la_copie_se_dechiffre_sous_le_porteur_et_l_original_reste(live):
    from oto_mcp import credentials_store
    _poser_ancienne("jane@contoso.example", "RT-JANE", {"is_default": True})
    _poser_ancienne("john@fabrikam.example", "RT-JOHN")
    bilan = _copier()
    assert bilan["copied"] == 2 and bilan["unreadable"] == 0
    et, eid = _membre()
    copie = credentials_store.get_credential_with_meta(et, eid, "microsoft",
                                                       account="jane@contoso.example")
    assert copie["secret"] == "RT-JANE"
    assert copie["meta"]["is_default"] is True and copie["meta"]["scopes"] == _SCOPES_D_AVANT
    assert "copied_to" not in copie["meta"]
    # L'original : intact (le code d'avant le lit encore) et marqué.
    lignes = _rows("SELECT account, meta FROM connector_credentials "
                   "WHERE connector = 'sharepoint' ORDER BY account")
    assert [l["meta"]["copied_to"] for l in lignes] == ["microsoft", "microsoft"]
    # L'instance naît avec la ligne copiée (entonnoir `_upsert`).
    assert len(_rows("SELECT 1 FROM connector_instances WHERE connector = 'microsoft' "
                     "AND revoked_at IS NULL")) == 2


def test_rejouee_elle_ne_fait_rien_et_ne_ressuscite_pas(live):
    from oto_mcp import credentials_store
    _poser_ancienne("jane@contoso.example", "RT-JANE")
    _copier()
    assert _copier() == {"copied": 0, "present": 0, "unreadable": 0, "settings": 0}
    et, eid = _membre()
    credentials_store.clear_credential(et, eid, "microsoft", account="jane@contoso.example")
    _copier()
    assert not credentials_store.list_accounts(et, eid, "microsoft"), \
        "un compte retiré sous le porteur ne revient pas au démarrage suivant"


def test_un_compte_ajoute_par_le_code_d_avant_est_copie_au_demarrage_suivant(live):
    from oto_mcp import credentials_store
    _poser_ancienne("jane@contoso.example", "RT-JANE")
    _copier()
    _poser_ancienne("john@fabrikam.example", "RT-JOHN")
    assert _copier()["copied"] == 1
    et, eid = _membre()
    assert [a["account"] for a in credentials_store.list_accounts(et, eid, "microsoft")] == [
        "jane@contoso.example", "john@fabrikam.example"]


def test_une_ligne_deja_presente_sous_le_porteur_n_est_pas_ecrasee(live):
    from oto_mcp import credentials_store
    et, eid = _membre()
    credentials_store.set_credential(et, eid, "microsoft", "RT-NEUF", set_by=SUB,
                                     meta={"scopes": "User.Read"},
                                     account="jane@contoso.example")
    _poser_ancienne("jane@contoso.example", "RT-VIEUX")
    assert _copier()["present"] == 1
    assert credentials_store.get_credential(
        et, eid, "microsoft", account="jane@contoso.example") == "RT-NEUF"


def test_les_coordonnees_de_l_application_sont_copiees_une_fois(live):
    from oto_mcp.auth import microsoft as ms_auth
    from oto_mcp.db import connector_settings as store
    for cle, valeur in (("client_id", "app-id"), ("client_secret", "app-secret"),
                        ("autre", "x")):
        store.set_connector_setting("platform", "platform", "sharepoint", cle, valeur)
    assert _copier()["settings"] == 2
    assert ms_auth.app() == {"client_id": "app-id", "client_secret": "app-secret"}
    assert not [r for r in store.list_connector_settings()
                if r["connector"] == "microsoft" and r["key"] == "autre"]
    # L'opérateur gère désormais le porteur : un démarrage ne réécrit rien.
    store.clear_connector_setting("platform", "platform", "microsoft", "client_secret")
    assert _copier()["settings"] == 0
    assert ms_auth.coordonnees_manquantes() == ["client_secret"]


def test_les_scopes_d_avant_le_porteur_donnent_sharepoint(live):
    from oto_mcp import credentials_store
    from oto_mcp.auth import microsoft as ms_auth
    _poser_ancienne("jane@contoso.example", "RT-JANE")
    _copier()
    et, eid = _membre()
    (compte,) = credentials_store.list_accounts(et, eid, "microsoft")
    assert ms_auth.services_granted(compte["meta"]["scopes"]) == ["sharepoint"]
