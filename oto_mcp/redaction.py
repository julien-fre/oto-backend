"""Rédaction des champs sensibles du RÉSULTAT d'un tool — logique PARTAGÉE.

Extraite de `middleware.FieldRedactionMiddleware` (chemin protocole `tools/call`)
pour être réutilisée par `oto_call` (dispatch universel, ADR 0036) : le dispatch
exécute la cible via `Tool.run` **hors chaîne de middleware**, donc il doit
ré-appliquer la rédaction lui-même — sinon un connecteur à PII
(folk/pennylane/unipile) fuiterait par ce canal. « Derive don't duplicate ».

Politique (ADR 0009/0015) : la policy de l'org active gouverne l'exposition.
**Fail-closed** : une policy qui EXISTE mais échoue RETIENT la sortie (lève
`RedactionWithheld`) plutôt que de laisser fuiter le brut. Absence de policy
(`is_empty`), échec de résolution sur un service sans défaut serveur, ou payload
non-structuré = passe-through (sentinelle `PASSTHROUGH`).

Ce module porte aussi le **rendu du VIDE** (`is_empty_payload`/`render_empty`) :
un résultat sans aucun résultat se sert au modèle **en phrase**, jamais en
structure nue. Cf. `EMPTY_MESSAGES` pour la règle et son incident fondateur.
"""
from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

# Sentinelle « rien à rédiger » — distincte de None (un payload peut légitimement
# valoir None). L'appelant renvoie alors le résultat d'ORIGINE inchangé (pas de
# re-sérialisation sur le chemin chaud du cas commun « aucune policy »).
PASSTHROUGH = object()


class RedactionWithheld(Exception):
    """La rédaction d'une policy EXISTANTE a échoué → sortie retenue (fail-closed)."""


def _resolve_field_filter(service: str):
    # Import tardif : `access` importe des stores → éviter un cycle au chargement.
    from . import access
    return access.resolve_field_filter(service)


def _service_has_server_default(service: str) -> bool:
    from . import field_filter_defaults
    return service in field_filter_defaults.SERVER_DEFAULTS


def extract_payload(result) -> dict | list | None:
    """Forme brute renvoyée par un tool à partir de son `ToolResult` :
    `structured_content` si dict, sinon le JSON du 1er bloc `content`. None si
    rien de structuré (texte libre / binaire)."""
    sc = getattr(result, "structured_content", None)
    if isinstance(sc, dict):
        return sc
    content = getattr(result, "content", None) or []
    block = content[0] if content else None
    text = getattr(block, "text", None)
    if isinstance(text, str):
        try:
            data = json.loads(text)
        except (ValueError, TypeError):
            return None
        if isinstance(data, (dict, list)):
            return data
    return None


def redact_payload(service: str, payload):
    """Applique la policy de rédaction de l'org active pour `service` (namespace)
    au `payload` brut (dict | list). Retourne le payload rédacté, ou `PASSTHROUGH`
    si rien ne s'applique. Lève `RedactionWithheld` si une policy existe mais lève."""
    if not isinstance(payload, (dict, list)):
        return PASSTHROUGH
    try:
        ff = _resolve_field_filter(service)
    except Exception:
        # Résolution de policy en échec (ex. DB) : policy inconnue. Service à PII
        # connu (défaut serveur déclaré) → fail-closed ; sinon passe-through pour
        # ne pas casser tous les tools sur un aléa DB.
        logger.exception("resolve_field_filter a échoué pour %s", service)
        if _service_has_server_default(service):
            raise RedactionWithheld(service)
        return PASSTHROUGH
    if ff.is_empty:
        return PASSTHROUGH
    # Une policy EXISTE pour ce service → fail-closed à partir d'ici.
    try:
        return ff.apply(payload)
    except Exception:
        logger.exception("rédaction de %s en échec — sortie retenue", service)
        raise RedactionWithheld(service)


def rebuild_result(result, redacted):
    """Réémet un `ToolResult` avec `redacted` sur les DEUX canaux : texte JSON +
    `structured_content` (seulement si l'original en portait un dict — sinon la
    donnée vivait dans le canal texte, le structuré reste vide)."""
    from fastmcp.tools.tool import ToolResult
    from mcp.types import TextContent
    sc = getattr(result, "structured_content", None)
    return ToolResult(
        content=[TextContent(type="text", text=json.dumps(redacted, default=str))],
        structured_content=redacted if isinstance(sc, dict) else None,
        meta=getattr(result, "meta", None),
        is_error=False,
    )


def withheld_result(name: str):
    """`ToolResult` d'erreur « sortie retenue » (fail-closed) pour l'outil `name`."""
    from fastmcp.tools.tool import ToolResult
    from mcp.types import TextContent
    return ToolResult(
        content=[TextContent(
            type="text",
            text=f"[oto] rédaction de « {name} » impossible — sortie retenue par sécurité.")],
        is_error=True,
    )


# --- Rendu du VIDE : une phrase, jamais une structure nue ---------------------

# Phrase servie quand l'outil ne déclare pas la sienne.
EMPTY_MESSAGE_DEFAULT = "Aucun résultat pour cette recherche."

# Gabarit par outil — table de DÉCLARATION, pas de logique : le rendu du vide est
# générique, seul le mot juste appartient à l'outil. Clé = nom CANONIQUE (le
# préfixe de tenant est rétabli plus haut dans la chaîne). Déclarée ici, dans la
# couche de rendu, plutôt qu'au registre des connecteurs : la table est lue sur le
# chemin chaud de CHAQUE appel, et un outil n'a pas à savoir comment on le rend.
EMPTY_MESSAGES: dict[str, str] = {
    "fr_accords_search": "Aucun accord déposé pour ce SIREN.",
}

# Clés reconnues comme compteur de volume à côté d'une collection.
_COUNTER_KEYS = ("total_count", "total", "count")


def empty_message(tool_name: str) -> str:
    """Phrase à servir pour un résultat vide de `tool_name` : son gabarit déclaré,
    sinon la phrase générique."""
    return EMPTY_MESSAGES.get(tool_name) or EMPTY_MESSAGE_DEFAULT


def is_empty_payload(payload) -> bool:
    """Vrai quand le payload ne porte AUCUN résultat.

    Règle volontairement SYNTAXIQUE — elle ne connaît aucun outil :
    - une **liste** est vide si elle n'a pas d'élément ;
    - un **dict** est vide si (a) il porte au moins une collection — toute clé dont
      la valeur est une liste, `rows`/`items`/`results`/`data`/`hits` comprises —,
      (b) elles sont TOUTES vides, et (c) tout compteur présent (`total_count`,
      `total`, `count`) vaut 0 ; un compteur non nul CONTREDIT la collection vide,
      et on rend alors la structure telle quelle plutôt que d'affirmer un vide ;
    - tout le reste n'est PAS vide : un scalaire, un dict SANS collection, une
      collection non vide. Une clé scalaire à côté d'une collection vide (l'écho
      `_account`, un curseur) ne l'empêche pas — le vide se juge sur les collections.

    Le vide ne se cherche qu'à la RACINE : une collection vide imbriquée sous un
    résultat par ailleurs peuplé n'est pas un résultat vide.
    """
    if isinstance(payload, list):
        return not payload
    if not isinstance(payload, dict):
        return False
    collections = [v for v in payload.values() if isinstance(v, list)]
    if not collections or any(collections):
        return False
    for cle in _COUNTER_KEYS:
        valeur = payload.get(cle)
        if isinstance(valeur, int) and not isinstance(valeur, bool) and valeur != 0:
            return False
    return True


def render_empty(result, tool_name: str):
    """Réémet un `ToolResult` dont le canal TEXTE ne porte que la phrase, le canal
    structuré restant INTACT — le client qui parse garde sa structure vide.

    C'est la structure DANS LE TEXTE qui fait dégénérer le décodage du modèle, pas
    son existence : un `{"total_count": 0, "rows": []}` servi en texte a fait boucler
    une flotte d'agents sur des centaines de `]}` avant qu'ils n'encadrent leur propre
    narration en appel d'outil — 16 des 26 faux départs d'une campagne et 10 des 11
    d'une vague de production (2026-08-27, otomata-tech/oto#32).

    Phrase SEULE : y rajouter la structure « pour information » rétablirait très
    exactement le déclencheur qu'on retire.
    """
    from fastmcp.tools.tool import ToolResult
    from mcp.types import TextContent
    return ToolResult(
        content=[TextContent(type="text", text=empty_message(tool_name))],
        structured_content=getattr(result, "structured_content", None),
        meta=getattr(result, "meta", None),
        is_error=False,
    )
