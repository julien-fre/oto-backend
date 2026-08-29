# Email — envoi per-org, par connecteur

> Extrait du CLAUDE.md (refactor 2026-07-02) — domicile du détail ; le CLAUDE.md garde le résumé + pointeur.


Envoi d'email modélisé **par connecteur** (la config/gestion email s'exprime comme
celle d'un connecteur, pas une page à part). **Deux connecteurs** (déclarés dans `providers/scaleway.py` et
`providers/resend.py` ; le routage expéditeur→transport reste
`providers.EMAIL_CONNECTOR_TRANSPORT`) :
`scaleway` (**BYO-org depuis le 2026-07-01** : `auth_modes={byo_org}`,
`secret_kind="fields"` — `secret_key`+`project_id`+`region` du compte Scaleway TEM
de L'ORG ; transport = API TEM en direct `email.send_via_scaleway_tem`, plus de
service mailer ni de clé plateforme ; master ON **sûr** car la propriété du domaine
est garantie PAR Scaleway — l'API refuse un `from` dont le domaine n'est pas vérifié
dans le compte de l'org, ce qui rend #64 caduque) + `resend` (BYOK,
`auth_modes={byo_org}`). **Le transport DÉRIVE du connecteur** :
`providers.EMAIL_CONNECTOR_TRANSPORT={scaleway:scaleway, resend:resend}` (pas de
champ transport sur l'expéditeur).

- `email_send` (`tools/email.py`) = **spine** (pas un connecteur) : route
  `sender→connecteur→transport` ; autz dynamique (membre d'org pour une adresse
  déclarée ; super_admin pour le repli marque `oto@otomata.tech`). `email.py` =
  `send_composed_email` (mailer.oto.zone, env `OTO_MAILER_SEND_BEARER`) +
  `send_via_resend` (httpx direct, clé org). `scaleway`/`resend` = providers
  credential/config-only (`tools/{scaleway,resend}.py` = `register()` no-op).
- **Config = `orgs.email_settings` JSONB keyé PAR CONNECTEUR** :
  `{<connector>:{senders:[{email,name?,reply_to?}], quiet_hours?}}` (calqué sur
  `field_filters`). `org_store.get/set_org_email_settings(org, connector)`,
  `resolve_sender(org, from)→(sender, connector)`, `org_email_quiet_hours`. Capacité
  `orgs_email_settings` : GET bundle + `PUT /api/orgs/{id}/email-settings/{connector}`.
- **Envoi différé** : params `send_at`/`force_now` + garde-fou **quiet hours par
  connecteur** (défaut Europe/Paris 20h–8h). `scheduler.py` : `compute_scheduled_at`
  (pure, testée) + boucle asyncio démarrée via le lifespan (`server.py`), batch isolé
  en `asyncio.to_thread` (ne bloque pas l'event loop) ; table `scheduled_emails`
  (claim `FOR UPDATE SKIP LOCKED`, retry ×3). Gestion : `oto_list/cancel_scheduled_emails`.
- **Vérif de domaine d'envoi = déléguée au provider** (les deux connecteurs sont
  BYO) : Scaleway TEM comme Resend refusent un `from` hors domaine vérifié dans le
  compte de l'org → pas de vérif côté oto (#64 sans objet depuis le passage BYO).
  Otomata (org 2) envoie avec sa clé TEM dédiée (app IAM `oto-email-scaleway`,
  vault `SCW_TEM_*`).

> **Invariant connecteurs (corrigé 2026-06-24)** : `_org_list` (vue ORG
> `/org/connectors`) ne liste QUE les connecteurs **activés par la plateforme**
> (master ON, ou forcé par l'override d'org), comme la surface USER
> (`_visible_catalog`). Master-OFF non accordé → invisible (fin du levier inerte
> « coupé par la plateforme »). Filtre sur le **cap master**, pas sur `effective`
> (un override OFF d'org doit rester réactivable).

## Front qui héberge l'org (invitations, 07/08)

> **Front qui héberge l'org (invitations, 07/08).** oto-backend sert plusieurs produits
> depuis une instance (oto, Tulina) : deux colonnes `orgs.front_base_url` / `front_brand`
> (NULL = oto) portent le front d'une org, lues par `emit_invitation` — base du lien
> `/invitation/<code>`, marque du texte du mail, **et pas de magic-link** dès qu'un front
> tiers est posé (l'OTT est minté sur NOTRE Logto : il serait inerte sur l'émetteur dédié
> du tiers, soit un échec de connexion silencieux). **Dérivé de l'org CIBLE, jamais déclaré
> par l'appelant** — sinon c'est un champ d'API publique (REST + surface MCP) qu'il faudra
> retirer à l'arrivée de l'étage tenant (ADR 0052, où ces colonnes remontent d'un cran), et
> une invitation pourrait prétendre venir d'un front auquel l'org n'appartient pas. Les 3
> niveaux de la cascade en héritent sans rien porter. La marque s'arrête au TEXTE :
> l'expéditeur reste `_MAIL_FROM`, un domaine d'envoi tiers supposerait sa vérification TEM.
> ⚠️ Aucune surface n'édite ces colonnes (UPDATE à la main) : une nouvelle org sous front
> tiers naît donc sous marque oto tant que personne ne la renseigne.

## Une image en tête d'un `email_send` (2026-08-29)

**Par où une image arrive-t-elle à une URL publique stable ? Par `oto_upload_url(target="image")`.**
Avant ce lot, aucun chemin MCP n'y menait : `target='project_file'` dépose un blob
**privé** durable (l'agent n'en reçoit qu'une `download_url` signée qui expire) ; la
bascule publique d'un fichier de projet (`POST /api/me/projects/{p}/files/{f}/public`)
est **REST-only**, sans face MCP, et concerne un « Autre document » ; et
`media_store.upload_image` (public-read, clé par hash de contenu, 2 Mo, type par magic
bytes) n'était branchée que sur l'avatar et le logo d'org, en multipart REST. La cible
`image` est la **plus petite exposition** de cette fonction, choisie contre une entrée
`image={kind: drive|url}` sur `email_send` : celle-ci aurait ré-uploadé le visuel **à
chaque envoi** — or le même visuel ressert (trois mails d'onboarding, des annonces).
**Un upload, une URL, réutilisée d'envoi en envoi.**

Ce que la cible `image` garantit (`upload_tokens.py`, `media_store.upload_image`) :
- **porteur authentifié** : le sub est scellé dans le jeton signé (même régime que
  l'avatar) ; aucune ressource cible, donc aucune autre autz à réappliquer ;
- **2 Mo max** (`OTO_MCP_S3_MAX_IMAGE_BYTES`), et c'est cette borne que le mint annonce
  dans `max_bytes` — pas le plafond générique de 25 Mo ;
- **png / jpeg / gif / webp seulement, reconnus aux octets** : le `Content-Type` déclaré
  (curl, formulaire) n'est jamais cru ;
- **clé non devinable** : `images/<sub>/<sha256[:32]>.<ext>` — 128 bits qu'on ne retrouve
  qu'en possédant l'image ; ré-uploader le même fichier rend la même URL (idempotent) ;
- **l'accusé rend `url`** (publique, permanente, `Cache-Control: immutable`) — et la page
  d'upload humaine (claude.ai sans shell) l'affiche aussi, sinon le dépôt serait un
  succès dont personne ne peut rien faire.

Ce que le gabarit impose (`email.render_composed_email`, `_image_html`) :
- **une seule image, avant le corps** — pas de galerie, pas d'image par section, pas de
  pièce jointe ;
- **`image_alt` REQUIS** avec `image_url`, et refusé sans elle : beaucoup de clients
  bloquent les images, le mail doit garder son sens ; aucun texte par défaut (il ne
  dirait rien) ;
- **`https://` seul** (un `http://` est bloqué ou marqué « non sécurisé », un `data:`
  n'est pas une URL publique) ;
- **largeur utile 480 px** : `width="480"` (lu par les clients qui ignorent le CSS) +
  `max-width:100%; height:auto; display:block` (affichage réduit) ;
- **URL et alt échappés en attribut, guillemets compris** (`html.escape(quote=True)` —
  `_esc` ne traite pas `"`, et un `"` dans l'alt refermerait l'attribut) ; le `href`
  du bouton (`cta_url`, fourni par l'agent lui aussi) est échappé de la même façon
  depuis ce lot — c'était le même trou ;
- **sans image, le rendu est celui d'avant à l'octet** (golden dans
  `tests/test_email_image.py`).

L'appel complet, tel que le couvrent `tests/test_upload_image_public.py` et
`tests/test_email_image.py` :

```
# 1. publier le visuel UNE fois (agent avec shell)
oto_upload_url(target="image")
  → {url: "https://mcp.oto.cx/api/upload/<jeton>", method: "PUT", max_bytes: 2097152, …}
curl -X PUT --data-binary @hero.png 'https://mcp.oto.cx/api/upload/<jeton>'
  → {"ok": true, "kind": "image", "url": "https://<bucket>/images/<sub>/<hash>.png", "bytes": 48213}
#    (sans shell : transmettre l'URL d'upload à l'humain, la page affiche l'URL publique)

# 2. relire, puis envoyer — la même `url` sert à chaque mail
email_send(to="…", subject="bienvenue sur oto", body="…",
           image_url="https://<bucket>/images/<sub>/<hash>.png",
           image_alt="l'écran d'accueil d'oto : vos connecteurs, prêts à l'emploi",
           cta_text="ouvrir oto", cta_url="https://manage.oto.cx", dry_run=True)
email_send(…, dry_run=False)          # ou send_at=… : la file porte le HTML avec l'image
```

Refus explicites, jamais de repli : `image_url` sans `image_alt`, `image_alt` sans
`image_url`, une URL qui ne commence pas par `https://`, un fichier de plus de 2 Mo, un
fichier qui n'est pas une image. Laissé de côté, volontairement : plusieurs images,
l'image par section, la pièce jointe, et une entrée `image={kind: drive|url}` résolue
côté serveur par `file_source` (un upload par envoi — la mauvaise forme pour un visuel
qui ressert).
