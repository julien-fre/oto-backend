"""Déclencher une routine Claude Code par son endpoint `/fire`.

Une **routine** est un agent autonome hébergé par Anthropic : un prompt figé, des
connecteurs MCP, et un ou plusieurs déclencheurs (planifié, API, événement GitHub).
oto ne fait pas tourner la boucle — il la déclenche, et l'agent revient sur `/mcp`
avec les outils de son compte. C'est le contraire d'un runner à héberger : le plan
de contrôle, l'exécution et la supervision (chaque run est une session consultable)
sont fournis.

Trois propriétés de la plateforme qu'on ne réimplémente PAS ici, et qu'il faut avoir
en tête pour ne pas les défaire :

1. **Le jeton est scopé à UNE routine.** Il ne vaut pas une clé d'API : sa fuite ne
   permet que de déclencher cette routine-là. D'où « une instance = une routine »
   côté registre — un jeton unique vers une routine à tout faire annulerait le cran.
2. **`text` arrive à l'agent enveloppé dans un bloc `<routine-fire-payload>` étiqueté
   DONNÉE NON FIABLE**, et le prompt de la routine doit explicitement opter pour
   l'exploiter. C'est la séparation instruction/donnée que réclame tout déclenchement
   par un tiers — donc on passe une RÉFÉRENCE (« la ligne 42 du tableau leads »), pas
   l'enregistrement : l'agent relit la donnée fraîche par le MCP.
3. **L'appel ne bloque pas** : il crée la session et rend son id. Le résultat se lit
   dans la session, pas dans cette réponse.
"""
from __future__ import annotations

import logging
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_FIRE_URL = "https://api.anthropic.com/v1/claude_code/routines/{routine_id}/fire"

# Version datée de la beta. Anthropic garantit que les deux versions précédentes
# continuent de fonctionner — d'où une constante nommée plutôt qu'un littéral perdu
# dans les headers : le jour de la migration, c'est la seule ligne à bouger.
_BETA = "experimental-cc-routine-2026-04-01"

# (connexion, lecture). L'appel ne fait que CRÉER la session — il ne l'attend pas —
# donc une lecture courte suffit et un amont muet ne doit pas immobiliser un worker.
_TIMEOUT = (10, 30)


class RoutineFireError(RuntimeError):
    """Échec du déclenchement. Le message est SÛR à afficher : jamais le jeton,
    jamais l'URL (qui porte l'id de routine)."""

    def __init__(self, message: str, *, status: Optional[int] = None,
                 retryable: bool = False):
        super().__init__(message)
        self.status = status
        self.retryable = retryable


def fire(routine_id: str, token: str, text: Optional[str] = None) -> dict:
    """Déclenche la routine et rend `{session_id, session_url}`.

    `text` = le contexte du run, libre. Il n'est PAS parsé : envoyer du JSON le fait
    arriver comme une chaîne littérale. Préférer une référence courte que l'agent
    saura recharger par le MCP.
    """
    routine_id = (routine_id or "").strip()
    if not routine_id:
        raise RoutineFireError("`routine_id` manquant dans le credential de la routine")
    if not (token or "").strip():
        raise RoutineFireError("jeton de déclenchement manquant dans le credential")

    body: dict = {}
    if text is not None and str(text).strip():
        body["text"] = str(text)

    try:
        r = requests.post(
            _FIRE_URL.format(routine_id=routine_id),
            headers={
                "Authorization": f"Bearer {token}",
                "anthropic-beta": _BETA,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=_TIMEOUT,
        )
    except requests.RequestException as e:
        # Pas de `str(e)` nu : le message de requests porte l'URL, donc l'id de
        # routine, qui finirait dans le contexte de l'agent et dans Sentry.
        raise RoutineFireError(
            f"routine injoignable ({type(e).__name__}) — réessaie dans un instant",
            retryable=True) from None

    if r.status_code == 401:
        raise RoutineFireError(
            "jeton de déclenchement refusé — il a été régénéré ou révoqué côté "
            "claude.ai ; regénère-le sur la routine et repose le credential",
            status=401)
    if r.status_code == 404:
        raise RoutineFireError(
            "routine introuvable — l'`routine_id` du credential ne correspond à "
            "aucune routine de ce compte (elle a pu être supprimée)", status=404)
    if r.status_code == 429:
        raise RoutineFireError(
            "plafond de déclenchements atteint sur ce compte claude.ai — les "
            "routines ont un quota quotidien de runs, distinct de l'usage",
            status=429, retryable=True)
    if r.status_code >= 400:
        raise RoutineFireError(
            f"déclenchement refusé (HTTP {r.status_code}) : {_detail(r)}",
            status=r.status_code, retryable=r.status_code >= 500)

    data = r.json() if r.content else {}
    return {
        "session_id": data.get("claude_code_session_id"),
        "session_url": data.get("claude_code_session_url"),
        "fired": True,
    }


def _detail(r: requests.Response) -> str:
    """Le dire du serveur, borné. Une erreur d'API porte souvent le vrai motif dans
    le corps ; le jeter obligerait à deviner."""
    try:
        body = r.json()
        msg = (body.get("error") or {}).get("message") or body.get("message")
        if msg:
            return str(msg)[:300]
    except ValueError:
        pass
    return (r.text or "")[:300]
