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
| Chemin REST | `GET /api/doctrines/library` | `GET /api/guide-library` | 308 | B2 |
| Chemin REST | `GET /api/doctrines/library/{slug}` | `GET /api/guide-library/{slug}` | 308 | B2 |
| Chemin REST | `GET /api/me/doctrines/library` | `GET /api/me/guide-library` | 308 | B2 |
| Chemin REST | `GET /api/me/doctrines/library/{slug}` | `GET /api/me/guide-library/{slug}` | 308 | B2 |
| Chemin REST | `DELETE /api/me/doctrines/library/{id}` | `DELETE /api/me/guide-library/{id}` | 308 | B2 |
| Chemin REST | `POST /api/me/doctrines/publish` | `POST /api/me/guide-library/publish` | 308 | B2 |
| Chemin REST | `POST /api/me/doctrines/fork` | `POST /api/me/guide-library/fork` | 308 | B2 |
| Chemin REST | `GET /api/me/doctrines/{doctrine_id}` | `GET /api/me/guides/{guide_id}` | 308 | B2 |
| Clé de capacité | `org.doctrine.get` | `org.guide.get` | **renommée, sans alias** (voir plus bas) | B2 |
| Clé de capacité | `org.doctrine.admin_get` | `org.guide.admin_get` | idem | B2 |
| Clé de capacité | `org.doctrine.admin_list` | `org.guide.admin_list` | idem | B2 |
| Clé de capacité | `admin.doctrine` | `admin.guide` | idem | B2 |
| Clé de réponse | `doctrine_id` | `guide_id` | les deux servies, même valeur | B3 |
| Clé de réponse | `doctrine_version` | `guide_version` | idem | B3 |
| Clé de réponse | `doctrine_ref_count` | `guide_ref_count` | idem | B3 |
| Clé de réponse | `doctrines` | `guides` | idem | B3 |
| Clé de réponse | `group_doctrine` | `group_guide` | idem | B3 |
| Clé de réponse | `doctrine` | `guide` | idem | B3 |
| Paramètre d'entrée | `doctrine_id` | `guide_id` | les deux acceptés | B3 |
| Paramètre d'entrée | `run_start(doctrine=)` | `run_start(guide=)` | les deux acceptés | B3 |
| Code d'erreur | `unknown_doctrine` | `unknown_guide` | l'ancien dans `details.legacy_code` | B3 |
| Schéma OpenAPI | `DoctrineMeta` | `GuideMeta` | `$ref` déprécié vers le neuf | B3 |
| Relation en base | table `doctrine_library` | vue `guide_library` | la vue sert, la table reste ; le renommage physique est au lot D | B4 |

Ce qui reste EN BASE et **n'a pas de doublure** — colonne `runs.doctrine`, valeur
d'énumération `missing_doctrine` (contrainte `CHECK`), kind d'ownership `doctrine`
(`resource_grants.resource_type`, une VALEUR de ligne), clé `doctrine_version` écrite
dans les `props` d'un nœud : ce sont des **données déjà écrites**, pas des noms.
Aucune vue ne les renomme ; il faut les migrer, et une migration de données est un
acte nommé et daté (ADR 0065 étage 2), pas une ligne de boot. **Lot D, #526.**

⚠️ **`DoctrineView` n'est pas dans cette table**, et ce n'est pas un oubli : un
modèle `Output` de premier niveau n'est pas un composant OpenAPI. Son schéma est
INLINE dans la réponse 200, et son nom n'y apparaît que comme `title` — ce
qu'aucun `$ref` ne peut viser. Le renommer n'engage personne ; publier un alias
pour lui inventerait un contrat qui n'a jamais existé.

⚠️ **`/api/guide-library` n'est PAS `/api/guides/library`.** Le premier est le
**marché** des guides publiés par les orgs (forkables, table `doctrine_library`) ; le
second, les guides **plateforme**. Les deux existaient déjà côte à côte sous des noms
qui se ressemblent ; c'est ce qui interdisait de renommer `/api/doctrines/library` en
`/api/guides/library` — le nom était pris par un autre objet.

### Pourquoi les clés de capacité n'ont pas d'alias

Une clé de capacité ne sort du serveur qu'à deux endroits : `/api/admin/capabilities`
— le navigateur d'objets de la plateforme, réservé à l'admin plateforme, sans
intégrateur tiers — et l'`operationId` de `/openapi.json`. Seul le second engage
quelqu'un dehors, et il est **préservé sur l'entrée dépréciée du chemin d'avant**. Il
n'y a donc rien à aliaser.

⚠️ L'`operationId` suit la **capacité**, pas le chemin. Quand la clé ne change pas
(`library.list`), c'est le NOUVEAU chemin qui en hérite : regénérer un client ne
renomme aucune méthode, seule l'URL bouge. L'entrée dépréciée reçoit alors un id
dérivé de son chemin — deux entrées ne peuvent pas partager un `operationId` sans
faire disparaître une méthode d'un client généré, en silence.

### Pourquoi une clé de réponse se double, et ne se renomme jamais

C'est la panne la plus chère de tout ce chantier, et la plus **silencieuse** : un
client qui lit `doctrine` sur une réponse qui ne sert plus que `guide` reçoit `null`.
Pas d'erreur, pas de log, rien qui s'allume. Il affiche un readme vide, ou range une
liste vide dans son cache. On l'apprend par un utilisateur, des jours plus tard.

Le doublage est fait par `deprecations.avec_les_deux_noms`, appelé **site par site**,
jamais posé globalement : un passage automatique sur toute réponse traverserait aussi
les données de l'utilisateur — la ligne d'un tableau dont il a nommé une colonne
« doctrine » gagnerait une colonne fantôme. Une compatibilité ne doit jamais inventer
un champ dans la donnée de quelqu'un.

⚠️ **Un nom doublé en SORTIE doit rester accepté en ENTRÉE**, et réciproquement.
Servir `guide_id` sans l'accepter en paramètre serait un piège : l'agent recopie ce
qu'il lit, et échoue.

⚠️ **Un code d'erreur, lui, ne se double pas** — il n'y a qu'un champ `error`. Le
nouveau prend la place, l'ancien est conservé dans `details.legacy_code`.

## Ce qu'un consommateur doit faire

1. **Lire la liste ci-dessus** et chercher les anciens noms dans son code.
2. **Basculer sur le nouveau nom** — il répond déjà, aujourd'hui, à l'identique.
3. Ne pas attendre le retrait pour le découvrir : après le tag, l'ancien nom ne
   répond plus du tout.

## Comment c'est fait, côté serveur

**La relation en base** : la table garde son nom, une **vue** `guide_library` porte
celui d'aujourd'hui, et tout le code passe par elle — au lot D, il ne restera qu'à
droper la vue et renommer la table, sans toucher une ligne de Python. La vue est
recréée à CHAQUE boot, **après tous les `ALTER`** : une vue `SELECT *` fige ses
colonnes à sa création (vérifié sur PostgreSQL 16), donc posée avant un
`ADD COLUMN` elle masquerait la colonne neuve — sans erreur, sans log, avec un `None`
là où le code attend une valeur. Elle est auto-updatable : `INSERT` avec DEFAULT et
`RETURNING`, `ON CONFLICT DO UPDATE`, `UPDATE`, `DELETE` la traversent — mesuré sur
une vraie base, pas supposé (`tests/test_guide_library_view.py`, qui compare aussi
les colonnes des deux relations).

⚠️ Une exception assumée : `db/users.py` (l'inventaire des colonnes porteuses d'un
`sub`) nomme encore la TABLE. Cet inventaire est vérifié contre le DDL, où une vue
n'apparaît pas ; y mettre la vue rendrait ce garde-fou aveugle à une entrée
réellement morte.

**Les chemins REST** : l'ancien chemin reste monté (`oto_mcp/api/alias_routes.py`) et
répond **308** vers le nouveau. Quatre crans, chacun payé par un incident possible :

- **308, pas 301/302.** Les deux derniers autorisent le client à retomber en `GET` :
  un `POST …/publish` deviendrait un `GET` — 405, ou pire, un no-op silencieux.
- **La query string est reportée.** Le build de la vitrine appelle
  `…?limit=200` ; la perdre rendrait 100 entrées au lieu de 200, sans code d'erreur.
- **Les en-têtes CORS sont sur la redirection elle-même**, et le préflight `OPTIONS`
  n'est jamais redirigé. Un navigateur vérifie CORS sur chaque réponse d'une chaîne ;
  et un `OPTIONS` redirigé le ferait abandonner avant d'essayer la vraie requête.
- **Les alias sont montés EN DERNIER.** Un alias ne peut alors capturer que ce que
  rien d'autre ne sert : impossible qu'un de ses placeholders éclipse une vraie route.

L'ancien chemin porte aussi `Deprecation: true` et `Sunset: <date>` — un intégrateur
qui lit ses logs voit la date sans ouvrir cette page.

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
