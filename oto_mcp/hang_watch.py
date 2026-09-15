"""Détecte un BLOCAGE de l'event loop et journalise la VRAIE pile qui bloque —
complète `loop_watch.py`, qui journalise APRÈS coup et sans dire OÙ.

`loop_watch.enable()` (aiodebug.log_slow_callbacks) chronomètre un callback et
n'appelle `on_slow_callback` qu'APRÈS son exécution : au moment où il tourne, la pile
qui bloquait s'est déjà déroulée — un `sys._current_frames()` là ne montrerait que la
sonde elle-même, jamais le coupable. `aiodebug.hang_inspection` (même lib, jamais
branché ici) résout le problème par un THREAD SÉPARÉ qui surveille un timestamp
partagé, mis à jour par une tâche minuscule de la boucle : si le délai dépasse le
seuil, la boucle est bloquée EN CE MOMENT, et c'est ce thread — qui tourne PENDANT le
blocage, pas après — qui peut dumper la vraie pile.

Adapté ici, sur le même patron mais pas la même sortie :
- **journalise** (logger `oto_mcp.loop`, comme `loop_watch.py`) plutôt qu'écrire des
  fichiers `stacktrace-*.txt` sur la box ;
- filtre au **seul thread principal** (celui qui fait tourner la boucle asyncio —
  les autres threads du process sont le threadpool, jamais la boucle elle-même : les
  dumper coûterait et bruiterait sans rien dire du gel) ;
- **aucune variable locale** (`traceback.extract_stack`, jamais `format_exc` ni rien
  qui inspecterait une frame plus profondément) — même famille de risque que le fix
  Sentry du 2026-09-15 (#564, `include_local_variables=False`) : un secret déchiffré
  qui traînerait dans une frame ne doit jamais atteindre un journal ;
- **débit limité** (nuit du 14-15/09 : une salve de 6 blocages courts en ~1 min suite
  à une bascule) : UN dump par ÉPISODE de blocage — un état armé tant que le délai
  reste dépassé, réarmé proprement à la résorption — plus un plafond par minute toutes
  causes confondues, pour qu'une salve ne remplisse pas le journal de dumps redondants
  du même incident sans rien ajouter au premier ;
- **coupé au process, pas au déploiement** : `OTO_HANG_WATCH_ENABLED` (défaut actif)
  coupe le mécanisme par un redémarrage, sans redéployer. Le mécanisme est la réponse
  directe à des gels de production dont la cause reste non identifiée
  (`docs/event-loop-perf.md`) — le désactiver par défaut reviendrait à se priver de la
  preuve le jour où elle sert. Actif par défaut est défendable seulement parce que le
  coût, lui, est mesuré (pas supposé) : `tests/test_hang_watch.py` chiffre le coût de
  la tâche qui bat le timestamp (~0,1 µs/appel — négligeable). Le coût du thread
  watchdog lui-même SUIT LA FRÉQUENCE DE RÉVEIL — pas du bruit de machine, un coût
  réel et attendu du mécanisme à haute fréquence : mesuré dans le test à un réveil
  de 10 ms (réveil toutes les 5 ms, `interval/2`) : **12,7 % de CPU pour le thread
  sur 10s, et le débit de la boucle perd 9 %**. Au seuil réel de prod
  (`OTO_SLOW_CALLBACK_WARN=1.0s`, réveil toutes les 500 ms — 100× moins fréquent),
  isolé (`/proc/self/task/<tid>/stat`, fenêtre de 180s, 360 réveils, demande de la
  session de déploiement le 15/09/2026) : **0,020s CPU sur 180s, soit 0,011 % d'un
  cœur**. Sans commune mesure avec le coût d'un aller-retour réseau ou DB qu'un vrai
  handler paierait de toute façon. Le nom suit le patron déjà en place pour les
  boucles de fond
  (`OTO_SCHEDULER_ENABLED`, `OTO_BILLING_RUNNER_ENABLED`… — `boucles_de_fond.py`),
  pas celui de `OTO_SLOW_CALLBACK_WARN`/`_SENTRY` (des seuils, pas des interrupteurs).

Le seuil de déclenchement reste `OTO_SLOW_CALLBACK_WARN` (déf. 1.0s) — même sonde,
même vocabulaire de "lent", pas un second seuil à régler séparément.

⚠️ La sonde ne casse JAMAIS la boucle qu'elle observe (même principe que
`loop_watch.py`) : toute écriture de log est enveloppée dans un `try/except` qui ne
peut jamais lever, et la tâche côté boucle qui bat le timestamp ne fait rien d'autre
qu'écrire un flottant avant de redormir.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import threading
import time
import traceback

logger = logging.getLogger("oto_mcp.loop")

OTO_HANG_WATCH_ENABLED = "OTO_HANG_WATCH_ENABLED"

# Plafond de dumps par minute, toutes causes confondues. Pas exposé par variable
# d'env (contrairement à `OTO_HANG_WATCH_ENABLED`) : ce n'est pas un besoin exprimé,
# et chaque interrupteur de plus est une surface de plus à documenter et à retenir.
# 3 : assez pour voir si une salve porte plusieurs causes distinctes (les dumps
# suivants ne répètent que le comptage, jamais le texte), pas assez pour qu'une
# salve de blocages courts et rapprochés (6 en ~1 min, vécu la nuit du 14-15/09)
# remplisse le journal de piles redondantes du même incident.
_MAX_DUMPS_PER_MIN = 3


def enabled() -> bool:
    """Lu au démarrage (redémarrage du process pour changer d'avis, pas de redéploi).
    Absent ou toute valeur ≠ "0" ⟹ actif — fail-open vers le comportement utile, jamais
    une erreur de boot pour une variable mal orthographiée. Même convention que les
    interrupteurs de boucles de fond (`boucles_de_fond._interrupteur`)."""
    return os.environ.get(OTO_HANG_WATCH_ENABLED, "1") != "0"


class HangWatch(threading.Thread):
    """Thread démon : observe un timestamp partagé (mis à jour par `beat()`, appelé
    depuis la boucle) et journalise — au plus une fois par ÉPISODE de blocage, borné
    en plus par minute — la pile du thread PRINCIPAL pendant qu'elle bloque
    réellement.

    `_last_beat` est une liste à un élément (pas un flottant nu) : Python la passe
    par RÉFÉRENCE, donc `beat()` (thread de la boucle) et `_check()` (ce thread) voient
    la même case — même patron qu'`aiodebug.hang_inspection`. Aucun verrou : sous le
    GIL, une écriture/lecture d'un flottant dans une liste ne se déchire pas."""

    def __init__(self, interval: float, max_dumps_per_min: int = _MAX_DUMPS_PER_MIN) -> None:
        super().__init__(daemon=True, name="oto-hang-watch")
        self._interval = interval
        self._max_dumps_per_min = max_dumps_per_min
        self._last_beat = [time.monotonic()]
        self._stop_event = threading.Event()
        self._episode_open = False
        self._episode_started_at = 0.0
        self._dump_times: list[float] = []
        self._main_thread_id = threading.main_thread().ident

    def beat(self) -> None:
        """Appelé depuis la boucle asyncio — coût : une écriture de flottant."""
        self._last_beat[0] = time.monotonic()

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:  # thread démon, jamais join()
        # `Event.wait` relâche le GIL pendant l'attente (contrairement à `time.sleep`
        # sous charge du GIL, mais les deux le font) : le coût de ce thread AU REPOS
        # est le réveil périodique, pas l'attente elle-même.
        while not self._stop_event.wait(self._interval / 2):
            self._check()

    def _check(self) -> None:
        now = time.monotonic()
        delay = now - self._last_beat[0]
        if delay > self._interval:
            if not self._episode_open:
                self._episode_open = True
                self._episode_started_at = now
                self._maybe_dump(delay)
            # sinon : même épisode, déjà dumpé — on attend la résorption
            return
        if self._episode_open:
            self._episode_open = False
            duration = now - self._episode_started_at
            self._safe_log(logging.WARNING, "event loop débloquée après %.1fs", duration)

    def _maybe_dump(self, delay: float) -> None:
        """`delay` est mesuré depuis le dernier `beat()` — qui a lieu toutes les
        `interval/2` — pas depuis le début réel d'un éventuel blocage : le vrai
        blocage a pu commencer jusqu'à `interval/2` APRÈS le `delay` rapporté ici.
        D'où le libellé « sans battement depuis » plutôt que « bloquée » : ce
        nombre peut SURESTIMER le temps de blocage réel, jusqu'à `interval/2`
        (0,5s au seuil de prod) — ce n'est pas une mesure exacte du blocage, juste
        de l'absence de battement."""
        now = time.monotonic()
        self._dump_times = [t for t in self._dump_times if now - t < 60.0]
        if len(self._dump_times) >= self._max_dumps_per_min:
            self._safe_log(
                logging.WARNING,
                "event loop sans battement depuis %.1fs — dump ignoré (plafond de "
                "%d/min atteint, probablement la même salve)",
                delay, self._max_dumps_per_min,
            )
            return
        self._dump_times.append(now)
        stack = self._main_thread_stack_text()
        self._safe_log(
            logging.WARNING,
            "event loop sans battement depuis %.1fs — pile du thread principal:\n%s",
            delay, stack,
        )

    def _main_thread_stack_text(self) -> str:
        frame = sys._current_frames().get(self._main_thread_id)
        if frame is None:
            return "(thread principal introuvable dans sys._current_frames())"
        lines: list[str] = []
        # extract_stack ne rend jamais les variables locales (capture_locals=False,
        # son défaut) — fichier/ligne/fonction/texte de la ligne, rien de plus.
        for filename, line_no, name, text in traceback.extract_stack(frame):
            lines.append(f'  File "{filename}", line {line_no}, in {name}')
            if text:
                lines.append(f"    {text}")
        return "\n".join(lines)

    @staticmethod
    def _safe_log(level: int, msg: str, *args: object) -> None:
        try:
            logger.log(level, msg, *args)
        # noqa: SILENT — la sonde de boucle ne casse jamais la boucle qu'elle observe
        except Exception:
            pass


async def run_heartbeat_loop() -> None:
    """La boucle elle-même — déclarée dans `boucles_de_fond.BOUCLES` (`tiers=False` :
    aucun drain de travail en base, aucun effet chez un tiers, donc aucun risque de
    doublon prod/preprod) : elle tourne dans TOUS les environnements, y compris la
    préprod, exactement là où le prochain gel non identifié peut survenir. Son propre
    interrupteur (`enabled()`) vit ici, pas dans `boucles_de_fond.py`, qui se contente
    de le lire (`_hang_watch_armee`).

    Démarre le thread watchdog à l'entrée, l'arrête proprement à l'annulation
    (`finally`). Le corps de la boucle ne fait rien d'autre que battre le timestamp
    puis redormir — c'est la légèreté de CE geste qui est mesurée par
    `tests/test_hang_watch.py`, pas supposée."""
    try:
        interval = float(os.environ.get("OTO_SLOW_CALLBACK_WARN", "1.0") or "1.0")
        watch = HangWatch(interval=interval)
        watch.start()
    except Exception:
        logger.warning("hang watch : échec du démarrage du watchdog", exc_info=True)
        return
    logger.info(
        "hang watch actif (seuil %.1fs, plafond %d dump/min)",
        interval, _MAX_DUMPS_PER_MIN,
    )
    try:
        while True:
            watch.beat()
            await asyncio.sleep(interval / 2.0)
    finally:
        watch.stop()
