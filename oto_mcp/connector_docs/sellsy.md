## prerequisite — un accès API V2 (client id + secret)

Dans Sellsy : **Réglages → Portail développeur → API V2 → créer un accès**. Choisis un accès de type **personnel** (« personal ») : c'est celui qui délivre un jeton directement à partir du couple identifiant/secret, sans passage par un navigateur.

Colle ensuite dans oto :
- **Client ID** — l'identifiant de l'accès
- **Client Secret** — son secret (affiché une seule fois côté Sellsy)

Coche les **droits (scopes)** correspondant à ce que l'agent devra faire : lecture seule pour consulter, écriture pour créer des tiers ou des documents. Un droit manquant se manifeste par un refus `HTTP 403` au moment de l'appel, pas à la connexion.

byo uniquement : un compte Sellsy est celui d'une entreprise, il n'y a pas de clé partagée par oto. L'accès hérite des permissions du collaborateur auquel il est rattaché.

## usage — le CRM et la facturation dans la même conversation

Sellsy tient les deux bouts : qui sont les clients, et ce qui leur est facturé.
- « quelles sociétés ont été créées ce mois-ci ? » → `sellsy_third_party` (op `search`)
- « qui est Acme chez nous ? » → `sellsy_search` (plein texte, tous objets)
- « où en est le pipeline ? » → `sellsy_opportunity`, puis op `move` pour changer d'étape
- « liste les factures impayées » → `sellsy_document` (kind `invoice`, op `search`, filtre `status`)
- « fais un devis pour ce client » → `sellsy_document` (kind `estimate`, op `create`) — il naît en brouillon
- « quels articles au catalogue ? » → `sellsy_item` ; « les taux de TVA, les collaborateurs » → `sellsy_ref`

Les identifiants (étape de pipeline, taxe, collaborateur, champ personnalisé) se lisent avec `sellsy_ref` — ne jamais les deviner.

## note — écrire sans casser

Deux réflexes valent d'être connus avant de laisser l'agent écrire.

**Essayer à blanc.** `op="create"` accepte `dry_run=true` : Sellsy valide le corps et ne persiste rien. Utile avant une création en série, les champs obligatoires variant d'un compte à l'autre (numérotation, champs personnalisés).

**Valider est irréversible.** Un document créé est un brouillon ; `op="validate"` sur une facture ou un avoir fige son numéro et le rend comptable. À réserver à une décision humaine. Un devis, lui, change simplement d'état (`op="status"`).

Les quotas Sellsy se comptent par seconde, minute, jour et mois, et **chaque requête compte, même en erreur** : préférer `filters` + `fields` à un `all_pages` large.
