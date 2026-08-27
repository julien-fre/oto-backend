---
title: Groupes (départements) & hiérarchie de droits unifiée
type: explanation
description: >-
  Explique l'architecture des groupes (départements) dans oto-backend : hiérarchie
  de droits centralisée dans roles.py (platform_admin ⊇ org_admin ⊇ group_admin ⊇
  member, escalade descendante), les deux ressources gouvernées par délégation
  (secrets partagés dans connector_credentials entity_type='group', doctrine/skills
  org_group_instructions), et la cascade
  de résolution user_key > secret groupe actif > secret org > grant plateforme (ADR 0012).
  Détaille le schéma DB (org_groups, org_group_members avec index partiel one_active,
  org_group_instructions), l'invariant groupe⊂org actif, et les surfaces MCP/REST
  via capacités groups*.py. À lire pour comprendre la délégation d'accès par équipe.
adr:
  - "0012"
---

# Groupes (départements) & hiérarchie de droits unifiée

> Statut : implémenté sur la branche `claude/group-principles-departments-k3qa6u`.
> À relier à un ADR du méta-repo (`otomata/docs/adr/0012-*`) au moment du merge.
> Voir aussi `connector-vault.md` (coffre) et `CLAUDE.md` §Visibility / §Doctrines.

## Pourquoi

Une org oto était une **liste plate** de membres avec deux rôles
(`org_admin`/`org_member`) + le rôle plateforme (`users.role` = `member`/`admin`).
Pour un client qui veut **structurer son org en départements** avec un **chef
d'équipe** par département, il manquait un palier intermédiaire. On l'ajoute sans
refaire l'autz à la main partout : on **centralise la hiérarchie de droits**.

## La hiérarchie unifiée (source unique : `roles.py`)

```
platform_admin   (users.role = 'admin')
   ⊇ org_admin      (org_members.org_role = 'org_admin')
       ⊇ group_admin    (org_group_members.group_role = 'group_admin')  ← chef d'équipe
           ⊇ member         (org_member / group_member)
```

**Escalade descendante** : un rôle supérieur *subsume* les inférieurs.
- `platform_admin` agit comme org_admin de TOUTE org et group_admin de TOUT groupe.
- `org_admin` d'une org agit comme group_admin de TOUS ses groupes.

Avant, cette escalade était recopiée dans chaque combinateur d'autz
(`role == ADMIN or org_store.get_org_role(...) == 'org_admin'`). Désormais elle
vit **uniquement** dans `roles.py` :

- `roles.is_org_admin(sub, org_id)` / `is_org_member(sub, org_id)`
- `roles.can_admin_group(sub, group_id)` — chef d'équipe, ou org_admin parent, ou platform
- `roles.can_read_group(sub, group_id)` — membre du groupe, ou les ci-dessus
- `roles.effective_group_role(sub, group_id)` — pour `/api/me` + l'UI

Les combinateurs de la couche capacité (`capabilities/_authz.py`) délèguent à
`roles` : `ORG_ADMIN_OF`, `ORG_MEMBER_OF`, `GROUP_ADMIN_OF`, `GROUP_MEMBER_OF`.
Ajouter un palier plus tard = un seul endroit à toucher.

## Ce qu'un groupe gouverne

Un groupe ≠ juste un label : il **gouverne deux ressources** par **délégation de
l'org** (le reste — entitlements de namespace gouverné — reste au niveau org).

| Ressource | Stockage | Résolution |
|-----------|----------|------------|
| **Doctrine & skills** | `org_group_instructions` (+ revisions), en clair | `get_claude_md()` sert org **puis** groupe actif (complément) |
| **Secrets partagés** | coffre `connector_credentials` (entity_type='group') | cascade `resolve_api_key` |

### Cascade de résolution des secrets (ADR 0012)

```
user_key  >  secret du GROUPE actif  >  secret de l'ORG active  >  grant plateforme
```

Le secret de groupe est le plus spécifique. `is_platform=False` (coût fixe,
jamais métré). Un user sans groupe/org actif → comportement **identique à avant**.

### Visibilité des outils

Il n'y a plus de baseline de toolset de groupe/org (les presets de tools ont été
retirés). La visibilité effective (`tool_visibility.is_tool_visible`, ordre de
priorité) ne dépend que des défauts plateforme et des toggles perso :

1. **grant-only** : barrière d'entitlement inchangée.
2. **méta-tools protégés** (`PROTECTED_TOOLS`) → toujours visibles (anti-lockout).
3. override perso **positif** (`oto_enable_tool`) → visible.
4. perso **désactivé** (`oto_disable_tool`) → masqué.
5. masqué-par-défaut → masqué ; sinon visible.

## Groupe actif (mirroir de l'org active)

Un user a au plus **un groupe actif** (`org_group_members.is_active`, index
partiel unique par `sub`). **Invariant** : le groupe actif appartient à l'org
active.
- `set_active_group(sub, group_id)` pose AUSSI l'org active = org du groupe (atomique).
- `set_active_org(sub, …)` **efface** le groupe actif (il pointait l'ancienne org).
- Retirer un membre d'une org le retire de tous ses groupes.

`oto_use_group(group_id)` (MCP) / `PUT /api/me/active-group` (REST) basculent ;
`oto_clear_group` / `DELETE /api/me/active-group` reviennent au niveau org.

## Schéma (db.py `_SCHEMA`)

- `org_groups(id, org_id→orgs, name, description, created_by, created_at, UNIQUE(org_id,name))`
- `org_group_members(group_id→org_groups, sub, group_role, is_active, joined_at, PK(group_id,sub))`
  + index `idx_org_group_members_sub` + partiel unique `org_group_members_one_active`
- `org_group_instructions(group_id, slug, …, version, PK(group_id,slug))` + `…_revisions`
- secrets de groupe : `connector_credentials(entity_type='group', entity_id=group_id::text, …)`

Toutes les FK `ON DELETE CASCADE` vers `org_groups` / `orgs` ; les secrets de
groupe (hors FK) sont purgés explicitement par `delete_group`.

## Surfaces

### Capacités (ADR 0009 — REST + MCP co-déclarés)

`capabilities/groups*.py`, montées automatiquement (registre).

- **CRUD / actif** (`groups.py`) : `group.create` (org_admin), `group.list`
  (membre org, REST), `group.list_mine` (MCP `oto_list_groups`), `group.use`
  (`oto_use_group` + `PUT /api/me/active-group`), `group.clear`, `group.get`,
  `group.update`, `group.delete`.
- **membres** (`groups_members.py`) : `group.member.{add,set_role,remove}`
  (`GROUP_ADMIN_OF`, cible doit être membre de l'org). Les trois passent par la MÊME
  garde anti-lockout — **« une équipe a toujours quelqu'un qui peut l'administrer, le
  responsable d'organisation compris »** (#280) : retirer/rétrograder le dernier chef
  explicite est **autorisé** (l'org_admin administre toutes les équipes de son org) ;
  seul l'état sans personne — zéro chef ET zéro `org_admin` dans l'org — est refusé
  (409 `group_unadministrable`). `add` étant un upsert, il porte la garde comme
  `set_role` (avant #280 il rétrogradait ce que l'autre refusait).
- **secrets** (`groups_secrets.py`) : `group.secret.{set,delete}`.
- **doctrine** (`groups_doctrine.py`) : `group.instruction.{list,get,set,delete,
  versions,revert}` — lecture = membre, écriture = chef. Édité par le dashboard
  via `REST /api/groups/{id}/instructions*`.

### `/api/me`

Ajoute `active_group`, `active_group_name`, `group_role` (effectif) ;
`providers[].mode` peut valoir `group` ; `providers[].group_secret_configured`.

## Limites connues

- Sessions MCP déjà ouvertes au moment d'un changement de groupe via REST
  ne sont pas notifiées live (même limite que la visibilité per-user : le hook
  `on_initialize` ne tape qu'à la naissance d'une session).
- Pas de sous-groupes (groupes plats sous l'org) — décision produit v1.
- Les entitlements de namespace gouverné restent **org-level** (non délégués au
  groupe) — décision produit v1.

## Gouvernance de connecteur par l'équipe (ADR 0012 B1/B2, restrict-only)

Migré de la carte : le détail des paliers, du fail-open par palier et des
capacités n'a pas sa place dans un index.

**gouvernance de connecteur (ADR 0012 B1/B2, restrict-only — 08/07/2026)** — le chef
  d'équipe peut, pour SON équipe : **couper** un connecteur (lignes scope 'group' de
  `connector_availability`, coupures seules) et **réserver** un connecteur à des membres
  (lignes scope 'group' de `connector_acl`).
  **INVARIANT MONOTONE** : l'équipe ne peut que RÉTRÉCIR ce que l'org expose, jamais élargir
  (platform ⊇ org ⊇ group). Dispo = **visibilité** (`session_visibility`, fail-open,
  `connector_activation.effective_for_group`/`group_cut_connectors`). Accès = **gate DUR** :
  seam `access.group_rbac_denied_connectors` (mirror de `rbac_denied_connectors`, bypass
  super/org_admin/group_admin) ; `require_connector_access` = `org_block OR grp_block` à
  **fail-open INDÉPENDANT par palier** (un hoquet DB d'équipe ne désactive pas l'org).
  Capacités `connectors.activation.{group_list,set_group,clear_group}` +
  `connectors.acl.{group_list,group_grant,group_revoke}` (GROUP_*). REST
  `/api/groups/{id}/connectors[/{name}]/activation` + `.../access`.

## Ce que la carte en disait (migré le 2026-08-27)

Une org se subdivise en **groupes** (départements/équipes) avec un **chef
d'équipe** (`group_role='group_admin'`). La gestion des droits est **centralisée**
dans `roles.py` (escalade descendante, source unique) :

```
platform_admin ⊇ org_admin ⊇ group_admin (chef) ⊇ member
```

Les combinateurs d'autz (`capabilities/_authz.py`) délèguent à `roles`
(`is_org_admin`, `can_admin_group`, `can_read_group`, `effective_group_role`) —
plus d'escalade recopiée à la main. Combinateurs : `GROUP_ADMIN_OF`,
`GROUP_MEMBER_OF` (en plus de `ORG_*`).

Un groupe **gouverne 3 ressources** par délégation de l'org (⚠️ **substrat unifié le
10/07/2026** — chantiers du cadrage objets/visibilité : plus de tables jumelles par
grain, le scope est une COLONNE ; migrations vivantes sur la DB partagée = playbook
**`docs/live-migrations.md`**) :
- **secrets partagés** — coffre `connector_credentials` (entity_type='group') ;
  cascade `resolve_api_key` = **user_key > secret groupe actif > secret org active > grant plateforme**.
- **doctrine & skills** — table UNIFIÉE `org_instructions` (`owner_type='group'`,
  `owner_id=group_id`, `org_id`=org parente ; ex-jumelle `org_group_instructions`
  DROPpée) ; `oto_procedure(op='get')` sert org **puis** groupe actif (complément,
  chaque skill taggée `scope`). Les procédures d'équipe ont un `id` (ownership 0030).
- **gouvernance de connecteur** — le chef d'équipe peut COUPER un connecteur et le
  RÉSERVER à des membres, pour son équipe seulement. **Invariant monotone** :
  l'équipe RÉTRÉCIT ce que l'org expose, jamais l'inverse (platform ⊇ org ⊇ group).
  Détail (paliers, fail-open indépendant, capacités) : `docs/groups-and-roles.md`.

**Groupe actif** : ≤1 par sub (`org_group_members.is_active`, index partiel),
**invariant** = appartient à l'org active. `set_active_group` pose aussi l'org
active ; `set_active_org` efface le groupe actif. `oto_use_group` /
`PUT /api/me/active-group` (+ `oto_clear_group` / `DELETE`).

Stores : `group_store.py` (miroir d'`org_store` au grain groupe). **Aucun module
du package `org_store/` n'importe `group_store`** : l'invariant org↔groupe est
tenu en SQL direct dans `org_store/members.py` (`remove_org_member` sort le membre
de tous les groupes de l'org, `set_active_org` invalide le groupe actif) → pas de
cycle. Règle **vérifiée** depuis la découpe du 2026-08-27 par
`tests/test_org_store_surface_frozen.py`, plus seulement tenue à la main. Surfaces : capacités `capabilities/groups*.py` (REST `/api/orgs/{id}/groups`,
`/api/groups/{id}*`, `/api/me/active-group` + MCP `oto_*_group*`). `/api/me`
expose `active_group`/`active_group_name`/`group_role` ; `providers[].mode` peut
valoir `group`. **Détails : `docs/groups-and-roles.md`.**
