"""Le paiement encaissé doit être CELUI qu'on confirme (#291).

Le scénario qui perdait de l'argent : rien n'interdit deux souscriptions ouvertes à
la fois (retour arrière, page rechargée, hésitation carte/SEPA), donc deux pages
payables. Le payeur termine l'ANCIENNE ; Mollie encaisse ; le webhook retrouve la
bonne ligne ; puis `confirm` repartait « du plus récent », la trouvait non payée et
rendait `pending`. Résultat : org débitée, aucun droit ouvert, et un journal qui
annonçait `confirmed`.

La facturation est en production : ces tests exercent la logique réelle avec le PSP
et la base stubbés, jamais le seam qu'ils vérifient.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from oto_mcp import billing


TERMINAL = ("failed", "canceled", "expired", "paid_confirmed")


class _Db:
    """Journal de paiements en mémoire, trié DESC comme la vraie requête."""

    def __init__(self, payments, sub=None):
        self.payments = payments
        self.sub = sub
        self.upserted = []
        self.updated = []

    def list_billing_payments(self, org_id, limit=20):
        return self.payments[:limit]

    def get_org_subscription(self, org_id):
        return self.sub

    def update_billing_payment(self, pid, **kw):
        self.updated.append((pid, kw))

    def upsert_org_subscription(self, org_id, **kw):
        self.upserted.append((org_id, kw))

    def get_billing_payment_by_ref(self, ref):
        return next((p for p in self.payments if p["payment_intent_id"] == ref), None)

    TERMINAL_PAYMENT_STATUSES = TERMINAL


def _payment(pid, ref, status="open", age=timedelta(seconds=2)):
    return {"id": pid, "payment_intent_id": ref, "kind": "initial",
            "status": status, "org_id": 7,
            "created_at": datetime.now(timezone.utc) - age}


@pytest.fixture
def deux_checkouts(monkeypatch):
    """Deux paiements ouverts ; SEUL l'ancien a été encaissé chez le PSP."""
    db = _Db([_payment(2, "tr_RECENT"), _payment(1, "tr_ANCIEN")])
    monkeypatch.setattr(billing, "db_billing", db)

    def get_payment(ref):
        if ref == "tr_ANCIEN":
            return {"id": ref, "status": "paid", "customerId": "cst_1",
                    "metadata": {"plan": "standard"}}
        return {"id": ref, "status": "open"}

    monkeypatch.setattr(billing.mollie_client, "get_payment", get_payment)
    monkeypatch.setattr(billing.mollie_client, "valid_mandate",
                        lambda cid: {"id": "mdt_1", "mandateReference": "RUM1"})
    monkeypatch.setattr(billing, "apply_plan_entitlements", lambda *a, **k: None)
    return db


def test_sans_reference_on_confirme_le_mauvais(deux_checkouts):
    """Le comportement d'origine, conservé pour le POLLING qui ne sait pas lequel a
    été payé : il regarde le plus récent, non payé, et rend `pending`. Ce test fige
    la raison d'être du paramètre — sans lui, l'encaissement reste invisible."""
    out = billing.confirm(7)
    assert out["status"] == "pending"
    assert deux_checkouts.upserted == [], "aucun abonnement ne doit être posé"


def test_avec_la_reference_du_paiement_encaisse_l_abonnement_s_ouvre(deux_checkouts):
    """Le cœur du correctif : le webhook sait lequel a été payé, il le dit."""
    out = billing.confirm(7, payment_ref="tr_ANCIEN")
    assert out["status"] == "active"
    assert out["plan"] == "standard"
    assert len(deux_checkouts.upserted) == 1
    org_id, kw = deux_checkouts.upserted[0]
    assert org_id == 7 and kw["status"] == "active" and kw["plan"] == "standard"


def test_le_webhook_transmet_la_reference(monkeypatch, deux_checkouts):
    """Bout en bout : c'est la chaîne complète qui était rompue, pas `confirm` seul."""
    assert billing.process_webhook("tr_ANCIEN") == "confirmed"
    assert len(deux_checkouts.upserted) == 1


def test_une_reference_inconnue_ne_se_rabat_sur_personne(deux_checkouts):
    """Se rabattre sur un autre paiement reviendrait à ouvrir des droits sur la foi
    d'un encaissement qui concerne autre chose."""
    with pytest.raises(ValueError) as e:
        billing.confirm(7, payment_ref="tr_INCONNU")
    assert "unknown_payment" in str(e.value)
    assert deux_checkouts.upserted == []


def test_le_webhook_n_annonce_pas_un_succes_qu_il_n_a_pas_constate(monkeypatch):
    """`confirmed` était rendu quoi qu'il arrive : le journal affirmait le contraire
    de ce qui s'était passé, ce qui envoie chercher l'incident ailleurs.

    Le paiement est daté HORS de la fenêtre de mandat : dedans, un mandat absent est
    une course normale (`awaiting_mandate`), pas un incident — cf. le test suivant."""
    vieux = _payment(1, "tr_X")
    vieux["created_at"] = (datetime.now(timezone.utc)
                           - billing.PENDING_WINDOW - timedelta(minutes=1))
    db = _Db([vieux])
    monkeypatch.setattr(billing, "db_billing", db)
    monkeypatch.setattr(billing.mollie_client, "get_payment",
                        lambda ref: {"id": ref, "status": "paid", "customerId": "cst_1",
                                     "metadata": {"plan": "standard"}})
    # Encaissé mais AUCUN mandat réutilisable → refus définitif, pas d'abonnement.
    monkeypatch.setattr(billing.mollie_client, "valid_mandate", lambda cid: None)

    assert billing.process_webhook("tr_X") == "not_confirmed"
    assert db.upserted == []


def test_le_webhook_ne_crie_pas_sur_une_course_de_mandat(monkeypatch):
    """#493 : le webhook arrive une seconde après l'encaissement, le mandat n'existe
    pas encore. Le compter comme un incident envoie chercher un défaut là où il n'y
    en a pas — et l'encaissement, lui, doit être journalisé tout de suite."""
    db = _Db([_payment(1, "tr_X")])
    monkeypatch.setattr(billing, "db_billing", db)
    monkeypatch.setattr(billing.mollie_client, "get_payment",
                        lambda ref: {"id": ref, "status": "paid", "customerId": "cst_1",
                                     "metadata": {"plan": "standard"}})
    monkeypatch.setattr(billing.mollie_client, "valid_mandate", lambda cid: None)

    assert billing.process_webhook("tr_X") == "awaiting_mandate"
    assert db.upserted == []
    assert db.updated == [(1, {"status": "paid", "payment_id": "tr_X"})]


def test_un_initial_ouvert_ancien_reste_visible(monkeypatch):
    """Le défaut aggravant : au `limit` par défaut (20), un paiement ouvert plus
    ancien sortait de la fenêtre et n'était jamais confirmé, sans aucun message."""
    vieux = _payment(1, "tr_VIEUX")
    db = _Db([_payment(i, f"tr_{i}", status="expired") for i in range(50, 1, -1)] + [vieux])
    monkeypatch.setattr(billing, "db_billing", db)
    monkeypatch.setattr(billing.mollie_client, "get_payment",
                        lambda ref: {"id": ref, "status": "paid", "customerId": "c",
                                     "metadata": {"plan": "standard"}})
    monkeypatch.setattr(billing.mollie_client, "valid_mandate",
                        lambda cid: {"id": "m", "mandateReference": "R"})
    monkeypatch.setattr(billing, "apply_plan_entitlements", lambda *a, **k: None)

    out = billing.confirm(7, payment_ref="tr_VIEUX")
    assert out["status"] == "active"


def test_la_date_sort_au_meme_format_que_status(deux_checkouts):
    """Le même champ rendait deux formes selon le verbe : un client qui parse
    `confirm` cassait sur `status`."""
    out = billing.confirm(7, payment_ref="tr_ANCIEN")
    end = out["current_period_end"]
    assert isinstance(end, str)
    assert "T" not in end and "+" not in end, f"format ISO à offset revenu : {end!r}"
    assert len(end) == 19, f"attendu 'YYYY-MM-DD HH:MM:SS', reçu {end!r}"
