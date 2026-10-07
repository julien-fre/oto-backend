"""Les exécutions PROGRAMMÉES d'une recette : une boucle de fond, sans modèle et sans
requête, au nom de qui a posé le programme.

**Production seule, éteinte par défaut.** La boucle agit chez des tiers (connecteurs
payants, CRM) : elle est déclarée `tiers=True` (`boucles_de_fond.py`), donc jamais en
préprod, et elle ne démarre que si `OTO_RECIPE_SCHEDULER_ENABLED=1`.

**L'identité est revérifiée à CHAQUE passage** : le compte existe, n'est pas en pause ni
coupé avec son tenant, a toujours un rôle dans l'org du programme, et l'org n'est pas
suspendue. Sinon le programme est suspendu, et la raison écrite dans son dernier reçu.
L'org est épinglée explicitement (`session_org.set_call_org`) : la clé, la facturation
et le journal suivent l'org du programme.

**Un passage enchaîne les reprises** (pages, parents, lignes) jusqu'à `PASSAGE_S`, sous
le bail du tableau. Son reçu — des comptes et des codes — est gardé sur le programme ;
`ECHECS_MAX` échecs d'affilée le suspendent.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Optional

from starlette.concurrency import run_in_threadpool

log = logging.getLogger(__name__)

INTERVALLE_S = 60
PASSAGE_S = 300
ECHECS_MAX = 3
#: Les arrêts d'un passage qui ne sont PAS des échecs : il reprendra au suivant.
_ARRETS_NORMAUX = frozenset({None, "job_submitted", "job_running", "job_starting",
                             "max_pages", "max_parents", "max_rows", "time_budget",
                             "spend_cap", "run_in_progress", "server_not_ready"})
_REPRISES = frozenset({"max_pages", "max_parents", "max_rows", "time_budget"})


def armee() -> bool:
    """Éteinte par défaut : un programme agit sans personne devant l'écran."""
    return os.environ.get("OTO_RECIPE_SCHEDULER_ENABLED", "0") == "1"


def identite_invalide(sub: str, org_id: Optional[int]) -> Optional[str]:
    """Pourquoi ce compte ne peut plus agir pour ce programme — ou None."""
    from .. import db, garde_identite, org_store
    if db.get_user(sub) is None:
        return "account_gone"
    if garde_identite.refus(sub):
        return "account_paused"
    if org_id is not None:
        if org_store.get_org_role(org_id, sub) is None:
            return "no_longer_member"
        if org_store.get_org_suspension(org_id):
            return "org_suspended"
    return None


async def passer(prog: dict) -> dict:
    """UN passage d'un programme. Rend son reçu (comptes et codes)."""
    from .. import session_org, tool_registry
    from ..auth.hooks import sub_override
    from ..db import recipes as db_recipes
    from . import contrat, moteur
    raison = await run_in_threadpool(identite_invalide, prog["sub"], prog["org_id"])
    if raison:
        return {"stopped": "schedule_identity_invalid", "reason": raison, "fatal": True}
    fiche = await run_in_threadpool(db_recipes.get_recipe_by_id, prog["recipe_id"])
    numero = (fiche or {}).get("published_version")
    if numero != prog["version"]:
        # Une autre version publiée depuis — peut-être par un autre membre — ne tourne
        # jamais sous l'identité de qui a posé le programme : il le repose, s'il la veut.
        return {"stopped": "version_changed", "fatal": True}
    version = await run_in_threadpool(db_recipes.get_version, prog["recipe_id"], numero)
    if version is None:
        return {"stopped": "not_published", "fatal": True}
    corps = version["body"]
    try:
        params = contrat.params_resolus(corps, prog.get("params") or {})
    except contrat.RecetteInvalide as e:
        return {"stopped": "invalid_params", "problems": e.problemes, "fatal": True}
    fastmcp = tool_registry.bound_instance()
    if fastmcp is None:
        return {"stopped": "server_not_ready"}
    fin = time.monotonic() + PASSAGE_S
    reprise, recu, appels = None, {}, 0
    with sub_override(prog["sub"]):
        jeton = session_org.set_call_org(prog["org_id"]) if prog["org_id"] else None
        try:
            while True:
                appels += 1
                try:
                    recu = await moteur.executer(
                        corps, params, fastmcp=fastmcp, sub=prog["sub"],
                        datastore=prog["datastore"], reprise=reprise, ecrire=True,
                        temoin=version.get("test_report"),
                        cle_travail=(prog["recipe_id"], moteur.empreinte(params)))
                except moteur.RecetteRefusee as e:
                    return {"stopped": e.code}
                if recu.get("stopped") in _REPRISES and recu.get("resume") \
                        and time.monotonic() < fin:
                    reprise = recu["resume"]
                    continue
                break
        finally:
            if jeton is not None:
                session_org.reset_call_org(jeton)
    recu = {k: v for k, v in recu.items() if k != "resume"}
    recu["calls_in_pass"] = appels
    return recu


async def _un_tour() -> int:
    from ..db import recipes as db_recipes
    faits = 0
    while (prog := await run_in_threadpool(db_recipes.prendre_programme_du)) is not None:
        try:
            recu = await passer(prog)
        except Exception as e:  # noqa: BLE001 — un programme en panne ne tue pas la boucle
            log.warning("programme de recette %s en échec : %s", prog["id"], type(e).__name__)
            recu = {"stopped": "internal_error"}
        echec = recu.get("stopped") not in _ARRETS_NORMAUX
        await run_in_threadpool(db_recipes.noter_passage, prog["id"], recu=recu,
                                echec=echec, seuil=1 if recu.get("fatal") else ECHECS_MAX)
        faits += 1
    return faits


async def run_loop(interval: int = INTERVALLE_S) -> None:
    """Boucle de fond : exécute les programmes échus. Ne meurt jamais sur un tour."""
    log.info("programmes de recettes : boucle démarrée (intervalle %ss)", interval)
    while True:
        try:
            n = await _un_tour()
            if n:
                log.info("programmes de recettes : %d passage(s)", n)
        except asyncio.CancelledError:
            log.info("programmes de recettes : boucle arrêtée")
            raise
        except Exception as e:  # noqa: BLE001 — un tour raté ne tue pas la boucle
            log.warning("programmes de recettes : tour échoué : %s", type(e).__name__)
        await asyncio.sleep(interval)
