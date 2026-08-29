"""L-clés, PR 1 — le coffre connaît le tenant, contre un vrai PostgreSQL.

Ce que ce fichier prouve, dans l'ordre de ce qui coûterait le plus cher à rater :

1. **Aucun credential existant ne bouge.** Une clé d'org posée AVANT la clé tenant se
   relit APRÈS, et son sceau (l'AAD) est figé à l'octet : le lot ajoute une valeur
   d'entité, il ne touche pas à la dérivation.
2. **Le sceau porte le tenant comme il porte l'org** — un ciphertext scellé pour un
   tenant ne se transplante pas sur une org (ni sur un autre tenant).
3. **L'instance naît à la pose et s'archive au retrait** (L6 pièce 2), avec le
   propriétaire `tenant` que la table prévoyait « inerte » jusqu'à ce lot.
4. **La résolution RÉELLE sert la clé tenant aux comptes du tenant — et à eux seuls.**
   Un sub nu (tenant primaire) dans la même org ne la voit jamais.

Base DÉDIÉE sur le conteneur partagé (`CREATE DATABASE`), montée par le DDL réel :
jouer `init_db()` dans la base de session casse les tests voisins (docs/commands.md).
"""
from __future__ import annotations

import os
import uuid

import pytest

from oto_mcp import access, credentials_store as cs, crypto, tenancy, tenant_vault

PILOTE = "pilote"
ISSUER = "https://auth.pilote.test/oidc"
ORG = 4242
SUB_T = f"{PILOTE}:usr_t"        # un compte du tenant pilote
SUB_NU = "usr_nu"                 # un compte du tenant primaire, membre de la même org
CONN = "hunter"                   # byo_user + byo_org + platform : tous les barreaux


def _exec(sql, params=()):
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        conn.execute(sql, params)


def _one(sql, params=()):
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        return conn.execute(sql, params).fetchone()


@pytest.fixture(scope="module")
def live(pg_dsn):
    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    name = "oto_lcles_" + uuid.uuid4().hex[:8]
    root = psycopg.connect(pg_dsn, autocommit=True)
    root.execute(f'CREATE DATABASE "{name}"')
    dsn = pg_dsn.rsplit("/", 1)[0] + "/" + name

    prev_url, prev_pool = os.environ.get("DATABASE_URL"), dbconn._pool
    prev_key = os.environ.get("OTO_MCP_MASTER_KEY")
    prev_registry = tenancy.current()
    os.environ["DATABASE_URL"] = dsn
    os.environ.setdefault("OTO_MCP_MASTER_KEY", "0" * 64)
    dbconn._pool = None
    try:
        from oto_mcp.db import init_db
        init_db()
        _exec("INSERT INTO tenants (slug, name, issuer) VALUES (%s, 'Pilote', %s)",
              (PILOTE, ISSUER))
        _exec("INSERT INTO orgs (id, name) VALUES (%s, 'o') ON CONFLICT DO NOTHING", (ORG,))
        for s in (SUB_T, SUB_NU):
            _exec("INSERT INTO users (sub) VALUES (%s) ON CONFLICT DO NOTHING", (s,))
        tenancy.install(tenancy.IssuerRegistry(tenancy.build(
            "https://auth.oto.ninja/oidc",
            tenants=[{"slug": PILOTE, "issuer": ISSUER}])))
        yield
    finally:
        tenancy.install(prev_registry)
        if dbconn._pool is not None:
            dbconn._pool.close()
        dbconn._pool = prev_pool
        if prev_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = prev_url
        if prev_key is None:
            os.environ.pop("OTO_MCP_MASTER_KEY", None)
        root.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        root.close()


@pytest.fixture
def coffre_vide(live):
    _exec("DELETE FROM connector_instances")
    _exec("DELETE FROM connector_credentials")
    yield


# ── 1. l'existant ne bouge pas ────────────────────────────────────────────────

def test_une_cle_d_org_existante_se_relit_avant_et_apres_la_pose_d_une_cle_tenant(coffre_vide):
    from oto_mcp import org_store
    org_store.set_org_secret(ORG, CONN, "k-org", set_by=SUB_NU)
    assert org_store.get_org_secret(ORG, CONN) == "k-org"
    # Le sceau de la ligne d'org, à l'octet — c'est lui que le lot ne doit pas toucher.
    assert cs._aad("org", str(ORG), CONN) == f"connector_credentials:org:{ORG}:{CONN}"

    tenant_vault.set_tenant_secret(PILOTE, CONN, "k-tenant", set_by="operateur")

    assert org_store.get_org_secret(ORG, CONN) == "k-org"
    assert tenant_vault.get_tenant_secret(PILOTE, CONN) == "k-tenant"


# ── 2. le sceau porte le tenant ───────────────────────────────────────────────

def test_le_sceau_d_une_cle_tenant_ne_se_transplante_pas(live):
    scelle = crypto.encrypt("s", cs._aad(cs.TENANT, PILOTE, CONN))
    assert cs._aad(cs.TENANT, PILOTE, CONN) == f"connector_credentials:tenant:{PILOTE}:{CONN}"
    with pytest.raises(RuntimeError):
        crypto.decrypt(scelle, cs._aad("org", PILOTE, CONN))
    with pytest.raises(RuntimeError):
        crypto.decrypt(scelle, cs._aad(cs.TENANT, "autre", CONN))
    assert crypto.decrypt(scelle, cs._aad(cs.TENANT, PILOTE, CONN)) == "s"


def test_la_cle_tenant_se_pose_se_relit_et_se_retire(coffre_vide):
    tenant_vault.set_tenant_secret(PILOTE, CONN, "k-tenant", set_by="operateur")
    assert tenant_vault.has_tenant_secret(PILOTE, CONN) is True
    assert tenant_vault.get_tenant_secret(PILOTE, CONN) == "k-tenant"
    liste = tenant_vault.list_tenant_secrets(PILOTE)
    assert [(r["provider"], r["set_by"]) for r in liste] == [(CONN, "operateur")]
    assert not any("k-tenant" in str(v) for r in liste for v in r.values())

    assert tenant_vault.delete_tenant_secret(PILOTE, CONN) is True
    assert tenant_vault.get_tenant_secret(PILOTE, CONN) is None
    assert tenant_vault.has_tenant_secret(PILOTE, CONN) is False
    assert tenant_vault.delete_tenant_secret(PILOTE, CONN) is False   # idempotent


def test_le_tenant_primaire_ne_porte_pas_de_cle_tenant(coffre_vide):
    """Les clés partagées de la plateforme SONT ses instances plateforme (avec leurs
    grants) : une clé « tenant oto » serait un second mécanisme pour la même fonction."""
    with pytest.raises(tenant_vault.PrimaryTenantKeyRefused):
        tenant_vault.set_tenant_secret(tenancy.PRIMARY_SLUG, CONN, "k", set_by="x")
    assert _one("SELECT count(*) AS n FROM connector_credentials "
                "WHERE entity_type = 'tenant'")["n"] == 0


# ── 3. l'instance naît à la pose ──────────────────────────────────────────────

def test_l_instance_nait_a_la_pose_et_s_archive_au_retrait(coffre_vide):
    tenant_vault.set_tenant_secret(PILOTE, CONN, "k-tenant", set_by="operateur")
    row = _one("SELECT owner_type, owner_id, connector, account, revoked_at "
               "FROM connector_instances WHERE owner_type = 'tenant'")
    assert (row["owner_type"], row["owner_id"], row["connector"], row["account"],
            row["revoked_at"]) == ("tenant", PILOTE, CONN, "", None)

    tenant_vault.delete_tenant_secret(PILOTE, CONN)
    row = _one("SELECT revoked_at, revoked_reason FROM connector_instances "
               "WHERE owner_type = 'tenant'")
    assert row["revoked_at"] is not None and row["revoked_reason"] == "credential_removed"


# ── 4. la résolution réelle ───────────────────────────────────────────────────

@pytest.fixture
def contexte(monkeypatch):
    """L'org de contexte, hors session MCP — le seam `current_org` la rend."""
    monkeypatch.setattr(access, "require_connector_access", lambda p, s=None: None)
    monkeypatch.setattr(access, "current_org", lambda sub: ORG)
    monkeypatch.setattr(access, "current_group", lambda sub: None)
    monkeypatch.setattr(access, "project_pinned_instance", lambda p: None)


def test_la_resolution_sert_la_cle_tenant_aux_comptes_du_tenant_et_a_eux_seuls(
        coffre_vide, contexte):
    from mcp.shared.exceptions import McpError
    tenant_vault.set_tenant_secret(PILOTE, CONN, "k-tenant", set_by="operateur")

    rc = access.resolve_credential(CONN, sub=SUB_T)
    assert (rc.key, rc.mode, rc.entity_type, rc.entity_id, rc.is_platform) == (
        "k-tenant", "tenant", cs.TENANT, PILOTE, False)
    assert access.status_for(SUB_T, org=ORG, group=None)["providers"][CONN]["mode"] == "tenant"

    # Même org, sub NU : le barreau tenant n'existe pas pour lui — et rien d'autre ne
    # résout dans cette base (aucune clé plateforme posée).
    with pytest.raises(McpError):
        access.resolve_credential(CONN, sub=SUB_NU)
    assert access.status_for(SUB_NU, org=ORG, group=None)["providers"][CONN]["mode"] == "forbidden"


def test_une_cle_d_org_prime_sur_la_cle_tenant_pour_un_compte_du_tenant(coffre_vide, contexte):
    from oto_mcp import org_store
    tenant_vault.set_tenant_secret(PILOTE, CONN, "k-tenant", set_by="operateur")
    org_store.set_org_secret(ORG, CONN, "k-org", set_by=SUB_NU)
    rc = access.resolve_credential(CONN, sub=SUB_T)
    assert (rc.key, rc.mode) == ("k-org", "org")
    # Retirer la clé d'org fait retomber sur le tenant — la cascade, pas un repli.
    org_store.delete_org_secret(ORG, CONN)
    rc = access.resolve_credential(CONN, sub=SUB_T)
    assert (rc.key, rc.mode) == ("k-tenant", "tenant")
