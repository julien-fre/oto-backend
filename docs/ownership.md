# Propriété de ressource — primitive `ownership` (ADR 0030)

> Extrait du CLAUDE.md (refactor 2026-07-02) — domicile du détail ; le CLAUDE.md garde le résumé + pointeur.


Le datastore n'est **plus scopé par `sub`** : il est le **pilote** de la primitive
d'ownership générique. `ownership.py` est le **seam unique** : une ressource
`(resource_type, resource_id)` est possédée par `(owner_type∈{user,group,org,platform},
owner_id)` (colonnes sur la ressource — pour le datastore : `user_datastores.owner_*`,
`resource_id = id::text`, **stable au renommage**) ; le partage cross-type vit dans
**`resource_grants`** (deny-by-default, remplace `datastore_shares`). Deux plans, jamais
confondus : **`can_access`** (CONTENU = owner-match ∪ grant ; *privacy by default* — pas
d'escalade admin sur du perso) et **`can_govern`** (GOUVERNANCE : re-partager/lister/
révoquer/supprimer/publier **sans lire**). La lecture opérateur du contenu
perso reste le **view-as audité** (ADR 0023). `DatastorePg._resolve` passe par
`can_access` ; le share/transfert/delete par `can_govern` (un super_admin/org_admin
gouverne donc un datastore tiers). ⚠️ **Scoping des LISTES de contenu** : une liste de
ressources possédées (datastore `list_namespaces`, projets `op=list`) scope sur
**`ownership.active_owner(current_org)`** (= l'org active, le pendant `ownership` de
`current_org`/ADR 0023), **JAMAIS** sur `accessor_scope().owner_pairs()` (= union de
TOUTES les orgs de l'acteur, réservé au plan **gouvernance** `oto_resource list` +
découverte/modèles). Les confondre = fuite cross-org *fail-open* (le superset montre
plus que le contexte chargé) — vécu 2026-06-30 (projets/datastore d'une autre org
visibles dans le dashboard). Garde-fou : `tests/test_owner_scope_tripwire.py` fige les
call-sites `owner_pairs()`. **org-owned activé** : `data_create_namespace` /
`POST /api/datastore/namespaces` acceptent un `owner` (classeur d'équipe). Capacité
générique **`oto_resource`** (`capabilities/resources.py`, op `list/get/transfer/share/
unshare`, autz combinateur `RESOURCE_GOVERN`) = chemin de gouvernance MCP+REST + alimente
l'object-browser admin.

> ⚠️ **DEUX surfaces depuis le 2026-09-01, et un défaut connu conservé sur l'héritée.**
> `oto_resource` / `POST /api/resources` donne à `resource_type` le défaut
> `datastore_namespace` (relique du pilote ADR 0030). **Omettre le champ ne veut donc pas
> dire « n'importe quel type »** : l'appel vise silencieusement un tableau, et sur
> `op=transfer`/`op=share` il **agit sur une autre ressource que celle visée** (même id
> numérique, autre famille). Le corriger en place a cassé de vrais appelants (#756,
> reverté par #774) : le défaut est donc **conservé, et écrit dans la description
> servie** — c'est le seul texte qu'un appelant relit à chaque appel.
> La correction vit sur une surface **doublée** : `oto_resource_v2` / `POST
> /api/resources/v2` (`capabilities/resources_v2.py`), où `resource_type` est obligatoire
> et `resource_id` numérique. **Même handler, même autz, même forme de sortie** — seul le
> contrat d'entrée diffère. Elle est en **bêta** (option `beta`, cf.
> `docs/tool-visibility.md`) ; la migration se fait appelant par appelant, sans
> date-couperet. Cliquet : `tests/test_resources_deux_surfaces.py` +
> `tests/resources_input_legacy.json` figent le schéma d'entrée servi de l'héritée. Catalogue du registre : **`GET /api/admin/capabilities`**
(`capabilities_catalog.py`, `PLATFORM_ADMIN`, JSON Schema dérivé des Input pydantic) →
UI admin **dérivée**. ⚠️ **Migration en cours** : `user_datastores.sub` + colonnes Sheets
sont des reliques nullable, **DROP différé** (Phase H) après cutover prod vérifié.

> **Partage unifié « audience × rôle » (ADR 0048, amende 0030/0032).** Le grant porte
> désormais un **RÔLE** (`resource_grants.role ∈ {viewer, editor, manager}`) et non plus
> seulement `permission ∈ {read, write}`. `permission` reste **la projection CONTENU
> appariée** (viewer→read, editor/manager→write), dérivée du rôle à l'écriture
> (`db.grant_resource`) → **tout le SQL du plan contenu est inchangé** (`max(g.permission)`,
> `g.permission='write'`). Le rôle **`manager` (gérant)** rend la **gouvernance GRANTABLE** :
> `can_govern = owner ∪ grant gérant ∪ escalade roles.py` (`_has_manager_grant`). Le
> **transfert de propriété** reste plus strict — `can_transfer = owner ∪ escalade` (jamais
> un gérant, ADR 0048 §3) ; le handler `oto_resource op=transfer` le re-garde après le gate
> `RESOURCE_GOVERN`. Surface unifiée : **`oto_resource op=share`** accepte deux axes —
> **audience** (`person`/`team`/`org` → grant ; `public`/`secret` → publication projet ;
> `private` → dépublier) × **rôle** ; l'ancien `permission` read/write est accepté en entrée
> (mappé). ⚠️ **Son défaut diverge entre les deux surfaces, à dessein (ADR 0068)** :
> `oto_resource` garde `"write"` — son schéma servi est un **cliquet** (empreinte JSON
> figée sur des appelants mesurés au journal le 01/09), et un défaut qui change y casse
> en production, chez quelqu'un d'autre, sans trace ; `oto_resource_v2` prend `"read"`,
> comme le veut « privé par défaut ». C'est le sens même de la duplication (ADR
> 0019/0050 : un contrat servi ne se durcit pas en place, il se double). Le partage de
> tableau (`data_share`, route REST), lui, n'a pas de cliquet : son défaut passe bien à
> `read`. Rétro-compat : backfill `role` depuis `permission` au boot (jamais `manager`, qui
> est un acte explicite). Tests purs + **tripwire gouvernance** (`test_ownership.py` :
> un lecteur/éditeur/inconnu ne gouverne JAMAIS). Front : sélecteur de rôle
> lecteur/éditeur/gérant (`lib/resourceRole.ts`).

> **Échelle 4 crans (ADR 0049, 2026-07-10).** Le projet rejoint l'échelle
> platform/org/group/user — **la visibilité DÉCOULE de l'ownership**, aucun mécanisme
> de restriction (leçon 0044 §G : restreindre = poser la ressource au bon scope).
> **group-owned** : création `oto_project(op=create, owner_type='group')` (garde
> `can_read_group`), listé dans l'org PARENTE (membres du pôle ; org_admin = tous les
> pôles de son org), `visible_in_org` mappe le groupe sur son org ; transfert cible
> `new_owner_group` (`oto_resource op=transfer`). **platform-owned** (`owner_id=
> 'platform'`, sentinelle comme les guides) : le cran BIBLIOTHÈQUE — `can_access`
> read = tout utilisateur authentifié (un modèle est fait pour être copié), write/
> govern/transfer = admin plateforme ; `op=list_templates` inclut toujours l'owner
> platform. Non-fait : la publication MCP d'un projet group/platform-owned reste
> org-centrique (l'endpoint sert sous l'autorité d'une org).

> **Suppression du « perso » (2026-06-30, amende ADR 0015/0023/0030).** Plus d'état
> **org-less** (`org_id=0` / `current_org`=None) : **tout user est TOUJOURS dans une org**.
> Chaque user a une **org perso dédiée** (`orgs.personal_of=sub`, privée mono-membre) —
> `org_store.ensure_personal_org` (créée au 1er insert d'`upsert_user` + au boot par
> `backfill_personal_orgs`, **reclaim sûr** : ne marque une org existante comme perso que
> si c'est la SEULE org du user, créée par lui ; sinon org fraîche → multi-org intact, zéro
> fuite). Les ressources `owner_type='user'` ont **migré** vers l'org perso ; les **défauts
> de création** (datastore/projet) vont dans l'**org active** (`current_org`, toujours posé).
> Plus de retour-perso (`clear_active_org` retiré ; `oto_clear_org` REST → org perso, MCP →
> maison). Filets gardés : `ownership` accepte encore `owner_type='user'` **en lecture**
> (reliquat) ; `session_visibility` `prof_org = active_org or 0` (défensif). `org_id=0`
> purgé des profils de visibilité.

## Le seam `ownership` — ce que la carte en disait

`ownership.py` = seam unique : ressource possédée par `(owner_type∈{user,group,org},
owner_id)` + partages `resource_grants` (deny-by-default). **Deux plans jamais
confondus** : `can_access` (contenu, privacy by default) vs `can_govern` (gouvernance,
escalade roles.py). ⚠️ **Une LISTE de contenu scope sur `active_owner(current_org)`,
JAMAIS `owner_pairs()`** (union de toutes les orgs = fuite fail-open ; tripwire
`test_owner_scope_tripwire.py`). Plus de « perso » : tout user a une org perso dédiée
(`orgs.personal_of`), défauts de création = org active.
**Détail (datastore pilote, oto_resource, migration, abolition du perso) : `docs/ownership.md`**.

## Partage unifié audience × rôle (ADR 0048)

> **Partage unifié audience × rôle (ADR 0048).** Le grant porte un **rôle**
> `resource_grants.role ∈ {viewer, editor, manager}` (`permission` read/write reste la
> projection CONTENU dérivée → SQL du plan contenu inchangé). `manager` (gérant) rend la
> **gouvernance grantable** : `can_govern = owner ∪ grant gérant ∪ escalade roles.py` ; le
> **transfert** reste `can_transfer = owner ∪ escalade` (jamais un gérant). Surface unique
> `oto_resource op=share` : axe **audience** (person/team/org→grant ; public/secret→publication
> projet ; private→dépublier) × **rôle**. Rétro-compat `permission` en entrée.
