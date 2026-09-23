---
title: Notice — le mode d'emploi d'oto
description: Mode d'emploi complet de la plateforme — le socle du handshake n'en est que le résumé ; à lire en début de session.
---

Le socle injecté au handshake est un RÉSUMÉ : plusieurs clients le tronquent (~2 000
caractères) ou ne le livrent pas du tout. Ce guide est la version intégrale — chaque
règle du résumé est détaillée ici. Après lecture, charge ton contexte — celui de
ton org (son readme, celui de ton équipe, ses projets récents) et le tien (ta fiche,
ta note) : `oto_context` — et recharge-le après un changement d'org/équipe/projet.

## Cherche une procédure AVANT d'agir

Avant une tâche substantielle, vérifie s'il existe une procédure pertinente (`oto_procedure op=list`), y compris hors du projet courant. Une procédure existante fait autorité sur ta propre méthode.

## Écrire une procédure

Une procédure se lit en prose : des étapes numérotées, que l'agent qui l'exécute suit dans l'ordre. Aucun dessin n'est requis. Si le corps que tu lis porte une ligne `<!-- flowchart: … -->`, c'est le dessin d'une version existante mis de côté : recopie-la telle quelle pour le conserver.

## Encadre et remonte

Quand tu exécutes une procédure — un guide nommé OU un déroulé one-shot qui mérite d'être tracé — ouvre-la par `run_start(label, guide?)` (passe `guide`=slug pour un guide nommé, omets-le pour un run ad-hoc) et ferme-la par `run_finish(run_id, outcome)` (done|partial|failed|blocked — `partial` = terminé proprement mais pas tout fait : la `note` dit alors ce qui est fait et ce qui reste). **Remonte tout signal d'usage** via `feedback(signal, kind, target, text?)` : `signal='gap'` quand oto ne couvre PAS ce dont tu as besoin (outil, guide ou donnée manquants — `target`=ce que tu voulais faire) plutôt que d'abandonner en silence ; `signal='tool_feedback'` quand un outil se comporte mal ou excellemment (`target`=le nom de l'outil). **Déclenche-le DE TOI-MÊME, immédiatement, sans attendre que l'utilisateur te le demande** : dès qu'un outil échoue (erreur, timeout), renvoie un résultat trompeur/vide/incohérent, ou qu'une capacité te manque pour agir — appelle `feedback` sur le coup, puis poursuis. Un signal manqué = un bug que la plateforme ne verra jamais. C'est ainsi que la plateforme apprend.

## Travaille dans un projet

Un projet est le foyer d'une tâche : son contexte (brief, tableaux, connecteurs préconfigurés, procédures). Quand tu agis POUR un projet, passe le jeton `_project=<id>` sur CHAQUE appel de travail (liste/charge via `oto_project` op=list/get — aucun état de session) : tes connecteurs prennent alors l'identité préconfigurée du projet, l'org du projet s'applique, tes runs lui sont rattachés, et tes tableaux de sortie doivent y être liés (`oto_project(op=link, target_type=tableau)`). Une procédure exécutée dans un projet partage SES ressources (tableaux, connecteurs) : ne crée pas de ressources propres à la procédure. Pour une tâche ad-hoc sans projet existant (extraction one-shot, prospection ponctuelle…), **crée un projet** pour héberger sa sortie et sa trace plutôt que de travailler hors-sol.

## Porte ton contexte DANS l'appel, jamais dans un état de session

Il n'y a AUCUN état de session serveur : quand une action dépend d'un contexte précis, passe-le EN PARAMÈTRE de l'appel. **Les jetons de contexte sont préfixés `_`** (ils sont à la plateforme ; sans préfixe, le nom appartient aux arguments métier de l'outil) — `_project=<id>` (le jeton PRIMAIRE : org du projet, slots `slot:<nom>`, identités connecteur préfaites), `_org=<id>` / `_group=<id>` (agir dans une org/équipe donnée), `_account=<label>` (connecteur multi-compte, ex. « 2 Zoho » — `oto_identity(op="list")` liste les labels), `_instance=<ref>` (une instance de connecteur PRÉCISE — un credential exact, refs via `oto_instance(op="list")`), `_run_id=<id>` (rattacher l'appel à un `run_start`). **Les cinq premiers sont OPTIONNELS : omis, chacun prend son défaut** — ton org courante, aucun projet, ton compte par défaut, la résolution de credential normale. Ne les passe que pour t'écarter de ce défaut. **`_run_id` fait EXCEPTION : dès que tu as ouvert un run (`run_start`) ou réservé une ligne (`data_claim_next`), passe-le sur CHAQUE appel jusqu'à `run_finish`.** Il n'est hérité que si le serveur tient une session avec un run actif — ne compte pas dessus : sans lui, une écriture sur une ligne que tu as toi-même réservée est REFUSÉE, et tu perds la ligne. Leur description au schéma est volontairement d'une ligne, le fond est ici. Les `oto_use_*` ne posent plus d'état : ils valident l'accès et te rappellent le jeton à passer.

## Ce qui dure : trois domiciles

Ce que tu apprends et qui doit survivre à la session a un domicile selon ce qu'il décrit :

- **Comment l'organisation travaille** — règles, conventions, vocabulaire, qui fait quoi, ce qu'il ne faut jamais faire : son **guide et son contexte**. `oto_context` te les rend (à charger en début de session) ; ils s'écrivent par `oto_guide` (`scope=org`, réservé à un admin d'org — si tu ne l'es pas, propose le texte à la personne plutôt que de le ranger ailleurs). Un déroulé qui se rejoue est une **procédure** (`oto_procedure`).
- **Ce qui concerne la personne** — son rôle, ses objectifs, ses préférences, sa façon de travailler : **sa fiche** (`oto_profile op=update`, ce qu'elle t'a dit, jamais une supposition) et **sa note** (`oto_guide scope=user delivery=init`, sa voix à elle). Les deux sont relues à chaque session.
- **Les faits d'un travail** — sources, comptes rendus, notes de référence, livrables : **un doc dans le projet concerné** (`oto_doc op=create|update`, `kind=source|note`, avec le `project_id` du projet auquel le fait appartient — `oto_project op=list` pour le choisir, `op=create` pour en ouvrir un). Une page en cite une autre de n'importe quel projet de l'org par `[[Titre exact]]`.

**Réflexe** : avant le web, cherche dans ce que l'org sait déjà — son contexte (`oto_context`), ses procédures (`oto_procedure op=list`), ses documents (`oto_doc op=search`) — et capture au bon domicile plutôt que de laisser filer. ⚠️ Il n'y a pas de « base de connaissance » à part : ce qu'une procédure ou une page appelle encore ainsi est un projet ordinaire de l'org, dont les pages se lisent et s'écrivent avec `oto_doc` comme celles de n'importe quel projet.

## Un outil non listé ? Appelle-le quand même via `oto_call`

`oto_call(name, arguments)` est le pont universel : il exécute par son nom N'IMPORTE quel outil du catalogue — un outil masqué, un outil de FOD, ou un connecteur que tu VIENS d'activer. ⚠️ Activer un connecteur en cours de conversation ne monte PAS ses outils dans la session (le registre est figé à l'ouverture, et claude.ai n'applique pas le rechargement à chaud) : n'en conclus JAMAIS « la capacité n'existe pas ». Appelle-le tout de suite via `oto_call(name="<connecteur>_…", arguments={…})` — il accepte aussi `_org=` pour exécuter sous une org donnée — ou invite l'utilisateur à ouvrir une NOUVELLE conversation pour les voir montés. (Un sous-agent que tu lances hérite du même registre figé → lui aussi passe par `oto_call`.)

⚠️ **Un outil absent de ta liste n'est PAS un connecteur en panne.** La liste d'une session — y compris d'une session planifiée, sur un connecteur activé de longue date — peut ne pas montrer des outils qui répondent parfaitement : c'est un **filtre d'affichage**, pas un défaut d'accès. La voie attendue est `oto_call(name="<outil>", arguments={…})`. Une panne se constate sur la RÉPONSE d'un appel (erreur nommée), jamais sur une absence de la liste : ne rapporte jamais « connecteur en panne » ou « connecteur absent » avant d'avoir appelé l'outil par `oto_call`.

## Ta boîte à outils = ce qui est installé ; le reste s'INSTALLE

Ta boîte à outils, pour cette org, contient ce qui y est installé pour toi : par toi, ou par le **kit de ton organisation** (la liste que son admin installe chez tous ses membres — un membre peut retirer ce que le kit a posé). Une org sans kit démarre sans aucun connecteur : c'est le régime normal, pas une panne. Un connecteur installé mais coupé par l'org reste masqué jusqu'à sa réouverture. Si la toolbox ne montre (presque) que des outils `oto_*`/`data_*`, ton rôle est de GUIDER : comprends ce que l'utilisateur veut faire, repère les capacités correspondantes dans le catalogue de namespaces injecté au handshake (ou `oto_connector(op='list')` pour l'état par connecteur), propose-en 2-3 pertinentes et installe-les (`oto_connector(op='select', name=…)`). N'attends pas le remontage : exécute tout de suite via `oto_call`. Les capacités open data (`fr_*`, `foncier_*`, `juris_*`…) et à free tier (serper, hunter…) marchent sans aucune configuration ; celles à clé ou à compte se connectent sur le dashboard — dis-le simplement, ne simule jamais un résultat.

## Slots : la procédure déclare, le projet binde

Une procédure déclare ses entités requises en slots nommés et sa prose les référence `<slot:name>` — jamais un nom d'instance en dur. Le projet fait la correspondance nom→instance via ses liens (`oto_project(op=link, …, slot='name')` ; pour un connecteur, `instance_ref=<ref>` binde un credential EXACT). Tu adresses le tableau d'un slot avec `datastore='slot:<name>'` sur les tools `data_*`, dans le cadre du projet (`_project=<id>` sur l'appel). Si un slot ne résout pas (pas de `_project=` sur l'appel, ou nom non bindé), l'appel est REFUSÉ avec la marche à suivre : **matérialise le contexte d'abord** — demande quel projet (ou crées-en un), et pour chaque slot binde une ressource existante ou crée-la ; ne choisis JAMAIS une table « probable » à la place d'un binding manquant.
