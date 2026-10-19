"""Le temps qu'un agent fait gagner — compté depuis ce qu'il a FAIT, sans rien demander.

**La règle** (décision d'Alexis, 17/10/2026) : SEUL ce qu'un outil DÉCLARE compte. Le
type d'un résultat vient de l'annotation que l'outil porte (`tools/lecture.py`) :
`LECTURE` lit (une recherche), `SIMULATION` lit ou prépare ce que personne ne voit (ses
lectures sont des recherches, sa préparation ne compte pas), `ACTE`/`ENVOI` agit chez un
tiers. Un outil qui ne déclare rien ne rapporte RIEN — on ne déduit jamais une écriture
de l'absence de déclaration, ni un type du nom de l'outil.

**Ce qui compte** :
- seuls les runs CONCLUS `done` ET liés au journal (`tool_calls`) : un run sans journal
  n'est pas compté, et le résumé le dit (`not_counted`), il ne retombe sur rien ;
- de chaque appel : son `op`, s'il a RÉUSSI, et l'enregistrement touché ;
- sur un outil multiplexé, l'`op` dit le geste : une `op` de `READ_OPS` est une
  recherche ; sur un outil qui agit, seule une `op` de `SEND_OPS` est un envoi —
  ranger, archiver ou supprimer (`gmail_act op=archive|trash`) n'est pas un envoi et
  ne rapporte rien ;
- un même enregistrement ne compte qu'UNE fois par jour et par agent ;
- la RECHERCHE ne compte que dans un run qui a PRODUIT quelque chose de neuf, et
  plafonnée par run ;
- un run qui a cherché puis seulement ENVOYÉ (un résumé, une alerte) est un RAPPORT —
  une fois par run ;
- la tenue de la plateforme ne compte pas (`oto_*`, `run_start`/`run_finish`, les
  lectures et réservations du tableau de l'agent) ; seules les écritures du tableau
  nommées ici (`_DATA_WRITES`) sont des enregistrements écrits.

**Les minutes par défaut** sont des ESTIMATIONS — des points de départ prudents, pas
des mesures. Chaque org les surcharge (`oto_org_settings domain=time_saved`, admin),
jamais l'agent un par un.
"""
from __future__ import annotations

from typing import NamedTuple, Optional

#: Les types de résultat, dans l'ordre où l'écran les dit. `minutes` = défaut.
KINDS: dict[str, dict] = {
    "report": {"label": "report posted", "plural": "reports posted", "minutes": 15.0},
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
#: Les outils du tableau qui ÉCRIVENT un résultat, nommés un à un — les autres `data_*`
#: (lire, réserver, relâcher, schéma) sont la tenue du travail. Les capacités de la
#: plateforme ne portent pas d'annotation MCP : cette liste EST leur déclaration.
_DATA_WRITES = frozenset({"data_write", "data_update_where"})

#: Les `op` qui LISENT, quel que soit ce que l'outil déclare : un outil multiplexé
#: (`gmail op=search|get|attachment|drafts`) n'y compte que des recherches.
READ_OPS = frozenset({
    "get", "list", "search", "find", "read", "lookup", "query", "count", "history",
    "schema", "status", "describe", "preview", "fetch", "show", "inbox", "stats",
    "activities", "info", "check", "match", "resolve", "export", "download",
    "attachment", "drafts",
})

#: Les `op` d'un outil qui AGIT (`ACTE`/`ENVOI`) qui atteignent un tiers : un envoi.
#: Les autres gestes d'un tel outil (ranger, archiver, supprimer) ne rapportent rien.
SEND_OPS = frozenset({
    "send", "reply", "forward", "publish", "broadcast", "invite", "remind", "post",
    "comment",
})

#: Les résultats qui font d'un run un run PRODUCTIF.
_PRODUCTS = frozenset({"action", "record"})


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


def kind_of(name: str, op: Optional[str] = None) -> Optional[str]:
    """Le type de résultat d'un appel à `name` (avec son `op`, lu dans le journal), ou
    None quand il ne compte pas — dont tout outil qui ne déclare rien."""
    base = resolve(name) or name
    if base in _IGNORED_EXACT or base.startswith("oto_"):
        return None
    if base.startswith("data_"):
        return "record" if base in _DATA_WRITES else None
    effet = _effet(base)
    geste = str(op).lower() if op else None
    if effet == "read":
        return "research"
    if effet == "simulation":
        return "research" if geste in READ_OPS else None
    if effet == "act":
        if geste is None:
            return "action"
        if geste in READ_OPS:
            return "research"
        return "action" if geste in SEND_OPS else None
    return None


def standards(overrides: Optional[dict]) -> dict[str, float]:
    """Les minutes par type, défauts surchargés par l'org (clés inconnues ignorées)."""
    out = {k: v["minutes"] for k, v in KINDS.items()}
    for k, v in (overrides or {}).items():
        if k in out and isinstance(v, (int, float)):
            out[k] = float(v)
    return out


class Call(NamedTuple):
    """Un appel d'un run, tel que le journal le connaît (`tool_calls`)."""
    tool: str
    op: Optional[str] = None
    ok: bool = True
    #: L'enregistrement touché (id, email, url…) quand l'appel le nomme — None sinon.
    key: Optional[str] = None
    #: Le jour de l'appel, pour « une fois par jour ».
    day: Optional[str] = None


def for_agent(runs: list[list[Call]], minutes: dict[str, float]) -> tuple[float, dict[str, dict]]:
    """(minutes gagnées, `{type: {count, minutes}}`) d'UN agent sur ses runs conclus,
    dans l'ordre où ils ont tourné. Les règles du module, appliquées run par run :
    appels réussis seulement, un enregistrement une fois par jour, la recherche dans
    un run productif seulement et plafonnée, le rapport une fois par run."""
    vus: set[tuple] = set()
    tally: dict[str, dict] = {}
    total = 0.0

    def ajoute(k: str, n: int, m: float) -> None:
        nonlocal total
        t = tally.setdefault(k, {"count": 0, "minutes": 0.0})
        t["count"] += n
        t["minutes"] += m
        total += m

    for run in runs:
        counts: dict[str, int] = {}
        for c in run:
            if not c.ok:
                continue
            k = kind_of(c.tool, c.op)
            if not k:
                continue
            if k in _PRODUCTS and c.key:
                marque = (k, resolve(c.tool) or c.tool, c.key, c.day)
                if marque in vus:
                    continue
                vus.add(marque)
            counts[k] = counts.get(k, 0) + 1
        produits = {k: n for k, n in counts.items() if k in _PRODUCTS}
        if not produits:
            continue          # un balayage qui n'a rien produit de neuf
        recherche = counts.get("research", 0)
        if recherche and set(produits) == {"action"}:
            ajoute("report", 1, minutes["report"])     # chercher puis poster : un rapport
            continue
        for k, n in produits.items():
            ajoute(k, n, n * minutes[k])
        if recherche:
            ajoute("research", recherche, min(recherche * minutes["research"], RESEARCH_CAP_PER_RUN))
    return total, tally

