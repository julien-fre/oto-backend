---
title: Connector vault — registre + coffre chiffré + résolution
type: reference
description: >-
  Architecture centrale des credentials oto-backend : registre source unique connectors.py
  (dataclass Connector, 3 axes disponibilité/visibilité/credential, schéma multi-champs
  secret_fields dérivé), coffre chiffré unique connector_credentials (table 4-col PK
  entity_type/entity_id/connector/account, AES-256-GCM obligatoire via crypto.py,
  master key en Scaleway Secret Manager), et résolution access.py (resolve_api_key,
  resolve_credential_fields, resolve_mount_token, status_for,
  list_datastore_namespaces_granted_to).
  Inclut le packing multi-champs, Google multi-compte, LinkedIn/Crunchbase en coffre,
  et le modèle de connecteur remote (ADR 0003, pilote = un connecteur remote client). À consulter pour
  tout ajout de connecteur, débogage de credential, ou compréhension du chiffrement.
adr:
  - "0003"
  - "0011"
---

# Connector vault — registre + coffre chiffré + résolution

Substrat unique des connecteurs, credentials et accès d'oto-mcp. Déployé en prod (2026-06).
Chiffrement **obligatoire** : tous les secrets vivent chiffrés (`secret_enc`), plus aucune colonne plaintext ni dual-write (purge legacy 2026-06-11). Un serveur sans `OTO_MCP_MASTER_KEY` boote mais tout write de credential échoue fort.

## Registre — source unique (`connectors.py`)

Module pur (aucun import oto_mcp, comme `tool_visibility.py`). Une dataclass `Connector` par connecteur, 3 axes orthogonaux :
- **A. Disponibilité** : `availability` ∈ {`self_serve`, `platform_granted`}. platform_granted = grant-only (deny-by-default, ex. `mm`, `gocardless`).
- **B. Visibilité** : `default_active` (ADR 0050 — socle installé d'office au seed d'un nouveau (sub, org) ; **vide depuis le 16/07** : tout le catalogue est en library installable, l'agent guide).
- **C. Credential** : `auth_modes` ⊆ {`byo_user`, `byo_org`, `platform`} ; `keyed` (résolu via `resolve_api_key`) ; `secret_kind` (api_key/basic_auth/fields/refresh_token/oauth/cookie/none) ; `personal_session` ; `env_secret_name` ; `default_quota`.
- **Modèle de saisie multi-champs** (ADR 0011) : `secret_fields` (propriété) = schéma de saisie du credential — `credential_fields` explicites (`CredentialField` name/label/secret/reveal) ou dérivés du `secret_kind` (`api_key`=1 champ `key` ; `basic_auth`=`email`+`password`). Vide pour `cookie`/`oauth`/`none` (flux dédiés). SOURCE UNIQUE du formulaire dashboard, de l'endpoint `/api/settings/api-keys`, de `status_for` et du packing — zéro branche par connecteur (ex. Silae = 3 champs déclarés, aucun code spécifique).
- **Chargement** : `Connector.modules` = modules `tools/<m>.py` à importer (kind="tools" ; défaut = nom du provider). Voir §Chargement dérivé.

**Tout dérive du registre** (mêmes symboles, ré-export) : `KEY_PROVIDERS`, `ORG_SHAREABLE_PROVIDERS`, `ADMIN_GRANT_ONLY_NAMESPACES`, `QUOTA_DEFAULTS`, `ENV_SECRET_NAMES`, `DEFAULT_BUNDLE/PRESET`. Plus de listes en dur parallèles. `GET /api/connectors` = vue publique.
Helpers : `require_keyed`, `is_byo_user`, `is_org_shareable`, `require_credential(entity_type, name)` (user→byo_user, org→org-partageable).

## Coffre — `connector_credentials` (table unique)

A remplacé (et les a fait DROP, purge 2026-06-11) les 9 colonnes `users.<provider>_api_key`, `org_secrets`, les colonnes session (`users.linkedin_*`/`crunchbase_*`) et la table `user_google_oauth`. `init_db._drop_legacy_plaintext_stores` exécute les `DROP … IF EXISTS` (idempotent, no-op sur DB fraîche on-prem).

```
connector_credentials(entity_type, entity_id, connector, account, secret_enc,
                      secret_kind, meta JSONB, set_by, set_at,
                      PK(entity_type, entity_id, connector, account))
```
- `entity_type` ∈ {`user`,`org`} ; `entity_id` = `sub` | `org_id::text`. Toujours requêter `(entity_type, entity_id)` ENSEMBLE.
- `account` = discriminant **multi-compte** ('' = mono ; ex. email Google). 1 ligne par compte connecté.
- `secret_enc` = enveloppe chiffrée (pas de colonne plaintext). `meta` = satellites NON-secrets (user_agent linkedin/crunchbase, access_token/expires_at/scopes/is_default google).

Store = `credentials_store.py` (calqué `org_store.py`, réutilise `db._connect`, jamais d'import circulaire) :
`get_credential` / `get_credential_with_meta` (secret+meta+set_at, déchiffre) / `credential_status` (présence+meta SANS déchiffrer, pour /api/me) / `has_credential` / `set_credential` (chiffre) / `clear_credential` / `update_meta` (merge JSONB sans re-chiffrer) / `list_accounts`.
- **Packing multi-champs** : `pack_secret(connector, fields)` / `unpack_secret(connector, secret)` encodent les `secret_fields` dans l'unique `secret_enc` — 3 formats selon la forme : 1 champ (`api_key`) = valeur brute (back-compat) ; `basic_auth` = `base64("email:password")` (format de fil que le mount distant décode, ex. planity-mcp) ; ≥2 champs = `json`. L'endpoint de saisie et `resolve_credential_fields` passent par là.

## Chiffrement au repos — `crypto.py`

Enveloppe **AES-256-GCM**, **obligatoire** (`set_credential`/`_pk_encrypt` chiffrent toujours ; `crypto.encrypt`/`decrypt` lèvent si master key absente — pas de stockage ni lecture plaintext). Master key **hors-DB** (env `OTO_MCP_MASTER_KEY`, hex64 ou base64-32o ; en prod fetchée de Scaleway Secret Manager au boot, cible KMS unwrap, cf. `ADR 0002 (meta privé otomata-private/docs/adr)`). AAD = `connector_credentials:{entity_type}:{entity_id}:{connector}[:{account}]` (anti-transplant ; segment account omis si vide → compat ascendante mono-compte). Envelope = `key_ref(1o)‖nonce(12o)‖ct`.
- Déchiffrement **JIT** dans `resolve_api_key`/`get_credential` uniquement, jamais loggé ; `status_for` lit la présence (`has_credential`/`credential_status`), ne déchiffre pas. Échec de déchiffrement = LÈVE (pas de fallback silencieux).
- `platform_keys` : secret dans `api_key_enc` (même pattern, AAD `platform_keys:{provider}:{label}`).
- Dump Postgres = **ciphertext only**. Pas de rotation de clé (key_ref réservé). Perte de master key = perte totale → Secret Manager versionné + escrow.

## Résolution + accès (`access.py`)

`resolve_api_key(provider) -> (api_key, is_platform)` : (1) clé membre scopée (sub, org de contexte) (`get_member_api_key`→coffre, entity `member`/`{org}:{sub}`, ADR 0033) ; (2) org secret (si `byo_org` + org active) ; (3) platform grant + quota ; (4) McpError actionnable. Le connecteur **`bridge`** universel (ADR 0034) se résout par les **champs standard** (`resolve_credential_fields("bridge")` → `base_url`/`token`/`label`, cascade membre > groupe > org), raise actionnable si absent, **jamais de fallback SOPS serveur** — plus de `meta.base_url` (l'ex-`resolve_remote_credential` per-namespace retiré en B4).
`resolve_credential_fields(provider) -> dict` : credential **multi-champs byo_user** (ex. `silae` : client_id/client_secret/subscription_key) — lit le coffre + `unpack_secret`. **byo-only, pas de platform key ni quota** (le credential EST le grant). Pour les clients in-process s'instanciant avec plusieurs secrets.
`resolve_mount_token(provider)` : token per-user d'un MCP fédéré `kind="mount"` (OAuth atlassian, ou base64 basic_auth planity), injecté en bearer par le proxy.
`status_for` = miroir exact (modes user/org/platform/over_quota/forbidden) — boucle aussi sur les byo_user à `secret_fields` hors `KEY_PROVIDERS` (planity, silae : `user`/`forbidden`). `granted_namespaces_for`/`require_namespace` = gate des namespaces grant-only (deny-by-default), source unique consommée par middleware + meta-tools + REST.

## Palier org

Tables `orgs`/`org_members`(index partiel `org_members_one_active`)/`org_entitlements` ; `org_store.py` ; 12 meta-tools `oto_admin_*` (`tools/orgs.py`). Entité = **user ET org, 2 niveaux** (perso prime sur org).

## Folds des secrets de session (cible : coffre unique)

- **LinkedIn / Crunchbase** : cookie chiffré dans `secret_enc`, UA dans `meta` ; `db.set/get/clear_linkedin_cookie`/`crunchbase_session` sur le coffre ; statut /api/me via `credential_status` (sans déchiffrer).
- **Google OAuth multi-compte** : `connector='google'`, `account=email` ; refresh_token chiffré, access_token/expires_at/scopes/is_default/granted_at dans `meta`. Les 6 fns db (`set/get/list/set_default/delete_google_oauth`, `update_google_access_token`) sur le coffre ; `update_google_access_token` = `update_meta` (merge, sans re-chiffrer). Flow OAuth `google_oauth.py` inchangé (seule la couche stockage change). ⚠️ access_token reste en **clair dans `meta`** (bearer ~1h, dérivé) ; seul le refresh_token (`secret_enc`) est chiffré.

## Connecteurs remote — bridges (ADR 0003, pilote mm)

`kind="remote"` au registre = **aucun code ni credential client dans oto** : un bridge (service HTTP distant, ex. un bridge back-office client (repo privé)) détient le credential du système client ; oto-mcp = middleware générique `tools/remote.py` (tools `<ns>_describe` + `<ns>_call`, forward bearer M2M + `X-Oto-Sub` pour l'audit côté bridge). Le credential d'org = `secret` = token M2M + `meta.base_url` = endpoint (posé via `oto_admin_set_org_secret(..., base_url=…)`). Gating inchangé : grant-only + `require_namespace` au call-time. Contrat bridge (`/healthz`, `/describe`, `/call`) : ADR 0003 du meta-repo. Le mount MCP-to-MCP (`otomata#16`) = flavor complémentaire pour les remotes déjà-MCP.

## Projection instances (ADR 0038 B4)

Le coffre est relu comme un **listing d'instances possédées nommées** (une ligne
`(entity_type, entity_id, connector, account)` = une instance), en **lecture pure,
sans jamais déchiffrer** : capacité `connectors.instances.list` (MCP
`oto_instance(op="list")` (console ADR 0047), REST `GET /api/me/connector-instances`,
`capabilities/connectors_instances.py`). Agrège les 4 familles que la cascade
résout — membre `(org, sub)` > mes groupes de l'org > org > clés plateforme
(grants user/org + free-tier via `db.list_platform_keys_meta`, le pendant
non-déchiffrant de `list_platform_keys`). Chaque instance porte un **`ref` stable
opaque** (grammaire dans `instance_refs.py`, projection 1:1 de la PK ;
`platform:{id}` pour les clés plateforme) — future cible des bindings B5 et de
l'axe `instance=` B6. Métadonnées seulement (meta public, jamais un bearer) ;
limite : les `config_fields` packés dans `secret_enc` (ex. `data_center`) ne
sortent pas — seule la part `meta` est projetée en `config`. Le « gagnant » de la
cascade reste dit par `status_for` (une seule vérité) ; la projection ne porte
que l'ordre de proximité (tri membre < groupe < org < plateforme).

## Credentials qui se CONSOMMENT à l'usage (rotation)

Certains fournisseurs invalident le jeton à chaque utilisation et en renvoient un neuf
— **Salesforce l'impose** sur les External Client Apps (contrôle verrouillé, « paramètre
obligatoire », application 2026, donc chez *tous* les clients).

**La règle : sous rotation, toute LECTURE est une ÉCRITURE.** Tout chemin qui touche au
jeton en devient consommateur et doit persister le remplaçant — sinon il détruit le
credential en s'en servant.

Les consommateurs, à traiter **ensemble** (les traiter un par un ne marche pas, chacun
suffit à tuer la connexion) :

| Consommateur | Ce qu'il lui faut |
|---|---|
| appel d'outil | rappel `on_refresh` → réécriture à l'entité résolue |
| sonde de vérification | l'**entité sondée** (`connector_verify.run(instance=…)`) — la cascade désigne la clé la plus PROCHE, pas celle qu'on teste |
| sonde post-écriture d'un callback OAuth | *retirée* — une requête navigateur n'a pas de contexte authentifié, donc aucun moyen de savoir où réécrire |
| script de diagnostic | il consomme comme les autres : préférer la sonde du serveur |

**L'écriture est CONDITIONNELLE**, jamais un écrasement : on ne réécrit que si le jeton
stocké est encore celui qu'on a lu. Deux appels concurrents — ou preprod et prod, qui
partagent la base — peuvent avoir tourné entre-temps ; remettre en place un jeton déjà
consommé est précisément ce que le fournisseur traite comme une compromission (Salesforce
révoque alors le jeton courant *et* tous les access tokens associés).

Deux corollaires qui coûtent cher quand on les découvre en production :

- **un cache d'access token porté par l'instance de client ne sert à rien** côté serveur
  (une instance par appel MCP) : sans cache process-wide, on rafraîchit — donc on fait
  tourner le jeton — à chaque appel d'outil. C'est ce qui transforme la rotation en
  problème explosif plutôt que contraignant ;
- **une sonde n'est plus « sans effet de bord »**, et ne peut pas l'être.

### Application ≠ jeton

Corollaire de modèle, visible sur Salesforce (`salesforce_oauth.py`) : l'**application**
OAuth (`client_id`/`client_secret`/`login_url`) est une **infrastructure d'org** — un
admin la pose une fois ; le **refresh token** est une **identité** — il appartient à qui
consent.

D'où une asymétrie délibérée : l'application se **lit en cascade** du scope demandé vers
le haut (membre → équipe → org), le jeton s'**écrit au scope demandé** exactement. Un
membre consent donc avec l'application de son org sans jamais en connaître les
identifiants. La cascade **remonte et ne descend jamais** : consentir pour l'org
n'utilisera pas l'application d'un particulier, sinon la connexion de toute l'org serait
adossée aux identifiants d'une personne.

⚠️ L'aller (`build_auth_url`) et le retour (`read_saved_fields`) doivent appliquer la
**même** règle : un code d'autorisation est émis pour un `client_id` précis, l'échanger
avec un autre échoue — après le consentement de l'utilisateur, au pire moment.

### App d'ÉDITEUR — le cran au-dessus de l'org

Prolongement direct d'« Application ≠ jeton » : si l'application est une infrastructure,
elle peut être fournie par **oto** plutôt que par chaque org. Sans ce cran, un connecteur
à consentement impose le mode **Self Client** — l'utilisateur crée lui-même une app dans
la console du fournisseur et coche ses scopes à la main (3 incidents : #190, #202, Desk
articles-only). Avec, il ne reste que le geste utile : consentir.

- **Rangement** : scope `PLATFORM` du coffre, `entity_id = editor:<data_center>` — une
  app OAuth est enregistrée dans **sa** région (`accounts.zoho.eu` rejette un client
  `.com`), donc la région fait partie de la clé. Accesseurs
  `credentials_store.{set,get,list,clear}_editor_app`.
- **Ordre de lecture** (`zoho_oauth.app_fields`) : le BYO **prime** (membre > équipe >
  org) — une org qui veut voir SON app dans ses logs la pose et rien ne change pour
  elle ; l'app d'éditeur n'est le repli que si personne n'a rien apporté.
- ⚠️ **Invariant qui rend le rangement sûr** : `walk_cascade` ne propose le palier
  plateforme que si le connecteur déclare `auth_modes ∋ 'platform'`. Les connecteurs à
  consentement ne le déclarent pas ⟹ l'app d'éditeur n'est **jamais** servie comme
  credential d'appel. Sans ça, un membre qui n'a pas consenti hériterait d'une app
  **nue** (sans `refresh_token`) et se prendrait un échec OAuth opaque au lieu de
  s'entendre dire de se connecter. Figé par `tests/test_editor_app.py` (avec
  contre-épreuve sur un connecteur qui, lui, déclare le mode plateforme).
- **Pose** : `POST /api/admin/editor-apps` (super admin, capacité
  `platform.editor_app.set`). **REST seulement** — un secret brut en argument d'outil
  MCP transiterait par le contexte du modèle.
- **Conséquence de rotation** : `persist()` range une COPIE de l'app dans le credential
  né du consentement. Roter l'app d'éditeur ne casse donc pas les connexions déjà
  établies… jusqu'à leur prochain refresh, qui échouera avec l'ancien `client_secret`.

## Validation

Pas de framework de tests dans le repo → validation manuelle sur **PG16 jetable (docker)** + revue adversariale par phase. Migrations idempotentes au boot (`init_db` : ALTER additifs, PK 4-col, backfills, encrypt-existing, drop-plaintext gaté).

## Déchiffrer un credential ad-hoc sur la box (ops)

```bash
# ⚠️ Déchiffrer un credential ad-hoc (crypto.decrypt / _reveal / credential_status) :
# `OTO_MCP_MASTER_KEY` n'est PAS dans .env — start-encrypted.sh la fetch au boot
# depuis Scaleway Secret Manager. Un script qui ne source que .env voit
# `encryption_enabled()=False` → tous les déchiffrements lèvent RuntimeError (FAUX
# négatif, ≠ InvalidTag). Pour reproduire le runtime, répliquer le fetch :
#   set -a; . .env; . /etc/oto-mcp/scw.env; set +a
#   RESP=$(curl -s -H "X-Auth-Token: $SCW_SECRET_KEY" \
#     ".../secret-manager/v1beta1/regions/fr-par/secrets/<id>/versions/latest_enabled/access")
#   export OTO_MCP_MASTER_KEY=$(echo "$RESP" | python3 -c 'import json,sys,base64; print(base64.b64decode(json.load(sys.stdin)["data"]).decode())')
# Vécu 2026-06-22 (triage Sentry InvalidTag : 1 ligne de mount corrompue, écrite
# avec une clé ≠ courante — les autres lignes déchiffraient → pas un souci de clé ;
# fix = purge → re-OAuth). `status_for` doit utiliser `credential_status` (présence
# sans déchiffrer), jamais `get_credential_with_meta`, pour ne pas 500 /api/me.
```
