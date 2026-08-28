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
jamais la partager) — ⚠️ **et la même exigence vaut pour `server/discover`, repérée le
2026-08-28** : son résultat porte `instructions`, que nous composons **par (sub, org)**
(readme d'org, d'équipe, d'utilisateur). Servi en `cacheScope: "public"` — la valeur de
l'exemple de la spec — un intermédiaire servirait le readme d'une org à une autre. Le
point ① ne visait que `tools/list` ; il vise les deux ; ② suppression de la résumabilité SSE ⟹ un stream cassé se rejoue
en requête neuve : les outils longs (browser, INPI, fullenrich) doivent être
**idempotents** ; ③ DCR déprécié au profit des Client ID Metadata Documents — notre
façade DCR (`auth/facade.py`, palliatif du Logto self-hosted sans DCR) a une date de
péremption → épic sécurité auth/MCP #35 ; ④ MRTR (`resultType: "input_required"`)
remplace elicitation/sampling : **pas une dette** ici (nos `*_connect_start` /
`*_connect_status` sont déjà des handles), une standardisation possible.

## Où vit le savoir MCP (rangé le 2026-08-28)

Trois domiciles, un rôle chacun — **chercher dans oto AVANT de re-dériver** (coût vécu le
28/08 : une journée à retrouver depuis le code et la spec ce qui était écrit depuis un
mois, deux affirmations fausses publiées puis corrigées) :

| quoi | où |
|---|---|
| **Décisions & architecture** (spec 2026-07-28, `server/discover`, budget P12, canaux fiables) | oto, projet 180 « oto headless », **page 480** (doc B §13.5) |
| **Relevés clients** — ce que les clients transmettent RÉELLEMENT au modèle, mesuré et daté, avec la façon de reconstater (troncature 2048 de Claude Code, claude.ai qui ne montre rien, 1re ligne d'une description = contrat de sélection, coût d'une injection en résultat, épisode du filet stateful) | oto, projet 180, **page 1178** |
| **Ce qui contraint le code d'ICI** (veille SEP ci-dessus, liste de migration, tripwires) | ce fichier |

Deux règles issues des relevés, appliquées dans CE repo : la première ligne d'une
description d'outil est une phrase complète, courte, autonome (175 outils à reprendre) ;
et toute livraison de contexte est **sans état conservé entre appels** (ADR 0038 — le
« filet » à registre mémoire a été posé puis retiré le 28/08, cf. page 1178).
