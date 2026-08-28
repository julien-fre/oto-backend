"""La surface de la facture (#488) : la liste, le PDF, la reprise, le webhook.

Trois choses s'y vérifient, et aucune n'est du ressort du module d'émission :

1. **la LISTE ne remonte jamais d'octets** — `pdf` est un `BYTEA`, et le row factory
   ne normalise que les dates : un `SELECT *` ferait une 500 à la sérialisation JSON,
   sur le chemin le moins emprunté de la surface ;
2. **le PDF se sert par une route écrite à la main**, parce qu'un handler de capacité
   rend un `dict` — et son autorisation porte sur l'org QUI PORTE la facture, pas
   sur l'org active : ce lien s'ouvre depuis un e-mail ;
3. **la reprise est la garantie** — c'est le balayage du runner, pas les appels en
   ligne, qui rend vraie la phrase « jamais un paiement sans trace de facture ».
"""
from __future__ import annotations

import asyncio
import json
import uuid

import pytest
from starlette.requests import Request
from starlette.responses import JSONResponse

from _datastore_rest import call, stub_authz
from _faux_pennylane import (_abonnement, _identite, _org,  # noqa: F401
                            _paiement, brancher, live)


# ── la liste ─────────────────────────────────────────────────────────────────

def test_la_liste_rend_le_chemin_du_pdf_et_aucun_octet(live, monkeypatch):
    from oto_mcp import billing_invoices

    brancher(monkeypatch)
    org = _org()
    _identite(org)
    _abonnement(org)
    inv = billing_invoices.ensure_invoice_for_payment(_paiement(org), plan="standard")

    stub_authz(monkeypatch, org_id=org)
    code, corps = call("me.billing.invoices.list")

    assert code == 200 and len(corps["invoices"]) == 1
    vue = corps["invoices"][0]
    assert vue["number"] == inv["number"] and vue["status"] == "issued"
    assert vue["has_pdf"] is True
    assert vue["pdf_path"] == f"/api/me/billing/invoices/{inv['id']}/pdf"
    assert "pdf" not in vue, "les OCTETS ne sortent jamais d'une liste"
    # La réponse doit être sérialisable telle quelle : c'est tout l'enjeu.
    json.dumps(corps)


def test_une_facture_en_attente_na_pas_de_chemin_de_pdf(live, monkeypatch):
    """Un lien vers une 404 se subit au clic, il ne se diagnostique pas."""
    from oto_mcp import billing_invoices

    monkeypatch.delenv(billing_invoices.PLATFORM_KEY_ENV, raising=False)
    org = _org()
    _identite(org)
    _abonnement(org)
    billing_invoices.ensure_invoice_for_payment(_paiement(org), plan="standard")

    stub_authz(monkeypatch, org_id=org)
    _, corps = call("me.billing.invoices.list")

    vue = corps["invoices"][0]
    assert vue["status"] == "pending" and vue["number"] is None
    assert vue["has_pdf"] is False and vue["pdf_path"] is None
    assert "error_code" not in vue, "la cause d'un échec est interne, pas cliente"


# ── la route de téléchargement ───────────────────────────────────────────────

def _route_pdf(monkeypatch, sub: str = "u-1"):
    """Monte la route réelle (`api/billing.py`) et rend un appelant."""
    from oto_mcp.api import billing as api_billing

    monkeypatch.setenv("OTO_BILLING_ENABLED", "1")

    async def _auth(_req, _verifier, **kw):
        return sub, None

    def _json_error(_req, status, code, detail=None, details=None):
        return JSONResponse({"error": code, "detail": detail}, status_code=status)

    async def _options(_req):
        return JSONResponse({})

    routes = api_billing.make_routes(_options, verifier=None, authenticate=_auth,
                                     json_error=_json_error)
    handler = next(r for r in routes
                   if r.path.endswith("/pdf") and "GET" in (r.methods or []))

    def appeler(invoice_id):
        async def _receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        req = Request({"type": "http", "method": "GET", "path": handler.path,
                       "headers": [], "query_string": b"",
                       "path_params": {"id": str(invoice_id)}}, _receive)
        return asyncio.run(handler.endpoint(req))

    return appeler


def test_le_pdf_se_telecharge_pour_un_membre_de_lorg_facturee(live, monkeypatch):
    from oto_mcp import billing_invoices
    from oto_mcp.db._conn import _connect

    brancher(monkeypatch)
    org = _org()
    _identite(org)
    _abonnement(org)
    inv = billing_invoices.ensure_invoice_for_payment(_paiement(org), plan="standard")
    with _connect() as conn:
        conn.execute("INSERT INTO org_members (org_id, sub, org_role) "
                     "VALUES (%s, 'u-membre', 'org_member')", (org,))

    rep = _route_pdf(monkeypatch, sub="u-membre")(inv["id"])

    assert rep.status_code == 200
    assert rep.media_type == "application/pdf"
    assert bytes(rep.body).startswith(b"%PDF")
    assert inv["number"] in rep.headers["content-disposition"]


def test_un_etranger_ne_distingue_pas_une_facture_absente_dune_interdite(live, monkeypatch):
    """404 et non 403 : un « interdit » confirmerait l'existence du document, donc
    la facturation d'une autre org."""
    from oto_mcp import billing_invoices

    brancher(monkeypatch)
    org = _org()
    _identite(org)
    _abonnement(org)
    inv = billing_invoices.ensure_invoice_for_payment(_paiement(org), plan="standard")

    appeler = _route_pdf(monkeypatch, sub="u-etranger")
    assert appeler(inv["id"]).status_code == 404
    assert appeler(999_999).status_code == 404


def test_un_document_sans_fichier_le_dit(live, monkeypatch):
    """Émis mais PDF pas encore récupéré : un 409 nommé, jamais un corps vide."""
    from oto_mcp import billing_invoices
    from oto_mcp.db import billing_invoices as db_invoices
    from oto_mcp.db._conn import _connect

    brancher(monkeypatch)
    org = _org()
    _identite(org)
    _abonnement(org)
    inv = billing_invoices.ensure_invoice_for_payment(_paiement(org), plan="standard")
    db_invoices.set_billing_invoice_pdf(inv["id"], None)
    with _connect() as conn:
        conn.execute("INSERT INTO org_members (org_id, sub, org_role) "
                     "VALUES (%s, 'u-admin2', 'org_admin')", (org,))

    rep = _route_pdf(monkeypatch, sub="u-admin2")(inv["id"])
    assert rep.status_code == 409
    assert json.loads(bytes(rep.body))["error"] == "pdf_not_available"


def test_billing_dormant_la_route_repond_404(live, monkeypatch):
    """La route est montée en toutes circonstances (les cliquets de surface
    l'exigent) : c'est le HANDLER qui porte le dark launch. Un client d'un
    déploiement sans billing voit exactement ce qu'il verrait d'une route absente."""
    from oto_mcp import billing_invoices

    brancher(monkeypatch)
    org = _org()
    _identite(org)
    _abonnement(org)
    inv = billing_invoices.ensure_invoice_for_payment(_paiement(org), plan="standard")

    appeler = _route_pdf(monkeypatch, sub="u-1")
    monkeypatch.delenv("OTO_BILLING_ENABLED", raising=False)
    rep = appeler(inv["id"])

    assert rep.status_code == 404
    assert json.loads(bytes(rep.body))["error"] == "billing_disabled"


# ── la reprise ───────────────────────────────────────────────────────────────

def test_le_balayage_facture_ce_qui_ne_lavait_pas_ete(live, monkeypatch):
    """Le filet : un encaissement que personne n'a facturé en ligne."""
    from oto_mcp import billing_invoices
    from oto_mcp.db import billing_invoices as db_invoices

    brancher(monkeypatch)
    org = _org()
    _identite(org)
    _abonnement(org)
    paiement = _paiement(org)              # aucun appel d'émission

    counts = billing_invoices.sweep()

    assert counts.get("invoice_new", 0) >= 1
    inv = db_invoices.get_billing_invoice_for_payment(paiement["id"])
    assert inv and inv["status"] == "issued"


def test_le_balayage_rejoue_une_emission_restee_en_attente(live, monkeypatch):
    """Une clé absente puis posée : la facture part au tick suivant, sans doublon."""
    from oto_mcp import billing_invoices
    from oto_mcp.db import billing_invoices as db_invoices

    monkeypatch.delenv(billing_invoices.PLATFORM_KEY_ENV, raising=False)
    org = _org()
    _identite(org)
    _abonnement(org)
    paiement = _paiement(org)
    attente = billing_invoices.ensure_invoice_for_payment(paiement, plan="standard")
    assert attente["status"] == "pending"

    faux = brancher(monkeypatch)           # le fournisseur redevient joignable
    billing_invoices.sweep()

    inv = db_invoices.get_billing_invoice_for_payment(paiement["id"])
    assert inv["id"] == attente["id"], "la MÊME ligne aboutit, il n'en naît pas une seconde"
    assert inv["status"] == "issued" and inv["error_code"] is None
    assert len(db_invoices.list_billing_invoices(org)) == 1
    assert len([c for c in faux.calls if c[0] == "create_invoice"]) == 1


# ── le webhook ───────────────────────────────────────────────────────────────

def test_le_webhook_dune_echeance_encaissee_facture(live, monkeypatch):
    from oto_mcp import billing, billing_invoices, mollie_client
    from oto_mcp.db import billing_invoices as db_invoices

    brancher(monkeypatch)
    org = _org()
    _identite(org)
    _abonnement(org)
    paiement = _paiement(org, kind="renewal", statut="open")
    ref = paiement["payment_intent_id"]
    monkeypatch.setattr(mollie_client, "get_payment",
                        lambda pid: {"id": ref, "status": "paid"})

    assert billing.process_webhook(ref) == "updated"

    inv = db_invoices.get_billing_invoice_for_payment(paiement["id"])
    assert inv and inv["status"] == "issued"


def test_le_webhook_dun_remboursement_produit_lavoir(live, monkeypatch):
    """Mollie n'a pas d'URL propre aux remboursements : c'est le webhook du PAIEMENT
    qui rappelle, et `amountRefunded` qui porte l'information."""
    from oto_mcp import billing, billing_invoices, mollie_client
    from oto_mcp.db import billing_invoices as db_invoices

    brancher(monkeypatch)
    org = _org()
    _identite(org)
    _abonnement(org)
    paiement = _paiement(org)
    billing_invoices.ensure_invoice_for_payment(paiement, plan="standard")
    ref = paiement["payment_intent_id"]
    monkeypatch.setattr(mollie_client, "get_payment", lambda pid: {
        "id": ref, "status": "paid",
        "amountRefunded": {"currency": "EUR", "value": "22.80"}})

    assert billing.process_webhook(ref) == "refunded"

    avoir = db_invoices.get_billing_invoice_for_payment(paiement["id"], "credit_note")
    assert avoir and avoir["status"] == "issued" and avoir["amount_ttc"] == -2280


def test_un_paiement_sans_remboursement_ne_declenche_pas_davoir(live, monkeypatch):
    """`amountRefunded` absent (le cas courant) ne doit pas se lire comme zéro
    remboursé mais traité — la branche ne s'ouvre pas du tout."""
    from oto_mcp import mollie_client

    assert mollie_client.cents_from_amount(None) == 0
    assert mollie_client.cents_from_amount({"currency": "EUR", "value": "0.00"}) == 0
    assert mollie_client.cents_from_amount({"currency": "EUR", "value": "22.80"}) == 2280


# ── les garanties de schéma ──────────────────────────────────────────────────

def test_un_seul_document_par_paiement_et_par_nature(live):
    """L'idempotence est une CONTRAINTE, pas une lecture préalable : deux webhooks
    simultanés franchiraient tout `SELECT` avant insertion."""
    from oto_mcp.db import billing_invoices as db_invoices

    org = _org()
    paiement = _paiement(org)
    a = db_invoices.ensure_billing_invoice(org, paiement["id"], payment_ref="tr_x")
    b = db_invoices.ensure_billing_invoice(org, paiement["id"], payment_ref="tr_x")
    avoir = db_invoices.ensure_billing_invoice(org, paiement["id"], kind="credit_note")

    assert a["id"] == b["id"], "une seule facture par paiement"
    assert avoir["id"] != a["id"], "l'avoir est un SECOND document du même paiement"
    assert len(db_invoices.list_billing_invoices(org)) == 2


def test_une_nature_inconnue_est_refusee(live):
    from oto_mcp.db import billing_invoices as db_invoices

    org = _org()
    paiement = _paiement(org)
    with pytest.raises(ValueError, match="kind de facture inconnu"):
        db_invoices.ensure_billing_invoice(org, paiement["id"], kind="reçu")


def test_les_factures_ne_survivent_pas_a_leur_org(live):
    from oto_mcp.db import billing_invoices as db_invoices
    from oto_mcp.db._conn import _connect

    org = _org("EPHEMERE " + uuid.uuid4().hex[:6])
    paiement = _paiement(org)
    db_invoices.ensure_billing_invoice(org, paiement["id"])
    with _connect() as conn:
        conn.execute("DELETE FROM orgs WHERE id = %s", (org,))
        reste = conn.execute("SELECT COUNT(*) AS n FROM billing_invoices "
                             "WHERE org_id = %s", (org,)).fetchone()["n"]
    assert reste == 0
