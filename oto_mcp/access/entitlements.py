"""Le point d'accès du cœur aux droits déclarés d'une org (ADR 0070 §7).

Le cœur demande « cette org a-t-elle ce droit ? » et ne sait rien de plus : ni qui
l'a posé, ni s'il est payé. Il n'importe pas `billing` — le commerce est un
producteur de droits parmi d'autres, qui écrit dans `org_entitlements`.
"""
from __future__ import annotations

from ..db import entitlements as db_entitlements


def org_has(org_id: int, right_key: str) -> bool:
    """Vrai si une ligne VIVANTE, de n'importe quelle source, accorde ce droit."""
    return db_entitlements.has(org_id, right_key)
