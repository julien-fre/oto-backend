"""Périmètre d'URL d'un projet — ce qu'un outil de recherche ÉCARTE et ce qu'un outil
d'extraction REFUSE (#605).

Un contrat client peut exclure la CONSULTATION de certaines pages (le cas fondateur :
les profils personnels d'un réseau social professionnel). La consigne le disait ; sur
cent fiches, deux ont consulté quand même. Hiérarchie : le chemin n'existe pas > la
machine refuse > un contrôle détecte > la consigne interdit. Ce module est le premier
cran : une option par projet, `excluded_url_prefixes`, et UN seam que tous les
outils concernés appellent — jamais une règle réécrite outil par outil.

Trois gestes, une seule définition du « correspond » :

- **`perimeter_of_call()`** — le périmètre du projet de l'APPEL (jeton `_project=`,
  ou projet de l'endpoint publié), ou `None` : sans projet ou sans option, rien ne
  change, et c'est prouvé par différentiel (`tests/test_url_perimeter.py`).
- **`filter_results(payload, perimeter)`** — sortie d'un outil de RECHERCHE : les
  résultats dont l'URL correspond ne sont pas rendus, et la réponse porte
  `excluded_by_perimeter` (combien, par quel projet, par quel motif) — jamais en
  silence, même à zéro : l'agent sait qu'un périmètre est en force.
- **`refuse_if_excluded(url, ...)`** — entrée d'un outil d'EXTRACTION : une URL
  correspondante est refusée en nommant le motif et le projet.

**Grammaire d'un motif** — hôte + préfixe de chemin, normalisés ; jamais une regex :
- `linkedin.com/in/` (≡ `https://www.LinkedIn.com/in`) : l'hôte ou l'un de ses
  sous-domaines (`fr.linkedin.com`), `www.` ignoré, casse ignorée ; le chemin se compare
  SEGMENT par segment (`/in/` couvre `/in/jane`, pas `/inbox`).
- `linkedin.com/company/` n'est PAS couvert par `linkedin.com/in/` : les motifs sont
  précis, un domaine entier n'est jamais implicite.
- Un domaine entier s'écrit EXPLICITEMENT `exemple.com/*` ; un hôte nu (`exemple.com`,
  `exemple.com/`) est REFUSÉ à la pose, avec la forme à écrire — l'oubli d'un chemin ne
  doit pas devenir une exclusion de tout un site.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import unquote, urlsplit

from .mcp_errors import McpError
from mcp.types import ErrorData, INVALID_PARAMS

# Nom de l'option de projet (colonne `projects.excluded_url_prefixes`). Le cliquet de
# `tests/test_url_perimeter.py` interdit de relire ce nom hors de ce seam, de la
# persistance et du contrat d'entrée.
OPTION = "excluded_url_prefixes"

MAX_PREFIXES = 50
MAX_PREFIX_LEN = 200

_HOST_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")
_URL_KEYS = ("link", "url")
_META_URL_KEYS = ("sourceURL", "url")
_MAX_DEPTH = 6


class PerimeterError(ValueError):
    """Motif irrecevable À LA POSE — `code` court + message qui dit la forme attendue."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Prefix:
    host: str                    # normalisé : minuscules, sans `www.`, sans port
    segments: tuple[str, ...]    # segments de chemin normalisés ; `("*",)` = tout l'hôte

    @property
    def canonical(self) -> str:
        if self.segments == ("*",):
            return f"{self.host}/*"
        return f"{self.host}/{'/'.join(self.segments)}/"

    def matches(self, url: str) -> bool:
        parts = _split_url(url)
        if parts is None:
            return False
        host, segs = parts
        if host != self.host and not host.endswith("." + self.host):
            return False
        if self.segments == ("*",):
            return True
        return tuple(segs[:len(self.segments)]) == self.segments


@dataclass(frozen=True)
class Perimeter:
    project_id: int
    project_name: str
    prefixes: tuple[Prefix, ...]

    def match(self, url: str) -> Optional[Prefix]:
        for p in self.prefixes:
            if p.matches(url):
                return p
        return None


# ── grammaire ─────────────────────────────────────────────────────────────────

def _split_url(url: object) -> Optional[tuple[str, list[str]]]:
    """(hôte normalisé, segments de chemin normalisés) d'une URL, ou None si elle n'a
    pas d'hôte. Une URL sans schéma (`linkedin.com/in/x`) est lue comme https."""
    s = str(url or "").strip()
    if not s:
        return None
    if "://" not in s:
        s = "https://" + s
    parts = urlsplit(s)
    host = (parts.hostname or "").lower().removeprefix("www.")
    if not host:
        return None
    segs = [unquote(seg).lower() for seg in parts.path.split("/") if seg]
    return host, segs


def parse_prefix(raw: object) -> Prefix:
    """Un motif saisi → sa forme canonique, ou `PerimeterError` qui dit quoi écrire."""
    s = str(raw or "").strip()
    if not s:
        raise PerimeterError("empty_prefix", "motif vide.")
    if len(s) > MAX_PREFIX_LEN:
        raise PerimeterError("prefix_too_long",
                             f"motif de {len(s)} caractères (max {MAX_PREFIX_LEN}).")
    if any(c.isspace() for c in s):
        raise PerimeterError("prefix_has_space", f"`{s}` contient une espace.")
    if "://" in s:
        scheme, _, s = s.partition("://")
        if scheme.lower() not in ("http", "https"):
            raise PerimeterError("bad_scheme",
                                 f"schéma `{scheme}` refusé — un motif est http(s) ou nu.")
    if "?" in s or "#" in s:
        raise PerimeterError(
            "query_in_prefix",
            f"`{s}` porte une requête ou un fragment — un motif est un hôte + un "
            "préfixe de chemin, rien après.")
    host, _, path = s.partition("/")
    if "@" in host:
        raise PerimeterError("bad_host", f"`{host}` : identifiants dans l'hôte refusés.")
    host = host.split(":", 1)[0].lower().removeprefix("www.")
    if not _HOST_RE.match(host):
        raise PerimeterError(
            "bad_host",
            f"`{host or s}` n'est pas un hôte (attendu : `exemple.com/chemin/`).")
    segs = [seg for seg in path.split("/") if seg]
    if not segs:
        raise PerimeterError(
            "bare_host",
            f"`{host}` seul désignerait le domaine ENTIER. Précise le chemin à exclure "
            f"(ex. `{host}/in/`) — ou, si c'est vraiment tout le site, écris-le "
            f"explicitement : `{host}/*`.")
    if "*" in path:
        if segs != ["*"]:
            raise PerimeterError(
                "wildcard",
                f"`{s}` : `*` n'est accepté que seul après l'hôte (`{host}/*` = tout le "
                "site) — un préfixe de chemin couvre déjà tout ce qui est dessous.")
        return Prefix(host, ("*",))
    return Prefix(host, tuple(unquote(seg).lower() for seg in segs))


def normalize_prefixes(raws: object) -> list[str]:
    """La liste saisie → la liste STOCKÉE (canonique, dédoublonnée, bornée).
    Lève `PerimeterError` sur le premier motif irrecevable : on ne stocke rien d'un lot
    dont un élément est faux — une pose partielle serait une exclusion partielle
    silencieuse."""
    if raws is None:
        return []
    if not isinstance(raws, (list, tuple)):
        raise PerimeterError("not_a_list", "`excluded_url_prefixes` attend une liste de motifs.")
    if len(raws) > MAX_PREFIXES:
        raise PerimeterError("too_many_prefixes",
                             f"{len(raws)} motifs (max {MAX_PREFIXES}).")
    out: list[str] = []
    for raw in raws:
        canon = parse_prefix(raw).canonical
        if canon not in out:
            out.append(canon)
    return out


# ── résolution ────────────────────────────────────────────────────────────────

def perimeter_of_project(row: Optional[dict]) -> Optional[Perimeter]:
    """Le périmètre d'une ligne `projects`, ou None (pas de ligne, option vide). Pur."""
    if not row:
        return None
    stored = row.get(OPTION) or []
    if not stored:
        return None
    return Perimeter(project_id=int(row["id"]), project_name=str(row.get("name") or ""),
                     prefixes=tuple(parse_prefix(s) for s in stored))


def current_project_id() -> Optional[int]:
    """Projet de l'APPEL : le jeton `_project=` (`access.current_project`), sinon le
    projet de l'endpoint publié (`subdomain_project`). None hors projet."""
    from . import access, subdomain_project
    pid = access.current_project()
    if pid is None:
        pid = subdomain_project.current_anon_project_id()
    return int(pid) if pid is not None else None


def perimeter_of_call() -> Optional[Perimeter]:
    """Le périmètre en force pour CET appel, ou None (sans projet, ou sans option :
    aucun changement). Une lecture DB (sync) — à appeler depuis un handler sync ou via
    `asyncio.to_thread` depuis un handler async (contrainte mono-loop)."""
    pid = current_project_id()
    if pid is None:
        return None
    from . import db
    return perimeter_of_project(db.get_project_by_id(pid))


# ── effet (a) : sortie d'un outil de recherche ────────────────────────────────

def _item_url(item: dict) -> Optional[str]:
    for k in _URL_KEYS:
        v = item.get(k)
        if isinstance(v, str) and v:
            return v
    meta = item.get("metadata")
    if isinstance(meta, dict):
        for k in _META_URL_KEYS:
            v = meta.get(k)
            if isinstance(v, str) and v:
                return v
    return None


def _looks_like_url(s: str) -> bool:
    return s.startswith(("http://", "https://"))


def _walk(node: Any, per: Perimeter, counts: dict[str, int], depth: int) -> Any:
    if depth > _MAX_DEPTH:
        return node
    if isinstance(node, list):
        out = []
        for el in node:
            if isinstance(el, dict):
                u = _item_url(el)
                hit = per.match(u) if u else None
                if hit is not None:
                    counts[hit.canonical] += 1
                    continue
                out.append(_walk(el, per, counts, depth + 1))
            elif isinstance(el, str) and _looks_like_url(el):
                hit = per.match(el)
                if hit is not None:
                    counts[hit.canonical] += 1
                    continue
                out.append(el)
            else:
                out.append(_walk(el, per, counts, depth + 1))
        return out
    if isinstance(node, dict):
        return {k: _walk(v, per, counts, depth + 1) for k, v in node.items()}
    return node


def filter_results(payload: Any, perimeter: Optional[Perimeter]) -> Any:
    """Copie de `payload` sans les résultats dont l'URL correspond au périmètre.

    Sans périmètre : `payload` est rendu TEL QUEL (même objet) — c'est l'identité que
    le différentiel prouve. Avec : chaque liste, à toute profondeur, perd les éléments
    (dict à `link`/`url`/`metadata.sourceURL`, ou chaîne URL) qui correspondent ; le
    résultat porte `excluded_by_perimeter = {count, project_id, project, prefixes}` —
    `prefixes` = CHAQUE motif en force → nombre écarté, zéro compris.

    Ne filtre pas la prose (une réponse synthétique qui cite une URL en texte) : le
    seam voit des structures, pas des phrases — dit dans `docs/projects.md`."""
    if perimeter is None:
        return payload
    counts = {p.canonical: 0 for p in perimeter.prefixes}
    out = _walk(payload, perimeter, counts, 0)
    if isinstance(out, dict):
        out["excluded_by_perimeter"] = {
            "count": sum(counts.values()),
            "project_id": perimeter.project_id,
            "project": perimeter.project_name,
            "prefixes": counts,
        }
    return out


# ── effet (b) : entrée d'un outil d'extraction ────────────────────────────────

def refusal_message(url: str, hit: Prefix, perimeter: Perimeter) -> str:
    return (f"URL refusée : `{url}` relève du motif `{hit.canonical}`, exclu par le "
            f"périmètre du projet « {perimeter.project_name} » (#{perimeter.project_id}, "
            f"option `{OPTION}`). Cette page n'est pas consultable dans ce projet — ne la "
            "contourne par aucun autre outil.")


def refuse_if_excluded(url: object, perimeter: Optional[Perimeter]) -> None:
    """Lève une `McpError` (entrée invalide, hors Sentry) si `url` correspond au
    périmètre. No-op sans périmètre. Le périmètre se passe explicitement : l'appelant
    le résout UNE fois (`perimeter_of_call`) et le réutilise pour l'URL demandée comme
    pour l'URL finale observée après redirection."""
    if perimeter is None or not url:
        return
    hit = perimeter.match(str(url))
    if hit is not None:
        raise McpError(ErrorData(code=INVALID_PARAMS,
                                 message=refusal_message(str(url), hit, perimeter)))


def refuse_if_any_excluded(urls: object, perimeter: Optional[Perimeter]) -> None:
    """Lot d'URLs (extraction multiple) : TOUT le lot est refusé si une seule
    correspond, en les nommant toutes — un lot servi « sans les mauvaises » serait un
    résultat partiel que rien n'annoncerait dans le contenu rendu."""
    if perimeter is None or not urls:
        return
    hits = [(str(u), perimeter.match(str(u))) for u in urls if u]
    bad = [(u, h) for u, h in hits if h is not None]
    if not bad:
        return
    if len(bad) == 1:
        raise McpError(ErrorData(code=INVALID_PARAMS,
                                 message=refusal_message(bad[0][0], bad[0][1], perimeter)))
    lignes = "\n".join(f"- `{u}` (motif `{h.canonical}`)" for u, h in bad)
    raise McpError(ErrorData(
        code=INVALID_PARAMS,
        message=(f"{len(bad)} URL refusées — exclues par le périmètre du projet "
                 f"« {perimeter.project_name} » (#{perimeter.project_id}, option "
                 f"`{OPTION}`) :\n{lignes}\nRetire-les du lot : ces pages ne sont pas "
                 "consultables dans ce projet, par aucun outil.")))
