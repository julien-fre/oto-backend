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

## Ce que les clients font vraiment du protocole — relevés datés

La spec dit ce qu'un serveur peut émettre ; elle ne dit pas ce qu'un client en montre au
modèle. **Cet écart se mesure, il ne se déduit pas.** Chaque relevé ci-dessous est daté et
porte sa façon d'être reconstaté — un relevé qu'on ne sait pas refaire est une croyance.
(Les décisions d'architecture qui s'appuient dessus vivent dans le dossier de conception
interne ; côté oto, la page « Protocole MCP » n'est qu'un pointeur vers ce fichier.)

### Relevé 1 — le champ `instructions` du handshake n'atteint pas le modèle (28/08/2026)

Nos instructions composées font 17 292 caractères (socle 8 458 + catalogue 8 832, puis les
couches par-personne : contexte résolu, readme d'org, d'équipe, d'utilisateur).

| client | le serveur émet | ce que le modèle voit |
|---|---|---|
| Claude Code | oui, en entier | **les 2 048 premiers caractères**, coupés en plein mot, sans avertir (constante du bundle client, simple log debug) |
| claude.ai | oui, même chemin serveur | **rien** — la session testée ne reçoit aucun bloc d'instructions |

Conséquences ici : les couches personnalisées commencent au caractère 17 292 — les readme
d'org/équipe/utilisateur n'étaient **jamais** délivrés sous Claude Code (promesse ADR 0042
non tenue sur ce client), et l'écran « ce que voit l'agent » montre ce que le serveur
**compose**, pas ce que le client **reçoit**.

**Reconstater** : comparer la coupe à 2 048 de `instructions.compose_session()` avec la fin
du bloc reçu par une session Claude Code branchée sur oto (il se termine par
`… [truncated]`). Le 28/08, les deux tombaient au même caractère, au milieu du mot
« dessous ». Côté claude.ai : demander à une session ce qu'elle voit du connecteur.

**Et le canal s'affaiblit dans la spec `2026-07-28`** : `initialize` disparaît,
`instructions` déménage dans le résultat de `server/discover` — que le serveur DOIT
implémenter mais que **le client est libre de ne jamais appeler**. D'un canal toujours émis
mais mal rendu, on passe à un canal qui peut n'être jamais sollicité. Aucun plafond de
taille n'est documenté dans la spec : les 2 048 sont une décision de client.
(⚠️ `cacheScope: "private"` obligatoire sur ce résultat — cf. le point ① de la liste de
migration ci-dessus : notre `instructions` est composé par (sub, org).)

### Relevé 2 — la 1re ligne d'une description d'outil est un contrat de sélection (28/08/2026)

Sur les 556 outils servis : **68 % ont une description multi-lignes**, et **84 % du texte
des descriptions vit après la première ligne** — invisible au moment où le modèle choisit
un outil, chez au moins un client qui ne montre que la première ligne. **175 premières
lignes s'arrêtent au milieu d'une phrase** (rédigées comme des paragraphes).

**Deux troncatures distinctes, à ne pas confondre** : la nôtre (un saut de ligne au
mauvais endroit — réparable) et un plafond de longueur côté client (constaté sur une
première ligne complète de 81 caractères, coupée 2 caractères avant sa fin). Des premières
lignes **courtes et complètes** protègent des deux.

**Règle d'écriture** : la première ligne = une phrase complète, courte, autonome —
impérative si l'outil est une amorce (`oto_context` : « CALL THIS FIRST… »). Le détail
vient après, pour l'appel.

**Reconstater** : réchauffer le registre hors serveur (`register_all` + `warm_registry`,
cf. `docs/commands.md`) et compter.

### Relevé 3 — les canaux fiables, et ce qu'ils coûtent (28/08/2026)

Ce qui **arrive toujours** au modèle : les descriptions d'outils, et les résultats
d'outils.

**Mais l'injection dans un résultat se paie cher** : le bloc de contexte fait 12 889
caractères ≈ 3 000–3 500 jetons — mesuré sur une chaîne d'enrichissement en production,
**plus que le coût facturé d'un traitement entier** (~2 500 jetons, le reste servi du
cache). Et il arrive en **fin de préfixe** : jamais réutilisé par le cache du fournisseur,
payé plein tarif à chaque livraison. Sur une flotte, il double le coût du job qui le
reçoit.

**Contrainte dure, vécue le 28/08** : toute livraison dans un résultat doit être **sans
état conservé entre appels** (ADR 0038). Un « filet » livrant le bloc à la première
réponse d'outil, avec un registre mémoire « déjà servi » à fenêtre de 30 min, a vécu
quelques heures en prod (v1.155.0) avant retrait le jour même : le registre était un état
de session keyé sur une identité — et sous un jeton partagé par une flotte, la livraison
dépendait de qui avait appelé en dernier. Deux mesures utiles de l'épisode : une **balise**
explicite (« ceci n'est PAS le résultat de l'outil ») suffit à empêcher la recopie dans les
livrables (0 trace sur 14 fiches produites pendant la fenêtre) ; et une livraison « une
fois par identité » rend deux exécutions du même travail **non comparables** — rédhibitoire
pour toute mesure agentique.

### Relevé 4 — le flux SSE n'annonçait pas son charset (29/08/2026, #472)

`POST /mcp` répondait `content-type: text/event-stream` **nu**. Or HTTP/1.1 a laissé un
défaut historique pour `text/*` : **ISO-8859-1**. Un client qui l'applique lit
« dÃ©jÃ  » là où le serveur a écrit « déjà ». Mesuré sur `requests` :
`text/event-stream` → devine `ISO-8859-1` ; avec `; charset=utf-8` → `utf-8`.
`application/json` n'a jamais eu ce piège (RFC 8259 impose UTF-8, les clients le
savent) — il est complété par cohérence, pas par nécessité.

**Ce que le relevé N'EST PAS** : les octets servis sont, et ont toujours été, de
l'UTF-8 valide — vérifié sur le fil (`d\xc3\xa9j\xc3\xa0`). ⚠️ Le rapport d'origine de
#472 affirmait que 203 descriptions d'outil et 1 186 descriptions de paramètre
« arrivaient au modèle en double mojibake » : **c'est faux**, et la correction est
datée ici. Le mojibake était produit par les scripts d'analyse du runner, qui
laissaient leur bibliothèque HTTP deviner l'encodage. Aucun modèle n'a lu de charabia,
et aucune description n'a changé au correctif — seul l'en-tête bouge.

La cause est dans le SDK : `CONTENT_TYPE_SSE` / `CONTENT_TYPE_JSON`
(`mcp/server/streamable_http.py`) sont posés à la main dans le dict `headers`, ce qui
désactive le `populate_content_type` de Starlette — la seule mécanique qui aurait
ajouté le charset. FastMCP 3.4.2 n'expose aucun réglage dessus, d'où une couche ASGI
maison (`oto_mcp/response_charset.py`), posée dans `build_root_app` sous la garde de
déconnexion et au-dessus du dispatch par Host : elle couvre donc `/mcp` canonique,
`/mcp` anonyme des sous-domaines de projet **et** `/api/*` d'un seul geste.

**Reconstater** : `tests/test_response_charset.py` sert la même app avec et sans la
couche sur un vrai socket et la lit avec `requests` **sans toucher à
`response.encoding`** — mêmes octets des deux côtés, deux lectures opposées.

### Ce que ces relevés bornent

- Le contexte injecté d'office doit être **bref** — deux raisons indépendantes convergent :
  la troncature client et le coût par livraison. Garde-fou visé : un budget mesuré en CI,
  seuils par couche, release cassée au-delà.
- Le catalogue des capacités (8 832 caractères, la moitié de la composition) est le premier
  candidat au retrait : déjà interrogeable à la demande (`oto_list_my_tools`,
  `oto_tool_schema`).
- Un **garde-fou métier** (« validation avant envoi externe ») ne se confie pas à de la
  prose qu'un client peut ne pas transmettre : il se fait respecter **côté serveur, sur
  l'outil qui engage**, en refus nommé.
- Chantier : #478.
