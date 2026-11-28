---
title: Connector vault — registre + coffre chiffré + résolution
type: reference
description: >-
  Architecture centrale des credentials oto-backend : registre source unique providers/
  (dataclass Connector, 3 axes disponibilité/visibilité/credential, schéma multi-champs
  secret_fields dérivé), coffre chiffré unique connector_credentials (table 4-col PK
  entity_type/entity_id/connector/account, AES-256-GCM obligatoire via crypto.py,
  master key en Scaleway Secret Manager), et résolution access/ (resolve_api_key,
  resolve_credential_fields, status_for,
  list_datastore_namespaces_granted_to).
  Inclut le packing multi-champs, Google multi-compte, LinkedIn/Crunchbase en coffre,
  l'instance comme OBJET (table connector_instances posée A COTE du coffre, id stable
  inst:{id}, lot L6 du chantier tenant & instances),
  et le modèle de connecteur remote (ADR 0003, pilote = un connecteur remote client). À consulter pour
  tout ajout de connecteur, débogage de credential, ou compréhension du chiffrement.
adr:
  - "0003"
  - "0011"
---

# Connector vault — registre + coffre chiffré + résolution

Substrat unique des connecteurs, credentials et accès d'oto-mcp. Déployé en prod (2026-06).
Chiffrement **obligatoire** : tous les secrets vivent chiffrés (`secret_enc`), plus aucune colonne plaintext ni dual-write (purge legacy 2026-06-11). Un serveur sans aucune clé déclarée refuse de démarrer (`crypto.charger_trousseau`, §Les modes de clé) ; un autre processus sans clé (script, maintenance) échoue fort au premier chiffrement ou déchiffrement.

## Registre — source unique (package `providers/`)

Package pur (aucun import oto_mcp, comme `tool_visibility.py`). Une dataclass `Connector` par connecteur, 3 axes orthogonaux :

**Où ça vit** (découpage 2026-08-27, ex-`providers.py` monolithique de 1 900 lignes) :
- `providers/<nom>.py` — le **domicile unique** d'un connecteur : `CONNECTOR = _c(…)`, ses commentaires, et ses constantes curées `CATEGORY` / `PUBLISHER` / `DESCRIPTION` / `LOGO_DOMAIN` / `SANS_LOGO_DE_MARQUE`. Le module porte le nom du connecteur (vérifié à l'import).
- `providers/_model.py` — la **forme** : `CredentialField`, `Connector`, la factory `_c`. Un nouveau CHAMP par connecteur s'ajoute ici, puis se renseigne dans le module du connecteur — jamais dans une liste transverse indexée par nom (c'est ce qu'ont acté `account_noun` puis `cardinality`, cf. ci-dessous).

### Ce que la fiche DIT — l'éditeur nomme qui reçoit l'appel

Tranché par Alexis le **2026-09-02** : *« dire la vérité sur les deux »*. Les champs curés décrivent le service **rendu**, jamais la marque dont on emprunte le nom :

- **`PUBLISHER` = qui reçoit réellement l'appel.** Une passerelle tierce se nomme (`reddit` → `redditapis.com`, l'API de Reddit étant fermée en self-serve) ; un service qu'on écrit et opère soi-même se nomme aussi (`planity` → `Otomata`, le connecteur est le NÔTRE).
- ⚠️ **Et un DÉFAUT ne parle pas à la place d'une déclaration absente — il n'y en a donc PLUS.** Jusqu'au 2026-09-02, sans constante `PUBLISHER`, `publisher_name` retombait sur « Otomata » : une omission était indiscernable du choix « connecteur maison ». C'est ce qui a fait servir le MCP **officiel de Folk** sous notre nom (`folkmcp` — connecteur retiré depuis, avec la fédération MCP, le 2026-09-09) pendant un mois, et les **six canaux de messagerie** hébergés — qui *envoient des messages* par une passerelle tierce — sous notre nom aussi. Le repli est retiré : sans déclaration, la fiche n'affiche **rien** — chaîne vide, jamais `None` ni un libellé de remplacement. Le choix `""` plutôt que `null` se tranche sur le CONTRAT, pas sur le rendu : mesuré le 2026-09-02 en rendant les composants, les cinq surfaces qui affichent l'éditeur traitent les deux à l'identique (elles masquent ou coercent, aucune ne rend « undefined »), mais oto-dashboard déclare `publisher: string` dans `types/api.ts` — `null` le ferait mentir sans rien gagner.

  > **Une absence se voit et se corrige ; une attribution fausse se croit.** *(Alexis, 2026-09-02)*

  Corollaire : **un connecteur légitimement nôtre le DÉCLARE** (`PUBLISHER = "Otomata"`) au lieu d'y retomber — même valeur, mais elle devient un choix relisible. Les trois génériques/maison (`browser`, `web`, `http`) l'ont fait le 2026-09-02 ; les six canaux nomment désormais `Unipile`, la passerelle qui reçoit l'appel et détient la session du compte opéré — déclaré **une seule fois**, chez le porteur de la clé (`providers/unipile.channel`), parce que c'est une propriété du COMPTE et pas du canal. Cliquet : **`tests/test_connector_publisher.py`**, qui exige une déclaration pour **tout** le registre, lit la valeur **SERVIE** (`publisher_name` — donc les deux chemins de déclaration, constante de module *et* champ d'entrée), et refuse aussi la déclaration périmée. ⚠️ Il n'a **aucun filtre de famille**, et il le prouve **par mutation** : un test paramétré sur les familles **présentes** rend muet un connecteur réel de chacune et exige que le contrôle le voie — glisser `kind == "tools"` dans son calcul faisait rougir la famille `mount`, nommément. ⚠️ Cette famille a disparu du registre le **2026-09-09** (retrait de la fédération MCP, **ADR 0069**) ; le test ne perd rien à son départ, précisément parce qu'il se paramètre sur les familles présentes — il couvre aujourd'hui `tools`, `credential` et `remote`, et couvrira la prochaine famille sans que personne y pense. Un test qui se contenterait de recalculer la population serait aveugle au même défaut.
- **`help` dit qu'un intermédiaire existe**, en une clause et sans jargon — avant l'installation, pas après. Et quand la connexion se fait avec les **identifiants du service** (planity : email + mot de passe qu'oto rejoue lui-même, faute d'API publique chez Planity), l'aide le dit : c'est ce que la fiche engage de plus lourd.

  ⚠️ **L'éditeur ne dispense PAS de le dire dans l'aide** — les deux champs ne sont pas lus au même endroit. Le **bloc catalogue injecté au handshake** (`render_namespace_catalog`, ~10 400 c. à chaque session) ne sert que `« label : help »` : il ne porte **jamais** l'éditeur. Une fiche dont seul `PUBLISHER` nommerait la passerelle la tairait donc à l'agent, et à la personne au moment de décider. Les **six canaux hébergés** ont reçu la clause le 2026-09-02, la même pour les six — *« Ton compte se connecte chez Unipile, notre prestataire, qui détient la session. »* — au prix mesuré de **+486 c.** sur le bloc injecté (10 402 → 10 888) et d'une recopie par outil dans `oto_list_my_tools`. Cliquet : `tests/test_unipile_split.py`, sur une population **dérivée du registre** (`credential_of == "unipile"`), pas sur une liste écrite à la main — un septième canal y entre tout seul.
- **`LOGO_DOMAIN` ne pose pas une marque tierce sur un service qui n'est pas le sien.** L'absence se DÉCLARE (`SANS_LOGO_DE_MARQUE = True`), elle ne s'invente pas.
- Le **NOM** du connecteur et de ses tools ne bouge pas pour autant : des appelants s'y accrochent (`docs/alias-deprecies.md`).

⚠️ **Les fautes vont dans des sens DIFFÉRENTS, et une seule relecture les confond.** Les trois du 2026-09-02, corrigées ensemble :

| connecteur | ce que la fiche disait | qui rend le service | le sens de la faute |
|---|---|---|---|
| `reddit` | éditeur « Reddit », logo reddit.com | `api.redditapis.com`, un revendeur | on crédite une marque tierce d'un service qu'elle ne rend pas, et l'intermédiaire disparaît |
| `planity` | éditeur « Planity », logo planity.com | notre propre code, qui rejoue la connexion à `pro.planity.com` | on crédite un tiers de ce qu'on écrit — ça se lit comme une intégration officielle |
| `folkmcp` | éditeur « Otomata » (le défaut) | `mcp.folk.app`, le MCP officiel de Folk | on se crédite du produit d'un tiers |

Chercher « à qui l'appel arrive-t-il ? » attrape les trois ; chercher « la marque est-elle la bonne ? » n'en attrape aucune. *(`folkmcp` a été retiré le 2026-09-09 avec la fédération MCP, ADR 0069 ; `planity` est resté au catalogue, en connecteur NATIF — sa fiche, elle, ne bouge pas d'un iota, c'est toujours notre code qui rejoue la connexion.)*

⚠️ Le ratchet `tests/test_connector_logos.py` a filtré `kind == "tools"` du 2026-08-02 au 2026-09-02, dans ses **deux** directions — donc aucun connecteur fédéré n'était jugé. L'angle mort a coûté aux deux sens : `folkmcp` est resté sans logo ni éditeur déclaré (servi « Otomata »), et la déclaration d'absence légitime de `planity` — alors fédéré — passait pour une entrée morte. Le filtre est retiré : du point de vue de la fiche, la famille d'un connecteur ne change rien à ce qu'on exige de sa fiche. La leçon a survécu au mécanisme : les mounts sont partis le 2026-09-09, **le filtre n'est pas revenu sous un autre nom**.

### La cardinalité d'auth — dérivée, déclarée, et plus jamais listée

Tranché par Alexis le 2026-08-27 : *« ce qui gêne, c'est la LISTE elle-même »*. Trois
crans, dans cet ordre :

1. **Dérivée** du descripteur d'auth — multi dès que le credential se POSE
   (`method=secret`, `secret_kind ∈ {api_key, basic_auth, fields}`, hors
   `personal_cross_org`), mono sinon. C'est le cas normal : **74 connecteurs sur 96**
   n'ont rien à déclarer.
2. **Déclarée** par le connecteur, dans son entrée : `cardinality="mono"|"multi"`, à
   côté de ce qu'elle qualifie et avec son motif. Deux porteurs, et c'est la **mesure**
   qui les a désignés — ceux dont le descripteur dit faux : `google` (OAuth ⟹ dérivé
   mono, mais N consentements = N comptes) et `browser` (cookie ⟹ dérivé mono, mais un
   compte est un **site**). Aucun connecteur ne se déclare `mono` aujourd'hui : ceux
   qui le sont le sont par une condition structurelle.
3. **Surchargée en base** (`connector_settings`), par **org** puis par **plateforme** —
   le patron du bloc d'instructions : constante = défaut, ligne DB = surcharge. Un
   élargissement ne demande plus un déploiement.

**L'ordre est l'arbitrage** : org de contexte > plateforme > registre. Et **une seule
fonction le tranche** : `connectors.cardinality.is_multi_account(connector, org)`.

⚠️ **Le vrai risque du lot n'est pas de mal lire la surcharge, c'est de ne la lire que
d'un côté.** **Trois** chemins très éloignés consultent la cardinalité : la **garde
d'écriture** (« ce deuxième compte a-t-il le droit d'exister ? »), la **résolution**
(« va-t-on le chercher ? ») et la **carte servie** (« l'écran le propose-t-il ? »). Lue
par la première seulement, une surcharge accepterait une ligne que personne n'irait
jamais lire — mot pour mot le défaut d'oto-backend#409, corrigé le 27/08. D'où deux
gardes : une sonde AST qui interdit de lire `Connector.auth_multi_account` hors du seam
et des surfaces d'inventaire, et un test qui pose une surcharge, recharge, et vérifie
que les **verdicts basculent ensemble**.

⚠️ **La carte servie l'a appris à ses dépens (oto-backend#732, 2026-09-01).**
`providers.public_catalog()` posait `auth.cardinality` **depuis le registre**, donc
depuis le défaut du CODE — et c'est cette clé que le dashboard lit pour décider s'il
propose un second compte. Une org élargie par surcharge voyait donc le serveur
**accepter** un geste que l'écran ne **proposait** jamais : le même demi-élargissement
que #409, pris par l'autre bout. Le registre ne pouvait pas faire mieux, il est PUR ;
c'est aux surfaces qui connaissent un requérant — donc une org de contexte — de
repasser par le seam : `cardinality.overlay_for_org(rows, org)`, appliqué dans
`api.public.connectors_catalog` (`GET /api/connectors`) et
`capabilities.connectors.selection._visible_catalog` (`connectors.me` + `oto_search`).
La règle est forcée mécaniquement plutôt qu'écrite : **une fonction qui appelle
`public_catalog()` doit appeler `overlay_for_org()`** — sonde AST dans
`tests/test_connector_cardinality_override.py`, et les deux surfaces sont exercées pour
de vrai dans `tests/connectors/test_carte_cardinalite_servie.py`. Écart **latent** quand
il a été trouvé (aucune ligne de surcharge en base à cette date, donc zéro octet de
différence sur le fil) — un écart qui n'a pas encore coûté, pas une panne. **Le code dit
le possible, la base dit l'exposé.**

⚠️ **Les surcharges vivent en MÉMOIRE, et poser une ligne ne suffit pas.** La
cardinalité est consultée jusqu'à **4× par appel d'outil** (`access/resolve.py`), sur un
serveur mono-loop, contre une base managée distante : une requête par consultation
serait un gel de boucle (`docs/event-loop-perf.md`). Elles sont donc chargées **au
boot** et rechargées par un geste explicite — même patron que le registre d'émetteurs,
et **même conséquence : le rechargement est PAR PROCESS**. Recharger la preprod ne
recharge pas la prod.

```
oto_admin_connector_setting op=set   connector=… value=mono|multi [org_id=…]
oto_admin_connector_setting op=reload          # ← sans lui, la ligne ne fait rien
oto_admin_connector_setting op=list            # `rows` = la base, `active` = CE process
```
(`POST /api/admin/connectors/settings`, SUPER_ADMIN.) `op=list` rend **les deux** —
les lignes de la base et les surcharges vivantes du process : c'est précisément l'écart
qu'un admin doit voir avant de se demander pourquoi son réglage « ne marche pas ».

L'axe d'appel `_account=` fait exception, volontairement : il est **org-agnostique et
permissif** (`accepted_anywhere`). Il est lu par le middleware, où résoudre l'org
coûterait une requête par appel — et il n'autorise rien, il NOMME un compte, la
résolution refusant (actionnable) si ce compte n'existe pas. Le refuser rendrait en
revanche une org élargie incapable de viser son second compte.

⚠️ **`MULTI_ACCOUNT_PROVIDERS` a été retirée le 2026-08-29**, et sa disparition EST le
lot. Elle confondait deux choses sans rapport, et c'est ce qui la rendait
indéboulonnable : la **cardinalité** (qui parle du coffre) et l'**annonce statique de
l'axe `_account=`** (qui parle du schéma des tools, recopié à chaque handshake — donc
curé, `test_call_axes_budget`). La seconde est devenue `account_axis_static`, sur les
mêmes quatre connecteurs. ⚠️ `zoho` et `folk` n'ont **rien** à déclarer : depuis que la
règle couvre les credentials multi-champs, la dérivation les rend multi toute seule —
les garder déclarés aurait reconduit la liste sous un autre nom. Un test AST fige
l'absence de la liste, sous ce nom ou un autre.
- `providers/__init__.py` — l'**agrégateur** : `_DECLARATIONS` (l'ordre, écrit à la main — jamais un `glob`) + toutes les dérivations. Il ne décrit aucun connecteur.

⚠️ La déclaration ne vit **pas** dans `tools/<nom>.py` (le module d'outils du même connecteur) : celui-ci importe `..access`, qui importe le registre — cycle — et `register_all` le charge en try/except, si bien qu'une dépendance optionnelle manquante retirerait le connecteur du **catalogue**, pas seulement ses outils. Le registre doit rester pur et sans dépendance.

Invariants verrouillés par `tests/test_providers_registry_snapshot.py` : le registre EST la concaténation des déclarations, chaque déclaration a exactement un domicile (les deux sens), l'ordre est déterministe.
- **A. Disponibilité** : `availability` ∈ {`self_serve`, `platform_granted`}. platform_granted = grant-only (deny-by-default, ex. `gocardless`, ou un connecteur remote réservé à un client).
- **B. Visibilité** : `default_active` (ADR 0050 — socle installé d'office au seed d'un nouveau (sub, org) ; **vide depuis le 16/07** : tout le catalogue est en library installable, l'agent guide).
- **C. Credential** : `auth_modes` ⊆ {`byo_user`, `byo_org`, `platform`} ; `keyed` (résolu via `resolve_api_key`) ; `secret_kind` (api_key/basic_auth/fields/refresh_token/oauth/cookie/none) ; `personal_session` ; `env_secret_name` ; `default_quota`.
- **Modèle de saisie multi-champs** (ADR 0011) : `secret_fields` (propriété) = schéma de saisie du credential — `credential_fields` explicites (`CredentialField` name/label/secret/**when**/**choices**) ou dérivés du `secret_kind` (`api_key`=1 champ `key` ; `basic_auth`=`email`+`password`). Vide pour `cookie`/`oauth`/`none` (flux dédiés). SOURCE UNIQUE du formulaire dashboard, de l'endpoint `/api/settings/api-keys`, de `status_for` et du packing — zéro branche par connecteur (ex. Silae = 3 champs déclarés, aucun code spécifique).
- **Un champ peut en SÉLECTIONNER d'autres** (`Connector.field_discriminator`, 2026-08-27) : `api` déclare `auth_mode`, et chaque champ dit par `when=` les modes qui le rendent pertinent. `Connector.fields_for(valeurs)` en dérive les champs à afficher ET ceux dont le `required` s'applique ; `choices=` ferme le jeu de valeurs d'un champ. Le descripteur `auth` publie les trois (`field_discriminator`, `when`, `choices`) pour que le front filtre **sans connaître le connecteur**. Discriminant absent (les ~90 autres) = comportement inchangé.
- **Chargement** : `Connector.modules` = modules `tools/<m>.py` à importer (kind="tools" ; défaut = nom du provider). Voir §Chargement dérivé.

**Tout dérive du registre** (mêmes symboles, ré-export) : `KEY_PROVIDERS`, `ORG_SHAREABLE_PROVIDERS`, `ADMIN_GRANT_ONLY_NAMESPACES`, `QUOTA_DEFAULTS`, `ENV_SECRET_NAMES`, `DEFAULT_BUNDLE/PRESET`. Plus de listes en dur parallèles. `GET /api/connectors` = vue publique.
Helpers : `require_keyed`, `is_byo_user`, `is_org_shareable`, `require_credential(entity_type, name)` (user→byo_user, org→org-partageable).

## Lire et modifier un credential — ce qui sort, ce qui ne sort plus

⚠️ **Une clé posée ne se relit JAMAIS — décision du 2026-08-31 (oto-backend#671).**
Jusqu'à cette date, `GET /api/settings/api-keys/{provider}` rendait la valeur ENTIÈRE,
en clair, de tout champ déclaré `reveal=True` — et c'était le **défaut** : les 49
connecteurs `secret_kind="api_key"` sans `credential_fields` explicites héritaient d'un
`key` révélable, plus 6 déclarés à la main, soit **55 connecteurs**. Le cran `reveal`
est **retiré du registre** (pas neutralisé : le passer lève un `TypeError`) ; `secret`
décide seul, et c'est exactement ce que le front lisait déjà (`auth.fields[].secret`).

Ce que la lecture rend aujourd'hui, à tous les paliers :

- **les champs NON secrets, tels quels** (`base_url`, `auth_mode`, `region`, un email) ;
- **de quoi reconnaître la clé sans la lire** : `configured`, `read_set_at`,
  `read_set_by` (qui l'a posée), et `read_fingerprints` — `{champ: 4 caractères}`, un
  HMAC **lié à la ligne du coffre**, jamais des caractères du secret. Le front affiche
  `•••• 3f7a`. Un champ secret vide n'y figure pas.
- **rien d'autre** : la clé d'un champ secret est **ABSENTE** du corps, pas `null` ni
  `""`. Un champ vidé se lirait « aucune clé posée » et un appelant continuerait sur du
  vide — c'est le mode d'échec de `oto ninja secrets get`, dont l'usage documenté est
  `export FOO=$(…)`, où `export` masque le code de sortie.
- **demander la valeur reçoit un refus nommé** : `?reveal=1` → `403
  secret_never_revealed`, jamais un 200 amputé.

⚠️ **L'empreinte est liée à sa ligne, à dessein.** Une empreinte globale de la valeur
serait la même pour la même clé partout : qui lit l'empreinte d'un palier pourrait poser
un candidat sur une ligne qu'il contrôle et comparer — un oracle de confirmation à
1/65536 sur quatre caractères. Liée à `(entity_type, entity_id, connector, account,
champ)`, la seule façon de comparer est d'écraser la clé qu'on cherchait à confirmer.
Primitive : `journal_secrets.fingerprint()`, même clé HMAC que le masque du journal
(`OTO_MCP_OAUTH_STATE_SECRET`, présent en prod → stable d'un boot à l'autre).

**Ce que la révélation ne servait pas** : la modification PARTIELLE. Elle tient au MERGE
côté serveur (#448), pas à la relecture — le dashboard jetait déjà toute valeur secrète
reçue (`CredentialFieldsDialog.vue`, garde `!f.secret`).

- **L'écriture est un MERGE côté serveur.** Une clé absente du corps est complétée
  depuis le coffre (`credentials_store.merge_with_existing`) ; une clé présente mais
  **vide est un effacement explicite**. Le formulaire du dashboard, qui poste tous
  ses champs, garde donc son comportement de remplacement. **C'est le seul chemin de
  changement d'une clé** : on repose, on ne relit pas.
- **La lecture existe à TOUS les paliers** (oto-backend#448, 2026-08-27).
  `me.credential.get` prend un `scope` (`member` défaut / `group` / `org`, les deux
  derniers gardés admin du palier, l'org_admin subsumant l'admin d'équipe).

⚠️ **Le merge se fait dans la même ligne, jamais par le client.** L'AAD dérive de
`entity_type/entity_id/connector/account` : relire, fusionner et rechiffrer sur place
ne la change pas. Le secret ne repasse à aucun moment sur le fil.

⚠️ **Le corps rendu par le GET est PLAT** : ses autres clés sont celles du connecteur.
`api` déclare un champ nommé `scope` (les scopes oauth2) — d'où les clés d'enveloppe
`read_scope`/`read_account`, préfixées à dessein. Toute clé ajoutée à ce corps doit
rester impossible à confondre avec un `credential_field`.

**Ce que ça a coûté avant** : jusqu'au 2026-08-27, `_get` lisait `MEMBER` **en dur** et
`groups_secrets` n'exposait que set/delete. Un credential d'équipe n'avait donc aucune
surface capable d'en rendre la `base_url`, alors que le registre la déclare non
secrète — et l'écriture étant un remplacement total, changer cette URL exigeait
de réécrire un bearer que rien ne restituait. Piège à perte de données par
construction, sur le chemin de TOUS les ponts clients (ADR 0003/0037). Un repointage
de pont en production a été abandonné devant ce formulaire.

⚠️ Défaut de la même famille, corrigé avec : l'éligibilité de ces trois routes testait
`is_byo_user` **quel que soit le palier** — un connecteur purement `byo_org` comme
`http` se faisait répondre « connecteur inconnu » en lecture comme en retrait, même à
un admin d'org. C'est `is_org_shareable` qui décide aux paliers équipe et org.

## Egress — la destination d'un connecteur (`oto_mcp/egress.py`)

Sept connecteurs prennent leur destination dans le credential, donc chez un **admin
d'org cliente** : `api` (`base_url` ET `token_url`), `n8n`, `make`, `github`
(Enterprise), `lighton`, `posthog`, `salesforce` (`login_url` — celui-là reçoit le
refresh token, donc un secret). Le process tourne en root, sans conteneur : sa boucle
locale porte l'administration du proxy web, le pilotage du navigateur et les autres
services de la machine.

**Ce qui est refusé** : toute destination dont l'hôte **résout** vers une adresse non
publique — boucle locale, plages privées (RFC 1918 / ULA), lien-local (les métadonnées
d'infra), réservées, multicast, non spécifiée. Le contrôle porte sur la RÉSOLUTION, pas
sur la forme du texte : un nom de domaine public qui pointe vers l'intérieur est refusé
comme une adresse écrite en clair, et un hôte qui résout à la fois public et interne est
refusé tout court. Le refus nomme la raison, l'adresse jointe, la carte à corriger, et
l'entrée exacte à déclarer.

**L'instance elle-même est refusée (#1499)**, quelle que soit la politique et sans
exception possible : l'hôte de `OTO_MCP_PUBLIC_URL` (`config.public_host()`), ses
sous-domaines et ses hôtes épinglés par org (`<slug>--<hôte>`). Un agent n'appelle pas
oto par un connecteur — une carte `api` qui visait l'instance joignait `/api/hooks/…`
avec un porteur obtenu ailleurs, hors de toute liste d'outils. Sans la variable, la
garde **lève** (`RuntimeError`, comme `config.public_host`) plutôt que de deviner. ⚠️
Un autre nom du même service (IP de la machine, domaine de tenant) n'est pas reconnu :
comparer les adresses résolues refuserait tout site qui partage un frontal avec nous.

**Chaque appel sortant dit au nom de qui il sort** (`titulaire`, paramètre OBLIGATOIRE
de `egress.check_url`, `egress.decide` et des sessions d'`egress_transport`) :

- un identifiant du coffre : sa ligne (`egress.titulaire_de(entity_type, entity_id)` —
  `ResolvedCredential.titulaire`, `instance` d'une sonde via
  `egress.titulaire_d_instance`, champs résolus via `egress.titulaire_des_champs`) ;
- `egress.PLATEFORME` : un identifiant posé par l'opérateur ;
- l'org d'une application de Custom API pour ce qu'un administrateur de cette org y a
  saisi (découverte, `token_url` du jeton par membre) ;
- `egress.AUCUN` : une clé dont la ligne n'existe pas encore (pose, échange d'un code) ;
- `egress.AGENT` : une URL choisie par un agent (lecture de page, source `url`, import
  public, avatar, CV) — aucune exception, et le refus le dit à l'agent.

Un appel qui ne le dit pas ne s'exécute pas (`TypeError`). Le transport épinglé décide à
la connexion avec le MÊME titulaire que le contrôle précoce.

**Les exceptions se DÉCLARENT, chacune pour son titulaire** — variable d'environnement
du service :

```
OTO_EGRESS_ALLOW="pont-local=127.0.0.1:9000, pont-equipe=127.0.0.1:9002@group:3, pont-partage=10.0.0.5:9003@group:3,org:7"
```

Grammaire : `nom=adresse:port[@palier:id[,palier:id…]]`, entrées séparées par des
virgules ou des retours à la ligne ; un morceau sans `=` qui suit une entrée à suffixe
est un titulaire de plus. `palier` ∈ `group`, `org`, `tenant` ; `id` = l'`entity_id` de
la ligne du coffre (l'id de l'équipe, de l'org, le slug du tenant).

- **Sans `@`** : la plateforme seule. C'est le format d'avant, toujours lisible : une
  exception posée avant les titulaires vaut désormais pour la plateforme seulement,
  jusqu'à ce qu'on lui ajoute un titulaire.
- **Avec `@palier:id`** : les seuls identifiants dont la ligne a EXACTEMENT ce palier et
  cet id. Une équipe n'hérite pas des exceptions de son org, ni l'inverse ; une clé
  personnelle de membre n'en reçoit aucune.

**Déployer cette version (titulaires des exceptions).**

1. AVANT de déployer : réécrire `OTO_EGRESS_ALLOW` en ajoutant leur titulaire
   (`@palier:id`) à chaque exception qui sert une org ou un groupe ; une exception
   sans `@` ne sert plus que la plateforme. Relire la valeur : une valeur mal formée
   arrête le service au démarrage.
2. Déployer. Au démarrage, le journal nomme (noms seulement) les exceptions restées
   sans titulaire, donc réservées à la plateforme : « exceptions d'egress réservées à
   la plateforme : a, b ; … ». Rien n'est journalisé quand il n'y en a pas.
3. Retour arrière, dans l'ordre inverse : remettre l'ancienne valeur, sans `@`, AVANT
   de revenir à la version précédente — son lecteur lève une erreur sur `@` à chaque
   sortie vers une adresse interne.

**Adresse littérale obligatoire** (un nom d'hôte peut se déplacer sous nous) et **port
obligatoire** (une exception par adresse seule ouvrirait toute la machine : le pont sur
9000 donnerait aussi l'administration du proxy sur 9001). Le défaut est **vide** : ce
dépôt est public, les destinations réelles ne s'y écrivent pas. Une entrée illisible
(destination sans port, nom d'hôte, palier inconnu, `@` sans id) **refuse le
démarrage** (`egress.verifier_reglages`, appelé au boot) : jamais un repli sur une liste
partielle ou vide. Le refus d'une destination interne propose l'entrée exacte, suffixe
du titulaire compris.

⚠️ **Un pont oublié dans la liste échoue bruyamment**, avec le message ci-dessus : c'est
voulu. Avant d'armer sur un déploiement, établir les destinations des instances
existantes — `GET /api/settings/api-keys/{provider}` rend les champs NON secrets, donc
`base_url`, sans jamais rendre de secret (scope `org`/`group` = admin du palier).

**Aucune sortie réseau hors garde.** `tests/test_egress_titulaires.py` parcourt le code
servi : `requests.<verbe>`/`requests.Session`, `httpx`, `aiohttp`, `urllib.request`,
`socket.create_connection`/`socket.socket` y sont refusés hors d'une liste d'exceptions
NOMMÉES (`_SORTIES_ADMISES`), chacune avec sa raison — une destination fixe du code, une
table fermée, ou un service déclaré par l'opérateur. Une exception qui ne sert plus
rougit aussi. Un sous-domaine saisi qui entre dans un nom d'hôte (Pipedrive) est validé
à la forme stricte d'une étiquette DNS.

**La garde fait foi à la CONNEXION (`oto_mcp/egress_transport.py`).** `check_url` reste
le refus précoce, celui qui explique et nomme la carte. Mais le client HTTP résolvait
l'hôte une seconde fois pour se connecter : un DNS qui répond « public » au contrôle puis
« interne » à la connexion (DNS rebinding) passait, et une redirection 3xx vers
l'intérieur aussi. Une session `requests` gardée (`egress_transport.session`, `.harden`,
`.pinned`) monte un adaptateur dont chaque connexion — redirections comprises, puisque
chaque saut ouvre la sienne — résout l'hôte UNE fois, applique la même décision
(`egress.refuse_instance` + `egress.decide`, exceptions selon la politique) et ouvre la
socket sur l'une de ces adresses ; le nom d'origine reste celui du SNI, de la
vérification du certificat et de l'en-tête `Host`. Pas de patch global de la
résolution : le processus parle légitimement à sa boucle locale. Les proxies
d'environnement sont ignorés (`trust_env=False`) et un proxy explicite est refusé —
derrière un proxy, c'est lui qui résout. Le dépôt ne lit aucune variable de proxy ; une instance derrière un proxy d'egress ou un magasin de certificats propre (`REQUESTS_CA_BUNDLE`) voit ces chemins échouer, fermés.

Câblé : `web_read`/`web_image` (`tools/web.py`), `infosec` (en-têtes HTTP, RDAP, journaux de certificats par la session gardée de l'agent ; la poignée TLS se connecte à l'adresse jugée par `egress.decide`), une source `url` de fichier
(`file_source.py`), l'import d'une description publique
(`api_operations.fetch_public_document`), le CV d'une candidature (`tools/wttj_resume.py`),
l'import d'avatar, la session des clients `n8n`, `make`, `github`, `posthog`, `lucca` et
du webhook `clay` (`egress_transport.pinned`), et l'échange du code OAuth de Salesforce sur
`login_url` (`auth/salesforce.exchange_code` passe une session gardée à
`auth/flow.exchange_code`, dont le défaut — hôte fixe des autres fournisseurs — ne change
pas). Les clients dont la lib prend une session à la construction (`session=`, oto-core
v1.250.0) la reçoivent gardée, et TOUTES leurs requêtes y passent : `api`
(`tools/api.client_for` : les appels et le login du mode session sur `base_url`, le jeton
`oauth2` sur `token_url` par une session à part, `token_session=` ; le pont MCP
`connectors/api_mcp` aussi), `wordpress` (sonde, index public, appels — `site_url`),
`lighton` (`base_url`, SaaS compris), `salesforce` (l'échange du refresh token sur
`login_url` et les appels sur l'`instance_url` rendue, chacun nommé dans le refus) et
`threecx` (jeton ET appels — `base_url`). Politique : celle de l'ORIGINE du credential,
la même pour leur `check_url` précoce et leur session (ci-dessous). Les sessions
gardées reprennent le magasin de certificats de l'environnement (`REQUESTS_CA_BUNDLE`,
puis `CURL_CA_BUNDLE`) pour la seule vérification TLS — une autorité privée chez un
client ; les proxies d'environnement restent ignorés.
Banc : `tests/test_egress_transport.py`.

⚠️ **Ce qui reste sous le seul contrôle précoce** : un appel `requests.<verbe>` au niveau
module dans la lib, qui ne passe pas par `.session` — `egress_transport.pinned` ne couvre
que `client.session`. Le fermer demande un paramètre `session=` côté lib.

⚠️ **Contredit l'ADR 0037 §4**, qui décidait « zéro code SSRF dans le connecteur » et
renvoyait le blocage des plages privées à un egress proxy « plus tard… si le besoin
naît ». Ce proxy n'a jamais existé ; le seul filtrage réel est un `IPAddressDeny` du
lien-local. L'ADR (dépôt d'archive, lecture seule) reste à amender : la garde vit dans
le code, et depuis le transport épinglé elle tient aussi contre le rebinding et les
redirections, le point sur lequel l'ADR avait raison.

## Coffre — `connector_credentials` (table unique)

A remplacé (et les a fait DROP, purge 2026-06-11) les 9 colonnes `users.<provider>_api_key`, `org_secrets`, les colonnes session (`users.linkedin_*`/`crunchbase_*`) et la table `user_google_oauth`. `init_db._drop_legacy_plaintext_stores` exécute les `DROP … IF EXISTS` (idempotent, no-op sur DB fraîche on-prem).

```
connector_credentials(entity_type, entity_id, connector, account, secret_enc,
                      secret_kind, meta JSONB, set_by, set_at,
                      PK(entity_type, entity_id, connector, account))
```
- `entity_type` ∈ {`member`, `user`, `org`, `group`, `tenant`, `platform`} ; `entity_id` = `org:sub` (member, ADR 0033) | `sub` (user, résidu OAuth) | `org_id::text` | `group_id::text` | **slug du tenant** (L-clés PR 1, 2026-08-29 — façade `tenant_vault`) | label de la clé (platform, ADR 0044 §F). Toujours requêter `(entity_type, entity_id)` ENSEMBLE. ⚠️ Cette ligne a dit `{user, org}` du 2026-06 au 2026-08-29 alors que quatre valeurs de plus étaient servies.
- `account` = discriminant **multi-compte** ('' = mono ; ex. email Google). 1 ligne par compte connecté.
- `secret_enc` = enveloppe chiffrée (pas de colonne plaintext). `meta` = satellites NON-secrets (user_agent linkedin/crunchbase, scopes/is_default/client_id google). Aucune clé de forme jeton (`credentials_store.BEARER_META_KEYS` : `access_token`, `refresh_token`, `id_token`) : un jeton d'accès vit en mémoire, par processus, un satellite secret qui doit durer se scelle (`seal_satellite`, §Le jar de session). Cette liste fermée sert trois fois : les surfaces de statut la masquent, les relais qui recopient `meta` sur une autre ligne (`copy_connector_rows`, reconnexion Microsoft) ne la recopient pas, et `oto-mcp maintenance jetons-en-meta` la retire des lignes d'avant (`docs/commands.md`).

Store = `credentials_store.py` (calqué sur le palier org, réutilise `db._connect`, jamais d'import circulaire) :
`get_credential` / `get_credential_with_meta` (secret+meta+set_at, déchiffre) / `credential_status` (présence+meta SANS déchiffrer, pour /api/me) / `has_credential` / `set_credential` (chiffre) / `clear_credential` / `update_meta` (merge JSONB sans re-chiffrer) / `list_accounts`.
- **Packing multi-champs** : `pack_secret(connector, fields)` / `unpack_secret(connector, secret)` encodent les `secret_fields` dans l'unique `secret_enc` — 3 formats selon la forme : 1 champ (`api_key`) = valeur brute (back-compat) ; `basic_auth` = `base64("email:password")` (format qui servait aussi de fil vers un serveur MCP **fédéré** distant qui le décodait — la fédération MCP est retirée depuis le **2026-09-09**, **ADR 0069** ; le format reste, sans consommateur distant, et des credentials restent stockés ainsi — dont ceux de `planity`, devenu natif le même jour sans qu'un octet du coffre bouge) ; ≥2 champs = `json`. L'endpoint de saisie et `resolve_credential_fields` passent par là.
- **Un champ rangé dans `meta`** (`CredentialField(in_meta=True)`, 14/09/2026) : sa valeur vit EN CLAIR dans `meta`, hors du chiffré, et **la forme du chiffré ne se lit que sur les champs qui y vivent** (`Connector.vault_fields`). C'est ce qui permet d'ajouter un champ NON secret à un connecteur dont des credentials sont déjà posés sans reconditionner une seule ligne : une clé brute reste brute, et l'ancien code comme le nouveau relisent la même ligne de la base partagée. ⚠️ Refusé à l'import sur un champ `secret=True` — `meta` sort vers les écrans de statut. La pose partagée (org, équipe, tenant) passe par `preparer_pose`, qui rend le secret ET le patch de `meta` (que la capacité fusionne : `_upsert` remplace `meta`) ; la repose conserve un champ `in_meta` absent du corps et efface un champ envoyé vide (#448), y compris quand seule la clé arrive par `api_key`. Cas fondateur : le `workspace_id` d'une clé d'organisation `anthropic`.
- **La clé plateforme se pose par la même porte** (`POST /api/admin/platform-keys` → `preparer_pose`) : sur un connecteur dont le chiffré porte plusieurs champs (`forager`, `transcription`), une clé envoyée seule par `api_key` devient son premier champ SECRET **à la pose**, les autres passent par `fields`. Avant, la valeur collée était stockée brute et la lecture la rattrapait ; les lignes de cette époque se réécrivent une fois par `oto-mcp maintenance cles-plateforme-au-format` (à blanc par défaut, `--apply` pour écrire, rien de réécrit si une ligne est illisible, aucune valeur au rapport).
- **Un champ `in_meta` sorti du chiffré laisse des lignes d'avant** : `qonto` (#1906) range le `login` dans `meta.login` et la seule clé secrète dans le chiffré ; les lignes posées avant portent la paire `login:clé` dans le chiffré, que `tools/qonto.py:_authorization` lit encore. Elles se rangent une fois par `oto-mcp maintenance qonto-deux-champs` (`docs/commands.md`) : à blanc par défaut, des comptes seulement, `--appliquer` réécrit sous verrou par `_upsert` (même AAD, `meta` relu et complété) ; une ligne dont `meta.login` diffère du préfixe n'est jamais convertie. La lecture de l'ancien format ne se retire qu'après.

## Chiffrement au repos — `crypto.py`

Enveloppe **AES-256-GCM**, **obligatoire** (`set_credential`/`_pk_encrypt` chiffrent toujours ; `crypto.encrypt`/`decrypt` lèvent si master key absente — pas de stockage ni lecture plaintext). Master key **hors-DB** (env `OTO_MCP_MASTER_KEY`, hex64 ou base64-32o ; en prod fetchée de Scaleway Secret Manager au boot, cible KMS unwrap, cf. `ADR 0002 (meta privé otomata-private/docs/adr)`). AAD = `connector_credentials:{entity_type}:{entity_id}:{connector}[:{account}]` (anti-transplant ; segment account omis si vide → compat ascendante mono-compte). Envelope = `key_ref(1o)‖nonce(12o)‖ct`.
- Déchiffrement **JIT** dans `resolve_api_key`/`get_credential` uniquement, jamais loggé ; `status_for` lit la présence (`has_credential`/`credential_status`), ne déchiffre pas. Échec de déchiffrement = LÈVE (pas de fallback silencieux).
- `platform_keys` : secret dans `api_key_enc` (même pattern, AAD `platform_keys:{provider}:{label}`).
- **Le trousseau et `key_ref`** : le premier octet de l'enveloppe (`key_ref`) nomme la clé qui l'a scellée. `crypto.trousseau()` rend `{key_ref: clé}`, les clés que l'instance sait ouvrir ; `decrypt` lit `key_ref` et y prend sa clé (`cle_du_trousseau`), `encrypt` écrit sous le `key_ref` d'écriture déclaré (`key_ref_d_ecriture`, `OTO_MCP_CLE_ECRITURE`, `01` par défaut = `KEY_REF_ECRITURE`). `0x01` (`CLE_PLATE`) = `OTO_MCP_MASTER_KEY`, que portent toutes les enveloppes écrites avant le mode Key Manager ; les autres `key_ref` sont des clés de données enveloppées par Key Manager (§Les modes de clé). Un `key_ref` absent du trousseau lève `crypto.CleInconnue`, distincte d'une enveloppe qui ne s'ouvre pas (`EnveloppeIndechiffrable`, clé périmée ou AAD fausse) : un appelant qui jette un cache illisible ne l'avale pas. `key_ref` n'entre pas dans l'AAD : changer la clé d'une ligne ne change pas son identité. Personne d'autre ne lit la clé : l'export de périmètre aussi passe par le trousseau (l'import vérifie la clé `0x01` de la cible, §Les modes de clé).
- **La liste des valeurs chiffrées** est `export_perimetre/rechiffrement.COLONNES_CHIFFREES` (colonne et AAD de la ligne) : `connector_credentials.secret_enc` (clés d'instance comprises), les satellites `meta.<nom>_enc`, `runner_triggers.hook_signing_secret_enc`, `transcription_jobs.api_key_enc`, `docs.public_token_enc`, `process_shares.token_enc`. `tests/export_perimetre/test_colonnes_chiffrees.py` refuse une colonne `*_enc` du schéma ou un appel à `crypto.encrypt` qu'elle ne couvre pas.
- **`oto_admin_vault_health`** (`GET /api/admin/vault/health`) : en plus des lignes du coffre qui ne se déchiffrent pas, `by_key_ref` compte, pour chaque valeur de `COLONNES_CHIFFREES`, les lignes par `key_ref` (premier octet lu en base, rien d'autre ; « illisible » si l'enveloppe n'a pas la forme base64).
- Dump Postgres = **ciphertext only**. Perte de master key = perte totale → Secret Manager versionné + escrow.


### Les modes de clé : plate et Key Manager

Le mode est **déclaré**, jamais deviné : `crypto.declaration()` lit l'environnement, et
`crypto.charger_trousseau()`, au démarrage du serveur (`server.main`, avant la base),
refuse de démarrer sur toute incohérence, avec un message qui la nomme.

| mode | variables | clés du trousseau |
| --- | --- | --- |
| **plate** | `OTO_MCP_MASTER_KEY` seule (`OTO_MCP_CLE_ECRITURE` absente ou `01`) | `0x01` |
| **Key Manager, transition** | `OTO_MCP_MASTER_KEY` + `OTO_MCP_TROUSSEAU` + `OTO_MCP_KMS_CLE` + `OTO_MCP_KMS_REGION`, écriture `01` puis `02` | `0x01` et `0x02` |
| **Key Manager seul** (après le rechiffrement de fond et le retrait de la clé plate) | les trois variables Key Manager, écriture `02` | `0x02` |

- `OTO_MCP_TROUSSEAU` (secret, tiré du Secret Manager par le lanceur comme les autres,
  déclaré dans `OTO_SECRETS_OPTIONNELS`) : `{"02": "<ciphertext rendu par GenerateDataKey>"}`.
  Le `key_ref` s'écrit en deux chiffres hexadécimaux, `01` y est interdit (c'est la clé
  plate). `OTO_MCP_KMS_CLE` (l'id de la clé Key Manager qui enveloppe) et
  `OTO_MCP_KMS_REGION` ne sont pas secrets : ils vont dans le `.env`.
- **Le déchiffrement** se fait dans `crypto`, une fois par processus et par clé :
  `POST https://api.scaleway.com/key-manager/v1alpha1/regions/{region}/keys/{id}/decrypt`,
  corps `{"ciphertext": …}`, réponse `{"plaintext": <base64>}`, en-tête `X-Auth-Token`
  (`urllib`, comme `deploy/lanceur_secrets.py`). Délai de 15 s par essai, 3 essais sur une
  panne réseau, un 429 ou un 5xx ; un autre refus (403, 404, 400) ne se rejoue pas. La clé
  de données ne vit qu'en RAM : ni disque, ni environnement, ni journal. Le lanceur ne
  déchiffre rien : la clé en clair ne passe jamais par `/proc/<pid>/environ`.
- **La clé d'API** de `Decrypt` (une application IAM dont la seule permission est
  `KeyManagerKeyDecrypt`, dans le projet Scaleway dédié à la clé) arrive par
  `LoadCredential=scw-kms:/etc/oto-mcp/scw-kms.key` : un fichier 0600, root, lu dans
  `$CREDENTIALS_DIRECTORY/scw-kms`. **Aucune unité du dépôt ne porte cette ligne** : elle se
  pose À LA MAIN, au passage à Key Manager, et seulement une fois le fichier en place, car
  un chemin absent fait échouer l'unité. On garde la forme avec chemin, portable : la forme
  sans chemin (recherche dans `/etc/credstore/`) n'existe qu'à partir de systemd 250. La
  ligne se pose par un **fichier de complément** (drop-in), pas dans l'unité elle-même :
  `oto-mcp-maintenance.service` est reposée à chaque déploiement de prod
  (`install_timers`), et les unités d'une instance cible à chaque amorce ; un complément
  survit à ces reposes :
  ```
  # /etc/systemd/system/<unité>.d/scw-kms.conf, puis systemctl daemon-reload
  [Service]
  LoadCredential=scw-kms:/etc/oto-mcp/scw-kms.key
  ```
  Unités concernées : le service bleu/vert de chaque rôle (`oto-mcp@.service`,
  `oto-mcp-canari@.service`), `oto-mcp-maintenance.service`, `oto-journal-archive.service`.
  Un `systemd-run` qui lance le lanceur à la main ajoute
  `-p LoadCredential=scw-kms:/etc/oto-mcp/scw-kms.key` (docs/commands.md §mode Key Manager).
- **Au démarrage**, le journal dit, pour chaque clé, son `key_ref`, son origine et une
  empreinte courte (`crypto.empreinte_cle`, 12 caractères), puis le mode et le `key_ref`
  d'écriture. Jamais la clé, jamais le jeton.
- **Les autres processus** (maintenance, scripts par `lanceur_secrets.py --script`,
  `oto-mcp perimetre`) passent par le même `crypto.trousseau()`, qui ouvre les clés
  enveloppées à leur premier usage.

**Refus nommés** (le serveur ne démarre pas) :

- aucune clé déclarée (ni `OTO_MCP_MASTER_KEY`, ni `OTO_MCP_TROUSSEAU`) ;
- `OTO_MCP_CLE_ECRITURE` qui nomme un `key_ref` absent du trousseau (`02` sans trousseau,
  `01` sans clé plate) ou qui n'est pas un `key_ref` ;
- `OTO_MCP_TROUSSEAU` illisible ou vide, qui porte `01`, un `key_ref` mal écrit ou une
  valeur vide ; trousseau sans `OTO_MCP_KMS_CLE` ou sans `OTO_MCP_KMS_REGION` ;
  `OTO_MCP_KMS_CLE` ou `OTO_MCP_KMS_REGION` sans trousseau ;
- clé d'API absente (`LoadCredential=scw-kms`), Key Manager qui refuse (HTTP 4xx), clé
  ouverte qui ne fait pas 32 octets ;
- « Key Manager injoignable : clé <id>, région <r> — démarrage refusé » après 3 essais.
  En bleu/vert, la couleur qui sert garde son trousseau en RAM : la bascule échoue
  proprement, rien ne s'arrête.

**L'ordre de bascule** (chaque étape est réversible jusqu'au retrait de la clé plate) :

1. Infra pose la clé Key Manager, l'application IAM `Decrypt` et
   `/etc/oto-mcp/scw-kms.key` (0600, root) ; PUIS, le fichier en place, le complément
   `LoadCredential=scw-kms:/etc/oto-mcp/scw-kms.key` de chaque unité (ci-dessus) et
   `systemctl daemon-reload` ; puis le secret `OTO_MCP_TROUSSEAU` (`/prod` et `/preprod`),
   qu'elle ajoute à `OTO_SECRETS_OPTIONNELS`, et `OTO_MCP_KMS_CLE`, `OTO_MCP_KMS_REGION` dans
   le `.env`.
2. Redémarrage (un déploiement) **en restant en écriture `01`** : le journal doit montrer
   `key_ref=0x01` et `key_ref=0x02` chargées, avec leurs empreintes. Rien n'est encore écrit
   sous `0x02`.
3. Vérifier : `oto_admin_vault_health` sain, aucune erreur au journal.
4. Passer `OTO_MCP_CLE_ECRITURE=02` dans le `.env`, redéployer : toute écriture neuve est
   sous `0x02` (`by_key_ref` de `oto_admin_vault_health` le montre).
5. Retour arrière : remettre `OTO_MCP_CLE_ECRITURE=01` suffit tant que la clé plate reste au
   trousseau ; les lignes déjà en `0x02` restent lisibles tant que le trousseau est posé.
6. Ensuite (hors de ce mode) : le rechiffrement de fond des lignes `0x01`, puis le retrait
   de `OTO_MCP_MASTER_KEY`.

**Export de périmètre** : l'export rechiffre chaque secret sous la clé **plate** de la cible
(`OTO_EXPORT_CLE_CIBLE`, `key_ref 0x01`) ; l'import compare l'empreinte du manifeste à la
clé `0x01` de son trousseau, qu'elle soit ou non la clé d'écriture. Une cible en transition
importe donc normalement. Une cible sous Key Manager seul ne peut pas recevoir d'export
chiffré : sa clé de données n'existe en clair nulle part, l'import refuse nommément.

## Résolution + accès (`access/`)

`resolve_api_key(provider) -> (api_key, is_platform)` : **l'ordre des barreaux est énoncé une seule fois, dans [`roles-and-resolution.md`](roles-and-resolution.md)** — membre, perso cross-org, équipe, org, tenant, plateforme. Ce document ne le redit plus : sa version omettait les étages équipe et tenant, et a menti jusqu'au 14/09/2026. Le connecteur **`bridge`** universel (ADR 0034) se résout par les **champs standard** (`resolve_credential_fields("bridge")` → `base_url`/`token`/`label`, cascade membre > groupe > org), raise actionnable si absent, **jamais de fallback SOPS serveur** — plus de `meta.base_url` (l'ex-`resolve_remote_credential` per-namespace retiré en B4).
`resolve_credential_fields(provider) -> dict` : credential **multi-champs byo_user** (ex. `silae` : client_id/client_secret/subscription_key) — lit le coffre + `unpack_secret`. **byo-only, pas de platform key ni quota** (le credential EST le grant). Pour les clients in-process s'instanciant avec plusieurs secrets.
⚠️ `resolve_mount_token(provider)` a été **retiré le 2026-09-09** (**ADR 0069**) : il rendait le token per-user d'un MCP fédéré `kind="mount"` (OAuth atlassian/folkmcp, ou la branche base64 `basic_auth`), que le proxy injectait en bearer. Plus aucun appelant, plus de fonction — ce qui suit ne le mentionne que pour qu'on cesse de le chercher.
`status_for` = miroir exact (modes user/org/platform/over_quota/forbidden) — boucle aussi sur les byo_user à `secret_fields` hors `KEY_PROVIDERS` (planity, silae : `user`/`forbidden`) — `planity` y reste après son passage en natif, c'est la FORME du credential qui l'y met, pas son `kind`. `granted_namespaces_for`/`require_namespace` = gate des namespaces grant-only (deny-by-default), source unique consommée par middleware + meta-tools + REST.

## Palier org

Tables `orgs`/`org_members`(index partiel `org_members_one_active`)/`org_entitlements` ; `org_store/` (`orgs.py` la fiche, `members.py` l'appartenance, `vault.py` les secrets). ⚠️ Le credential d'org se pose désormais **uniquement** par `PUT /api/orgs/{id}/secrets/{provider}` (`capabilities/orgs/secrets.py`) : les 12 meta-tools `oto_admin_*` de `tools/orgs.py` ont été retirés le 2026-06-13 (ADR 0009 barreau 2), le module n'existe plus. Entité = **user ET org, 2 niveaux** (perso prime sur org).

## Folds des secrets de session (cible : coffre unique)

- **LinkedIn / Crunchbase** : cookie chiffré dans `secret_enc`, UA dans `meta` ; `db.set/get/clear_linkedin_cookie`/`crunchbase_session` sur le coffre ; statut /api/me via `credential_status` (sans déchiffrer).
- **Google OAuth multi-compte** : `connector='google'`, `account=email` ; refresh_token chiffré, scopes/is_default/granted_at/client_id dans `meta`. Les fns db (`set/get/list/set_default/delete_google_oauth`) sur le coffre. **Le jeton d'accès n'est pas rangé dans le coffre** (hygiène du coffre, comme Microsoft, HubSpot, LinkedIn Ads) : chaque processus le tire par refresh au premier usage et le garde en mémoire jusqu'à une minute de son terme (`auth/google._JETONS`, clé = la ligne du compte résolu, quelle que soit son entité, son refresh_token et la borne ENVOYÉE au refresh — une reconnexion ou un changement de droits en tire un neuf). La borne = les scopes enregistrés que le code déclare encore (`_declared_scopes` : un scope retiré, comme l'ancien `chat.messages`, n'est jamais renvoyé) ; si Google la refuse (`invalid_scope`), un seul réessai borné à l'identité + au service appelé (`_service_bound`), jamais sans borne ; les `scopes` de `Credentials` suivent la même borne. Coût : un refresh de plus par processus et par heure et par compte servi ; deux refreshs simultanés rendent chacun un jeton valide (Google ne fait pas tourner le refresh_token), le dernier gardé l'emporte. Le jeton d'échange du consentement ne sert qu'à lire l'adresse : il n'est pas gardé, le premier usage en tire un borné aux scopes de la ligne. Le compte d'un appel se choisit par la résolution commune `access.resolve_credential` (`account`/`_account=` > épinglage projet > compte unique > défaut, oto-backend#1160). Les lignes posées avant ce changement portent encore `access_token`/`expires_at` dans `meta` : plus rien ne les lit ; `oto-mcp maintenance jetons-en-meta` retire la clé `access_token`, APRÈS le déploiement du code qui ne l'écrit plus (le code d'avant l'y réécrit à son prochain refresh).

## Connecteurs remote — bridges (ADR 0003, pilote : un bridge back-office client)

`kind="remote"` au registre = **aucun code ni credential client dans oto** : un bridge (service HTTP distant, ex. un bridge back-office client (repo privé)) détient le credential du système client ; oto-mcp = middleware générique `tools/remote.py` (tools `<ns>_describe` + `<ns>_call`, forward bearer M2M + `X-Oto-Sub` pour l'audit côté bridge). Le credential d'org = `secret` = token M2M + `meta.base_url` = endpoint (posé via `oto_admin_set_org_secret(..., base_url=…)`). Gating inchangé : grant-only + `require_namespace` au call-time. Contrat bridge (`/healthz`, `/describe`, `/call`) : ADR 0003 du meta-repo. ⚠️ Le flavor complémentaire envisagé pour les remotes **déjà-MCP** (« mount MCP-to-MCP », `otomata#16`) est **sans objet depuis le 2026-09-09** : la fédération MCP est retirée (**ADR 0069**). `kind="remote"`, lui, ne bouge pas — un service distant déjà-MCP se joint désormais par le connecteur `api` générique, ou s'écrit en connecteur natif.

## Projection instances (ADR 0038 B4)

Le coffre est relu comme un **listing d'instances possédées nommées** (une ligne
`(entity_type, entity_id, connector, account)` = une instance), en **lecture pure,
sans jamais déchiffrer** : capacité `connectors.instances.list` (MCP
`oto_instance(op="list")` (console ADR 0047), REST `GET /api/me/connector-instances`,
`capabilities/connectors/instances.py`). Agrège les 4 familles que la cascade
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

## L'instance, OBJET — `connector_instances` (lot L6, blueprint ADR 0053-D9)

Depuis le 2026-08-27, une instance de connecteur **existe comme objet**, dans une table
posée **à côté** du coffre : `connector_instances(id, connector, owner_type, owner_id,
account, label, config, visibility, parent_id, created_at, revoked_at)`. Arbitrage R1,
prononcé par Alexis : *« la table à côté »*.

**Pourquoi à côté et pas dans le coffre.** L'AAD lie le ciphertext aux **quatre colonnes
d'identité de SA ligne** (`credentials_store._aad`), pas à la table qui la porte : une
table posée à côté, la ligne du secret gardant ses quatre colonnes, donne l'identifiant
stable **sans un octet de rechiffrement**. Une fois le rechiffrement hors de l'équation,
ce qui décide est le fond : le modèle prévoit des instances **sans secret** — une
instance `api` qui ne fige qu'une `base_url` (0057), une sous-instance qui ne pose
qu'une *détermination* (« le compte de Jane », 0053-D9-3). Un objet qui n'est plus un
credential n'a rien à faire dans une table qui l'est.

**Le lien avec le coffre est le quadruplet, et c'est une FK LOGIQUE** —
`owner_type`/`owner_id`/`connector`/`account` **sont** `entity_type`/`entity_id`/
`connector`/`account`, à l'octet près. Pas de FK déclarée, délibérément : le coffre n'a
aucune clé de substitution (sa PK **est** ce quadruplet, donc rien à quoi un
`credential_id` pourrait pointer), et une vraie FK interdirait les instances sans secret
le jour où elles arrivent. Le sens du pointeur suit la même lecture : un `instance_id`
posé **sur** le coffre serait perdu à chaque `rename_account` (= `_upsert` d'une ligne
neuve + `_delete` de l'ancienne, et la liste de colonnes de l'INSERT ne le nommerait
pas), donc réparable seulement en éditant les primitives d'écriture du coffre.

**Une instance vivante par ligne de coffre**, tenu par la base : index UNIQUE **partiel**
sur le quadruplet (`WHERE revoked_at IS NULL`). Son **jumeau non partiel** n'est pas un
doublon : le backfill demande « existe-t-il une instance, **archivée comprise** ? » —
c'est ce qui l'empêche de *ressusciter* une instance retirée à la main entre deux boots.
⚠️ Ne pas les « harmoniser » : leurs deux lectures sont opposées (même partage des rôles
qu'entre `idx_grants_grantee` et `idx_grants_resource_grantee`).

### L'instance naît à la POSE (pièce 2, 2026-08-28)

Jusqu'au 27/08 l'instance naissait **au boot** — le prix assumé de ne toucher aucun chemin
d'écriture du coffre, et une fenêtre pendant laquelle une clé fraîche n'avait pas
d'identifiant. La pièce 2 ferme la fenêtre, et le fait **au fond**.

**Un seul point d'accroche, parce qu'il n'y en a qu'un à avoir.** Le relevé est net : il
existe **un seul `INSERT INTO connector_credentials`** et **un seul `DELETE`** dans tout
le dépôt — `credentials_store._upsert` et `._delete`. Toutes les surfaces déclaratives
(clé membre, clé d'org, clé d'équipe, clé plateforme, jeton d'API, session navigateur,
flux OAuth google/zoho/salesforce — et folk/atlassian jusqu'au 2026-09-09, ADR 0069)
y aboutissent par `set_credential` / `clear_credential`. Accrocher la naissance aux surfaces aurait demandé une dizaine de
crochets pour le même effet, avec une chance sur dix d'en oublier un.

| Geste sur le coffre | Effet sur l'instance |
|---|---|
| poser une clé (n'importe quelle surface) | **naissance**, dans la même transaction |
| roter le secret d'une clé existante | rien — c'est la même instance |
| retirer une clé | **archivage** (`revoked_at`), jamais un `DELETE` |
| reposer une clé retirée | une instance **NEUVE** (id neuf) ; l'archivée reste archivée |
| renommer un compte (`rename_account`) | l'instance **SUIT**, elle garde son id |
| supprimer une équipe · déconnecter tous les comptes Google · retirer une app d'éditeur | archivage de toutes les instances visées |
| accorder/retirer un accès plateforme, suspendre, marquer un défaut, rafraîchir un jeton | rien — ces gestes n'écrivent que du partage ou du `meta` |
| basculer un compte vers un autre annuaire (`migrate_sub`) | rien, **et c'est le point** (voir plus bas) |

**Pourquoi le renommage a son geste à lui.** `rename_account` rechiffre — l'`account`
entre dans l'AAD — donc il écrit une ligne neuve et supprime l'ancienne. Laissé aux seuls
crochets, il tuerait l'instance et en ferait naître une autre : soit exactement ce qu'un
ref composé fait déjà, et qu'on remplace. Le déplacement se fait donc **en premier**, dans
la même transaction ; après lui les deux crochets sont des non-événements. Le geste rend
un `RenameOutcome` et **ne tait jamais un archivage** : si une instance vivante occupait
déjà l'arrivée (un écart), l'arrivée gagne, le départ s'archive, et l'objet rendu dit
lequel au profit duquel.

**Une bascule de compte ne détache pas l'instance de sa ligne.** Migrer un sub vers
l'annuaire d'un tenant ne repointe **jamais** `connector_credentials.entity_id` par un
`UPDATE` nu (l'AAD : la ligne deviendrait indéchiffrable). Depuis #439, la clé
personnelle (`user`, `member`) est **rechiffrée en place** sous la nouvelle entité
(`rekey_personal_credentials`) et son instance la suit avec son id
(`connector_instances.move_instance_to_owner`) ; une collision ou une ligne illisible
reste en place, et son instance aussi. Repointer l'instance seule la **détacherait**
de sa ligne : un objet qui désigne une clé qui n'existe pas, strictement pire que rien.
Le garde-fou d'inventaire (`tests/test_migrate_sub_inventory.py`) portait la consigne
inverse jusqu'au 28/08 ; elle est corrigée sur place.

**Ce que la pièce 2 NE fait pas.** `config` reste **inerte** — la config publique vit
toujours dans `meta`, les `config_fields` packés vivent dans le ciphertext (les sortir
suppose de déchiffrer, c'est un lot à part). `label` reste vide, le nom affiché reste
dérivé. `visibility` et `parent_id` restent posés et inertes.

**Ce que le filet de boot fait, et ne fait pas.** `_init.init_db` continue de nommer les
lignes de coffre orphelines (idempotent, en lots, journalisé, rejoué à chaque boot) — mais
c'est désormais un **filet**, plus le chemin de naissance : après ce lot il ne nomme plus
rien (0 ligne, mesuré en preprod). Il laisse `label` et `config` vides. Aucune ligne du
coffre n'est touchée, aucun secret déchiffré. Un `entity_type` hors vocabulaire est compté
et journalisé, jamais inventé — le CHECK de la table emporterait sinon la transaction de
schéma entière, sur une base partagée avec la production. ⚠️ **À la POSE, le même cas est
un refus NOMMÉ** (`db.connector_instances.OwnerKindUnknown`) qui emporte la pose entière :
nommer fait désormais partie de poser, et une clé que rien ne désigne n'a pas à naître.

⚠️ **L'angle mort du filet, nommé plutôt que bouché.** Sa garde `NOT EXISTS` ne filtre pas
`revoked_at` : il **refuse** de nommer une ligne de coffre qui porte déjà une instance
archivée — c'est ce qui l'empêche de ressusciter une instance retirée à la main. Après la
pièce 2 ce cas ne naît plus que d'un geste manuel en base (archiver une instance en
laissant vivre sa clé). Ce n'est donc pas au filet de le rattraper, c'est à l'**invariant**
de le montrer.

### Qui VOIT une instance — R9, la visibilité dérivée

Arbitrage prononcé par Alexis le 27/08 : *« la visibilité est une propriété de
l'INSTANCE »*, dérivée de la chaîne — **découvrable par les scopes sous son
propriétaire, dans la même org, jamais cross-org**, avec surcharge explicite du
propriétaire. Le domicile de la dérivation est
`oto_mcp/connectors/instance_visibility.py` ; `GET /api/me/connector-instances` et
`oto_instance op=list` servent le résultat en `visible_to`.

**Ce que ça n'est PAS, et c'est la moitié du lot.** `visible_to` ne filtre rien,
n'élargit aucune liste, ne gate aucun appel. Un non-membre continue de voir *aucune
clé configurée* — la divulgation (« il existe un accès à demander, et chez qui ») reste
une question **produit**, que R9 range dans un réglage d'org opt-in, plus tard. Ce qui
est livré est **descriptif** : la même liste qu'avant, où chaque instance dit qui la
voit.

**Pourquoi la question est dérivable.** 0053-D2 a retiré l'enjeu de protection —
masquer ne protège de rien, tout se refuse à l'appel. Ce qui reste est de l'ergonomie,
et sa réponse honnête est *qui peut la résoudre la voit*. La résolution est déjà écrite
une fois pour toutes dans `access.cascade.walk_cascade` ; la dérivation l'**inverse**.

| Palier | Audience | Gate |
|---|---|---|
| membre (et le résidu `user`) | `user:<sub>` — la personne, jamais le couple `(org, sub)` | — |
| équipe | `group:<id>` | `org_shareable` : sinon **personne** (la clé existe, la cascade ne la lit jamais) |
| org | `org:<id>` | idem |
| plateforme | les bénéficiaires | `auth_modes ∋ platform` : sinon **personne** |
| *tous* | + `share_side` (prêts nominatifs, ADR 0044) — une **extension**, jamais une allowlist ; peut viser hors de l'org | — |

**Prêter une instance à une ÉQUIPE** (`oto_instance op=lend to_group=<id>`, `group:<id>` dans
`share_side`) : `group_store.lent_instances` verse le prêt au palier équipe — listé avec
ses clés (`has_group_secret`, `list_group_secrets`, `group_secret_map`, identités) —, mais
il ne sert ses membres **que nommé** (`_account=`, ou `_instance=` pour un connecteur
mono-compte), jamais choisi d'office (`docs/partage-de-compte.md`). La règle se décide dans
la sonde équipe de la résolution (`access.resolve._group_fetch` →
`access.prets.PretsDuParcours`), pas dans le walker, qui n'est pas recopié : sans nom, le
compte de l'équipe elle-même sert, sinon le palier suivant (org, tenant) ; si seuls des
prêts auraient servi, refus `account_required` qui les nomme avec leur prêteur — jamais la
clé plateforme à leur place. Le prêt servi est la ligne du PRÊTEUR (trace du journal, écho
`lent_by`/`lent_via_group`, rotation d'un jeton). Il reste au prêteur : même org que
l'équipe seulement (un prêt à une équipe ne traverse pas d'org), ignoré dès que le prêteur
quitte l'org, se met en pause, suspend l'instance, ou que l'org interdit la clé perso pour
ce connecteur (#1239, refusé aussi à la pose) ; un nom de compte que l'équipe détient déjà
masque le prêt ; l'équipe ne le renomme pas et n'en fait pas son défaut
(`connectors.identities._refuse_lent`). Un prêt à une PERSONNE (`user:<sub>`) s'épingle
(`_instance=`) et cesse de même quand son prêteur quitte l'org où il est né (refus
`lender_left_org`, `access.guard_instance_access`) — rien n'est détaché. La garde des
niveaux d'accès juge un prêt désigné sous l'org du prêteur et sous l'équipe emprunteuse
(`connectors/access_level_gate._loan_contexts`).

**Prêter une instance à TOUTE L'ORG** (`oto_instance op=lend to_org=true`, REST
`POST /api/me/connectors/{connector}/lend` `{"to_org": true}`, `org:<id>` de l'org COURANTE
dans `share_side` ; la réponse dit `lent_to_org`) : même règle qu'un prêt à une équipe,
décidée dans la sonde org de la résolution (`access.resolve._org_fetch` →
`PretsDuParcours.au_palier_org`), pour les membres de cette org seulement, la ligne du
prêteur exceptée — sans nom, le compte de l'org elle-même sert ; nommé, le prêt sert et
l'écho dit `lent_by`/`lent_via_org`. Mêmes conditions de vie (`group_store.live_loans` :
même org, prêteur membre et pas en pause, instance non suspendue, clé perso non
interdite) ; même refus à la pose d'un porteur que rien ne servirait
(`org_loan_not_served`, Microsoft servi par son résolveur). Listé aux membres
(`via=shared_with_me`) ; `visible_to` du prêteur contient `org:<id>`.

**Un compte Microsoft de l'org ou d'une équipe** (lot 5 de `docs/partage-de-compte.md`) : le
porteur `microsoft` est org-partageable (`byo_org`), un admin d'org ou le chef de l'équipe
active le connecte au nom de l'une ou de l'autre (`scope=org|group` du flux), la ligne vit à
l'entité `org`/`group` sous `microsoft` (pas de clé collée : `org_secret_meta` refuse un
OAuth). Les paliers équipe et org de la résolution la servent aux membres sans nom, et la
listent dans leurs instances (`visible_to` = l'équipe ou l'org). Ces paliers n'y lisent que
les lignes de l'équipe et de l'org elles-mêmes : les prêts Microsoft restent jugés par son
résolveur seul (`access.prets.RESOLVEUR_PROPRE`, `PretsDuParcours.cle_de_l_equipe`).

**Le journal des prêts** (`connector_loan_events`, révision `0058_journal_des_prets`,
`db/loan_events`) : chaque pose et chaque retrait EFFECTIF d'une cible (`user:`, `group:`,
`org:`) y laisse une ligne — qui (`actor_sub`), quel compte (la ligne du coffre du
prêteur : `entity_type`, `entity_id`, `connector`, `account`), vers qui (`target`), quand
(`at`), quel geste (`lend`/`revoke`) —, écrite dans la transaction du `share_side`
(`credentials_store.set_instance_sharing(journal=…)`). Append-only, jamais de secret. Un
geste répété qui ne change rien n'y entre pas. ⚠️ **Aucune surface ne le lit encore** :
la lecture par le propriétaire (`db.loan_events.pour_la_ligne`) reste à exposer.

Le palier plateforme est le seul dont l'audience n'est pas structurelle. Trois issues,
dans l'ordre où la résolution les prend : la **chaîne accorde** (0053, L5) ⟹ les
bénéficiaires des arêtes vivantes ; la **chaîne refuse** (des arêtes existent, toutes
révoquées) ⟹ personne, **sans repli** (c'est ce qui rend une révocation vraie) ; la
**chaîne est muette** ⟹ l'ancien chemin, à l'identique (`closed` ⟹ l'allowlist ;
`open` ⟹ l'allowlist si elle existe, sinon `platform`, le mot de l'audience non bornée).

**La surcharge** vit dans `connector_instances.visibility` : `inherited` (défaut — la
dérivation décide), `hidden` (le propriétaire seul), `org` (l'org du propriétaire en
plus). ⚠️ **Rien ne l'écrit aujourd'hui** — les deux autres branches sont écrites et
testées, pas servies ; le geste qui les pose est un lot produit, et un test fige
l'absence d'écrivain. ⚠️ `hidden` est un cran d'**ergonomie**, pas de sécurité : celui
qui résout continue de résoudre, il cesse seulement de la voir listée.

⚠️ **Le risque du lot, et son garde-fou.** Inverser un walker, c'est risquer d'en
écrire une deuxième copie — le défaut que `keyStack.ts` porte déjà côté dashboard, et
qui ne casse pas : il **ment**. Deux réponses, toutes deux mécaniques : (1) aucune règle
n'est recopiée — les gates sont lus à leur source (le registre, les colonnes de partage
du coffre, `grants_chain`), et un registre consulté deux fois n'est pas une duplication ;
(2) `tests/test_instance_visibility.py` **confronte** l'audience dérivée au verdict réel
de `walk_cascade` sur un vrai PostgreSQL, pour deux appelants aux droits différents. Si
les deux divergent, ce test rougit.

**Coût** : deux requêtes en lot pour toute la liste (les identifiants et le partage),
jamais deux par instance ; plus, pour les seules clés plateforme d'un connecteur
basculé, la lecture de leurs arêtes. Fail-open loggé et **séparé** de celui de `id` :
le partage peut tomber sans emporter l'identifiant.

### Nettoyer les orphelines d'avant la pièce 2 — une commande, pas un boot

Entre la pièce 1 (le boot NOMMAIT chaque ligne de coffre) et la pièce 2 (la pose nomme,
le retrait archive), **chaque suppression de credential fabriquait une orpheline** : la
ligne partait, l'instance restait vivante. Mesuré sur la base servie le 2026-08-28,
avant déploiement : **2 orphelines** sur 139 instances vivantes (`member … slack` et
`org … aiark`) — exactement les retraits qui contournaient l'entonnoir. La pièce 2 ferme
la source ; elle ne nettoie pas le passé, parce que nettoyer n'est pas le travail d'un
boot.

```
# sur la box, après déploiement — dry-run par défaut : liste, n'écrit rien
cd /opt/oto-mcp && ./.venv/bin/python -m scripts.archive_orphan_instances
#   --apply pour écrire
```

Le script **archive** (`revoked_at`, motif `vault_row_missing`), jamais un DELETE : si un
binding ou une arête a nommé l'orpheline, « elle a été retirée » et « elle n'a jamais
existé » ne sont pas le même verdict. **Idempotent** — son prédicat est « vivante ET sans
ligne de coffre », donc un second passage rend 0, et il ne peut pas mordre sur une
instance saine.

⚠️ **Hors du boot, délibérément** (ADR 0065). Le filet de démarrage ne sait qu'insérer ;
lui faire archiver au boot ferait d'un ordonnanceur de maintenance un écrivain de masse
sur une base **partagée avec la production**.

**Le motif (`revoked_reason`), et pourquoi il vaut une colonne.** Sans lui, un archivage
est muet six mois plus tard : impossible de distinguer « l'utilisateur a retiré sa clé »
d'une réparation de maintenance. Trois valeurs, posées par ce dépôt seul :
`credential_removed` (le cas normal), `renamed_onto_existing` (un renommage vers une
instance déjà vivante), `vault_row_missing` (ce script). Nullable et sans `CHECK` — le
vocabulaire est fermé par le code qui écrit, sinon le prochain motif est une migration.

### L'invariant, et la requête qui le dit

> Chaque ligne de coffre a **exactement une** instance vivante, et chaque instance vivante
> a **sa** ligne de coffre.

Les deux sens comptent : une instance orpheline (« je désigne une clé qui n'existe pas »)
est au moins aussi grave qu'une clé sans nom, et un binding ou une arête peuvent la
nommer. La requête vit dans `tests/test_connector_instances_birth_live.py`
(`INVARIANT_SQL`), où elle est exercée sur les deux écarts — et c'est la même qu'on joue
en preprod après un merge :

```sql
SELECT COALESCE(c.entity_type, i.owner_type) AS owner_type,
       COALESCE(c.entity_id,   i.owner_id)   AS owner_id,
       COALESCE(c.connector,   i.connector)  AS connector,
       COALESCE(c.account,     i.account)    AS account,
       CASE WHEN i.id IS NULL THEN 'coffre sans instance'
            ELSE 'instance sans ligne de coffre' END AS ecart
  FROM connector_credentials c
  FULL OUTER JOIN (SELECT * FROM connector_instances WHERE revoked_at IS NULL) i
    ON  i.owner_type = c.entity_type AND i.owner_id = c.entity_id
    AND i.connector  = c.connector   AND i.account  = c.account
 WHERE c.entity_type IS NULL OR i.id IS NULL
 ORDER BY 1, 2, 3, 4;
```

Zéro ligne = l'invariant tient. Les instances **archivées** sont hors périmètre des deux
côtés : c'est leur raison d'être (une consommation ou un partage passés doivent rester
relisibles).

**Ce que rien ne fait encore.** Ni la cascade (`access.walk_cascade`), ni la résolution
(`access.resolve_credential`) ne lisent cette table — c'est le lot L7. Le coffre, lui, y
**écrit** depuis la pièce 2, et n'y lit jamais : l'intention est gardée à deux grains par
`tests/test_connector_instances_l6.py` — une allowlist de **six** fichiers, et, à
l'intérieur du coffre, un relevé AST par FONCTION qui n'admet que les cinq primitives
d'écriture. Un lecteur de credential qui se mettrait à lire les instances ferait dépendre
la désignation d'une clé d'autre chose que du quadruplet ; c'est le lot L7, avec sa revue.

### Les applications de Custom API (18/11/2026)

**Le besoin.** Un connecteur personnalisé ÉTAIT une ligne du coffre : son adresse, son
mode d'authentification (champs chiffrés) et ses actions (`config` de son instance)
vivaient sur la clé. Une org qui veut que chacun se serve de l'outil avec SES droits chez
le fournisseur — une API interne qui applique elle-même les rôles de ses utilisateurs, un
jeton par personne ou par équipe — se retrouvait avec autant de connecteurs que de clés,
sans actions hors de la carte d'org, et ne pouvait pas faire exister un outil sans clé
d'org (une clé d'org donne la même identité à tout le monde : c'est précisément ce que
l'org ne veut pas).

**Le modèle.** Deux objets, comme pour un connecteur natif où la définition est le code :

- l'**application** (`api_apps`, `db/schema/connectors.API_APPS`) : par org et par nom de
  compte, sa `connection` (`base_url`, `auth_mode`, `header_name`, `query_param`,
  `doc_path`, `token_url`, `scope`, `session`) et sa `definition` (même format que celle
  d'une instance, `custom_ops_definition`). Jamais un secret. Créée, changée, retirée par
  un admin de l'org seul (`capabilities/connectors/api_apps.py`) ; archivée, jamais
  supprimée ; le nom se réutilise après retrait ;
- ses **clés** : des lignes ORDINAIRES du coffre (membre, équipe, org), sous le compte de
  l'application, posées par les trois surfaces de toujours. La cascade ne change pas :
  perso > équipe ACTIVE > org.

**La liaison se fait à la POSE, et seulement là** (`connectors/api_apps.completer_la_pose`,
appelée par `me.credential.set`, `group.secret.set`, `org.secret.set`) : une clé posée sous
le nom d'une application VIVANTE de son org reçoit la connexion de l'application (un champ
de connexion envoyé qui diffère : `api_app_field_fixed`, jamais écrasé en silence) et
`meta.api_app = <id>`. ⚠️ **Jamais par la seule rencontre des noms** : une clé posée AVANT
l'application, du même nom, reste une carte ordinaire (et la cascade la sert d'abord à son
titulaire, comme avant) ; l'`id`, pas le nom, désigne l'application — recréer une
application du même nom après un retrait n'adopte pas les clés de l'ancienne.

**À l'appel** (`servie`, branchée à `tools/api_ops._resolved` et `tools/api._resolve`) :
la ligne résolue garde ses secrets ; la connexion et la définition sont celles de
l'application. ⚠️ **Le secret ne suit jamais un changement d'hôte** : la clé garde dans le
coffre l'adresse et le mode de sa pose ; si l'application a changé d'hôte (ou de mode)
depuis, la clé REFUSE (`api_app_key_stale`) au lieu de partir vers un hôte pour lequel
personne ne l'a posée, jusqu'à ce que son titulaire la repose. Application retirée :
`api_app_removed`. La définition d'une application ne s'écrit que par un admin de l'org,
quel que soit le palier de la clé qui la résout (`api_act op=set_definition`,
`_require_admin`) ; les routes de définition d'une CARTE refusent une clé liée
(`api_app_owns_definition`).

**Compatibilité avec la base partagée.** Une clé liée porte TOUS ses champs dans le coffre
(adresse et mode compris, recopiés à la pose) : un code qui ignore les applications la sert
comme une carte ordinaire, sans ses actions. Aucune donnée n'est reprise d'office : une
carte d'org existante devient une application par un geste d'admin (`adopt_org_key`),
qui reprend sa connexion et sa définition et lie sa clé. La définition laissée sur son
instance n'est plus lue pour elle.

**Ce que le modèle ne fait pas.** Pas de liste d'accès propre à l'application : qui y
accède est qui détient une clé qui se résout pour lui (les règles de comptes, #1499, la
porteront). ⚠️ La clé d'ÉQUIPE ne sert que les membres dont c'est l'équipe ACTIVE
(`current_group`) : quelqu'un dans plusieurs équipes pose `_group=` ou change d'équipe
active, ou prend une clé personnelle. ⚠️ Un agent hébergé appelle avec la clé de SON
propriétaire : un agent monté sur une application à droits étendus, partagé à d'autres,
leur rend ce que son propriétaire voit. Pas d'import GraphQL (l'import lit OpenAPI ; une
API GraphQL se décrit par une action `POST /graphql`).

### Le mode `oauth2_user` des applications de Custom API : chaque membre se connecte (23/11/2026)

Une API interne qui applique ses droits PAR PERSONNE (un CRM où chaque commercial ne voit
que ses comptes) n'était joignable qu'avec UNE identité : une clé, un couple basic, un
client `client_credentials`. Le mode `oauth2_user` donne à chaque membre la sienne
(`auth/api_oauth.py`) :

- **l'application** porte où se connecter, jamais un secret : `authorize_url`,
  `token_url`, `oauth_client_id` (un client PUBLIC, PKCE S256, sans secret — type
  natif / SPA chez le serveur d'autorisation), `scope`, `resource` (RFC 8707, envoyé à
  l'autorisation, à l'échange et au renouvellement : sans lui, un serveur OpenID qui émet
  ses jetons par ressource rend un jeton opaque que l'API refuse). `offline_access` dans
  les scopes fait envoyer `prompt=consent` (sans lui, pas de refresh token) ;
- **« Connecter »** = `POST /api/orgs/{id}/connectors/api/apps/{account}/connect`
  (membre de l'org ; refusé si l'org interdit la clé perso du Custom API). Le retour suit
  la règle commune (`auth/flow.py`) : callback `/api/custom-api/oauth/callback`, carte
  `api`, complétion par le front avec le jeton de la personne ;
- **la clé du membre** = une ligne ordinaire du coffre, palier membre, sous le nom de
  compte de l'application, liée par `meta.api_app` (`completer_la_pose`, comme une clé
  posée à la main) ; son seul secret est `refresh_token`, qui ne se pose qu'au palier
  membre : une clé d'équipe ou d'org qui le porte est refusée
  (`refresh_token_member_only`) ;
- **à l'appel**, `tools/api.client_for` demande un jeton d'accès à `access_token_for` et
  appelle en `bearer` : oto-core ne connaît pas ce mode (`HOST_AUTH_MODES`, hors du
  tripwire `test_api_auth_modes.py`). Renouvellement sous verrou consultatif de la LIGNE,
  rotation écrite dans la même transaction, grant mort revérifié contre la ligne puis
  MARQUÉ (`health_ko`), jamais purgé ; le jeton d'accès ne va jamais en base.

Pas de clé d'équipe ni d'org dans ce mode côté produit : l'intérêt est l'identité de la
personne. Un agent hébergé appelle sous l'identité de son propriétaire, donc avec SA clé.

### Un serveur MCP comme Custom API : `protocol: mcp` (23/11/2026)

Une carte (ou une application) de Custom API peut désigner un SERVEUR MCP distant
(Streamable HTTP) au lieu d'une API REST : champ `protocol` = `mcp`, `base_url` =
l'adresse du serveur (`connectors/api_mcp.py`). Ce n'est **pas** la fédération retirée
(ADR 0069) : rien n'est monté au catalogue, aucun outil distant ne devient un outil
d'oto. Deux verbes l'atteignent, par la carte :

- `api(op="mcp_tools")` — les outils distants (nom, description, schéma d'entrée) ;
- `api_act(op="call_mcp", operation=<outil>, args={…})` — un appel, effet `detruire`
  (les annotations d'un serveur tiers ne sont que sa parole).

L'auth est celle de la carte (tout mode sauf `session` et `visitor`), connexion par
membre `oauth2_user` comprise — un jeton refusé (401) est oublié et l'appel rejoué une
fois. Une session courte par verbe (`initialize`, `notifications/initialized`, la
requête, `DELETE`), réponse JSON ou SSE, corps borné, **aucune redirection suivie**,
`base_url` et `token_url` sous la garde d'egress. La définition porte une liste blanche
`mcp_tools` (noms ; vide = tous) que l'admin pose : un outil hors liste est refusé avant
tout envoi, et n'apparaît pas à la liste. Ce que le serveur distant écrit (descriptions,
texte, `structuredContent` en JSON indenté, messages d'erreur) arrive à l'agent dans un
bloc nommé de données tierces (`texte_tiers.cloture`).

### Un serveur MCP en `oauth2_user` se connecte comme Claude : sans clé ni client saisis (24/11/2026)

Pour une application `protocol: mcp` en `oauth2_user`, l'admin ne donne que l'adresse du
serveur ; le premier « Connecter » d'un membre découvre le reste (`auth/mcp_discovery.py`,
spec d'autorisation MCP) et l'ÉCRIT sur l'application (seuls les champs vides) :

1. un `initialize` sans jeton → 401 + `WWW-Authenticate: Bearer resource_metadata=…`
   (sinon les chemins connus de RFC 9728) ; la `resource` annoncée DOIT être l'adresse
   de l'app, sinon refus (un serveur ne peut pas réclamer les jetons d'un autre) ;
2. les métadonnées du serveur d'autorisation (RFC 8414, puis OpenID) DOIVENT nommer
   l'émetteur cherché ; PKCE S256 exigé ;
3. l'identité du client : notre **Client ID Metadata Document**
   (`GET /api/custom-api/oauth/client.json`, servi sans authentification, client
   public, notre callback) si le serveur l'accepte ; sinon une inscription dynamique
   (RFC 7591, client public) ; sinon refus nommé `mcp_discovery_failed` — l'admin crée
   un client public et pose son ID.

Les champs remplis par la découverte sont marqués dans la connexion (`discovered`,
écrit par la découverte seule ; un champ que l'admin change ou vide en sort). Un
`token_url` ainsi annoncé est revérifié par la garde d'egress **sans exception
déclarée** à chaque échange de code et à chaque renouvellement ; celui que l'admin a
saisi garde la politique de la plateforme.

Changer l'adresse d'une telle app vers une autre origine efface ce qui avait été
découvert (sauf valeurs neuves envoyées par l'admin) : rien n'est reporté d'un serveur à
l'autre. Le membre qui déclenche la découverte écrit donc la connexion de l'app
(`updated_by` = lui).
Tout membre peut ainsi déclencher la découverte : elle ne lit que des métadonnées
publiques du serveur choisi par l'admin et n'écrit que ce qu'elle a trouvé — admis tel
quel.

### Un seul service d'autorisation, une seule ressource, `iss` vérifié (25/11/2026)

L'admin d'une org est traité comme non fiable par la garde d'egress ; il ne doit pas
davantage pouvoir faire partir le code, le verifier ou le jeton d'un membre vers un tiers
(`connectors/api_apps.refus_d_origine`, relu à l'enregistrement de l'app, à la
découverte et à chacune des trois demandes — autorisation, échange, renouvellement) :

- `authorize_url` et `token_url` sont sur UNE origine (schéma, hôte, port) : celle de
  l'émetteur découvert (`issuer`, écrit par la découverte seule) quand il est connu,
  sinon celle que l'admin a déclarée. Un mélange est refusé (`invalid_app_connection` à
  l'écriture, `api_app_origin_mixed` à l'usage, `mcp_discovery_failed` à la
  découverte) ;
- une `resource` déclarée désigne l'API elle-même : en mode MCP l'adresse canonique du
  serveur (RFC 8707, `ressource_canonique`), imposée aux trois demandes ; en http une URI
  de l'origine de `base_url`. Une autre saisie est refusée ;
- au retour, le callback scelle le code avec le paramètre `iss` (RFC 9207) : exigé et
  égal à l'émetteur quand le serveur annonce `authorization_response_iss_parameter_supported`,
  refusé dans tous les cas s'il est présent et différent (`api_oauth_issuer_mismatch`) ;
- « Connecter » rend `sign_in_host` et `api_host`, pour que l'écran les montre avant
  le départ ; le document client (CIMD) nomme `oto`.

Toutes les requêtes de ce chemin (découverte, inscription, échange, renouvellement, et
les quatre requêtes MCP) partent sur une session épinglée (`egress_transport`). Le
renouvellement fait sa requête HORS transaction ; seule l'écriture du jeton tourné prend
le verrou de la ligne, et n'écrase pas un jeton changé entretemps. Limite assumée : le
service d'autorisation et l'API peuvent être sur des domaines différents (un fournisseur
d'identité devant une API interne), et un service qui ignore `resource` délivre un jeton
valable pour toute API qui l'accepte ; la confiance dans la configuration d'une app
repose sur l'admin de l'org, et sur l'attention du membre aux deux hôtes montrés avant
la connexion (`SECURITY.md` §10). ⚠️ Limite : le mode
`oauth2` (client credentials) d'une carte MCP obtient son jeton par oto-core, hors de
ce transport.

### Le renommage `http` → `api` (12/11/2026)

Décision du propriétaire du produit : le connecteur s'appelle `api`, ses outils `api`, `api_act`, `api_get`,
`api_post`, `api_doc` — mêmes ops, mêmes arguments. **Les surfaces se doublent** (`docs/alias-deprecies.md`) :
les anciens noms d'outils `http*` restent servis en **alias dépréciés** (`deprecations.OUTILS_CONNECTEUR_API`),
traduits au bord du protocole et par `oto_call`, chaque réponse portant l'avis en tête ; les routes REST
deviennent `/api/orgs/{id}/connectors/api/{operations,definition}`, et les anciennes `…/connectors/http/…`
répondent **308** vers elles (`deprecations.alias_rest_derives`). Retrait : quand les consommateurs déclarés ont
suivi et que l'usage compté (`tool_calls.args.deprecated_name`) est nul, au plus tôt le 31/01/2027. Le NOM du
connecteur, lui, n'a pas d'alias : `http` refuse en nommant `api` (`providers.CONNECTEURS_RENOMMES`, lu par les
refus « connecteur inconnu » ; source de fichier `kind="http"` → `kind="api"`). Les données suivent en deux temps : le **coffre** par une étape de démarrage
(`api_operations.copier_depuis_http`, COPIE re-chiffrée — l'AAD porte le nom du connecteur — qui laisse les
lignes `http` en place, garde le partage de la ligne (`share_*`) et recopie la définition de chaque instance) ;
le reste par la révision `20261112_1200_connecteur_api`, à jouer APRÈS la copie — après le tag chez nous ; sur
une cible, qui migre avant de démarrer, la porte joue la copie d'abord (`oto-mcp maintenance copies-du-coffre`,
`instance-cible.md` § Les migrations) : sélections, activations,
réglages, kits, listes d'outils, et ce qui DÉSIGNE une instance `http` — règles d'accès des comptes
(`connector_account_rules`), arêtes `grants`, liens de projet (`target_ref`, `instance_ref`, `identity_ref`) —
repointé vers la copie `api` du même propriétaire et du même compte. Pourquoi la révision et pas le démarrage :
l'id de la copie s'y lit en base, et le démarrage du code neuf précède le tag sur une base partagée avec la
prod, qui lirait encore ces désignations. Même règle de conflit partout (même sens : le doublon part ; sens
différent : la révision s'arrête et n'écrit rien). Les lignes `http` du coffre partent plus tard, par
`oto-mcp maintenance orphelins` ; d'ici là, le constat (`op=connecteurs_orphelins`) les marque `renomme_en: "api"`
et les compte à part (`total_renommes`), hors de `total`.

### La `config` d'une instance — premier écrivain : les opérations d'un « Custom API » (17/10/2026)

Le connecteur `api` (nommé `http` jusqu'au 12/11/2026) se présente comme **« Custom API »** (catégorie `Custom`) : c'est la
carte qu'on cherche quand l'outil d'un client (une paie, un ERP) n'a pas de connecteur.
Une org y **décrit en données** les opérations qu'elle appelle (nom, description, méthode,
gabarit de chemin, schéma d'entrée — format de `oto_mcp/custom_ops.py`, sous-ensemble à plat
de `connector.schema.json` d'oto-core), et l'agent les appelle par leur nom (`api_act(op="call")`).
La liste vit dans **`connector_instances.config["operations"]`** de l'instance qui nomme la
ligne du coffre — désignée par le quadruplet, jamais autrement
(`connectors/api_operations.py`, seul module admis à cette clé, cf. l'allowlist de
`tests/test_connector_instances_l6.py`). Conséquences assumées : une **rotation** de la clé
garde la liste (l'instance survit à l'`_upsert`), un **renommage** de compte la fait
suivre (`move_instance_to_account`), un **retrait** de la clé l'archive avec l'instance.
Écriture = les **clés données** de `config` remplacées (`merge_instance_config`),
jamais le document entier. Droits : ceux de la pose de la clé (`ORG_ADMIN_OF` pour un compte d'org, chef
d'équipe pour un compte d'équipe) ; la lecture est ouverte aux membres. Faces : REST
`/api/orgs/{id}/connectors/api/operations` (+ `/import`, conversion OpenAPI sans
écriture), MCP en paire (`tests/test_convention_act.py`) : `api` lit — `op=list` (les connecteurs
personnalisés de l'org, ou la définition d'un compte) et `op=setup_link` (le lien de la carte pré-remplie **sans** secret, la clé se saisit
dans le formulaire, jamais dans une conversation) ; `api_act` agit — `op=call` (une
action par son nom ; même un GET : le client ne peut pas savoir ce qu'une action décrite
fait chez le tiers), `op=request` (appel brut), `op=import` (conversion OpenAPI, sans
écriture mais préparant une écriture) et `op=set_definition`. Les outils d'avant la paire
(`api_get`/`api_post`/`api_doc`) restent servis ; `api` reste dans `A_MIGRER` tant
qu'ils n'ont pas rejoint la paire.

**`class: read` par action** : une action peut déclarer `class: read` (le mot d'oto-core, `class` d'une
fonction décrite), seule valeur admise, sur un GET ou un POST seulement — une recherche envoyée en corps
JSON, un point d'appel RPC. Posée par qui administre la carte (`set_definition`, mêmes droits que ci-dessus),
elle change deux choses pour **cette action seule** : son effet (`tools/effets` la juge `lire` au lieu de
`envoyer`) et le mode `session`, qui la laisse passer au lieu de la refuser (`api_session_read_only`). Sans
elle, une action se juge sur son verbe ; un appel brut (`op=request`) ne la porte jamais. `write` et
`sensitive` sont refusés : ils ne diraient rien de plus que le verbe, ou moins.

**La réponse d'une action** (`oto_mcp/custom_ops_output.py`) : deux clés facultatives, les mots et le code d'oto-core
(`oto.connectors.serve`, jamais recopié). `output` (`items`, `strip`, `projection` ; doc « sortie » de la lib) dit
ce que l'agent reçoit : la liste à déballer, les clés retirées par chemin à points, les clés de premier niveau
gardées ; une réponse sans la liste `items` déclarée est refusée (`api_output_items_missing`), jamais lue vide.
`refusals` (doc « signal de refus » de la lib) dit une erreur que le service écrit dans le **corps d'un succès**
(`{"hasError": true, "errorCode": …}` sous un 200) : `{code, when: 200, body: {path, equals | contains}, message,
retryable?}`. Jugée sur le corps brut, avant `output`, la première entrée qui tient est servie en refus nommé
(`code`, `retryable`), suivie d'un extrait de la réponse, clôturé comme le corps d'une erreur du tiers — jamais
un succès. `when` vaut 200 seulement (un statut d'erreur arrive déjà à l'agent avec son corps, et le client ne
rend pas le 2xx exact d'un succès) ; `body` est exigé ; `marks_account` n'est pas pris. Une action qui ne déclare
ni l'une ni l'autre rend exactement ce qu'elle rendait ; `op=request` ne les porte jamais.

**Le même lien pour tout le catalogue** : `oto_connector(op="setup_link", name=…)`
(`capabilities/connectors/setup_link.py`) rend la carte d'UN connecteur
(`links.connector_setup_link`, page connecteurs du produit de l'appelant), avec les
champs que le formulaire demandera (noms, jamais de valeur), la méthode (`secret`,
`oauth`, `hosted`…), et où trouver la clé (sections `prerequisite`/`setup` de la doc
du connecteur). Le lien ne porte **ni secret ni texte choisi par le modèle** — une
phrase d'agent affichée sur une page de confiance à côté d'un champ de clé serait un
hameçon. `api` est renvoyé vers son propre `setup_link` (pré-remplissage). Savoir si la
personne a fini : `op=list name=…` (`credential`), pas un état de plus.

**Configuré une fois, servi à tous** : la `config` porte aussi la **définition** du
connecteur personnalisé (`oto_mcp/custom_ops_definition.py`) — `label`, `description`,
`logo_domain`, `headers` constants NON secrets (un en-tête qui authentifie ou route est
refusé, celui de la carte compris), `variables` (valeurs non secrètes qui remplissent un
argument d'action du même nom). Les en-têtes constants passent par la session du client
oto-core après sa préparation (`with_constant_headers`, accroché à `_ready` — suite
attendue : un paramètre `headers=` côté oto-core). Le nom affiché de la carte
(`label`) vit en clair dans `meta` (`in_meta`, 17/10/2026) : la liste des connecteurs
personnalisés de l'org (`connectors/api_operations.org_connectors`), le refus
`oto_connector(name=…)` (qui rend le connecteur au lieu de « inconnu »), le contexte
de l'agent (une ligne par connecteur) et le roster `/api/me/connector-instances`
(`custom_api`) le lisent sans déchiffrer. Export/pose : `GET|PUT
/api/orgs/{id}/connectors/api/definition` — l'export ne porte aucun champ `secret`.

**Rapport à `otomata-tech/oto-pkg#60`** (le connecteur privé décrit en données, repris
d'oto-backend#808 pour oto 2). Le Custom API est la forme d'**oto 1** de ce besoin, livrée
ici sans attendre oto 2 ; il ne fixe rien pour oto 2. Point par point :

| #60 demande | Ici | |
| --- | --- | --- |
| le format au grain de l'opération, textes compris (nom, description, verbe, chemin, paramètres typés) | `custom_ops.py` — sous-ensemble à plat de `function` (`name`, `description`, `input`, `call.method`, `call.path`) | ✓ |
| la forme de la sortie (`function.output.schema`, déclarable côté `connectors`) | absente : la réponse du tiers est rendue telle quelle | ✗ — à ajouter avec le même nom de clé, `output`, si oto 1 en a besoin |
| un verbe générique : lister les méthodes d'une instance, en appeler une par son nom | `api(op="list")`, `api_act(op="call", operation=…)` — deux noms d'outils fixes, la liste vient de l'instance | ✓ |
| la garde de destination dès le départ | `egress.check_url` à chaque appel (base, serveur de jeton, document importé), aucune redirection suivie | ✓ |
| le jeton de rafraîchissement | absent (`oauth2` = client credentials seulement) | ✗ |
| le repli hébergé chez le tiers | hors périmètre | ✗ |

Le jour où oto 2 sert les connecteurs privés, une définition d'ici s'y convertit champ à champ
(`GET …/http/definition`, sans secret) : ses champs sont ceux de `function`, à plat.

**La clé d'un membre** (13/11/2026) : la carte `api` déclare `byo_user` en plus de `byo_org`. Un membre
non-admin pose SA clé par la route du membre (`POST /api/me/credentials/api`) ; elle est rangée dans son
org (`member`, `{org}:{sub}`), et le walker la préfère à celle de l'équipe puis de l'org — qui n'en a pas
garde la clé de l'org. Les routes de la définition et des opérations prennent `scope` : `org` (défaut,
droits ci-dessus) ou `member` (la clé de l'APPELANT dans cette org, lue et écrite par lui seul).
La ligne membre se déduit de l'identité de l'appelant, jamais d'un argument : aucune route n'atteint
la clé d'un autre membre ; l'autorisation se branche sur `scope` (`BY_OP`). Le jar du mode `session`
vit dans la ligne résolue, donc un par personne. Banc : `tests/test_api_cle_membre_live.py`.

**Plusieurs clés perso** (13/11/2026) : chaque compte de la carte est une API distincte, donc l'axe
`_account` est annoncé **statiquement** sur les outils `api*` (`account_axis_static`) — une session qui a
figé sa liste avant la seconde clé le voit déjà. Deux clés au même palier sans défaut et sans `_account` :
`api_get`/`api_post`/`api_doc` refusent par l'ambiguïté de la cascade (`CompteAmbigu`, qui nomme les
comptes), jamais par « carte non configurée » — `tools/api._resolve` ne ravale plus les refus de la
résolution. Banc : `tests/test_api_cles_perso_multiples_live.py`.

Les listes de l'agent la montrent (`connectors/api_operations.org_connectors`) : `api(op="list")` sans
`_account`, la ligne « Custom connector » du contexte et `oto_connector(name=…)` listent les clés de
l'APPELANT (`scope: "member"`, mention « your personal key, used before the org's ») avant celles de
l'org (`scope: "org"`, ligne de contexte inchangée). Le palier membre n'est lu que si la carte déclare
`byo_user`, et sa ligne se déduit de l'appelant : un collègue ne la voit dans aucune liste. Un même
compte aux deux portées est listé deux fois, mais reste UN connecteur pour l'appel par nom
(`api_act(op="call")`) : la cascade sert la clé perso.

### Le mode `session` du Custom API : un état propre au compte, scellé dans `meta` (12/11/2026)

Pour un service sans clé d'API, la carte `api` prend `auth_mode=session` : un identifiant, un mot de passe
(secret) et des **étapes** (`session`, un objet JSON lu strictement par oto-core, `oto.tools.http.session`) —
un POST JSON de login, puis, au besoin, le GET de l'URL que la réponse nomme et ses redirections. Chaque saut
reste sur l'hôte de la `base_url`, un hôte listé ou le domaine déclaré, et passe par `egress.check_url`
(`connectors/api_session.guard` ; les exceptions nommées ne valent que pour l'hôte de la `base_url`, et
pour une carte du palier plateforme). Un 401
ou un renvoi vers une page de login déclenche un login complet et un seul rejeu ; refusé encore, la clé passe
au rouge (`credential_rejected`). Le mode **ne fait que lire** : `api_post` et les verbes d'écriture
d'`api_act` sont refusés par nom (`api_session_read_only`) avant tout envoi, `dry_run` compris — sauf une
action déclarée `class: read` (ci-dessus). ⚠️ oto-core garde sa propre garde de lecture seule
(`READ_ONLY_MODES`) : tant qu'elle ne connaît pas cette exception, un POST `class: read` passe la garde
de l'hôte et reste refusé par le client.

**Le jar de cookies** est un état du compte qui change à l'usage, pas à la pose. Il ne va donc pas dans
`secret_enc` (que seule la pose réécrit, sous verrou optimiste) mais dans un **satellite scellé** de la même
ligne : `meta.session_jar_enc`, chiffré AES-GCM sous la clé du coffre, AAD = celle de la ligne suivie de
`#session_jar` (`credentials_store.seal_satellite` / `unseal_satellite`, jeu fermé `SEALED_SATELLITES`, retiré
des surfaces de statut comme un bearer). Écrit par fusion JSONB : ni `version` ni `set_at` ne bougent. Le
scellé porte l'empreinte des champs qui décident qui se connecte (base, identifiant, mot de passe, étapes) :
un jar scellé pour d'autres champs n'est pas resservi. Une repose remplace `meta`, donc efface le jar. Entre
processus, **le dernier écrivain gagne** — au pire un login de plus, jamais un autre compte. Un scellé qui ne
se déchiffre plus (ligne déplacée, renommée — la ligne `''` devenue `principal` à la pose d'un premier
compte nommé —, copiée vers une autre instance) est un cache perdu : journalisé, et l'appel se reconnecte.
`crypto.unseal` lève alors `crypto.EnveloppeIndechiffrable`, seule erreur que `read_jar` avale (une master
key absente monte). Le jar n'est jamais rendu à l'agent ni journalisé.

**Une clé que le coffre ne relit plus** (signal 1465) : hors du jar — qui n'est qu'un cache —, une enveloppe
qui ne s'ouvre pas (`crypto.EnveloppeIndechiffrable`) ou un secret qui ne se déballe pas
(`credentials_store.SecretUnpackError`) est servi à l'agent sous son nom, code `credential_unreadable`,
non rejouable, avec son message (écrit par nous, sans secret) et le geste : reposer la clé. Il restait
« Internal server error. », lu comme une panne et rejoué. Il reste remonté à Sentry : c'est un défaut de
notre côté (`error_taxonomy._vault_unreadable`).

**Le mode `visitor`** (14/11/2026) : pour un site public sans compte. La carte ne prend que `session`, lu par
oto-core avec son propre jeu fermé (`login` = `{path, method: "GET", query}`, sans `follow`, sans page de
login par défaut) : la « connexion » est le GET d'une page d'entrée dont la réponse pose les cookies du
visiteur, ses redirections suivies. Même garde à chaque saut, même jar scellé, même empreinte
(`api_session.JAR_MODES`, `keeps_jar`) — identifiant et mot de passe y valent vide. Un 401 relance une
visite et un seul rejeu. Le site qui ne donne pas de session (aucun cookie posé, visite refusée, refus
encore après une visite neuve) est refusé par nom, `api_visitor_refused`, **jamais** `credential_rejected` :
il n'y a pas de clé à reposer, la carte ne passe pas au rouge. Le mode **n'est pas en lecture seule** (aucun
mot de passe d'une personne n'est en jeu) : un POST y est jugé comme partout (`tools/effets` : `api_post`
envoie, `api_act` suit le verbe), sans exception par mode. Non traité ici : une réponse 200 qui porte une
erreur métier dans son corps (un drapeau d'erreur, un code) reste un succès jusqu'au lot qui projette la
réponse (`output`). Banc : `tests/test_api_mode_visiteur.py`.

### `inst:{id}` — la forme de fil

`instance_refs.make_instance_ref(id)` rend `inst:{id}`, et `parse_ref` accepte désormais
**les deux grammaires** : les refs composés sont déjà distribués (bindings de slot B5,
axe `_instance=`, `resource_id` des arêtes de `grants`) et rien ne les réécrit ici.
`GET /api/me/connector-instances` sert donc `id` **en plus** de `ref` — résolu en **une**
requête pour toute la liste, **fail-open** (l'identifiant n'est consommé par rien encore ;
faire tomber le listing des clés de quelqu'un pour lui serait hors de proportion), donc
**`id` peut être absent** et le client garde `ref`. ⚠️ Depuis la pièce 2 (28/08), une
absence n'est plus jamais « la clé est trop fraîche » : elle ne peut venir que du
fail-open. La phrase servie a été corrigée en conséquence — c'est le seul changement
d'empreinte du lot.

⚠️ **`inst:` se PARSE mais ne se résout pas.** Les deux gardes de pose — l'axe
`_instance=` (`access.rbac.guard_instance_access`) et le binding de projet — le refusent
**nommément** : sans cette branche, un `inst:` s'entendrait dire que « les refs
`platform:` ne s'épinglent pas », un message faux qui envoie chercher au mauvais endroit.

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

Corollaire de modèle, visible sur Salesforce (`auth/salesforce.py`) : l'**application**
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

**Zoho, même règle avec les MÊMES entrées.** Le retour est appelé par Zoho, sans session :
re-résolu depuis le contexte ambiant, il voyait l'équipe MAISON du compte, pas celle de
l'aller — donc potentiellement une autre app. Depuis le 14/10/2026 l'aller signe dans le
state l'org, **l'équipe** et le **niveau d'accès** (`make_state`, refusé sans eux), et le
retour rejoue `zoho_oauth.app_fields(…, org=, group=)` avec ces valeurs — la cascade du
BYO résolue sous cette org et cette équipe (`_under`), refusée si l'appelant a quitté l'org
entre-temps. `persist` range en meta le niveau demandé et le `scope` que Zoho dit avoir
accordé (`docs/connector-model.md` §Niveau d'accès).

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
- **Ordre de lecture** (`auth.zoho.app_fields`) : l'app **dédiée** de l'équipe, puis
  celle de l'org (§App DÉDIÉE ci-dessous), puis le BYO (membre > équipe > org) — une org
  qui veut voir SON app dans ses logs la pose et rien ne change pour elle ; l'app
  d'éditeur n'est le repli que si personne n'a rien apporté.
- ⚠️ **Invariant qui rend le rangement sûr** : `walk_cascade` ne propose le palier
  plateforme que si le connecteur déclare `auth_modes ∋ 'platform'`. Les connecteurs à
  consentement ne le déclarent pas ⟹ l'app d'éditeur n'est **jamais** servie comme
  credential d'appel. Sans ça, un membre qui n'a pas consenti hériterait d'une app
  **nue** (sans `refresh_token`) et se prendrait un échec OAuth opaque au lieu de
  s'entendre dire de se connecter. Figé par `tests/test_editor_app.py` (avec
  contre-épreuve sur un connecteur qui, lui, déclare le mode plateforme).
- **Pose** : `POST /api/admin/editor-apps` (super admin, capacité
  `platform.editor_app.set`). **REST seulement** — un secret brut en argument d'outil
  MCP transiterait par le contexte du modèle. La réponse rend le `callback_url` à
  déclarer chez le fournisseur : celui du **tenant** quand la clé le désigne
  (`tenant:<slug>`, cf. ci-dessous), le nôtre sinon.
- **Google, par TENANT** (23/09/2026) : la clé n'est pas une région mais le tenant,
  dans son **propre espace de noms** — `tenant:<slug>` (`credentials_store.tenant_app_key`,
  donc `editor:tenant:<slug>`). ⚠️ Jamais le slug nu : un tenant nommé comme une région
  zoho (`eu`, `com`…) aurait posé, lu ou retiré l'app zoho de la plateforme pour cette
  région. Un partenaire qui veut SON écran de consentement (sa marque, son projet Google
  Cloud, ses scopes vérifiés sous son nom) pose son client ici ou depuis son tableau de
  bord (`tenant_apps`, liste FERMÉE : `google` seul, le seul flux qui lit l'app d'un
  tenant) ; `google_oauth.app_for(sub)` le sert à tout compte qualifié sous ce slug, et
  le rappel passe sur le premier host que le tenant TIENT (`tenancy.callback_host`) — un
  client OAuth n'accepte que les domaines de son propriétaire. Sans app posée : l'env
  (`GOOGLE_WORKSPACE_CLIENT_ID/_SECRET`) et notre rappel, l'état d'avant. Une erreur de
  coffre **remonte** (pas de repli silencieux sous notre marque, silences B7).
  Consentement, échange du code et refresh emploient la MÊME app, et **un jeton ne se
  rafraîchit qu'avec le client qui l'a émis** : ce client est noté sur le jeton (meta
  `client_id` ; absent = notre app, celle de tout jeton d'avant). Poser, changer ou
  retirer l'app d'un tenant rend ses jetons d'avant refusés AU PROCHAIN APPEL, avant
  tout réseau : `GoogleReauthRequired` (« reconnecte ce compte »), compte marqué, jamais
  purgé. Un client refusé par Google au refresh (`unauthorized_client`,
  `invalid_client`) est une `GoogleClientRejected` nommée — pas un grant mort, rien
  n'est marqué.
- ⚠️ **Le blob d'une app d'éditeur a sa forme PROPRE** (`_pack_editor_app`, JSON des
  deux champs), indépendante du schéma du connecteur. `pack_secret` en dépendait : sur
  `google` (`secret_kind="oauth"`, schéma vide) il ne gardait que la première valeur —
  le `client_secret` était perdu à la pose et `get_editor_app` rendait `None` sans un
  mot. Byte-à-byte ce que zoho écrivait déjà (≥ 2 champs) : rien à migrer.
  Figé par `tests/auth/test_google_tenant_app.py`.
- **Conséquence de rotation** : `persist()` range une COPIE de l'app dans le credential
  né du consentement. Roter l'app d'éditeur ne casse donc pas les connexions déjà
  établies… jusqu'à leur prochain refresh, qui échouera avec l'ancien `client_secret`.

### App DÉDIÉE d'une org ou d'une équipe — l'app sans le jeton (14/10/2026)

L'app d'éditeur un cran plus bas. Posée en BYO au palier org, l'app d'une org (ses
`client_id`/`client_secret`) était un **credential d'appel** : la cascade la servait NUE,
sans `refresh_token`, à qui n'avait pas consenti. Rangée à part, elle n'est lue que par le
consentement.

- **Rangement** : scope `PLATFORM`, `entity_id = app:org:<id>` / `app:group:<id>`, blob JSON
  explicite `{client_id, client_secret, data_center}` — pour Zoho, la **région fait partie de
  l'app** (une app est enregistrée dans un data center). Préfixe distinct d'`editor:`, donc
  absent de `list_editor_apps`. Accesseurs `credentials_store.{set,get,clear}_dedicated_app`,
  `dedicated_app_owners` (présence sans déchiffrer, pour `app_ready` : une lecture par
  appel pour tous les connecteurs, `dedicated_apps`, mémoïsée par `lecture_d_appel`).
- **Même invariant que l'app d'éditeur** : `walk_cascade` ne propose le palier plateforme
  qu'aux connecteurs `auth_modes ∋ 'platform'` ; les connecteurs à consentement ne le
  déclarent pas ⟹ jamais servie comme credential d'appel (`tests/test_editor_app.py`,
  prouvé en SQL par `tests/connectors/test_access_level_live.py`).
- **Ordre de lecture du consentement** : app dédiée de l'équipe > app dédiée de l'org >
  credential BYO existant > app d'éditeur. Une app qui porte sa région l'impose (`consent_region`) :
  l'app dédiée, et aussi la clé posée sur la carte, par son champ `data_center`. Un départ
  qui en nomme une autre est refusé ; un départ sans région prend la sienne, ce qui permet
  à un membre de cliquer « connecter » sans connaître la région de l'app de son équipe.
  Sans région nulle part, le refus est nommé `zoho_region_missing` et dit quoi renseigner :
  aucune région par défaut.
  L'écran le sait avant le clic : `connect.fixed` (projection authentifiée de
  `GET /api/me/connectors`) porte `{data_center: <région>}` quand l'app applicable en lie
  une — le front montre la région au lieu du choix. Lu dans le même passage qu'`app_ready`
  (`auth/zoho.py::_applicable_app`, une fois par appel), sans secret ; vide pour l'app
  d'éditeur, qui se choisit par région au clic. Le même passage sert `connect.app_owner`
  (`member`, `group`, `org`, `tenant`, `editor`) : l'écran offre de changer ou de retirer SA
  clé, et nomme à qui s'adresser pour une clé partagée ; et `connect.ready_options`
  (`{data_center: [régions]}`, celles où oto publie une app, vide sous une app apportée) :
  l'écran dit qu'une région n'a pas d'app avant le clic. Le champ `data_center` d'une clé est
  une liste fermée sans valeur présélectionnée (`providers/zoho.data_center_field`).
- **Organisation consentie** (`zoho_org_id`, champ non secret, optionnel, de la clé et de
  l'app dédiée) : n'importe quel compte Zoho peut consentir à une app server-based. Au retour,
  AVANT de ranger, `check_org` lit l'organisation que le jeton neuf atteint (CRM `GET
  /crm/v7/org` : `id`, `zgid` ou `domain_name` ; Desk `GET /api/v1/organizations` ;
  Analytics, ses orgs) et la compare. Écart : jeton **révoqué** chez Zoho (un échec de
  révocation se journalise `zoho_revocation_failed`), rien de rangé : la complétion
  (`POST /api/me/connectors/{name}/complete`, oto-backend#1908) répond 409 `org_mismatch`,
  `details.consented_org=<nom>`. Organisation illisible alors qu'elle est attendue : refus
  (502 `exchange_failed`). App apportée SANS `zoho_org_id` : le consentement passe, SIGNALÉ
  (`warning: zoho_org_unverified` dans la réponse 200, `connect.warnings` sur la carte ;
  `zoho_org_unverifiable` sur Zoho Sign, dont la carte n'a pas ce champ : `auth/zoho.org_warning`).
  Personne côté admin n'a de compte dans l'org Zoho du membre pour y lire son identifiant :
  la réponse MONTRE donc l'org consentie — `details.consented_org` et
  `details.consented_org_id`, une entrée par org (appariées par position) —, l'identifiant affiché étant celui que l'aide du
  champ fait saisir (CRM : `zgid`, l'« Org ID » de Company Details, le nombre après `org`
  dans l'URL du CRM ; `id` si Zoho n'envoie pas de `zgid` ; Desk : `id` ; Analytics :
  `org_id`). Rien n'en est retenu : l'admin le recopie dans `zoho_org_id` s'il le veut. Cette
  lecture ne fait que montrer : un échec ne révoque ni ne refuse rien, garde l'avertissement
  seul et se journalise `zoho_consented_org_unread` (type d'exception seul, ni jeton ni
  corps). La carte (`connect.warnings`) n'en porte rien : seul le retour connaît l'org.
  L'app d'éditeur d'oto n'est jamais comparée ni lue : tout compte y consent, par
  construction.
- **Pose : REST seule** (secret brut), `ORG_ADMIN_OF` / `GROUP_ADMIN_OF`,
  `PUT|GET|DELETE /api/{orgs,groups}/{id}/connectors/{name}/oauth-app`
  (`capabilities/connectors/oauth_app.py`). Le `GET` rend `configured`, la région, la date
  et l'auteur de la pose et `read_fingerprints` (4 caractères HMAC par champ secret, liés à
  la ligne) — jamais le secret ni le `client_id` (contrat de `me.credential`, #671). Liste
  FERMÉE : seuls les connecteurs dont le flux lit une app dédiée (les trois Zoho).
- ⚠️ Rangée au palier plateforme, elle **ne part pas** avec un export de périmètre de son
  org (`export_perimetre` ne prend jamais `platform`) : l'org importée la repose.

### Secrets de l'application de l'INSTANCE — l'app d'éditeur sans région (19/11/2026)

Les connecteurs dont l'application OAuth est celle de l'instance, enregistrée une fois par
l'exploitant chez le fournisseur — `microsoft` (et ses services, dont `sharepoint`),
`jefacture`, `instagram_meta`, `hubspot`, `slack`, `linkedin_ads`, `meta_ads` — rangent les
secrets de cette application comme l'app d'éditeur : au coffre, chiffrés par la clé maître.
Leurs réglages publics restent des réglages.

- **Partage public / secret, par le NOM de la clé** : `connector_settings.a_la_forme_d_un_secret`,
  liste FERMÉE de motifs (`secret`, `password`, `token`, `api_key`, `private_key`…). Une
  clé de cette forme (`client_secret`, `app_secret`, `user_client_secret`,
  `signing_secret`) va au coffre ; les autres (`client_id`, `app_id`, `config_id`) restent
  au palier plateforme de `connector_settings` — elles voyagent de toute façon dans chaque
  URL d'autorisation. Le `client_id` ne suit pas le secret, contrairement au blob de l'app
  d'éditeur : celle-ci se pose en un geste (`client_id` + `client_secret` ensemble), ces
  coordonnées se posent clé par clé par la console, et une clé publique n'a rien à gagner au
  chiffrement.
- **Rangement** : scope `PLATFORM`, `entity_id = app:instance` (préfixe `app:` des apps
  dédiées, propriétaire `instance` qu'aucune app dédiée ne peut porter, absent de
  `list_editor_apps`), **une ligne par secret**, la clé du réglage en `account` (donc dans
  l'AAD). Accesseurs `credentials_store.{set_app_secret, get_app_secrets, app_secrets_set,
  clear_app_secret}` ; `app_secrets_set` dit la présence sans déchiffrer, mémoïsée par
  appel (une lecture pour toutes les cartes d'une liste).
- **Même invariant que l'app d'éditeur** : aucun de ces connecteurs ne déclare
  `auth_modes ∋ 'platform'` ⟹ `walk_cascade` ne sert jamais ces lignes comme credential
  d'appel (`tests/test_editor_app.py`).
- **Un lecteur** : `auth/app_instance.{reglages, manquantes}`, appelé par chaque module
  `auth/*` concerné. Un secret ne se lit QU'au coffre : une ligne en clair de la même clé
  n'est jamais lue, un secret absent est dit manquant par le refus nommé du connecteur
  (« not configured… missing client_secret… sets them with `oto_admin_connector_setting` »).
  Un déchiffrement en échec lève.
- **Pose** : la console admin, inchangée pour l'exploitant — `oto_admin_connector_setting
  op=set connector=<c> key=<clé> value=…` (REST `POST /api/admin/connectors/settings`) —
  route une clé de forme secret au coffre, au palier plateforme seulement (`org_id` refusé) ;
  `op=clear` la retire du coffre ; `op=list` la rend « posé », jamais sa valeur.
- **Garde** : `db/connector_settings.set_connector_setting`, écrivain unique de la table,
  REFUSE une clé de forme secret (`ReglageSecretRefuse`), et la copie entre cartes
  (`copy_platform_settings`) aussi. Une exception nommée : `planity` / `firebase_api_key`,
  clé publique par conception (servie au navigateur par le site du fournisseur). Sonde sur
  le code : `tests/test_connector_settings_sans_secret.py`.
- **Les lignes d'avant** se scellent par `oto-mcp maintenance secrets-d-application`
  (`docs/commands.md`), qui demande la clé maître — une révision Alembic ne le peut pas.

### Grant mort : marquer, plus jamais purger (oto#25 lot a, 2026-09-04)

Un refresh token révoqué (`invalid_grant`) n'efface plus la ligne du coffre — ça la
rendait indiscernable d'un credential jamais posé, un repli qui masque un problème
plutôt que de le nommer. Elle se fait marquer (`meta.health_ko` + `meta.health_reason`
= motif fournisseur **brut**, pas une catégorie opaque), même mécanisme que la sonde
`verify` des connecteurs keyés. **La règle vaut toujours** ; ce qui a changé, c'est sa
population. Le lot visait les mounts OAuth fédérés au scope LEGACY `("user", sub)`
(`auth/atlassian.py`, `auth/folk.py`) — **retirés le 2026-09-09 avec la fédération MCP
(ADR 0069)**, comme les deux modules. Les connecteurs OAuth restants (google,
salesforce, zoho — lots b2 et b3 ci-dessous) portent le même mécanisme au palier
MEMBRE. Récit complet, ce que ça rendait observable (`/api/me` via
`connectors/link.py::LinkState`, seam intact) et ce que ça ne rendait PAS observable
(`oto_instance op=verify`, faute de sonde enregistrée et d'un walker qui sache lire ce
scope) dans `connector-model.md` §« Purge silencieuse des mounts OAuth ».

### L'aide partagée, généralisée à salesforce/zoho (oto#25 lot b2, 2026-09-04)

Le mécanisme du lot (a) était PRIVÉ à `capabilities/connectors/verify.py`
(`_FLAGGABLE` + `_record_health`, sous ces noms). Extrait en module public
`connectors/health.py` — `FLAGGABLE_SCOPES` (member/group/org, **et** le scope
LEGACY `user` : aussi étroit que member, un seul utilisateur, jamais atteint par la
cascade de `verify`) + `record_health` (utilisé par `verify.py`, démarque aussi sur
succès) + `mark_rejected` (la façade neuve : un module qui connaît son ENTITÉ
directement, sans passer par `ResolvedCtx`). `verify.py`, `auth/atlassian.py` et
`auth/folk.py` appelaient ce module au lieu de leurs anciennes
définitions/appels directs à `credentials_store.update_meta` — même comportement,
un seul endroit qui sait marquer une ligne rejetée. ⚠️ Les deux modules `auth/` sont
partis le **2026-09-09** (ADR 0069) ; `FLAGGABLE_SCOPES` **garde** le scope LEGACY
`user`, et c'est délibéré : plus aucun connecteur vivant n'y écrit, mais des lignes y
dorment, et le retirer ferait d'un scope inatteignable un scope *interdit* — deux
choses différentes.

Deux connecteurs NEUFS rejoignent le mécanisme : `tools/salesforce.py` et
`tools/zoho.py` marquent désormais leur ligne au refus du REFRESH
(`SalesforceAuthError`/`ZohoAuthError`, exceptions **typées** que lève le CLIENT
oto-core — jamais un 401 nu d'un geste applicatif ordinaire, ex. permission
manquante sur un enregistrement précis avec une clé par ailleurs saine), PUIS
RE-LÈVENT toujours l'exception d'origine — marquer n'est jamais un fallback qui
avale l'erreur réelle. `tools/zoho.py` bascule au passage de
`access.resolve_credential_fields("zoho")` vers
`access.resolve_credential("zoho", want="byo")` (mêmes champs, plus l'entité
gagnante — nécessaire pour marquer la bonne ligne).

**`google` reste explicitement EXCLU de ce lot** : un WIP concurrent touche son
retour OAuth (`auth/google.py`) au moment du lot b2 — à faire une fois ce WIP
stabilisé, dans un lot séparé.

✅ **Fait dans ce lot séparé (2026-09-05, une fois oto-backend#877 poussé et
tagué)** : `credentials_for` (le seul appelant de `_refresh_access_token`) marque
sur `invalid_grant` (`GoogleReauthRequired`, même règle `oauth_flow.grant_is_dead`)
puis **relève toujours** — contrairement à atlassian/folk (alors encore au catalogue,
retirés le 2026-09-09, ADR 0069), `credentials_for` n'a
jamais rendu de `None` muet et ce lot ne change pas ce contrat. Démarque
explicitement au refresh réussi (depuis que le jeton d'accès vit en mémoire, le refresh
n'écrit plus rien dans `meta` : l'appel explicite est le seul démarquage) : le refresh MERGEAIT le meta
(`credentials_store.update_meta`, JSONB `||`), donc un `health_ko` posé plus tôt n'aurait jamais
disparu tout seul (même raison que le point 3 ci-dessous pour Salesforce —
contrairement à atlassian/folk, dont le remplacement total du meta démarquait déjà
par accident). Bancs : `tests/auth/test_google_health_marking.py`.

### Le démarquage (oto#25 lot b3, 2026-09-05)

Trois déclencheurs, et AUCUN autre — surtout pas un appel `ok=true` quelconque, qui
crierait au loup à tort sur un simple throttle passager :

1. **`oto_instance op=verify` vert** — déjà en place depuis toujours (`record_health`
   appelé avec `ok=True` par `verify.py`), rien à faire ici.
2. **Une nouvelle clé posée (reconnexion)** — déjà acquis, mais par un mécanisme
   ACCIDENTEL qu'il fallait vérifier plutôt que supposer :
   `credentials_store.set_credential` REMPLACE tout le `meta` (jamais un merge, cf.
   son propre docstring) — poser une clé, quel que soit le chemin
   (`capabilities/me_credentials.py::_set` pour salesforce/zoho et tous les
   connecteurs keyés ; `persist_token` d'atlassian/folk jusqu'au 2026-09-09) écrit un `meta` neuf qui ne reporte
   JAMAIS un `health_ko` d'avant. Figé par des tests dédiés
   (`tests/auth/test_oauth_dead_grant_marks_rejected.py`,
   `tests/test_me_credentials_capability.py`) plutôt que laissé implicite : une
   régression de `set_credential` vers un merge romprait cette garantie en silence.
3. **Un refresh réussi** — le point qui manquait réellement :
   - `atlassian`/`folk` *(retirés le 2026-09-09, ADR 0069 — gardé pour le record)* :
     même mécanisme que le point 2 (`access_token_for` écrivait
     `meta={access_token, expires_at}` au chemin nominal, un remplacement qui efface
     `health_ko` de la même façon).
   - `salesforce` : `on_refresh` (`_rotation_writer`) n'est invoqué qu'APRÈS un
     refresh d'access token réussi — démarquage ajouté ici, **inconditionnel**
     (qu'il y ait ROTATION du refresh token ou non ; beaucoup de Connected Apps n'en
     imposent pas, et le démarquage ne doit pas attendre un événement qui n'arrivera
     peut-être jamais).
   - `zoho` : **couvert depuis oto-core v1.116.0.** Le client expose désormais un
     `on_refresh` symétrique à celui de Salesforce, et le backend le câble sur le
     démarquage. C'était un manque assumé tant que le refresh réussi restait
     entièrement interne au client (cache process-wide), donc invisible du backend :
     le corriger demandait de toucher oto-core, ce qu'un lot backend-only ne pouvait
     pas faire.
     ⚠️ Le rappel ne part **jamais sur un succès de cache**, et c'est ce qui en fait
     une preuve de vie. Le cache Zoho est process-wide et dure une heure : démarquer
     sur un jeton qu'il a servi repeindrait une ligne en vert sur la foi d'un refresh
     vieux d'une heure. Il ne part pas non plus sur un refresh en échec — un
     credential refusé ne doit surtout pas se faire démarquer comme sain.

## Validation

Pas de framework de tests dans le repo → validation manuelle sur **PG16 jetable (docker)** + revue adversariale par phase. Migrations idempotentes au boot (`init_db` : ALTER additifs, PK 4-col, backfills, encrypt-existing, drop-plaintext gaté).

## Déchiffrer un credential ad-hoc sur la box (ops)

```bash
# ⚠️ Déchiffrer un credential ad-hoc (crypto.decrypt / _reveal / credential_status) :
# `OTO_MCP_MASTER_KEY` n'est PAS dans .env — le lanceur la tire au boot du Secret
# Manager. Un script qui ne source que .env voit
# `encryption_enabled()=False` → tous les déchiffrements lèvent RuntimeError (FAUX
# négatif, ≠ InvalidTag). Depuis le passage de la box au lanceur générique (#967 lot 5,
# 30/09/2026), `OTO_MCP_MASTER_KEY` est tirée par NOM par `deploy/lanceur_secrets.py` :
# passer par lui (`lanceur --script scripts/X.py`, docs/commands.md §Un script d'entretien
# par le lanceur) et non par le fetch ci-dessous, celui de l'ancien `start-encrypted.sh`,
# qui ne vaut plus que sur une box revenue en arrière (retiré au lot 5b) :
#   set -a; . .env; . /etc/oto-mcp/scw.env; set +a
#   RESP=$(curl -s -H "X-Auth-Token: $SCW_SECRET_KEY" \
#     ".../secret-manager/v1beta1/regions/fr-par/secrets/<id>/versions/latest_enabled/access")
#   export OTO_MCP_MASTER_KEY=$(echo "$RESP" | python3 -c 'import json,sys,base64; print(base64.b64decode(json.load(sys.stdin)["data"]).decode())')
# Vécu 2026-06-22 (triage Sentry InvalidTag : 1 ligne de credential d'un connecteur
# alors fédéré — « mount », famille retirée le 2026-09-09 — corrompue, écrite
# avec une clé ≠ courante — les autres lignes déchiffraient → pas un souci de clé ;
# fix = purge → re-OAuth). `status_for` doit utiliser `credential_status` (présence
# sans déchiffrer), jamais `get_credential_with_meta`, pour ne pas 500 /api/me.
```

## Déplacer des clés d'org vers un membre ou une équipe dédiée (ops, 24/09/2026)

Restreindre un connecteur, c'est **placer la clé au bon niveau** (ADR 0053 D1) : la
réservation d'un connecteur à une partie des membres (`connector_acl`) a disparu le
24/09/2026. Une org qui s'en servait pour réserver ses clés les voit descendre au bon
niveau — les clés personnelles d'UN membre (`--vers membre`), ou une ÉQUIPE dédiée
(`--vers equipe`). Le geste est `scripts/deplacer_cles_org.py` — jamais un
`UPDATE` : l'AAD porte `entity_type:entity_id`, donc une ligne se **déchiffre avec le
sceau de l'org puis se réécrit** par `_upsert` (sceau du membre, `member_id(org, sub)`,
ou de l'équipe, `group:<id>`), et l'ancienne sort par `_delete` (son instance
s'archive, la nouvelle naît), comme `backfill_member_scope`.

```bash
# passe à blanc (défaut) : rejoue les écritures ET la relecture dans une transaction, puis ANNULE
python -m scripts.deplacer_cles_org --org <id> --sub <sub> --connectors a,b,c --vers membre
python -m scripts.deplacer_cles_org --org <id> --sub <sub> --connectors a,b,c \
    --vers equipe --equipe-nom "<nom>" --membres a,b,c [--rendre-active]
# valide : la même commande + --apply
```

- **Une transaction**, lignes d'org verrouillées (`FOR UPDATE`), `statement_timeout`
  15 s, `lock_timeout` 5 s ; `set_by` et `meta` gardés (plus une trace `_deplacement`).
- **Relecture avant validation** : déchiffrement au niveau cible, empreinte SHA-256
  égale à l'original, ligne d'org absente — sinon ROLLBACK (sortie 4), création de
  l'équipe comprise.
- **`--vers equipe`** crée l'équipe dans la même transaction : `--sub` en est le chef
  (`group_admin`, `created_by`), y entrent en `group_member` les subs de `--membres`,
  et eux seuls. ⚠️ **`--membres` est exigé** (sortie 2 sinon, refus nommé) : la table
  qui disait qui était autorisé est retirée (#1169), et le script ne devine pas — sans
  liste, il ouvrirait l'équipe à toute l'org en silence. Qui était autorisé se lit dans
  l'export attesté (ci-dessous) ; le chef seul : `--membres <sub du chef>`. Un sub de la
  liste hors de l'org : sortie 2. Un connecteur non org-partageable est refusé :
  le walker ne lit le palier équipe que pour un connecteur org-partageable.
- ⚠️ **Une clé d'équipe n'est lue que dans l'équipe ACTIVE** (`scope.current_group` :
  `_group=` de l'appel, consultation, sinon l'équipe « maison », UNE par sub toutes
  orgs confondues) ou par un pin de l'instance (`_instance=`, lien de projet). Une
  équipe neuve n'est active pour personne : `--rendre-active` la rend active pour ses
  membres dont l'org active est déjà cette org et qui n'ont aucune équipe active ; les
  autres sont comptés, à régler à la main (basculer leur équipe active retirerait ses
  clés ; changer leur org active changerait leur boîte à outils MCP, #1058).
- **`--vers membre` : la clé du membre n'est jamais touchée** : un compte nommé déjà
  pris renomme la clé DÉPLACÉE (`<compte>-org-<org>`, `principal-org-<org>` pour la
  ligne sans nom) ; une coexistence impossible (mono-compte, ligne sans nom côté
  membre) est un refus.
- **Refus d'ensemble en `--apply`** (sortie 3, rien d'écrit) : connecteur sans clé
  d'org ou non lisible au niveau cible, collision non résoluble, équipe du même nom
  déjà présente, ou objet qui désigne encore une instance d'org déplacée (lien de
  projet, arête `grants`, déclencheur, flotte) — `--force-orphan-bindings` passe outre
  ce dernier cas, en le disant.
- **Impact** affiché : autres membres de l'org ; en `--vers membre`, par connecteur,
  ceux qui résolvent la clé d'org aujourd'hui et la perdront ; en `--vers equipe`, les
  membres de l'équipe et leur équipe active (`--show-members` pour les subs). Les
  partages de l'instance d'org ne sont pas repris (comptés) : `share_down` ne vit plus
  que sur les clés plateforme, `share_side` n'est lu qu'au niveau membre, et
  `share_mode='open'` est la valeur par défaut, pas un partage.
- **Aucun secret** dans la sortie ni les logs (banc `tests/test_deplacer_cles_org.py`).
  Même prérequis que ci-dessus : `OTO_MCP_MASTER_KEY` doit être chargée comme au boot.

## Retirer `connector_acl` : l'export d'abord (ops, oto-backend#1169)

La table n'a plus de lecteur ni d'écrivain, mais porte encore des réservations. Elle
part par la révision `0053_connector_acl_retiree`, qui **refuse** (`ExportNonAtteste`,
rien supprimé) tant qu'une table non vide n'a pas d'export attesté. Le geste, unique,
joué AVANT `migrer upgrade head`, par le lanceur (même ligne `systemd-run` que `migrer`,
docs/migrations-versionnees.md §5, avec `--script deploy/archive_connector_acl.py` à la
place de `migrer upgrade head`) :

1. une transaction en lecture répétable exporte la table entière en CSV gzip vers
   `s3://<OTO_MCP_S3_BUCKET>/archives/connector_acl/<horodatage UTC>.csv.gz` (privé) —
   **jamais d'écrasement** : un objet présent est un refus, le bucket n'est pas versionné ;
2. il RELIT l'objet (mêmes octets, autant d'enregistrements que de lignes) ;
3. il pose l'attestation : `COMMENT ON TABLE connector_acl IS 'oto:exportee {objet,
   lignes, empreinte, le}'` (`oto_mcp/db/export_avant_retrait.py`). La révision, sous
   `ACCESS EXCLUSIVE`, recalcule compte et empreinte (md5 des lignes en JSON, triées,
   en UTC) et ne supprime que s'ils sont ceux attestés. Une table VIDE part sans
   attestation (base neuve, instance cible).

La sortie ne porte que la clé de l'objet, le compte et l'empreinte. Rejoué sur une table
attestée inchangée, le script ne refait rien ; table absente ou vide : rien à exporter.
