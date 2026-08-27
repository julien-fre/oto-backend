---
title: Modèle de connecteur — les 3 couches
type: explanation
description: >-
  Carte conceptuelle canonique des trois couches orthogonales qui gouvernent tout
  connecteur oto (unipile, google, pennylane, sirene…) : disponibilité (connector_activation
  master ± override org + availability self_serve/platform_granted), authentification
  (cascade resolve_api_key BYO-user > groupe > org > clé plateforme), et option de
  connecteur (has_option = comp admin via option_comps OU abonnement d'org, ADR 0043 ;
  option_open = has_option ∪ BYO). Explique aussi le RBAC interne org-connector-access
  (ADR 0025). À lire AVANT de toucher activation, clés ou options ; les autres docs
  (connector-vault, roles-and-resolution) sont le détail de chaque couche.
adr:
  - "0025"
  - "0043"
---

# Modèle de connecteur — les 3 couches

> **Pourquoi ce doc.** Un connecteur (unipile, google, pennylane, sirene…) a son
> comportement gouverné par **trois couches indépendantes** qui se confondent vite.
> Cette page est la carte canonique : avant de toucher activation / clés / options,
> lire ici. Sources de vérité code : `connector_activation.py`, `access.resolve_api_key`,
> `access.has_option`.

Pour qu'un connecteur **marche** pour un utilisateur, les **trois** doivent être OK :

| # | Couche | Question | Substrat |
|---|--------|----------|----------|
| 1 | **Disponibilité** | le connecteur est-il exposé ? | `connector_activation` (master ± override org) + `availability` |
| 2 | **Authentification** | avec quelle clé appelle-t-il l'API ? | cascade `resolve_api_key` (user→groupe→org→clé plateforme) |
| 3 | **Option** *(options gatées only)* | l'option est-elle débloquée ? | `option_open(sub, connector)` = **BYO** ∪ `has_option` (comp admin user\|org **OU abonnement d'org**) |

La plupart des connecteurs n'ont que **1 + 2**. Seuls les **connecteurs à option gatée**
(unipile, linkedin hébergé) ont la couche **3**.

---

## Couche 1 — Disponibilité (le connecteur est-il exposé ?)

- **Master switch** : `connector_activation` ligne `org_id IS NULL` → activé/désactivé pour
  toute la plateforme. Deny-by-default.
- **Override par org** : `connector_activation` ligne `org_id=<X>` → force on/off pour cette
  org (sinon hérite du master).
- **`availability`** (registre `providers/`, déclaré dans `providers/<nom>.py`) : `self_serve` (l'user l'installe lui-même, BYO
  possible) | `platform_granted` (deny-by-default, débloqué par un **grant de namespace** admin).
- Appliqué à la **visibilité par session** (middleware) + au catalogue `/api/connectors`.
- Surfaces : `/platform/connectors` (master + clé plateforme, super_admin) ; `/org/connectors`
  (override org).
- **RBAC interne à l'org (ADR 0025)** — grain plus fin que l'org entière : un org_admin réserve
  un connecteur à des **départements (groupes)** et/ou **membres** via `org_connector_access`
  (présence de ≥1 ligne ⟹ RESTREINT/deny-by-default ; absence ⟹ ouvert). **DUR** (réemploi du
  patron grant-only). **3 surfaces d'enforcement cohérentes** : (a) visibilité MCP (`session_visibility`
  masque les tools), (b) **marketplace dashboard** (`/api/me/connectors` via `connectors_selection._visible_catalog`
  → la page `/console/connectors` du membre, donc « voir en tant que » reflète l'effet réel), (c) **backstop
  call-time** `access.require_connector_access` dans `resolve_credential` → bloque **même avec une clé BYO**.
  super_admin bypasse ; fail-open sur erreur infra.
  Surface : `oto_{list,set,clear}_connector_access` / `/api/orgs/{id}/connectors/{acl,…/access}`
  (`ORG_ADMIN_OF`) + levier « accès » sur la carte `/org/connectors`.

## Couche 2 — Authentification (quelle clé ?)

`access.resolve_api_key(provider)` — cascade, **la plus spécifique gagne** :

```
clé MEMBRE (BYO, scopée (sub, org))  >  secret groupe  >  secret org  >  clé PLATEFORME (partagée)
```

> **Scope membre (ADR 0033).** Il n'y a **plus de clé « perso » org-agnostique** : la clé
> BYO d'un membre est keyée **(sub, org de contexte)** — coffre `entity_type='member'`,
> `entity_id="{org}:{sub}"`. Posée dans l'org A, elle ne résout PAS depuis l'org B (avant,
> elle suivait l'user partout et écrasait même la clé d'org). Vaut pour les clés API
> keyed/fields, les sessions browser (Contexts Browserbase), les comptes **Google**
> (l'org du start OAuth voyage dans le state) et les bindings **unipile**
> (`unipile_accounts` PK `(sub, org_id, provider)`). Seuls les **mounts oauth fédérés**
> (atlassian/folkmcp) restent `entity_type='user'`.

Deux notions à ne **pas** confondre :
- **BYO** (*bring your own*) : l'entité **pose SA propre clé**, stockée chiffrée dans
  `connector_credentials` (`entity_type` member|group|org). Possible aux **3 niveaux**.
- **Partage de la clé PLATEFORME** : Otomata détient **une** clé (`platform_keys`), et on
  **prête son usage** (métré, **jamais révélée/copiée**) via un **grant**. ⚠️ Aujourd'hui le
  grant de clé plateforme est **per-USER uniquement** (`user_grants`, `access.get_active_grant`).
  **Pas** de partage de clé plateforme au niveau org (trou connu — cf. §Trous).
- `auth_modes` du registre déclare ce qui est permis : `byo_user`, `byo_org`, `platform`.
- Gate de défense : le chemin clé-plateforme n'est valide que si `platform ∈ auth_modes`.

Surfaces : fiche user `/platform/users/<sub>` carte « connector access » → **« grant key »**
(prête la clé plateforme à CET user, métré) ; `/account` (l'user pose sa BYO).

**Attribution côté système tiers (BYO partagé groupe/org).** Un secret **partagé** (byo_org)
= **une seule identité** côté tiers : c'est **oto** qui agit sous le **propriétaire du credential**
(compte de service), pas le membre qui déclenche l'action. Sans effet en **lecture** ; en
**écriture**, l'audit « créé par » est le compte de service, pas l'utilisateur. Mitigation quand
le tiers sépare audit et assignation : poser explicitement le champ **owner** par enregistrement
(map *user oto → user tiers*) — ex. **Zoho CRM** `Owner` (le lead **appartient** au bon
commercial, seul « Created By » reste le compte de service). Attribution **native par personne**
⇒ il faut du **per-user** (BYO user, ou OAuth/mount per-user — cf. fédération MCP), pas un secret
partagé. (Noté 2026-06-24 — pertinent pour l'automatisation d'écriture Zoho (CRM client).)

## Couche 3 — Option de connecteur (unipile, linkedin hébergé)

Certains connecteurs (messagerie hébergée) sont **gatés par une option** : ils consomment des
sièges sur la clé plateforme Otomata — l'accès s'ouvre donc par l'**offre** ou par un **geste
d'admin**.

**Deux seams, deux questions distinctes — ne pas les confondre :**

**`access.has_option(sub, option)`** — « l'option est-elle ACCORDÉE ? ». Vraie si **l'une**
des trois :

1. **Comp admin sur l'user** — `option_comps (entity_type='user', entity_id=sub)`.
2. **Comp admin sur l'org active** — `option_comps (entity_type='org', entity_id=org)`.
3. **Abonnement actif de l'org** dont le plan inclut l'option (**ADR 0043**) — mapping
   `billing.plan_options`, miroir `org_subscriptions`. `past_due` reste **ouvert** tant que
   la grâce court ; la fermeture est un acte du `billing_runner`, jamais un effet de bord de
   lecture.

**`access.option_open(sub, connector)`** — « l'option est-elle LEVÉE pour cet appel ? », donc
`has_option` **∪ BYO** (clé propre user/groupe/org). C'est le seam que lisent le statut de la
carte connecteur (`connectors_selection.option_ok`) ET le gate « connecter » (`status_for.
subscribed`) : les faire diverger a déjà produit une carte « clé d'org » + « Bloqué »
incohérente (corrigé 2026-07-07). **Un nouveau chemin appelle `option_open`**, pas les sources.

> ⚠️ **Le BYO lève la couche 3 par CONSTRUCTION, pas par faveur** : l'entité gère sa propre
> instance chez le fournisseur, il n'y a donc aucun siège plateforme à protéger. C'est la
> raison, et elle explique pourquoi le gate ne se contourne pas autrement.

> ⚠️ **Ce doc a affirmé le contraire jusqu'au 13/08/2026** (« plus de paiement — le modèle
> billing/Stripe a été retiré, la gouvernance de l'option est purement admin »). C'était vrai
> à l'écriture, faux depuis l'**ADR 0043** (abonnement par org, PSP Mollie, LIVE prod le
> 03/08/2026) : l'abonnement est redevenu une source de `has_option`, et la carte canonique
> disait encore qu'il n'en existait qu'une. Un lecteur qui s'y fiait concluait qu'un client
> abonné devait quand même recevoir un comp.

Surfaces : bouton **« accorder l'option »** (super_admin) sur la fiche **user** (`option_comps`
user) ET la fiche **org** (`option_comps` org) ; l'abonnement, lui, pose les options à
l'activation du plan (`billing.apply_plan_entitlements`).

---

## Récap — « activer unipile pour quelqu'un »

1. **Disponible** ? unipile master ON (✓ par défaut).
2. **Clé** ? il pose sa clé Unipile (BYO) **ou** un admin lui **grant la clé plateforme** (fiche user → « grant key »).
3. **Option débloquée** ? l'org est **abonnée** à un plan qui l'inclut, **ou** un admin
   **accorde l'option** (comp, fiche user ou org), **ou** l'entité est en BYO.
4. Puis **lui** connecte son LinkedIn/WhatsApp (hosted-auth, `/console/connectors`).

## Trous connus (à combler)

- **Partage de clé plateforme org-level** : aujourd'hui le grant de clé plateforme est per-user
  seulement ; pas de « partager la clé plateforme à toute une org ». (couche 2)
