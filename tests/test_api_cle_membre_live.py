"""Custom API (`api`) au palier MEMBRE (`byo_user`) : un membre pose SA clé, rangée dans
son org, et s'en sert ; la clé de l'org sert toujours ceux qui n'en ont pas.

Contre un VRAI PostgreSQL, par la vraie chaîne : la pose par la route REST du membre,
la résolution par le walker unique (`access.resolve_credential`), la définition des
actions par ses routes REST (`scope=member`), le jar de session rangé dans la ligne.

Ce qui est prouvé :
- un membre non-admin pose sa clé et la résout ;
- un autre membre de la même org ne la résout pas, ne la lit pas et n'en touche pas
  la définition : la ligne membre se déduit de l'appelant, jamais d'un argument ;
- la clé de l'org sert qui n'a pas de clé perso ; la clé perso gagne sur elle ;
- le jar de session suit sa ligne : un jar par personne.
"""
from __future__ import annotations

import base64
import os
import uuid

import pytest

from _datastore_rest import call

CONNECTEUR = "api"
COMPTE = "paie"
_KEY = base64.b64encode(b"\x37" * 32).decode()
OP = {"name": "list_teams", "description": "List the teams.", "method": "GET",
      "path": "/teams", "input": {"type": "object", "properties": {}}}


@pytest.fixture(scope="module")
def live(pg_dsn):
    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    name = "oto_apimembre_" + uuid.uuid4().hex[:8]
    root = psycopg.connect(pg_dsn, autocommit=True)
    root.execute(f'CREATE DATABASE "{name}"')
    avant = {k: os.environ.get(k) for k in ("DATABASE_URL", "OTO_MCP_MASTER_KEY")}
    avant_pool = dbconn._pool
    os.environ["DATABASE_URL"] = pg_dsn.rsplit("/", 1)[0] + "/" + name
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
        for cle, valeur in avant.items():
            if valeur is None:
                os.environ.pop(cle, None)
            else:
                os.environ[cle] = valeur
        root.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        root.close()


def _carte(token: str) -> dict:
    return {"base_url": "https://api.acme.test", "auth_mode": "bearer", "token": token,
            "account": COMPTE}


@pytest.fixture
def monde(live):
    """Une org : un admin, deux membres simples (A et B), chacun l'org pour active.
    L'org détient une clé `api` au compte `paie`, posée par l'admin."""
    from oto_mcp import credentials_store as cs
    from oto_mcp import db, org_store
    u = uuid.uuid4().hex[:8]
    admin, a, b = f"admin_{u}", f"membre_a_{u}", f"membre_b_{u}"
    for s in (admin, a, b):
        db.upsert_user(s, email=f"{s}@example.test")
    org = org_store.create_org(f"org_{u}", created_by=admin)
    org_store.add_org_member(org, admin, "org_admin")
    for s in (a, b):
        org_store.add_org_member(org, s)
    for s in (admin, a, b):
        org_store.set_active_org(s, org)
    cs.set_credential(cs.ORG, str(org), CONNECTEUR,
                      cs.pack_secret(CONNECTEUR, {k: v for k, v in _carte("CLE-ORG").items()
                                                  if k != "account"}),
                      set_by=admin, account=COMPTE)
    return {"org": org, "admin": admin, "a": a, "b": b}


def _poser_perso(sub: str, token: str):
    return call("me.credential.set", path_params={"provider": CONNECTEUR},
                body=_carte(token), sub=sub)


def _resolu(sub: str):
    from oto_mcp import access
    return access.resolve_credential(CONNECTEUR, "byo", sub=sub, account=COMPTE,
                                     emit_on_failure=False)


def _definition(sub: str, org: int, scope: str, body=None):
    q = f"account={COMPTE}&scope={scope}".encode()
    if body is None:
        return call("custom_api.definition.get", path_params={"id": str(org)},
                    query=q, sub=sub)
    return call("custom_api.definition.set", path_params={"id": str(org)},
                query=q, body=body, sub=sub)


def test_la_carte_declare_le_palier_membre():
    from oto_mcp import providers
    assert {"byo_user", "byo_org"} <= set(providers.REGISTRY[CONNECTEUR].auth_modes)
    assert providers.is_byo_user(CONNECTEUR)


def test_sans_cle_perso_la_cle_de_l_org_sert(monde):
    rc = _resolu(monde["a"])
    assert (rc.entity_type, str(rc.entity_id)) == ("org", str(monde["org"]))
    assert rc.fields["token"] == "CLE-ORG"


def test_un_membre_pose_sa_cle_et_elle_gagne_sur_celle_de_l_org(monde):
    from oto_mcp import credentials_store as cs
    code, out = _poser_perso(monde["a"], "CLE-DE-A")
    assert code == 200, out
    assert out["org_id"] == monde["org"] and out["account"] == COMPTE
    rc = _resolu(monde["a"])
    assert (rc.entity_type, str(rc.entity_id)) == (
        cs.MEMBER, cs.member_id(monde["org"], monde["a"]))
    assert rc.fields["token"] == "CLE-DE-A"


def test_un_autre_membre_ne_voit_ni_n_utilise_la_cle_perso(monde):
    assert _poser_perso(monde["a"], "CLE-DE-A")[0] == 200
    rc = _resolu(monde["b"])
    assert (rc.entity_type, str(rc.entity_id)) == ("org", str(monde["org"]))
    assert rc.fields["token"] == "CLE-ORG"
    # Sa propre lecture au palier membre ne rend rien : la ligne est la sienne, vide.
    code, out = call("me.credential.get", path_params={"provider": CONNECTEUR},
                     query=f"account={COMPTE}".encode(), sub=monde["b"])
    assert code == 404, out
    code, _ = _definition(monde["b"], monde["org"], "member")
    assert code == 404
    code, _ = _definition(monde["b"], monde["org"], "member", {"operations": [OP]})
    assert code == 404


def test_la_definition_d_une_cle_perso_est_a_son_titulaire(monde):
    assert _poser_perso(monde["a"], "CLE-DE-A")[0] == 200
    code, out = _definition(monde["a"], monde["org"], "member",
                            {"description": "Ma paie.", "operations": [OP]})
    assert code == 200, out
    assert out["operations"] == [OP] and "token" not in out
    code, out = _definition(monde["a"], monde["org"], "member")
    assert code == 200 and out["description"] == "Ma paie."
    # Celle de l'org n'a pas bougé, et un membre simple ne l'écrit pas.
    code, out = _definition(monde["a"], monde["org"], "org")
    assert code == 200 and out["operations"] == []
    code, _ = _definition(monde["a"], monde["org"], "org", {"operations": [OP]})
    assert code == 403
    code, _ = call("custom_api.operations.get", path_params={"id": str(monde["org"])},
                   query=f"account={COMPTE}&scope=member".encode(), sub=monde["a"])
    assert code == 200


def test_le_jar_de_session_suit_sa_ligne(monde):
    from oto_mcp.connectors import api_session
    assert _poser_perso(monde["a"], "CLE-DE-A")[0] == 200
    ra, rb = _resolu(monde["a"]), _resolu(monde["b"])
    ligne_a = (ra.entity_type, str(ra.entity_id), ra.account or "")
    ligne_b = (rb.entity_type, str(rb.entity_id), rb.account or "")
    assert ligne_a != ligne_b
    api_session.jar_writer(ligne_a, ra.fields)("jar-de-a")
    assert api_session.read_jar(ligne_a, ra.fields) == "jar-de-a"
    assert api_session.read_jar(ligne_b, rb.fields) is None
