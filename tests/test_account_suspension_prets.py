"""Un compte en pause suspend ce qu'il PRÊTAIT, et le rend à son réveil (#898).

Arbitrage du 23/09/2026, option A : les connecteurs qu'un compte a prêtés — un compte
Unipile (`connector_account_grants`, nominatif ou de groupe) ou une clé prêtée à un
pair (`share_side`, ADR 0044) — s'arrêtent avec la pause ; le bénéficiaire reçoit un
refus NOMMÉ (`lender_suspended`), pas « révoqué » ni « inconnu » ; au réveil, le prêt
revient sans rien reconfigurer.

Exercé sur SQL réel : c'est la jointure sur l'état du prêteur qu'on prouve, et le fait
que rien n'a été détaché (grant, `share_side` et pointeur du bénéficiaire intacts)."""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest


@pytest.fixture(scope="module")
def live(pg_dsn):
    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    name = "oto_prets_" + uuid.uuid4().hex[:8]
    root = psycopg.connect(pg_dsn, autocommit=True)
    root.execute(f'CREATE DATABASE "{name}"')
    dsn = pg_dsn.rsplit("/", 1)[0] + "/" + name

    avant_url, avant_pool = os.environ.get("DATABASE_URL"), dbconn._pool
    avant_key = os.environ.get("OTO_MCP_MASTER_KEY")
    os.environ["DATABASE_URL"] = dsn
    os.environ["OTO_MCP_MASTER_KEY"] = "5" * 64
    dbconn._pool = None
    try:
        from oto_mcp.db import init_db
        init_db()
        yield
    finally:
        if dbconn._pool is not None:
            dbconn._pool.close()
        dbconn._pool = avant_pool
        for cle, valeur in (("DATABASE_URL", avant_url),
                            ("OTO_MCP_MASTER_KEY", avant_key)):
            if valeur is None:
                os.environ.pop(cle, None)
            else:
                os.environ[cle] = valeur
        root.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        root.close()


@pytest.fixture
def pret(live):
    """Un prêteur avec un LinkedIn connecté, prêté nominativement à `nominal` et à un
    groupe dont `membre` fait partie. Noms uniques : la base survit au module."""
    from oto_mcp import db, group_store, org_store
    u = uuid.uuid4().hex[:8]
    preteur, nominal, membre = f"preteur_{u}", f"nominal_{u}", f"membre_{u}"
    for sub in (preteur, nominal, membre):
        db.upsert_user(sub, email=f"{sub}@exemple.test")
    org = org_store.create_org(f"org_{u}", created_by=preteur)
    db.set_unipile_account(preteur, "ACC_" + u, "Avatar", org_id=org, provider="LINKEDIN")
    groupe = group_store.create_group(org, f"groupe_{u}")
    group_store.add_group_member(groupe, membre)
    db.set_account_grant(preteur, "LINKEDIN", "ACC_" + u, nominal, granted_by=preteur)
    db.set_account_group_grant(preteur, "LINKEDIN", "ACC_" + u, groupe,
                               granted_by=preteur)
    return {"preteur": preteur, "nominal": nominal, "membre": membre, "org": org,
            "acc": "ACC_" + u}


def _pause(sub):
    from oto_mcp import db
    assert db.suspend_account(sub, by="ops", reason="départ") is not None


def _reveil(sub):
    from oto_mcp import db
    assert db.resume_account(sub) is True


# ── Compte Unipile prêté (#55) ────────────────────────────────────────────────

@pytest.mark.parametrize("beneficiaire", ["nominal", "membre"])
def test_la_pause_du_preteur_retient_le_pret_et_le_reveil_le_rend(pret, beneficiaire):
    from oto_mcp import db
    qui, acc = pret[beneficiaire], pret["acc"]
    assert acc in db.granted_accounts_for(qui, "LINKEDIN")

    _pause(pret["preteur"])
    assert db.granted_accounts_for(qui, "LINKEDIN") == {}
    assert acc in db.suspended_lenders_for(qui, "LINKEDIN")

    _reveil(pret["preteur"])
    assert acc in db.granted_accounts_for(qui, "LINKEDIN")
    assert db.suspended_lenders_for(qui, "LINKEDIN") == {}


def test_la_liste_des_prets_recus_dit_que_le_preteur_est_en_pause(pret):
    from oto_mcp import db
    _pause(pret["preteur"])
    rows = db.list_account_grants_to(pret["nominal"])
    assert [(r["active"], r["owner_suspended"]) for r in rows] == [(False, True)]
    _reveil(pret["preteur"])
    rows = db.list_account_grants_to(pret["nominal"])
    assert [(r["active"], r["owner_suspended"]) for r in rows] == [(True, False)]


def test_le_pointeur_du_beneficiaire_rencontre_un_refus_NOMME_puis_revient_seul(pret):
    from oto_mcp import account_suspension, db
    from oto_mcp.connectors import identities
    qui, acc = pret["nominal"], pret["acc"]
    db.set_operated_account(qui, "LINKEDIN", acc, pret["preteur"])
    assert identities.resolve_operated_account_id(qui, "LINKEDIN") == acc

    _pause(pret["preteur"])
    with pytest.raises(account_suspension.PreteurEnPause) as e:
        identities.resolve_operated_account_id(qui, "LINKEDIN")
    assert e.value.code == "lender_suspended"
    assert f"{pret['preteur']}@exemple.test" in str(e.value)
    assert "en pause" in str(e.value) and "révoqué" in str(e.value)
    assert "départ" not in str(e.value)          # le motif n'est pas servi au bénéficiaire
    # Rien n'a été détaché : le pointeur est toujours là…
    assert db.get_operated_account(qui, "LINKEDIN")["account_id"] == acc

    _reveil(pret["preteur"])
    # … et c'est lui qui rend le prêt, sans rien reconfigurer.
    assert identities.resolve_operated_account_id(qui, "LINKEDIN") == acc


def test_une_revocation_garde_son_refus_a_elle(pret):
    """Contrefactuel : le refus nommé ne se sert qu'à la pause — un grant retiré
    garde « révoquée ou déconnecté »."""
    from oto_mcp import account_suspension, db
    from oto_mcp.connectors import identities
    qui, acc = pret["nominal"], pret["acc"]
    db.set_operated_account(qui, "LINKEDIN", acc, pret["preteur"])
    db.clear_account_grant(pret["preteur"], "LINKEDIN", qui)
    with pytest.raises(ValueError) as e:
        identities.resolve_operated_account_id(qui, "LINKEDIN")
    assert not isinstance(e.value, account_suspension.PreteurEnPause)
    assert "révoquée" in str(e.value)


def test_choisir_un_compte_prete_par_un_compte_en_pause_est_un_refus_nomme(pret):
    from oto_mcp import account_suspension
    from oto_mcp.connectors import identities
    _pause(pret["preteur"])
    with pytest.raises(account_suspension.PreteurEnPause):
        asyncio.run(identities.select_identity(pret["nominal"], "unipile", pret["acc"]))


# ── Clé prêtée à un pair (`share_side`, ADR 0044) ─────────────────────────────

@pytest.fixture
def cle_pretee(live):
    from oto_mcp import credentials_store, db, instance_refs, org_store
    u = uuid.uuid4().hex[:8]
    preteur, emprunteur = f"cle_preteur_{u}", f"cle_emprunteur_{u}"
    for sub in (preteur, emprunteur):
        db.upsert_user(sub, email=f"{sub}@exemple.test")
    org = org_store.create_org(f"cle_org_{u}", created_by=preteur)
    db.set_member_api_key(preteur, org, "hunter", "sk_" + u)
    assert credentials_store.set_instance_sharing(
        credentials_store.MEMBER, credentials_store.member_id(org, preteur), "hunter",
        "", share_side=[f"user:{emprunteur}"])
    ref = instance_refs.parse_ref(instance_refs.make_member_ref(org, preteur, "hunter"))
    return {"preteur": preteur, "emprunteur": emprunteur, "ref": ref}


def test_la_cle_pretee_sarrete_avec_la_pause_et_revient_au_reveil(cle_pretee, monkeypatch):
    from oto_mcp import access
    from oto_mcp.mcp_errors import McpError
    # L'org co-posée est celle de l'APPELANT (hors de ce qu'on prouve ici).
    monkeypatch.setattr(access, "current_org", lambda sub: 777)
    qui, ref = cle_pretee["emprunteur"], cle_pretee["ref"]
    assert access.guard_instance_access(qui, ref) == 777

    _pause(cle_pretee["preteur"])
    with pytest.raises(McpError) as e:
        access.guard_instance_access(qui, ref)
    assert e.value.error.data == {"code": "lender_suspended", "retryable": False}
    assert f"{cle_pretee['preteur']}@exemple.test" in e.value.error.message
    assert "en pause" in e.value.error.message

    _reveil(cle_pretee["preteur"])
    assert access.guard_instance_access(qui, ref) == 777
