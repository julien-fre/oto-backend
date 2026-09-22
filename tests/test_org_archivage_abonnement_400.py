"""Une org qui porte un abonnement qui prélève ne s'archive pas (oto-backend#400, piste 1).

`archive_org` est un soft-delete pur : l'org disparaît de tous les listings, personne ne
peut plus la résilier, et son abonnement continuait d'être prélevé. Décision d'Alexis
(piste 1 de l'issue) : **on refuse l'archivage** tant que l'abonnement n'est pas résilié,
avec un message qui dit quoi faire. Le filtre d'échéance de `due_subscriptions` reste le
filet des orgs DÉJÀ archivées (`test_billing_echeance_org_archivee.py`).

Ce qui « prélève » (`db.billing.ABONNEMENT_QUI_PRELEVE`) : `active` ou `past_due`, ni
résilié à fin de période (`canceled_at` posé : plus d'échéance), ni offert (`comp` : jamais
tiré, rien à « résilier d'abord » côté utilisateur).

Banc : un PostgreSQL réel — le défaut est un prédicat SQL et une course entre deux
transactions, qu'un stub ne mesurerait pas. La course est rendue DÉTERMINISTE en tenant
une transaction ouverte à la main plutôt qu'en tirant deux threads au hasard : un vert
obtenu « parce que la course n'a pas eu lieu » ne prouverait rien.
"""
from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from oto_mcp import org_store
from oto_mcp.capabilities import _types
from oto_mcp.capabilities.orgs import admin as orgs_admin
from oto_mcp.capabilities.orgs import update as orgs_update
from oto_mcp.db import billing as db_billing

psycopg = pytest.importorskip("psycopg")

CODE = "org_has_active_subscription"


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


def _org(nom: str) -> int:
    from oto_mcp.db._conn import _connect

    with _connect() as conn:
        return conn.execute("INSERT INTO orgs (name) VALUES (%s) RETURNING id",
                            (nom,)).fetchone()["id"]


def _abonner(org: int, **kw) -> None:
    du = (datetime.now(timezone.utc) + timedelta(days=10)).replace(microsecond=0)
    db_billing.upsert_org_subscription(
        org, plan="standard", customer_id="cst_banc", mandate_id="mdt_banc",
        current_period_end=du, next_billing_at=du, **kw)


def _archivee(org: int) -> bool:
    from oto_mcp.db._conn import _connect

    with _connect() as conn:
        return conn.execute("SELECT archived_at FROM orgs WHERE id = %s",
                            (org,)).fetchone()["archived_at"] is not None


# ── le refus, selon l'état de l'abonnement ────────────────────────────────────

@pytest.mark.parametrize("statut", ["active", "past_due"])
def test_un_abonnement_qui_preleve_refuse_l_archivage(base, statut):
    org = _org(f"banc-400-{statut}")
    _abonner(org, status=statut)
    with pytest.raises(org_store.OrgAvecAbonnementActif):
        org_store.archive_org(org)
    assert not _archivee(org), "l'org ne doit PAS être archivée : le refus est atomique"


def test_sans_abonnement_l_archivage_passe(base):
    org = _org("banc-400-sans")
    assert org_store.archive_org(org) is True
    assert _archivee(org)


def test_un_abonnement_resilie_n_empeche_rien(base):
    org = _org("banc-400-canceled")
    _abonner(org, status="canceled")
    assert org_store.archive_org(org) is True


def test_resilie_a_fin_de_periode_n_empeche_rien(base):
    """Le statut RESTE `active` jusqu'à la fin de période (entitlement) mais `canceled_at`
    est posé et `next_billing_at` coupé : plus rien ne sera prélevé, « résilie d'abord »
    serait absurde — l'utilisateur vient de le faire."""
    org = _org("banc-400-fin-de-periode")
    _abonner(org, status="active")
    assert db_billing.mark_cancel_at_period_end(org) is True
    assert org_store.archive_org(org) is True


def test_un_abonnement_offert_n_empeche_rien(base):
    """`comp` = accordé par un admin, jamais tiré, sans PSP derrière : l'utilisateur n'a
    rien à résilier, le refuser le laisserait sans issue."""
    org = _org("banc-400-comp")
    db_billing.set_comp_subscription(org, "standard")
    assert org_store.archive_org(org) is True


def test_archiver_une_org_deja_archivee_reste_idempotent(base):
    """`OrgArchived` promet `archived:false` (« c'était déjà fait ») : le nouveau refus ne
    doit pas le transformer en erreur pour une org archivée qui porte un abonnement."""
    org = _org("banc-400-idempotent")
    _abonner(org, status="active")
    from oto_mcp.db._conn import _connect

    with _connect() as conn:                     # une org DÉJÀ archivée (préexistante)
        conn.execute("UPDATE orgs SET archived_at = now() WHERE id = %s", (org,))
    assert org_store.archive_org(org) is False


# ── les deux points d'entrée de l'archivage ───────────────────────────────────

def test_l_archivage_self_service_repond_409(base):
    org = _org("banc-400-self-service")
    _abonner(org, status="active")
    ctx = SimpleNamespace(sub="sub-banc-400")
    with pytest.raises(_types.AuthzDenied) as e:
        orgs_update._archive_org(ctx, orgs_update.OrgIdInput(org_id=org))
    assert e.value.status == 409 and e.value.code == CODE
    assert "abonnement" in str(e.value).lower()      # dit quoi faire, pas seulement non
    assert not _archivee(org)


def test_l_archivage_admin_repond_409(base):
    org = _org("banc-400-admin")
    _abonner(org, status="past_due")
    ctx = SimpleNamespace(sub="sub-banc-400-admin")
    with pytest.raises(_types.AuthzDenied) as e:
        orgs_admin._archive_org(ctx, orgs_admin.OrgIdInput(org_id=org))
    assert e.value.status == 409 and e.value.code == CODE
    assert not _archivee(org)


def test_le_409_est_declare_dans_les_deux_capacites(base):
    """Un 409 nouveau sur un endpoint existant doit être VISIBLE dans l'OpenAPI, sinon un
    client généré ne sait pas qu'il existe (`errors=` → contrat du front)."""
    from oto_mcp.capabilities.registry import CAPABILITIES

    par_cle = {c.key: c for c in CAPABILITIES}
    for cle in ("org.archive", "org.admin.archive"):
        codes = {(d.status, d.code) for d in par_cle[cle].errors}
        assert (409, CODE) in codes, f"{cle} ne déclare pas le 409 {CODE}"


# ── la course : le contrôle et la pose d'`archived_at` sont atomiques ─────────

def _attend_bloque(fn) -> tuple[threading.Thread, dict]:
    """Lance `fn` dans un thread ; rend le thread et le dict de résultat."""
    res: dict = {}

    def cible():
        try:
            res["ok"] = fn()
        except BaseException as e:               # noqa: BLE001 — on ASSERTE dessus
            res["exc"] = e

    t = threading.Thread(target=cible, daemon=True)
    t.start()
    return t, res


def test_un_abonnement_en_cours_de_creation_bloque_l_archivage(base):
    """Une souscription ouverte mais pas encore validée (transaction A) ne doit pas
    échapper au contrôle : l'archivage ATTEND qu'elle se termine, puis la voit et refuse.

    Sans verrou sur la ligne `orgs`, l'archivage lirait « pas d'abonnement » dans son
    instantané, et les deux transactions passeraient : une org archivée portant un
    abonnement actif."""
    org = _org("banc-400-course-archivage")
    with psycopg.connect(base) as a:             # transaction A : ce que fait l'upsert
        a.execute("SELECT 1 FROM orgs WHERE id = %s FOR SHARE", (org,))
        a.execute("INSERT INTO org_subscriptions (org_id, provider, method, plan, status) "
                  "VALUES (%s, 'mollie', 'card', 'standard', 'active')", (org,))
        t, res = _attend_bloque(lambda: org_store.archive_org(org))
        t.join(1.0)
        assert t.is_alive(), (
            "l'archivage a fini alors qu'un abonnement non validé existe : il n'a pas "
            "pris de verrou sur `orgs`, la course est ouverte")
        a.commit()
    t.join(10.0)
    assert not t.is_alive()
    assert isinstance(res.get("exc"), org_store.OrgAvecAbonnementActif), res
    assert not _archivee(org)


def test_l_archivage_en_cours_bloque_la_souscription(base):
    """Le pendant : pendant qu'un archivage tient la ligne `orgs`, la souscription
    ATTEND — et l'ordre qui en sort est l'un des deux ordres sûrs (archivage d'abord :
    l'org porte un abonnement, que le filtre d'échéance ne tirera pas).

    ⚠️ Le verrou de B est `NO KEY UPDATE`, celui que `archive_org` prend réellement, et
    PAS `FOR UPDATE` : ce dernier bloquerait aussi la vérification de clé étrangère de
    l'`INSERT`, donc le test serait vert même sans le verrou partagé de la souscription —
    un vert obtenu pour la mauvaise raison."""
    org = _org("banc-400-course-souscription")
    with psycopg.connect(base) as b:             # transaction B : l'archivage en vol
        b.execute("SELECT 1 FROM orgs WHERE id = %s FOR NO KEY UPDATE", (org,))
        t, res = _attend_bloque(lambda: _abonner(org, status="active"))
        t.join(1.0)
        assert t.is_alive(), (
            "la souscription a fini pendant qu'un archivage tient l'org : "
            "`upsert_org_subscription` ne prend pas son verrou partagé")
        b.commit()
    t.join(10.0)
    assert not t.is_alive() and "exc" not in res, res


def test_le_verrou_ne_fige_pas_une_souscription_ordinaire(base):
    """Contrôle : sans archivage en vol, la souscription passe sans attendre."""
    org = _org("banc-400-souscription-libre")
    debut = time.monotonic()
    _abonner(org, status="active")
    assert time.monotonic() - debut < 2.0
