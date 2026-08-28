# Guides partagés — jeu « talent sourcing » (bibliothèque publique)

Guides **versionnés au repo** publiés dans la bibliothèque publique (table
vue `guide_library`) sous l'auteur **Otomata** : un
catalogue de skills que n'importe quelle org peut **forker**
(`oto_procedure(op='fork')`) dans son espace.

Diffère de `scripts/seed_doctrine_library.py` (le nom du SCRIPT, inchangé ; il
publie les skills d'une org
existante) : ici les guides sont des fichiers markdown, pas besoin d'org source.

## ⚠️ Deux foyers sous un même toit, et ils ne se sèment PAS pareil

| Où | Ce que c'est | Quand c'est semé |
|---|---|---|
| `oto_mcp/guides/*.md` (racine) | **guides PLATEFORME** (ADR 0042) | à CHAQUE boot, dans la table `guides`, servis dans l'index d'`oto_guide` |
| `oto_mcp/guides/<jeu>/*.md` (sous-dossier) | **guides de BIBLIOTHÈQUE** | jamais au boot — à la main, par un script de seed |

`guide_store.list_file_guides` balaie `guides/*.md` en glob **non récursif** :
un sous-dossier lui est invisible. C'est ce qui permet aux deux foyers de
cohabiter — et c'est aussi ce qui rend un fichier posé à la RACINE
immédiatement servi à tout le monde au prochain boot, sans autre geste.
Le garde-fou qui tient cette frontière est `tests/test_guides_seeds_foyer.py`.

## Format

Un fichier `<slug>.md` par guide, avec un front-matter `---` :

```markdown
---
slug: mon-skill
title: Titre lisible
description: Une phrase de résumé (affichée au catalogue).
category: Recrutement
tags: tag1, tag2, tag3
---

# Titre

Le corps markdown du guide…
```

⚠️ Ce front-matter n'est PAS celui des guides plateforme (`title` /
`description` seulement) : `slug`, `category` et `tags` n'ont de sens que pour
le catalogue de la bibliothèque.

## Publier / mettre à jour

Idempotent (upsert par slug, incrémente la version) :

```bash
# sur la box (DB accessible)
cd /opt/oto-mcp && ./.venv/bin/python -m scripts.seed_talent_doctrines
```

## Jeux de guides

- `talent-sourcing/` — RH / sourcing de talents / ATS : workflow de bout en bout
  (`talent-sourcing`) + skills `boolean-search`, `candidate-screening`,
  `ats-hygiene`, `recruiter-outreach`. Tirent parti des connecteurs ATS
  (greenhouse / lever / ashby / teamtailor / recruitee), LinkedIn (unipile),
  enrichissement (hunter / fullenrich / zerobounce) et outreach (lemlist).
