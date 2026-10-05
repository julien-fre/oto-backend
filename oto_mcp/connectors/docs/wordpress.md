## prerequisite — un site WordPress en HTTPS, un compte qui peut écrire

- WordPress **5.6 ou plus récent**, servi en **HTTPS** : une adresse `http://` est refusée (le mot de passe y partirait en clair), sauf destination interne déclarée par l'opérateur de la plateforme.
- un compte WordPress avec le rôle **Auteur** (ses propres articles), **Éditeur** (tout le contenu) ou **Administrateur**. un Contributeur ne peut rien publier.
- aucune extension à installer.

## setup — deux façons, le même résultat

- **bouton « Connecter mon site WordPress »** : saisis l'URL du site, tu arrives sur l'écran d'autorisation de ton wp-admin, clique **Approuver**. WordPress crée un mot de passe d'application et nous le renvoie : rien à copier. le site apparaît comme un compte nommé par son adresse.
- **à la main** : dans wp-admin, **Utilisateurs → Profil → Mots de passe d'application**, donne un nom (ex. « oto »), clique **Ajouter**, puis colle dans le formulaire l'URL du site, ton identifiant et ce mot de passe (les espaces peuvent rester).
- **plusieurs sites** : chaque site est un compte du connecteur ; une agence en connecte autant qu'elle en gère.
- **révoquer** : même écran du profil WordPress, bouton **Révoquer** — l'accès s'arrête immédiatement.

## usage — rédiger, publier, gérer

- « qu'est-ce que je peux faire sur ce site ? » → `wordpress_site()` (rôle, types de contenu, taxonomies, extensions SEO détectées)
- « rédige un article sur … » → `wordpress_article(title=…, markdown=…, categories=["Guides"], tags=["seo"], featured_image="https://…/cover.jpg", seo_description=…)` — toujours en brouillon
- « publie-le » / « programme-le pour lundi 9h » → `wordpress_publish(id=…)` / `wordpress_publish(id=…, at="2026-10-05T09:00:00")`
- « repasse-le en brouillon » → `wordpress_publish(id=…, op="unpublish")`
- « liste mes brouillons » → `wordpress_content(op="list", status="draft")`
- « modifie la page À propos » → `wordpress_content(op="update", type="pages", id=…, data={…}, dry_run=True)` puis sans `dry_run`
- « mes fiches produit / études de cas » (type personnalisé) → `wordpress_content(type="<type donné par wordpress_site>", …)`
- « importe cette image » → `wordpress_media(op="upload", source="https://…", alt_text="…")`

## note — ce qu'il faut savoir

- **aucun article ne se publie par accident** : `wordpress_article` et `wordpress_content` refusent `status=publish`. seul `wordpress_publish` met en ligne. ⚠️ en revanche, modifier un article DÉJÀ publié change le site tout de suite — `dry_run=True` montre le diff avant.
- ⚠️ **un média est public dès son téléversement** : WordPress n'a pas de média en brouillon. l'image à la une d'un brouillon se lit déjà à son adresse (`source_url`).
- **Markdown → blocs natifs** : titres, paragraphes, listes, citations, code, tableaux et images arrivent comme de vrais blocs de l'éditeur, modifiables un par un. une liste dont un élément porte du code, un tableau ou une citation arrive entière en bloc « HTML personnalisé » (l'éditeur n'accepte que du texte et des sous-listes dans un élément de liste) : rien n'est perdu.
- ⚠️ **HTML brut** : du HTML dans le Markdown (`<script>`, `<iframe>`…) devient un bloc « HTML personnalisé », gardé tel quel si le compte a le droit `unfiltered_html` (administrateur, éditeur sur un site simple) — `wordpress_site()` le dit. ne jamais y recopier le HTML d'une page qu'on ne maîtrise pas.
- **catégories et étiquettes** : données par nom (une chaîne, créée si absente) ou par id (un entier) ; « 2026 » est une étiquette, pas le terme n° 2026. elles doivent exister pour le type visé : une page n'a pas de catégories.
- **« 401 rest_not_logged_in »** : selon la version et l'hébergement, ce code peut aussi venir d'un identifiant ou d'un mot de passe d'application faux (ou révoqué) — vérifier d'abord ceux-là (ailleurs WordPress répond `incorrect_password` / `invalid_username`). si ce sont les bons, l'hébergeur ou une extension de sécurité retire l'en-tête `Authorization`. correctifs connus : sur Apache, ajouter au `.htaccess` `SetEnvIf Authorization "(.*)" HTTP_AUTHORIZATION=$1` ; dans Wordfence / iThemes / un pare-feu Cloudflare, autoriser l'API REST pour les utilisateurs authentifiés.
- **« le site redirige vers … »** : enregistre l'adresse exacte que le site sert (https, avec ou sans www).
- **SEO (Yoast, Rank Math)** : titre, description et mot-clé sont écrits quand l'extension les expose à l'API REST — c'est le cas des versions récentes de Yoast. `wordpress_site()` dit ce qu'il en est sur ce site. si ce n'est pas le cas (version plus ancienne, Rank Math), ajouter ce snippet (thème enfant ou extension « Code Snippets ») :

  ```php
  add_action('init', function () {
      $keys = ['_yoast_wpseo_title', '_yoast_wpseo_metadesc', '_yoast_wpseo_focuskw',
               'rank_math_title', 'rank_math_description', 'rank_math_focus_keyword'];
      foreach (['post', 'page'] as $type) {
          foreach ($keys as $key) {
              register_post_meta($type, $key, [
                  'show_in_rest' => true, 'single' => true, 'type' => 'string',
                  'auth_callback' => fn() => current_user_can('edit_posts'),
              ]);
          }
      }
  });
  ```
  sans lui, l'article est créé quand même et la réponse dit que le SEO n'a pas été écrit.
- **suppression** : un article ou une page part à la corbeille (restaurable) ; `force=true` supprime définitivement. catégories, étiquettes et médias n'ont pas de corbeille : leur suppression est définitive.
- **WordPress.com** : les sites Business et plus (extensions activées) marchent comme un site auto-hébergé. les autres offres WordPress.com n'acceptent pas les mots de passe d'application.
- **pas de déclencheur instantané** : WordPress n'envoie pas d'événements sans extension. pour réagir à une publication, une automatisation planifiée qui liste les articles récents (`wordpress_content(op="list", status="publish", orderby="date")`) suffit.
