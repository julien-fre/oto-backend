---
title: Veille protocole MCP
type: reference
description: >-
  Acté le 2026-07-30 : on suit les SEP (décisions qu'on peut anticiper), pas les specs publi
  ées (faits accomplis). Ce qui nous concerne, d'où vient la règle, et les quatre points à t
  raiter d'ici la migration vers la spec 2026-07-28.
---

# Veille protocole MCP — suivre les SEP en amont

> Extrait de `CLAUDE.md` le 2026-08-27 — le contenu n'a pas changé, seule sa place a bougé.
> La carte garde le résumé + le pointeur ; le détail (schémas, incidents datés et leurs
> leçons) vit ici.

## La règle

**Règle : on suit les SEP, pas les specs.** Une spec publiée est un fait accompli ;
un SEP en discussion est une décision qu'on peut anticiper (voire influencer).
Domicile : PR markdown dans `seps/` du repo `modelcontextprotocol/modelcontextprotocol`
(SEP-1850 : numérotation dérivée de la PR, statut porté par les labels, sponsor
identifié). Revue périodique des SEP `proposed`/`accepted` qui touchent **ce qu'on
utilise** — transport streamable HTTP, autorisation/OAuth, tools, MCP Apps (SEP-1865),
tasks. On ignore ce qu'on n'a jamais adopté (sampling, roots, logging : dépréciés
le 2026-07-28, zéro occurrence ici).

## D'où ça vient

**D'où ça vient** : la spec `2026-07-28` rend MCP stateless (SEP-2575 : plus
d'`initialize`, plus de `Mcp-Session-Id`, contexte par appel dans `_meta` ; SEP-2567 :
l'état cross-appel passe par des handles en arguments d'outil). C'est **exactement**
l'ADR 0038, qu'on a tranchée en avance — mais empiriquement, en encaissant le bug en
prod (claude.ai frappe un `Mcp-Session-Id` neuf à chaque appel, cf. `call_axes.py`),
pendant que les SEP concernés étaient publics. Bon pari, mauvaise méthode.

## À traiter d'ici la migration

**À traiter d'ici la migration** (bloquée tant que FastMCP ne porte pas `2026-07-28`,
plancher actuel 3.4.2) : ① `ttlMs` + `cacheScope: **private**` obligatoires sur
`tools/list` (notre liste varie par identité, ADR 0015/0031 — un intermédiaire ne doit
jamais la partager) ; ② suppression de la résumabilité SSE ⟹ un stream cassé se rejoue
en requête neuve : les outils longs (browser, INPI, fullenrich) doivent être
**idempotents** ; ③ DCR déprécié au profit des Client ID Metadata Documents — notre
façade DCR (`oauth_facade.py`, palliatif du Logto self-hosted sans DCR) a une date de
péremption → épic sécurité auth/MCP #35 ; ④ MRTR (`resultType: "input_required"`)
remplace elicitation/sampling : **pas une dette** ici (nos `*_connect_start` /
`*_connect_status` sont déjà des handles), une standardisation possible.
