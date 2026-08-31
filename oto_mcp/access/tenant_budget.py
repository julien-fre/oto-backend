"""Le BUDGET PAR ORG d'une clé de tenant — l'arête tenant→org de 0053 (L-clés PR 2).

R10, tranché le 12/08 : budget PARTAGÉ — la lettre de D7. Le compteur est celui de
l'arête `tenant:{slug}:{connecteur} —grant→ org:{id}` (`grant_counters`), sommé sur la
fenêtre du jour pour le couple (instance, bénéficiaire) : tous les membres de l'org
puisent au même budget, et c'est voulu.

**Trois états, hérités du lot L5** (`grants_chain.tenant_rung`) :
- MUETTE (aucune arête n'a jamais visé cette org) → rien à borner, rien à débiter :
  la clé sert comme en PR 1 — c'est l'inertie promise ;
- ACCORDE → la contrainte `quota` de l'arête borne le jour (0 ou absente = illimité,
  convention de l'ancien chemin) et l'arête est débitée ;
- REFUSE (toutes révoquées) → le walker a déjà SAUTÉ le barreau, on n'arrive pas ici.

⚠️ **Débité à la RÉSOLUTION, pas au succès de l'appel** — à la différence du compteur
plateforme, que chaque outil débite lui-même après un appel réussi
(`access.record_platform_usage`, ~10 sites). Une clé de tenant n'a pas ces sites, et
en ajouter un par outil serait précisément la copie que le walker unique existe pour
éviter. Le prix : un appel qui échoue chez le fournisseur compte. L'alternative —
une borne posée que personne ne débite — est le défaut de #409 (une ligne acceptée
que rien ne lit), et il est pire. Le déplacement vers « au succès » passera par le
relevé d'appel du middleware, avec L8.
"""
from __future__ import annotations

from ..mcp_errors import McpError
from mcp.types import ErrorData, INVALID_PARAMS

from .. import grants_chain
from ..db import grants as db_grants


def enforce(slug: str, provider: str, org: "int | None") -> None:
    """Applique le budget de l'arête tenant→org pour CET appel : lève si le jour est
    épuisé, débite sinon. No-op (aucune lecture) sans arête."""
    verdict = grants_chain.tenant_rung(slug, provider, org)
    if verdict is None or not verdict.granted or verdict.grant_id is None:
        return
    if verdict.quota:
        used = db_grants.counter_sum_today(verdict.resource_id, "org", str(org))
        if used >= verdict.quota:
            raise McpError(ErrorData(
                code=INVALID_PARAMS,
                message=(
                    f"Budget de la clé `{provider}` du tenant `{slug}` épuisé aujourd'hui "
                    f"pour cette org ({used}/{verdict.quota}). Le budget est partagé par "
                    f"toute l'org ; son admin de tenant peut le relever."
                )))
    db_grants.bump_counter(verdict.grant_id, 1)
