"""Stripe — clients, abonnements, factures, encaissements, solde, catalogue.

Wrappe `oto.tools.stripe.client.StripeClient` (API v1, Bearer, corps
form-encodés). Credential à TROIS champs (`secret_kind="fields"`, résolu par
`access.resolve_credential_fields`) : la clé, plus deux satellites NON secrets
qui décident **ce que la clé lit**.

- `api_version` — vide = la version par défaut du compte, celle que le client
  voit dans son propre dashboard. Épingler une version qui diverge du compte
  change des formes de réponse en silence.
- `stripe_account` — `acct_…` (Connect). Ce n'est pas de la décoration : avec
  Connect, LA MÊME question rend le chiffre d'affaires d'une AUTRE société
  selon cet en-tête. En faire un champ de credential plutôt qu'un paramètre
  d'outil est la forme la plus forte de « jamais déduit par appel » : un
  credential = un jeu de livres, posé une fois, visible sur la carte, et
  impossible à basculer en cours de conversation.

**byo-only, jamais de clé plateforme** : ce sont les livres de comptes du
client. Une clé Stripe partagée par plusieurs orgs n'a aucun sens.

**Rien ne déplace d'argent, et c'est structurel.** Ce module ne peut PAS
rembourser, résilier, finaliser, encaisser, envoyer ni supprimer : les méthodes
correspondantes n'existent pas sur `StripeClient` (choix documenté dans son
docstring). Ce n'est donc pas une politique de ce fichier, qu'une PR d'une ligne
lèverait — c'est une frontière qui demande une PR oto-core pour bouger.

**Neuf tools, un par objet métier** (ADR 0047), verbe en `op=`. Aucun paramètre
n'est retenu au silence : un `op` qui n'utilise pas un argument fourni REFUSE
(patron `_refuse_ignored`, silae/granola).

**Testé en live le 2026-08-22** contre un vrai compte Stripe en mode test (clé
restreinte `rk_test_`) : les 20 lectures et les écritures sûres répondent comme
codé. Deux comportements que la doc ne dit pas, tous deux trouvés par sonde :

1. ⚠️ **`create_draft` n'attrape PAS les lignes en attente par défaut.** Créer
   une ligne (`op="add_item"`) puis une facture rendait `total=0` et zéro ligne,
   la ligne restant `invoice=None`. Un agent aurait annoncé « facture créée »
   en produisant une facture VIDE et en laissant le montant en suspens. D'où le
   défaut `pending_items="include"` ici (mesuré : `total=4200`, 1 ligne).
2. ⚠️ **Un lien de paiement peut exiger un `tax_code` sur le produit** quand le
   compte est éligible aux paiements gérés : `create_link` rendait
   `400 « the product tax code is missing »`. Poser `tax_code` sur le produit
   (ex. `txcd_10000000`, services génériques) débloque — le message d'erreur le
   dit désormais explicitement.
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from fastmcp import FastMCP
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData, INVALID_PARAMS

from .. import access, connector_verify

# Stripe borne ses listes à 100 et retombe SILENCIEUSEMENT à 10 quand `limit`
# est omis. On pose donc toujours une valeur explicite.
_DEFAULT_LIMIT = 100
_MAX_LIMIT = 100

# Plafond de balayage d'`op="totals"` : 20 pages × 100 = 2 000 objets. Au-delà,
# la réponse le DIT (`complete: false`) au lieu de rendre une somme partielle
# qui passerait pour le chiffre d'affaires réel.
_AGGREGATE_MAX_PAGES = 20


def _bad(msg: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=msg))


def _refuse_ignored(op: str, hint: str, **provided) -> None:
    """Un argument fourni que CET op n'utilise pas est une erreur d'intention.
    Sinon `stripe_invoice(op="list", invoice_id=…)` rendrait TOUTES les factures
    en laissant croire qu'on en a ciblé une."""
    for name, value in provided.items():
        if value is not None:
            raise _bad(f"op={op!r} n'utilise pas `{name}` — {hint}")


def _limit(value: Optional[int]) -> int:
    if value is None:
        return _DEFAULT_LIMIT
    if not 1 <= value <= _MAX_LIMIT:
        raise _bad(f"`limit` doit être entre 1 et {_MAX_LIMIT} (reçu {value}) — "
                   "Stripe n'en rend pas davantage par page.")
    return value


def _window(created_after: Optional[int], created_before: Optional[int]) -> Optional[dict]:
    """La fenêtre temporelle de Stripe est un dict d'opérateurs, pas deux champs."""
    w = {}
    if created_after is not None:
        w["gte"] = created_after
    if created_before is not None:
        w["lt"] = created_before
    return w or None


def _upstream_message(e) -> str:
    status = e.status_code
    body = e.body if isinstance(e.body, dict) else {}
    err = body.get("error", {}) if isinstance(body, dict) else {}
    detail = err.get("message") or ""
    code = err.get("code")
    param = err.get("param")
    req = body.get("request_id")
    tail = f" (request_id {req})" if req else ""
    if status in (401, 403):
        return (f"Stripe a rejeté la clé (HTTP {status}) — soit elle est invalide, soit "
                f"c'est une clé RESTREINTE à laquelle il manque la permission de lecture "
                f"pour cette ressource. Stripe Dashboard → Developers → API keys.{tail} "
                f"{detail}")
    if status == 404:
        return (f"Stripe : objet introuvable. Vérifie l'identifiant, et surtout le MODE : "
                f"un objet de test n'existe pas en mode réel, et inversement.{tail} {detail}")
    if status == 429:
        return f"Stripe : trop de requêtes (429) — réessaie dans un instant.{tail}"
    if status >= 500:
        return f"Stripe est momentanément indisponible (HTTP {status}) — réessaie plus tard.{tail}"
    extra = ""
    if "tax code is missing" in detail:
        extra = (" — pose un code de taxe sur le produit avant de créer le lien : "
                 "`stripe_catalog(op=\"update_product\", product_id=…, "
                 "tax_code=\"txcd_10000000\")` (services génériques).")
    where = f" (champ `{param}`)" if param else ""
    return f"Stripe a refusé la requête (HTTP {status}, code {code}){where} : {detail}{extra}{tail}"


def _verify(fields: dict, config: dict | None = None) -> None:
    """Sonde « tester la connexion » : deux GET, aucun effet de bord.

    `GET /v1/balance` prouve que la clé vit — mais une clé restreinte au seul
    Balance:read le passerait tout en échouant sur chaque question réelle. La
    seconde lecture (une facture, `limit=1`) prouve donc la CAPACITÉ, pas
    seulement l'authentification : c'est la leçon de la sonde Zoho (auth OK,
    zéro scope CRM).
    """
    from oto.tools.stripe.client import StripeClient
    cfg = config or {}
    client = StripeClient(fields["api_key"],
                          api_version=cfg.get("api_version") or None,
                          stripe_account=cfg.get("stripe_account") or None)
    balance = client.balance()
    mode = "réel" if balance.get("livemode") else "test"
    try:
        client.list_invoices(limit=1)
    except Exception as e:  # noqa: BLE001 — le message d'exception EST le retour d'erreur
        raise RuntimeError(
            f"La clé authentifie bien (solde lu, mode {mode}) mais ne peut pas lire les "
            f"factures — c'est une clé restreinte sans la permission Invoices:read. "
            f"Détail : {e}") from e


def register(mcp: FastMCP) -> None:
    from oto.tools.common.errors import UpstreamHTTPError
    from oto.tools.stripe.client import StripeClient

    connector_verify.register("stripe", _verify)

    def _client() -> StripeClient:
        fields = access.resolve_credential_fields("stripe")
        return StripeClient(fields["api_key"],
                            api_version=fields.get("api_version") or None,
                            stripe_account=fields.get("stripe_account") or None)

    def _run(fn):
        try:
            return fn()
        except ValueError as e:
            raise _bad(str(e))
        except UpstreamHTTPError as e:
            raise _bad(_upstream_message(e))

    # ================================================================
    # Recherche — la surface de lecture la plus rentable
    # ================================================================

    @mcp.tool()
    def stripe_search(
        resource: Literal["customers", "invoices", "subscriptions", "charges",
                          "payment_intents", "products", "prices"],
        query: str,
        limit: Optional[int] = None,
        page: Optional[str] = None,
    ) -> object:
        """Search Stripe with its own query language. One verb, no `op` — the
        axis here is `resource`.

        Only these SEVEN resources are searchable; anything else must be listed
        with filters instead.

        Only these SEVEN resources are searchable; anything else must be listed
        with filters instead.

        Search is eventually consistent: an object created seconds ago may not
        appear yet. To read something just written, fetch it by id.

        Args:
            resource: which object type to search.
            query: Stripe Query Language, e.g. `email~"acme.com"`,
                `status:"active"`, `created>1704067200`,
                `metadata["order_id"]:"6735"`, `total>10000 AND currency:"eur"`.
                Operators: `:` exact, `~` contains, `>` `<` `>=` `<=`,
                `AND`/`OR`, `-` negation. Searchable FIELDS differ per resource.
            limit: 1-100 (default 100).
            page: the opaque `next_page` token from a previous response — NOT
                the `starting_after` cursor the list ops use; they are different
                paginators.

        """
        client = _client()
        return _run(lambda: client.search(resource, query, limit=_limit(limit), page=page))

    # ================================================================
    # Clients
    # ================================================================

    @mcp.tool()
    def stripe_customer(
        op: Literal["list", "get", "payment_methods", "create", "update"] = "list",
        customer_id: Optional[str] = None,
        email: Optional[str] = None,
        name: Optional[str] = None,
        description: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        created_after: Optional[int] = None,
        created_before: Optional[int] = None,
        limit: Optional[int] = None,
        starting_after: Optional[str] = None,
    ) -> object:
        """A Stripe customer — the entity almost every other question resolves
        through.

        "payment_methods" is the diagnosis for involuntary churn: an expired card
        (`card.exp_month`/`exp_year`) or an empty list explains most `past_due`
        subscriptions.

        Args:
            op: "list" (default) | "get" | "payment_methods" | "create" | "update".
            customer_id: REQUIRED by "get"/"payment_methods"/"update" (`cus_…`).
            email: on "list", an EXACT-match filter. On "create"/"update", the
                value to set. For partial matching use
                `stripe_search(resource="customers", query='email~"acme.com"')`.
            name/description/metadata: "create"/"update" only.
            created_after/created_before: "list" only — Unix timestamps.
            limit: "list" only, 1-100 (default 100).
            starting_after: "list" pagination cursor — the id of the LAST object
                of the previous page.

        """
        client = _client()
        if op == "list":
            _refuse_ignored(op, "utilise op='create' pour en créer un, op='get' pour en cibler un",
                            customer_id=customer_id, name=name, description=description,
                            metadata=metadata)
            return _run(lambda: client.list_customers(
                email=email, created=_window(created_after, created_before),
                limit=_limit(limit), starting_after=starting_after))
        if op in ("get", "payment_methods"):
            if not customer_id:
                raise _bad(f"op={op!r} requiert `customer_id`")
            _refuse_ignored(op, "ces champs ne s'appliquent qu'à list/create/update",
                            email=email, name=name, description=description,
                            metadata=metadata, created_after=created_after,
                            created_before=created_before, starting_after=starting_after)
            if op == "get":
                return _run(lambda: client.get_customer(customer_id))
            return _run(lambda: client.list_customer_payment_methods(
                customer_id, limit=_limit(limit)))
        if op == "create":
            _refuse_ignored(op, "un nouveau client n'a pas encore d'id",
                            customer_id=customer_id, created_after=created_after,
                            created_before=created_before, starting_after=starting_after,
                            limit=limit)
            if not email and not name:
                raise _bad("op='create' requiert au moins `email` ou `name` — un client "
                           "sans aucun des deux est introuvable ensuite.")
            return _run(lambda: client.create_customer(
                email=email, name=name, description=description, metadata=metadata))
        if op == "update":
            if not customer_id:
                raise _bad("op='update' requiert `customer_id`")
            _refuse_ignored(op, "ces filtres ne s'appliquent qu'à op='list'",
                            created_after=created_after, created_before=created_before,
                            starting_after=starting_after, limit=limit)
            body = {k: v for k, v in dict(email=email, name=name,
                                          description=description,
                                          metadata=metadata).items() if v is not None}
            if not body:
                raise _bad("op='update' requiert au moins un champ à modifier")
            return _run(lambda: client.update_customer(customer_id, **body))
        raise _bad("op doit être 'list', 'get', 'payment_methods', 'create' ou 'update'")

    # ================================================================
    # Abonnements
    # ================================================================

    @mcp.tool()
    def stripe_subscription(
        op: Literal["list", "get", "items"] = "list",
        subscription_id: Optional[str] = None,
        customer_id: Optional[str] = None,
        status: Optional[Literal["active", "past_due", "trialing", "canceled",
                                 "unpaid", "paused", "incomplete",
                                 "incomplete_expired", "ended", "all"]] = None,
        price_id: Optional[str] = None,
        limit: Optional[int] = None,
        starting_after: Optional[str] = None,
    ) -> object:
        """Subscriptions — read-only (cancelling is not available in this
        connector; do it from the Stripe dashboard).

        Stripe has no "churning" status. Build it: `cancel_at_period_end == true`
        on active subs (voluntary, scheduled), `status == "past_due"` or
        `"unpaid"` (involuntary, payment failing), `status == "canceled"` with
        `canceled_at` in the window (already gone).

        "items" returns the subscription's lines, where price and quantity
        (seat counts) actually live for multi-product subscriptions.

        Args:
            op: "list" (default) | "get" | "items".
            subscription_id: REQUIRED by "get"/"items" (`sub_…`).
            customer_id/price_id: "list" filters.
            status: "list" filter. ⚠️ WITHOUT it Stripe returns only active and
                trialing subscriptions — cancelled ones silently disappear, which
                is exactly wrong for a churn question. Pass "all" to see everything.
            limit: 1-100 (default 100). starting_after: pagination cursor.

        """
        client = _client()
        if op == "list":
            _refuse_ignored(op, "utilise op='get' pour un abonnement précis",
                            subscription_id=subscription_id)
            return _run(lambda: client.list_subscriptions(
                customer=customer_id, status=status, price=price_id,
                limit=_limit(limit), starting_after=starting_after))
        if not subscription_id:
            raise _bad(f"op={op!r} requiert `subscription_id`")
        _refuse_ignored(op, "ces filtres ne s'appliquent qu'à op='list'",
                        customer_id=customer_id, status=status, price_id=price_id,
                        starting_after=starting_after)
        if op == "get":
            return _run(lambda: client.get_subscription(subscription_id))
        if op == "items":
            return _run(lambda: client.list_subscription_items(
                subscription_id, limit=_limit(limit)))
        raise _bad("op doit être 'list', 'get' ou 'items'")

    # ================================================================
    # Factures — dont l'agrégat borné, la vraie réponse à « combien »
    # ================================================================

    @mcp.tool()
    def stripe_invoice(
        op: Literal["list", "get", "lines", "totals", "list_items",
                    "create_draft", "add_item", "update"] = "list",
        invoice_id: Optional[str] = None,
        customer_id: Optional[str] = None,
        subscription_id: Optional[str] = None,
        status: Optional[Literal["draft", "open", "paid", "uncollectible", "void"]] = None,
        created_after: Optional[int] = None,
        created_before: Optional[int] = None,
        amount: Optional[int] = None,
        currency: Optional[str] = None,
        description: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        pending_items: Optional[Literal["include", "exclude"]] = None,
        limit: Optional[int] = None,
        starting_after: Optional[str] = None,
    ) -> object:
        """Invoices — what was billed, and the only op here that SUMS.

        **`op="totals"` is how you answer "how much did we bill last month".**
        Listing caps at 100 per page and Stripe gives no total_count, so summing
        by hand across pages is both expensive and silently truncated. "totals"
        sweeps up to 2000 invoices server-side and returns
        `{count, complete, by_currency: {eur: {total, amount_paid, amount_due}}}`.
        ⚠️ It groups BY CURRENCY and never adds currencies together — a single
        number across mixed currencies would be meaningless. If `complete` is
        false, the window held more than the sweep cap: narrow the dates and say
        so rather than reporting the partial sum as fact. Amounts are in each
        currency's smallest unit (cents).

        ⚠️ **"create_draft" only creates a DRAFT.** Nothing is sent to the
        customer and nothing is charged — finalising, sending and paying are not
        available in this connector. Verified live: without
        `pending_items="include"` the draft comes back with `total=0` and no
        lines even when items were just added, leaving the amount dangling —
        hence that default.

        Args:
            op: "list" (default) | "get" | "lines" | "totals" | "list_items" |
                "create_draft" | "add_item" | "update".
            invoice_id: REQUIRED by "get"/"lines"/"update" (`in_…`).
            customer_id: filter on "list"/"totals"/"list_items"; REQUIRED by
                "create_draft"/"add_item".
            subscription_id/status: "list"/"totals" filters.
            created_after/created_before: Unix timestamps — the window for
                "list"/"totals".
            amount: "add_item" — in the currency's SMALLEST unit (cents). A
                NEGATIVE amount is a credit/goodwill gesture.
            currency: "add_item" (e.g. "eur"). description/metadata: writes.
            pending_items: "create_draft" — "include" (default) pulls the
                customer's pending invoice items onto the invoice; "exclude"
                creates an empty draft. See the warning below.
            limit/starting_after: "list"/"list_items" pagination.
        """
        client = _client()
        if op == "list":
            _refuse_ignored(op, "utilise op='get' pour une facture précise",
                            invoice_id=invoice_id, amount=amount, currency=currency,
                            description=description, metadata=metadata,
                            pending_items=pending_items)
            return _run(lambda: client.list_invoices(
                customer=customer_id, subscription=subscription_id, status=status,
                created=_window(created_after, created_before),
                limit=_limit(limit), starting_after=starting_after))
        if op == "totals":
            _refuse_ignored(op, "un agrégat ne cible pas une facture ni n'écrit",
                            invoice_id=invoice_id, amount=amount, currency=currency,
                            description=description, metadata=metadata,
                            pending_items=pending_items, starting_after=starting_after,
                            limit=limit)
            return _run(lambda: _totals(
                client, customer=customer_id, subscription=subscription_id,
                status=status, created=_window(created_after, created_before)))
        if op == "list_items":
            _refuse_ignored(op, "ces champs ne s'appliquent pas à op='list_items'",
                            subscription_id=subscription_id, status=status,
                            amount=amount, currency=currency, description=description,
                            metadata=metadata, pending_items=pending_items)
            return _run(lambda: client.list_invoice_items(
                customer=customer_id, invoice=invoice_id, limit=_limit(limit),
                starting_after=starting_after))
        if op in ("get", "lines"):
            if not invoice_id:
                raise _bad(f"op={op!r} requiert `invoice_id`")
            _refuse_ignored(op, "ces filtres ne s'appliquent qu'à op='list'/'totals'",
                            customer_id=customer_id, subscription_id=subscription_id,
                            status=status, created_after=created_after,
                            created_before=created_before, amount=amount,
                            currency=currency, metadata=metadata,
                            pending_items=pending_items)
            if op == "get":
                return _run(lambda: client.get_invoice(invoice_id))
            return _run(lambda: client.get_invoice_lines(invoice_id, limit=_limit(limit)))
        if op == "add_item":
            if not customer_id:
                raise _bad("op='add_item' requiert `customer_id`")
            if amount is None or not currency:
                raise _bad("op='add_item' requiert `amount` (en centimes) et `currency`")
            _refuse_ignored(op, "ces filtres ne s'appliquent pas à une création de ligne",
                            subscription_id=subscription_id, status=status,
                            created_after=created_after, created_before=created_before,
                            pending_items=pending_items, starting_after=starting_after,
                            limit=limit)
            return _run(lambda: client.create_invoice_item(
                customer=customer_id, amount=amount, currency=currency,
                invoice=invoice_id, description=description, metadata=metadata))
        if op == "create_draft":
            if not customer_id:
                raise _bad("op='create_draft' requiert `customer_id`")
            _refuse_ignored(op, "une facture neuve n'a pas encore d'id",
                            invoice_id=invoice_id, status=status,
                            created_after=created_after, created_before=created_before,
                            amount=amount, currency=currency,
                            starting_after=starting_after, limit=limit)
            return _run(lambda: client.create_invoice(
                customer=customer_id, subscription=subscription_id,
                auto_advance=False,
                pending_invoice_items_behavior=pending_items or "include",
                description=description, metadata=metadata))
        if op == "update":
            if not invoice_id:
                raise _bad("op='update' requiert `invoice_id`")
            body = {k: v for k, v in dict(description=description,
                                          metadata=metadata).items() if v is not None}
            if not body:
                raise _bad("op='update' requiert au moins `description` ou `metadata` — "
                           "une facture FINALISÉE n'accepte plus que ces champs.")
            return _run(lambda: client.update_invoice(invoice_id, **body))
        raise _bad("op inconnu pour stripe_invoice")

    def _totals(client, **filters) -> dict:
        """Balaie les factures d'une fenêtre et somme PAR DEVISE, jusqu'au
        plafond. Le drapeau `complete` dit si le balayage a tout vu — une somme
        partielle présentée comme le chiffre d'affaires serait le pire des
        résultats possibles."""
        by_currency: Dict[str, Dict[str, int]] = {}
        counts_by_status: Dict[str, int] = {}
        count = 0
        cursor = None
        complete = True
        for page in range(_AGGREGATE_MAX_PAGES + 1):
            if page == _AGGREGATE_MAX_PAGES:
                complete = False
                break
            batch = client.list_invoices(limit=_MAX_LIMIT, starting_after=cursor,
                                         **filters)
            rows = batch.get("data", [])
            for inv in rows:
                cur = (inv.get("currency") or "?").lower()
                acc = by_currency.setdefault(cur, {"total": 0, "amount_paid": 0,
                                                   "amount_due": 0})
                acc["total"] += inv.get("total") or 0
                acc["amount_paid"] += inv.get("amount_paid") or 0
                acc["amount_due"] += inv.get("amount_due") or 0
                st = inv.get("status") or "?"
                counts_by_status[st] = counts_by_status.get(st, 0) + 1
            count += len(rows)
            if not batch.get("has_more") or not rows:
                break
            cursor = rows[-1]["id"]
        return {
            "count": count,
            "complete": complete,
            "by_currency": by_currency,
            "count_by_status": counts_by_status,
            "note": ("Montants dans la plus petite unité de chaque devise (centimes). "
                     "Jamais additionnés entre devises." if by_currency else
                     "Aucune facture dans cette fenêtre.")
            + ("" if complete else
               f" ⚠️ INCOMPLET : plus de {_AGGREGATE_MAX_PAGES * _MAX_LIMIT} factures "
               "dans la fenêtre, la somme ne porte que sur les premières. Restreins "
               "la période."),
        }

    # ================================================================
    # Paiements — intentions, encaissements, remboursements, litiges
    # ================================================================

    @mcp.tool()
    def stripe_payment(
        op: Literal["list_intents", "get_intent", "list_charges", "get_charge",
                    "list_refunds", "get_refund", "list_disputes",
                    "get_dispute"] = "list_charges",
        payment_intent_id: Optional[str] = None,
        charge_id: Optional[str] = None,
        refund_id: Optional[str] = None,
        dispute_id: Optional[str] = None,
        customer_id: Optional[str] = None,
        created_after: Optional[int] = None,
        created_before: Optional[int] = None,
        limit: Optional[int] = None,
        starting_after: Optional[str] = None,
    ) -> object:
        """Payments — read-only. Refunding is not available in this connector.

        For "why did this payment fail": the PaymentIntent's `last_payment_error`
        is authoritative (`code`, `decline_code`, `message`); on a Charge, prefer
        `outcome.seller_message`, which is written for the merchant.

        Disputes carry a hard deadline in `evidence_details.due_by`, and the money
        is already withdrawn from the balance while one is open — surface them
        proactively rather than on request.

        Args:
            op: "list_charges" (default) | "get_charge" | "list_intents" |
                "get_intent" | "list_refunds" | "get_refund" | "list_disputes" |
                "get_dispute".
            payment_intent_id/charge_id/refund_id/dispute_id: REQUIRED by the
                matching "get_*" op; on "list_refunds"/"list_disputes",
                `charge_id`/`payment_intent_id` narrow the list.
            customer_id, created_after/created_before, limit, starting_after:
                list filters.

        """
        client = _client()
        window = _window(created_after, created_before)
        if op == "list_charges":
            return _run(lambda: client.list_charges(
                customer=customer_id, payment_intent=payment_intent_id, created=window,
                limit=_limit(limit), starting_after=starting_after))
        if op == "list_intents":
            return _run(lambda: client.list_payment_intents(
                customer=customer_id, created=window, limit=_limit(limit),
                starting_after=starting_after))
        if op == "list_refunds":
            return _run(lambda: client.list_refunds(
                charge=charge_id, payment_intent=payment_intent_id, created=window,
                limit=_limit(limit), starting_after=starting_after))
        if op == "list_disputes":
            return _run(lambda: client.list_disputes(
                charge=charge_id, payment_intent=payment_intent_id, created=window,
                limit=_limit(limit), starting_after=starting_after))
        targets = {"get_intent": (payment_intent_id, "payment_intent_id", client.get_payment_intent),
                   "get_charge": (charge_id, "charge_id", client.get_charge),
                   "get_refund": (refund_id, "refund_id", client.get_refund),
                   "get_dispute": (dispute_id, "dispute_id", client.get_dispute)}
        if op in targets:
            value, name, fn = targets[op]
            if not value:
                raise _bad(f"op={op!r} requiert `{name}`")
            _refuse_ignored(op, "ces filtres ne s'appliquent qu'aux op de liste",
                            customer_id=customer_id, created_after=created_after,
                            created_before=created_before, starting_after=starting_after,
                            limit=limit)
            return _run(lambda: fn(value))
        raise _bad("op inconnu pour stripe_payment")

    # ================================================================
    # Solde, écritures de solde, virements
    # ================================================================

    @mcp.tool()
    def stripe_balance(
        op: Literal["get", "transactions", "transaction", "payouts", "payout"] = "get",
        transaction_id: Optional[str] = None,
        payout_id: Optional[str] = None,
        currency: Optional[str] = None,
        created_after: Optional[int] = None,
        created_before: Optional[int] = None,
        limit: Optional[int] = None,
        starting_after: Optional[str] = None,
    ) -> object:
        """Money actually held and moved — as opposed to money billed.

        "transactions" is the right answer to "how much did we actually MAKE",
        because each row carries `fee` and `net` — invoices only know what was
        billed. To see what was inside a payout, pass its id as `payout_id`
        with op="transactions".

        Creating, cancelling or reversing a payout is not available here.

        Args:
            op: "get" (default, the current balance) | "transactions" |
                "transaction" | "payouts" | "payout".
            transaction_id/payout_id: REQUIRED by "transaction"/"payout".
            currency, created_after/created_before, limit, starting_after:
                filters for "transactions"/"payouts".

        """
        client = _client()
        if op == "get":
            _refuse_ignored(op, "le solde courant ne prend aucun filtre",
                            transaction_id=transaction_id, payout_id=payout_id,
                            currency=currency, created_after=created_after,
                            created_before=created_before, starting_after=starting_after,
                            limit=limit)
            return _run(lambda: client.balance())
        window = _window(created_after, created_before)
        if op == "transactions":
            return _run(lambda: client.list_balance_transactions(
                currency=currency, payout=payout_id, created=window,
                limit=_limit(limit), starting_after=starting_after))
        if op == "payouts":
            return _run(lambda: client.list_payouts(
                created=window, limit=_limit(limit), starting_after=starting_after))
        if op == "transaction":
            if not transaction_id:
                raise _bad("op='transaction' requiert `transaction_id`")
            return _run(lambda: client.get_balance_transaction(transaction_id))
        if op == "payout":
            if not payout_id:
                raise _bad("op='payout' requiert `payout_id`")
            return _run(lambda: client.get_payout(payout_id))
        raise _bad("op inconnu pour stripe_balance")

    # ================================================================
    # Catalogue — produits, prix, réductions
    # ================================================================

    @mcp.tool()
    def stripe_catalog(
        op: Literal["list_products", "get_product", "create_product", "update_product",
                    "list_prices", "get_price", "create_price", "update_price",
                    "list_coupons", "list_promotion_codes"] = "list_products",
        product_id: Optional[str] = None,
        price_id: Optional[str] = None,
        name: Optional[str] = None,
        description: Optional[str] = None,
        tax_code: Optional[str] = None,
        active: Optional[bool] = None,
        unit_amount: Optional[int] = None,
        currency: Optional[str] = None,
        recurring_interval: Optional[Literal["day", "week", "month", "year"]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        code: Optional[str] = None,
        limit: Optional[int] = None,
        starting_after: Optional[str] = None,
    ) -> object:
        """The catalogue: products, prices, coupons, promotion codes.

        ⚠️ **A Stripe price is immutable on its amount.** "Change the price" means
        creating a NEW price and deactivating the old one — `update_price` only
        touches `active`, `metadata` and `nickname`. Say that rather than
        appearing to edit an amount.

        Deleting a product or price is not available here; deactivate instead.

        Args:
            op: "list_products" (default) and the other nine verbs.
            product_id: REQUIRED by "get_product"/"update_product"/"create_price";
                filters "list_prices".
            price_id: REQUIRED by "get_price"/"update_price".
            name: REQUIRED by "create_product". description/metadata: writes.
            tax_code: on "create_product"/"update_product" — e.g. "txcd_10000000"
                (general services). Needed before a payment link can be created
                on accounts eligible for managed payments (verified live).
            active: filter on lists; on "update_product"/"update_price",
                `active=false` retires it from sale without deleting anything.
            unit_amount: "create_price", in cents. currency: "create_price".
            recurring_interval: "create_price" — omit for a one-off price.
            code: "list_promotion_codes" filter.
            limit/starting_after: list pagination.

        """
        client = _client()
        if op == "list_products":
            return _run(lambda: client.list_products(
                active=active, limit=_limit(limit), starting_after=starting_after))
        if op == "list_prices":
            return _run(lambda: client.list_prices(
                product=product_id, active=active, currency=currency,
                limit=_limit(limit), starting_after=starting_after))
        if op == "list_coupons":
            return _run(lambda: client.list_coupons(
                limit=_limit(limit), starting_after=starting_after))
        if op == "list_promotion_codes":
            return _run(lambda: client.list_promotion_codes(
                code=code, active=active, limit=_limit(limit),
                starting_after=starting_after))
        if op == "get_product":
            if not product_id:
                raise _bad("op='get_product' requiert `product_id`")
            return _run(lambda: client.get_product(product_id))
        if op == "get_price":
            if not price_id:
                raise _bad("op='get_price' requiert `price_id`")
            return _run(lambda: client.get_price(price_id))
        if op == "create_product":
            if not name:
                raise _bad("op='create_product' requiert `name`")
            return _run(lambda: client.create_product(
                name=name, description=description, tax_code=tax_code,
                active=active, metadata=metadata))
        if op == "update_product":
            if not product_id:
                raise _bad("op='update_product' requiert `product_id`")
            body = {k: v for k, v in dict(name=name, description=description,
                                          tax_code=tax_code, active=active,
                                          metadata=metadata).items() if v is not None}
            if not body:
                raise _bad("op='update_product' requiert au moins un champ à modifier")
            return _run(lambda: client.update_product(product_id, **body))
        if op == "create_price":
            if not product_id or unit_amount is None or not currency:
                raise _bad("op='create_price' requiert `product_id`, `unit_amount` "
                           "(en centimes) et `currency`")
            recurring = {"interval": recurring_interval} if recurring_interval else None
            return _run(lambda: client.create_price(
                product=product_id, unit_amount=unit_amount, currency=currency,
                recurring=recurring, metadata=metadata))
        if op == "update_price":
            if not price_id:
                raise _bad("op='update_price' requiert `price_id`")
            if unit_amount is not None:
                raise _bad("Le MONTANT d'un prix Stripe est immuable : crée un nouveau "
                           "prix (op='create_price') puis désactive l'ancien "
                           "(op='update_price', active=false).")
            body = {k: v for k, v in dict(active=active,
                                          metadata=metadata).items() if v is not None}
            if not body:
                raise _bad("op='update_price' requiert `active` ou `metadata`")
            return _run(lambda: client.update_price(price_id, **body))
        raise _bad("op inconnu pour stripe_catalog")

    # ================================================================
    # Encaissement hébergé — liens de paiement & sessions Checkout
    # ================================================================

    @mcp.tool()
    def stripe_checkout(
        op: Literal["list_links", "get_link", "link_line_items", "create_link",
                    "update_link", "list_sessions", "get_session",
                    "session_line_items"] = "list_links",
        payment_link_id: Optional[str] = None,
        session_id: Optional[str] = None,
        price_id: Optional[str] = None,
        quantity: Optional[int] = None,
        customer_id: Optional[str] = None,
        status: Optional[Literal["open", "complete", "expired"]] = None,
        active: Optional[bool] = None,
        metadata: Optional[Dict[str, Any]] = None,
        limit: Optional[int] = None,
        starting_after: Optional[str] = None,
    ) -> object:
        """Hosted collection — payment links and Checkout sessions.

        **"create_link" is the safest way to collect money here**: it returns a
        reusable URL to a page hosted by Stripe, so no card number ever touches
        oto, and nobody is charged until a human opens it and pays.

        ⚠️ Creating a link can fail with "the product tax code is missing" on
        accounts eligible for managed payments (verified live). Fix it on the
        product: `stripe_catalog(op="update_product", product_id=…,
        tax_code="txcd_10000000")`.

        Args:
            op: "list_links" (default) | "get_link" | "link_line_items" |
                "create_link" | "update_link" | "list_sessions" | "get_session" |
                "session_line_items".
            payment_link_id: REQUIRED by "get_link"/"link_line_items"/"update_link".
            session_id: REQUIRED by "get_session"/"session_line_items".
            price_id: REQUIRED by "create_link" — from `stripe_catalog`.
            quantity: "create_link" (default 1).
            active: filter on "list_links"; on "update_link", `active=false`
                switches a link off without deleting it.
            customer_id/status: "list_sessions" filters — `status="open"` are
                abandoned checkouts.
            metadata, limit, starting_after: as elsewhere.

        """
        client = _client()
        if op == "list_links":
            return _run(lambda: client.list_payment_links(
                active=active, limit=_limit(limit), starting_after=starting_after))
        if op == "list_sessions":
            return _run(lambda: client.list_checkout_sessions(
                customer=customer_id, status=status, limit=_limit(limit),
                starting_after=starting_after))
        if op == "get_link":
            if not payment_link_id:
                raise _bad("op='get_link' requiert `payment_link_id`")
            return _run(lambda: client.get_payment_link(payment_link_id))
        if op == "link_line_items":
            if not payment_link_id:
                raise _bad("op='link_line_items' requiert `payment_link_id`")
            return _run(lambda: client.get_payment_link_line_items(
                payment_link_id, limit=_limit(limit)))
        if op == "get_session":
            if not session_id:
                raise _bad("op='get_session' requiert `session_id`")
            return _run(lambda: client.get_checkout_session(session_id))
        if op == "session_line_items":
            if not session_id:
                raise _bad("op='session_line_items' requiert `session_id`")
            return _run(lambda: client.get_checkout_session_line_items(
                session_id, limit=_limit(limit)))
        if op == "create_link":
            if not price_id:
                raise _bad("op='create_link' requiert `price_id` — liste-les avec "
                           "stripe_catalog(op='list_prices').")
            items: List[Dict[str, Any]] = [{"price": price_id, "quantity": quantity or 1}]
            return _run(lambda: client.create_payment_link(items, metadata=metadata))
        if op == "update_link":
            if not payment_link_id:
                raise _bad("op='update_link' requiert `payment_link_id`")
            body = {k: v for k, v in dict(active=active,
                                          metadata=metadata).items() if v is not None}
            if not body:
                raise _bad("op='update_link' requiert `active` ou `metadata`")
            return _run(lambda: client.update_payment_link(payment_link_id, **body))
        raise _bad("op inconnu pour stripe_checkout")

    # ================================================================
    # Activité du compte
    # ================================================================

    @mcp.tool()
    def stripe_event(
        op: Literal["list", "get", "webhook_endpoints"] = "list",
        event_id: Optional[str] = None,
        type: Optional[str] = None,
        created_after: Optional[int] = None,
        created_before: Optional[int] = None,
        limit: Optional[int] = None,
        starting_after: Optional[str] = None,
    ) -> object:
        """The account's activity feed — every state change, newest first, with
        the affected object embedded in `data.object`.

        ⚠️ **Stripe keeps events for 30 DAYS only.** Any question about a longer
        period ("this quarter", "last year") will silently come back short here —
        use `stripe_invoice(op="totals")` or the list ops, which read the objects
        themselves and have no such window.

        "webhook_endpoints" lists which integrations are listening, for
        diagnosis. Creating or deleting them is not available here: deleting one
        silently breaks whatever automation depended on it.

        Args:
            op: "list" (default) | "get" | "webhook_endpoints".
            event_id: REQUIRED by "get" (`evt_…`).
            type: "list" filter, wildcards allowed — `invoice.*`,
                `invoice.payment_failed`, `customer.subscription.deleted`.
            created_after/created_before, limit, starting_after: as elsewhere.

        """
        client = _client()
        if op == "list":
            _refuse_ignored(op, "utilise op='get' pour un événement précis", event_id=event_id)
            return _run(lambda: client.list_events(
                type=type, created=_window(created_after, created_before),
                limit=_limit(limit), starting_after=starting_after))
        if op == "get":
            if not event_id:
                raise _bad("op='get' requiert `event_id`")
            _refuse_ignored(op, "ces filtres ne s'appliquent qu'à op='list'",
                            type=type, created_after=created_after,
                            created_before=created_before, starting_after=starting_after)
            return _run(lambda: client.get_event(event_id))
        if op == "webhook_endpoints":
            _refuse_ignored(op, "la liste des endpoints ne prend pas ces filtres",
                            event_id=event_id, type=type, created_after=created_after,
                            created_before=created_before)
            return _run(lambda: client.list_webhook_endpoints(limit=_limit(limit)))
        raise _bad("op doit être 'list', 'get' ou 'webhook_endpoints'")
