"""L'arrêt de l'émission automatique (#488, décision du 2026-09-09).

Ce que ce fichier doit prouver tient en une phrase : **un encaissement ne crée
plus rien chez Pennylane, et il n'est pas perdu pour autant.**

Pourquoi une base RÉELLE plutôt qu'un store simulé : trois des garanties ne vivent
que dans le schéma, et un stub dirait oui à n'importe quoi.

1. **L'idempotence est une CONTRAINTE**, `UNIQUE (payment_row_id, kind)`. C'est elle
   qui empêche un webhook rejoué de produire deux lignes — pas une lecture
   préalable, que deux appels simultanés franchiraient tous les deux.
2. **La file de reprise est un prédicat SQL** : `status = 'pending'`. C'est ce
   filtre-là, et rien d'autre, qui fait qu'une ligne `held` cesse d'être revisitée
   toutes les heures.
3. **La règle (c)** — les deux encaissements du 25/08/2026, sans décomposition
   fiscale, ne sont jamais tracés — est un prédicat SQL dans la même file.

⚠️ **Pourquoi l'espion, et pas une assertion sur nos fonctions.** Un banc qui
vérifierait « `_emettre` n'est pas appelée » ne prouverait rien le jour où l'appel
revient par un autre chemin. Et la garde socket du conftest ne suffit pas non plus :
elle lève, et tous les chemins de facturation absorbent (une exception ne doit
jamais atteindre le payeur, #493) — une tentative réelle y deviendrait un journal,
pas un rouge. L'espion se pose donc sur les entonnoirs de sortie du process et
GARDE LA TRACE de la tentative. Il est contrôlé avant chaque mesure : un appel réel
au client Pennylane d'oto-core et un e-mail réel doivent y apparaître, sans quoi une
liste vide ne distinguerait pas « rien n'est parti » de « l'espion regarde à côté ».
"""
from __future__ import annotations

import contextlib

import pytest

from _facturation import (SortieReseau, _abonnement, _document_emis,  # noqa: F401
                          _identite, _org, _paiement, espionner_le_reseau)

# La clé de la compta d'Otomata. Elle vivait dans le seam supprimé ; le banc la
# nomme en toutes lettres parce qu'elle est POSÉE exprès : sans elle, un zéro
# d'appel ne prouverait que l'absence de clé.
CLE_PLATEFORME = "OTO_PENNYLANE_API_KEY"


def _controler_lespion(espion, monkeypatch) -> None:
    """L'instrument voit-il RÉELLEMENT les deux sorties qu'il surveille ?

    Le geste joué est celui qu'on a supprimé — `create_customer_invoice`, la
    création de la pièce qui a fait le doublon F-2026-09-7 — sur le vrai client
    d'oto-core, plus un envoi réel par le relais transactionnel."""
    from oto.tools.common.field_filter import FieldFilter
    from oto.tools.pennylane import PennylaneClient
    from oto_mcp import email

    with contextlib.suppress(Exception):
        PennylaneClient(api_key="cle-de-suite", rate_limit_delay=0,
                        field_filter=FieldFilter()).create_customer_invoice(
            customer_id=1, date="2026-09-09", deadline="2026-09-09",
            lines=[{"label": "témoin", "quantity": 1, "unit": "piece",
                    "raw_currency_unit_price": "19.00", "vat_rate": "FR_200"}])
    assert espion.vers("pennylane.com"), (
        "l'espion doit voir passer une création de facture RÉELLE, sinon il ne "
        "prouve rien")

    monkeypatch.setenv("OTO_MAILER_SEND_BEARER", "bearer-de-suite")
    with contextlib.suppress(Exception):
        email._send("temoin@exemple.test", "témoin", "<p>témoin</p>")
    assert espion.vers(email._mailer_url()), (
        "l'espion doit voir passer un e-mail RÉEL, sinon un zéro d'e-mail ne "
        "prouverait rien non plus")
    espion.appels.clear()


# ── la preuve ────────────────────────────────────────────────────────────────

def test_aucune_piece_nest_creee_chez_pennylane_a_lencaissement(live, monkeypatch):
    """La mesure : le cycle complet d'un encaissement, ZÉRO sortie réseau.

    Les quatre portes sont jouées — les deux fonctions du module, celle qu'appelle
    le cycle de paiement (`tracer_encaissement`), et le balayage du runner. Aucune
    ne doit produire une seule tentative, ni vers Pennylane ni vers le relais
    d'e-mail."""
    from oto_mcp import billing_invoices
    from oto_mcp.db import billing_invoices as db_invoices

    monkeypatch.setenv(CLE_PLATEFORME, "cle-de-suite")   # la clé EST posée
    espion = espionner_le_reseau(monkeypatch)
    _controler_lespion(espion, monkeypatch)

    org = _org()
    _identite(org)                # identité de facturation complète : rien ne manque
    _abonnement(org)
    paiement = _paiement(org)

    facture = billing_invoices.ensure_invoice_for_payment(paiement)
    avoir = billing_invoices.ensure_credit_note_for_refund(paiement, 2280)
    billing_invoices.tracer_encaissement(_paiement(org, kind="renewal")["id"])
    billing_invoices.sweep()

    assert espion.appels == [], (
        "aucune pièce n'est créée chez Pennylane, et aucun e-mail ne part — "
        f"tentatives vues : {espion.appels}")
    assert facture["status"] == "held" and avoir["status"] == "held"
    assert all(l["pennylane_invoice_id"] is None and l["number"] is None
               for l in db_invoices.list_billing_invoices(org))


def test_le_paquet_nexpose_plus_le_seam_pennylane():
    """Le chemin a été RETIRÉ, pas neutralisé : ni client fournisseur, ni émission.

    Un appel qui reste dans le code, seulement plus appelé, revient au premier
    nettoyage qui « répare » un appel manquant."""
    import importlib

    from oto_mcp import billing_invoices

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("oto_mcp.billing_invoices.pennylane")
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("oto_mcp.billing_invoices.mail")
    for parti in ("facturer_encaissement", "avoir_remboursement", "is_configured",
                  "PennylaneUnavailable", "InvoiceRefused", "billing_contact"):
        assert not hasattr(billing_invoices, parti), parti


# ── l'état d'une facture non émise ───────────────────────────────────────────

def test_un_encaissement_est_tenu_et_dit_pourquoi(live, monkeypatch):
    """`held` : tracé, visible, silencieux — et jamais confondu avec une panne."""
    from oto_mcp import billing_invoices
    from oto_mcp.db import billing_invoices as db_invoices

    espionner_le_reseau(monkeypatch)
    org = _org()
    _identite(org)
    _abonnement(org)
    paiement = _paiement(org)

    inv = billing_invoices.ensure_invoice_for_payment(paiement)

    assert inv["status"] == "held"
    assert inv["error_code"] == billing_invoices.EN_ATTENTE_DE_LA_MAIN
    assert "2026-09-09" in inv["error_detail"], (
        "la cause DATE la décision — un code nu ferait chercher un incident")
    assert inv["attempts"] == 0, (
        "aucun appel n'a été tenté : compter une tentative ferait lire un "
        "fournisseur en panne là où il y a une décision")
    assert inv["number"] is None and inv["has_pdf"] is False
    # L'encaissement, lui, est intact : c'est la ligne de journal qui porte l'argent.
    assert db_invoices.billing_payment_row(paiement["id"])["status"] == "paid"
    # Et la ligne est SERVIE au client, dans le tableau des factures.
    assert [r["id"] for r in db_invoices.list_billing_invoices(org)] == [inv["id"]]


def test_une_ligne_tenue_quitte_la_file_de_reprise_et_se_tait(live, monkeypatch):
    """La différence entre une COUPURE et une PANNE.

    Retirer la clé plateforme aurait laissé une ligne `pending` par paiement, donc
    une erreur par ligne à chaque tick horaire, sur une file qui ne se vide jamais.
    Une ligne tenue sort du prédicat de la file — et le balayage ne la touche plus,
    pas même son `updated_at`."""
    from oto_mcp import billing_invoices
    from oto_mcp.db import billing_invoices as db_invoices

    espionner_le_reseau(monkeypatch)
    org = _org()
    _identite(org)
    _abonnement(org)
    inv = billing_invoices.ensure_invoice_for_payment(_paiement(org))

    assert inv["id"] not in {r["id"] for r in db_invoices.pending_billing_invoices(100)}

    counts = billing_invoices.sweep()
    apres = db_invoices.get_billing_invoice(inv["id"])
    assert apres["updated_at"] == inv["updated_at"], (
        "un tick qui retoucherait la ligne ferait avancer sa date d'une heure en "
        "une heure sans qu'il se soit rien passé")
    assert not counts.get("invoice_waiting"), "rien à revisiter"


def test_le_balayage_tient_les_lignes_restees_en_attente(live, monkeypatch):
    """Le chemin de PASSAGE : les lignes `pending` d'AVANT la coupure — de vraies
    tentatives échouées — sont tenues à leur tour, et la file converge vers le
    vide en un tick. C'est ce qui fait taire le journal horaire."""
    from oto_mcp import billing_invoices
    from oto_mcp.db import billing_invoices as db_invoices
    from oto_mcp.db._conn import _connect

    espionner_le_reseau(monkeypatch)
    org = _org()
    _identite(org)
    _abonnement(org)
    inv = billing_invoices.ensure_invoice_for_payment(_paiement(org))
    with _connect() as conn:     # on rejoue l'état d'avant le 2026-09-09
        conn.execute("UPDATE billing_invoices SET status='pending', "
                     "error_code='pennylane_error', attempts=3 WHERE id=%s",
                     (inv["id"],))

    counts = billing_invoices.sweep()

    apres = db_invoices.get_billing_invoice(inv["id"])
    assert counts.get("invoice_waiting") == 1
    assert apres["status"] == "held"
    assert apres["error_code"] == billing_invoices.EN_ATTENTE_DE_LA_MAIN
    assert apres["attempts"] == 3, "les tentatives PASSÉES ne s'effacent pas"
    assert db_invoices.pending_billing_invoices(100) == []


def test_un_document_deja_emis_nest_jamais_retouche(live, monkeypatch):
    """Les factures d'avant la coupure — et celles qu'une main posera — sont
    intouchables : `held` ne dé-facture pas."""
    from oto_mcp import billing_invoices
    from oto_mcp.db import billing_invoices as db_invoices

    espionner_le_reseau(monkeypatch)
    org = _org()
    _identite(org)
    _abonnement(org)
    paiement = _paiement(org)
    emis = _document_emis(org, paiement=paiement)

    billing_invoices.ensure_invoice_for_payment(paiement)
    billing_invoices.sweep()

    apres = db_invoices.get_billing_invoice(emis["id"])
    assert apres["status"] == "issued" and apres["number"] == "F-2026-09-7"
    assert apres["has_pdf"] is True and apres["error_code"] is None


# ── ce qui ne bouge pas ──────────────────────────────────────────────────────

def test_un_webhook_rejoue_ne_cree_quune_ligne(live, monkeypatch):
    from oto_mcp import billing_invoices
    from oto_mcp.db import billing_invoices as db_invoices

    espionner_le_reseau(monkeypatch)
    org = _org()
    _identite(org)
    _abonnement(org)
    paiement = _paiement(org)

    premier = billing_invoices.ensure_invoice_for_payment(paiement)
    second = billing_invoices.ensure_invoice_for_payment(paiement)

    assert premier["id"] == second["id"] and second["status"] == "held"
    assert len(db_invoices.list_billing_invoices(org)) == 1


def test_les_deux_encaissements_davant_la_regle_ne_sont_pas_traces(live, monkeypatch):
    """Règle (c) de #488 : `amount_ht IS NULL` = ligne d'avant la TVA. Sans
    décomposition, aucune facture conforme n'est calculable — et une ligne
    d'attente que rien ne résoudra jamais sonnerait pour toujours."""
    from oto_mcp import billing_invoices
    from oto_mcp.db import billing_invoices as db_invoices

    espionner_le_reseau(monkeypatch)
    org = _org("ORG DU 25/08")
    _identite(org)
    _abonnement(org)
    paiement = _paiement(org, sans_tva=True)

    assert billing_invoices.ensure_invoice_for_payment(paiement) is None
    assert billing_invoices.ensure_credit_note_for_refund(paiement, 1900) is None
    assert db_invoices.list_billing_invoices(org) == []
    assert paiement["id"] not in {p["id"] for p
                                  in db_invoices.paid_payments_without_invoice(100)}


def test_un_remboursement_garde_son_montant(live, monkeypatch):
    """Le webhook qui a vu le remboursement ne repassera pas : son montant est
    écrit à la création de la ligne, sinon l'avoir posé à la main l'aurait perdu."""
    from oto_mcp import billing_invoices

    espionner_le_reseau(monkeypatch)
    org = _org()
    _identite(org)
    _abonnement(org)
    paiement = _paiement(org)
    billing_invoices.ensure_invoice_for_payment(paiement)

    avoir = billing_invoices.ensure_credit_note_for_refund(paiement, 1140)

    assert avoir["kind"] == "credit_note" and avoir["status"] == "held"
    assert avoir["amount_ttc"] == -1140, "le montant remboursé, en négatif"
    assert avoir["error_code"] == billing_invoices.EN_ATTENTE_DE_LA_MAIN


def test_un_paiement_non_encaisse_na_pas_de_ligne(live, monkeypatch):
    from oto_mcp import billing_invoices
    from oto_mcp.db import billing_invoices as db_invoices

    espionner_le_reseau(monkeypatch)
    org = _org()
    _identite(org)
    paiement = _paiement(org, statut="open")

    assert billing_invoices.ensure_invoice_for_payment(paiement) is None
    assert db_invoices.list_billing_invoices(org) == []
