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
