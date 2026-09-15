"""Le gréement des tests de facturation (#488) — et l'espion de sortie réseau.

Il n'y a plus de faux Pennylane ici, et c'est le sujet : **depuis le 2026-09-09 la
plateforme n'émet plus aucune pièce chez le fournisseur** (`billing_invoices/
emission.py` dit la décision). Un double du fournisseur ne prouverait donc plus
rien — ce qu'il faut pouvoir démontrer est qu'aucun geste ne part vers lui.

L'espion se pose donc sur la **destination**, pas sur nos fonctions :
`requests.Session.request` (le seul entonnoir du client Pennylane d'oto-core) et
`httpx.Client.send` (celui du relais d'e-mail et de tout téléchargement). Un appel
réintroduit par un chemin qu'on n'a pas prévu — un client neuf, un import par
valeur ailleurs — y apparaîtrait quand même.

⚠️ **La garde socket du conftest ne suffit pas à prouver ça.** Elle bloque les
connexions sortantes, mais elle le fait en LEVANT, et les chemins de facturation
absorbent tout (une exception ne doit jamais atteindre le payeur, #493). Une
tentative réelle deviendrait donc un journal, pas un rouge. L'espion, lui, garde la
trace de la tentative : c'est elle qu'on mesure.

Il porte aussi la fixture `live` (une base PostgreSQL jetable) et le gréement des
tests de facturation — org, identité, abonnement, ligne de journal encaissée, et un
document ÉMIS écrit directement par le store (plus aucun chemin de code ne sait en
produire un). Ils sont partagés par deux fichiers de test et n'appartiennent à
aucun des deux ; les laisser dans l'un ferait importer un module `test_*` depuis
l'autre, ce que pytest collecte deux fois.
"""
from __future__ import annotations

import uuid

import pytest


class SortieReseau(RuntimeError):
    """Levée par l'espion à la place de l'appel : rien ne part sur le fil."""


class EspionReseau:
    """Le journal des tentatives de sortie — `(verbe, url)`, dans l'ordre."""

    def __init__(self):
        self.appels: list[tuple[str, str]] = []

    def vers(self, fragment: str) -> list[tuple[str, str]]:
        return [a for a in self.appels if fragment in a[1]]

    def _voir(self, verbe: str, url: str):
        self.appels.append((str(verbe).upper(), str(url)))
        raise SortieReseau(f"{verbe} {url}")


def espionner_le_reseau(monkeypatch) -> EspionReseau:
    """Pose l'espion sur les DEUX entonnoirs de sortie du process, et le rend.

    `requests.Session.request` couvre tout le client Pennylane d'oto-core (ses
    `get`/`post`/`put` passent par là) ; `httpx.Client.send` couvre les helpers de
    module `httpx.get`/`httpx.post`, qui construisent un client et l'appellent."""
    import httpx
    import requests

    espion = EspionReseau()

    def _requests(self, method, url, *a, **k):
        return espion._voir(method, url)

    def _httpx(self, request, *a, **k):
        return espion._voir(request.method, request.url)

    monkeypatch.setattr(requests.sessions.Session, "request", _requests)
    monkeypatch.setattr(httpx.Client, "send", _httpx)
    return espion


# ── gréement des tests de facturation ────────────────────────────────────────

IDENTITE_FR = dict(legal_name="ACME SAS", country_code="FR",
                   address_line="1 rue de la Paix", postal_code="13001",
                   city="Marseille", billing_email="compta@acme.test")


# ── petits gréements ─────────────────────────────────────────────────────────

def _org(nom: str = "ACME") -> int:
    from oto_mcp.db._conn import _connect

    with _connect() as conn:
        return conn.execute(
            "INSERT INTO orgs (name) VALUES (%s) RETURNING id", (nom,)
        ).fetchone()["id"]


def _identite(org: int, **champs) -> None:
    from oto_mcp.db import billing as db_billing

    db_billing.upsert_billing_identity(org, **{**IDENTITE_FR, **champs})


def _abonnement(org: int, plan: str = "standard") -> None:
    from oto_mcp.db import billing as db_billing

    db_billing.upsert_org_subscription(org, plan=plan, customer_id="cst_1",
                                       mandate_id="mdt_1", status="active",
                                       current_period_end="2026-09-28 10:00:00")


def _paiement(org: int, *, ht: int = 1900, pays: str = "FR",
              tva: str | None = None, kind: str = "initial",
              statut: str = "paid", sans_tva: bool = False) -> dict:
    """Une ligne de journal ENCAISSÉE, décomposition fiscale comprise.

    `sans_tva=True` reproduit les deux encaissements du 25/08 : débités du HT, sans
    `amount_ht` — ce que la règle (c) exclut de la facturation automatique."""
    from oto_mcp import billing_vat
    from oto_mcp.db import billing as db_billing
    from oto_mcp.db import billing_invoices as db_invoices

    if sans_tva:
        row_id = db_billing.insert_billing_payment(
            org, kind, ht, payment_intent_id=f"tr_{uuid.uuid4().hex[:8]}",
            status=statut, tax=None)
    else:
        tax = billing_vat.tax_for(ht, pays, tva)
        row_id = db_billing.insert_billing_payment(
            org, kind, tax["amount_ttc"],
            payment_intent_id=f"tr_{uuid.uuid4().hex[:8]}", status=statut, tax=tax)
    return db_invoices.billing_payment_row(row_id)


def _document_emis(org: int, *, numero: str = "F-2026-09-7",
                   pdf: bytes | None = b"%PDF-1.4 faux document",
                   paiement: dict | None = None) -> dict:
    """Un document ÉMIS — écrit directement par le store, et il n'y a plus d'autre
    façon d'en obtenir un : le chemin qui appelait Pennylane a été retiré le
    2026-09-09.

    C'est ce que la surface (liste, route PDF, autorisation) doit continuer de
    servir : les documents d'avant la coupure, et ceux qu'une main posera."""
    from oto_mcp.db import billing_invoices as db_invoices

    paiement = paiement or _paiement(org)
    row = db_invoices.ensure_billing_invoice(
        org, paiement["id"], kind="invoice",
        payment_ref=paiement.get("payment_intent_id"))
    db_invoices.mark_billing_invoice_issued(
        row["id"], pennylane_invoice_id=4242, number=numero,
        external_reference=f"oto-payment-{paiement.get('payment_intent_id')}",
        pennylane_customer_id=77, amount_ht=1900, vat_rate_bps=2000,
        vat_amount=380, amount_ttc=2280, vat_scheme="fr_ttc",
        issued_at=paiement["updated_at"])
    db_invoices.set_billing_invoice_pdf(
        row["id"], pdf, filename=f"{numero}.pdf",
        url="https://faux.pennylane.test/4242.pdf")
    return db_invoices.get_billing_invoice(row["id"])
