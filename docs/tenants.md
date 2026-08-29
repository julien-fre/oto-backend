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

**Ce qui reste (PR 2)** : le rôle « admin de tenant » (le partenaire pose SA clé depuis SON
tableau de bord), l'arête tenant→org de la chaîne 0053 (budget par org — R10, la sémantique
de partage), l'étage tenant sur l'endpoint anonyme, la ligne `tenant` dans `oto_instance
op=list` (les instances existent, elles ne sont pas encore listées ni épinglables depuis la
liste), et le mode `tenant` de `/api/me` côté dashboard (`keyStack.ts`).
