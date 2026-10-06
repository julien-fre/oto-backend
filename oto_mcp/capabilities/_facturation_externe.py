"""Billing lives elsewhere: the `billing_moved` refusal (#1097).

Since the core split, oto-commerce owns billing and is the ONLY one to set the declared
entitlements (`org_entitlements`) through the service API (`capabilities/service_commerce.py`).
The core no longer writes any. An action that used to sell, grant or declare an entitlement
here can therefore no longer have any effect — letting it answer `ok` would be lying: it
would write a subscription, a contract or a gift that nothing turns into an entitlement.

These actions REFUSE, by name, with a 409 `billing_moved`: a single factory, so the code,
the status and the sentence cannot drift apart from one surface to another.
"""
from __future__ import annotations

from ._types import AuthzDenied, DeclaredError

_OU = ("billing is handled by the billing service, oto-commerce: selling a subscription, "
       "granting an entitlement, setting or ending a contract is done there.")


def refus(geste: str, suite: str = "") -> AuthzDenied:
    """The refusal of a core billing action, saying where it is now done."""
    return AuthzDenied(409, "billing_moved", f"{geste}: {_OU}{suite}")


QUAND = ("Billing is handled by the billing service (oto-commerce): this action is done "
         "there.")


def declaration(quand: str = QUAND) -> DeclaredError:
    """The published declaration of the refusal, for every capability that raises it. By
    default, the sentence shared by all billing actions; a capability that refuses only in
    one case (a catalog key) says which."""
    return DeclaredError(409, "billing_moved", quand)
