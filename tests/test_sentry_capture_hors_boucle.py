"""Sortir `sentry_sdk.capture_exception` de la boucle (`SentryToolErrorMiddleware`).

Diagnostiqué le 14/09/2026 sur les timeouts urllib3/requests de
`linkedin_aiark_person` en production (2 tentatives de 30 s, `_appel_avec_reprise`) :
sur 30 échecs, les 30 portent un `sentry_event_id` (donc `capture_exception` tourne à
chaque fois — `on_call_tool` est `async def`, mono-loop, docs/event-loop-perf.md mode
n°2), mais seulement 5 sur ~24-30 dans la même fenêtre ont produit un gel de boucle
mesuré ≥1 s. Le coût de `capture_exception` VARIE — à chiffrer, pas à supposer.

Trois preuves, dans l'ordre de la tâche :
1. `test_capture_exception_cout_mesure_sur_la_vraie_chaine_timeout_aiark` — le
   chiffre mesuré, sur une vraie chaîne d'exception (un socket qui pend pour de
   vrai, pas une exception fabriquée à la main).
2. `test_capture_exception_tourne_hors_du_thread_de_la_boucle` — rouge→vert :
   `capture_exception` tournait sur le thread de la boucle, tourne maintenant sur
   un thread du pool (`run_in_threadpool`), plus son contrôle qui mord.
3. `test_le_tag_pose_avant_le_thread_est_sur_levent_capture_dans_le_thread` — le
   scope Sentry (tags + user) posé dans le contexte ASYNC avant le thread est bien
   présent sur l'event capturé DEDANS — vérifié sur un event RÉEL (transport
   collecteur, aucun réseau), pas supposé depuis la doc du SDK.
"""
from __future__ import annotations

import asyncio
import socket
import statistics
import threading
import time
from contextlib import contextmanager

import pytest
import requests
import sentry_sdk
import urllib3
from fastmcp import Client, FastMCP
from sentry_sdk.integrations.mcp import MCPIntegration
from sentry_sdk.transport import Transport

from oto_mcp import sentry_setup
from oto_mcp.tools import aiark as aiark_mod


# ── Un vrai socket qui pend : la même chaîne d'exception que la production ──────

@contextmanager
def _serveur_qui_pend(*, tentatives: int, delai_lecture_s: float = 0.1):
    """TCP local qui ACCEPTE la connexion puis ne répond JAMAIS — un vrai read
    timeout urllib3/requests, pas une exception fabriquée à la main (la tâche
    demande de vérifier `__context__`/`__cause__` RÉELS)."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(tentatives + 1)
    port = srv.getsockname()[1]
    arret = False

    def _accepte_et_pend():
        for _ in range(tentatives):
            if arret:
                return
            srv.settimeout(1.0)
            try:
                conn, _ = srv.accept()
            except socket.timeout:
                continue
            threading.Thread(
                target=lambda c=conn: (time.sleep(delai_lecture_s * 20), c.close()),
                daemon=True).start()

    t = threading.Thread(target=_accepte_et_pend, daemon=True)
    t.start()
    try:
        yield port, delai_lecture_s
    finally:
        arret = True
        srv.close()


def _appel_qui_timeout(port: int, delai_lecture_s: float):
    return requests.get(f"http://127.0.0.1:{port}/", timeout=delai_lecture_s)


# ── Partie 1 : chiffrer, pas supposer ────────────────────────────────────────

def test_la_chaine_timeout_est_celle_produite_par_requests_pas_fabriquee():
    """Vérifie la FORME de la chaîne réelle avant de s'en servir pour chronométrer :
    `_appel_avec_reprise` fait 2 tentatives puis laisse remonter tel quel
    (`docs`/aiark.py) ; l'exception finale est un `requests.exceptions.Timeout`
    dont `__context__` est le VRAI `urllib3.exceptions.ReadTimeoutError` (chaînage
    IMPLICITE — `requests` ne fait pas `raise ... from`, `__cause__` reste None),
    et PAS l'exception de la 1ʳᵉ tentative (chaque itération de la boucle de
    reprise clôt son `except` avant la suivante — vérifié)."""
    with _serveur_qui_pend(tentatives=2) as (port, delai):
        with pytest.raises(requests.exceptions.Timeout) as exc_info:
            aiark_mod._appel_avec_reprise(
                lambda c: _appel_qui_timeout(port, delai), None)
    e = exc_info.value
    assert e.__cause__ is None
    assert isinstance(e.__context__, urllib3.exceptions.ReadTimeoutError)
    # La chaîne ne remonte pas jusqu'à la 1ʳᵉ tentative — seulement au socket réel.
    assert isinstance(e.__context__.__context__, (socket.timeout, TimeoutError))


def test_capture_exception_cout_mesure_sur_la_vraie_chaine_timeout_aiark(monkeypatch):
    """Chronomètre `sentry_sdk.capture_exception` sur l'exception RÉELLE. Sentry en
    DSN vide : `client.transport` reste `None`, donc AUCUN envoi réseau — mais
    `Client.capture_event` construit l'event EN ENTIER avant sa dernière ligne
    (`if self.transport is not None: self.transport.capture_envelope(...)`),
    seule partie court-circuitée. C'est exactement le coût LOCAL à mesurer, pas
    une approximation."""
    previous = sentry_sdk.get_client()
    sentry_sdk.init(
        dsn="",
        environment="bench",
        include_local_variables=False,
        traces_sample_rate=0,
        before_send=sentry_setup._before_send,
        # Même config que `init_sentry()` (oto-backend#869) : sans ce retrait, le
        # SDK auto-active `MCPIntegration`, qui capture la MÊME exception une
        # SECONDE fois (directement, sans passer par notre middleware ni son
        # thread) — ça polluerait à la fois la mesure et l'état global du process
        # pour les tests suivants du fichier (les intégrations restent actives
        # une fois posées, même après restauration du client).
        disabled_integrations=[MCPIntegration()],
        # `default_integrations=False` en plus de `disabled_integrations` : si ce
        # test fait le tout premier `sentry_sdk.init()` de la suite pytest complète
        # (pas seulement de ce fichier), les intégrations par défaut du SDK
        # resteraient patchées pour TOUT LE PROCESS ensuite — aucune ne se
        # désinstalle entre deux tests. Ce banc mesure `capture_exception` brut, pas
        # une intégration tierce : aucune des intégrations par défaut n'a de raison
        # d'être active ici.
        default_integrations=False,
    )
    client = sentry_sdk.get_client()
    assert client.options.get("include_local_variables") is False, (
        "sans ce réglage actif DANS ce banc, le chiffre mesurerait le MAUVAIS "
        "réglage (#564) — chaque frame partirait avec ses variables locales")
    assert client.transport is None, (
        "un vrai transport tenterait d'ouvrir une connexion réseau depuis le banc")

    monkeypatch.setattr(aiark_mod, "_TRANSPORT_PAUSE_S", 0.01)  # ne mesure pas la pause

    durations_ms: list[float] = []
    n = 30
    try:
        for _ in range(n):
            with _serveur_qui_pend(tentatives=2) as (port, delai):
                try:
                    aiark_mod._appel_avec_reprise(
                        lambda c: _appel_qui_timeout(port, delai), None)
                except requests.exceptions.Timeout as e:
                    with sentry_sdk.new_scope() as scope:
                        scope.set_tag("mcp.tool", "linkedin_aiark_person")
                        scope.set_user({"id": "u-bench"})
                        t0 = time.perf_counter()
                        sentry_sdk.capture_exception(e)
                        durations_ms.append((time.perf_counter() - t0) * 1000)
    finally:
        sentry_sdk.get_global_scope().set_client(previous)

    assert len(durations_ms) == n, "toutes les tentatives doivent avoir levé le timeout"
    mediane = statistics.median(durations_ms)
    pire = max(durations_ms)
    print(
        f"\ncapture_exception sur chaîne timeout aiark (N={n}) : "
        f"médiane={mediane:.2f} ms, min={min(durations_ms):.2f} ms, "
        f"max={pire:.2f} ms, moyenne={statistics.mean(durations_ms):.2f} ms"
    )
    # Tripwire large (pas un SLA) : mesuré en local médiane ~5-9 ms, une capture à
    # froid (1ᵉʳ appel, `linecache` lit chaque fichier une fois) jusqu'à ~70 ms.
    # Une régression qui ferait exploser ce coût (ex. `include_local_variables`
    # qui revient à True) doit se voir ici, sans faire de ce test un SLA strict.
    assert mediane < 300, f"coût médian anormalement élevé : {mediane:.2f} ms"


# ── Partie 2 : déplacer capture_exception en threadpool sans perdre le scope ──

def _mcp_avec_boom() -> FastMCP:
    mcp = FastMCP("t")

    @mcp.tool()
    def boom() -> str:
        raise RuntimeError("panne réelle, pas une erreur gérée")

    mcp.add_middleware(sentry_setup.SentryToolErrorMiddleware())
    return mcp


def test_capture_exception_tourne_hors_du_thread_de_la_boucle(monkeypatch):
    """`on_call_tool` est `async def` : tout ce qu'il fait NÛMENT tourne sur le
    thread de la boucle (mono-loop, docs/event-loop-perf.md). `capture_exception`
    fait de l'I/O (lecture disque via `linecache` pour le contexte source de
    chaque frame, cf. Partie 1) — il doit tourner sur un thread du pool."""
    threads: list[threading.Thread] = []

    def _mouchard(e):
        threads.append(threading.current_thread())
        return "evt-mouchard"

    monkeypatch.setattr(sentry_setup.sentry_sdk, "capture_exception", _mouchard)
    monkeypatch.setattr(sentry_setup, "current_user_sub_from_token", lambda: None)
    monkeypatch.setattr(sentry_setup, "current_client_id_from_token", lambda: None)

    boucle_thread: dict = {}

    async def _go():
        boucle_thread["t"] = threading.current_thread()
        async with Client(_mcp_avec_boom()) as c:
            with pytest.raises(Exception):
                await c.call_tool("boom", {})

    asyncio.run(_go())

    assert threads, (
        "garde INERTE : capture_exception n'a jamais été appelée — revoir le "
        "montage du test avant de conclure au vert")
    coupables = [t for t in threads if t is boucle_thread["t"]]
    assert not coupables, (
        f"{len(coupables)} appel(s) de capture_exception depuis le thread de la "
        "boucle : le serveur est mono-loop, l'appel doit passer par "
        "run_in_threadpool (cf. docs/event-loop-perf.md, mode n°2)")


def test_le_mouchard_mord_sur_un_appel_nu_dans_la_boucle(monkeypatch):
    """Contrôle : le MÊME mouchard, sur un appel NU dans la boucle (ce que faisait
    le code avant le correctif), doit être vu comme fautif — sans quoi le test
    ci-dessus pourrait être vert pour de mauvaises raisons."""
    threads: list[threading.Thread] = []
    monkeypatch.setattr(
        sentry_setup.sentry_sdk, "capture_exception",
        lambda e: threads.append(threading.current_thread()))

    boucle_thread: dict = {}

    async def _go():
        boucle_thread["t"] = threading.current_thread()
        try:
            raise RuntimeError("x")
        except RuntimeError as e:
            sentry_setup.sentry_sdk.capture_exception(e)  # nu, DANS la boucle

    asyncio.run(_go())
    assert any(t is boucle_thread["t"] for t in threads), (
        "le mouchard n'a rien vu sur un appel pourtant fait DANS la boucle : la "
        "détection elle-même est cassée, les autres tests de ce module ne "
        "prouvent rien")


class _TransportCollecteur(Transport):
    """Transport RÉEL (pas un mock de `capture_exception`) qui ne fait AUCUN envoi
    réseau — seul moyen d'inspecter l'event tel que le SDK l'a VRAIMENT construit,
    y compris depuis un thread du pool."""

    def __init__(self, options=None) -> None:
        super().__init__(options)
        self.envelopes: list = []

    def capture_envelope(self, envelope) -> None:
        self.envelopes.append(envelope)


@pytest.fixture
def sentry_local():
    """Sentry RÉELLEMENT actif (transport collecteur, jamais de réseau), restauré
    à l'état d'avant le test (probablement inactif — Sentry ne tourne pas en
    suite)."""
    previous = sentry_sdk.get_client()
    collector = _TransportCollecteur()
    sentry_sdk.init(
        dsn="https://x@o0.ingest.sentry.io/0",  # forme valide, jamais résolu (transport collecteur)
        transport=collector,
        include_local_variables=False,
        traces_sample_rate=0,
        before_send=sentry_setup._before_send,
        disabled_integrations=[MCPIntegration()],  # cf. commentaire du banc plus haut
        default_integrations=False,  # idem : ne pas laisser une intégration par défaut coller au process
    )
    try:
        yield collector
    finally:
        sentry_sdk.get_global_scope().set_client(previous)


@pytest.mark.asyncio
async def test_le_tag_pose_avant_le_thread_est_sur_levent_capture_dans_le_thread(
        sentry_local, monkeypatch):
    """LA vérification empirique demandée par la tâche : le tag posé dans le
    contexte ASYNC (avant `run_in_threadpool`) est-il présent sur l'event capturé
    DANS LE THREAD ? Si `run_in_threadpool` ne copiait pas le contexte Sentry, cet
    event partirait anonyme — pire qu'un gel : un traceback qu'on ne peut plus
    relier à personne."""
    monkeypatch.setattr(sentry_setup, "current_user_sub_from_token", lambda: "u-preuve")
    monkeypatch.setattr(sentry_setup, "current_client_id_from_token", lambda: "claude-code")

    async with Client(_mcp_avec_boom()) as c:
        with pytest.raises(Exception):
            await c.call_tool("boom", {})

    assert sentry_local.envelopes, "aucun event capturé"
    event = sentry_local.envelopes[-1].get_event()
    assert event is not None
    assert event["tags"].get("mcp.tool") == "boom"
    assert event["tags"].get("mcp.client") == "claude-code"
    assert event["user"]["id"] == "u-preuve"

# ── `_LAST_EVENT_ID` reste posée dans le contexte ASYNC : voir
# `tests/test_sentry_event_id_stamp.py` (inchangé par ce lot) — il exerce la VRAIE
# chaîne `ToolCallLogger` + `SentryToolErrorMiddleware` et vérifie que le sink
# (tâche créée par `asyncio.create_task`, donc une COPIE de contexte distincte de
# celle du test) relit bien l'event id posé par le middleware. Tenté ici une
# lecture directe depuis le contexte du test — écartée : le client/serveur
# FastMCP en mémoire ne partagent pas nécessairement le contexte du test
# appelant, ce qui aurait fait un test non concluant (échoue même quand le
# comportement est correct), pas un test rouge→vert.
