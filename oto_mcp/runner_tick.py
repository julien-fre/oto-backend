"""Le tick des déclencheurs — une horloge qui ENFILE, jamais qui exécute (R3).

Le patron exact du scheduler d'emails : une boucle de fond au lifespan, le
travail par tranche en `asyncio.to_thread` (le serveur est mono-loop), un tick
raté ne tue jamais la boucle. Ce que le tick fait d'une échéance : gagner le
compare-and-swap (prod et preprod partagent la base — deux ticks, UN gagnant
par échéance), enfiler un job `start`, recalculer la prochaine échéance. C'est
tout. L'exécution appartient au worker (`oto-runner`), qui claime quand il veut.

Le calcul d'échéance vit ICI, à un seul endroit : `next_due(cron, tz)` — croniter
évalue DANS le fuseau du déclencheur (l'heure d'été ne décale pas une veille en
silence), et rend un instant UTC-aware que PG stocke tel quel.
"""
from __future__ import annotations

import asyncio
import datetime
import logging
import os
from zoneinfo import ZoneInfo

from croniter import croniter

from . import db

log = logging.getLogger(__name__)

_POLL_INTERVAL_S = 60

# Deux échéances consécutives plus proches que ça = un cron d'arrosage, pas un
# déclencheur de runs (chaque run coûte des tours de modèle). Refusé à la pose.
MIN_SPACING_S = 300


def validate_cron(expr: str, tz: str) -> None:
    """Lève ValueError si l'expression ou le fuseau ne tiennent pas la route —
    le message nomme le fautif (le refus muet est interdit de séjour)."""
    try:
        zone = ZoneInfo(tz)
    except Exception:
        raise ValueError(f"fuseau inconnu : `{tz}` (forme attendue : Europe/Paris)")
    if not croniter.is_valid(expr):
        raise ValueError(f"expression cron invalide : `{expr}` (5 champs, "
                         "ex. `5 6 * * *` = tous les jours à 6h05)")
    base = datetime.datetime.now(zone)
    it = croniter(expr, base)
    d1 = it.get_next(datetime.datetime)
    d2 = it.get_next(datetime.datetime)
    if (d2 - d1).total_seconds() < MIN_SPACING_S:
        raise ValueError(
            f"cadence trop serrée : deux échéances à {(d2 - d1).total_seconds():.0f}s "
            f"d'écart pour un plancher de {MIN_SPACING_S}s — un run n'est pas un ping")


def next_due(expr: str, tz: str,
             apres: datetime.datetime | None = None) -> datetime.datetime:
    """La prochaine échéance APRÈS `apres` (défaut : maintenant), évaluée dans le
    fuseau du déclencheur, rendue tz-aware."""
    zone = ZoneInfo(tz)
    base = (apres or datetime.datetime.now(datetime.timezone.utc)).astimezone(zone)
    return croniter(expr, base).get_next(datetime.datetime)


def _tick() -> int:
    """UN tour d'horloge. Rend le nombre de jobs enfilés (télémétrie du log)."""
    enfiles = 0
    for t in db.due_triggers():
        try:
            prochaine = next_due(t["cron"], t["tz"])
        except Exception as e:  # noqa: BLE001 — un cron devenu invalide (édité à la
            # main ?) ne doit pas bloquer les AUTRES déclencheurs du tour
            log.warning("déclencheur %s : cron inévaluable (%s)", t["id"], e)
            continue
        # Le CAS d'abord : si un tick concurrent (l'autre environnement, même base)
        # a déjà consommé cette échéance, on passe sans enfiler.
        if not db.consume_due(t["id"], t["next_due"], prochaine):
            continue
        payload = {
            "procedure": t["procedure"],
            "project_id": t["project_id"],
            "tools": t.get("tools") or [],
            "input": t.get("input"),
            "label": t.get("label") or f"planifié — {t['procedure']}",
            "max_steps": t.get("max_steps"),
            "trigger_id": t["id"],
        }
        db.enqueue_job(t["org_id"], "start",
                       payload={k: v for k, v in payload.items() if v is not None})
        enfiles += 1
    return enfiles


async def run_runner_tick_loop(interval: int = _POLL_INTERVAL_S) -> None:
    """Boucle de fond : enfile les déclencheurs dus. Ne meurt jamais sur un tick."""
    log.info("tick du runner démarré (intervalle %ss)", interval)
    while True:
        try:
            n = await asyncio.to_thread(_tick)
            if n:
                log.info("tick runner : %d job(s) enfilé(s)", n)
        except asyncio.CancelledError:
            log.info("tick runner arrêté")
            raise
        except Exception as e:  # noqa: BLE001 — un tick raté ne tue pas la boucle
            log.warning("tick runner échoué : %s", e)
        await asyncio.sleep(interval)


def enabled() -> bool:
    return os.environ.get("OTO_RUNNER_TICK_ENABLED", "1") != "0"
