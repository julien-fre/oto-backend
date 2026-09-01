"""La version **SERVIE** : ce que CE processus exécute, et rien d'autre.

Le 28/08/2026, quatre mises en production dans la même journée, dont une quinze
minutes avant une mesure de comportement d'une flotte d'agents. Aucune des deux
faces (`/mcp`, `/api/*`) ne disait sa version : l'attribution — quel changement a
produit quel effet — a dû se reconstituer après coup depuis les tags GitHub, et
plusieurs sessions y ont laissé leur matinée à établir « qu'est-ce qui est
réellement servi ». D'où ce module (oto#33).

**Ce qu'on refuse de prendre pour la version.**

- *Le dernier run vert du workflow.* Il dit qu'un déploiement a été **lancé**, pas
  ce qui tourne : entre le déclenchement et l'exécution du script, `main` a pu
  avancer, une bascule a pu échouer et laisser l'ancienne couleur en service, un
  rollback a pu rebasculer sur la version d'avant. Un run vert désigne une
  intention ; nous voulons un fait.
- *`git rev-parse` sur la box.* Le déploiement est bleu/vert : l'arbre de la
  couleur INACTIVE est réécrit pendant que l'autre sert. Lire git au moment de la
  requête, c'est lire l'état d'un arbre à un instant qui n'a rien à voir avec le
  démarrage du processus — sans compter qu'une image on-premise n'a ni git ni
  dépôt.
- *`importlib.metadata.version("oto-core")`, donc `pip show`.* Il rend le champ
  `version` du `pyproject` d'oto-core, **gelé à 1.100.0** depuis que les tags ont
  cessé de le bumper. Mesuré le 01/09/2026 dans le venv du backend : `pip show`
  annonçait `1.100.0` là où l'installé était `v1.101.0` — il aurait annoncé la même
  chose pour `v1.200.0`. La coordonnée fiable est ce que **pip écrit à
  l'installation** : `direct_url.json` (PEP 610), qui porte la révision demandée
  (le tag) et le commit résolu.

**Ce qu'on prend, donc.** Une coordonnée **écrite par celui qui installe, dans
l'arbre qu'il vient d'écrire, avant que le processus ne démarre** — le
`.oto-deploy.json` que pose `deploy/oto-mcp-bluegreen.sh` — et lue **une seule
fois**, au premier appel. Un fichier réécrit sous un processus vivant ne change
donc pas ce qu'il annonce : la valeur servie reste celle de son propre démarrage.
Une image Docker ou un on-premise, qui n'a pas cet arbre, passe les mêmes
coordonnées par l'environnement (`OTO_DEPLOY_REF` / `OTO_DEPLOY_SHA` /
`OTO_DEPLOY_AT`), qui **prime** — c'est aussi la porte de sortie si le fichier
manque.

Et quand on ne sait pas, on le DIT (`"unknown"`, `source: "unknown"`) : une version
inventée serait pire que pas de version, puisqu'on daterait un changement de
comportement sur une coordonnée fausse.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from functools import lru_cache
from importlib import metadata
from pathlib import Path

SERVICE = "oto-backend"

#: Ce que rend une coordonnée absente — jamais une valeur plausible.
INCONNU = "unknown"

ENV_REF = "OTO_DEPLOY_REF"
ENV_SHA = "OTO_DEPLOY_SHA"
ENV_AT = "OTO_DEPLOY_AT"

#: Écrit par le déploiement à la racine de l'arbre de la couleur, juste après le
#: `git reset --hard`. Non versionné (cf. `.gitignore`) : `git reset --hard` ne
#: touche pas aux fichiers non suivis, donc il survit exactement le temps que
#: l'arbre porte cette version.
FICHIER_DEPLOIEMENT = ".oto-deploy.json"

#: Démarrage du processus. Relevé à l'import, donc au boot — c'est la seule
#: coordonnée qui n'a de sens que prise là.
DEMARRE_A = datetime.now(timezone.utc).isoformat(timespec="seconds")


def racine_de_l_arbre() -> Path:
    """La racine du checkout, en installation editable (`pip install -e .`) — ce
    qu'installe le déploiement. En wheel (image Docker), ce chemin pointe dans
    `site-packages` : le fichier n'y est pas, et c'est l'environnement qui parle."""
    return Path(__file__).resolve().parent.parent


def _fichier(racine: Path) -> dict:
    """Le contenu du `.oto-deploy.json`, ou `{}`. Un fichier illisible ou tronqué
    (déploiement interrompu en pleine écriture) vaut absence : on retombera sur
    `unknown`, ce qui est vrai, plutôt que sur une moitié de coordonnée."""
    try:
        brut = (racine / FICHIER_DEPLOIEMENT).read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        charge = json.loads(brut)
    except ValueError:
        return {}
    return charge if isinstance(charge, dict) else {}


def _texte(valeur) -> str | None:
    """Une coordonnée n'est retenue que si c'est une chaîne non vide."""
    if isinstance(valeur, str) and valeur.strip():
        return valeur.strip()
    return None


def etiquette(ref: str | None, commit: str | None) -> str:
    """La chaîne UNIQUE servie partout — `/api/version`, `info.version` de
    l'OpenAPI, en-tête `X-Oto-Version`. Une seule forme, pour qu'un journal
    d'appels et un descriptif d'API se comparent sans traduction.

    `v1.2.3+6d5bf16b` : le tag DIT la release, le commit court la DÉSIGNE. Les deux,
    parce que le tag seul ment après un retag et que le commit seul ne se lit pas.
    """
    court = commit[:8] if commit else None
    if ref and court:
        return f"{ref}+{court}"
    return ref or court or INCONNU


def _coordonnees() -> tuple[str | None, str | None, str | None, str]:
    """(ref, commit, date de déploiement, d'où ça vient). L'environnement prime :
    c'est la seule voie d'un on-premise, et la seule reprise si le fichier manque."""
    ref = _texte(os.environ.get(ENV_REF))
    commit = _texte(os.environ.get(ENV_SHA))
    if ref or commit:
        return ref, commit, _texte(os.environ.get(ENV_AT)), "env"
    charge = _fichier(racine_de_l_arbre())
    ref = _texte(charge.get("ref"))
    commit = _texte(charge.get("commit"))
    if ref or commit:
        return ref, commit, _texte(charge.get("deployed_at")), "deploy_file"
    return None, None, None, INCONNU


def oto_core() -> dict:
    """Le tag oto-core **réellement installé**, pas celui qu'épingle le `pyproject`.

    Les deux divergent régulièrement : `pip` NE RÉINSTALLE PAS une dépendance VCS
    déjà présente, donc un venv peut porter un tag antérieur au pin sans qu'aucune
    commande ne s'en plaigne (le déploiement force la réinstallation précisément
    pour ça). C'est l'installé qui exécute les appels, donc c'est lui qu'on publie.

    `source` nomme la précision qu'on a pu obtenir, plutôt que de laisser un `null`
    se lire comme « rien n'est installé » :

    - `direct_url` — installation depuis git : `requested_revision` est le tag,
      `commit_id` le commit résolu. La coordonnée exacte.
    - `metadata` — installation depuis PyPI : plus de `direct_url.json`, il ne
      reste que le champ `Version`. ⚠️ C'est le numéro GELÉ décrit en tête de
      module ; on le sert faute de mieux, en le nommant pour ce qu'il est.
    - `absent` — oto-core n'est pas installé (aucun connecteur ne peut tourner).
    """
    try:
        dist = metadata.distribution("oto-core")
    except metadata.PackageNotFoundError:
        return {"tag": None, "commit": None, "source": "absent"}
    try:
        brut = dist.read_text("direct_url.json")
    except OSError:                       # dist-info illisible → on a encore METADATA
        brut = None
    if brut:
        try:
            vcs = (json.loads(brut) or {}).get("vcs_info") or {}
        except ValueError:
            vcs = {}
        tag = _texte(vcs.get("requested_revision"))
        commit = _texte(vcs.get("commit_id"))
        if tag or commit:
            return {"tag": tag, "commit": commit, "source": "direct_url"}
    return {"tag": _texte(dist.metadata.get("Version")), "commit": None,
            "source": "metadata"}


@lru_cache(maxsize=1)
def instantane() -> dict:
    """Ce que ce processus exécute, figé au premier appel (donc au boot : la
    première requête servie le déclenche, et l'en-tête de réponse aussi).

    ⚠️ Mémoïsé DÉLIBÉRÉMENT : un déploiement qui réécrit le fichier sous un
    processus encore vivant — la couleur en cours de vidange, pendant les deux
    minutes où elle finit ses requêtes — ne doit pas lui faire annoncer la version
    de son successeur. Les tests qui font varier l'environnement appellent
    `instantane.cache_clear()`.
    """
    ref, commit, deploye_a, source = _coordonnees()
    return {
        "service": SERVICE,
        "version": etiquette(ref, commit),
        "ref": ref,
        "commit": commit,
        "deployed_at": deploye_a,
        "started_at": DEMARRE_A,
        "source": source,
        "oto_core": oto_core(),
    }


def version_servie() -> str:
    """L'étiquette seule — ce que portent l'en-tête et `info.version`."""
    return instantane()["version"]
