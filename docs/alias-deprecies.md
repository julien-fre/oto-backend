---
title: Alias dépréciés et dates de retrait
type: reference
description: >-
  La table UNIQUE des noms servis qui portent encore l'ancien vocabulaire du
  produit (#519, « doctrine » → « guide ») : pour chaque surface — outil MCP, clé
  de capacité, chemin REST, clé de réponse, schéma OpenAPI, code d'erreur, objet en
  base — l'ancien nom, le nouveau, la forme de la coexistence, et LA date de
  retrait. Explique pourquoi la date se compte en tags et non en merges, où elle
  vit dans le code (oto_mcp/deprecations.RETRAIT), et ce qu'un consommateur doit
  faire avant cette date. À charger avant de renommer une surface, avant de
  consommer un nom qui figure ici, et au moment du retrait (lot D, #526).
adr:
  - "0042"
---

# Alias dépréciés et dates de retrait

Le produit a changé de mot le 28/08/2026 (#519, décision d'Alexis) : il dit **guide**
(ADR 0042 — le guide est la primitive unique d'instruction) et **procédure** pour ce
qui s'exécute. L'ancien mot servait pour deux choses à la fois, l'objet produit *et*
« principe maison » ; c'est cette double vie qui prête à confusion.

Le **lot A** (PR #525) l'a retiré de l'interne sans changer un octet servi. Le **lot
B** renomme les SURFACES — et une surface ne se renomme pas, elle se **double**.

## La règle

> **Rien de servi ne disparaît dans le lot B.** Tout gagne son nouveau nom ;
> l'ancien devient un alias déprécié, avec une date de retrait écrite là où le
> consommateur la lit.

Trois raisons, dans l'ordre de ce qu'elles coûtent :

1. **Nos appelants vivent hors de ce dépôt** : dashboard, extension, CLI, oto-core,
   plugin, fronts partenaires, flotte d'agents. Un renommage sec ne casse rien en
   CI — il casse en production, chez quelqu'un d'autre, sans trace.
2. **La prose déjà écrite cite les anciens noms** : procédures d'org, guides, corps
   de guide, messages d'erreur archivés. Personne ne la réécrit d'un coup. Un agent
   qui suit une procédure de 2026-07 doit continuer à aboutir.
3. **La base est PARTAGÉE prod/preprod** (ADR 0065) : un objet renommé sur `main`
   est renommé sous la prod du même geste. Les renommages en base sont donc
   **additifs** (vue d'abord, table ensuite, au tag).

## La date

**Retrait au premier tag `vX.Y.Z` posé à partir du 27/09/2026** — 30 jours après la
décision.

⚠️ **Un tag, pas un merge.** `main` est la PREPROD : un alias retiré au merge serait
retiré du serveur que les intégrateurs sondent, avec 30 jours de préavis annoncés et
zéro jour servi.

La date vit à **un seul endroit** dans le code — `oto_mcp/deprecations.RETRAIT` — et
chaque avis servi la recopie depuis là. Décaler le retrait est alors un geste (changer
la constante), et non une chasse aux chaînes dans quarante descriptions dont on
oublierait trois. `tests/test_alias_deprecies_outils.py` garde cette propriété, et
**rougit quand la date est dépassée** : c'est la sonnerie du lot D.

Le retrait lui-même = **lot D, issue #526**, qui porte la liste complète, le
préalable de blocage (le lot C — dashboard, oto-core, oto-cli, plugin — doit avoir
basculé) et la migration en base.

## La table

Une ligne par surface. « Forme » dit comment les deux noms coexistent.

| Surface | Ancien nom (part le 27/09/2026) | Nouveau nom | Forme | Lot |
| --- | --- | --- | --- | --- |
| Outil MCP | `oto_admin_doctrine` | `oto_admin_guide` | les deux listés et appelables ; l'ancien porte l'avis en tête de sa description | B1 |

*(Cette table se remplit au fil du lot B : capacités et chemins REST en B2, clés de
réponse et schémas en B3, objets en base en B4.)*

## Ce qu'un consommateur doit faire

1. **Lire la liste ci-dessus** et chercher les anciens noms dans son code.
2. **Basculer sur le nouveau nom** — il répond déjà, aujourd'hui, à l'identique.
3. Ne pas attendre le retrait pour le découvrir : après le tag, l'ancien nom ne
   répond plus du tout.

## Comment c'est fait, côté serveur

**Les outils MCP** : le doublage se fait au **bord du protocole**, dans
`ToolAliasMiddleware` (`oto_mcp/middleware/alias.py`), le middleware le plus externe.
`tools/list` sert les deux entrées ; `tools/call` rétablit le nom canonique **avant**
que quoi que ce soit d'autre ne le lise. C'est ce qui garantit que rien en aval —
gates de contexte d'appel, denylist de visibilité, journal `tool_calls`, refs
`<tool:slug>` des procédures — n'apprend qu'un alias existe, donc que rien en aval ne
peut diverger. L'alias n'est **jamais monté comme un vrai outil** : monté, il
doublerait le journal, échapperait au toggle posé sur son canonique, et survivrait au
lot D sans qu'on le voie.

L'alias est **dérivé de la liste réellement servie**, jamais du registre : il hérite
donc du filtrage de visibilité de son outil. Un outil masqué pour un compte ne
réapparaît pas par son ancien nom — ce serait un contournement de la denylist, pas
une compatibilité.

## Le cliquet

`tests/test_vocabulaire_guide.py` compte les occurrences de l'ancien mot dans
`oto_mcp/`, fichier par fichier, et refuse trois choses : un fichier neuf qui le
reprend, un fichier qui en porte **plus**, et un plafond **qui n'est plus atteint**.
Le lot B baisse le compte à chaque PR ; le lot D le met à zéro et supprime la table.
