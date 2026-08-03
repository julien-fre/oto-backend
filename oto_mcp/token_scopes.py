"""Portée d'un jeton API `oto_…` — le confier sans confier l'organisation.

Un jeton API **est** le sub : le porteur peut tout ce que la personne peut. C'est
pourtant lui qu'on confie à une intégration tierce (un front client qui affiche UN
tableau) — elle reçoit l'organisation entière, plus l'identité, plus la liste des
connecteurs, plus les jetons du compte. La restriction était alors portée par le
code de l'intégration : la mauvaise couche.

Un jeton **porté** (`user_api_tokens.scopes` non NULL) inverse la posture :
**rien n'est permis sauf ce que la portée nomme**. Une seule portée est exprimable
aujourd'hui, le datastore :

    {"namespaces": {"leads-accords-dormants": "read", "sorties": "write"}}

`read` = lire le tableau ; `write` = lire **et** écrire ses LIGNES. Ni l'un ni
l'autre n'ouvre la gouvernance (créer / supprimer / renommer / partager un tableau),
ni quoi que ce soit hors datastore (`/api/me`, `/api/me/tokens`, `/api/connectors`,
les capacités…). La table `_ALLOWED` ci-dessous est la **seule** porte : tout ce qui
n'y figure pas est refusé, y compris une route ajoutée demain — deny-by-default, pas
une denylist à tenir à jour.

Un jeton **sans** portée (`scopes` NULL) garde le comportement historique (pleins
pouvoirs du sub) : aucune migration, aucun jeton existant cassé.

⚠️ La portée nomme le tableau par son **nom** — ce que l'URL adresse — pas par son
id. Renommer un tableau (ou en créer un qui reprend un nom libéré) déplace donc ce
que le jeton atteint. Les deux actes sont hors de portée d'un jeton porté (ils
demandent une session interactive du propriétaire), mais après un renommage :
ré-émettre le jeton.
"""
from __future__ import annotations

import contextvars
import re
from typing import Optional
from urllib.parse import unquote

READ, WRITE = "read", "write"

# `write` contient `read` : un jeton en écriture lit aussi.
_IMPLIES = {READ: frozenset({READ}), WRITE: frozenset({READ, WRITE})}

# Portée du jeton de la requête courante — posée par `api_routes._authenticate` à
# CHAQUE requête (None comprise : jamais de valeur rémanente d'une requête voisine).
# ContextVar = par tâche asyncio, donc par requête. Lue par les handlers qui doivent
# FILTRER leur réponse (la liste des tableaux) plutôt que la refuser en bloc.
_CURRENT: contextvars.ContextVar[Optional[dict]] = contextvars.ContextVar(
    "oto_token_scope", default=None)

_NS = r"(?P<ns>[^/]+)"

# (chemin, méthodes, permission requise sur le namespace capturé). Disjointes.
_ALLOWED: tuple[tuple[re.Pattern, frozenset, str], ...] = (
    (re.compile(rf"^/api/datastore/namespaces/{_NS}/rows$"), frozenset({"GET"}), READ),
    (re.compile(rf"^/api/datastore/namespaces/{_NS}/rows$"), frozenset({"POST"}), WRITE),
    (re.compile(rf"^/api/datastore/namespaces/{_NS}/rows/[^/]+$"), frozenset({"GET"}), READ),
    (re.compile(rf"^/api/datastore/namespaces/{_NS}/rows/[^/]+$"),
     frozenset({"PATCH", "DELETE"}), WRITE),
    (re.compile(rf"^/api/datastore/namespaces/{_NS}/rows/[^/]+/release$"),
     frozenset({"POST"}), WRITE),
    (re.compile(rf"^/api/datastore/namespaces/{_NS}/rows/[^/]+/activity$"),
     frozenset({"GET"}), READ),
    (re.compile(rf"^/api/datastore/namespaces/{_NS}/activity$"), frozenset({"GET"}), READ),
    (re.compile(rf"^/api/datastore/namespaces/{_NS}/queue$"), frozenset({"GET"}), READ),
    (re.compile(rf"^/api/datastore/namespaces/{_NS}/aggregate$"), frozenset({"GET"}), READ),
    (re.compile(rf"^/api/datastore/namespaces/{_NS}/url$"), frozenset({"GET"}), READ),
    (re.compile(rf"^/api/datastore/namespaces/{_NS}/schema$"), frozenset({"PUT"}), WRITE),
)

# Le catalogue des tableaux est LISIBLE par un jeton porté, mais FILTRÉ à sa portée
# par le handler (`ds_list_ns`) : sans lui, une intégration ne peut pas découvrir le
# schéma de son tableau (les colonnes) — `page_rows` ne le rend pas.
_FILTERED = ("GET", "/api/datastore/namespaces")


class ScopeError(ValueError):
    """Document de portée invalide (saisie de l'émetteur, jamais du porteur)."""


def parse(raw: object) -> Optional[dict]:
    """Valide et normalise un document de portée à la CRÉATION du jeton.

    `None`/absent ⇒ jeton non porté (pleins pouvoirs du sub, comportement
    historique). Sinon `{"namespaces": {nom: "read"|"write"}}`, au moins une entrée
    — une portée vide serait un jeton inerte, presque sûrement une erreur de saisie.
    """
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ScopeError("scopes doit être un objet {\"namespaces\": {…}}")
    unknown = set(raw) - {"namespaces"}
    if unknown:
        raise ScopeError(f"clé(s) de portée inconnue(s) : {sorted(unknown)}")
    ns = raw.get("namespaces")
    if not isinstance(ns, dict) or not ns:
        raise ScopeError("scopes.namespaces doit être un objet non vide {nom: read|write}")
    out: dict[str, str] = {}
    for name, perm in ns.items():
        if not isinstance(name, str) or not name.strip():
            raise ScopeError("nom de tableau vide dans scopes.namespaces")
        if perm not in (READ, WRITE):
            raise ScopeError(f"permission « {perm} » sur « {name} » : attendu read|write")
        out[name.strip()] = perm
    return {"namespaces": out}


def namespaces(scopes: Optional[dict]) -> frozenset:
    """Noms de tableaux nommés par la portée (vide si le jeton n'est pas porté)."""
    if not scopes:
        return frozenset()
    return frozenset((scopes.get("namespaces") or {}).keys())


def authorize(scopes: Optional[dict], method: str, path: str) -> bool:
    """La requête `(method, path)` est-elle dans la portée ? Fail-closed.

    `scopes` None ⇒ jeton non porté ⇒ True (le gate ne s'applique qu'aux jetons
    portés ; les droits du sub restent seuls juges en aval).
    """
    if scopes is None:
        return True
    grants = (scopes or {}).get("namespaces") or {}
    method = (method or "").upper()
    path = path.rstrip("/") or "/"
    if (method, path) == _FILTERED:
        return True                       # lecture filtrée par le handler
    for pattern, methods, needed in _ALLOWED:
        if method not in methods:
            continue
        m = pattern.match(path)
        if not m:
            continue
        granted = grants.get(unquote(m.group("ns")))
        return granted is not None and needed in _IMPLIES[granted]
    return False


# ── Portée de la requête courante ────────────────────────────────────────────

def set_current(scopes: Optional[dict]) -> None:
    """Posée à chaque authentification REST — y compris à None (JWT, jeton non
    porté), pour qu'aucune portée ne survive à sa requête."""
    _CURRENT.set(scopes)


def current() -> Optional[dict]:
    return _CURRENT.get()


def filter_namespaces(rows: list) -> list:
    """Restreint une liste de tableaux à la portée du jeton courant (no-op hors
    jeton porté). Le catalogue est la seule réponse FILTRÉE plutôt que refusée.

    Les droits annoncés sont **rabattus** sur ceux du jeton : une entrée dit
    `permission='write'` parce que le SUB peut écrire, or c'est le jeton qui appelle.
    Un front qui peint ses boutons sur ces champs afficherait sinon une écriture que
    le serveur refusera.
    """
    scopes = current()
    if scopes is None:
        return rows
    grants = (scopes or {}).get("namespaces") or {}
    out = []
    for r in rows:
        perm = grants.get((r or {}).get("namespace"))
        if perm is None:
            continue
        e = dict(r)
        e["permission"] = perm
        e["can_write"] = perm == WRITE
        e["can_govern"] = False           # un jeton porté ne gouverne jamais
        out.append(e)
    return out
