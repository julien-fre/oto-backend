"""Le client Pennylane d'une org se POSE, il ne se devine pas (#917).

La facture F-2026-09-7 est née en doublon chez le comptable : le code rapprochait
le client par une référence frappée par oto (`oto-org-<id>`), qu'un client créé
à la main ne porte pas — « introuvable », donc créé une seconde fois. Décision
d'Alexis (09/09/2026) : l'identifiant est posé À LA MAIN par un admin plateforme
sur l'identité de facturation, et le code le respecte ; pas de rapprochement.

Ce fichier fige quatre choses, et la première est le cœur du sujet :

1. **le formulaire côté org laisse l'id intact** — `me.billing.identity.set`
   remplace la fiche EN BLOC ; si la colonne entrait dans son `SET`, un org_admin
   qui resauvegarde effacerait l'id sans erreur ni trace, et on ne le verrait qu'à
   la facture suivante, en doublon. Prouvé par sa chute : ajouter la colonne au
   `SET` de `upsert_billing_identity` rougit `test_le_formulaire_cote_org_…` ;
2. **l'id se pose SUR une fiche**, jamais à la place d'une fiche ;
3. **l'admin le voit et le pose, l'org ne le voit pas** — c'est un lien vers la
   comptabilité d'Otomata, pas une donnée du client ;
4. **la colonne arrive sur une base qui existe déjà** — prod et preprod partagent
   la base (`docs/live-migrations.md`), le `CREATE TABLE` n'y est jamais rejoué.

Un vrai PostgreSQL (fixture `pg_dsn`, jetable) : la question est posée à la BASE,
jamais au retour d'un appel.
"""
from __future__ import annotations

import uuid

import pytest

from oto_mcp.capabilities._types import AuthzDenied, RawCtx, ResolvedCtx
from oto_mcp.capabilities.registry import by_key

CLIENT_PENNYLANE = 1472767250432


def _org(nom: str = "ACME") -> int:
    from oto_mcp.db._conn import _connect

    with _connect() as conn:
        return conn.execute(
            "INSERT INTO orgs (name) VALUES (%s) RETURNING id", (nom,)
        ).fetchone()["id"]


def _fiche(**over):
    from oto_mcp.capabilities.billing_identity import IdentityInput

    base = dict(legal_name="ACME SAS", country_code="FR",
                address_line="1 rue de la Paix", postal_code="13001",
                city="Marseille", billing_email="compta@acme.test")
    base.update(over)
    return IdentityInput(**base)


def _id_en_base(org: int):
    from oto_mcp.db._conn import _connect

    with _connect() as conn:
        return conn.execute(
            "SELECT pennylane_customer_id FROM billing_identities WHERE org_id = %s",
            (org,)).fetchone()["pennylane_customer_id"]


# ── 1. le cœur : un geste légitime côté org n'efface pas l'id ────────────────

def test_le_formulaire_cote_org_laisse_lid_intact(live):
    """L'org_admin rouvre son formulaire et le resauvegarde — champ par champ
    identique ou non, peu importe : la fiche est remplacée en bloc, et l'id posé
    par l'admin plateforme doit survivre au remplacement."""
    from oto_mcp.db import billing as db_billing

    org = _org()
    by_key("me.billing.identity.set").handler(ResolvedCtx(sub="u-org", org_id=org), _fiche())
    assert db_billing.set_billing_identity_pennylane_customer_id(org, CLIENT_PENNYLANE)
    assert _id_en_base(org) == CLIENT_PENNYLANE

    # Resauvegarde côté org, avec un changement réel (nouvelle adresse) : c'est
    # exactement le geste du piège.
    by_key("me.billing.identity.set").handler(
        ResolvedCtx(sub="u-org", org_id=org), _fiche(address_line="2 rue Neuve"))

    ligne = db_billing.get_billing_identity(org)
    assert ligne["address_line"] == "2 rue Neuve", "la fiche a bien été remplacée"
    assert _id_en_base(org) == CLIENT_PENNYLANE, (
        "l'id Pennylane a été effacé par le formulaire côté org — la colonne est "
        "entrée dans le SET de `upsert_billing_identity` (#917)")


# ── 2. l'id se pose SUR une fiche ────────────────────────────────────────────

def test_lid_ne_se_pose_pas_sans_fiche(live):
    from oto_mcp.db import billing as db_billing

    org = _org("SANS FICHE")
    assert db_billing.set_billing_identity_pennylane_customer_id(org, CLIENT_PENNYLANE) is False
    assert db_billing.get_billing_identity(org) is None, "aucune fiche n'a été créée"


def test_none_retire_la_designation(live):
    from oto_mcp.db import billing as db_billing

    org = _org()
    by_key("me.billing.identity.set").handler(ResolvedCtx(sub="u-org", org_id=org), _fiche())
    db_billing.set_billing_identity_pennylane_customer_id(org, CLIENT_PENNYLANE)
    assert db_billing.set_billing_identity_pennylane_customer_id(org, None)
    assert _id_en_base(org) is None


# ── 3. l'admin le voit et le pose ; l'org ne le voit pas ─────────────────────

def _monde_admin(monkeypatch):
    from oto_mcp import access, org_store

    monkeypatch.setattr(access, "is_platform_operator", lambda sub: sub == "operateur")
    monkeypatch.setattr(access, "current_org", lambda sub: None)
    monkeypatch.setattr(access, "get_user_role", lambda sub: "admin")
    monkeypatch.setattr(org_store, "get_org", lambda org_id: {"id": org_id})


def test_ladmin_pose_la_fiche_et_lid_dans_le_meme_geste(live, monkeypatch):
    from oto_mcp.capabilities.billing_identity_admin import AdminIdentityInput

    _monde_admin(monkeypatch)
    org = _org()
    cap = by_key("admin.orgs.billing_identity.set")
    ctx = cap.authz(RawCtx(sub="operateur"), None)
    vue = cap.handler(ctx, AdminIdentityInput(
        org_id=org, pennylane_customer_id=CLIENT_PENNYLANE, **_fiche().model_dump()))
    assert vue["pennylane_customer_id"] == CLIENT_PENNYLANE
    assert vue["identity"]["legal_name"] == "ACME SAS"
    assert vue["missing"] == []
    assert _id_en_base(org) == CLIENT_PENNYLANE

    # Reposté sans id : la désignation est RETIRÉE — le formulaire admin remplace,
    # il ne fusionne pas (comme côté org).
    vue = cap.handler(ctx, AdminIdentityInput(org_id=org, **_fiche().model_dump()))
    assert vue["pennylane_customer_id"] is None
    assert _id_en_base(org) is None


def test_lorg_ne_voit_pas_lid_et_ladmin_le_voit(live, monkeypatch):
    from oto_mcp.db import billing as db_billing

    _monde_admin(monkeypatch)
    org = _org()
    by_key("me.billing.identity.set").handler(ResolvedCtx(sub="u-org", org_id=org), _fiche())
    db_billing.set_billing_identity_pennylane_customer_id(org, CLIENT_PENNYLANE)

    vue_org = by_key("me.billing.identity.get").handler(
        ResolvedCtx(sub="u-org", org_id=org), None)
    assert "pennylane_customer_id" not in vue_org
    assert "pennylane_customer_id" not in vue_org["identity"]

    from oto_mcp.capabilities.billing_identity_admin import AdminOrgIdInput
    cap = by_key("admin.orgs.billing_identity.get")
    vue_admin = cap.handler(cap.authz(RawCtx(sub="operateur"), None), AdminOrgIdInput(org_id=org))
    assert vue_admin["pennylane_customer_id"] == CLIENT_PENNYLANE
    assert vue_admin["identity"]["legal_name"] == "ACME SAS"


def test_un_org_admin_ne_passe_pas_la_porte_admin(monkeypatch):
    _monde_admin(monkeypatch)
    for key in ("admin.orgs.billing_identity.get", "admin.orgs.billing_identity.set"):
        with pytest.raises(AuthzDenied) as refus:
            by_key(key).authz(RawCtx(sub="u-org-admin"), None)
        assert refus.value.status == 403


def test_une_org_inconnue_est_un_404_nomme(live, monkeypatch):
    from oto_mcp import org_store
    from oto_mcp.capabilities.billing_identity_admin import AdminOrgIdInput

    _monde_admin(monkeypatch)
    monkeypatch.setattr(org_store, "get_org", lambda org_id: None)
    with pytest.raises(AuthzDenied) as refus:
        by_key("admin.orgs.billing_identity.get").handler(
            ResolvedCtx(sub="operateur"), AdminOrgIdInput(org_id=999_999))
    assert (refus.value.status, refus.value.code) == (404, "unknown_org")


# ── 4. la colonne arrive sur une base qui existe déjà ────────────────────────

def test_la_colonne_arrive_par_le_boot_sur_une_base_existante(live):
    """Prod et preprod partagent la base : `CREATE TABLE IF NOT EXISTS` y est
    sauté, seul l'`ALTER … ADD COLUMN IF NOT EXISTS` de `_init.py` pose la colonne.
    On la retire, on reboote, elle doit revenir — et le boot doit passer."""
    from oto_mcp.db import init_db
    from oto_mcp.db._conn import _connect

    def colonne():
        with _connect() as conn:
            return conn.execute(
                "SELECT data_type FROM information_schema.columns WHERE table_name = "
                "'billing_identities' AND column_name = 'pennylane_customer_id'"
            ).fetchone()

    with _connect() as conn:
        conn.execute("ALTER TABLE billing_identities DROP COLUMN pennylane_customer_id")
    assert colonne() is None
    init_db()
    assert colonne()["data_type"] == "bigint"
