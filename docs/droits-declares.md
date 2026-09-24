---
title: Droits déclarés — ce qu'une personne dans une org a le droit de faire, et à quelle valeur
type: reference
description: >-
  Le modèle des droits déclarés (ADR 0070 §7, oto-backend#1066) : le cœur applique des
  limites DÉCLARÉES sans savoir qui paie. Une ligne = une portée (org ou personne dans
  l'org), une clé du catalogue, une valeur entière jamais vide, une fenêtre de dates, une
  source opaque. Un seul point de lecture (`access.entitlements.value_for`), le plus
  généreux gagne, et sans ligne c'est le défaut DÉCLARÉ par l'instance
  (`OTO_ENTITLEMENT_DEFAULTS`) — jamais un défaut du code. Ce qui est branché, ce qui ne
  l'est pas encore, et la migration en deux révisions.
---

# Droits déclarés

**Le cœur ne sait pas qui paie.** Il applique des droits qu'un producteur — aujourd'hui
la réconciliation du commerce (`billing_droits.py`), demain un service de commerce à part
par l'API d'administration — a DÉCLARÉS dans la table `org_entitlements`. Le cœur les
relit à chaque usage, jamais mis en cache comme acquis.

## Un droit

| champ | ce qu'il dit |
|---|---|
| `org_id` | l'org où le droit s'applique |
| `sub` | **la portée** : NULL = la ligne vaut pour l'org (tous ses membres) ; posé = pour cette personne dans cette org |
| `right_key` | une clé du **catalogue** (ci-dessous) — toute autre est refusée à la pose |
| `value` | un **entier, jamais vide** : oui/non = `1`/`0` ; sinon le nombre (plafond, quota par jour). « Sans plafond » = `SANS_PLAFOND` (2 147 483 647), une valeur explicite |
| `starts_at`, `expires_at` | la fenêtre : **début inclus, fin exclue**, fin nulle = sans échéance |
| `source` | qui l'a posé, au sens du producteur. **Informative** (affichage, reprise) : la règle d'application ne dépend jamais d'elle |

**Une ligne par (org, personne, droit, source)** : la contrainte
`org_entitlements_une_ligne` (`UNIQUE NULLS NOT DISTINCT`) le tient, `sub` nul compris.
Reposer la même quadruple remplace la ligne, bornes et valeur comprises : le producteur
dit l'état entier de son droit à chaque pose.

**Pourquoi une valeur jamais vide.** Une valeur nulle voulait dire « pas d'avis », et un
plan « sans avis » sur les sièges n'écrivait rien (#805) — l'absence se lisait comme un
défaut que personne n'avait décidé. Oui, non, un nombre, sans plafond : chacun s'écrit.

## Le catalogue — `oto_mcp/entitlements_catalogue.py`

Le catalogue vit **dans le cœur, en code**. Un producteur ne pose jamais une clé que le
cœur ne sait pas appliquer.

| clé | genre | aujourd'hui |
|---|---|---|
| `unipile` | oui/non | la messagerie hébergée — lue par l'option payante (`access.quotas`) et la fin de droit de la messagerie |
| `platform_unmetered` | oui/non | quotas levés sur les clés de plateforme — lue par `access.resolve` |
| `unipile_seats` | nombre | nombre de comptes de messagerie — **pas encore lue** : le plafond vient toujours de `orgs.unipile_account_limit` et `OTO_MCP_UNIPILE_DEFAULT_LIMIT` |
| `platform_key:<connecteur>` | nombre (quota par jour, `0` = pas d'accès) | l'accès à notre clé de plateforme d'un connecteur du registre `providers` (suffixe vérifié) — **pas encore lue** : le registre (`platform_key_open`, `default_quota`) reste la règle appliquée |
| `members_max` | nombre | ⚠️ **hérité, sans lecteur** : le nombre de licences d'un abonnement réglé hors plateforme. Le cœur ne connaît aucun plafond de membres ; la clé sort du catalogue quand le commerce posera les droits payants par personne |

Hors catalogue, délibérément : `beta` (un drapeau de population du cœur, lu dans
`option_comps`, pas un droit vendu). La réconciliation du commerce ne pose plus un don
d'option hors catalogue en droit.

## Le point de lecture unique — `access.entitlements`

```python
value_for(sub, org_id, key, now=None) -> int
```

1. les lignes **valides** à `now` (défaut : l'horloge de la BASE, la même pour tous les
   processus) ;
2. de l'**org** (`sub` NULL) **et** de la **personne** `sub` dans l'org, toutes sources ;
3. **le plus généreux gagne** : le maximum. Un don ne retire jamais ce qu'un abonnement
   donne, ni l'inverse ; une ligne à `0` dit « non » mais ne retire pas un « oui » posé
   ailleurs ;
4. **aucune ligne valide → le défaut déclaré par l'instance**.

⚠️ Une ligne vaut même si elle est MOINS généreuse que le défaut : le défaut ne répond
qu'en l'absence de toute ligne valide.

`org_has(org_id, key)` en dérive (`value_for(None, org_id, key) >= 1`). **Personne d'autre
ne lit la table pour appliquer un droit.** Deux lectures directes subsistent, qui
n'appliquent rien : le témoin « la table est-elle remplie ? » de la fin de droit de la
messagerie (`db/unipile_fin_de_droit.py`) et la liste des orgs à réconcilier du commerce
(`db/billing.orgs_with_commercial_rights`).

## L'écriture — `db/entitlements.py`

`grant(org_id, key, source, *, value, sub=None, starts_at=None, expires_at=None,
granted_by=None)` : pose idempotente ; clé hors catalogue, valeur vide ou hors genre →
`ValueError` nommée (`entitlement_unknown_key`, `entitlement_value_required`,
`entitlement_value_invalid`), rien n'est écrit. `revoke(org_id, key, source, *, sub=None)`
retire une ligne. Listes : `list_for_org` (org et personnes, échues comprises — une
console doit voir un droit échu), `list_for_person`, `list_for_right` (« quelles orgs ou
personnes ont X », vivantes par défaut).

## Les défauts de l'instance — `OTO_ENTITLEMENT_DEFAULTS`

Le **gratuit**, c'est ce qu'a une personne sans aucun droit posé : les défauts déclarés
par l'instance, clé par clé. Une variable d'environnement **requise**, en JSON :

```json
{"unipile": 0, "platform_unmetered": 0, "unipile_seats": 5,
 "members_max": "unlimited", "platform_key:*": 0, "platform_key:<connecteur>": 100}
```

- `platform_key:*` est un **joker** : il couvre tout connecteur sans surcharge ;
- `unlimited` s'écrit en toutes lettres ;
- **une clé du catalogue ni déclarée ni couverte refuse le démarrage** (`server.main` →
  `verifier_defauts`, avant de préparer la base), de même qu'une clé inconnue ou une
  valeur hors genre. Jamais un `0` silencieux au premier usage.

`python -m scripts.defauts_des_droits` imprime les défauts que le code applique
aujourd'hui, dérivés du registre (`platform_key_open`, `default_quota` et sa surcharge
`OTO_MCP_QUOTA_<P>_DAILY`) et du plafond de messagerie : de quoi amorcer la variable
d'une instance qui veut notre comportement. Une instance tierce naît avec les siens —
typiquement sans aucune de nos clés de plateforme.

## Le producteur d'aujourd'hui, et son vocabulaire

La réconciliation du commerce (`billing_droits.py`) dérive les lignes de l'état des
abonnements et des dons, et les aligne ; elle pose `1` pour un droit oui/non. Ses
étiquettes de source sont `subscription`, `offered`, `partner`, `contract` (et un essai
s'étiquettera `trial`).

⚠️ **Écart de vocabulaire avec la conception**, qui nomme les sources
`abonnement | essai | don | instance` : les étiquettes en place sont conservées, pas
renommées — la source étant opaque pour le cœur, le renommage est l'affaire du
producteur, et il réécrirait des lignes servies.

## La migration de la base servie — deux révisions

La base neuve reçoit la forme cible du fragment `db/schema/entitlements.py`. La base
PARTAGÉE (préproduction et production) la reçoit en deux temps, parce que le code d'avant
#1066 pose `value` NULL et cible l'ancienne clé primaire `(org_id, right_key, source)`
dans son `ON CONFLICT` :

| révision | contenu | quand |
|---|---|---|
| `0014_droits_portee_personne` | `sub`, `value` NULL → 1, contrainte `org_entitlements_une_ligne` — additif pour l'ancien code | **avant la fusion** : le code du lot lit `sub` et cible la contrainte |
| `0015_droits_valeur_obligatoire` | `value` NULL → 1, `SET NOT NULL`, retrait de la PK | **après le tag de production** : plus aucun processus ne sert l'ancien code |

Entre les deux, une ligne de personne du même (org, droit, source) qu'une ligne d'org
serait refusée par la PK encore en place — aucun producteur n'en pose encore. Détail et
ordre : `docs/migrations-versionnees.md` §5.1, et l'en-tête de chaque révision.

## Ce qui n'est pas encore fait

- faire lire `unipile_seats` et `platform_key:<connecteur>` par `value_for` (messagerie,
  quotas et cascade des clés de plateforme) ;
- l'API d'administration qui laisse un service de commerce poser des droits, et son
  identité de service ;
- l'essai (source `trial`), les droits payants par personne, le retrait de `members_max`.

## Ce qu'on ne fait pas

- **Pas de clé libre** : un producteur ne pose que ce que le catalogue connaît.
- **Pas de défaut dans le code** : l'instance déclare, sinon elle ne démarre pas.
- **Pas de droit mis en cache** : relu à chaque usage.
- **Pas de portée tenant** : le budget d'un tenant reste un mécanisme du cœur, hors droits
  déclarés.
