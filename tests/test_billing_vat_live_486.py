"""La TVA côté BASE (#486) — le DDL, la migration vivante, et ce qui n'est pas réécrit.

Le reste du lot se teste sur un store simulé (`test_billing_vat_486.py`). Deux
choses ne peuvent PAS l'être, et ce sont justement celles qui touchent la
production :

1. **le DDL** — une table neuve (`billing_identities`) et cinq colonnes ajoutées à
   une table qui existe déjà. Un store en mémoire dirait « oui » à n'importe quel
   SQL ;
2. **la migration VIVANTE** — prod et preprod partagent la MÊME base
   (`docs/live-migrations.md`) : les `ALTER … ADD COLUMN IF NOT EXISTS` du boot
   s'appliquent à une table `billing_payments` qui porte déjà des lignes, celles
   des encaissements du 25/08. Le boot doit passer, et ces lignes doivent survivre
   TELLES QUELLES — un backfill leur inventerait une TVA qu'elles n'ont jamais eue.

D'où un vrai PostgreSQL (fixture `pg_dsn`, jetable), et la question posée à la
BASE, jamais au retour d'un appel.
"""
from __future__ import annotations

import uuid

import pytest


@pytest.fixture(scope="module")
def live(pg_dsn):
    import os

    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    name = "oto_tva_" + uuid.uuid4().hex[:8]
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


def _org(nom: str = "ACME") -> int:
    """Une org réelle : les deux tables du lot ont une FK vers `orgs`, et une FK ne
    se teste pas sur un identifiant inventé."""
    from oto_mcp.db._conn import _connect

    with _connect() as conn:
        return conn.execute(
            "INSERT INTO orgs (name) VALUES (%s) RETURNING id", (nom,)
        ).fetchone()["id"]


# ── l'identité ───────────────────────────────────────────────────────────────

def test_l_identite_fait_l_aller_retour_et_se_remplace_en_bloc(live):
    from oto_mcp.db import billing as db_billing

    org = _org()
    assert db_billing.get_billing_identity(org) is None, "aucune identité au départ"

    db_billing.upsert_billing_identity(
        org, legal_name="ACME SAS", country_code="FR",
        address_line="1 rue de la Paix", postal_code="13001", city="Marseille",
        billing_email="compta@acme.test")
    ligne = db_billing.get_billing_identity(org)
    assert ligne["legal_name"] == "ACME SAS" and ligne["country_code"] == "FR"
    assert ligne["vat_number"] is None

    # Un second appel REMPLACE : un champ omis est effacé. C'est un formulaire d'une
    # page, et un merge silencieux laisserait un vieux numéro de TVA sur une société
    # qui vient de déclarer ne plus être assujettie.
    db_billing.upsert_billing_identity(
        org, legal_name="ACME SA", country_code="BE", vat_number="BE0123456789",
        address_line="2 rue Neuve", postal_code="1000", city="Bruxelles")
    ligne = db_billing.get_billing_identity(org)
    assert (ligne["legal_name"], ligne["country_code"]) == ("ACME SA", "BE")
    assert ligne["vat_number"] == "BE0123456789"
    assert ligne["billing_email"] is None, "champ omis = effacé, pas conservé"


def test_une_org_par_identite_et_la_suppression_cascade(live):
    from oto_mcp.db._conn import _connect
    from oto_mcp.db import billing as db_billing

    org = _org("EPHEMERE")
    db_billing.upsert_billing_identity(org, legal_name="X", country_code="FR")
    with _connect() as conn:
        conn.execute("DELETE FROM orgs WHERE id = %s", (org,))
        reste = conn.execute(
            "SELECT COUNT(*) AS n FROM billing_identities WHERE org_id = %s", (org,)
        ).fetchone()["n"]
    assert reste == 0, "l'identité de facturation ne survit pas à son org"


# ── la décomposition journalisée ─────────────────────────────────────────────

def test_le_journal_porte_la_decomposition_et_la_relit(live):
    from oto_mcp.db import billing as db_billing
    from oto_mcp import billing_vat

    org = _org()
    tax = billing_vat.tax_for(1900, "FR", None)
    db_billing.insert_billing_payment(
        org, "initial", tax["amount_ttc"], payment_intent_id="tr_neuf",
        status="paid", tax=tax)

    ligne = db_billing.list_billing_payments(org)[0]
    assert ligne["amount"] == 2280, "le journal dit ce que le PSP a pris : le TTC"
    assert ligne["amount_ht"] == 1900 and ligne["vat_amount"] == 380
    assert ligne["vat_rate_bps"] == 2000
    assert ligne["country_code"] == "FR" and ligne["vat_scheme"] == "fr_ttc"
    # Le taux revient en INT, pas en Decimal : une colonne NUMERIC ressortirait en
    # `Decimal`, que le sérialiseur JSON des réponses refuse — 500 à la lecture.
    assert type(ligne["vat_rate_bps"]) is int


def test_une_ligne_sans_decomposition_reste_ecrivable_et_se_lit_en_null(live):
    """Le cas des DEUX encaissements du 25/08 : `amount_ht IS NULL` est ce qui les
    distingue d'une ligne calculée. Jamais un zéro — un zéro affirmerait une
    exonération, et ce n'est pas ce qui s'est passé."""
    from oto_mcp.db import billing as db_billing

    org = _org()
    db_billing.insert_billing_payment(org, "initial", 1900,
                                      payment_intent_id="tr_ancien", status="paid")
    ligne = db_billing.list_billing_payments(org)[0]
    assert ligne["amount"] == 1900
    assert ligne["amount_ht"] is None and ligne["vat_amount"] is None
    assert ligne["vat_scheme"] is None


# ── la migration VIVANTE ─────────────────────────────────────────────────────

def test_le_boot_ajoute_les_colonnes_a_une_table_deja_peuplee(live):
    """La vraie forme du risque : la base est PARTAGÉE prod/preprod, `billing_payments`
    existe déjà avec ses lignes, et le boot doit poser les colonnes SANS les perdre.

    On reconstitue l'état d'AVANT (colonnes retirées, une ligne d'encaissement en
    place), puis on rejoue `init_db` — exactement ce que fait un déploiement."""
    from oto_mcp.db import init_db
    from oto_mcp.db._conn import _connect

    org = _org()
    neuves = ("amount_ht", "vat_rate_bps", "vat_amount", "country_code", "vat_scheme")
    with _connect() as conn:
        for col in neuves:
            conn.execute(f"ALTER TABLE billing_payments DROP COLUMN IF EXISTS {col}")
        conn.execute(
            "INSERT INTO billing_payments (org_id, kind, amount, currency, "
            "payment_intent_id, status) VALUES (%s,'initial',1900,'eur','tr_25_08','paid')",
            (org,))

    init_db()          # le boot d'un déploiement, rejoué

    with _connect() as conn:
        colonnes = {r["column_name"] for r in conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'billing_payments'")}
        ligne = conn.execute(
            "SELECT * FROM billing_payments WHERE payment_intent_id = 'tr_25_08'"
        ).fetchone()

    assert set(neuves) <= colonnes, "le boot doit poser les cinq colonnes"
    # …et l'encaissement historique est intact, sans décomposition inventée.
    assert ligne["amount"] == 1900 and ligne["status"] == "paid"
    assert all(ligne[c] is None for c in neuves), "aucun backfill : ce serait un mensonge"


def test_rejouer_le_boot_est_idempotent(live):
    """Un déploiement peut rebooter deux fois (rollback, restart) : les `ADD COLUMN
    IF NOT EXISTS` doivent traverser sans rien casser ni rien réécrire."""
    from oto_mcp.db import init_db
    from oto_mcp.db._conn import _connect
    from oto_mcp.db import billing as db_billing

    org = _org()
    db_billing.upsert_billing_identity(org, legal_name="STABLE SAS", country_code="FR")
    db_billing.insert_billing_payment(
        org, "renewal", 2280, status="paid",
        tax={"amount_ht": 1900, "vat_rate_bps": 2000, "vat_amount": 380,
             "country_code": "FR", "vat_scheme": "fr_ttc"})

    init_db()
    init_db()

    assert db_billing.get_billing_identity(org)["legal_name"] == "STABLE SAS"
    with _connect() as conn:
        n = conn.execute("SELECT COUNT(*) AS n FROM billing_payments WHERE org_id=%s",
                         (org,)).fetchone()["n"]
    assert n == 1, "rejouer le boot ne duplique rien"
    assert db_billing.list_billing_payments(org)[0]["amount_ht"] == 1900
