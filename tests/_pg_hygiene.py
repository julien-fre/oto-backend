"""Hygiène du PostgreSQL jetable des tests (#640) — étiquette, tmpfs, sortie, balai.

Le 30/08/2026, dix conteneurs `oto-test-pg-*` tournaient depuis 7 h à 2 jours sur le
poste et ~519 volumes anonymes s'accumulaient (~260/jour, 3 G libres) : la fixture
lançait `docker run --rm` et finalisait par `docker rm -f`, mais quand pytest meurt
sans finalizer (limite de session, timeout, agent coupé), `--rm` ne joue jamais —
postgres ne sort pas de lui-même — et le volume anonyme de `PGDATA` reste.

Quatre parades, cumulées, parce qu'aucune ne couvre tout :
- le conteneur porte `oto-test=1` et `oto-test-started=<epoch>` : on sait le retrouver
  et le dater sans dépendre de son nom ;
- `PGDATA` est un tmpfs : rien à laisser derrière soi, et un init plus rapide ;
- la sortie est couverte par `atexit` et par SIGTERM/SIGINT : le conteneur est retiré,
  puis le signal est RELAYÉ à ce qu'il aurait fait sans nous (un SIGTERM tue toujours
  le processus, il ne devient pas une sortie propre qui masquerait la coupure) ;
- SIGKILL ne se rattrape pas : chaque session pytest balaie ce qui a plus de
  `MAX_AGE_S`, en le disant, une ligne par conteneur.

Et toute suppression est `docker rm -f -v` : sans `-v`, le volume anonyme d'un
conteneur `--rm` SURVIT (prouvé le 30/08/2026). L'ancien finalizer en fuyait donc un à
chaque run propre — les ~260 volumes/jour de l'issue. Le tmpfs rend le point sans objet
pour nos conteneurs ; le `-v` compte pour ceux qu'une autre session a lancés sans lui.

Ce module n'est pas collecté (pas de préfixe `test_`) ; `tests/` est sur le `sys.path`
de pytest grâce à `conftest.py`, donc `from _pg_hygiene import …`.
"""
from __future__ import annotations

import atexit
import os
import signal
import subprocess
import threading
import time
from typing import Optional

# ⚠️ **pgvector, et pas `postgres:17-alpine`** : `init_db()` fait
# `CREATE EXTENSION vector` avant `_SCHEMA` (des tables de `_SCHEMA` déclarent des
# `halfvec`), donc une image sans l'extension rend le VRAI boot intestable — et un
# test de migration qui ne peut pas jouer `init_db` ne prouve rien de la migration.
# Image officielle pgvector = PostgreSQL 17 standard + l'extension, `pg_trgm` inclus.
IMAGE = "pgvector/pgvector:pg17"

# `PGDATA` ET le `VOLUME` déclaré par l'image (vérifié sur `docker image inspect`,
# 30/08/2026) : un tmpfs posé à ce chemin PRIME sur le volume anonyme — `Mounts` vide.
# L'entrypoint fait `chown postgres` + `chmod 700` dessus, ce que tmpfs accepte.
PGDATA = "/var/lib/postgresql/data"
TMPFS_SIZE = "2g"          # un plafond, pas une réservation : seules les pages écrites comptent

LABEL = "oto-test"
LABEL_STARTED = "oto-test-started"
MAX_AGE_S = 2 * 3600       # la doc dit « orphelin à 1 h » ; le balai garde une marge
_DOCKER_TIMEOUT_S = 30


def _docker(*args: str) -> Optional[subprocess.CompletedProcess]:
    """Un appel `docker …`, ou None si le binaire est absent ou si le démon ne répond pas."""
    try:
        return subprocess.run(
            ["docker", *args], capture_output=True, text=True, timeout=_DOCKER_TIMEOUT_S)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


def docker_available() -> bool:
    r = _docker("info")
    return r is not None and r.returncode == 0


def run_args(name: str, *, started: Optional[float] = None) -> list[str]:
    """L'argv de `docker run` du PostgreSQL jetable : étiqueté, daté, `PGDATA` en tmpfs,
    port publié — et donc AUCUN volume, anonyme ou non."""
    epoch = int(time.time() if started is None else started)
    return [
        "docker", "run", "-d", "--rm", "--name", name,
        "--label", f"{LABEL}=1", "--label", f"{LABEL_STARTED}={epoch}",
        "--tmpfs", f"{PGDATA}:rw,size={TMPFS_SIZE}",
        "-e", "POSTGRES_PASSWORD=test", "-P", IMAGE,
    ]


def _age(seconds: float) -> str:
    return f"{int(seconds // 3600)} h {int(seconds % 3600 // 60):02d}"


def sweep_orphans(now: float, max_age_s: int = MAX_AGE_S) -> list[str]:
    """Retire les conteneurs `oto-test=1` lancés il y a plus de `max_age_s` (d'après leur
    label epoch) et rend une ligne par conteneur traité. Un conteneur étiqueté mais non
    daté n'est pas touché. Sans docker : liste vide, rien d'autre ne change."""
    r = _docker("ps", "-a", "--filter", f"label={LABEL}=1",
                "--format", "{{.Names}}\t{{.Label \"" + LABEL_STARTED + "\"}}")
    if r is None or r.returncode != 0:
        return []
    lines: list[str] = []
    for raw in r.stdout.splitlines():
        name, _, started = raw.partition("\t")
        if not started.strip().isdigit():
            continue
        age = now - int(started)
        if age <= max_age_s:
            continue
        rm = _docker("rm", "-f", "-v", name)
        if rm is not None and rm.returncode == 0:
            lines.append(f"#640 balayage : {name} lancé il y a {_age(age)} — retiré")
        else:
            err = (rm.stderr.strip() if rm is not None else "docker ne répond pas")
            lines.append(f"#640 balayage : {name} lancé il y a {_age(age)} — PAS retiré ({err})")
    return lines


class Guard:
    """Retire le conteneur à la sortie, quelle qu'elle soit : finalizer, `atexit`,
    SIGTERM, SIGINT. Idempotent — le finalizer et le handler peuvent tous deux passer.

    Le handler relaie le signal : il restaure ce qui était installé avant lui, puis
    l'appelle (SIGINT ⟹ `default_int_handler` ⟹ `KeyboardInterrupt`, et pytest finalise
    comme d'habitude) ou rejoue le signal sur le processus avec l'action par défaut
    (SIGTERM ⟹ le processus meurt DE SIGTERM). Jamais une sortie propre à la place.
    """

    _SIGNALS = (signal.SIGTERM, signal.SIGINT)

    def __init__(self, name: str) -> None:
        self.name = name
        self._done = False
        self._previous: dict[int, object] = {}

    def remove(self) -> None:
        if self._done:
            return
        self._done = True
        _docker("rm", "-f", "-v", self.name)

    def install(self) -> None:
        atexit.register(self.remove)
        if threading.current_thread() is not threading.main_thread():
            return  # `signal.signal` n'est permis que dans le thread principal — atexit couvre
        for sig in self._SIGNALS:
            prev = signal.signal(sig, self._on_signal)
            self._previous[sig] = signal.SIG_DFL if prev is None else prev

    def uninstall(self) -> None:
        atexit.unregister(self.remove)
        for sig, prev in self._previous.items():
            signal.signal(sig, prev)
        self._previous.clear()

    def _on_signal(self, signum: int, frame) -> None:
        prev = self._previous.get(signum, signal.SIG_DFL)
        self.remove()
        self.uninstall()
        if callable(prev):
            prev(signum, frame)
        elif prev == signal.SIG_DFL:
            os.kill(os.getpid(), signum)
        # SIG_IGN : rien à relayer, le processus continue comme il l'aurait fait.
