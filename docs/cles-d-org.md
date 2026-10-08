# Clés d'API d'org

Conception du lot oto-backend#1188. Décisions d'Alexis du 08/10/2026 ; forme technique
arrêtée ici.

## Le besoin

Un jeton `oto_` **est** une personne (`user_api_tokens.sub`). Une intégration branchée par
un membre tombe quand il quitte l'org ou que son compte est mis en pause, et les admins de
l'org ne voient ni ne coupent ces clés.

## Ce qui est décidé (Alexis)

- Une clé d'org agit **au nom de l'org elle-même** : un compte de service propre à l'org,
  jamais celui d'un membre. Il voit ce que l'org possède et ce que ses équipes partagent
  avec lui, **jamais** les objets personnels des membres. Il survit à tous les départs.
- Toujours **à portée** (`auth/token_scopes`) ; elle peut être **émettrice** (`issue`,
  plafond nommé ou `{"orgs": {"<son org>": …}}`).
- Seuls les **admins de l'org** créent, listent et révoquent ses clés.
- Les écritures sont journalisées **au nom de l'org**, avec l'id du jeton.

## Forme retenue : un compte de service par org, rôle dérivé, sans ligne de membre

**Le compte.** Une ligne `users` par org (le jeton garde sa clé étrangère vers `users`),
créée à la première clé, jamais par `upsert_user` (qui créerait une org perso). Son sub :
`org-<id>` pour une org du tenant primaire, `<slug>:org-<id>` pour une org d'un tenant —
le préfixe est ce qui fait couper ses jetons par la désactivation du tenant
(`tenants.tenant_desactive_du_sub`, `desactiver_tenant`), sans code nouveau. Une table
`org_service_accounts (org_id PK → orgs, sub UNIQUE → users, created_at, created_by)` dit
qu'un sub est le compte d'une org, et laquelle.

**Le rôle, dérivé et non stocké.** Pas de ligne `org_members`. Le compte est `org_member`
de SON org par les trois lectures d'appartenance d'`org_store.members` :
`get_org_role`, `list_orgs_for_user`, `get_active_org`. Tout le reste en découle
(`roles.effective_org_role`, `access.current_org`, `ownership.accessor_scope`, la
validation de `X-Oto-Org`). Une seule requête joint `org_members` elle-même sur le chemin
d'une clé : la garde du run en en-tête (`db.usage.run_pour_en_tete`), qui reçoit la même
dérivation.

Pourquoi pas une ligne `org_members` : vingt-neuf requêtes lisent cette table, dont les
listes de membres, les sièges et la facturation. Avec une ligne, chacune devrait l'exclure,
et un oubli montre un faux membre ou le facture — fail-open. Sans ligne, une requête qui
joint `org_members` pour décider d'un accès ne voit pas le compte : il est refusé —
fail-closed, et le test le révèle.

Pourquoi `org_member` et pas `org_admin` : un admin d'org voit toutes les équipes de l'org
(`ownership.py`, `datastore/core.py`). Le compte d'org ne voit une équipe que si elle lui
partage un objet.

Pourquoi pas un principal distinct (comme `platform_worker` ou l'identité de service) :
`current_org`, `accessor_scope`, `can_access`, `walk_cascade`, les quotas, le journal et
le geste lisent tous un sub. Un principal sans sub les traverse tous, et les quotas ne
seraient plus métrés (`record_platform_usage` ne fait rien sans sub).

**L'enfermement.** Chaque clé d'org porte le verrou d'org (`verrou_org=true`,
`verrou_org_id=<org>`), comme un jeton de délégation : hors de son org, aucun rôle, aucune
org d'appel, aucun partage (`verrou_org.py`). Un enfant d'une clé émettrice hérite du
verrou de son parent.

## Coupures

- **Org suspendue** : la garde de suspension (`org_suspension`) coupe déjà toute capacité
  résolue dans l'org ; le verrou empêche d'en résoudre une autre.
- **Org archivée** : ses clés sont révoquées dans la transaction de l'archivage, motif
  « org archivée ».
- **Tenant désactivé** : par le préfixe du sub (ci-dessus).
- **Pause du compte** : `users.suspended_at` vaut aussi pour le compte d'org, posé par un
  opérateur ; `garde_identite` le lit déjà.

## Quotas et droits déclarés

Le compte est un sub : l'accès aux clés de plateforme passe par l'arête `org:` de son org
(il n'a pas de ligne `user:`), et ses appels se comptent sous son sub, à part de ceux des
membres. `has_right(sub, org, clé)` lui rend la valeur de l'org.

## Ce qu'il crée

Rien, aujourd'hui : aucune route de création de tableau ni de projet n'est ouverte à un
jeton à portée (`token_scopes._ALLOWED`). Il écrit des lignes dans les tableaux nommés,
qui appartiennent au tableau. Le jour où une création s'ouvre aux jetons, son
propriétaire par défaut (`datastore/registre._default_owner`, `capabilities/projects`)
devra devenir l'org quand l'acteur est un compte d'org : un objet personnel d'un compte
de service serait invisible de tous les membres.

## Surface REST

Capacités `org.api_key.*`, `ORG_ADMIN_OF("org_id")`, aucune face MCP,
`allow_api_token=False` (une clé ne gère pas les clés de son org) :

- `GET /api/orgs/{id}/api-keys` — les clés de l'org, sans secret (`include_revoked`) ;
- `POST /api/orgs/{id}/api-keys` — `{label, scopes, ttl_days}` ; `scopes` requis
  (`400 scopes_required`) ; rend le secret une seule fois, `201` ;
- `DELETE /api/orgs/{id}/api-keys/{token_id}` — révoque, `{reason}` ; ses enfants avec.

Une clé émettrice gère SES enfants par `/api/me/tokens`, comme tout émetteur : pour elle,
« moi » est le compte de l'org.

Le jeton porte `kind='org'` : `list_api_tokens` (écran « mes jetons ») ne le montre pas, la
liste d'org le montre avec `created_by` (l'admin qui l'a émis).

## Journal

La ligne REST porte le sub du compte d'org (`org-<id>`), `token_id` et
`token_kind='org'` ; l'export d'audit la rend telle quelle, `email` à null — le sub suffit
à dire que l'acteur est l'org, et `token_id` quelle clé. Les clés elles-mêmes nomment
l'admin qui les a émises (`created_by`) et celui qui les a révoquées (`revoked_by`).

## Ce qui ne marche pas, et c'est voulu

- **Pas de MCP** : un jeton à portée y est refusé (`server.py`), donc une clé d'org ne
  sert qu'en REST.
- Pas de clé d'org non portée, ni runner : toujours une portée de tableaux et projets, ou
  un plafond d'émission.
- Le compte d'org ne reçoit pas le kit de connecteurs de l'org (semé depuis
  `org_members`) : sans effet en REST, où les routes ouvertes à une clé ne passent pas
  par la sélection d'outils.
- Il n'apparaît ni dans les membres, ni dans les sièges, ni dans la facturation, ni dans
  les audiences de relance, et ne peut pas prêter un abonnement.
- ⚠️ Membre dérivé, il passe les contrôles qui lisent `get_org_role` : un admin pourrait
  lui transmettre une automatisation (`give`), qu'il ne saurait pas exécuter. Non gardé
  dans ce lot.
