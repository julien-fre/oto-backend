---
title: Tenants (ADR 0052)
type: reference
description: >-
  L'étage d'identité entre plateforme et org : émetteur dédié, hosts, préfixe d'outils, écra
  n de suivi, et les pièges d'une bascule de compte (clés personnelles abandonnées, marque d
  'espace personnel, colonnes à sub triagées).
---

# Tenant — l'étage d'identité au-dessus des orgs (ADR 0052)

> Extrait de `CLAUDE.md` le 2026-08-27 — le contenu n'a pas changé, seule sa place a bougé.
> La carte garde le résumé + le pointeur ; le détail (schémas, incidents datés et leurs
> leçons) vit ici.

## Un tenant tiers est SERVI depuis le 13/08 (oto-private#83)

> **Un tenant tiers est SERVI, depuis le 13/08 (oto-private#83).** Le premier partenaire a
> son émetteur, son host, son client OAuth et ses 10 comptes qualifiés. Trois règles en
> sont sorties, toutes contre-intuitives, toutes payées en prod :
> - ⚠️ **La découverte annonce la FAÇADE sur le host du tenant, JAMAIS son émetteur en
>   direct.** Annoncer l'émetteur paraît plus honnête et retire `auth.facade` du chemin —
>   or elle existe parce que Logto self-hosted **ne fait pas de DCR** : le client échoue sur
>   « l'enregistrement automatique n'est pas pris en charge ». La façade s'annonce comme
>   serveur (issuer = le host) et route autorisation/jeton/clés vers l'annuaire du tenant.
>   Elle rend `tenants.oauth_client_id` — le client de l'annuaire VISÉ, sinon le client se
>   présente chez l'un avec l'identité de l'autre.
> - ⚠️ **Pas de patron, pas de lien** (`links.py`, `tenants.link_paths`). Les chemins d'un
>   partenaire ne ressemblent pas aux nôtres et certaines de nos vues n'ont **aucun**
>   équivalent chez lui : coller nos chemins sous son domaine fabrique des liens morts, pire
>   qu'un lien à notre marque. Un lien AFFICHÉ peut valoir `None` ; une REDIRECTION (retour
>   OAuth) aboutit toujours (`redirect_for`) — on ne peut pas « ne pas rediriger ».
> - ⚠️ **Nos propres patrons par défaut se vérifient contre le ROUTEUR du front, pas de
>   mémoire.** Corrigé le 28/08 : `DEFAULT_PATHS["doc"]` a dit `/docs/{id}` du 13/08 au
>   28/08 — un chemin que le tableau de bord ne route pas (il ne connaît que la section
>   `/documents`, sans id) et que son attrape-tout renvoie sur `/overview`. Le lien
>   n'aurait affiché aucune erreur : il aurait ouvert la page d'accueil en se faisant
>   passer pour la page demandée. Il n'avait aucun appelant, ce qui l'a gardé invisible un
>   mois — c'est-à-dire le lien mort que ce module interdit, posé chez nous. Le vrai
>   chemin d'une page l'ouvre DANS son projet (`/projects/{project_id}?doc={id}`) : une
>   page n'a pas d'écran à elle. **Un patron sans appelant n'est pas inerte, il est
>   simplement non testé** — n'en déclarer un qu'avec ce qui l'utilise.
> - **Le socle d'instructions suit le tenant** (`guides` scope `tenant`, owner = le slug) :
>   sinon l'assistant d'un partenaire se présente sous NOTRE marque à chaque session.
> - ⚠️ **Le socle ne suffisait pas : les OUTILS aussi portaient notre nom.** Dans la
>   conversation d'un client du partenaire, chaque appel s'affichait `Oto doc`,
>   `Oto project`… — le nom d'un outil n'est pas de la prose, il est réaffiché à chaque
>   tour. `tenants.tool_prefix` (NULL = inerte) fait traduire `oto_*` → `<prefix>_*` **au
>   bord du protocole seulement** (`tool_alias.py` + `ToolAliasMiddleware`, OUTERMOST) :
>   le nom CANONIQUE est rétabli avant que quoi que ce soit d'autre ne le lise, donc le
>   journal `tool_calls`, les toggles `user_disabled_tools`, les gates par namespace et
>   les refs `<tool:slug>` des procédures ne connaissent toujours qu'UN nom par outil —
>   rien à migrer. Les deux formes sont acceptées à l'appel (la prose déjà écrite cite
>   les canoniques). Sont traduits : `tools/list` (noms + descriptions), l'artefact de
>   session, le contrat d'erreur, et les cinq tools qui prennent un nom en argument
>   (`oto_call`, `oto_tool_schema`, `oto_list_my_tools`, `oto_{disable,enable}_tool`) —
>   sinon le catalogue et le dispatch parleraient deux langues. **Seul le namespace `oto`
>   bouge** : `data_*`, `run_*`/`feedback` et les connecteurs nomment une capacité ou un
>   fournisseur, pas notre marque. ⚠️ Un préfixe qui serait un namespace déclaré est
>   REFUSÉ (l'alias éclipserait un vrai outil) ; rien n'est réparé au passage (`Acme` est
>   refusé, pas abaissé — cf. le slug). Poser = `UPDATE tenants SET tool_prefix=…` **+
>   restart** (le registre est bâti au boot) ; l'écran `/platform/tenants` nomme l'écart
>   `tool_prefix` (déclaré) vs `tool_prefix_effectif` (appliqué). ⚠️ **Pas de canari
>   possible** : prod et preprod partagent la base (§Infra), donc la colonne posée vaut
>   pour les deux — la seule fenêtre de test est le décalage des redémarrages. La face
>   REST reste en canonique : ses écritures sont keyées par nom, et le dashboard d'un
>   tenant n'est pas le nôtre. **`serverInfo` du `initialize` est traduit aussi
>   (23/08)** : `name` suit le `tool_prefix` déclaré, `title` le nom du tenant
>   (`tool_alias.server_identity_for` + hook `on_initialize` du même middleware) —
>   rien de déclaré ⟹ l'annonce d'avant, à l'octet près.
>
> **Le suivi est un ÉCRAN depuis le 15/08** (`capabilities/tenants_admin.py` + `db.list_tenants_overview`,
> REST `/api/admin/tenants[/{slug}]`, MCP `oto_admin_tenant`, dashboard `/platform/tenants`) : qui est
> servi, sous quel émetteur, avec quelles orgs/comptes/appels. Trois choses à savoir avant d'y toucher :
> **(1) lecture seule** — le provisionnement reste un runbook et le registre est bâti AU BOOT, d'où le
> verdict `pending_restart` (déclaré en base, absent du registre ⟹ ses jetons sont encore rejetés) plutôt
> qu'un formulaire. **Depuis le 23/08, la prise d'effet ne demande plus de restart** :
> `oto_admin_tenant op=reload` / `POST /api/admin/tenants/reload` (SUPER_ADMIN,
> `server.reload_tenant_registry`) relit la base et swappe atomiquement le registre installé ET les
> émetteurs acceptés du verifier vivant — échec de lecture ⟹ rien n'est écrit, l'ancien registre reste
> entier. ⚠️ Par-process : recharger la preprod ne recharge pas la prod (même topologie que les `.env`) ; **(2) les deux rattachements sont comptés SÉPARÉMENT** (`orgs.tenant_id` vs la
> qualification du sub) et l'écart est nommé `orgs_desalignees` — en dériver un chiffre unique le ferait
> mentir ; **(3) c'est le premier LECTEUR de `orgs.tenant_id`** : le garde-fou L1 est passé d'une
> interdiction totale à une **allowlist** de deux fichiers (`test_tenant_l1_migration.py`) — un chemin de
> **résolution** qui dépendrait du rattachement d'org le casse toujours, et c'est voulu.
>
> ⚠️ **OPS — une bascule de tenant ABANDONNE les clés personnelles.** L'AAD dérive de
> l'entité : `migrate_sub` ne repointe plus `connector_credentials.entity_id` (une ligne
> repointée sans rechiffrement est indéchiffrable — pire qu'absente, la fiche la dit posée).
> Toute fenêtre doit donc s'accompagner de la LISTE « qui repose quelles clés », prévenue
> avant. ⚠️ Le scope `member` a `entity_id = "<org_id>:<sub>"` : une requête qui cherche le
> sub nu ne les voit PAS (elles sont pourtant la majorité).
>
> ⚠️ **Une fusion de comptes emporte la MARQUE d'espace personnel — depuis le 14/08
> seulement.** `orgs.personal_of` échappait aux deux garde-fous (pas une FK ⟹ invisible à
> `test_migrate_sub_cascade` ; pas dans l'inventaire ⟹ hors `test_migrate_sub_inventory`,
> qui vérifie que les entrées listées EXISTENT, jamais que les colonnes porteuses d'un
> identifiant soient listées). La marque restait donc sur un identifiant que l'étape 4
> supprime : plus d'espace personnel trouvable, et le boot suivant en fabriquait un neuf —
> **deux organisations au même nom**, dont l'ancienne, celle qui porte l'historique,
> n'était plus reconnue comme l'espace de son propriétaire. 14 comptes en prod, dont 9
> issus de la seule bascule du 13/08 ; archivés à la main le 14/08 (les 14 doublons
> n'avaient jamais servi : projets semés, aucune page, aucun tableau, aucune clé).
> Traitement à part (étape 2 quater, `test_migrate_sub_personal_org.py`) : l'espace de
> l'ANCIEN compte reste l'espace personnel sous le nouvel identifiant, celui du nouveau
> est démarqué — jamais archivé automatiquement, « cet espace n'a jamais servi » ne se
> décide pas au fond d'une transaction de merge.
>
> ✅ **Les colonnes à sub que le merge abandonnait sont TRIAGÉES depuis le 23/08.**
> L'historique (`runs.sub`, `project_activity.sub`, `runner_triggers.sub`,
> `tool_calls.effective_sub`) et les attributions (`resolved_by`/`created_by`/
> `granted_by`/`set_by`/`requested_by` de 10 tables) sont repointés par `_SUB_COLUMNS` ;
> `legal_acceptances`, `connector_acl.principal_id` et `option_comps.entity_id` passent
> par le patron PK (`_PK_SUB_TABLES` — sub jamais numérique ⟹ l'UPDATE ne touche que les
> lignes user) ; ⚠️ **le JOURNAL des acceptations (`legal_acceptance_events`, #487) est
> ajouté à `_SUB_COLUMNS` et PAS au patron PK** — il n'a aucune unicité, et dédupliquer
> y supprimerait des consentements pour cause de doublon. C'est lui que le gate lit :
> `legal_acceptances` n'est plus qu'une projection transitoire (issue #507) ; les arêtes `grants.grantee_id` ont leur étape filtrée (3 bis). Le trou de
> méthode est fermé par le **tripwire inverse** `test_migrate_sub_sub_bearing_columns_are_
> triaged` : toute colonne du DDL de la famille « porte un sub » doit être repointée,
> pré-traitée ou allowlistée AVEC sa raison — une colonne neuve arrive rouge. Restent
> hors repointage, par construction : `connector_credentials.entity_id` (AAD) et la
> mécanique du merge lui-même (`sub_aliases`, `users.sub`, `orgs.personal_of`).
>
> ⚠️ **Une TROISIÈME famille depuis le 2026-09-02 : `_UNIQUE_INDEX_SUB_TABLES`** (étape
> 2 quinquies). Elle existe parce que les deux premières répondent à des questions
> différentes, et qu'une table peut n'entrer dans aucune : `outreach_sends` a pour clé
> primaire un `id` BIGSERIAL, mais son unicité réelle est un index **partiel**
> (`(campaign, sub) WHERE kind='send'`). Rangée d'abord dans `_PK_SUB_TABLES`, elle
> aurait fait prendre `kind` pour une colonne de clé et **supprimé des essais que rien
> ne menaçait** ; rangée dans `_SUB_COLUMNS` seule, l'UPDATE nu aurait levé
> `UniqueViolation` dès que les deux comptes d'une personne ont reçu la même campagne —
> et fait échouer TOUT le merge. La famille dédoublonne donc sur les colonnes de
> l'INDEX, prédicat partiel reporté sur les deux côtés, puis laisse l'UPDATE nu de
> l'étape 3 repointer (d'où l'obligation d'être AUSSI dans `_SUB_COLUMNS`).
> Garde-fous dérivés du DDL : `test_migrate_sub_unique_index.py` (l'index existe, avec
> ces colonnes et ce prédicat ; l'entrée a son repointage ; elle n'est pas dans deux
> bacs). **Le classement reste manuel, et c'est le vrai défaut** : deux erreurs
> symétriques en quatre heures le 02/09 (deux colonnes de clé mises en repointage
> simple le matin, une colonne hors clé mise en patron PK le soir).
>
> ⚠️ **Et le classement manuel a un second trou, celui-là JAMAIS gardé** : les trois
> familles répondent toutes à la même question — *un UPDATE nu peut-il lever une
> violation d'unicité ?* — mais rien ne vérifiait qu'une colonne à qui la réponse est
> OUI soit bien rangée quelque part. `test_pk_sub_tables_reste_matches_the_real_primary_
> key` juge les entrées DÉCLARÉES ; il ne va jamais chercher les MANQUANTES.
> `test_active_membership_tables_are_pre_treated` ne ferme qu'une forme écrite en dur
> (`ON <table>(sub) WHERE is_active`), et seulement dans `_schema.py`.
>
> `test_toute_colonne_sub_sous_index_unique_est_pre_traitee` (2026-09-02) ferme la
> CLASSE : toute colonne de `_SUB_COLUMNS` couverte par un index unique — partiel ou
> non, déclaré dans `_schema.py` **ou créé par `_init.py`** — doit être pré-traitée ou
> allowlistée avec sa raison. La moitié `_init.py` est le cœur du sujet : **dix des
> quatorze index uniques du schéma y sont créés, et aucune garde ne les avait jamais
> lus** — dont quatre des huit qui couvrent une colonne porteuse de sub.
> Une colonne qui n'est PAS repointée (`connector_instances.owner_id`,
> `orgs.personal_of`) sort d'elle-même du critère : pas d'UPDATE nu, pas de violation.
>
> 🔴 **Ce que la garde a trouvé, et qui reste OUVERT : `user_datastores.owner_id`.**
> `uq_user_datastores_owner_ns (owner_type, owner_id, namespace)` n'est pas partiel ;
> deux comptes d'une même personne ayant chacun un namespace du même nom font lever
> `UniqueViolation` à l'étape 3 et échouer TOUT le merge — le mode d'échec du 28/07,
> encore ouvert, reproduit sur base réelle le 2026-09-02. Il n'est pas corrigé avec la
> garde parce que le geste mécanique des autres familles (DELETE de la ligne en trop)
> serait PIRE que la panne : `datastore_rows` est en `ON DELETE CASCADE` sur
> `user_datastores(id)`, donc jeter le namespace de l'ancien compte détruit ses
> LIGNES. Un merge qui échoue est bruyant et rejouable ; des lignes effacées en
> silence, non. La résolution correcte est probablement de RENOMMER le namespace
> repris — le nom que verra l'utilisateur est une décision de produit, pas de
> tuyauterie. Entrée d'allowlist datée, à retirer une fois tranché.

## La clé de connecteur du tenant (L-clés PR 1 — 2026-08-29)

**Ce que c'est.** Un opérateur de N orgs était condamné à la clé plateforme mutualisée ou à
une clé par org. Depuis cette PR, **le tenant possède sa clé de connecteur** : une ligne du
coffre `connector_credentials` avec `entity_type='tenant'`, `entity_id` = le slug — même
sceau que les autres entités (l'AAD porte `tenant:<slug>`, un ciphertext de tenant ne se
transplante sur rien d'autre), même naissance d'instance à la pose (`connector_instances`,
`owner_type='tenant'`, prévu « inerte » par le lot L6 et activé ici), même archivage au
retrait. Aucun credential existant n'a bougé : l'AAD d'une ligne d'org est identique à
l'octet avant et après (`tests/test_tenant_key_vault_live.py`).

**Où elle sert.** Entre l'org et la plateforme dans le walker unique
(`access.cascade.walk_cascade`) : `membre > équipe > org > tenant > plateforme`. Elle sert à
toutes les orgs du tenant qui n'en ont pas de plus proche. Trois choix qui ne se relisent
pas dans le code seul :

- **Le tenant se lit sur le sub qualifié de l'appelant** (`tenant_vault.rung_tenant`),
  jamais sur le rattachement de l'org — c'est le garde-fou du lot L1 (aucun chemin de
  résolution ne dépend de ce rattachement), et il tient toujours. Un compte du tenant
  actif dans une org « désalignée » (`orgs_desalignees` du suivi) voit donc SA clé de
  tenant, pas celle du tenant de l'org. L'endpoint MCP anonyme (`<slug>.mcp.oto.cx`, pas
  de sub) n'a pas l'étage : lui le donner demande l'arête tenant→org de 0053 (PR 2).
- **Le tenant `oto` n'a pas de clé de tenant.** Ses clés partagées SONT les instances
  plateforme, avec leurs grants ; une clé « tenant oto » serait un second mécanisme pour
  la même fonction. Refusée à la pose (`PrimaryTenantKeyRefused`) et **jamais sondée** à
  la lecture — les deux d'un même geste. Conséquence : un sub nu ne coûte aucune lecture
  de plus par appel (serveur mono-loop : c'est ce qui a été mesuré, pas un confort).
- **La fenêtre L7 est symétrique** : la chaîne 0053 (`chain_shadow`) calcule le même
  étage à la même source. Sans ça, chaque appel servi par une clé tenant aurait compté
  une divergence `inconnu` — la seule classe que la porte de la PR 2 de L7 exige à zéro.

**Surfaces admin (plancher opérateur — le rôle « admin de tenant » est la PR 2).**

| geste | surface | plancher |
|---|---|---|
| lister (jamais le secret) | `GET /api/admin/tenants/{slug}/keys` · `oto_admin_tenant op=keys` | platform admin |
| poser / roter | `PUT /api/admin/tenants/{slug}/keys/{provider}` — **REST seule** (`api_key` \| `fields` \| `base_url`, `account`) | super admin |
| retirer | `DELETE /api/admin/tenants/{slug}/keys/{provider}` · `oto_admin_tenant op=key_clear` | super admin |

La pose est REST seule pour la même raison que les clés d'org et les clés plateforme depuis
le 2026-06-25 : **un secret brut ne traverse pas un appel d'outil.** Même validation qu'une
clé d'org (`providers.org_secret_meta`, écriture partielle #448, garde de compte #409).

**Retour arrière.** Retirer la clé (`key_clear`) ramène toutes les orgs du tenant à la
cascade d'avant, à l'appel suivant — rien à invalider. Aucune colonne n'a été ajoutée : la
valeur `tenant` d'`entity_type` reste inerte tant qu'aucune ligne ne la porte, et le
`CHECK` d'`owner_type` sur `connector_instances` l'acceptait déjà.

**Ce qui restait à la PR 2** (livré le 2026-08-29, section suivante) : le rôle « admin de
tenant », l'arête tenant→org de 0053, l'étage tenant de l'endpoint anonyme, la ligne
`tenant` dans `oto_instance op=list`. Reste côté dashboard : le mode `tenant` de `/api/me`
(`keyStack.ts`, oto-front).

## Le rôle « admin de tenant » et l'arête tenant→org (L-clés PR 2 — 2026-08-29)

**Le rôle.** La sortie nommée du régime transitoire (0052 §Amendement 27/08 : l'opérateur du
premier tenant tiers est `super_admin` faute de ce rôle). Table `tenant_admins (slug, sub)`,
additive et réversible ; règle `_authz.TENANT_ADMIN_OF(slug, platform=…)` = la règle
plateforme essayée d'abord (`PLATFORM_ADMIN` pour lire, `SUPER_ADMIN` pour écrire), et sur
son 403 seulement, l'admin du tenant — **lu sur le sub qualifié** (`tenancy.tenant_of`), jamais
sur le rattachement d'une org (L1). Un admin déclaré sur `pilote` est un compte `pilote:…` ;
le tenant `oto` n'en a pas (ses admins sont ceux de la plateforme). Ce qu'il fait : poser et
retirer la clé de son tenant, en accorder l'usage à ses orgs, voir la fiche de son tenant, et
**mettre en pause / réveiller les comptes de son tenant** (2026-09-03,
`docs/comptes-en-pause.md`).
Déclarer le rôle reste un acte de la plateforme (`POST/DELETE /api/admin/tenants/{slug}/
admins[/{sub}]`, `oto_admin_tenant op=admin_add|admin_remove`, super admin).

⚠️ **L'admin de tenant agit par la face REST (son tableau de bord), pas par `oto_admin_tenant`.**
Le plancher d'un outil consolidé est le plus BAS de ses ops (`_authz._lowest_floor`) : une seule
op au rôle de tenant ferait descendre `oto_admin_tenant` à `None`, c'est-à-dire l'entrée de
l'outil dans le handshake de chaque compte de la plateforme. La console garde donc son plancher
`operator` (gardé par `test_tenant_admin_role.py`), et ce sont les capacités REST par geste qui
portent `TENANT_ADMIN_OF`.

⚠️ **Une exception assumée depuis le 2026-09-03 : `oto_admin_account`** (mettre un compte en
pause) est servi **en MCP**, plancher `None`, donc visible de tout le monde — 1 242 caractères,
1,37 % de ce qu'un compte ordinaire reçoit au handshake. Le raisonnement ci-dessus reste juste
(le plancher descend), mais sa conclusion ne vaut que pour une console qui a d'AUTRES ops : ici
la capacité est dédiée, et la restreindre à la face web reproduirait le défaut qui rend le rôle
inerte — **un partenaire travaille par MCP**, et fastmcp refuse aussi le `tools/call` d'un outil
masqué (#471), pas seulement le `tools/list`. Une capacité bornée inatteignable depuis la face
qu'on utilise ne rend littéralement rien.

⚠️ **`tenant_admins` est à zéro ligne en production à ce jour** : tant que personne n'y est
nommé, tous ces verbes ne sont exerçables que par la plateforme. C'est un fait de peuplement,
pas de conception — et c'est ce qui explique que l'opérateur du premier tenant tiers travaille
encore avec un rôle plateforme.

**L'arête tenant→org** (0053-D3 : « le tenant s'insère dans la même chaîne »). Ressource = la
clé du tenant (`tenant:{slug}:{connecteur}`, le ref du coffre), grantor `tenant:{slug}`,
grantee `org:{id}`, contrainte `quota` = appels/jour pour **toute l'org** (R10 : budget partagé,
la lettre de D7). Trois états, ceux de la clé plateforme au lot L5 :

| état | condition | ce que sert la cascade | la chaîne 0053 |
|---|---|---|---|
| MUETTE | aucune arête n'a jamais visé cette org | la clé, comme en PR 1 | `appartenance` |
| ACCORDE | ≥1 arête vivante | la clé, budget débité (`access.tenant_budget`) | `grant` |
| REFUSE | des arêtes, toutes révoquées | le barreau est **sauté** — l'org retombe sur la plateforme | idem |

`chain_shadow` lit l'arête par la **même fonction** que le walker (`grants_chain.tenant_rung`)
et passe au palier suivant sur REFUSE comme lui : aucun `inconnu` créé (gardé par
`test_tenant_edge_chain.py`). L'arête n'est lue qu'APRÈS que la sonde a trouvé une clé —
sans clé de tenant, zéro lecture.

⚠️ **Le budget est débité à la RÉSOLUTION, pas au succès de l'appel** — à la différence du
compteur plateforme, que chaque outil débite lui-même après un appel réussi
(`record_platform_usage`, ~10 sites). Une clé de tenant n'a pas ces sites ; en ajouter un par
outil serait la copie que le walker unique existe pour éviter, et une borne posée que personne
ne débite est le défaut de #409. Un appel qui échoue chez le fournisseur compte donc. Le
déplacement vers « au succès » passe par le relevé d'appel du middleware, avec L8.

**L'anonyme** (`<slug>.mcp.oto.cx`, ADR 0032) n'a pas d'identité : il n'obtient l'étage tenant
que par une **arête vivante** tenant→org (`grants_chain.tenant_for_org`), jamais par le
rattachement de l'org. Sans arête, sa cascade reste `org > plateforme`. Le budget vaut pour lui
aussi : l'org entière y puise, anonyme compris. ⚠️ L'arête ne re-tenante personne : un compte nu
dans une org accordée garde sa cascade d'avant.

**Surfaces** (`GET/PUT/DELETE /api/admin/tenants/{slug}/keys/{provider}/grants[/{org_id}]` ;
`oto_admin_tenant op=org_grants|org_grant|org_revoke`, plancher plateforme). Une arête sur une
clé absente est refusée (`no_tenant_key`) : elle ne servirait rien.

**`oto_instance op=list`** rend la clé du tenant de l'appelant au niveau `tenant` (entre `org`
et `platform`, `via=tenant_key`, `owner.type=tenant`), épinglable par `_instance=`. Un compte nu
ne la voit pas : il ne pourrait pas la résoudre (R9).

**Retour arrière.** Retirer le rôle (`admin_remove`) et révoquer les arêtes ramène à #603 —
avec une nuance à connaître : une org RÉVOQUÉE n'est pas une org « sans arête » (les archivées
restent, D7) ; pour la ramener à l'état MUET il faut supprimer ses lignes de `grants` (et
`grant_counters` d'abord, la FK refuse sinon). Aucune colonne retirée, une table neuve.
