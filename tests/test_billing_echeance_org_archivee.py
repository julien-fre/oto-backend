"""Une org archivée n'est plus prélevée (oto-backend#400).

`archive_org` est un soft-delete pur (`archived_at`, aucune FK touchée) : l'abonnement de
l'org restait `active`, et `due_subscriptions` — qui ne joint pas `orgs` — continuait de
le rendre au billing_runner. Une org invisible de tous les listings était donc prélevée,
sans que personne puisse la résilier.

Deux lectures portent le prédicat d'échéance et DOIVENT rester d'accord :
- `due_subscriptions` (la sélection du tick, sans verrou) ;
- `billing_reservation.reserver_echeance` (la relecture SOUS verrou, qui ferme la course
  entre deux processus). Si seule la première filtrait, une org archivée ENTRE la
  sélection et la réservation serait quand même tirée.

Banc : un PostgreSQL réel — le défaut est un prédicat SQL, qu'un stub ne mesurerait pas.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

from oto_mcp.db import billing as db_billing
from oto_mcp.db import billing_reservation

psycopg = pytest.importorskip("psycopg")


@pytest.fixture(scope="module")
def base(pg_module_dsn):
    avant = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = pg_module_dsn
    try:
        from oto_mcp.db import init_db
        init_db()
        yield pg_module_dsn
    finally:
        if avant is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = avant


def _org_echue(nom: str) -> int:
    """Une org et un abonnement payant échu depuis une heure."""
    from oto_mcp.db._conn import _connect

    with _connect() as conn:
        org = conn.execute("INSERT INTO orgs (name) VALUES (%s) RETURNING id",
                           (nom,)).fetchone()["id"]
    du = (datetime.now(timezone.utc) - timedelta(hours=1)).replace(microsecond=0)
    db_billing.upsert_org_subscription(org, plan="standard", customer_id="cst_banc",
                                       mandate_id="mdt_banc", status="active",
                                       current_period_end=du, next_billing_at=du)
    return org


def _archiver(org: int) -> None:
    """Pose `archived_at` À LA MAIN : `archive_org` refuse désormais une org qui porte un
    abonnement qui prélève (#400, piste 1 — `test_org_archivage_abonnement_400.py`). Ce
    banc-ci garde le FILET, celui des orgs DÉJÀ archivées avant ce refus : leur abonnement
    reste `active`, et le tick ne doit pas les tirer."""
    from oto_mcp.db._conn import _connect

    with _connect() as conn:
        conn.execute("UPDATE orgs SET archived_at = now() WHERE id = %s", (org,))


def _dues() -> set[int]:
    # `limit` large : la base du module peut porter d'autres échéances.
    return {l["org_id"] for l in db_billing.due_subscriptions(limit=1000)}


def test_une_org_active_reste_due(base):
    org = _org_echue("banc-400-active")
    assert org in _dues()


def test_une_org_archivee_n_est_plus_due(base):
    org = _org_echue("banc-400-archivee")
    assert org in _dues()          # contrôle : due tant qu'elle n'est pas archivée
    _archiver(org)
    assert org not in _dues()


def test_la_reservation_relit_avec_le_meme_predicat(base):
    """Archivée entre la sélection et la réservation : rien n'est tiré."""
    org = _org_echue("banc-400-course")
    ligne = next(l for l in db_billing.due_subscriptions(limit=1000)
                 if l["org_id"] == org)
    _archiver(org)
    with billing_reservation.reserver_echeance(ligne) as relue:
        assert relue is None


def test_la_reservation_rend_toujours_la_ligne_d_une_org_active(base):
    org = _org_echue("banc-400-reservee")
    ligne = next(l for l in db_billing.due_subscriptions(limit=1000)
                 if l["org_id"] == org)
    with billing_reservation.reserver_echeance(ligne) as relue:
        assert relue is not None and relue["org_id"] == org


def test_desarchiver_rend_l_echeance_intacte(base):
    """Rien n'est perdu : l'abonnement n'est pas touché, l'échéance revient à la
    désarchivation (la seule voie est un UPDATE en base, cf. `archive_org`)."""
    from oto_mcp.db._conn import _connect

    org = _org_echue("banc-400-retour")
    _archiver(org)
    assert org not in _dues()
    with _connect() as conn:
        conn.execute("UPDATE orgs SET archived_at = NULL WHERE id = %s", (org,))
    assert org in _dues()
