# Pennylane (cabinet) — connecteur `pennylane_firm` et relais d'upload

L'API « Firm » de Pennylane, côté **cabinet comptable** : un jeton de cabinet atteint toutes les sociétés du
portefeuille. Transport dans la lib (`oto.tools.pennylane_firm`, client + description déclarative
`connectors/pennylane_firm`) ; ici l'**adaptateur** — quatre outils et un relais d'upload.

## Trois connecteurs Pennylane, trois espaces

| connecteur | porte | crédential | `company_id` |
| --- | --- | --- | --- |
| `pennylane` | API Company v2 | clé d'UNE société | ses propres ids |
| `pennylane_firm` | API Firm v1 | jeton de cabinet (palier org) | ids de l'application |
| `pennylaneged` | API interne de la SPA, session navigateur | contexte Browserbase | ids de l'application |

`pennylane_firm` et `pennylaneged` partagent les ids ; `pennylane` non. L'API Firm n'expose ni la forme
juridique, ni la catégorie IS/IR, ni les régimes fiscal et de TVA : ces réglages ne se lisent que par
`pennylaneged_company`.

## Outils

- `pennylane_firm_companies` — une page du portefeuille (filtre `client_code`), ou la fiche d'une société ;
- `pennylane_firm_tree` — dossiers ou fichiers de la GED d'une société, au curseur ;
- `pennylane_firm_create_folder` — écriture ;
- `pennylane_firm_upload_url` — frappe un lien de relais, gardé par le contrôle de doublon.

⚠️ **L'API n'a ni suppression, ni déplacement, ni renommage** : un mauvais dépôt ne se corrige pas par elle
(seulement dans l'interface, ou par `pennylaneged_delete`). D'où le refus du doublon, joué AVANT d'écrire.

Aucune liste blanche de sociétés : le jeton de cabinet ouvre l'écriture sur tout le portefeuille, et
l'org qui le pose en répond.

Débit : 5 requêtes/s par jeton (limiteur de la lib, au niveau du module, partagé par les outils et le relais) ;
un 429 de Pennylane impose une minute d'attente et se rend en refus réessayable.

## Le relais d'upload

Le fichier **transite par le serveur** (choix produit). L'agent appelle `pennylane_firm_upload_url(company_id,
parent_folder_id, name)` ; le poste envoie `curl -F 'file=@<chemin>' '<url>'`, sans jeton ; le serveur dépose
le fichier avec le jeton de cabinet du coffre et rend l'objet fichier créé par Pennylane.

- **Route** `POST /api/relay/{token}` (`api/pennylane_firm.py`), SŒUR de `/api/upload/{token}` et non la même :
  celle-ci lit le corps en mémoire. Nature `NATURE`, aucun JWT, `ContratDeRoute` publié.
- **Jeton** : celui des uploads signés (`upload_tokens.sign/verify`, HMAC, 15 min, `jti` à usage unique) sous
  `typ="relay"` — un jeton d'upload oto ne s'ouvre pas ici, et inversement. Cible scellée : `company_id`,
  `parent_folder_id` (obligatoire : pas de dépôt à la racine), `name`, plus `sub` et `org`.
- **Ordre des contrôles**, du moins cher au plus cher, rien d'irréversible avant le dernier : signature, `typ`,
  expiration → garde d'identité du compte → **org scellée rejouée** (appartenance revérifiée, suspension,
  activation, jeton résolu sous CETTE org — sans quoi la cascade retomberait sur l'org « maison » du compte)
  → doublon → `Content-Length` présent (411) et sous le plafond (413) → place libre (503,
  lien non consommé) → consommation du `jti` (409) → lecture du multipart → relais.
- **Transit disque assumé** : le parseur de Starlette déborde sur disque au-delà d'un mégaoctet. Les octets sont
  comptés pendant la lecture et coupés au plafond ; le temporaire est fermé en `finally`, succès ou échec ; le
  fichier n'est jamais lu en entier, la lib l'envoie à Pennylane par morceaux.
- **Plafond** `OTO_PENNYLANE_FIRM_UPLOAD_MAX_BYTES` (100 Mo) — **provisoire**, la documentation de Pennylane ne
  donne pas sa limite ; à relever après mesure. Distinct de `OTO_MCP_UPLOAD_MAX_BYTES`.
- **Concurrence** `OTO_PENNYLANE_FIRM_RELAY_CONCURRENCY` (4 par processus), sans attente : au-delà, 503
  `relay_busy` + `Retry-After`, le même lien resservant.
- **Aucune connexion base pendant le transfert** : tout ce qui touche base et coffre est lu avant.
- **Journal** : une ligne `tool_calls` par dépôt tenté (`tool="pennylane_firm_relay"`, compte, org, cible,
  octets, id du fichier créé, durée), jamais un jeton.

## Le cache anti-doublon

Les NOMS d'un dossier, par (org, société, dossier), 15 min : rempli une fois par une lecture complète du dossier,
consulté à la frappe ET au relais, enrichi par chaque dépôt réussi ; un nom « en vol » est réservé (deux relais
du même nom ne partent pas tous les deux). Un 422 invalide l'entrée. Un dossier trop grand pour être lu en
entier ne conclut jamais « absent » : refus `duplicate_check_incomplete`.

⚠️ **Limite assumée** : un fichier ajouté dans l'interface de Pennylane pendant la vie d'une entrée n'est pas vu ;
le doublon passerait. Le cache est par processus.
