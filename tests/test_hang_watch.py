"""`oto_mcp/hang_watch.py` — le watchdog qui capture la VRAIE pile d'un blocage de
boucle, pendant qu'il a lieu (pas après, comme `loop_watch.py`).

Preuves, dans l'ordre de la tâche (oto-backend, gels de prod non identifiés,
`docs/event-loop-perf.md`) :

1. `test_capture_la_vraie_pile_pendant_un_vrai_blocage` — provoque un blocage
   RÉEL (`time.sleep` synchrone dans la boucle, pas une exception fabriquée) et
   vérifie que le dump journalisé montre la ligne du `time.sleep`.
2. `test_aucune_variable_locale_dans_le_dump` — la même capture ne montre AUCUNE
   variable locale, même quand la frame bloquante en porte une de forme
   sensible.
3. `test_run_normal_ne_declenche_jamais_le_dump` — un fonctionnement normal (la
   boucle reste occupée mais jamais bloquée au-delà du seuil) ne journalise
   jamais de dump : pas de faux positif.
4. `test_un_seul_dump_par_episode_de_blocage_continu` — un blocage continu, observé
   à plusieurs cycles de vérification, ne produit qu'UN dump (l'état d'épisode).
5. `test_episode_se_rearme_apres_resorption` — deux blocages séparés par une
   vraie résorption produisent bien DEUX dumps (le réarmement).
6. `test_plafond_par_minute_toutes_causes_confondues` — une salve d'épisodes
   courts et rapprochés (nuit du 14-15/09 : 6 en ~1 min) est plafonnée, au-delà
   du plafond le dump est refusé en le disant, jamais en silence.
7. `test_interrupteur_env_defaut_actif_et_desactivable` — `OTO_HANG_WATCH_ENABLED`
   : absent ou malformé ⟹ actif (fail-open vers l'utile), seul "0" désactive.
8. `test_env_invalide_ne_casse_pas_le_demarrage` — une valeur illisible de
   `OTO_SLOW_CALLBACK_WARN` ne fait PAS planter le lifespan du serveur : la
   tâche journalise un échec et rend la main.
9. `test_cout_du_battement_est_negligeable` — coût, MESURÉ, de `beat()` (la
   tâche côté boucle) : doit rester de l'ordre de la microseconde.
10. `test_cout_du_thread_watchdog_sous_charge_simulee` — coût, MESURÉ (pas
    supposé), du thread watchdog lui-même sous une charge qui garde la boucle
    occupée normalement (pas au repos total) : le réveil périodique du thread
    ne doit pas mesurablement ralentir le travail de la boucle.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import statistics
import threading
import time
import timeit

import pytest

from oto_mcp import hang_watch
from oto_mcp.hang_watch import HangWatch

LOGGER_NAME = "oto_mcp.loop"

# Seuil court pour que la suite reste rapide, avec assez de marge contre le bruit
# d'ordonnancement d'une CI chargée (le thread watchdog se réveille toutes les
# `interval/2` — plusieurs cycles de marge à chaque blocage provoqué ci-dessous).
_INTERVAL = 0.12
_BLOCK_S = 0.45   # > 3× l'intervalle : plusieurs cycles de vérification PENDANT le gel
_GAP_S = 0.4      # assez pour que la résorption soit vue et l'épisode réarmé

_SECRET_QUI_NE_DOIT_JAMAIS_PARAITRE = "sk-test-ne-doit-jamais-etre-loggue-000111"


def _stop_and_join(watch: HangWatch, timeout: float = 1.0) -> None:
    watch.stop()
    watch.join(timeout=timeout)
    assert not watch.is_alive(), "le thread watchdog n'a pas rendu la main"


def _bloque_avec_une_variable_secrete(duration: float) -> None:
    """Le callback bloquant provoqué par les tests — la ligne `time.sleep(duration)`
    est celle que le dump doit montrer ; `secret_local` est celle qu'il ne doit
    JAMAIS montrer, tout en étant bien VIVANTE dans la frame pendant le blocage."""
    secret_local = _SECRET_QUI_NE_DOIT_JAMAIS_PARAITRE
    time.sleep(duration)  # <-- la ligne attendue dans la pile capturée
    assert secret_local  # garde la variable "utilisée" aux yeux d'un lint éventuel


def _warning_texts(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == LOGGER_NAME]


@pytest.mark.asyncio
async def test_capture_la_vraie_pile_pendant_un_vrai_blocage(caplog):
    watch = HangWatch(interval=_INTERVAL)
    watch.start()
    try:
        with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
            _bloque_avec_une_variable_secrete(_BLOCK_S)
            await asyncio.sleep(_INTERVAL)  # laisse le watchdog écrire son dump
    finally:
        _stop_and_join(watch)

    dumps = [t for t in _warning_texts(caplog) if "pile du thread principal" in t]
    assert len(dumps) == 1, f"un seul dump attendu, {len(dumps)} obtenu(s) : {dumps}"
    assert "_bloque_avec_une_variable_secrete" in dumps[0]
    assert "time.sleep(duration)" in dumps[0]
    assert "test_hang_watch.py" in dumps[0]


@pytest.mark.asyncio
async def test_aucune_variable_locale_dans_le_dump(caplog):
    watch = HangWatch(interval=_INTERVAL)
    watch.start()
    try:
        with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
            _bloque_avec_une_variable_secrete(_BLOCK_S)
            await asyncio.sleep(_INTERVAL)
    finally:
        _stop_and_join(watch)

    dumps = [t for t in _warning_texts(caplog) if "pile du thread principal" in t]
    assert len(dumps) == 1
    assert _SECRET_QUI_NE_DOIT_JAMAIS_PARAITRE not in dumps[0], (
        "une variable locale a fuité dans le dump — traceback.extract_stack ne "
        "doit JAMAIS capturer de locals (même famille de risque que #564)"
    )
    assert "secret_local" not in dumps[0], "même le NOM de la variable ne doit pas fuiter"


@pytest.mark.asyncio
async def test_run_normal_ne_declenche_jamais_le_dump(caplog):
    """Pas de blocage : la boucle reste occupée (petit travail + `await` réguliers,
    jamais un repos total) pendant plusieurs multiples du seuil. Aucun WARNING
    de `hang_watch` ne doit apparaître — sinon le mécanisme mentirait à chaque
    campagne normale."""
    watch = HangWatch(interval=_INTERVAL)
    watch.start()

    async def heartbeat() -> None:
        while True:
            watch.beat()
            await asyncio.sleep(_INTERVAL / 2)

    hb = asyncio.create_task(heartbeat())
    try:
        with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
            deadline = time.monotonic() + _INTERVAL * 6
            while time.monotonic() < deadline:
                _ = sum(range(500))  # un peu de travail, pas un repos total
                await asyncio.sleep(0.01)
    finally:
        hb.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await hb
        _stop_and_join(watch)

    warnings = _warning_texts(caplog)
    assert warnings == [], f"faux positif : {warnings}"


@pytest.mark.asyncio
async def test_un_seul_dump_par_episode_de_blocage_continu(caplog):
    """Un blocage continu de plusieurs fois le seuil expose le timestamp figé à
    ~7-8 cycles de vérification (interval/2). Sans garde d'épisode, ça ferait
    autant de dumps ; avec, UN seul."""
    watch = HangWatch(interval=_INTERVAL)
    watch.start()
    try:
        with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
            time.sleep(_BLOCK_S * 2)  # ~7-8 cycles de vérification pendant le même gel
            await asyncio.sleep(_INTERVAL)
    finally:
        _stop_and_join(watch)

    dumps = [t for t in _warning_texts(caplog) if "pile du thread principal" in t]
    assert len(dumps) == 1, f"l'état d'épisode n'a pas tenu : {len(dumps)} dumps"


@pytest.mark.asyncio
async def test_episode_se_rearme_apres_resorption(caplog):
    watch = HangWatch(interval=_INTERVAL)
    watch.start()

    async def heartbeat() -> None:
        while True:
            watch.beat()
            await asyncio.sleep(_INTERVAL / 2)

    hb = asyncio.create_task(heartbeat())
    try:
        with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
            await asyncio.sleep(_INTERVAL)         # régime normal
            time.sleep(_BLOCK_S)                    # 1er blocage
            await asyncio.sleep(_GAP_S)              # résorption réelle, observée
            time.sleep(_BLOCK_S)                     # 2e blocage, distinct
            await asyncio.sleep(_INTERVAL)
    finally:
        hb.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await hb
        _stop_and_join(watch)

    warnings = _warning_texts(caplog)
    dumps = [t for t in warnings if "pile du thread principal" in t]
    resorptions = [t for t in warnings if "débloquée après" in t]
    assert len(dumps) == 2, f"le réarmement a échoué : {len(dumps)} dump(s) : {dumps}"
    # Une résorption après CHAQUE épisode (le 2e, laissé le temps de se résorber par le
    # dernier `await asyncio.sleep(_INTERVAL)`, se ferme lui aussi) — et surtout, la
    # PREMIÈRE doit être vue avant le 2e blocage, sans quoi le 2e dump ne prouverait rien
    # (l'épisode serait resté ouvert plutôt que réarmé).
    assert len(resorptions) == 2, f"résorptions inattendues : {resorptions}"


@pytest.mark.asyncio
async def test_plafond_par_minute_toutes_causes_confondues(caplog):
    """Salve de blocages courts et rapprochés (patron de la nuit du 14-15/09 :
    plusieurs gels en une minute) : au-delà du plafond, le dump est REFUSÉ en le
    disant — jamais avalé en silence, jamais répété non plus."""
    watch = HangWatch(interval=_INTERVAL, max_dumps_per_min=2)

    async def heartbeat() -> None:
        while True:
            watch.beat()
            await asyncio.sleep(_INTERVAL / 2)

    watch.start()
    hb = asyncio.create_task(heartbeat())
    try:
        with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
            for _ in range(4):
                time.sleep(_BLOCK_S)
                await asyncio.sleep(_GAP_S)
    finally:
        hb.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await hb
        _stop_and_join(watch)

    warnings = _warning_texts(caplog)
    dumps = [t for t in warnings if "pile du thread principal" in t]
    refuses = [t for t in warnings if "dump ignoré" in t]
    assert len(dumps) == 2, f"plafond non respecté côté dumps : {dumps}"
    assert len(refuses) == 2, f"les épisodes au-delà du plafond doivent se DIRE : {refuses}"
    assert all("plafond de 2/min" in t for t in refuses)


def test_interrupteur_env_defaut_actif_et_desactivable(monkeypatch):
    monkeypatch.delenv(hang_watch.OTO_HANG_WATCH_ENABLED, raising=False)
    assert hang_watch.enabled() is True, "absent ⟹ actif (fail-open vers l'utile)"

    monkeypatch.setenv(hang_watch.OTO_HANG_WATCH_ENABLED, "0")
    assert hang_watch.enabled() is False

    monkeypatch.setenv(hang_watch.OTO_HANG_WATCH_ENABLED, "1")
    assert hang_watch.enabled() is True

    monkeypatch.setenv(hang_watch.OTO_HANG_WATCH_ENABLED, "n'importe quoi")
    assert hang_watch.enabled() is True, "seul '0' désactive — pas de casse au boot"


@pytest.mark.asyncio
async def test_env_invalide_ne_casse_pas_le_demarrage(monkeypatch, caplog):
    """Une valeur illisible de OTO_SLOW_CALLBACK_WARN (le seuil réutilisé) ne doit
    jamais faire lever `run_heartbeat_loop` — sinon un env mal posé casserait le
    lifespan du serveur entier, pas seulement l'observabilité."""
    monkeypatch.setenv("OTO_SLOW_CALLBACK_WARN", "pas-un-nombre")
    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        await hang_watch.run_heartbeat_loop()  # ne doit PAS lever, doit juste rendre la main
    assert any("échec du démarrage" in r.getMessage() for r in caplog.records
               if r.name == LOGGER_NAME)


@pytest.mark.asyncio
async def test_run_heartbeat_loop_demarre_et_arrete_proprement_le_thread():
    """Cycle de vie réel : la tâche démarre le thread à l'entrée, l'arrête à
    l'annulation (`finally`) — pas de thread qui survit à sa tâche."""
    task = asyncio.create_task(hang_watch.run_heartbeat_loop())
    await asyncio.sleep(_INTERVAL / 2)  # laisse le thread démarrer et battre une fois
    watchdog_threads = [t for t in threading.enumerate() if t.name == "oto-hang-watch"]
    assert len(watchdog_threads) == 1, "le thread watchdog doit être démarré"
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    watchdog_threads[0].join(timeout=1.0)
    assert not watchdog_threads[0].is_alive(), "le thread watchdog doit s'arrêter avec sa tâche"


def test_cout_du_battement_est_negligeable():
    """Le geste côté boucle (`beat()`) doit être de l'ordre de la microseconde —
    c'est la légèreté annoncée par la docstring, ici MESURÉE, pas supposée."""
    watch = HangWatch(interval=1.0)
    n = 200_000
    elapsed = timeit.timeit(watch.beat, number=n)
    per_call_us = (elapsed / n) * 1_000_000
    print(f"\n[hang_watch] coût de beat() : {per_call_us:.3f} µs/appel (n={n})")
    # Marge large (µs très en dessous du budget d'une itération de boucle asyncio) :
    # ce test sanctionne une régression grossière, pas le bruit d'une CI partagée.
    assert per_call_us < 20.0, f"beat() a couté {per_call_us:.3f} µs/appel — inattendu"


@pytest.mark.asyncio
async def test_cout_du_thread_watchdog_sous_charge_simulee():
    """Coût du thread watchdog lui-même (le réveil `interval/2`, PAS `beat()`),
    sous une charge qui garde la boucle occupée normalement — pas au repos total,
    pour que le chiffre soit représentatif d'un serveur qui sert du trafic plutôt
    que d'un process qui attend. Intervalle resserré (10 ms) pour exagérer la
    fréquence de réveil du thread et rendre un éventuel coût MESURABLE plutôt que
    noyé dans le bruit à l'intervalle réel (0.5-1 s)."""

    async def charge_simulee(duration_s: float) -> int:
        iterations = 0
        deadline = time.monotonic() + duration_s
        while time.monotonic() < deadline:
            _ = sum(range(300))  # un petit travail CPU, comme un handler léger
            await asyncio.sleep(0)  # cède la main, comme un vrai tour de boucle
            iterations += 1
        return iterations

    duration = 0.5
    repeats = 3
    watchdog_interval = 0.01  # 10 ms : réveil du thread toutes les 5 ms

    sans_watchdog = [await charge_simulee(duration) for _ in range(repeats)]

    watch = HangWatch(interval=watchdog_interval)
    watch.start()

    async def heartbeat() -> None:
        while True:
            watch.beat()
            await asyncio.sleep(watchdog_interval / 2)

    hb = asyncio.create_task(heartbeat())
    try:
        avec_watchdog = [await charge_simulee(duration) for _ in range(repeats)]
    finally:
        hb.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await hb
        _stop_and_join(watch)

    base = statistics.median(sans_watchdog)
    avec = statistics.median(avec_watchdog)
    overhead_pct = (base - avec) / base * 100
    print(
        f"\n[hang_watch] itérations/{duration}s sans watchdog={sans_watchdog} "
        f"médiane={base:.0f} — avec watchdog (réveil {watchdog_interval*1000:.0f}ms, "
        f"y compris le battement)={avec_watchdog} médiane={avec:.0f} — "
        f"surcoût mesuré={overhead_pct:.1f}%"
    )
    # Seuil large : ce test sanctionne un coût mesurable (10-100 ms de dérive/s de
    # travail), pas le bruit ordinaire d'une CI partagée. Le battement lui-même est
    # inclus dans "avec watchdog" (coût déjà chiffré séparément ci-dessus) : ce test
    # isole surtout le réveil périodique du THREAD, qui prend le GIL sans travail
    # asyncio à faire pendant les 3/4 de la durée du test.
    assert overhead_pct < 20.0, (
        f"le watchdog a coûté {overhead_pct:.1f}% du débit de la boucle — inattendu"
    )
