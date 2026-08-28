"""`ContextNetMiddleware` — le filet : le contexte de l'org arrive même sans être demandé."""
from __future__ import annotations

import logging
import os
import time

from fastmcp.server.middleware import Middleware
from starlette.concurrency import run_in_threadpool

from ..auth.hooks import current_user_sub_from_token

logger = logging.getLogger(__name__)

# L'outil qui charge le contexte volontairement : l'appeler VAUT livraison.
_CONTEXT_TOOL = "oto_context"

# Fenêtre de non-répétition, par (sub, org) et par PROCESS. Une session Claude
# rehandshake par conversation ; on ne sait pas distinguer deux conversations d'un
# même compte, donc on borne par le temps plutôt que de deviner. Trop court = on
# repaie le bloc ; trop long = un agent d'une conversation suivante ne l'a pas.
# 30 min : plus court qu'une session de travail, plus long qu'une rafale d'appels.
_TTL_S = int(os.environ.get("OTO_CONTEXT_NET_TTL", "1800"))

# Cran d'arrêt GLOBAL. Ce middleware retouche le RENDU d'un résultat d'outil et compose
# du DB à chaud : il doit pouvoir être coupé sans rollback ni redéploiement de code.
def _armed() -> bool:
    return os.environ.get("OTO_CONTEXT_NET", "1") != "0"


def _orgs_exclues() -> set:
    """Les orgs qui ne reçoivent PAS le filet (ids séparés par des virgules).

    Un cran tout-ou-rien oblige à choisir entre la mesure d'une org et le garde-fou de
    toutes les autres. Demandé le 2026-08-28 par une campagne dont **chaque fiche est
    une session neuve** : le filet s'y serait ajouté au premier résultat de chaque
    fiche, pas une fois par campagne, et ces agents-là recopient le canal texte au lieu
    de le lire (le défaut qui leur a coûté une vague le 27/08).

    ⚠️ Exclure une org, c'est lui retirer ses garde-fous : à réserver à une fenêtre de
    mesure, décidée par qui exploite l'org, et à retirer après. Ce n'est pas un réglage
    de confort."""
    brut = os.environ.get("OTO_CONTEXT_NET_EXCLUDE_ORGS", "")
    out = set()
    for part in brut.replace(" ", "").split(","):
        if part.isdigit():
            out.add(int(part))
    return out


_servi: dict[tuple, float] = {}


def _deja_servi(cle: tuple) -> bool:
    t = _servi.get(cle)
    return t is not None and (time.monotonic() - t) < _TTL_S


def _marquer(cle: tuple) -> None:
    _servi[cle] = time.monotonic()
    # Purge opportuniste : ce cache ne doit pas devenir une fuite sur un serveur qui
    # voit passer beaucoup de comptes.
    if len(_servi) > 5000:
        seuil = time.monotonic() - _TTL_S
        for k in [k for k, v in _servi.items() if v < seuil]:
            _servi.pop(k, None)


def _compose(sub: str):
    """Le bloc de contexte de la session — le MÊME que rend `oto_context`.

    **Sync (DB), et LOURD** : traverse `access.status_for`, soit la cascade de statut
    de tous les connecteurs. À appeler via `run_in_threadpool`, JAMAIS dans la boucle
    — c'est la composition qui a gelé l'event loop le 15/08 (cf.
    `docs/event-loop-perf.md`, mode de gel n°2). D'où aussi la fenêtre de
    non-répétition : une fois par (sub, org), pas à chaque appel d'outil.
    """
    from .. import access, instructions
    org_id = access.current_org(sub)
    if org_id in _orgs_exclues():
        # Sortie AVANT la composition : l'org exclue ne paie même pas la lecture.
        return org_id, ""
    return org_id, instructions._block_c(sub, org_id)


class ContextNetMiddleware(Middleware):
    """Livre le contexte de l'org dans la PREMIÈRE réponse d'outil d'une session qui ne
    l'a pas chargé.

    **Pourquoi un filet.** Le contexte est censé arriver au handshake, dans le champ
    `instructions` de l'`initialize`. Deux clients mesurés, deux façons de ne pas le
    délivrer : Claude Code le tronque à 2048 caractères — soit, chez nous, avant même
    la fin du premier bloc — et sur claude.ai le modèle n'en voit rien
    (oto-backend#478). Le canal n'est pas fiable, et il ne le sera pas davantage
    demain : son sort dépend d'un client qu'on ne contrôle pas.

    **Ce que ça coûte de ne rien faire.** Ce bloc ne porte pas que des conseils
    d'usage : il porte les GARDE-FOUS de l'org — « propose seulement, fais valider
    avant tout envoi vers l'extérieur ». Tant qu'il dépend d'un appel volontaire à
    `oto_context`, un agent qui va droit à `email_send` ne l'a jamais lu. Un garde-fou
    que son destinataire peut ignorer sans le savoir n'en est pas un.

    **Ce qu'il fait.** Au premier appel d'outil d'un `sub` qui n'a pas déjà reçu le
    bloc, il l'ajoute au canal TEXTE du résultat, dans une balise qui le sépare
    franchement de la donnée de l'outil. Appeler `oto_context` vaut livraison : le
    filet ne double jamais un chargement volontaire.

    ⚠️ **Ajoute, ne remplace pas.** Le résultat de l'outil part intact — contenu et
    canal structuré. Un agent qui parse la structure ne voit aucune différence.

    ⚠️ **Doit être enregistré au-dessus de la rédaction et de l'écho de compte**, qui
    réémettent le payload : tourner sous eux ferait disparaître le bloc ajouté. Même
    raison que pour `EmptyResultMiddleware`, et sous `ToolAlias` pour lire le nom
    canonique de l'outil.

    Fail-open partout : pas de `sub`, pas d'org, composition en échec ou cran coupé →
    le résultat part inchangé. Le filet ne fait jamais échouer un appel.
    """

    async def on_call_tool(self, context, call_next):
        result = await call_next(context)
        if not _armed():
            return result
        try:
            sub = current_user_sub_from_token()
        except Exception:  # noqa: BLE001 — pas d'identité ⇒ pas de contexte à livrer
            logger.debug("filet de contexte : identité illisible", exc_info=True)
            return result
        if not sub:
            return result
        nom = getattr(context.message, "name", "") or ""
        try:
            if nom == _CONTEXT_TOOL:
                # Chargement VOLONTAIRE : il vaut livraison, quel que soit son org.
                from .. import access
                _marquer((sub, access.current_org(sub)))
                return result
            if getattr(result, "is_error", False):
                # Un appel en échec n'est pas le bon moment : le modèle lit une erreur,
                # et on lui empilerait dessus un bloc d'instructions sans rapport.
                return result
            org_id, bloc = await run_in_threadpool(_compose, sub)
            cle = (sub, org_id)
            if not bloc or _deja_servi(cle):
                return result
            _marquer(cle)
            return _avec_contexte(result, bloc)
        except Exception:  # noqa: BLE001 — le filet ne fait jamais échouer un appel
            logger.warning("filet de contexte échoué pour sub=%s (fail-open)", sub,
                           exc_info=True)
            return result


def _avec_contexte(result, bloc: str):
    """Réémet le résultat avec le bloc de contexte AJOUTÉ au canal texte.

    Balisé, et annoncé pour ce qu'il est : ces lignes ne sont pas le résultat de
    l'outil appelé. Sans la balise, le modèle lit la prose comme une sortie d'outil et
    la recopie — le même défaut de décodage qu'un résultat vide servi en structure nue
    (otomata-tech/oto#32)."""
    from fastmcp.tools.tool import ToolResult
    from mcp.types import TextContent
    entete = ("<oto-contexte-organisation>\n"
              "⚠️ Ceci n'est PAS le résultat de l'outil que tu viens d'appeler : ce sont "
              "les règles de travail de l'organisation, livrées ici parce que ton client "
              "ne les a pas reçues à la connexion. Elles s'appliquent à partir de "
              "maintenant. Recharge-les avec `oto_context` après un changement d'org, "
              "d'équipe ou de projet.\n\n")
    texte = TextContent(type="text", text=f"{entete}{bloc}\n</oto-contexte-organisation>")
    return ToolResult(
        content=list(getattr(result, "content", None) or []) + [texte],
        structured_content=getattr(result, "structured_content", None),
        meta=getattr(result, "meta", None),
        is_error=False,
    )
