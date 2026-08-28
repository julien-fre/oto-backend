"""Le CONSENTEMENT d'achat — ce que `billing.subscribe` exige avant de vendre (#487).

Souscrire demande deux choses au payeur, et une seule était vérifiée : une identité
de facturation (qui paie, depuis quel pays — #486) **et** l'acceptation des
documents du contexte `purchase` (CGU + CGV + DPA) à leur version courante. Sans
acceptation horodatée, les CGV et le DPA ne sont opposables à personne : ils sont
publiés, pas consentis.

Ce module porte la moitié « consentement » du gate, et le type de refus commun aux
deux moitiés. Il ne connaît ni Mollie ni la TVA : il lit `legal_docs` (source de
vérité des documents et des contextes) et rend soit `None`, soit la description de
ce qui manque.

Pourquoi ailleurs que dans `billing.py` : le cycle d'abonnement (souscrire →
confirmer → résilier → relancer) y tient déjà 600 lignes, et un gate de
consentement n'en fait pas partie — il le précède. Le seul lien est
`_purchase_preconditions`, qui appelle les deux moitiés d'affilée.
"""
from __future__ import annotations

from typing import Optional

from . import legal_docs

# Le contexte de documents qu'un achat exige (`legal_docs.CONTEXTS`).
PURCHASE_CONTEXT = "purchase"


class PurchaseBlocked(ValueError):
    """Les préalables de souscription NON satisfaits — tous nommés d'un coup.

    Un `ValueError`, comme les autres refus d'état du billing : la couche capacité
    continue de le traduire en 409 sans rien connaître de neuf. Ce qu'il ajoute est
    `blockers`, la liste STRUCTURÉE des manques — parce que le tunnel doit AFFICHER
    des documents (libellé, version, adresse), et qu'une phrase française n'est pas
    un contrat sur lequel un front peut se brancher.

    `code` = le premier manque dans l'ordre du tunnel. Les codes historiques restent
    donc exactement ce qu'ils étaient quand un seul préalable manque
    (`billing_identity_required`, `vat_consumer_unsupported`) ; le client qui veut
    tout voir lit `blockers`, et c'est LUI qu'il doit lire."""

    def __init__(self, blockers: list[dict]):
        self.blockers = blockers
        self.code = blockers[0]["code"]
        super().__init__(" ".join(b["message"] for b in blockers))


def legal_blocker(sub: Optional[str]) -> Optional[dict]:
    """Ce qui manque à `sub` pour acheter, ou `None` si rien ne manque.

    La forme rendue est celle d'un blocker : un `code`, un `message` lisible, et
    `documents` — la liste que le tunnel doit présenter, chacun avec son slug, son
    libellé, sa version COURANTE et son URL.

    `sub` absent ⟹ rien n'est accepté ⟹ tout est dû. Le gate se ferme, il ne
    s'ouvre pas."""
    manquants = legal_docs.missing_for_sub(sub, PURCHASE_CONTEXT)
    if not manquants:
        return None
    return {"code": "legal_required", "context": PURCHASE_CONTEXT,
            "documents": manquants, "message": _message(manquants)}


def _message(manquants: list[dict]) -> str:
    """Le refus, écrit pour quelqu'un qui doit LIRE les documents avant de les
    accepter : il les nomme, avec leur version et leur adresse."""
    liste = ", ".join(f"{d['label']} {d['version']} ({d['url']})" for d in manquants)
    perimes = [d for d in manquants if d["accepted_version"]]
    rappel = ""
    if perimes:
        # « Je l'ai déjà coché » est la première objection du payeur quand une
        # version a bougé : dire ce qu'il avait accepté évite de l'envoyer chercher
        # une case qui est bien cochée, mais sur la version d'avant.
        rappel = (" Une version antérieure avait été acceptée ("
                  + ", ".join(f"{d['label']} {d['accepted_version']}" for d in perimes)
                  + "), elle ne vaut pas pour la version courante.")
    return ("legal_required: la souscription demande l'acceptation des documents "
            f"suivants, à leur version courante : {liste}.{rappel} Enregistre-la "
            'avec POST /api/me/legal/accept {"context": "purchase"}, puis relance '
            "la souscription.")
