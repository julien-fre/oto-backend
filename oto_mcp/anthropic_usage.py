"""Ce qu'ANTHROPIC dit avoir facturé aux clés de l'instance — l'Admin API, lue pour
contrôler la mesure (18/10/2026).

La clé `anthropic` posée au rang plateforme se mesure elle-même : une ligne
`anthropic_tokens` par appel au modèle (`capabilities/runner_jobs._metrer_un_tour`).
C'est NOTRE code qui rend compte de lui-même. Ce module est le contrôle qui ne lui
fait pas confiance : le relevé d'usage d'Anthropic, par clé d'API et par modèle, pour
un jour UTC, converti en micro-dollars aux tarifs du catalogue
(`runner_models.tarif_anthropic`).

Sources, lues le 18/10/2026 :
- `GET /v1/organizations/usage_report/messages` — `starting_at`, `ending_at`,
  `bucket_width=1d`, `group_by[]=api_key_id&group_by[]=model`, pages par
  `has_more`/`next_page` (rendu en `page`). Chaque résultat porte
  `uncached_input_tokens`, `cache_creation.ephemeral_5m_input_tokens`,
  `cache_creation.ephemeral_1h_input_tokens`, `cache_read_input_tokens`,
  `output_tokens`, `api_key_id` (null = usage de la Console), `model`.
  https://platform.claude.com/docs/en/api/beta/organization/usage_report/retrieve_messages
- `GET /v1/organizations/api_keys` — `limit` (≤ 1000), pages par `has_more`/`last_id`
  (rendu en `after_id`) ; chaque clé porte `id`, `name`, `partial_key_hint`
  (`sk-ant-api03-R2D...igAA` : la tête et les 4 derniers caractères).
  https://platform.claude.com/docs/en/api/beta/organization/api_keys/list

Une clé d'ADMIN (`sk-ant-admin…`) les lit ; une clé ordinaire est refusée. Elle vient
de l'environnement (`OTO_ANTHROPIC_ADMIN_KEY`), jamais d'un argument.

⚠️ Pur réseau et calcul : ni base, ni coffre. Bloquant (`requests`) — l'appelant le
sort de la boucle.
"""
from __future__ import annotations

import math
import os
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional

import requests

from . import runner_models

_USAGE_URL = "https://api.anthropic.com/v1/organizations/usage_report/messages"
_KEYS_URL = "https://api.anthropic.com/v1/organizations/api_keys"
_VERSION = "2023-06-01"
#: Borne par requête. La capacité qui l'appelle est servie sous 45 s (REST) : quelques
#: pages au plus, dans un jour de relevé.
_TIMEOUT = 15
_MAX_PAGES = 20


class AnthropicAdminError(RuntimeError):
    """L'Admin API a refusé ou n'a pas répondu. `status` = le code HTTP, s'il y en a un."""

    def __init__(self, message: str, *, status: Optional[int] = None):
        super().__init__(message)
        self.status = status


def admin_key() -> str:
    return (os.environ.get("OTO_ANTHROPIC_ADMIN_KEY") or "").strip()


def _get(url: str, key: str, params: list) -> dict:
    try:
        r = requests.get(url, params=params, timeout=_TIMEOUT,
                         headers={"x-api-key": key, "anthropic-version": _VERSION})
    except requests.RequestException as e:
        # Pas de `str(e)` : le message de requests recopie l'URL et ses paramètres.
        raise AnthropicAdminError(f"Admin API injoignable ({type(e).__name__})") from None
    if r.status_code >= 400:
        raise AnthropicAdminError(f"Admin API : HTTP {r.status_code}", status=r.status_code)
    return r.json()


def usage_du_jour(jour: date, key: str) -> list[dict]:
    """Chaque résultat du relevé de `jour` (UTC), par clé d'API et par modèle."""
    debut = datetime.combine(jour, time(0), tzinfo=timezone.utc)
    params = [("starting_at", debut.strftime("%Y-%m-%dT%H:%M:%SZ")),
              ("ending_at", (debut + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")),
              ("bucket_width", "1d"), ("group_by[]", "api_key_id"), ("group_by[]", "model")]
    resultats: list[dict] = []
    page: Optional[str] = None
    for _ in range(_MAX_PAGES):
        corps = _get(_USAGE_URL, key, params + ([("page", page)] if page else []))
        for seau in corps.get("data") or []:
            resultats.extend(seau.get("results") or [])
        page = corps.get("next_page") if corps.get("has_more") else None
        if not page:
            return resultats
    raise AnthropicAdminError(f"relevé du {jour} non terminé après {_MAX_PAGES} pages")


def cles_d_api(key: str) -> list[dict]:
    """Toutes les clés d'API de l'organisation Anthropic, archivées comprises : une clé
    archivée dans la journée a pu dépenser avant."""
    cles: list[dict] = []
    apres: Optional[str] = None
    for _ in range(_MAX_PAGES):
        corps = _get(_KEYS_URL, key, [("limit", "1000")] + ([("after_id", apres)] if apres else []))
        cles.extend(corps.get("data") or [])
        apres = corps.get("last_id") if corps.get("has_more") else None
        if not apres:
            return cles
    raise AnthropicAdminError(f"liste des clés non terminée après {_MAX_PAGES} pages")


def indice_correspond(indice: Optional[str], secret: str) -> bool:
    """`partial_key_hint` (`sk-ant-api03-R2D...igAA`) désigne-t-il ce secret ? La tête
    avant `...` en est le début, les caractères après en sont la fin."""
    if not indice or "..." not in indice or not secret:
        return False
    tete, _, queue = indice.partition("...")
    return bool(tete) and bool(queue) and secret.startswith(tete) and secret.endswith(queue)


def microdollars(resultat: dict) -> Optional[int]:
    """Le coût d'un résultat du relevé aux tarifs du catalogue, arrondi au-dessus — ou
    None si son modèle n'a pas de tarif (jamais un prix deviné)."""
    tarif = runner_models.tarif_anthropic(resultat.get("model"))
    if tarif is None:
        return None
    cache = resultat.get("cache_creation") or {}
    total = (int(resultat.get("uncached_input_tokens") or 0) * tarif.entree
             + int(resultat.get("output_tokens") or 0) * tarif.sortie
             + int(cache.get("ephemeral_5m_input_tokens") or 0) * tarif.ecriture_cache
             + int(cache.get("ephemeral_1h_input_tokens") or 0)
             * runner_models.ecriture_cache_1h(tarif)
             + int(resultat.get("cache_read_input_tokens") or 0) * tarif.lecture_cache)
    return math.ceil(round(total, 6))
