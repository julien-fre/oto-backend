## prerequisite — ta clé api signwell

crée une clé dans SignWell : **Settings → API → Create API key**, puis colle-la dans oto.
- byo-only : pas de clé oto partagée, et ce n'est pas un choix de catalogue — une clé SignWell **agit au nom du compte qui l'a créée**. C'est ce nom qui figure sur les invitations et dans la piste d'audit du document signé : pose celle du compte au nom duquel les documents doivent partir.
- envoyée en en-tête `X-Api-Key`. Le mode test de l'API (`test_mode`) est gratuit et sans valeur juridique.

## usage — envoyer un document à signer, suivre, récupérer le pdf

cinq tools, verbe en `op` :
- « envoie ce NDA à signer » → `signwell_document(op="create", files=[{name, file_url}], recipients=[{id:"1", name, email}], text_tags=True)` crée un **brouillon** ; relis-le, puis `signwell_document(op="send", document_id=…)`
- « un fichier du projet » → `oto_project_files(op="list")` rend un `download_url` signé : c'est le `file_url` à passer
- « où en est la signature ? » → `signwell_document(op="get", document_id=…)` — statut, et pour chaque destinataire son `signing_link`, s'il a reçu un courriel (`emailed`) et où il en est
- « relance-le » → `signwell_document(op="remind", document_id=…)`
- « donne-moi le PDF signé » → `signwell_document(op="completed_pdf", document_id=…, audit_page=True)`
- « un lien à envoyer moi-même, sans courriel SignWell » → `embedded_signing=True` à la création : chaque destinataire reçoit un `signing_link` ouvrable dans un navigateur
- « depuis mon modèle » → `signwell_template(op="create_document", template_id=…, recipients=[{id, placeholder_name, name, email}])`
- « un envoi par ligne d'un fichier » → `signwell_bulk_send(op="csv_template")`, remplir, `op="validate_csv"`, puis `op="create"` (aperçu tant que `dry_run=False` n'est pas passé)
- « préviens mon système quand c'est signé » → `signwell_webhook(op="create", callback_url="https://…")`

placer les champs dans le fichier (text tags) : `oto_guide op=read slug="signwell-envoi"`.

## note — ce que les outils font autrement que l'api

- **créer ne veut pas dire envoyer.** Chez SignWell, `POST /documents` ENVOIE par défaut. Ici `op="create"` (et `signwell_template(op="create_document")`) crée un brouillon sauf `draft=False` explicite ; l'envoi est `op="send"`.
- **un seul lien par destinataire** : SignWell le rend sous `signing_url` ou `embedded_signing_url` selon le mode ; la vue le rend toujours sous `signing_link`, avec `emailed` qui dit si SignWell écrit à cette personne. `full=True` rend la charge brute.
- **le PDF signé se rend en lien**, jamais en octets. Ce lien est porteur : quiconque le détient télécharge le document.
- **pas de liste des documents** : l'API n'en a pas. Garde l'`id` rendu à la création — il ne se retrouve pas après coup.
- toute écriture accepte `dry_run=True` : validation identique, rien n'est écrit ni envoyé ; un changement de destinataires rend un vrai diff, un code d'accès n'est jamais réécrit en clair.

## note — relevé en live (2026-09-16), à connaître avant d'envoyer

- **le mode test n'écrit JAMAIS aux destinataires** : chaque invitation d'un document `test_mode` part au **titulaire du compte**, sujet préfixé `[TEST]`, avec le nom du destinataire prévu dans le corps. Un test « je n'ai rien reçu » se lit dans la boîte du titulaire.
- **`Sending` n'est pas un envoi** : le statut passe `Created → Sending → Sent`. Un document est resté en `Sending` plusieurs minutes sans que l'invitation parte, quand un document identique créé juste après est passé à `Sent`. Seul `Sent` (ou plus loin) atteste l'envoi.
- **les champs issus des text tags arrivent en différé** : la réponse de création peut porter zéro champ, un `get` quelques secondes plus tard les liste tous.
- **un lien de signature embarquée s'ouvre dans un simple navigateur**, pas seulement en iframe — et SignWell n'y vérifie pas l'adresse du signataire : quiconque a le lien peut signer. Pour une identité vérifiée, laisser SignWell envoyer l'invitation par courriel.
- avec `embedded_signing=True` et `send_email: false`, le titulaire a quand même reçu un avis « impossible d'envoyer ce document » pour une adresse de destinataire invalide : ne pas compter sur l'absence totale de courriel SignWell. Cause non établie.

## note — état de vérification

client et outils dérivés des définitions OpenAPI des pages de référence SignWell (lues le 2026-09-16), **et exercés en live le 2026-09-16** avec une vraie clé (compte Business), à travers les outils eux-mêmes :
- **exercé à travers les outils, conforme au code** : `signwell_account(op="me")` ; `signwell_document` — création d'un brouillon en test mode depuis un `.docx` en base64 avec text tags (15 champs détectés en différé), `get`, aperçus `dry_run` de `create`, `send` et `update_recipients` (vrai diff), `delete` ; `signwell_template` — `create` avec text tags et deux rôles, `get` (statut `Available`), `delete` ; `signwell_bulk_send(op="list")` et `op="csv_template"` (l'en-tête rendu est dérivé des rôles et des libellés de champs du modèle) ; `signwell_webhook(op="list")` ; les refus 401 (clé invalide) et 404.
- **exercé sur l'API directement, par les mêmes endpoints** (avant l'écriture des outils) : envoi réel d'un document, envoi en test mode, signature embarquée avec `send_email: false` — d'où les relevés de la note précédente.
- **le PDF signé d'un document PAS encore complété rend 404** (« Couldn't find the document requested »), pas un refus d'état : l'outil le dit plutôt que de laisser croire l'identifiant perdu.
- **forme d'un refus** : `{message, meta: {error, message, messages[]}}` — le motif utile est dans `meta.messages`.
- **non exercé, spec seul** (aurait envoyé de vrais courriels, ou demande un document complété) : `send` réel via l'outil, `remind`, `update_recipients` et `update_authentication` réels, le PDF signé et le certificat NOM-151 d'un document complété, `create_document` depuis un modèle, `validate_csv` et la création d'un envoi groupé, la création et la suppression d'un webhook, les applications API.
