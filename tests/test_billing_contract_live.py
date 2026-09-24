"""L'abonnement réglé HORS PLATEFORME (contrat, virement), sur une vraie base.

Ce n'est pas un don : un abonnement payé ailleurs, déclaré par un admin. Ce que ces
bancs tiennent :
- déclaré → droits du plan (source `contract`) jusqu'à sa fin, et `members_max` ;
- échu → fermés ; renouvelé (re-déclaré) → rouverts jusqu'à la nouvelle date ;
- sans date de fin → ouverts sans échéance ; la résiliation pose la fin de la période
  en cours (ou la date donnée), et les droits se ferment passé cette date ;
- le runner ne le prélève jamais ; `status` le montre comme tel ;
- la révision `0008_billing_contracts` pose la table, se défait, et le boot la sait posée.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from oto_mcp import billing, billing_droits, billing_runner
from oto_mcp.access.entitlements import MEMBERS_MAX, PLATFORM_UNMETERED, org_has
from oto_mcp.db import billing as db_billing
from oto_mcp.db._conn import _connect

MAINTENANT = datetime.now(timezone.utc).replace(microsecond=0)
DANS_UN_AN = MAINTENANT + timedelta(days=365)
HIER = MAINTENANT - timedelta(days=1)


def _org() -> int:
    with _connect() as conn:
        return conn.execute("INSERT INTO orgs (name) VALUES (%s) RETURNING id",
                            (f"org-{uuid.uuid4().hex[:8]}",)).fetchone()["id"]


def _lignes(org: int) -> dict:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT right_key, source, value, EXTRACT(EPOCH FROM expires_at) AS fin "
            "FROM org_entitlements WHERE org_id = %s", (org,)).fetchall()
    return {(r["right_key"], r["source"]):
            (r["value"], None if r["fin"] is None
             else datetime.fromtimestamp(float(r["fin"]), timezone.utc))
            for r in rows}


@pytest.fixture(autouse=True)
def _sans_ecran(monkeypatch):
    monkeypatch.setattr(billing, "_hosted_by_partner", lambda org_id: False)
    monkeypatch.setattr(billing.billing_grants, "granted_benefits", lambda *a, **k: [])
    monkeypatch.setattr(billing.billing_grants, "monthly_usage", lambda *a, **k: None)


def _declarer(org, **kw):
    kw.setdefault("seats", 12)
    return billing.admin_set_contract(org, "standard", granted_by="admin-test", **kw)


def test_declare_ouvre_les_droits_jusqu_a_la_fin_et_declare_les_licences(live):
    org = _org()
    st = _declarer(org, ends_at=DANS_UN_AN, unit_amount=2500, reference="CTR-TEST-1")
    assert _lignes(org) == {("unipile", "contract"): (1, DANS_UN_AN),
                            (PLATFORM_UNMETERED, "contract"): (1, DANS_UN_AN),
                            (MEMBERS_MAX, "contract"): (12, DANS_UN_AN)}
    assert org_has(org, "unipile")
    assert st["provider"] == "contract" and st["comp"] is False
    assert st["contract"]["seats"] == 12 and st["contract"]["reference"] == "CTR-TEST-1"
    assert st["subscribed"] is True and st["amount_ttc"] is None


def test_echu_les_droits_sont_fermes_puis_le_renouvellement_les_rouvre(live):
    org = _org()
    _declarer(org, starts_at=MAINTENANT - timedelta(days=60), ends_at=HIER)
    assert not org_has(org, "unipile")
    assert billing.status(org)["subscribed"] is False
    _declarer(org, starts_at=MAINTENANT - timedelta(days=60), ends_at=DANS_UN_AN)
    assert org_has(org, "unipile")
    assert _lignes(org)[("unipile", "contract")] == (1, DANS_UN_AN)


def test_sans_date_de_fin_les_droits_sont_ouverts_sans_echeance(live):
    org = _org()
    _declarer(org)
    assert _lignes(org)[("unipile", "contract")] == (1, None)
    assert org_has(org, "unipile")


def test_la_resiliation_pose_la_fin_de_la_periode_en_cours(live):
    org = _org()
    debut = MAINTENANT - timedelta(days=45)
    _declarer(org, starts_at=debut)
    st = billing.admin_cancel_contract(org)
    fin = billing._contract_period_end(debut, "month", datetime.now(timezone.utc))
    assert _lignes(org)[("unipile", "contract")] == (1, fin)
    assert MAINTENANT < fin <= MAINTENANT + timedelta(days=31)
    assert st["contract"]["ends_at"] is not None and st["canceled_at"] is not None
    assert org_has(org, "unipile"), "le droit court jusqu'à la fin posée"


def test_resilie_a_une_date_passee_les_droits_sont_fermes(live):
    org = _org()
    _declarer(org, starts_at=MAINTENANT - timedelta(days=45))
    billing.admin_cancel_contract(org, ends_at=HIER)
    assert not org_has(org, "unipile")


def test_un_debut_a_venir_n_ouvre_rien_avant_lui(live):
    org = _org()
    _declarer(org, starts_at=MAINTENANT + timedelta(days=3))
    assert ("unipile", "contract") in _lignes(org)
    assert not org_has(org, "unipile")


def test_le_runner_ne_prelève_jamais_un_contrat(live, monkeypatch):
    org = _org()
    _declarer(org, ends_at=DANS_UN_AN)
    assert org not in {s["org_id"] for s in db_billing.due_subscriptions(limit=500)}
    monkeypatch.setattr(billing_runner.mollie_client, "create_recurring_payment",
                        lambda *a, **k: pytest.fail("un contrat ne se prélève pas"))
    ligne = db_billing.get_org_subscription(org)
    assert billing_runner._charge_one(ligne, MAINTENANT) == "skipped"


def test_refus_de_remplacer_un_abonnement_paye_actif(live):
    org = _org()
    db_billing.upsert_org_subscription(org, plan="standard", provider="mollie",
                                       status="active", current_period_end=DANS_UN_AN,
                                       next_billing_at=DANS_UN_AN)
    with pytest.raises(ValueError, match="paid_subscription"):
        _declarer(org)


@pytest.mark.parametrize("kw,code", [
    ({"seats": 0}, "invalid_seats"),
    ({"interval": "week"}, "invalid_interval"),
    ({"starts_at": MAINTENANT, "ends_at": HIER}, "invalid_period"),
])
def test_les_parametres_invalides_sont_refuses(live, kw, code):
    with pytest.raises(ValueError, match=code):
        _declarer(_org(), **kw)


def test_l_org_ne_resilie_pas_elle_meme_un_contrat(live):
    org = _org()
    _declarer(org)
    with pytest.raises(ValueError, match="contract_subscription"):
        billing.cancel(org)
    with pytest.raises(ValueError, match="contract_subscription"):
        billing.admin_clear_plan(org)


def test_la_reprise_rejoue_un_contrat_sans_effet(live):
    org = _org()
    _declarer(org, ends_at=DANS_UN_AN)
    avant = _lignes(org)
    out = billing_droits.reconcilier(org)
    assert out["retires"] == 0 and _lignes(org) == avant


# ── la révision ──────────────────────────────────────────────────────────────

RACINE = Path(__file__).resolve().parent.parent


def _alembic():
    from alembic.config import Config
    cfg = Config(str(RACINE / "alembic.ini"))
    cfg.set_main_option("script_location", str(RACINE / "oto_mcp" / "db" / "migrations"))
    return cfg


def _a_la_table(dsn: str) -> bool:
    import psycopg
    with psycopg.connect(dsn) as c:
        return c.execute("SELECT to_regclass('billing_contracts') IS NOT NULL"
                         ).fetchone()[0]


def test_la_revision_pose_la_table_se_defait_et_le_boot_la_sait_posee(live, pg_module_dsn):
    import psycopg
    from alembic import command

    from oto_mcp.db import init_db
    assert _a_la_table(pg_module_dsn), "une base NEUVE la reçoit du démarrage"
    with psycopg.connect(pg_module_dsn, autocommit=True) as c:
        c.execute("DROP TABLE billing_contracts")
    cfg = _alembic()
    command.stamp(cfg, "0007_jetons_revocation_tracee")
    command.upgrade(cfg, "0008_billing_contracts")
    assert _a_la_table(pg_module_dsn), "la révision n'a rien écrit"
    command.downgrade(cfg, "0007_jetons_revocation_tracee")
    assert not _a_la_table(pg_module_dsn), "le retour arrière n'a rien retiré"
    command.upgrade(cfg, "head")
    init_db()
    assert _a_la_table(pg_module_dsn)
