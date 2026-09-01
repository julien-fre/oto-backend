## prerequisite — ton token hubspot (private app)

hubspot s'authentifie via un **token de private app**. dans les [réglages de ton compte hubspot](https://app.hubspot.com), va dans **integrations → private apps**, crée une private app et coche les scopes :
- `crm.objects.contacts`, `crm.objects.companies`, `crm.objects.deals`, `crm.objects.tickets` (lecture + écriture) — le crm lui-même
- `crm.schemas.*` (lecture) — pour découvrir les propriétés d'un objet
- `crm.lists.read` + `crm.lists.write` — pour les **listes (= les segments)**

⚠️ si ta private app date d'avant, elle n'a que les scopes `crm.objects.*` et `hubspot_list` répondra **403**. ça ne veut pas dire que ta clé est mauvaise : rouvre la private app et ajoute le scope, le token ne change pas.

⚠️ le token attendu est l'**access token** de la private app (forme `pat-<région>-…`). si tu colles autre chose — typiquement un *refresh token* oauth, un long blob base64 qui commence par `Ci…` — hubspot répond **401 `EXPIRED_AUTHENTICATION`** avec un message trompeur du genre « expired 20697 day(s) ago » et une date d'expiration au 1er janvier 1970. rien n'a expiré : un refresh token n'a pas de champ d'expiration, donc envoyé en bearer il est lu comme expiré depuis l'epoch. ce message-là veut dire « ce n'est pas un access token », pas « ton token a vieilli ».

- copie le **access token** généré
- colle-le dans oto sur ton compte (`/account`), connecteur **hubspot**
- byo uniquement : ta clé ou celle partagée de ton org, pas de clé plateforme

## usage — ce que tu peux faire

interroge et édite ton crm hubspot (contacts, companies, deals, tickets) depuis claude.
- « cherche les contacts de chez acme » → `hubspot_object` (op `search`, object_type `contacts`)
- « crée un deal à 10k€ » → `hubspot_object` (op `create`, object_type `deals`)
- « les deals associés à ce contact » → `hubspot_object` (op `associations`)
- « ajoute une note sur ce contact » → `hubspot_object` (op `add_note`)

### segments = listes

dans hubspot, un « segment » est une **liste** : il n'y a pas d'api segments séparée. `hubspot_list` les couvre.
- « quelles listes de contacts j'ai ? » → `hubspot_list` (op `search`, object_type `contacts`)
- « crée une liste "ICP France" et mets-y ces 40 contacts » → op `create` puis op `add_members`
- « qui est dans cette liste ? » → op `members`
- « dans quelles listes est ce contact ? » → op `record_lists`

trois types de listes, choisis à la création et **non modifiables ensuite** :
- **MANUAL** — tu décides qui est dedans (`add_members` / `remove_members`)
- **DYNAMIC** — hubspot recalcule les membres depuis des critères ; les ops d'appartenance sont **refusées** dessus, on change les critères (op `update`, `filter_branch`)
- **SNAPSHOT** — filtrée une fois à la création, gérée à la main ensuite

vider une liste (`clear_members`) et la supprimer (`delete`) acceptent `dry_run=true` : ça dit ce qui partirait sans rien toucher. une liste supprimée reste restaurable 90 jours (op `restore`).

### propriétés — à lire avant d'écrire

les noms internes hubspot ne sont pas les libellés de l'interface (`dealstage`, pas « Deal stage »), et une liste déroulante n'accepte que ses valeurs déclarées. `hubspot_property` (op `list`) donne le schéma réel d'un type d'objet — c'est ce qui rend fiables les `create`/`update` de `hubspot_object` **et** les critères `filter_branch` d'une liste dynamique, qui référencent les propriétés par nom interne.
