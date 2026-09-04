# La version servie — dater un changement de comportement

> **Le fait à retenir** : la version que la plateforme annonce désigne **ce que le
> processus exécute**, pas ce que le dernier workflow a déployé. Les deux diffèrent
> plus souvent qu'on ne le croit, et c'est précisément quand ils diffèrent qu'on a
> besoin de la version.

## Le problème (oto#33, constaté le 28/08/2026)

Une flotte d'agents mesure un comportement — taux d'appels malformés, forme des
sorties d'un outil — de part et d'autre d'une journée. Ce jour-là, **quatre mises en
production**, dont une **quinze minutes avant une mesure**. Aucune des deux faces
(`/mcp`, `/api/*`) ne disait sa version : `/api/health` et `/api/version`
n'existaient pas, et le document OpenAPI portait `version: "1"` en dur. L'attribution
— quel changement a produit quel effet — a dû se reconstituer après coup depuis les
tags GitHub. Le 01/09 encore, plusieurs sessions y ont laissé leur matinée à établir
« qu'est-ce qui est réellement servi ».

## Les trois surfaces, une seule étiquette

L'étiquette a une forme unique — `v1.2.3+6d5bf16b` (tag ou branche, `+`, commit
court) — pour qu'un descriptif d'API et un journal d'appels se recoupent **sans
traduction**. Trois sources qui divergeraient, c'est trois versions à réconcilier à
la main, soit le problème d'origine.

| Surface | Pour qui |
|---|---|
| `GET /api/version` — **sans auth** | Qui pense à demander : un contrôle externe (Uptime Kuma, un script de déploiement), un agent, un intégrateur. Sans auth parce que dater une dérive ne doit rien exiger d'autre. |
| `info.version` du document OpenAPI | L'intégrateur qui constate une dérive de forme et veut la situer. Le document est dérivé du serveur à chaque requête ; le figer à « 1 » revenait à publier une carte sans dire de quel jour elle date. |
| En-tête `X-Oto-Version` sur **chaque** réponse | **Le cas qui a mordu** : qui n'a rien demandé, et relit son journal après coup. C'est la seule forme qui date une mesure **rétrospectivement**, sans instrumentation préalable. |

⚠️ L'en-tête est exposé au navigateur (`Access-Control-Expose-Headers`) : sans cela
il part sur le fil mais reste illisible à `fetch`, donc au dashboard. Un en-tête
qu'aucun consommateur ne peut lire ne date rien.

## Trois coordonnées refusées, et pourquoi

Chacune est la réponse évidente, et chacune a déjà menti :

- **Le dernier run vert du workflow.** Il dit qu'un déploiement a été *lancé*, pas
  ce qui sert. Entre le déclenchement et l'exécution du script, `main` a pu avancer ;
  une bascule a pu échouer et laisser l'ancienne couleur en service ; un rollback a
  pu rebasculer sur la version d'avant. Un run vert désigne une intention.
- **`git rev-parse` sur la box.** Le déploiement est bleu/vert : l'arbre de la
  couleur **inactive** est réécrit pendant que l'autre sert. Lire git au moment de la
  requête, c'est lire l'état d'un arbre à un instant sans rapport avec le démarrage
  du processus — et une image on-premise n'a ni git ni dépôt.
- **`pip show oto-core`.** Il rend le champ `version` du `pyproject` d'oto-core,
  **gelé à 1.100.0** depuis que les tags ont cessé de le bumper. Le 01/09/2026, le
  venv portait le tag `v1.101.0` et le manifeste épinglait `v1.103.0` : **les deux
  déclarent `version = "1.100.0"`** (vérifié sur le dépôt distant, aux deux tags).
  `pip show` rend donc le **même numéro pour l'installé périmé et pour le bon** — il
  ne peut pas voir l'écart qu'on lui demande de mesurer, et sa réponse identique se
  lit comme une confirmation que tout va bien.

## D'où vient la coordonnée, alors

**Écrite par celui qui installe, dans l'arbre qu'il vient d'écrire, avant que le
processus ne démarre.** `deploy/oto-mcp-bluegreen.sh` pose un `.oto-deploy.json`
(non versionné) juste après le `git reset --hard` : le `ref` est ce qui a été
demandé, le `commit` ce qui a réellement été posé. Les deux ensemble ne mentent pas.

`oto_mcp/version.py` la lit **une seule fois**, au boot, et la mémoïse
**délibérément** : une couleur en cours de **vidange** finit ses requêtes pendant que
son successeur s'installe — elle exécute encore l'ancien code, elle doit donc
continuer à annoncer l'ancienne version. Sans la mémoïsation, la vidange fabriquerait
exactement l'erreur d'attribution que ce lot supprime.

⚠️ **Et côté LECTEUR, la vidange fabrique l'erreur que la mémoïsation évite côté
serveur (03/09/2026).** Chaque processus annonce correctement SA version — mais le
**journal des appels ne porte aucune colonne de version**. Une ligne de `tool_calls`
ne dit donc pas quel processus l'a servie, et pendant la vidange le journal MÉLANGE
les deux sans qu'aucun champ ne le signale. *Un horodatage postérieur au démarrage du
nouveau processus ne prouve pas que le nouveau code a servi l'appel.*

Mesuré à la mise en production de `v1.185.0` : le relevé montrait une extraction de
page à **46 s** après le démarrage, alors que le lot venait de ramener le plafond à
15 s — la signature exacte d'un contrat qui ment. Rejeu de la même adresse :
**16,5 s, et le refus nomme la borne**. L'appel de 46 s venait de l'ancien processus,
encore en vidange. La fausse alerte était à un cheveu d'être remontée comme un défaut.

**Le seul contrôle qui tranche est de REJOUER le cas soi-même** après la bascule ; la
relecture du journal ne le peut pas, et l'en-tête `X-Oto-Version` ne sert que sur une
réponse qu'on tient encore, jamais sur une ligne déjà journalisée. Vérifier un
correctif par le journal juste après un déploiement est donc une mesure dont
l'étiquette manque — au sens propre.

Ordre de résolution :

1. **L'environnement** — `OTO_DEPLOY_REF` / `OTO_DEPLOY_SHA` / `OTO_DEPLOY_AT`. Il
   **prime** : c'est la seule voie d'une image Docker ou d'un on-premise (pas d'arbre
   git), et la reprise si le fichier manque.
2. **`.oto-deploy.json`** à la racine de l'arbre — le cas nominal sur la box.
3. **Rien** → `"unknown"`, et `source: "unknown"`. Une version inventée daterait une
   mesure sur une coordonnée fausse : c'est pire que pas de version. Un checkout
   local répond donc `unknown`, ce qui est vrai.

Un fichier tronqué (déploiement interrompu en plein `printf`) vaut absence, pour la
même raison : mieux vaut `unknown` qu'une moitié de coordonnée.

## Le tag oto-core installé

`GET /api/version` porte aussi `oto_core`, parce que **le pin du manifeste et
l'installé divergent régulièrement** : `pip` ne réinstalle pas une dépendance VCS
déjà présente, donc un venv peut porter un tag antérieur au pin sans qu'aucune
commande ne s'en plaigne. C'est l'**installé** qui exécute les appels.

La coordonnée fiable est ce que **pip écrit à l'installation** — `direct_url.json`
(PEP 610) : `requested_revision` (le tag) et `commit_id` (le commit résolu). Le champ
`source` nomme la précision obtenue plutôt que de laisser un `null` se lire comme
« rien n'est installé » :

| `source` | Ce que ça veut dire |
|---|---|
| `direct_url` | Installation depuis git — la coordonnée exacte. |
| `metadata` | Installation depuis PyPI : plus de `direct_url.json`, il ne reste que le champ `Version`. ⚠️ C'est le numéro **gelé** — servi faute de mieux, nommé pour ce qu'il est. |
| `absent` | oto-core n'est pas installé (aucun connecteur ne peut tourner). |

## Le script de déploiement : ce dépôt en est la source unique

`deploy/oto-mcp-bluegreen.sh` **est** la source de ce que la box exécute en
`/opt/deploy/oto-mcp-bluegreen.sh`. Ce script déploie ce produit ; c'est l'équipe de
ce produit qui le modifie, et une PR ici est le geste naturel de qui y touche.

**Ça n'a pas toujours été vrai, et l'épisode mérite d'être daté.** Le 01/09/2026, le
script existait **en double** : une copie ici, une dans `otomata-tech/infra`
(`scripts/oto-backend-bluegreen/`), chacune se présentant en tête comme la source.
Elles ont divergé le jour même, dans `bg_install` — la fonction que ce lot modifie :
infra corrigeait la lecture de la dépendance oto-core (relire la **ligne entière** du
manifeste, extras compris, au lieu d'extraire le seul tag et de recoder
`oto-core[browser]` en dur, ce qui défaisait en silence le retrait délibéré de cet
extra à chaque déploiement) pendant que ce lot y ajoutait l'écriture de la
coordonnée. Les deux corrections ont été réunies ici, et infra ne garde qu'un
pointeur.

**La leçon, qui survit à l'épisode** : deux copies d'un même fichier qui se déclarent
chacune « la » source ne divergent pas *si* quelqu'un se trompe — elles divergent dès
que deux personnes ont raison en même temps, chacune dans sa copie. Le garde-fou n'est
pas la vigilance, c'est de n'en avoir qu'une.

⚠️ **La coordonnée n'est écrite que si l'installation a réussi.** Le refus du
manifeste illisible sort de `bg_install` **avant** le `printf` : une couleur dont
l'installation a échoué ne porte pas un `.oto-deploy.json` qui annoncerait une version
qu'elle n'a jamais installée. Et tant qu'une box n'a pas ce script, la plateforme
répond `unknown` — elle ne ment pas, elle ne sait pas.

## Ce que ce lot ne fait pas

- **Aucune surface existante touchée** : une route neuve, un en-tête neuf, un champ
  `info.version` qui cesse d'être constant. Confronté au contrat épinglé du front
  consommateur (`scripts/contrat-front.py`), le document servi ne présente **aucun
  écart** sur les 245 opérations préexistantes.
- **Pas de version côté MCP `serverInfo`** : le handshake continue d'annoncer la
  version de FastMCP. L'en-tête HTTP couvre déjà `/mcp`, et `serverInfo` est figé par
  un test qui garde autre chose.
