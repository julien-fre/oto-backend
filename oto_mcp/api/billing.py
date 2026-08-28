"""Les deux routes billing écrites à la main : le webhook Mollie et le PDF d'une facture.

`POST /api/billing/webhook` (ADR 0043) — **non authentifié** (Mollie l'appelle sans
JWT). Modèle Mollie : le corps ne porte QUE l'id du paiement (`id=tr_…`,
form-encodé) ; on re-fetch l'objet avec NOTRE clé API → aucune confiance dans le
POST (un id forgé/inconnu ne déclenche rien). Complète le polling du billing_runner
(le socle), il ne le remplace pas. Toujours 200 : Mollie retente sur non-2xx, et un
id qu'on ne suit pas n'est pas une erreur.

`GET /api/me/billing/invoices/{id}/pdf` (#488) — **authentifié**, et écrit à la main
pour une raison structurelle : un handler de capacité rend un `dict` que
l'adaptateur emballe en `JSONResponse`, il ne peut pas servir `application/pdf`.
Même exception, même précédent que l'export ZIP d'un projet
(`api/projects.py::me_project_export`). La LISTE, elle, reste une capacité
(`capabilities/billing_invoices.py`).

⚠️ Cette seconde route n'est montée que si `OTO_BILLING_ENABLED=1` — même gate que
les capacités billing, pour que la surface facturation apparaisse ou disparaisse
d'un bloc. Le webhook, lui, est monté toujours : Mollie doit obtenir un 200 même
quand le billing dort.
"""
from __future__ import annotations

from typing import Awaitable, Callable, Optional

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response
from starlette.routing import Route

from .. import billing, mollie_client, roles
from .base import AuthFn


def make_routes(options_handler: Callable[[Request], Awaitable[Response]],
                *, verifier=None, authenticate: Optional[AuthFn] = None,
                json_error=None) -> list[Route]:

    async def webhook(request: Request) -> Response:
        # billing non configuré (dormant / clé absente) → no-op silencieux.
        if not mollie_client.is_configured():
            return PlainTextResponse("ok")
        try:
            form = await request.form()
        # noqa: SILENT — ACK délibéré : un webhook rejoué en boucle est pire (compteur à poser, #424)
        except Exception:
            return PlainTextResponse("ok")
        payment_id = (form.get("id") or "").strip()
        if not payment_id:
            return PlainTextResponse("ok")   # ping / corps vide
        try:
            # DB + httpx sync → hors event loop (serveur mono-loop).
            await run_in_threadpool(billing.process_webhook, payment_id)
        except mollie_client.MollieError:
            # amont Mollie en erreur (id disparu, 5xx) : on absorbe, le polling
            # rattrapera ; répondre 200 évite une tempête de retries Mollie.
            pass
        return PlainTextResponse("ok")

    async def invoice_pdf(request: Request) -> Response:
        """Le PDF d'une facture, en pièce téléchargeable.

        L'autorisation est l'appartenance à l'org QUI PORTE la facture, et non
        l'org active : ce lien s'ouvre depuis un e-mail, où rien ne garantit que
        l'org de session est celle qu'on facture. `roles.is_org_member` porte
        l'escalade admin plateforme, comme la règle `ORG_MEMBER_OF` des capacités.
        """
        sub, err = await authenticate(request, verifier)
        if err:
            return err
        try:
            invoice_id = int(request.path_params["id"])
        except (KeyError, ValueError):
            return json_error(request, 400, "bad_invoice")
        from ..db import billing_invoices as db_invoices

        row = await run_in_threadpool(db_invoices.get_billing_invoice_pdf, invoice_id)
        if not row:
            return json_error(request, 404, "invoice_not_found")
        if not roles.is_org_member(sub, row["org_id"]):
            # 404 et non 403 : répondre « interdit » sur un id qu'on ne possède pas
            # confirmerait son existence, donc la facturation d'une autre org.
            return json_error(request, 404, "invoice_not_found")
        if not row.get("pdf"):
            # Le document peut être ÉMIS sans que son fichier ait été récupéré (le
            # fournisseur ne le rend pas toujours dans la foulée). La reprise le
            # retéléchargera — on le dit plutôt que de servir un corps vide.
            return json_error(request, 409, "pdf_not_available",
                              "Le PDF de ce document n'a pas encore été récupéré "
                              "auprès du fournisseur — il le sera automatiquement.")
        nom = row.get("pdf_filename") or f"facture-{invoice_id}.pdf"
        return Response(row["pdf"], media_type="application/pdf",
                        headers={"Content-Disposition": f'attachment; filename="{nom}"'})

    routes = [
        Route("/api/billing/webhook", webhook, methods=["POST"]),
        Route("/api/billing/webhook", options_handler, methods=["OPTIONS"]),
    ]
    if billing.is_enabled() and authenticate is not None:
        routes += [
            Route("/api/me/billing/invoices/{id}/pdf", invoice_pdf, methods=["GET"]),
            Route("/api/me/billing/invoices/{id}/pdf", options_handler,
                  methods=["OPTIONS"]),
        ]
    return routes
