---
title: Commandes
type: how-to
description: >-
  Recettes d'exécution du backend oto : lancer les tests (le venv local n'a pas
  pytest — recette exacte), tester un clone sans réinstaller les deps et ses deux
  pièges d'import, déployer (push main = preprod, tag v* = prod), lire les logs,
  inspecter la base managée, et les gotchas de registre d'outils hors serveur.
  À charger avant toute exécution de test, de déploiement ou d'inspection de prod.
---

```bash
# Transport stdio RETIRÉ (2026-06-13) : oto-mcp ne se sert qu'en streamable_http
# (toujours authentifié Logto). Usage local = CLI `oto`. Pour un serveur local,
# lancer en http avec les LOGTO_* et taper avec un bearer.

# Tests — le venv .venv N'A PAS pytest (extra `dev` non installé) et `uv run pytest`
# crée un env éphémère SANS les deps projet (piège, ModuleNotFoundError). Recette :
uv pip install --python .venv/bin/python "pytest>=8.0" "pytest-asyncio>=0.24"
.venv/bin/python -m pytest -q

# Tester un CLONE (clone scratchpad, ou `git archive <commit>` pour isoler un commit du WIP
# voisin du tree partagé) SANS réinstaller les deps : réutiliser le venv local (deps+pytest
# présents) en résolvant `oto_mcp` depuis le clone.
#   cd <clone> && PYTHONPATH=<clone> OTO_CONFIG_DISABLE_SOPS=1 \
#     /data/oto/backend/.venv/bin/python -m pytest -q tests/...
#
# ⚠️ **Le `cd <clone>` n'est PAS cosmétique : c'est LUI qui fait marcher la recette.**
# `PYTHONPATH` seul NE PRIME PAS sur l'editable install — son finder vit dans `sys.meta_path`,
# consulté AVANT `sys.path`. Lancé depuis `/data/oto/backend`, le même PYTHONPATH importe
# donc `/data/oto/backend/oto_mcp` : on croit tester le clone, on teste le tree partagé.
# Le mode d'échec est un **faux négatif silencieux** — vécu 11/08, un agent a conclu « le
# code d'avant passe déjà mes 16 tests » en testant en réalité son propre correctif, ce qui
# invalide la seule chose que le clone servait à prouver.
# **Valider l'instrument avant d'en tirer une conclusion**, une ligne suffit :
#   cd <clone> && PYTHONPATH=<clone> /data/oto/backend/.venv/bin/python -c \
#     "import oto_mcp.db.search as m; print(m.__file__)"   # doit pointer DANS le clone
#
# ⚠️ **2e piège (12/08) : la validation ci-dessus ne couvre PAS un fichier NEUF.** Si ton
# lot CRÉE un module (ex. `grants_chain.py`), le finder editable le sert depuis
# /data/oto/backend même avec le `cd` — le clone de HEAD ne l'a pas, l'import retombe sur
# le tree partagé, et la ligne de validation ne l'attrape pas (elle teste un module qui
# existe des deux côtés). Le test « rouge sur le code d'avant » devient alors un mensonge.
# Parade : un `sitecustomize.py` dans le clone qui retire les finders editable :
#   import sys; sys.meta_path = [f for f in sys.meta_path
#                                if "__editable__" not in type(f).__module__]
# Puis re-valider en important LE MODULE NEUF : il doit lever ImportError dans le clone.
# ⚠️ **3e piège (26/08) : le venv porte une COPIE FIGÉE d'oto-core.** `.venv` a une
# version INSTALLÉE de la lib (`pip show oto-core`), pas un lien vers le checkout — elle
# ne bouge donc pas quand le tronc bump son pin. Le mode d'échec va dans les DEUX sens :
#   • trop VIEUX → `ModuleNotFoundError: oto.tools.<neuf>` en masse (48 échecs + 33 erreurs
#     vécus le 26/08 sur airtable/tavily/waalaxy) : un faux ROUGE qu'on impute au tronc ou
#     à son propre lot, alors que rien n'est cassé ;
#   • trop RÉCENT (ou PYTHONPATH sur un checkout en avance) → un vert local sur des méthodes
#     que le pin du tronc n'a PAS : faux VERT, et la garde version-skew le rattrape en CI.
# Parade — viser un checkout À JOUR, et le mettre à jour d'abord :
#   git -C /data/oto/oto-core pull --rebase --autostash
#   PYTHONPATH=/data/oto/oto-core .venv/bin/python -m pytest -q
# Valider l'instrument AVANT de conclure (même réflexe que les deux pièges ci-dessus) :
#   PYTHONPATH=/data/oto/oto-core .venv/bin/python -c \
#     "import oto.tools as t; print(t.__path__)"     # doit pointer DANS /data/oto/oto-core
# et comparer la version du checkout au pin du tronc :
#   grep -m1 '^version' /data/oto/oto-core/pyproject.toml
#   grep -o 'oto-core.git@v[0-9.]*' pyproject.toml   # les deux doivent coïncider

# Tests À BASE (fixture `pg_dsn`) : elle prend `OTO_TEST_PG_DSN` s'il existe, sinon monte
# un PostgreSQL JETABLE via docker (`docker run --rm`, supprimé en fin de session). Sans
# l'un ni l'autre, ces tests sont **SKIPPÉS** — et un vert local sans base ne vaut RIEN
# contre la CI qui en a une (tronc cassé une heure ainsi le 23/08). Vérifier le compte de
# skips : `pytest -q -rs` ne doit montrer AUCUN skip motivé par l'absence de PostgreSQL.

# Convention : tester la LOGIQUE PURE (helpers hors DB, ex. `effective_for_group`,
# `_connector_blocked`/seams) + les gardes de capacité par stub ; le chemin SQL est vérifié
# au déploiement (le job `test` du CI tourne le vrai suite avec toutes les deps).

# Deploy — modèle tronc unique (refonte 2026-07-20, ADR 0020) :
#   push `main`  → PREPROD (« Deploy preprod », deploy-canari.yml, script serveur
#                  oto-backend-canari.sh : git reset --hard origin/main → preprod)
#   tag  `v*`    → PROD    (« Deploy prod », deploy.yml, script serveur
#                  oto-backend.sh <tag> : git reset --hard <tag> → prod)
# Le deploy (les deux) = SSH box dédiée via runner self-hosted : reset au ref +
# pip install -e . + **force-reinstall oto-core depuis le tag pinné** (lu du
# pyproject ; pip saute sinon une dép VCS déjà présente) + restart + **smoke HTTP**
# (GET 200 /.well-known/oauth-authorization-server) + **rollback auto** si
# install/restart/smoke échoue. Le restart relance start-encrypted (refetch master
# key). ⚠️ start-encrypted.sh untracked → survit au git reset.
#
# Preprod = travailler sur `main`, commit, push : deploy preprod auto (gate
# `needs: test`). Claude Code (web) ouvre ses PR sur main → merge = deploy preprod.
git push origin main            # → PREPROD

# Prod = acte explicite : taguer un commit de main + pousser le tag.
git tag v1.2.3 && git push origin v1.2.3   # → PROD (tags v* immuables, ruleset)
# ⚠️ `canari` est DÉPRÉCIÉE (ne déploie plus) : un checkout encore dessus doit
# passer sur main (`git checkout main`). guard-main + sync-main-to-canari retirés.

# Logs
ssh -i ~/.ssh/alexis root@<box> "journalctl -u oto-mcp -f"

# DB inspect (PG managed) — depuis la box (env du process inclut DATABASE_URL via .env)
# ⚠️ `psql` n'est PAS installé sur la box dédiée → passer par le venv + psycopg :
ssh -i ~/.ssh/alexis root@<box> 'cd /opt/oto-mcp && set -a; . .env; set +a; ./.venv/bin/python -c "
import os, psycopg
with psycopg.connect(os.environ[\"DATABASE_URL\"]) as c:
    for r in c.execute(\"SELECT sub, email, role FROM users\"): print(r)
"'

# ⚠️ Même besoin pour tout script d'ENTRETIEN lancé à la main (`python -m scripts.X`) :
# il n'hérite pas de l'environnement du service systemd, donc il sort en
# « RuntimeError: DATABASE_URL not set » avant d'avoir rien fait. Sourcer d'abord :
#   cd /opt/oto-mcp && set -a && . ./.env && set +a && ./.venv/bin/python -m scripts.X
# Vécu 19/08 sur scripts.archive_empty_kb_projects (dry-run par défaut, --apply pour agir).

# ⚠️ Un script HORS SERVEUR ne voit AUCUN outil : `tool_registry.boot_tool_names()`
# rend [] tant que le registre n'est pas réchauffé (le serveur le fait au lifespan).
# Toute validation de nom d'outil renvoie alors un `unknown_tool` TROMPEUR — vécu
# 05/08, j'ai failli annoncer un blocage inexistant. Diagnostic fidèle :
#   register_all(mcp := FastMCP("x")); tool_registry.bind(mcp)
#   asyncio.run(tool_registry.warm_registry(mcp))   # → 665 outils, la validation passe

# ⚠️ Déchiffrer un credential ad-hoc : OTO_MCP_MASTER_KEY n'est PAS dans .env
# (fetchée au boot depuis Secret Manager) → recette complète + pièges (RuntimeError
# ≠ InvalidTag ; status_for = credential_status, jamais get_credential_with_meta) :
# docs/connector-vault.md §Déchiffrer un credential ad-hoc.
```

## Maintenance — les travaux qui ont quitté le boot (ADR 0065 lot 0)

```bash
# Sur la box, avec l'environnement du service (jamais une copie du .env) :
sudo systemctl start oto-mcp-maintenance.service          # la passe complète, à la main
sudo journalctl -u oto-mcp-maintenance -n 50 --no-pager   # ce qu'elle a fait, avec ses durées
systemctl list-timers oto-mcp-maintenance.timer           # le prochain tir

# Un travail seul, et d'abord À BLANC — sur une base PARTAGÉE prod/preprod, la
# première question devant une purge est « combien de lignes ? ».
sudo -E env $(cat /opt/oto-mcp/.env | xargs) \
  /opt/oto-mcp/.venv/bin/oto-mcp maintenance retention --dry-run
#   retention | blocks | key-indexes | all      les travaux du timer
#   check-boot                                  rejoue l'ORDRE du boot en transaction
#                                               ANNULÉE — un diagnostic sans effet,
#                                               jouable contre la base servie
#   key-index-rebuild                           ⚠️ #421, n'a JAMAIS tourné en prod :
#                                               l'appeler est une décision, pas une
#                                               routine (hors `all`, hors timer)
```

⚠️ **Le timer n'est posé qu'en PROD**, par `deploy/oto-backend.sh` au tag (jamais à la
main, jamais en crontab) : prod et preprod partagent la base, deux exécutants se
disputeraient les mêmes lignes.

## Pin oto-core — une version déployée = une coordonnée reproductible

- **`oto-core[browser]` PINNÉ sur un tag git** (`@ git+…@vX.Y.Z` dans `pyproject.toml`, plus `@main` flottant ni dép `oto-cli`) : une version déployée = coordonnée reproductible. ⚠️ **`pip` ne réinstalle PAS une dép VCS déjà présente** (`oto-core` "satisfait" quelle que soit sa version) → `pip install -e .` seul ne monte JAMAIS oto-core au tag bumpé. Le deploy **force-réinstalle** oto-core depuis le tag lu du `pyproject` (`pip install --force-reinstall …@$tag`). Bump connecteurs = tag oto-core + édit du pin + deploy (PAS de `git pull` box). Cf. ADR 0020. ⚠️ **Symptôme trompeur en LOCAL** : des tests rouges peuvent être un venv en retard sur le pin, pas du code cassé (05/08 : 17 tests d'un connecteur neuf échouaient, son module n'existant pas dans l'oto-core installé). Réaligner avant de conclure — `uv pip install --python .venv/bin/python --force-reinstall --no-deps "oto-core[…] @ git+…@<tag du pyproject>"`. (⚠️ box `otomata-0` a un VIEUX oto-mcp décommissionné/stoppé avec un editable legacy `oto-cli` pré-split — ne pas s'y fier, le runtime live est la box dédiée.) ⚠️ **Le pin est un champ que TOUTES les sessions // éditent → régressions silencieuses récurrentes** : vécu 2026-07-07, un commit concurrent a réécrit le pin `v1.18.0→v1.17.0` et **cassé un tool déployé SANS erreur** (le tool était enregistré, sa méthode absente de l'ancien oto-core → `AttributeError` seulement à l'appel). Toujours bumper en **superset** (tag haut ⊇ tags bas) ; à la moindre divergence de pin en merge/rebase, **garder la version haute**.
