## prerequisite — a WordPress site on HTTPS, an account that can write

- WordPress **5.6 or newer**, served over **HTTPS**: an `http://` address is refused (the password would travel in clear), unless it is an internal destination declared by the platform operator.
- a WordPress account with the **Author** role (own posts), **Editor** (all content) or **Administrator**. A Contributor cannot publish anything.
- no plugin to install.

## setup — two ways, same result

- **"Connect my WordPress site" button**: enter the site URL, you land on the authorization screen of your wp-admin, click **Approve**. WordPress creates an application password and sends it back to us: nothing to copy. the site appears as an account named after its address.
- **by hand**: in wp-admin, **Users → Profile → Application Passwords**, give it a name (e.g. "oto"), click **Add**, then paste the site URL, your username and that password into the form (the spaces can stay).
- **multiple sites**: each site is an account of the connector; an agency connects as many as it manages.
- **revoke**: same WordPress profile screen, **Revoke** button — access stops immediately.

## usage — write, publish, manage

- "what can I do on this site?" → `wordpress_site()` (role, content types, taxonomies, detected SEO plugins)
- "write a post about …" → `wordpress_article(title=…, markdown=…, categories=["Guides"], tags=["seo"], featured_image="https://…/cover.jpg", seo_description=…)` — always as a draft
- "publish it" / "schedule it for Monday 9am" → `wordpress_publish(id=…)` / `wordpress_publish(id=…, at="2026-10-05T09:00:00")`
- "put it back to draft" → `wordpress_publish(id=…, op="unpublish")`
- "list my drafts" → `wordpress_content(op="list", status="draft")`
- "edit the About page" → `wordpress_content(op="update", type="pages", id=…, data={…}, dry_run=True)` then without `dry_run`
- "my product sheets / case studies" (custom type) → `wordpress_content(type="<type given by wordpress_site>", …)`
- "import this image" → `wordpress_media(op="upload", source="https://…", alt_text="…")`

## note — what to know

- **no post is published by accident**: `wordpress_article` and `wordpress_content` refuse `status=publish`. only `wordpress_publish` puts it live. ⚠️ however, editing a post that is ALREADY published changes the site right away — `dry_run=True` shows the diff first.
- ⚠️ **media is public as soon as it is uploaded**: WordPress has no draft media. the featured image of a draft can already be read at its address (`source_url`).
- **Markdown → native blocks**: headings, paragraphs, lists, quotes, code, tables and images arrive as real editor blocks, editable one by one. a list with an item that contains code, a table or a quote arrives whole as a "Custom HTML" block (the editor only accepts text and sub-lists inside a list item): nothing is lost.
- ⚠️ **raw HTML**: HTML in the Markdown (`<script>`, `<iframe>`…) becomes a "Custom HTML" block, kept as is if the account has the `unfiltered_html` capability (administrator, editor on a single site) — `wordpress_site()` says so. never paste in the HTML of a page you don't control.
- **categories and tags**: given by name (a string, created if missing) or by id (an integer); "2026" is a tag, not term no. 2026. they must exist for the target type: a page has no categories.
- **"401 rest_not_logged_in"**: depending on the version and hosting, this code can also come from a wrong (or revoked) username or application password — check those first (elsewhere WordPress answers `incorrect_password` / `invalid_username`). if they are right, the host or a security plugin strips the `Authorization` header. known fixes: on Apache, add to `.htaccess` `SetEnvIf Authorization "(.*)" HTTP_AUTHORIZATION=$1`; in Wordfence / iThemes / a Cloudflare firewall, allow the REST API for authenticated users.
- **"the site redirects to …"**: save the exact address the site serves (https, with or without www).
- **SEO (Yoast, Rank Math)**: title, description and keyword are written when the plugin exposes them to the REST API — this is the case for recent Yoast versions. `wordpress_site()` says how it stands on this site. if it is not the case (older version, Rank Math), add this snippet (child theme or "Code Snippets" plugin):

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
  without it, the post is still created and the response says the SEO was not written.
- **deletion**: a post or page goes to the trash (restorable); `force=true` deletes permanently. categories, tags and media have no trash: deleting them is permanent.
- **WordPress.com**: Business plans and above (plugins enabled) work like a self-hosted site. other WordPress.com plans do not accept application passwords.
- **no instant trigger**: WordPress sends no events without a plugin. to react to a publication, a scheduled automation that lists recent posts (`wordpress_content(op="list", status="publish", orderby="date")`) is enough.
