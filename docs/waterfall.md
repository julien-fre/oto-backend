---
title: Le waterfall natif — email, téléphone, société
type: explanation
description: >-
  `waterfall(op=email|phone|company)` : une cascade de fournisseurs
  DÉJÀ connectés, arrêtée au premier trouvé, où chaque étape est un appel ordinaire de
  l'outil du fournisseur (`meta.executer_cible`). Ce que la cascade garde de chaque
  étape (garde, clé, facturation, rédaction), ce qu'elle refuse d'être (un appel
  d'outil arbitraire, un fournisseur asynchrone, un lot), et comment on y ajoute une
  étape. À lire avant de toucher `oto_mcp/tools/waterfall.py`.
---

# Le waterfall natif

## Le problème

Trouver l'email d'une personne, c'est essayer Hunter, puis Apollo, puis Kaspr… jusqu'au
premier qui répond. Un agent le fait à la main : un appel par fournisseur, une lecture
de chaque réponse dans son contexte, une décision « trouvé ou pas » à chaque fois — et il
paie des jetons pour un travail sans jugement. FullEnrich vend cette cascade, mais chez
lui, sur ses fournisseurs, à son prix ; l'org qui a déjà ses propres clés n'en profite pas.

## Le principe

Un outil, `waterfall` (lecteur seul, donc pas de `waterfall_act` : convention
`<ns>` / `<ns>_act`), et une table d'étapes par `op` (`EMAIL_STEPS`, `PHONE_STEPS`, `COMPANY_STEPS`).
Une étape = un outil de connecteur existant, les entrées qu'il exige, la façon de
construire ses arguments et de lire sa réponse. La cascade s'arrête au **premier**
trouvé (choix produit : le moins cher, pas le plus sûr — l'email n'est pas vérifié ici).

**Chaque étape passe par `meta.executer_cible`**, la couture d'`oto_call` et des
recettes. Elle garde donc tout ce qu'un appel direct a : la garde d'activation du
connecteur pour l'org et l'équipe de l'appel, la cascade de clé, le niveau d'accès, la
liste d'outils d'un travail hébergé, **sa** ligne dans `tool_calls` (donc sa `quantity`
et son `key_mode` : la facturation reste sur la ligne du fournisseur, jamais sur celle
du waterfall), et la rédaction de **son** namespace. Le waterfall n'a ni clé, ni
facturation propres (`providers/waterfall.py`, `secret_kind="none"`).

## Ce qu'une étape qui échoue fait : rien d'autre que passer la main

Une garde qui refuse (`McpError` levée par `executer_cible` : connecteur coupé, hors
liste du travail), un échec du fournisseur (`IssueCible(ok=False)` : clé absente —
classée `invalid_input` par la taxonomie, il n'existe pas de code « non connecté » —,
compte vide, limite de débit, panne), un résultat retenu par la rédaction, une réponse
sans valeur : tout est écrit dans `attempts` (statut, code, message) et l'étape suivante
est essayée. Seules les fautes de l'appelant lèvent : aucune étape possible avec ces
entrées, une clé de `providers` inconnue, un SIREN malformé.

⚠️ **Un trouvé se juge à la FORME de la valeur** : une adresse qui a la forme d'un email,
un numéro qui a au moins six chiffres. Un fournisseur qui renvoie l'entrée qu'on lui a
donnée n'a rien trouvé ; un `person._stub` d'Apollo (fiche vide créée sur un identifiant
faible, pourtant facturée) non plus ; une ligne Lusha porteuse d'`error` non plus.
⚠️ **Une recherche par nom rend toujours quelque chose** : `fr_search` ne compte que si
le domaine correspond ou si le nom demandé est contenu dans la raison sociale — un
homonyme arrêtant la cascade cacherait la bonne réponse plus bas.

## Ce que le waterfall refuse d'être

- **Un appel d'outil arbitraire** : `providers=[…]` réordonne ou restreint les clés de
  la table, jamais un nom d'outil. C'est ce qui tient une écriture (ou le waterfall
  lui-même) hors d'une cascade.
- **Un fournisseur asynchrone** : Dropcontact, FullEnrich, lemlist, la révélation de
  téléphone d'Apollo répondent plus tard (sondage ou webhook) ; ils ne peuvent pas être
  une étape d'un appel qui doit répondre dans le délai d'un outil. Ils restent appelables
  directement.
- **Un lot** : une personne par appel. Une liste passe par une recette (`oto_recipe`,
  `for_each` sur les lignes d'un tableau) — d'où la déclaration `LECTURE` de
  l'outil : ils ne changent rien chez aucun tiers, même si leurs étapes dépensent des
  crédits (c'est le sens de `lecture.LECTURE`, qui ne dit pas « gratuit »). ⚠️ La recette
  ne juge que le waterfall ; ses étapes, appelées par `executer_cible`, ne sont pas
  rejugées « en lecture » — elles le sont toutes (des lectures d'enrichissement), et
  c'est la table fermée qui le garantit.

## Les étapes, et pourquoi pas les autres

- **Email** : hunter (nom + domaine) → apollo (URL LinkedIn, ou nom + domaine /
  société) → aiark (URL) → kaspr (URL) → forager (URL) → lusha (URL, ou nom + domaine /
  société).
- **Téléphone** : kaspr → aiark (`op=mobile`) → forager (`op=phone_numbers`) → lusha.
- **Société** : `fr_get` (SIREN) → `fr_search` (nom, France seulement, gratuit) → apollo
  (domaine, 1 crédit).
- **Absents, et pourquoi** : Cognism (ce que l'enrichissement rend dépend du contrat de
  la clé, et la forme de sa réponse n'est figée nulle part ici), HitHorizons (deux appels
  par société), Topograph et Crunchbase (un « introuvable » ne s'y distingue pas d'une
  panne : `internal`). Kaspr, Forager et Lusha sont lus par une extraction profonde
  (toute clé qui contient `email` / `phone`, hors celles qui ne font que qualifier) parce
  que la forme de leur réponse n'est figée par aucun test du dépôt.

## Ajouter une étape

Une ligne `Step(...)` dans la table : la clé, l'outil (synchrone, déjà monté — vérifié
par `tests/test_waterfall.py`), les groupes d'entrées exigées, `build`, `extract` (qui
lit le résultat SERVI, après rédaction) et une note de coût. Un test de son extraction
sur une réponse réelle du fournisseur, anonymisée.
