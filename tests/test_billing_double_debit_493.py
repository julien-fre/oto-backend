"""Le premier client payant a été débité DEUX FOIS (#493) — rejeu de l'incident.

25/08/2026, org 219, premier encaissement réel de la plateforme : deux paiements
`initial` de 19 € `paid` à deux minutes d'écart. L'enchaînement n'a rien d'exotique
— payer, voir un échec, recliquer :

| heure    | ce qui se passe                                                    |
| -------- | ------------------------------------------------------------------ |
| 10:29:44 | l'org ouvre un checkout                                             |
| 10:31:0x | elle paie ; Mollie encaisse                                         |
| 10:31:05 | retour navigateur, **1,4 s** après : le mandat n'existe pas encore  |
| —        | `confirm` rendait 409 `no_mandate` et n'écrivait même pas `paid`    |
| 10:31:44 | le payeur, qui a vu un échec, reclique → **second checkout payable** |
| 10:36    | le mandat apparaît ; le 2ᵉ paiement est encaissé lui aussi          |

Trois défauts composés : un refus définitif servi sur une course de quelques
minutes, un encaissement non journalisé, et une souscription qui ne se gardait que
sur un abonnement ACTIF. Ce fichier rejoue la chronologie et fige le verdict : un
paiement, un customer, un abonnement, et jamais un refus au payeur d'un paiement
réussi.

Mollie et le store sont simulés ici — aucun appel réel, comme dans tout le reste de
la famille `test_billing_*`.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from oto_mcp import billing, billing_runner
from oto_mcp.db import billing as db_billing

ORG = 219
RETURN_URL = "https://dashboard.oto.cx/org/billing?billing=return"
PRIX = billing.PLANS["standard"]["amount"]


# ── l'horloge du scénario ────────────────────────────────────────────────────

class _Timeline:
    """Horloge de scénario, sans toucher au `datetime.now()` du code de production.

    Les lignes de journal sont datées en instants de SCÉNARIO ; à la lecture, leur
    `created_at` est recalculé par rapport à l'heure réelle pour que l'ÂGE vu par le
    code soit exactement celui du scénario. On teste donc le vrai calcul de fenêtre,
    pas une horloge de laboratoire."""

    def __init__(self):
        self.t = timedelta(0)

    def advance(self, **kw) -> None:
        self.t += timedelta(**kw)

    def stamp(self, at: timedelta) -> datetime:
        """Horodatage réel équivalent à l'instant de scénario `at`."""
        return datetime.now(timezone.utc) - (self.t - at)


# ── le PSP simulé ────────────────────────────────────────────────────────────

class _Mollie:
    """Ce que Mollie fait vraiment : un customer par création, une page payable par
    checkout, et un mandat réutilisable qui n'apparaît QUE plusieurs minutes après
    l'encaissement."""

    def __init__(self, clock: _Timeline):
        self.clock = clock
        self.customers: list[str] = []
        self.payments: dict[str, dict] = {}
        self.mandate_visible_at: timedelta | None = None

    # — surface consommée par oto —
    def create_customer(self, **kw) -> dict:
        cid = f"cst_{len(self.customers) + 1}"
        self.customers.append(cid)
        return {"id": cid}

    def create_first_payment(self, amount, *, customer_id, redirect_url,
                             currency="eur", description=None, method=None,
                             metadata=None, webhook_url=None) -> dict:
        pid = f"tr_{len(self.payments) + 1}"
        self.payments[pid] = {
            "id": pid, "status": "open", "customerId": customer_id,
            "amount": amount, "metadata": metadata or {}, "method": method,
            "redirectUrl": redirect_url,
            "_links": {"checkout": {"href": f"https://www.mollie.com/checkout/{pid}"}},
        }
        return dict(self.payments[pid])

    def update_payment(self, payment_id, *, redirect_url=None, **kw) -> dict:
        if redirect_url is not None:
            self.payments[payment_id]["redirectUrl"] = redirect_url
        return dict(self.payments[payment_id])

    def get_payment(self, payment_id) -> dict:
        p = dict(self.payments[payment_id])
        # `paidAt` est recalculé à la LECTURE : c'est lui que le code lit pour savoir
        # depuis combien de temps l'argent est pris, il doit donc suivre l'horloge du
        # scénario et non l'instant réel où le test a appelé `pay()`.
        if p.pop("paid_at_t", None) is not None:
            p["paidAt"] = self.clock.stamp(
                self.payments[payment_id]["paid_at_t"]).isoformat()
        return p

    def valid_mandate(self, customer_id):
        if self.mandate_visible_at is None or self.clock.t < self.mandate_visible_at:
            return None
        return {"id": f"mdt_{customer_id}", "mandateReference": "RUM-493"}

    # — les gestes du payeur / du PSP, pilotés par le test —
    def pay(self, payment_id: str) -> None:
        """Le payeur conclut la page hébergée : Mollie encaisse."""
        p = self.payments[payment_id]
        p["status"] = "paid"
        p["method"] = "creditcard"
        p["paid_at_t"] = self.clock.t

    def mandate_in(self, **kw) -> None:
        self.mandate_visible_at = self.clock.t + timedelta(**kw)

    @property
    def encaisses(self) -> list[str]:
        return [p["id"] for p in self.payments.values() if p["status"] == "paid"]

    @property
    def total_debite(self) -> int:
        return sum(p["amount"] for p in self.payments.values() if p["status"] == "paid")


# ── le journal simulé ────────────────────────────────────────────────────────

class _Store:
    """`billing_payments` + `org_subscriptions` en mémoire, avec les MÊMES règles de
    tri et de filtrage que le SQL (plus récent d'abord, statuts terminaux figés)."""

    TERMINAL_PAYMENT_STATUSES = db_billing.TERMINAL_PAYMENT_STATUSES

    def __init__(self, clock: _Timeline):
        self.clock = clock
        self.rows: list[dict] = []
        self.sub: dict | None = None
        self.upserts = 0

    def _out(self, row: dict) -> dict:
        return {**row, "created_at": self.clock.stamp(row["at"])}

    def _desc(self) -> list[dict]:
        return sorted(self.rows, key=lambda r: r["id"], reverse=True)

    # — billing_payments —
    def insert_billing_payment(self, org_id, kind, amount, *, currency="eur",
                               payment_intent_id=None, payment_id=None,
                               status="processing", attempt=1, customer_id=None):
        row = {"id": len(self.rows) + 1, "org_id": org_id, "kind": kind,
               "amount": amount, "currency": currency, "status": status,
               "payment_intent_id": payment_intent_id, "payment_id": payment_id,
               "attempt": attempt, "customer_id": customer_id, "at": self.clock.t}
        self.rows.append(row)
        return row["id"]

    def update_billing_payment(self, row_id, *, status, payment_id=None):
        for r in self.rows:
            if r["id"] == row_id:
                r["status"] = status
                if payment_id:
                    r["payment_id"] = payment_id
                return True
        return False

    def list_billing_payments(self, org_id, limit=20):
        return [self._out(r) for r in self._desc() if r["org_id"] == org_id][:limit]

    def get_billing_payment_by_ref(self, ref):
        for r in self._desc():
            if ref in (r.get("payment_intent_id"), r.get("payment_id")):
                return self._out(r)
        return None

    def pending_initial_payment(self, org_id, *, since):
        vivants = self.TERMINAL_PAYMENT_STATUSES - {"paid"}
        for r in self._desc():
            if (r["org_id"] == org_id and r["kind"] == "initial"
                    and r["status"] not in vivants):
                out = self._out(r)
                if out["created_at"] > since:
                    return out
        return None

    def paid_initials_awaiting_subscription(self, limit=50, *, since):
        posee = self.sub is not None and self.sub.get("status") == "active"
        return [] if posee else [
            self._out(r) for r in sorted(self.rows, key=lambda r: r["id"])
            if r["kind"] == "initial" and r["status"] == "paid"
            and self._out(r)["created_at"] > since
        ][:limit]

    def last_customer_id_for_org(self, org_id):
        for r in self._desc():
            if r["org_id"] == org_id and r.get("customer_id"):
                return r["customer_id"]
        return None

    # — org_subscriptions —
    def get_org_subscription(self, org_id):
        return self.sub

    def upsert_org_subscription(self, org_id, **kw):
        self.sub = {"org_id": org_id, **kw}
        self.upserts += 1


@pytest.fixture
def scene(monkeypatch):
    """L'org 219 devant un PSP et un journal neufs."""
    clock = _Timeline()
    store, mollie = _Store(clock), _Mollie(clock)
    monkeypatch.setattr(billing, "db_billing", store)
    monkeypatch.setattr(billing_runner, "db_billing", store)
    for nom in ("create_customer", "create_first_payment", "update_payment",
                "get_payment", "valid_mandate"):
        monkeypatch.setattr(billing.mollie_client, nom, getattr(mollie, nom))
    monkeypatch.setattr(billing, "apply_plan_entitlements", lambda *a, **k: None)
    return clock, store, mollie


# ── le rejeu ─────────────────────────────────────────────────────────────────

def test_la_chronologie_du_25_aout_ne_debite_plus_qu_une_fois(scene):
    clock, store, mollie = scene

    # 10:29:44 — l'org ouvre un checkout.
    depart = billing.subscribe(ORG, "standard", RETURN_URL)
    tr1 = depart["payment_intent_id"]

    # 10:31:0x — elle paie. Le mandat, lui, mettra cinq minutes à naître.
    clock.advance(minutes=1, seconds=20)
    mollie.pay(tr1)
    mollie.mandate_in(minutes=5)

    # 10:31:05 — retour navigateur, 1,4 s après l'encaissement.
    clock.advance(milliseconds=1400)
    retour = billing.confirm(ORG, payment_ref=tr1)
    assert retour["status"] == "pending_mandate", "un paiement réussi ne se refuse pas"
    assert retour["payment_status"] == "paid" and retour["retry_after"] > 0
    # …et l'encaissement est DÉJÀ au journal : c'est ce qui manquait pour que la
    # souscription suivante puisse s'en garder.
    assert store.list_billing_payments(ORG)[0]["status"] == "paid"

    # 10:31:44 — le payeur reclique (il a vu « en attente », pas « payé »).
    clock.advance(seconds=39)
    with pytest.raises(ValueError) as refus:
        billing.subscribe(ORG, "standard", RETURN_URL)
    assert "payment_pending" in str(refus.value)
    assert tr1 in str(refus.value), "le refus doit nommer le paiement qui occupe la place"

    # 10:36 — le mandat apparaît ; la re-sonde du navigateur ouvre l'abonnement.
    clock.advance(minutes=5)
    fin = billing.confirm(ORG, payment_ref=tr1)
    assert fin["status"] == "active" and fin["plan"] == "standard"

    # Le verdict de l'incident.
    assert mollie.encaisses == [tr1], "un seul paiement encaissé"
    assert mollie.total_debite == PRIX, f"{PRIX} c débités, pas {2 * PRIX}"
    assert mollie.customers == ["cst_1"], "un seul customer Mollie pour l'org"
    assert store.sub["status"] == "active" and store.sub["customer_id"] == "cst_1"
    assert store.sub["mandate_id"] == "mdt_cst_1"


def test_le_second_clic_ne_cree_pas_un_second_customer(scene):
    """Le customer se relit sur le JOURNAL tant que le miroir n'est pas posé (#493) :
    c'est ce qui manquait pour que le 2ᵉ checkout du 25/08 n'ouvre pas un customer de
    plus, avec son propre mandat — celui que le rejeu MIT ne tirerait jamais."""
    clock, store, mollie = scene
    billing.subscribe(ORG, "standard", RETURN_URL)

    # La fenêtre s'écoule sans que rien n'aboutisse : le checkout expire.
    clock.advance(minutes=31)
    store.rows[0]["status"] = "expired"

    billing.subscribe(ORG, "standard", RETURN_URL)
    assert mollie.customers == ["cst_1"]
    assert [p["customerId"] for p in mollie.payments.values()] == ["cst_1", "cst_1"]


def test_un_checkout_expire_ne_bloque_plus_la_souscription(scene):
    """La garde vise une souscription EN VOL, pas un cimetière : un échec définitif
    (expired/failed/canceled) laisse immédiatement repartir."""
    clock, store, mollie = scene
    billing.subscribe(ORG, "standard", RETURN_URL)
    store.rows[0]["status"] = "failed"
    clock.advance(seconds=10)

    billing.subscribe(ORG, "standard", RETURN_URL)      # aucune levée
    assert len(mollie.payments) == 2


def test_le_retour_navigateur_porte_l_identite_du_paiement(scene):
    """`confirm` ne devine plus : l'URL de retour dit lequel vient d'être conclu."""
    _, _, mollie = scene
    out = billing.subscribe(ORG, "standard", RETURN_URL)
    attendu = f"{RETURN_URL}&payment_ref={out['payment_intent_id']}"
    assert mollie.payments[out["payment_intent_id"]]["redirectUrl"] == attendu


# ── les rejeux : webhook doublé, confirm relancé ─────────────────────────────

def test_deux_webhooks_pour_le_meme_paiement_n_ouvrent_qu_un_abonnement(scene):
    """Mollie rappelle plusieurs fois. Le second passage ne doit ni re-poser le
    miroir, ni repousser la fin de période d'un mois de plus."""
    clock, store, mollie = scene
    tr1 = billing.subscribe(ORG, "standard", RETURN_URL)["payment_intent_id"]
    clock.advance(minutes=1)
    mollie.pay(tr1)
    mollie.mandate_in(seconds=0)          # mandat immédiat : cas nominal

    assert billing.process_webhook(tr1) == "confirmed"
    fin_de_periode = store.sub["current_period_end"]

    clock.advance(seconds=30)
    assert billing.process_webhook(tr1) == "confirmed"
    assert store.upserts == 1, "le miroir n'est posé qu'une fois"
    assert store.sub["current_period_end"] == fin_de_periode


def test_confirm_rejoue_ne_prolonge_pas_la_periode(scene):
    """Le navigateur re-sonde tant qu'il est ouvert. Chaque appel doit être un
    no-op informatif une fois l'abonnement posé — pas un mois offert."""
    clock, store, mollie = scene
    tr1 = billing.subscribe(ORG, "standard", RETURN_URL)["payment_intent_id"]
    clock.advance(minutes=1)
    mollie.pay(tr1)
    mollie.mandate_in(seconds=0)

    premier = billing.confirm(ORG, payment_ref=tr1)
    assert premier["status"] == "active"
    fin_de_periode = store.sub["current_period_end"]

    for _ in range(3):
        clock.advance(seconds=15)
        rejeu = billing.confirm(ORG)
        assert rejeu == {"status": "active", "plan": "standard"}
    assert store.upserts == 1
    assert store.sub["current_period_end"] == fin_de_periode


def test_le_webhook_du_premier_paiement_bloque_le_second_clic(scene):
    """Le chemin réel du 25/08 : c'est le WEBHOOK qui a constaté l'encaissement en
    premier. Il doit suffire à fermer la porte au second checkout, même si le
    navigateur du payeur, lui, n'a rien re-sondé."""
    clock, store, mollie = scene
    tr1 = billing.subscribe(ORG, "standard", RETURN_URL)["payment_intent_id"]
    clock.advance(minutes=1)
    mollie.pay(tr1)
    mollie.mandate_in(minutes=5)

    assert billing.process_webhook(tr1) == "awaiting_mandate"
    clock.advance(seconds=40)
    with pytest.raises(ValueError, match="payment_pending"):
        billing.subscribe(ORG, "standard", RETURN_URL)


# ── le mandat qui n'arrive jamais ────────────────────────────────────────────

def test_un_mandat_qui_n_arrive_jamais_finit_par_etre_dit(scene):
    """Passé la fenêtre, l'attente devient un mensonge : plus rien ne viendra. On
    tranche pour le refus `no_mandate` (409), le code historique, dont c'est le seul
    sens vrai — encaissé, récurrence impossible, reprise manuelle. L'encaissement,
    lui, reste gravé au journal : c'est ce qui rend l'incident lisible."""
    clock, store, mollie = scene
    tr1 = billing.subscribe(ORG, "standard", RETURN_URL)["payment_intent_id"]
    clock.advance(minutes=1)
    mollie.pay(tr1)                                    # aucun mandat, jamais

    clock.advance(minutes=10)
    assert billing.confirm(ORG, payment_ref=tr1)["status"] == "pending_mandate"

    clock.advance(minutes=25)                          # au-delà de la fenêtre
    with pytest.raises(RuntimeError, match="no_mandate"):
        billing.confirm(ORG, payment_ref=tr1)
    assert store.sub is None, "aucun abonnement qu'on ne saurait pas renouveler"
    assert store.list_billing_payments(ORG)[0]["status"] == "paid"


def test_l_onglet_ferme_est_rattrape_par_le_runner(scene):
    """L'encaissement quitte la file de réconciliation dès qu'il est journalisé
    `paid`. Sans une seconde file, un payeur qui ferme son onglet pendant la course
    au mandat resterait débité et sans droits (#493)."""
    clock, store, mollie = scene
    tr1 = billing.subscribe(ORG, "standard", RETURN_URL)["payment_intent_id"]
    clock.advance(minutes=1)
    mollie.pay(tr1)
    mollie.mandate_in(minutes=5)
    assert billing.confirm(ORG, payment_ref=tr1)["status"] == "pending_mandate"
    # …puis l'onglet se ferme. Plus personne ne re-sonde côté navigateur.
    assert store.paid_initials_awaiting_subscription(
        since=datetime.now(timezone.utc) - timedelta(hours=48)), \
        "l'encaissement sans abonnement doit rester repérable"

    clock.advance(minutes=6)
    billing_runner._catch_up(ORG, tr1)
    assert store.sub["status"] == "active"
    assert mollie.total_debite == PRIX
