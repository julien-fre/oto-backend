"""L'empreinte du CLIENT d'une requête — son IP réelle et son user-agent.

Deux faits de TRANSPORT dont un handler de capacité a parfois besoin, alors qu'il
ne voit jamais la requête (ADR 0004, sens unique : le core n'importe pas
l'adaptateur). Même patron que `session_org._CALL_ORG` : l'adaptateur REST pose la
valeur autour de l'appel, le core la lit — ici `capabilities/me_legal`, qui SITUE
une acceptation de document légal.

Pourquoi une acceptation a besoin de ça : un consentement n'est opposable que daté
**et** situé. « Il a accepté » ne prouve rien tout seul ; « telle version, à telle
date, depuis telle adresse, avec tel navigateur » se défend.

Hors requête REST (MCP, boot, runner, tests) la trace vaut `None` de bout en bout.
Une trace absente reste absente : rien n'y est deviné, et surtout pas l'IP du
serveur lui-même.
"""
from __future__ import annotations

import contextvars
from typing import Optional

# Longueur maximale conservée d'un user-agent. L'en-tête est écrit par le client,
# donc de taille non bornée : sans écrêtage, une ligne de consentement peut porter
# un mégaoctet de chaîne arbitraire. 512 caractères couvrent tous les user-agents de
# navigateur réels avec de la marge — on écrête, on ne refuse pas : la trace vaut
# mieux tronquée qu'absente.
MAX_USER_AGENT = 512

_CLIENT: contextvars.ContextVar[Optional[dict]] = contextvars.ContextVar(
    "oto_client_trace", default=None)

VIDE = {"ip": None, "user_agent": None}


def pick_ip(cf_connecting_ip: Optional[str], x_forwarded_for: Optional[str],
            peer: Optional[str]) -> Optional[str]:
    """L'IP réelle du client derrière Cloudflare puis Caddy.

    Ordre imposé par la topologie (ADR infra : tout ce qui est servi est CF-proxied,
    donc l'IP de socket est celle du dernier relais, jamais celle du client) :
    `CF-Connecting-IP` > **premier** hop de `X-Forwarded-For` > IP de socket.

    Le premier hop de `X-Forwarded-For`, et pas le dernier : la liste se lit
    client → relais, chaque relais AJOUTANT en queue. Prendre la fin rendrait
    l'adresse de notre propre reverse proxy.

    Règle unique du dépôt — `subdomain_project._client_ip` l'appelle aussi, avec ses
    en-têtes ASGI en octets. Deux implémentations divergeraient à la première
    évolution de la chaîne de relais, et la divergence se verrait sur une trace de
    consentement, c'est-à-dire trop tard.
    """
    if cf_connecting_ip and cf_connecting_ip.strip():
        return cf_connecting_ip.strip()
    if x_forwarded_for and x_forwarded_for.strip():
        return x_forwarded_for.split(",")[0].strip() or None
    return peer or None


def set_current(*, ip: Optional[str],
                user_agent: Optional[str]) -> contextvars.Token:
    """Pose l'empreinte du client pour la durée de l'appel courant."""
    return _CLIENT.set({
        "ip": ip or None,
        "user_agent": (user_agent or "")[:MAX_USER_AGENT] or None,
    })


def reset(token: contextvars.Token) -> None:
    _CLIENT.reset(token)


def current() -> dict:
    """`{"ip", "user_agent"}` de la requête courante — les deux à `None` hors requête
    REST. Rend toujours un dict : un appelant qui écrit une trace n'a pas à savoir
    par quelle surface il est arrivé."""
    return _CLIENT.get() or dict(VIDE)
