"""Les gestes SENSIBLES demandent une session de connexion complète — jamais un jeton —
quand l'INSTANCE le déclare (`OTO_SENSITIVE_ACTIONS_REQUIRE_SESSION=1`).

CASA 2.4.1 (guide de test ADA) : une modification de compte ou une transaction sensible
exige une session de connexion complète ou une re-vérification du compte. Un jeton `oto_`
(jusqu'à 90 jours, celui d'une personne, d'un agent hébergé ou une clé d'org) n'est ni
l'un ni l'autre : il se copie, il fuit, et rien ne redemande qui le porte. Sous le
réglage, il ne fait donc pas ces gestes-là — archiver une org, retirer un membre ou
changer un rôle, donner le rang d'admin (invitation, ajout), transférer la propriété d'un
objet, régler la sécurité d'une org, les gestes de paiement.

**Réglage d'instance, éteint par défaut** : ce refus change le produit pour tous les
intégrateurs (un script qui gère ses membres par jeton cesse de marcher), il ne vit donc
pas en dur dans le tronc. Non posé (ou `0`) : le tronc est inchangé, routes et handlers
acceptent le jeton comme avant. `1` : les deux crans ci-dessous mordent. Lu à CHAQUE
appel (`actif`), jamais figé à l'import ; une valeur illisible refuse le démarrage
(`verifier_reglage`) et vaut « posé » si elle arrivait jusqu'ici — une faute de frappe ne
rouvre pas un geste qu'on a voulu fermer. Les routes déjà réservées à la session avant ce
réglage (gestion des jetons et des clés d'org, agent intégré) le restent, réglage ou non :
elles portent `allow_api_token=False`, pas `sensitive`.

Deux crans, parce qu'il y a deux faces et des gestes conditionnels :

- **le binding REST** (`RestBinding.sensitive=True`, lu par `_rest_adapter` à chaque
  requête) : sous le réglage, la route entière refuse le porteur de jeton à
  l'authentification, avant toute lecture — 403 `api_token_forbidden`.
  Il refuse aussi le secret de worker (`otow_`). L'identité de SERVICE, elle, n'est pas
  un jeton `oto_` et n'atteint aucune de ces routes (aucune règle ne la lit) ;
- **cette garde**, appelée par le HANDLER de domaine : elle tient la face MCP — qui
  accepte un jeton `oto_` non porté (`server._verify_api_token`) et sert les mêmes
  handlers par les consoles `oto_org`, `oto_admin_org_member`, `oto_group`,
  `oto_resource` — et les gestes qu'un binding ne sait pas dire (une invitation n'est
  sensible que si elle donne le rang d'admin).

⚠️ **Elle ne refuse que sur un jeton IDENTIFIÉ**, jamais sur « pas de session connue » :
un appel interne (boucle de fond, maintenance, banc) n'a ni geste REST ni jeton MCP, et
doit passer — contrairement à la garde de l'agent intégré (`copilot`), fail-closed parce
que sa seule face est REST. Une session OAuth Logto (dashboard, client MCP connecté par
OAuth) n'est pas un jeton nommé : elle passe.
"""
from __future__ import annotations

import os

from .. import geste
from ..auth import hooks, platform_worker, token_scopes
from ._types import AuthzDenied

#: Le même code que le refus du binding REST : un client lit UN jeton, quelle que soit
#: la face qui a refusé.
CODE = "api_token_forbidden"

#: Le réglage d'instance qui arme les deux crans.
ENV = "OTO_SENSITIVE_ACTIONS_REQUIRE_SESSION"
_OUI = frozenset({"1", "on", "true", "yes"})
_NON = frozenset({"", "0", "off", "false", "no"})


def _brut() -> str:
    return (os.environ.get("OTO_SENSITIVE_ACTIONS_REQUIRE_SESSION") or "").strip().lower()


def actif() -> bool:
    """Le réglage, lu à chaque appel : vrai quand l'instance ferme les gestes sensibles
    au jeton. Une valeur inconnue vaut « posé » (le démarrage l'a déjà refusée)."""
    return _brut() not in _NON


def verifier_reglage() -> None:
    """La garde de démarrage : une valeur illisible refuse de démarrer, en la nommant."""
    brut = _brut()
    if brut not in _OUI and brut not in _NON:
        raise RuntimeError(
            f"{ENV}={brut!r} illisible : `1` (gestes sensibles réservés à une session) "
            "ou `0` / non posé (comportement du tronc).")


def _jeton_mcp() -> bool:
    """La requête MCP en cours porte-t-elle un jeton `oto_` ? `server._verify_api_token`
    pose `client_id="oto_api_token"` et les axes `token_id`/`token_kind` ; une session
    OAuth n'a ni l'un ni les autres. Hors requête MCP : False."""
    if hooks.current_token_axes():
        return True
    try:
        from fastmcp.server.dependencies import get_access_token  # type: ignore
        jeton = get_access_token()
    # noqa: SILENT — hors contexte de requête MCP : aucun jeton à juger
    except Exception:  # noqa: BLE001
        return False
    return getattr(jeton, "client_id", None) == "oto_api_token"


def porteur_de_jeton() -> bool:
    """Vrai quand l'appel en cours est authentifié par un jeton plutôt que par une
    session : REST sous un jeton `oto_` (geste `api`, ou l'id de jeton que pose
    `api.base._authenticate`) ou un secret de worker, MCP sous un jeton `oto_`."""
    g = geste.courant()
    if g is not None and g.source == geste.API:
        return True
    # Second signal REST, indépendant du geste : l'authentification range l'id de tout
    # jeton `oto_` (porté ou non) — un `token_kind` absent ne rendrait pas la garde aveugle.
    if token_scopes.jeton_courant() is not None:
        return True
    if platform_worker.current() is not None:
        return True
    return _jeton_mcp()


def exiger_session(quoi: str) -> None:
    """Refuse `quoi` (le geste, en clair) sous un jeton : 403 `api_token_forbidden` —
    seulement quand l'instance le déclare (`actif`) ; sinon ne fait rien."""
    if not actif():
        return
    if porteur_de_jeton():
        # Code EN LITTÉRAL (= `CODE`) : `tests/_refus_atteignables.py` lit les littéraux.
        raise AuthzDenied(
            403, "api_token_forbidden",
            f"{quoi} demande une session de connexion complète (le tableau de bord, ou un "
            "client connecté par OAuth) : un jeton d'API ne fait pas ce geste.")
