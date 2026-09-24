"""Le client de l'agent de la FERME — la box où vivent les sandboxes d'abonnement.

Un sandbox = un espace de la box (`otomata-tech/claude-sandbox-manager`) où une personne a connecté
elle-même son abonnement au programme officiel du fournisseur. L'agent de la box le
crée, y ouvre la connexion, le détruit ; le backend ne fait que le lui demander.

⚠️ **Rien de ce qui passe ici n'est un identifiant d'abonnement.** La connexion rend
une URL du fournisseur, que la personne ouvre dans SON navigateur ; le code qu'elle
colle en retour est à usage unique et ne vaut rien hors du programme qui détient le
vérifieur, dans le sandbox. La session, elle, ne sort jamais du sandbox.

L'agent n'écoute que sur le réseau privé du parc (`OTO_FERME_URL`), derrière un jeton
partagé (`OTO_FERME_TOKEN`, 1Password « Ferme de Claude — jeton de l agent »).
"""
from __future__ import annotations

import hashlib
import os
from typing import Optional

import requests

_ENV_URL = "OTO_FERME_URL"
_ENV_JETON = "OTO_FERME_TOKEN"
_DELAI_S = 90   # `claude auth login` rend son URL en quelques secondes ; l'échange du code, pareil


class FermeIndisponible(RuntimeError):
    """L'agent de la ferme n'a pas répondu, ou pas comme convenu. `statut` = le code
    HTTP qu'il a rendu (409 : aucune connexion en attente, 422 : connexion refusée),
    `None` s'il n'a pas répondu."""

    def __init__(self, message: str, statut: Optional[int] = None):
        super().__init__(message)
        self.statut = statut


def sandbox_de(sub: str) -> str:
    """Le sandbox d'une personne : un nom stable, qui ne dit pas qui elle est."""
    return "u" + hashlib.sha256(sub.encode()).hexdigest()[:20]


def _appel(methode: str, chemin: str, corps: Optional[dict] = None, *,
           absent_ok: bool = False) -> dict:
    url, jeton = os.environ.get(_ENV_URL), os.environ.get(_ENV_JETON)
    if not url or not jeton:
        raise FermeIndisponible(f"{_ENV_URL} / {_ENV_JETON} absents : la ferme n'est pas configurée")
    try:
        r = requests.request(methode, f"{url.rstrip('/')}{chemin}", json=corps,
                             headers={"Authorization": f"Bearer {jeton}"}, timeout=_DELAI_S)
    except requests.RequestException as e:
        raise FermeIndisponible(f"agent de la ferme injoignable : {e}") from e
    if absent_ok and r.status_code == 404:
        return {}
    if r.status_code != 200:
        raise FermeIndisponible(f"agent de la ferme : {r.status_code} {r.text[:300]}",
                                r.status_code)
    return r.json()


def creer(sandbox: str) -> None:
    _appel("PUT", f"/api/sandboxes/{sandbox}")


def demarrer_connexion(sandbox: str, email: Optional[str] = None) -> str:
    """Rend l'URL du fournisseur où la personne se connecte."""
    return _appel("POST", f"/api/sandboxes/{sandbox}/login", {"email": email} if email else {})["url"]


def transmettre_code(sandbox: str, code: str) -> dict:
    """Rend l'état du sandbox après l'échange (`loggedIn`, `subscriptionType`, …)."""
    return _appel("PUT", f"/api/sandboxes/{sandbox}/login/code", {"code": code})


def detruire(sandbox: str) -> None:
    """Déconnecte la session du fournisseur, puis efface le sandbox et tout ce qu'il contient."""
    _appel("DELETE", f"/api/sandboxes/{sandbox}", absent_ok=True)   # déjà absent = déjà détruit
