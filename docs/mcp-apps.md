---
title: MCP Apps & guides
type: reference
description: >-
  Les tools qui renvoient une interface rendue (`prefab_ui`, extension MCP Apps SEP-1865) : 
  convention `*_app`, import guardé, gotcha de l'annotation de retour, et le régime tout-DB 
  des guides (seeds de boot vs table `guides`).
---

# MCP Apps — UI rendue (SEP-1865) & guides

> Extrait de `CLAUDE.md` le 2026-08-27 — le contenu n'a pas changé, seule sa place a bougé.
> La carte garde le résumé + le pointeur ; le détail (schémas, incidents datés et leurs
> leçons) vit ici.

## Le mécanisme

Certains tools renvoient une **interface rendue** (carte/table dans un iframe
sandbox côté host : claude.ai, VS Code…) au lieu de JSON brut, via l'extension
MCP Apps (SEP-1865, stable). Implémenté avec **`prefab_ui`** (extra
`fastmcp[apps]`, déclaré dans `pyproject.toml` → installé par le `pip install -e .`
du deploy) : un tool `@mcp.tool(app=True)` renvoie un composant `prefab_ui`
(`Card`/`Column`/`Heading`/`Text`/`DataTable`) que le host peint ; dégradation
gracieuse en texte pour les clients sans support.

## Convention `*_app`

**Convention** : variantes **flagship `*_app`** (≠ remplacer les tools JSON), où
un visuel aide vraiment l'utilisateur. Les tools JSON équivalents restent la voie
par défaut/agent (« si le rendu échoue, utiliser le tool JSON équivalent »).
L'import de `prefab_ui` est **optionnel et guardé** dans le module (si l'extra
manque, les `*_app` ne s'enregistrent pas, les tools JSON restent). Premier jeu :
`tools/foncier.py` → `foncier_site_app` (fiche site : géocodage + parcelle +
bâti), `foncier_comparables_app` (ventes comparables DVF autour d'une adresse),
`foncier_prix_m2_app` (stats €/m² d'une commune). Mêmes clients open-data que les
tools JSON ; rendu **défensif** (colonnes dérivées des clés réelles) pour ne pas
dépendre d'un nom de champ. Gatés par le connecteur (namespace `foncier`).

## Apps spine, gotchas, et guides tout-DB

Depuis, deux apps **spine** (hors gate) : `data_app` (datastore — table + fiche v2
schema-aware, `tools/datastore.py`) et `oto_doc_app` (pages/docs + KB, lecture
seule, `tools/docs_app.py`). ⚠️ Gotcha récurrent : **pas d'annotation de retour
`-> Card`** sur un tool `app=True` (hints résolus contre les globals du module au
build du schéma, or l'import prefab_ui est local à `register()` → NameError fatal
au boot, vécu #69). **Doc consommable par les agents = guide plateforme `mcp-apps`**
(servi par `oto_guide`, inventaire + quand app vs JSON + replis) — à tenir à jour
quand une app s'ajoute. ⚠️ **Guides = tout-DB (2026-07-16)** : la table `guides` est
la source de vérité des TROIS scopes on-demand (platform/org/user) ; les fichiers
`oto_mcp/guides/*.md` ne sont que des **seeds de boot** (`seed_platform_guides`,
idempotent, n'écrase jamais une ligne DB). Écriture platform = platform_admin
(MCP `oto_guide op=write scope=platform` / REST `PUT /api/me/guides/platform/{slug}`
/ dashboard `/platform/instructions`). Une édition durable doit AUSSI retoucher le
fichier seed (sinon un environnement neuf naît avec l'ancien texte). **Surface = UNE
capacité `me.guide`** (`capabilities/guides.py`, ADR 0042 §Convergence des surfaces,
2026-07-28) : `oto_guide` op-aware côté MCP + `me.guides.*` côté REST, **mêmes
handlers, une seule autz de scope** (`_owner_for_write`) — l'ex-`tools/guide.py`
(qui redéclarait la sienne) est supprimé. `scope` omis à l'écriture = `user`. Le cap
64 KB et le refus d'un corps vide s'appliquent désormais **aux deux faces**.
