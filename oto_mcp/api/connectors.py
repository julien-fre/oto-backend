"""LA route qui reste écrite à la main du domaine connecteurs : le WEBHOOK Unipile.

⚠️ **Tout le reste a migré en capacités le 2026-08-27** — mêmes chemins, mêmes réponses,
entrée ET sortie déclarées :
- le palier PLATEFORME (cran d'activation ADR 0010 B4 + accès plateforme ADR 0044 §H)
  → `capabilities/platform_connectors.py` ;
- la messagerie hébergée côté MEMBRE (`/api/me/unipile*`)
  → `capabilities/unipile_me.py`.

Le nom du fichier est resté (il est le point d'accroche d'`api/routes.py`) ; ce qu'il
porte, non.

**Pourquoi le webhook ne migre pas, et ne migrera pas.** Unipile l'appelle
server-to-server, **sans en-tête d'auth** — or `_rest_adapter` authentifie TOUJOURS : un
anonyme ne peut pas y passer, par construction. Il est classé par NATURE, comme les
callbacks OAuth et les autres webhooks.

Sa sécurité tient à **deux** confrontations, et le 2026-08-29 il n'en faisait qu'une
(#559). Le **nonce** (`name`) : on ne lie que si c'est un jeton VIVANT que nous avons
nous-mêmes posé (non devinable, court, consommé au premier resolve) — il prouve la
session de connexion d'une personne. L'**identifiant de compte** : il arrivait du corps
sans que rien ne le confronte, sur une clé fournisseur **partagée entre les
organisations** — donc n'importe quel siège de l'abonnement était nommable ici. La garde
qui manquait existait déjà sur le chemin jumeau (la réconciliation) ; elle est désormais
**une seule fonction que les deux appellent** (`unipile_connect.account_claimable`, et
l'écriture `unipile_connect.bind_account`).

Il répond **toujours 200**, et toujours le MÊME : un échec ne doit pas faire rejouer
Unipile en boucle, et un corps qui varierait entre succès et refus ferait de cette route
un oracle pour un appelant qu'on n'authentifie pas.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Awaitable, Callable

from fastmcp.server.auth.providers.jwt import JWTVerifier
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from .. import db, org_store

logger = logging.getLogger(__name__)


AuthFn = Callable[..., Awaitable[tuple[str | None, JSONResponse | None]]]


def make_routes(
    verifier: JWTVerifier,
    authenticate: AuthFn,
    json_response: Callable[..., JSONResponse],
    json_error: Callable[..., JSONResponse],
    options_handler: Callable[[Request], Awaitable[Response]],
) -> list[Route]:

    def _bind_from_webhook(body: dict) -> None:
        """Le corps SYNCHRONE de la notification — base + garde, hors event loop.

        Deux valeurs sont à confronter, et une seule l'était. Le **nonce** (`name`)
        prouve « c'est bien la session de connexion de cette personne » : 192 bits,
        TTL 1 h, consommé au premier `resolve`. Il ne dit rien de l'**identifiant de
        compte**, que ce handler reprenait tel quel — or la clé fournisseur est
        PARTAGÉE entre les organisations, donc n'importe quel siège de l'abonnement
        était nommable ici (#559). La confrontation vit dans
        `unipile_connect.account_claimable`, appelée AUSSI par la réconciliation :
        une seule garde, deux chemins.

        Format réel confirmé (instrumenté 2026-06-18) :
        `{status:"CREATION_SUCCESS", account_id, name:<nonce>, account_type}`. On ne
        lie QUE sur un succès de création — un événement d'échec ne doit pas mapper
        un `account_id`.
        """
        from .. import unipile_connect

        status = body.get("status")
        name = body.get("name")
        account_id = (body.get("account_id") or body.get("accountId")
                      or body.get("id"))
        # Un corps anonyme ne choisit pas non plus le TYPE de nos paramètres : un
        # objet ou une liste à la place d'une chaîne partirait en paramètre SQL.
        name = name if isinstance(name, str) else None
        account_id = account_id if isinstance(account_id, str) else None
        if status == "CREATION_SUCCESS" and name and account_id:
            pend = db.resolve_unipile_pending(name)
            if pend:
                # Filet : un pending émis AVANT le deploy B4 (BYO) porte org_id NULL
                # → org maison du sub (le binding doit toujours avoir une org).
                org_id = pend.get("org_id") or org_store.get_active_org(pend["sub"])
                issue = unipile_connect.bind_account(
                    pend["sub"], account_id, org_id=org_id,
                    provider=pend.get("provider", "LINKEDIN"),
                    platform_seat=bool(pend.get("platform_seat")))
                if issue.bound:
                    logger.info("unipile webhook: bound sub=%s account_id=%s org=%s",
                                pend["sub"], account_id, org_id)
                else:
                    # Un refus MUET est un refus que personne ne saura avoir eu : la
                    # réponse ne dit rien (elle ne peut pas), donc le journal doit
                    # tout dire. C'est le seul endroit où cette tentative existe.
                    logger.warning(
                        "unipile webhook: liaison REFUSÉE (%s) sub=%s org=%s "
                        "account_id=%s", issue.reason, pend["sub"], org_id, account_id)
            else:
                # Le nonce lui-même ne se journalise PAS : c'est le seul secret de ce
                # chemin, et l'appelant est anonyme.
                logger.warning("unipile webhook: nonce inconnu/expiré")
        elif status and status != "CREATION_SUCCESS":
            logger.info("unipile webhook: statut ignoré status=%s", status)

    async def unipile_webhook(request: Request) -> JSONResponse:
        """Notification Unipile au succès du hosted-auth (B3). **NON authentifié**
        (Unipile l'appelle, server-to-server).

        **Toujours 200, et toujours le MÊME 200** : un ack, parce qu'un webhook
        rejoué en boucle est pire qu'un refus ; et un corps indiscernable entre
        succès et refus, parce que répondre autre chose ferait de cette route un
        oracle (« cet identifiant est-il déjà pris ? » se lirait à la réponse) pour
        un appelant qu'on n'authentifie pas. Ce qui se passe vraiment est dans le
        journal, jamais dans la réponse.
        """
        raw = await request.body()
        try:
            body = json.loads(raw) if raw else {}
        # noqa: SILENT — ACK délibéré : un webhook rejoué en boucle est pire (compteur à poser, #424)
        except Exception:
            return JSONResponse({"ok": True})
        if not isinstance(body, dict):
            return JSONResponse({"ok": True})   # `[]`, `"x"`, `3` : pas un événement
        # Base synchrone hors de la boucle : le serveur est MONO-LOOP et cette route
        # est anonyme — un aller-retour SQL tenu dans la boucle s'y amplifie.
        await run_in_threadpool(_bind_from_webhook, body)
        return JSONResponse({"ok": True})




    return [
        # Les sièges de la clé plateforme unipile (inventaire + libération) sont des
        # CAPACITÉS depuis le 15/08 (`capabilities/unipile_seats.py`) : mêmes chemins,
        # dérivés — plus de route écrite ici.
        Route("/api/unipile/webhook", unipile_webhook, methods=["POST"]),
    ]
