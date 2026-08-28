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
façade DCR (`oauth_facade.py`, palliatif du Logto self-hosted sans DCR) a une date de
péremption → épic sécurité auth/MCP #35 ; ④ MRTR (`resultType: "input_required"`)
remplace elicitation/sampling : **pas une dette** ici (nos `*_connect_start` /
`*_connect_status` sont déjà des handles), une standardisation possible.

## Ce que les CLIENTS font vraiment du protocole — relevé, pas spec

La spec dit ce qu'un serveur peut émettre ; elle ne dit pas ce qu'un client en montre
au modèle. **L'écart se mesure, il ne se déduit pas.** Ce tableau est un relevé daté :
on y ajoute une ligne à chaque fois qu'on constate un écart, avec la façon de le
reconstater.

### `instructions` de l'`initialize` — canal NON fiable (relevé 2026-08-28)

| client | le serveur émet | ce que le modèle voit |
|---|---|---|
| Claude Code | oui, > 2048 car. | **les 2048 premiers**, coupés en plein mot, sans avertir l'utilisateur (log debug côté client) |
| claude.ai | oui, même chemin serveur | **rien** — d'après une session testée sur ce connecteur |

**Comment le reconstater sans jeton** : composer `instructions.compose_session()` et
comparer sa coupe à 2048 avec la fin du bloc reçu dans une session Claude Code. Le
2026-08-28 les deux tombaient au même caractère, au milieu du mot « dessous ».

**Ce que ça emportait chez nous** : les couches personnalisées (contexte résolu, agent
readme d'org, d'équipe, d'utilisateur) commencent au caractère 17 292 de la
composition — 8,4 fois au-delà du plafond. Elles n'étaient donc **jamais** délivrées
sous Claude Code, alors qu'ADR 0042 en fait le primitif d'instruction livré à chaque
session. Et l'écran de transparence `/api/me/agent-context` montrait ce que le serveur
COMPOSE, pas ce que le client REÇOIT.

⚠️ **Correction du 2026-08-28** — cette section a affirmé quelques heures que « le canal
disparaît de toute façon, la spec supprime `instructions` ». **C'est faux, et l'erreur
venait d'un raccourci** : SEP-2575 retire `initialize`, pas `instructions`. Vérifié à la
source : le champ **déménage** dans le `DiscoverResult` de **`server/discover`**, que les
serveurs **DOIVENT** implémenter.

Mais le canal en sort **plus faible, pas plus fort**, et c'est ce qui compte pour nous :

| | `initialize` (≤ 2025-11-25) | `server/discover` (2026-07-28) |
|---|---|---|
| le client l'appelle | **obligatoirement** (poignée de main) | **facultativement** — « a client may invoke any RPC inline » |
| donc `instructions` est | toujours transmis, diversement rendu | **peut n'être jamais demandé** |

On passe d'un canal toujours émis que le client rend mal, à un canal que le client peut
ne pas solliciter du tout. La conclusion pratique ne change pas — ne pas en dépendre —
mais le motif, si : ce n'est pas une suppression, c'est un passage d'obligatoire à
optionnel.

**Ce qu'on en a fait** (oto-backend#478) : le filet
(`middleware/context_net.py`) livre le bloc dans la première réponse d'outil d'une
session qui ne l'a pas chargé, et la première ligne d'`oto_context` est devenue un
impératif. Les deux passent par des canaux qui, eux, arrivent toujours au modèle.

### `description` d'un outil — la PREMIÈRE LIGNE est un contrat de sélection

Relevé le 2026-08-28 sur nos 556 outils : **381 (68 %) ont une description
multi-lignes**, et **84 % du texte des descriptions vit après la première ligne**. Or
c'est la première ligne qui décide de la sélection chez au moins un client. **175 de nos
premières lignes s'arrêtent en plein milieu d'une phrase.**

Deux troncatures distinctes, à ne pas confondre : la nôtre (un saut de ligne au mauvais
endroit, réparable) et un plafond de longueur côté client, constaté sur une première
ligne pourtant complète de 81 caractères, coupée deux caractères avant sa fin. Écrire
des premières lignes **courtes et complètes** protège des deux.

**Règle qui en découle** : la première ligne d'une description est un contrat de
sélection — phrase complète, courte, autonome, impérative si l'outil est une amorce.
Le détail vient après, pour l'appel.

