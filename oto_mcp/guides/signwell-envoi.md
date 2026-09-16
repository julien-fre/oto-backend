---
title: Envoyer un document à signer avec SignWell (signwell_document)
description: text tags, ordre de signature, mode test, lien embarqué vs courriel — à lire avant de préparer un document à signer
---

# Envoyer un document à signer avec SignWell

Les docstrings de `signwell_document` restent courtes ; ce guide porte ce qui fait
échouer un envoi en silence.

## Le déroulé sûr

1. **Le fichier** : `files=[{name, file_url}]` (URL publique ou `download_url` signé
   d'un fichier de projet) ou `[{name, file_base64}]`. PDF et `.docx` passent.
2. **Les destinataires** : `recipients=[{id: "1", name, email}, {id: "2", …}]`. L'`id`
   est à toi ; c'est le **numéro de signataire** des champs et des text tags.
3. **Les champs** : soit `fields` (coordonnées x/y/page), soit `text_tags=True` et des
   balises écrites dans le fichier (ci-dessous). Un document envoyé sans aucun champ
   est refusé.
4. **Créer** : `op="create"` rend un **brouillon** — rien n'est parti. Relis la vue :
   `fields_count` (quelques secondes après, les text tags sont détectés en différé),
   destinataires, `emailed`.
5. **Envoyer** : `op="send", document_id=…`. Vérifie ensuite `status == "Sent"` ;
   `Created` et `Sending` ne sont pas des envois.

## Text tags

Une balise `{{…}}` écrite dans le document devient un champ. SignWell **ne retire pas
le texte de la balise** : écris-la dans la couleur du fond (blanc sur blanc). La
largeur du champ est celle du texte de la balise — ajoute des espaces pour l'élargir.

Options séparées par `:`, **dans cet ordre** ; une option sautée reste vide :

| ordre | option | valeurs |
|---|---|---|
| 1 | type | `text`, `signature` (`s`), `initial` (`i`), `date` (`d`), `check` (`c`), `autofill_name` (`af_n`), `autofill_email` (`af_e`), `autofill_company` (`af_c`), `autofill_title` (`af_t`), `autofill_date_signed` (`af_d_s`)… |
| 2 | signataire | `1`, `2`… = l'`id` du destinataire, dans l'ordre de `recipients` |
| 3 | requis | `y` / `n` |
| 4 | libellé | texte (champs `text` et `date`) |
| 5 | valeur pré-remplie | texte ; `1`/`0` pour une case |
| 6 | api_id | identifiant du champ |
| 7-8 | largeur, hauteur | en pixels |
| 9 | `text` : validation · `date` : date de signature verrouillée | `numbers`, `email_address`… · `y` |
| 10 | `text` : largeur fixe · `date` : format | `y` · `dd/mm/yyyy`, `mm/dd/yyyy`, `yyyy/mm/dd` |

Exemples : `{{signature:1:y}}` · `{{text:1:y:Raison sociale}}` ·
`{{date:2:y:Date: : : : :y:dd/mm/yyyy}}` (date de signature du signataire 2,
verrouillée ; les options 5 à 8 sont laissées vides).

## Ordre de signature

`apply_signing_order=True` : les destinataires signent un par un, dans l'ordre de
`recipients`. Le suivant n'est invité qu'une fois le précédent passé. En signature
embarquée, personne n'est prévenu que c'est son tour : son `signing_link` est à lui
transmettre.

## Courriel SignWell ou lien à transmettre soi-même

- **Par défaut**, SignWell écrit à chaque destinataire et vérifie son adresse : c'est
  la piste d'audit la plus solide.
- **`embedded_signing=True`** : aucun courriel sauf `send_email: true` sur le
  destinataire ; chaque destinataire a un `signing_link` qui s'ouvre dans un simple
  navigateur. **Quiconque détient le lien peut signer** — le transmettre à la seule
  personne visée.

## Mode test

`test_mode=True` : gratuit, sans valeur juridique, et **aucune invitation ne part aux
destinataires** — SignWell les envoie au titulaire du compte, sujet `[TEST]`. Pour
voir le parcours du destinataire, ouvre son `signing_link`.

## Après la signature

- `op="get"` : `status` du document et de chaque destinataire.
- `op="completed_pdf", audit_page=True` : lien vers le PDF signé avec sa page d'audit
  (lien porteur).
- Pas de liste des documents dans l'API : garde l'`id` dans le projet.
