"""Le rappel du contexte d'org dans les réponses d'outils (oto-backend#1041).

Le README d'une org (`oto_guide scope=org`) est servi dans le bloc C des `instructions`
du handshake — que claude.ai n'injecte pas au modèle, que Claude Code coupe vers 2 048
caractères, et qui sont FIGÉES à l'`initialize` (#478, #696). Le seul canal réel est
l'appel explicite à `oto_context`, que les agents font rarement : mesuré sur une org
cliente, un README réécrit n'a été reçu par personne.

D'où le rappel, au seul endroit dont la livraison est certaine — la réponse d'un outil :
tant que l'appelant n'a pas lu `oto_context` pour l'org de l'appel **depuis la dernière
modification** de son README (ou d'une couche qu'il cumule : équipe active, note de
l'utilisateur), chaque réponse porte en tête une ligne courte qui le lui dit. Elle
disparaît dès la lecture.

**Sans état de session** (ADR 0038) : le verdict se lit dans la base — la date de
modification des couches et le journal `tool_calls` (`db.contexte_non_lu`). Le « filet
de contexte » du 28/08, retiré le jour même, gardait un registre « déjà servi » en
mémoire ; ici, la mémoire n'est qu'un CACHE du verdict de la base, borné dans le temps
(`TTL_S`) et par la taille (`MAX_ENTREES`) — il ne décide rien que la base ne dise.
La seule écriture qu'il reçoit hors d'elle est la lecture qu'on vient de SERVIR
(`constater_lecture`) : la ligne du journal qui l'atteste part en tâche de fond, et
rappeler dans la seconde un contexte qui vient d'être lu serait faux.

Bornes (l'issue) :
- jamais sur `oto_context` lui-même ni sur un outil de pure identité (`EXEMPTS`) ;
- une org sans README ne déclenche rien ;
- jamais pour un travail du runner (jeton de délégation) : son allowlist est EXACTE et
  fixée par le travail — un rappel qu'il ne peut pas suivre serait répété à chaque
  appel, pur coût de fenêtre ;
- coût : une requête (index d'identité + `idx_tool_call_log_tool`) par `(compte, org)`
  et par `TTL_S` au plus, toujours HORS de la boucle (`middleware/rappel_contexte.py`) ;
- taille : une ligne, au plus `MAX_LIGNE` caractères (le nom d'org est coupé à
  `MAX_NOM`), servie tant que le contexte n'est pas lu — jamais le README lui-même.

⚠️ Canal texte seulement : un outil qui garde un canal STRUCTURÉ (schéma de sortie
déclaré, app) est lu par certains clients sur ce canal-là (`middleware/un_seul_canal`),
qui ne porte pas la ligne. C'est la minorité : la plupart des outils n'en ont plus.
"""
from __future__ import annotations

import threading
import time
from typing import Optional

# L'outil qui LIT le contexte : sa réussite éteint le rappel.
OUTIL_CONTEXTE = "oto_context"
# Jamais rappelé sur ces outils : la lecture elle-même, et l'identité pure.
EXEMPTS = frozenset({OUTIL_CONTEXTE, "oto_whoami"})

TTL_S = 60.0
MAX_ENTREES = 10_000
MAX_NOM = 60
MAX_LIGNE = 300

_cache: dict[tuple[str, int], tuple[float, Optional[str]]] = {}
_verrou = threading.Lock()


def ligne(nom_org: str, org_id: int) -> str:
    """La ligne servie — courte, actionnable, bornée (`MAX_LIGNE`). `_org=` y est écrit
    pour que la lecture atterrisse sous l'org de l'appel, qui peut ne pas être la maison."""
    nom = nom_org if len(nom_org) <= MAX_NOM else nom_org[:MAX_NOM - 1] + "…"
    return (f"⚠️ oto — contexte de l'org « {nom} » non lu, ou modifié depuis ta dernière "
            f"lecture : appelle `{OUTIL_CONTEXTE}` (`_org={org_id}`) avant de continuer, "
            "il porte ses règles de travail.")


def _couches(sub: str, org_id: int) -> list[tuple[str, str, str]]:
    """Les couches de README que `oto_context` cumule pour ce `(sub, org)` — org, équipe
    active, note de l'utilisateur — sous leur clé naturelle `(scope, owner, slug)`."""
    from . import access
    from .guide_store import INIT_SLUG
    couches = [("org", str(org_id), INIT_SLUG), ("user", sub, INIT_SLUG)]
    gid = access.current_group(sub)
    if gid is not None:
        couches.append(("group", str(gid), INIT_SLUG))
    return couches


def _verdict(sub: str, org_id: int) -> Optional[str]:
    """La ligne à servir pour `(sub, org)`, ou `None` — le cache d'abord, la base sinon.
    **Sync (DB)** : hors de la boucle seulement."""
    maintenant = time.monotonic()
    with _verrou:
        entree = _cache.get((sub, org_id))
    if entree is not None and entree[0] > maintenant:
        return entree[1]
    from . import db, tool_alias
    nom = db.contexte_non_lu(sub, org_id, _couches(sub, org_id))
    texte = None
    if nom is not None:
        texte = tool_alias.rewrite_prose(ligne(nom, org_id), tool_alias.prefix_for(sub))
    _retenir(sub, org_id, texte, maintenant)
    return texte


def _retenir(sub: str, org_id: int, texte: Optional[str], maintenant: float) -> None:
    with _verrou:
        if len(_cache) >= MAX_ENTREES:
            for cle in [k for k, (fin, _) in _cache.items() if fin <= maintenant]:
                del _cache[cle]
            if len(_cache) >= MAX_ENTREES:
                _cache.clear()
        _cache[(sub, org_id)] = (maintenant + TTL_S, texte)


def pour_l_appel(outil: str) -> tuple[Optional[tuple[str, int]], Optional[str]]:
    """`(clé (sub, org) de l'appel, ligne à servir)` pour l'appel MCP en cours.

    **Sync (DB) — via `run_in_threadpool`**, dans la portée du contexte d'appel (l'org
    de l'appel est celle que `CallContextMiddleware` a posée : `_org=`, projet, run).
    La clé est rendue même sans ligne : c'est elle que `constater_lecture` éteint
    quand l'outil est `oto_context`. `(None, None)` hors compte, hors org, ou pour un
    travail du runner."""
    from . import access
    from .auth import hooks
    sub = hooks.current_user_sub_from_token()
    if not sub or hooks.current_token_axes().get("token_kind") == "delegation":
        return None, None
    org_id = access.current_org(sub)
    if org_id is None:
        return None, None
    cle = (sub, int(org_id))
    if outil in EXEMPTS:
        return cle, None
    return cle, _verdict(*cle)


def constater_lecture(cle: tuple[str, int]) -> None:
    """`oto_context` vient d'être SERVI pour `(sub, org)` : le rappel s'éteint tout de
    suite, sans attendre que la ligne du journal (écrite en tâche de fond) soit visible.
    Mémoire seule — appelable depuis la boucle."""
    _retenir(cle[0], cle[1], None, time.monotonic())
