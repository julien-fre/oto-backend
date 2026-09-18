"""L'exécuteur : lance le bac à sable d'une fonction et lit sa réponse (ADR 0073 §D4).

Un processus Deno par exécution, hors du serveur MCP. Ce module est BLOQUANT (il attend
un sous-processus) : ses appelants le jouent hors de la boucle (`run_in_threadpool`).

**Où vit le bac à sable.** `OTO_FUNCTIONS_SANDBOX_DIR` désigne un répertoire posé par
`scripts/installer_bac_a_sable.py` : `deno` (le binaire), `deno_dir/` (le cache npm où
vit Pyodide) et `wheels/` (les roues de la liste blanche), tous épinglés et vérifiés par
empreinte. Absent, l'exécuteur LÈVE : une instance qui n'a pas de bac à sable ne sait
pas exécuter de fonction, et le dire vaut mieux que chercher un `deno` au hasard du PATH.

**Ce que le processus a le droit de faire** : lire le runtime de Pyodide et les roues.
Rien d'autre — ni réseau, ni écriture, ni environnement, ni sous-processus. Deno ne
résout ses paquets que dans le cache (`--cached-only`) : rien ne se télécharge au moment
d'exécuter. L'environnement du processus est vidé : il n'hérite d'aucun secret du
serveur. Le répertoire peut être en lecture seule pour l'utilisateur du service.
"""
from __future__ import annotations

import json
import os
import secrets
import subprocess
import time
from pathlib import Path
from typing import Optional

from . import contract

ENV_DIR = "OTO_FUNCTIONS_SANDBOX_DIR"
_ICI = Path(__file__).resolve().parent
_SCRIPT = _ICI / "sandbox.ts"
_HARNAIS = _ICI / "harness.pyodide"
_VERROU = _ICI / "deno.lock"
# La version de Pyodide importée par `sandbox.ts` et posée par l'installateur. Les trois
# doivent dire la même : c'est ce que vérifie `tests/test_functions_executeur_live.py`.
PYODIDE = "0.26.4"
_PYODIDE_REL = f"deno_dir/npm/registry.npmjs.org/pyodide/{PYODIDE}"

# Plafonds communs à toute exécution (0057 §D6 : la définition du lieu, pas un réglage
# par fonction). Chiffres PROVISOIRES, à remplacer par ceux mesurés sur le pilote.
DELAI_S = 60
MAX_REPONSE = 30 * 1024 * 1024
TAS_V8_MO = 512

# La roue qui accompagne chaque dépendance de la liste blanche. `et_xmlfile` suit
# `openpyxl` : c'est sa dépendance, jamais une demande d'auteur.
_ROUES = {"openpyxl": ("openpyxl", "et_xmlfile")}


class BacASableAbsent(RuntimeError):
    """Cette instance n'a pas de bac à sable : elle ne peut pas exécuter de fonction."""


class ExecutionEchouee(RuntimeError):
    """Le bac à sable n'a pas rendu de réponse exploitable (délai, plantage, protocole)."""


def _repertoire() -> Path:
    brut = os.environ.get(ENV_DIR)
    if not brut:
        raise BacASableAbsent(f"{ENV_DIR} n'est pas posée : cette instance n'exécute pas "
                              "de fonction (cf. scripts/installer_bac_a_sable.py).")
    racine = Path(brut)
    for requis in ("deno", f"{_PYODIDE_REL}/pyodide.mjs", "wheels"):
        if not (racine / requis).exists():
            raise BacASableAbsent(f"{racine / requis} manque : bac à sable incomplet.")
    return racine


def _roues(racine: Path, requirements: list[str]) -> list[str]:
    chemins = []
    for dependance in sorted(set(requirements)):
        if dependance not in contract.DEPENDANCES:
            raise ValueError(f"dépendance hors liste blanche : {dependance}")
        for projet in _ROUES[dependance]:
            trouvees = sorted((racine / "wheels").glob(f"{projet}-*.whl"))
            if len(trouvees) != 1:
                raise BacASableAbsent(f"roue `{projet}` : {len(trouvees)} trouvée(s) dans "
                                      f"{racine / 'wheels'}, il en faut exactement une.")
            chemins.append(str(trouvees[0]))
    return chemins


def executer(*, sources: dict, entrypoint: str, requirements: list[str], mode: str,
             entree: Optional[dict] = None) -> dict:
    """Joue la fonction (`mode='run'`) ou ses tests (`mode='test'`) dans le bac à sable.

    Rend la sortie du harnais — `{ok, result, warnings, files}` ou `{ok, error}` en
    `run`, `{ok, tests}` en `test` — plus `journal` (ce que le code a imprimé) et
    `duree_ms`. Lève `ExecutionEchouee` si le bac à sable n'a rien rendu d'exploitable.
    """
    if mode not in ("run", "test"):
        raise ValueError(f"mode inconnu : {mode}")
    racine = _repertoire()
    roues = _roues(racine, requirements)
    nonce = secrets.token_hex(16)
    pyodide = racine / _PYODIDE_REL
    tache = {
        "pyodide": str(pyodide), "wheels": roues, "nonce": nonce,
        "harness": _HARNAIS.read_text(encoding="utf-8"),
        "job": {"sources": sources, "entrypoint": entrypoint, "mode": mode,
                "input": entree if entree is not None else {}},
    }
    lecture = ",".join([str(pyodide), str(racine / "wheels")])
    env = {"DENO_DIR": str(racine / "deno_dir"), "NO_COLOR": "1", "PATH": "/usr/bin:/bin"}
    # `--lock` + `--frozen` : la résolution des paquets est celle du verrou, jamais une
    # résolution neuve — sans eux Deno réclame des métadonnées que le cache n'a pas.
    commande = [str(racine / "deno"), "run", "--no-prompt", "--no-config", "--cached-only",
                "--no-remote", f"--lock={_VERROU}", "--frozen", f"--allow-read={lecture}",
                f"--v8-flags=--max-old-space-size={TAS_V8_MO}", str(_SCRIPT)]
    debut = time.monotonic()
    try:
        fini = subprocess.run(commande, input=json.dumps(tache).encode(), env=env,
                              capture_output=True, timeout=DELAI_S, cwd=str(racine))
    except subprocess.TimeoutExpired:
        raise ExecutionEchouee(f"délai dépassé ({DELAI_S} s)") from None
    duree = int((time.monotonic() - debut) * 1000)
    reponse = _lire_reponse(fini.stdout, nonce)
    if reponse is None:
        raise ExecutionEchouee(
            f"le bac à sable n'a rendu aucune réponse (code {fini.returncode}) : "
            f"{fini.stderr.decode(errors='replace')[-1500:]}")
    if not reponse.get("ok"):
        raise ExecutionEchouee(reponse.get("erreur") or "échec sans message")
    sortie = dict(reponse["sortie"])
    sortie["journal"] = reponse.get("journal") or ""
    sortie["duree_ms"] = duree
    return sortie


def _lire_reponse(stdout: bytes, nonce: str) -> Optional[dict]:
    """La DERNIÈRE ligne qui porte le nonce — et aucune autre."""
    if len(stdout) > MAX_REPONSE:
        raise ExecutionEchouee(f"réponse de {len(stdout)} octets : au plus {MAX_REPONSE}")
    for ligne in reversed(stdout.decode(errors="replace").splitlines()):
        if ligne.startswith(nonce):
            return json.loads(ligne[len(nonce):])
    return None

