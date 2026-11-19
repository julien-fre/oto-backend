"""Les limites d'envoi d'un compte LinkedIn, posées par la personne qui le possède.

LinkedIn plafonne chaque compte (invitations, messages, visites de profil) et Unipile ne
limite rien de son côté : sans borne ici, un agent ou une recette envoie jusqu'à ce que
LinkedIn refuse, ou restreigne le compte. Le titulaire du compte pose donc ses propres
limites, par compte, sur une fenêtre GLISSANTE (24 h, 7 jours) — le modèle des outils de
prospection LinkedIn.

- **Aucune limite par défaut** : sans réglage, rien ne change (des séquences tournent
  déjà). La carte montre des valeurs SUGGÉRÉES, jamais appliquées d'office.
- **Aucun plafond imposé** au réglage (au-delà d'une borne de saisie) : au-dessus du
  maximum que recommande le fournisseur, la carte avertit, elle ne refuse pas.
- **Par compte, pas par ligne** : un compte relié dans plusieurs orgs, ou prêté, a UNE
  limite et UN compte d'actions (`db/schema/unipile.py::SENDING_LIMITS`).
- **Seule une action faite compte** : la place est réservée avant l'appel, atomiquement,
  et rendue si l'appel échoue (`garde`).
- **Fail-open** : une base qui ne répond pas ne bloque pas un envoi — elle se journalise.

Le refus nommé (`linkedin_sending_limit_reached`, rejouable, avec l'heure où une place se
libère) est rendu par `tools/unipile.py`.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterator, Optional

from .db import unipile_limites as store

logger = logging.getLogger(__name__)

JOUR = 24 * 3600
SEMAINE = 7 * JOUR

#: Les actions comptées, par le geste qui les fait (`tools/unipile.py`).
INVITATION = "invitation"
MESSAGE = "message"
VISITE = "profile_visit"


@dataclass(frozen=True)
class Limite:
    action: str
    fenetre: int        # secondes, fenêtre glissante
    suggestion: int     # ce que la carte propose (prudent)
    maxi_fournisseur: int  # au-delà, la carte avertit (recommandation du fournisseur)
    libelle: str


#: L'ordre est celui de la carte.
LIMITES: dict[str, Limite] = {
    "invitations_per_day": Limite(INVITATION, JOUR, 20, 100, "Invitations per day"),
    "invitations_per_week": Limite(INVITATION, SEMAINE, 100, 200, "Invitations per week"),
    "messages_per_day": Limite(MESSAGE, JOUR, 50, 100, "Messages per day"),
    "profile_visits_per_day": Limite(VISITE, JOUR, 80, 100, "Profile visits per day"),
}

#: Borne de SAISIE (une faute de frappe), pas une politique : 0 met l'action en pause.
SAISIE_MAX = 1000


class LimiteAtteinte(Exception):
    """Une fenêtre pleine : rien n'a été envoyé."""

    def __init__(self, lim: str, maxi: int, used: int, resets_at: Optional[datetime]):
        self.lim, self.maxi, self.used, self.resets_at = lim, maxi, used, resets_at
        super().__init__(f"{lim} {used}/{maxi}")

    def secondes_restantes(self) -> int:
        if self.resets_at is None:
            return 60
        reste = (self.resets_at - datetime.now(timezone.utc)).total_seconds()
        return max(1, int(reste) + 1)

    def phrase(self) -> str:
        lib = LIMITES[self.lim].libelle.lower() if self.lim in LIMITES else self.lim
        quand = (f", next slot frees up at {self.resets_at.astimezone(timezone.utc):%H:%M} "
                 "UTC" if self.resets_at else "")
        if self.maxi == 0:
            return (f"this LinkedIn account's owner has paused this action ({lib} = 0). "
                    "Nothing was sent.")
        return (f"this LinkedIn account has reached the limit its owner set: {lib} "
                f"{self.used}/{self.maxi}{quand}. Nothing was sent.")


def compte_du_client(client) -> Optional[str]:
    """L'`account_id` que le client sert — celui que la résolution a retenu (le sien, un
    compte prêté, l'épingle d'un projet)."""
    acct = getattr(client, "_account_id", None)
    return acct if isinstance(acct, str) and acct else None


@contextmanager
def garde(client, action: str) -> Iterator[None]:
    """Réserve l'action sur le compte que sert `client`, lève `LimiteAtteinte` si une de
    ses fenêtres est pleine, rend la place si le corps du `with` échoue."""
    account_id = compte_du_client(client)
    if account_id is None:
        yield
        return
    res: Optional[dict] = None
    try:
        posees = store.limites(account_id)
        fenetres = [(lim, posees[lim], d.fenetre) for lim, d in LIMITES.items()
                    if d.action == action and lim in posees]
        res = store.reserver(account_id, action, store.proprietaire(account_id), fenetres)
    except Exception:  # noqa: BLE001 — fail-open : la borne ne coupe pas l'envoi, elle le dit
        logger.warning("linkedin sending limits: check failed for an account, sending "
                       "without it", exc_info=True)
    if res is None:
        yield
        return
    if "refus" in res:
        r = res["refus"]
        raise LimiteAtteinte(r["lim"], r["max"], r["used"], r["resets_at"])
    try:
        yield
    except BaseException:
        try:
            store.retirer(res["id"])
        except Exception:  # noqa: BLE001 — l'échec d'origine prime ; la place reste comptée
            logger.warning("linkedin sending limits: could not release a reservation",
                           exc_info=True)
        raise


def vue(account_id: str) -> dict:
    """Chaque limite du compte : `max` (None = pas de limite), `used` sur sa fenêtre,
    `resets_at`, et ce que la carte propose."""
    posees = store.limites(account_id)
    out = {}
    for lim, d in LIMITES.items():
        used, libre_a = store.utilise(account_id, d.action, d.fenetre)
        out[lim] = {
            "label": d.libelle,
            "action": d.action,
            "window": "week" if d.fenetre == SEMAINE else "day",
            "max": posees.get(lim),
            "used": used,
            "resets_at": libre_a.isoformat() if libre_a else None,
            "suggested": d.suggestion,
            "provider_max": d.maxi_fournisseur,
        }
    return out


def valider(valeurs: dict) -> dict[str, Optional[int]]:
    """Les valeurs à poser, refusées en nommant la faute (`ValueError`)."""
    propres: dict[str, Optional[int]] = {}
    for lim, n in valeurs.items():
        if lim not in LIMITES:
            raise ValueError(f"unknown limit `{lim}` (known: {', '.join(LIMITES)})")
        if n is None:
            propres[lim] = None
            continue
        if isinstance(n, bool) or not isinstance(n, int) or n < 0 or n > SAISIE_MAX:
            raise ValueError(f"`{lim}` must be a whole number from 0 to {SAISIE_MAX}, "
                             "or null to remove the limit")
        propres[lim] = n
    return propres
