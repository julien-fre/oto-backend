"""La route qu'un TIERS appelle pour déclencher un agent — `POST /api/hooks/{id}`.

Écrite à la main, hors de la couche capacité, et ce n'est pas un choix de style :
**l'adaptateur REST refuse tout champ d'entrée qu'une capacité ne déclare pas**
(400 `unknown_fields`). Un corps JSON LIBRE — la demande même de cette route — ne
peut donc pas y passer. Le précédent existe et porte la même marque : le webhook
Mollie (`api/billing.py`), non authentifié par JWT, monté à la main.

## Ce que la route fait, et ce qu'elle ne fait pas

Elle **adapte** : elle lit l'en-tête, le corps et l'agent appelant, puis appelle
`runner_hook.declencher`. Toute la décision — secret, lissage, façonnage de la
charge, enfilage — vit là-bas, où elle se teste sans HTTP.

⚠️ **Tout le travail passe par `run_in_threadpool`.** Le serveur est mono-loop et
psycopg est synchrone : une requête base faite dans la boucle bloque TOUTES les
autres requêtes du process. Une rafale de webhooks ressemblerait alors à une panne
de plateforme (`docs/event-loop-perf.md`, et le même geste dans le webhook Mollie).

⚠️ **Aucun 5xx pour une raison métier.** Un envoyeur qui reçoit un 500 retente, et
retente encore : c'est ainsi qu'une erreur de configuration devient une tempête.
Chaque refus prévu a son code, et il est définitif du point de vue de l'envoyeur.
"""
from __future__ import annotations

import json
import logging

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from .. import runner_hook

logger = logging.getLogger(__name__)

#: Le corps accepté. Lu AVANT d'être parsé : un mégaoctet de JSON hostile ne doit
#: pas être désérialisé pour être refusé.
_CORPS_MAX = runner_hook.CORPS_MAX


async def _lire_borne(request: Request) -> bytes | None:
    """Le corps, ou None dès qu'il dépasse le plafond — sans lire la suite."""
    declare = request.headers.get("content-length")
    if declare and declare.isdigit() and int(declare) > _CORPS_MAX:
        return None
    morceaux, total = [], 0
    async for morceau in request.stream():
        total += len(morceau)
        if total > _CORPS_MAX:
            return None
        morceaux.append(morceau)
    return b"".join(morceaux)


def _refus(statut: int, code: str, message: str, **extra) -> JSONResponse:
    return JSONResponse({"error": code, "detail": message, **extra},
                        status_code=statut)


async def fire(request: Request) -> JSONResponse:
    """Un tiers POSTe, un agent part.

    Réponses :
      202 le travail est enfilé (`delayed_seconds` s'il a été lissé)
      400 le corps n'est pas du JSON
      404 identifiant inconnu, OU secret faux — délibérément indistinguables
      409 l'agent est en pause
      413 le corps dépasse le plafond
      429 la file dépasse déjà sa fraîcheur (avec `Retry-After`)
    """
    try:
        trigger_id = int(request.path_params["trigger_id"])
    except (KeyError, TypeError, ValueError):
        return _refus(404, "hook_not_found", "déclencheur inconnu")
    secret = runner_hook.secret_du_porteur(request.headers.get("authorization"))

    # ⚠️ La TAILLE avant le PARSE, et en FLUX : `request.body()` bufferise tout
    # avant de rendre la main, donc un corps de cent mégaoctets serait entièrement
    # en mémoire au moment où on le refuse — sur une route qu'un inconnu peut
    # appeler sans credential. On lit morceau par morceau et on s'arrête au
    # premier octet de trop ; le reste n'est jamais lu.
    brut = await _lire_borne(request)
    if brut is None:
        # Le propriétaire est le seul à pouvoir réparer une source trop bavarde :
        # la trace part, hors boucle comme tout le reste.
        await run_in_threadpool(runner_hook.noter_corps_trop_gros, trigger_id,
                                secret, (request.headers.get("user-agent") or "")[:200])
        return _refus(413, "payload_too_large",
                      f"corps au-delà du plafond de {_CORPS_MAX} octets. Passe "
                      "une RÉFÉRENCE (un identifiant que l'agent rechargera), pas "
                      "l'enregistrement entier.")

    corps = None
    if brut.strip():
        try:
            corps = json.loads(brut)
        except ValueError:
            # ⚠️ Refus NOMMÉ plutôt qu'un corps ignoré : une source qui envoie du
            # formulaire là où on attend du JSON verrait sinon ses agents tourner
            # sans jamais recevoir sa donnée, et rien ne le dirait.
            return _refus(400, "invalid_json",
                          "le corps n'est pas du JSON. Envoie un objet JSON, ou "
                          "rien du tout si l'agent n'en a pas besoin.")

    try:
        rendu = await run_in_threadpool(
            runner_hook.declencher, trigger_id, secret, corps,
            (request.headers.get("user-agent") or "")[:200])
    except runner_hook.HookRefus as refus:
        entetes = ({"Retry-After": str(refus.retry_after)}
                   if refus.retry_after else None)
        r = _refus(refus.statut, refus.code, refus.message)
        if entetes:
            r.headers.update(entetes)
        return r
    except Exception:  # noqa: BLE001 — voir ci-dessous
        # ⚠️ Le SEUL 500 de cette route, et il dit une panne de NOTRE côté — base
        # injoignable, bogue. L'envoyeur DOIT le voir comme réessayable : c'est le
        # seul cas où sa retentative est la bonne conduite. Journalisé entier,
        # jamais avalé (`lint_silences`).
        logger.exception("webhook %s : déclenchement impossible", trigger_id)
        return _refus(500, "hook_failed",
                      "le déclenchement a échoué de notre côté. Réessaie : "
                      "aucun travail n'a été enfilé.")

    return JSONResponse(rendu, status_code=202)


def make_routes(options_handler) -> list[Route]:
    """La route du webhook. `options_handler` sert le pré-vol CORS, comme partout
    ailleurs — une source appelée depuis un navigateur existe (un formulaire, un
    outil no-code hébergé)."""
    return [
        Route("/api/hooks/{trigger_id}", fire, methods=["POST"]),
        Route("/api/hooks/{trigger_id}", options_handler, methods=["OPTIONS"]),
    ]
