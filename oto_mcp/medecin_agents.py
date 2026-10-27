"""Soigner un agent hébergé AVANT que son titulaire ne le remarque (27/10/2026).

L'alerte au titulaire (`alerte_agents_bloques.py`) annonce une panne au troisième
travail mort. Or relevé sur une semaine de production, presque toutes les pannes
étaient les NÔTRES — aucun worker ne servait la famille du modèle, un refus passager
du fournisseur, un défaut du runner qui conclut en échec un run réussi — et le
titulaire recevait un courriel sur une panne qu'il ne pouvait pas réparer.

Cette boucle passe toutes les cinq minutes, AVANT l'alerte :

1. elle RELÈVE les agents allumés qui ont un travail mort depuis leur dernier succès
   (`db/episodes_agents.pannes_en_cours`), dès le premier ;
2. elle RANGE chaque panne dans une classe (`classer`) qui dit QUI peut la réparer
   (`plateforme`, `proprietaire`, `inconnu`), et tient l'épisode (`agent_episodes`) ;
3. elle SOIGNE ce qui se soigne sans personne — et seulement ça :
   - un refus PASSAGER (`refus_passager`) : le travail mort est relancé une fois ;
   - une famille de modèle que personne ne servait : relancé une fois, et seulement
     quand un worker vivant la sert de nouveau pour cette org ;
   jamais plus de `RELANCES_MAX` relances par épisode, une seule par travail mort, et
   jamais quand une occurrence de l'agent attend déjà dans la file ;
4. elle CLÔT les épisodes guéris (un succès après l'ouverture) ou dont l'agent a été
   éteint.

Ce qu'elle ne fait JAMAIS : toucher une clé, une identité, une procédure, un horaire,
ou une donnée ; relancer une panne qu'elle ne sait pas nommer (un run qui a agi avant de
mourir referait ses effets — courriels, écritures — une seconde fois). Un épisode du côté du TITULAIRE passe `escalade` : c'est l'alerte existante qui le lui dit.
Un épisode `inconnu` reste `ouvert` pour l'agent médecin (`oto_admin_agent_repair`).

⚠️ Une relance est TENUE par la règle de péremption (#814) : si aucun worker ne la prend
avant l'occurrence suivante, le tick la périme. Relancer ne fabrique donc jamais
d'arriéré.

⚠️ L'alerte au titulaire se TAIT pour un épisode du côté plateforme
(`episodes_agents.agents_soignes_par_nous`) : le titulaire n'y peut rien. Une panne
plateforme qui ne guérit pas se lit dans `oto_admin_agent_repair op=list` et au journal,
pas dans la boîte du client.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

log = logging.getLogger("oto_mcp.medecin_agents")

#: La cadence : cinq minutes, trois fois plus serrée que l'alerte (quinze), pour qu'un
#: travail mort soit relancé avant que le troisième n'ouvre le courriel.
INTERVALLE_S = 300

#: Au plus deux relances par épisode. Au-delà, ce qui échoue encore n'est plus passager.
RELANCES_MAX = 2

#: Un travail mort plus vieux que ça ne se relance plus : son occurrence est passée.
AGE_MAX = timedelta(hours=24)

ACTEUR = "medecin"

# Les classes, dans l'ordre où on les essaie : (classe, côté, motif reconnu).
# ⚠️ `fin_anormale (success)` n'est PAS un défaut du runner : c'était un refus du
# fournisseur (`is_error` sous un `subtype` « success ») mal libellé par l'ancien runner,
# corrigé depuis. Il tombe en `inconnu`, jamais en relance automatique.
_CLASSES: tuple[tuple[str, str, "re.Pattern[str]"], ...] = (
    ("famille_non_servie", "plateforme", re.compile(
        r"ne tournent|aucun worker ne sert|aucun agent ne dessert", re.I)),
    ("cle_refusee", "proprietaire", re.compile(
        r"cle_sans_workspace|clé d'api invalide|invalid[ _]x-api-key|invalid_api"
        r"|authenticat|unauthori[sz]ed|forbidden|\bHTTP 40[13]\b|insufficient"
        r"|quota|crédit|credit|billing", re.I)),
    ("identite", "proprietaire", re.compile(
        r"identité invalide|ne nomme personne", re.I)),
)


def classer(motif: Optional[str]) -> tuple[str, str]:
    """`(classe, côté)` d'un motif d'échec. Le refus passager se lit par la règle de la
    file (`db.runner_jobs.refus_passager`), la même qui règle son recul : un motif que
    la file tient pour passager l'est ici aussi."""
    from .db.runner_jobs import refus_passager
    texte = motif or ""
    for classe, cote, motif_re in _CLASSES:
        if motif_re.search(texte):
            return classe, cote
    if refus_passager(texte):
        return "refus_passager", "plateforme"
    return "inconnu", "inconnu"


def _deja_relance(episode: dict, job_id) -> bool:
    return any(r.get("action") == "relance" and r.get("job_id") == job_id
               for r in (episode.get("reparations") or []))


def _famille_servie(panne: dict) -> bool:
    from . import db, runner_models
    famille = runner_models.famille(panne.get("model"))
    if not famille:
        return False
    return famille in (db.runner_arme(int(panne["org_id"])).get("families") or [])


def relancer(panne: dict, episode: dict, *, acteur: str = ACTEUR,
             maintenant: Optional[datetime] = None) -> Optional[str]:
    """Relance le dernier travail mort de l'agent, sous toutes les gardes. Rend None si
    la relance est partie, sinon la raison du refus — dite, jamais devinée par
    l'appelant. Partagé par la boucle et par l'agent médecin."""
    from . import db
    from .db import episodes_agents

    maintenant = maintenant or datetime.now(timezone.utc)
    job_id = panne.get("job_id")
    if job_id is None:
        return "aucun travail mort à relancer"
    if _deja_relance(episode, job_id):
        return "ce travail a déjà été relancé"
    fini = panne.get("dernier")
    if fini is not None and maintenant - fini > AGE_MAX:
        return "travail mort trop ancien : son occurrence est passée"
    file = db.file_du_declencheur(int(panne["trigger_id"]), int(panne["org_id"]))
    if file.get("pending") or file.get("held"):
        return "une occurrence de l'agent attend déjà dans la file"
    # La trace d'abord, sous garde : deux processus sur le même épisode, un seul relance.
    if episodes_agents.noter_reparation(
            int(episode["id"]), action="relance", acteur=acteur, job_id=job_id,
            detail=f"relance du travail {job_id} ({episode.get('classe')})",
            maximum=RELANCES_MAX) is None:
        return "plafond de relances atteint, ou épisode clos"
    db.enqueue_job(int(panne["org_id"]), panne.get("kind") or "start",
                   payload=dict(panne.get("payload") or {}), sub=panne.get("sub"))
    return None


def _soigner(panne: dict, episode: dict, maintenant: datetime) -> Optional[str]:
    """Le geste automatique pour cette panne, ou None. Rend l'action faite."""
    from .db import episodes_agents

    classe, statut = episode["classe"], episode["statut"]
    if episode["cote"] == "proprietaire":
        if statut != "escalade":
            episodes_agents.poser(int(episode["id"]), statut="escalade")
        return None
    if classe == "refus_passager" or (classe == "famille_non_servie"
                                       and _famille_servie(panne)):
        refus = relancer(panne, episode, maintenant=maintenant)
        if refus is None:
            return "relance"
        if "plafond" in refus and statut != "escalade":
            episodes_agents.poser(int(episode["id"]), statut="escalade",
                                  diagnostic=f"{RELANCES_MAX} relances sans guérison")
            log.warning("médecin des agents : agent %s (org %s) toujours en panne après "
                        "%d relances — %s", panne["trigger_id"], panne["org_id"],
                        RELANCES_MAX, classe)
    return None


def balayer(maintenant: Optional[datetime] = None) -> dict:
    """Un passage : clôt les guéris, relève et range les pannes, soigne ce qui se soigne.
    Synchrone (la boucle le pousse hors de l'event loop). Une panne qui casse ne
    bloque pas les suivantes."""
    from .db import episodes_agents

    maintenant = maintenant or datetime.now(timezone.utc)
    gueris = episodes_agents.clore_les_gueris()
    pannes, relances, erreurs = 0, 0, 0
    for panne in episodes_agents.pannes_en_cours():
        pannes += 1
        try:
            classe, cote = classer(panne.get("motif"))
            episode = episodes_agents.ouvrir_ou_rafraichir(
                org_id=panne["org_id"], trigger_id=panne["trigger_id"], classe=classe,
                cote=cote, motif=panne.get("motif"), job_id=panne.get("job_id"),
                travaux=panne.get("travaux") or 0)
            if _soigner(panne, episode, maintenant) == "relance":
                relances += 1
        except Exception as e:  # noqa: BLE001 — une panne illisible ne bloque pas les autres
            erreurs += 1
            log.warning("médecin des agents : agent %s illisible (%s)",
                        panne.get("trigger_id"), e)
    return {"pannes": pannes, "relances": relances, "gueris": len(gueris),
            "gueris_apres_soin": sum(1 for g in gueris if g.get("reparations")),
            "erreurs": erreurs}


async def run_medecin_agents_loop(interval: int = INTERVALLE_S) -> None:
    """La boucle de fond (`boucles_de_fond.py`, `tiers=True` : une relance refait agir un
    agent chez des tiers). Un passage raté ne tue pas la boucle — il le dit."""
    log.info("médecin des agents : démarré (passage toutes les %ss, %d relances max).",
             interval, RELANCES_MAX)
    while True:
        try:
            bilan = await asyncio.to_thread(balayer)
            if bilan.get("relances") or bilan.get("gueris") or bilan.get("erreurs"):
                log.info("médecin des agents : %s", bilan)
        except Exception as e:  # noqa: BLE001 — un passage raté ne tue pas la boucle
            log.warning("médecin des agents : passage en échec : %s", e)
        await asyncio.sleep(interval)
