"""Le temps qu'un agent fait gagner — estimé depuis ce qu'il a FAIT, sans rien demander.

**Le parti** (page Agents d'un front, 17/10/2026) : aucune saisie, aucun réglage
obligatoire, aucune question à l'utilisateur. Le temps gagné d'un run est la somme
de ses RÉSULTATS × des minutes qu'un humain y passerait. Le type d'un résultat se
déduit de l'outil appelé — de ce que l'outil DÉCLARE déjà (`tools/lecture.py` :
`LECTURE` lit, `ENVOI` agit chez un tiers, le reste écrit), pas d'une table tenue à
la main : un connecteur ajouté demain a son temps gagné le jour même.

**Ce qui compte** :
- seuls les runs CONCLUS `done` (un run en échec n'a rien fait gagner) ;
- `result.tool_counts` du worker (`oto-runner/conclusion.py`) : appels par outil ;
- la tenue de la plateforme ne compte pas (`oto_*`, `run_start`/`run_finish`, les
  lectures et réservations du tableau de l'agent) ;
- un outil que le registre ne connaît pas ne compte pas — on ne devine pas ;
- la RECHERCHE est plafonnée par run : un agent qui lit 200 pages n'est pas un héros.

⚠️ `tool_counts` compte les APPELS, réussis ou non : un appel refusé dans un run
qui conclut `done` compte. Le worker ne distingue pas encore — la borne est le run.

**Les minutes par défaut** sont des points de départ, prudents, à confirmer avec des
clients ; une org les surcharge (`oto_org_settings domain=time_saved`, admin),
jamais l'agent un par un.
"""
from __future__ import annotations

from typing import Optional

#: Les types de résultat, dans l'ordre où l'écran les dit. `minutes` = défaut.
KINDS: dict[str, dict] = {
    "enrichment": {"label": "contact enriched", "plural": "contacts enriched", "minutes": 3.0},
    "action": {"label": "message or action sent", "plural": "messages or actions sent", "minutes": 4.0},
    "record": {"label": "record written", "plural": "records written", "minutes": 1.5},
    "research": {"label": "research step", "plural": "research steps", "minutes": 2.0},
}

#: La recherche d'UN run ne compte pas plus que ceci, en minutes.
RESEARCH_CAP_PER_RUN = 10.0

#: Bornes d'une surcharge d'org, en minutes par résultat.
OVERRIDE_MIN, OVERRIDE_MAX = 0.0, 240.0

#: La tenue de la plateforme : jamais un résultat.
_IGNORED_EXACT = frozenset({"run_start", "run_finish", "feedback"})
#: Les outils du tableau qui ÉCRIVENT un résultat — les autres `data_*` (lire,
#: réserver, relâcher, schéma) sont la tenue du travail.
_DATA_WRITES = frozenset({"data_write", "data_update_where"})


def _effet(name: str) -> Optional[str]:
    from . import tool_registry
    return tool_registry.effet_de(name)


def resolve(name: str) -> Optional[str]:
    """Le nom canonique d'un outil compté. Le worker peut préfixer (`acme_doc` pour
    `oto_doc`, `demo-connecteur_data_claim_next`) : on prend l'outil connu dont le nom
    TERMINE le nom compté, sur une frontière `_` — le plus long, pour qu'un suffixe
    court (`get`) ne gagne jamais sur le vrai nom."""
    from . import tool_registry
    connus = tool_registry.effets_connus()
    if name in connus:
        return name
    meilleur = None
    for n in connus:
        if name.endswith("_" + n) and (meilleur is None or len(n) > len(meilleur)):
            meilleur = n
    return meilleur


def kind_of(name: str) -> Optional[str]:
    """Le type de résultat d'un appel à `name`, ou None quand il ne compte pas."""
    base = resolve(name) or name
    if base in _IGNORED_EXACT or base.startswith("oto_"):
        return None
    if base.startswith("data_"):
        return "record" if base in _DATA_WRITES else None
    effet = _effet(base)
    if effet is None:
        return None
    if "enrich" in base:
        return "enrichment"
    return {"read": "research", "act": "action", "write": "record"}.get(effet)


def standards(overrides: Optional[dict]) -> dict[str, float]:
    """Les minutes par type, défauts surchargés par l'org (clés inconnues ignorées)."""
    out = {k: v["minutes"] for k, v in KINDS.items()}
    for k, v in (overrides or {}).items():
        if k in out and isinstance(v, (int, float)):
            out[k] = float(v)
    return out


def for_run(tool_counts: dict, minutes: dict[str, float]) -> tuple[float, dict[str, int]]:
    """(minutes gagnées, résultats par type) d'UN run, depuis ses `tool_counts`."""
    counts: dict[str, int] = {}
    for name, n in (tool_counts or {}).items():
        if not isinstance(n, int) or n <= 0:
            continue
        k = kind_of(str(name))
        if k:
            counts[k] = counts.get(k, 0) + n
    total = 0.0
    for k, n in counts.items():
        m = n * minutes.get(k, 0.0)
        total += min(m, RESEARCH_CAP_PER_RUN) if k == "research" else m
    return total, counts
