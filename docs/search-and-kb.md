---
title: Recherche transverse & KB projets
type: reference
description: >-
  `oto_search` : un seul chemin de code (RRF lexical + sémantique), les grains matchés (page
   / ligne / tableau / fichier), l'invariant « cherchable ⇔ lisible » et son tripwire, les e
  mbeddings Mistral, l'épine de projet, les backlinks et les propositions de modification.
---

# Recherche transverse & KB projets (`oto_search`)

> Extrait de `CLAUDE.md` le 2026-08-27 — le contenu n'a pas changé, seule sa place a bougé.
> La carte garde le résumé + le pointeur ; le détail (schémas, incidents datés et leurs
> leçons) vit ici.

## `oto_search` — le verbe « retrouver »

**`oto_search`** (capacité `me.search`, MCP + `GET /api/me/search`) = LE verbe « retrouver »,
un seul chemin de code (`search.py` orchestration RRF k=60 · `db/search.py` SQL par source +
**expressions d'index = source unique index↔requête**, GIN d'expression, config `french` +
repli d'accents `translate`). Sources : pages/briefs/procédures/guides (passages, ts_headline
sur la saisie BRUTE) ∪ tableaux/fichiers/connecteurs (conteneurs, matchés en mémoire).
⚠️ **Deux grains distincts pour un tableau** : `tableau` = le CONTENEUR, matché en mémoire
sur le seul **nom du namespace + les labels de colonnes** ; `ligne` = le CONTENU des
tableaux (#67 V2.1, `_match_rows`), FTS sur les lignes elles-mêmes. Chercher « tableau »
seul ne trouvera donc jamais une valeur DANS une ligne — c'est `ligne` qu'il faut. Un
**fichier** reste matché sur `filename+title+description`, **jamais son contenu**.
**Invariant « cherchable ⇔ lisible »** : docs/briefs/fichiers scopés
`ownership.accessible_project_ids` (factorisation du scoping d'`op=list` — JAMAIS
`can_access`, cross-org) ; **tripwire par source = critère de merge**
(`test_search_scope_tripwire.py`). Le catalogue connecteurs est INJECTÉ par la capacité
(pas d'inversion de couche). `oto_doc(op=search)` = rerouté, déprécié. Fichiers matchés sur
`filename+title+description` (jamais `summary`, colonne morte).

## Sémantique + RRF (20/07)

**Sémantique + RRF (20/07, LIVE preprod)** : fusion LEXICAL + SÉMANTIQUE des pages.
`embeddings.py` = client Mistral `mistral-embed` (1024) — **sync `embed_texts`** (worker, batch DÉCOUPÉ sous le
budget de tokens/requête : 400 « too many tokens overall » sinon ; cap ~16k ch/input)
+ **async `embed_query`** (chemin requête). Outbox `docs.embed_dirty` (marqué à
create/update, coût nul) + `doc_embeddings(halfvec(1024))` + index HNSW cosine ; worker
`embed_worker` (boucle de fond composée au lifespan, embed HORS event loop via
`run_in_threadpool`, idempotent par `content_sha`) draine. Handler `oto_search` ASYNC :
embed la requête hors boucle → `search.search(query_embedding=…)` ajoute la source
`page`/`matched_by='semantic'`, la fusion RRF DÉDUPLIQUE (kind,ref) + SOMME les rangs
(une page trouvée par les deux remonte ; passage lexical conservé). **Dégradation
gracieuse** : sans `MISTRAL_API_KEY` ou sur échec → lexical seul, jamais un prérequis.
pgvector 0.8.2 sur otomata-main (`CREATE EXTENSION vector` AVANT `_SCHEMA` car halfvec en
dépend). Le **golden set JB** cale désormais la QUALITÉ (plus le *si*).

## Se repérer : chapô, ordre curé, épine, backlinks, propositions

**Se repérer** : `docs.description` (chapô ; fallback DÉRIVÉ À LA LECTURE `derive_description`,
jamais stocké) + `docs.position` (ordre curé, entiers ×16 ; `move_doc(parent?, position=INDEX)`
réindexe la fratrie ATOMIQUEMENT) + **épine** `oto_project(op=get, include=['spine'], from_doc?,
depth?)` bornée (N+2, plafond 200, compteurs `more`) — la carte que l'agent lit avant
`oto_doc(op=get)`, jamais `op=list` de tout. **KB d'org ancrée PAR ID** (`orgs.kb_project_id`,
claim optimiste anti-doublon, auto-réparation transfert/archive — le nom n'est plus un marqueur).
Le lien `project_links.target_type='doc'` est RETIRÉ ; relier des pages =
les **backlinks `[[…]]`** (Ship 4, LIVE) : résolus À L'ÉCRITURE (hook `db.create/update/
delete_doc` — JAMAIS capacité, `resolve_change` appelle db en direct), précédence projet >
KB (`db/backlinks.py`), table dérivée `doc_links` (CASCADE 2 côtés), `oto_doc op=backlinks`
= « Cité par » filtré accès. **Propositions modif+création + inbox** (Ship 3, LIVE) : « les
lecteurs proposent » — un viewer (lecture sans écriture) qui crée/modifie obtient une
PROPOSITION (`doc_change_requests`, `doc_id` nullable + `project_id` + emplacement + CHECK) ;
le dispatch `docs.py` route resolve/list/create-proposal sur request_id/project_id **AVANT
le gate doc_id** (une création doc_id NULL était sinon inatteignable) ; `me.inbox`
(`GET /api/me/inbox`, 2 voies À traiter/Récent, 200-vide sans org).

## Seam `pending_action`

**Seam `pending_action`** (`status_hints.py`, patron connector_verify) : un connecteur à
connexion en deux temps enregistre un hook « quelle étape manque ? » → `ProviderStatus.
pending_action` (fail-open) que le front rend tel quel en verdict+CTA. La spécificité vit
DANS le module connecteur (unipile : « Connecte un canal »), jamais dans le modèle commun.
