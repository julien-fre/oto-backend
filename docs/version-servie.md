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
  **gelé à 1.100.0** depuis que les tags ont cessé de le bumper. Mesuré le 01/09/2026
  dans le venv du backend : `1.100.0` annoncé pour un **`v1.101.0` installé** — il
  aurait dit la même chose pour un `v1.200.0`.

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

## ⚠️ Le script de déploiement vit à deux endroits

`/opt/deploy/oto-mcp-bluegreen.sh` sur la box a **deux copies de référence** :
`deploy/oto-mcp-bluegreen.sh` ici, et `scripts/oto-backend-bluegreen/` dans
`otomata-tech/infra`. Elles ont **divergé le 01/09/2026** (correction infra sur la
lecture de la dépendance oto-core dans le manifeste), et la divergence tombe dans
`bg_install` — la fonction même qui écrit la coordonnée.

**Conséquence pratique** : ne jamais recopier une de ces deux copies sur la box sans
avoir regardé l'autre. Tant que la copie vivante ne pose pas le `.oto-deploy.json`,
la plateforme répond honnêtement `unknown` — elle ne ment pas, elle ne sait pas.

## Ce que ce lot ne fait pas

- **Aucune surface existante touchée** : une route neuve, un en-tête neuf, un champ
  `info.version` qui cesse d'être constant. Confronté au contrat épinglé du front
  consommateur (`scripts/contrat-front.py`), le document servi ne présente **aucun
  écart** sur les 245 opérations préexistantes.
- **Pas de version côté MCP `serverInfo`** : le handshake continue d'annoncer la
  version de FastMCP. L'en-tête HTTP couvre déjà `/mcp`, et `serverInfo` est figé par
  un test qui garde autre chose.
