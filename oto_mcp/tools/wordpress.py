"""WordPress — articles, pages, contenus personnalisés, médias, taxonomies, via
l'API REST cœur (`wp/v2`). Wrappe `oto.tools.wordpress.WordPressClient`.

Credential multi-champs (`site_url`, `username`, `application_password`),
résolu par appel via `access.resolve_credential_fields("wordpress")`, `site_url`
passé à `_guard` (garde d'egress, puis HTTPS) AVANT toute
construction de client (le site est déclaré par l'utilisateur : c'est exactement
la forme que la garde existe pour refuser quand elle vise l'intérieur).
Multi-compte : un compte = un site.

⚠️ **Aucun article ni aucune page ne devient public sans `wordpress_publish`.**
`wordpress_content` et `wordpress_article` refusent `status=publish|future` : la
frontière « rien ne bouge » / « c'est en ligne » reste dans le NOM de l'outil
(même choix que `webflow_publish`), jamais un paramètre parmi d'autres.
⚠️ **Un média, lui, est public dès son téléversement** (son `source_url` se lit
sans compte), image à la une d'un brouillon comprise : WordPress n'a pas de
média en brouillon. Les descriptions le disent.

`wordpress_article` est le chemin composé — Markdown converti en blocs natifs
de l'éditeur (`wordpress_blocks`), catégories/étiquettes données par NOM
(créées si absentes), image à la une importée depuis une source oto, champs SEO
écrits quand le site le permet. C'est le chemin qu'un agent réussit ; les
outils unitaires restent pour le reste.

Le flux « Connecter » (écran d'autorisation natif de WordPress) vit dans
`auth/wordpress.py` ; il pose le MÊME credential que le formulaire.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Literal, Optional, Union

import requests
from fastmcp import FastMCP
from mcp.types import ErrorData, INVALID_PARAMS

from oto.tools.common.errors import UpstreamHTTPError

from .. import access, egress
from ..connectors import verify as connector_verify
from ..mcp_errors import McpError
from . import wordpress_blocks

if TYPE_CHECKING:
    from oto.tools.wordpress import WordPressClient

logger = logging.getLogger(__name__)

# Statuts qui rendent un contenu public — réservés à `wordpress_publish`.
_LIVE_STATUSES = {"publish", "future"}
# Types internes de l'éditeur : servis en REST, jamais du contenu à rédiger.
_INTERNAL_TYPES = frozenset({
    "attachment", "wp_block", "wp_template", "wp_template_part", "wp_navigation",
    "wp_font_family", "wp_font_face", "wp_global_styles", "nav_menu_item"})
# Ce qu'une liste rend par élément : de quoi choisir, pas le contenu entier.
_LIST_KEYS = ("id", "status", "date", "modified", "slug", "link", "type", "parent")

# Clés méta SEO connues, par extension. Écrites par `meta` UNIQUEMENT si le site
# les expose en REST (schéma de la route) — sinon WordPress les ignore EN SILENCE.
_SEO_META = {
    "yoast": {"title": "_yoast_wpseo_title", "description": "_yoast_wpseo_metadesc",
              "focus_keyword": "_yoast_wpseo_focuskw"},
    "rankmath": {"title": "rank_math_title", "description": "rank_math_description",
                 "focus_keyword": "rank_math_focus_keyword"},
}


def _bad(msg: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=msg))


def _need(value, name: str, op: str):
    if value is None or value == "":
        raise _bad(f"op='{op}' requiert {name}")
    return value


def _wp_code(e: UpstreamHTTPError) -> str:
    return e.body.get("code", "") if isinstance(e.body, dict) else ""


def _wp_message(e: UpstreamHTTPError) -> str:
    if isinstance(e.body, dict):
        return str(e.body.get("message") or e.body)[:300]
    return str(e.body)[:300]


def _translate(e: Exception) -> McpError:
    """Erreur amont → message actionnable. Le `code` WordPress (`rest_*`) est
    lu, jamais deviné sur le texte."""
    from oto.tools.wordpress import (WordPressMediaFieldsError, WordPressRateLimited,
                                     WordPressRedirect)

    if isinstance(e, McpError):
        return e
    if isinstance(e, WordPressMediaFieldsError):
        # Le média EXISTE (et il est public) : le renvoyer réessayer créerait un doublon.
        return _bad(f"média {e.media_id} téléversé (déjà public), mais son texte "
                    f"alternatif / sa légende / son titre n'ont pas été posés "
                    f"(HTTP {e.status_code}) — complète-le avec `wordpress_media "
                    f"op=update id={e.media_id}`, ne le téléverse pas à nouveau.")
    if isinstance(e, WordPressRateLimited):
        wait = (f"réessaie dans {int(e.retry_after)} s" if e.retry_after is not None
                else "réessaie plus tard")
        return _bad(f"le site WordPress limite le débit (429) — {wait}.")
    if isinstance(e, (WordPressRedirect, ValueError)):   # EgressRefused ⊂ ValueError
        return _bad(str(e))
    if isinstance(e, (requests.ConnectionError, requests.Timeout)):
        return _bad("site WordPress injoignable (connexion ou délai) — vérifie l'URL "
                    "du site, ou réessaie dans un moment.")
    if isinstance(e, UpstreamHTTPError):
        code, msg = _wp_code(e), _wp_message(e)
        if e.status_code == 401 and code == "rest_not_logged_in":
            # Ambigu par construction : WordPress rend ce même code pour un
            # identifiant/mot de passe faux ET pour un en-tête Authorization retiré
            # en route (constaté sur un WordPress local). On nomme les deux.
            return _bad(
                "WordPress ne t'a pas reconnu (401 rest_not_logged_in) : soit "
                "l'identifiant ou le mot de passe d'application est faux (ou révoqué), "
                "soit l'hébergeur / une extension de sécurité retire l'en-tête "
                "Authorization. Voir la fiche du connecteur (section note).")
        if e.status_code == 401:
            return _bad(f"identifiant ou mot de passe d'application refusé (401 {code}) : "
                        f"{msg}")
        if e.status_code == 403:
            return _bad(f"droits WordPress insuffisants pour ce compte (403 {code}) : {msg}")
        if e.status_code == 404 and code == "rest_no_route":
            return _bad("cette route n'existe pas sur le site (404 rest_no_route) — type "
                        "de contenu non exposé en REST, ou extension absente.")
        if e.status_code == 404:
            return _bad(f"introuvable (404 {code}) : {msg}")
        if e.status_code >= 500:
            return _bad(f"le site WordPress est en erreur (HTTP {e.status_code}) — "
                        f"ce n'est pas ton entrée ; réessaie plus tard. {msg}".rstrip())
        return _bad(f"WordPress a refusé la requête (HTTP {e.status_code} {code}) : {msg}")
    raise e


def _run(fn):
    try:
        return fn()
    except McpError:
        raise
    except Exception as e:  # noqa: BLE001 — traduit, ou re-levé tel quel par _translate
        raise _translate(e) from e


def _strip(item) -> dict:
    """Retire `_links` (lourd, sans valeur pour un agent)."""
    if isinstance(item, dict):
        return {k: v for k, v in item.items() if k != "_links"}
    return item


def _rendered(v):
    if isinstance(v, dict):
        return v.get("raw", v.get("rendered"))
    return v


def _slim(item: dict, keys=_LIST_KEYS) -> dict:
    """Ce qu'une LISTE rend par élément : de quoi choisir, jamais le contenu entier
    (un article complet par ligne serait payé à chaque tour). `get` rend le reste."""
    out = {k: item.get(k) for k in keys if k in item}
    for k in ("title", "name"):
        if k in item:
            out[k] = _rendered(item[k])
    return out


def _diff(current: dict, changes: dict) -> dict:
    """`{champ: {"from", "to"}}` pour les champs qui CHANGENT réellement —
    comparés sur la forme brute (`raw`), jamais sur le HTML rendu."""
    out = {}
    for k, new in changes.items():
        old = _rendered(current.get(k))
        if old != new:
            out[k] = {"from": old, "to": new}
    return out


def _guard(site_url: str) -> bool:
    """Avant toute construction de client : garde d'egress, puis HTTPS
    (`auth.wordpress.http_allowed`). Rend `allow_http` pour le client."""
    from ..auth.wordpress import http_allowed

    egress.check_url(site_url, connector="wordpress", field="site_url")
    return http_allowed(site_url)


def _verify(fields: dict, config: dict | None = None) -> None:
    """Sonde « tester la connexion » — `GET wp/v2/users/me?context=edit`.

    Authentifiée, sans effet de bord, gratuite. `context=edit` n'est servi qu'à
    un utilisateur identifié : un 401 dit donc bien « ce mot de passe ne passe
    pas » (ou l'en-tête est retiré par l'hébergeur — même geste : corriger côté
    site), jamais une limite de la sonde."""
    from oto.tools.wordpress import WordPressClient

    allow_http = _guard(fields.get("site_url") or "")
    try:
        me = WordPressClient(fields["site_url"], fields["username"],
                             fields["application_password"], allow_http=allow_http).me()
    except UpstreamHTTPError as e:
        if e.status_code == 401:
            raise connector_verify.NonAutorise(_translate(e).error.message) from e
        raise
    if not me.get("id"):
        raise RuntimeError(f"réponse inattendue de users/me : {str(me)[:200]}")


def _client(account: Optional[str] = None) -> WordPressClient:
    from oto.tools.wordpress import WordPressClient

    creds = access.resolve_credential_fields("wordpress", account)
    site = creds.get("site_url") or ""
    try:
        allow_http = _guard(site)
        return WordPressClient(site, creds.get("username") or "",
                               creds.get("application_password") or "",
                               allow_http=allow_http)
    except ValueError as e:   # garde d'egress, HTTP, URL ou identifiants absents
        raise _bad(f"credential WordPress inutilisable : {e}") from e


# --- résolution des types / taxonomies ---------------------------------------

def _route_for(entries: dict, key: str, kind: str) -> str:
    """`posts`/`post`/`product`/`book` → `wp/v2/posts`. Accepte le slug OU le
    `rest_base` ; un type absent du REST est nommé avec la liste disponible."""
    key = (key or "").strip()
    for slug, t in entries.items():
        base = t.get("rest_base")
        if not base:
            continue
        if key in (slug, base):
            return f"{t.get('rest_namespace') or 'wp/v2'}/{base}"
    dispo = sorted(t.get("rest_base") for s, t in entries.items()
                   if t.get("rest_base") and s not in _INTERNAL_TYPES
                   and "(" not in t.get("rest_base"))
    raise _bad(f"{kind} « {key} » introuvable en REST sur ce site. Disponibles : {dispo}.")


def _type_route(c, type_: str) -> str:
    if type_ in ("posts", "post"):
        return "wp/v2/posts"
    if type_ in ("pages", "page"):
        return "wp/v2/pages"
    return _route_for(_run(c.types), type_, "type de contenu")


def _type_taxonomies(c, type_: str) -> list:
    """Les taxonomies que CE type accepte (`category`, `post_tag`, une personnalisée…),
    lues sur le site : une page n'a pas de catégories, un type personnalisé a les
    siennes."""
    key = (type_ or "").strip()
    for slug, t in _run(c.types).items():
        if key in (slug, t.get("rest_base")):
            return list(t.get("taxonomies") or [])
    raise _bad(f"type de contenu « {key} » introuvable en REST sur ce site.")


def _tax_route(c, taxonomy: str) -> str:
    if taxonomy in ("categories", "category"):
        return "wp/v2/categories"
    if taxonomy in ("tags", "post_tag"):
        return "wp/v2/tags"
    return _route_for(_run(c.taxonomies), taxonomy, "taxonomie")


def _refuse_live(data: dict, tool: str) -> None:
    if (data or {}).get("status") in _LIVE_STATUSES:
        raise _bad(f"{tool} ne publie pas (status={data['status']!r}) : le contenu "
                   "reste en brouillon, `wordpress_publish` le met en ligne.")


# --- SEO -----------------------------------------------------------------------

def _seo_plan(c, route: str, namespaces: list) -> dict:
    """Où écrire le SEO sur CE site : `{"plugin", "via", "keys"}` ou
    `{"plugin", "via": None, "reason"}`. Lit le schéma de la route (OPTIONS) :
    une clé méta non déclarée `show_in_rest` est ignorée sans erreur par
    WordPress — on ne l'écrit donc que si le schéma la porte."""
    plugin = ("yoast" if "yoast/v1" in namespaces
              else "rankmath" if "rankmath/v1" in namespaces else None)
    if plugin is None:
        return {"plugin": None, "via": None,
                "reason": "aucune extension SEO détectée (Yoast, Rank Math)."}
    try:
        schema, _ = c.request("OPTIONS", route)
    except (UpstreamHTTPError, requests.ConnectionError, requests.Timeout) as e:
        # Le schéma n'a pas pu être LU : on n'écrit pas, et on dit pourquoi — jamais
        # « champs non exposés », qui enverrait poser un snippet pour rien.
        return {"plugin": plugin, "via": None,
                "reason": f"schéma de la route illisible : {_translate(e).error.message}"}
    meta_props = (((schema or {}).get("schema") or {}).get("properties") or {}) \
        .get("meta", {}).get("properties", {}) or {}
    keys = _SEO_META[plugin]
    if all(k in meta_props for k in keys.values()):
        return {"plugin": plugin, "via": "meta", "keys": keys}
    return {"plugin": plugin, "via": None,
            "reason": (f"{plugin} est installé mais ses champs ne sont pas exposés en "
                       "écriture par l'API REST de ce site — le snippet à ajouter est "
                       "sur la fiche du connecteur (section note).")}


def _seo_meta(plan: dict, seo: dict) -> dict:
    if plan.get("via") != "meta":
        return {}
    return {plan["keys"][k]: v for k, v in seo.items() if v is not None}


# --- termes par nom -------------------------------------------------------------

def _resolve_terms(c, route: str, values: list, *,
                   create: bool) -> tuple[list, list, list]:
    """Noms ou ids → `(ids, à créer, créés)`. Un entier est un id, une chaîne est
    TOUJOURS un nom (« 2026 » est une étiquette, pas le terme n° 2026). Un nom absent
    est créé (`create=True`) ; en dry_run il est seulement annoncé. `term_exists`
    (course, casse) rend l'id existant."""
    ids, to_create, created = [], [], []
    for v in values or []:
        if isinstance(v, int) and not isinstance(v, bool):
            ids.append(v)
            continue
        name = str(v).strip()
        if not name:
            continue
        found = _run(lambda: c.list(route, search=name, per_page=100))["items"]
        match = next((t for t in found
                      if str(t.get("name", "")).strip().lower() == name.lower()), None)
        if match:
            ids.append(match["id"])
            continue
        if not create:
            to_create.append(name)
            continue
        try:
            new_id = c.create(route, {"name": name})["id"]
            ids.append(new_id)
            created.append({"name": name, "id": new_id})
        except UpstreamHTTPError as e:
            existing = (e.body.get("data") or {}).get("term_id") if isinstance(e.body, dict) else None
            if _wp_code(e) == "term_exists" and existing:
                ids.append(int(existing))
            else:
                raise _translate(e) from e
    return ids, to_create, created


def _upload(c, source: Union[str, dict], *, filename: Optional[str] = None,
            **fields) -> dict:
    from .. import file_source

    src = {"kind": "url", "url": source} if isinstance(source, str) else source
    try:
        rf = file_source.resolve(src)
    except file_source.FileSourceError as e:
        raise _bad(f"source de fichier illisible : {e}") from e
    return _run(lambda: c.upload_media(rf.data, filename or rf.filename, rf.mime, **fields))


def register(mcp: FastMCP) -> None:
    connector_verify.register("wordpress", _verify)

    @mcp.tool()
    def wordpress_site() -> dict:
        """What this WordPress site is and what this connection can do there —
        call it first. Returns the site (name, url, description), the connected
        user and their role/capabilities (an Author can only publish their own
        posts; `unfiltered_html` false means scripts/iframes are stripped), the
        content types and taxonomies exposed in REST (with the `type`/`taxonomy`
        value the other wordpress_* tools accept — custom post types included),
        and the detected plugins (SEO: Yoast/Rank Math and whether SEO fields are
        writable here; WooCommerce; ACF)."""
        c = _client()
        index = _run(c.index)
        me = _run(c.me)
        types = _run(c.types)
        taxes = _run(c.taxonomies)
        ns = index.get("namespaces") or []
        caps = me.get("capabilities") or {}
        return {
            "site": {k: index.get(k) for k in ("name", "description", "url", "home",
                                                "gmt_offset", "timezone_string")},
            "user": {"id": me.get("id"), "name": me.get("name"), "roles": me.get("roles"),
                     "can": {k: bool(caps.get(k)) for k in (
                         "publish_posts", "edit_others_posts", "publish_pages",
                         "upload_files", "manage_categories", "unfiltered_html")}},
            "types": [{"type": t.get("rest_base"), "slug": s, "name": t.get("name"),
                       "hierarchical": t.get("hierarchical"),
                       "taxonomies": t.get("taxonomies")}
                      for s, t in types.items()
                      if t.get("rest_base") and s not in _INTERNAL_TYPES],
            "taxonomies": [{"taxonomy": t.get("rest_base"), "slug": s, "name": t.get("name"),
                            "hierarchical": t.get("hierarchical"), "types": t.get("types")}
                           for s, t in taxes.items()
                           if t.get("rest_base") and s not in ("nav_menu", "wp_pattern_category")],
            "plugins": {
                "seo": _seo_plan(c, "wp/v2/posts", ns),
                "woocommerce": "wc/v3" in ns,
                "acf": any(n.startswith("acf/") for n in ns),
            },
        }

    @mcp.tool()
    def wordpress_content(
        op: Literal["list", "get", "create", "update", "delete"],
        type: str = "posts",
        id: Optional[int] = None,
        data: Optional[dict] = None,
        content_format: Literal["raw", "markdown"] = "raw",
        search: Optional[str] = None,
        status: Optional[str] = None,
        page: int = 1,
        per_page: int = 20,
        orderby: Optional[str] = None,
        order: Optional[Literal["asc", "desc"]] = None,
        force: bool = False,
        dry_run: bool = False,
    ) -> dict:
        """WordPress posts, pages and custom post types — list, read, create,
        edit, trash. For a blog article prefer `wordpress_article` (Markdown,
        categories by name, featured image, SEO in one call). No item goes live
        here: `status` publish/future is refused, use `wordpress_publish` —
        but editing an item that is ALREADY published changes it live (check
        with `dry_run` first). Raw HTML in the content (`<script>`, `<iframe>`,
        a `wp:html` block) is kept as written unless the account lacks
        `unfiltered_html` — never paste HTML from an untrusted page.
        `get` returns the editable form (`content.raw`, block markup). `delete`
        moves to the trash; `force=true` deletes permanently. `dry_run` on
        update/delete fetches the item and returns the real diff / what would be
        deleted, without writing.

        Args:
            op: list | get | create | update | delete.
            type: `posts`, `pages`, or a custom type from `wordpress_site`.
            id: the item — get/update/delete.
            data: create/update fields as WordPress names them (title, content,
                excerpt, slug, status draft|pending|private, categories, tags,
                featured_media, parent, meta, acf…). update sends only these keys.
            content_format: `markdown` converts `data.content` to native blocks
                (raw HTML inside it is kept as an HTML block).
            search: list — full-text filter.
            status: list — e.g. `draft`, `publish`, `draft,pending`, `any`.
            page: list — 1-based page.
            per_page: list — up to 100.
            orderby: list — date, modified, title, id…
            order: list — asc | desc.
            force: delete — permanent instead of trash.
            dry_run: validate and preview, no write.
        """
        c = _client()
        route = _type_route(c, type)

        if op == "list":
            out = _run(lambda: c.list(route, page=page, per_page=per_page, search=search,
                                      status=status, orderby=orderby, order=order,
                                      context="edit"))
            out["items"] = [_slim(i) for i in out["items"]]
            return out

        if op == "get":
            _need(id, "id", op)
            return _strip(_run(lambda: c.get(route, id)))

        if op in ("create", "update"):
            body = dict(_need(data, "data", op))
            _refuse_live(body, "wordpress_content")
            if content_format == "markdown" and isinstance(body.get("content"), str):
                body["content"] = _run(
                    lambda: wordpress_blocks.markdown_to_blocks(body["content"]))
            if op == "create":
                body.setdefault("status", "draft")
                if dry_run:
                    return {"dry_run": True, "would_create": body, "route": route}
                return _strip(_run(lambda: c.create(route, body)))
            _need(id, "id", op)
            current = _run(lambda: c.get(route, id))
            if dry_run:
                return {"dry_run": True, "id": id, "status": current.get("status"),
                        "changes": _diff(current, body)}
            return _strip(_run(lambda: c.update(route, id, body)))

        if op == "delete":
            _need(id, "id", op)
            if dry_run:
                current = _run(lambda: c.get(route, id))
                return {"dry_run": True, "id": id,
                        "would": "delete permanently" if force else "move to trash",
                        "item": _slim(current)}
            return _strip(_run(lambda: c.delete(route, id, force=force)))

        raise _bad(f"op inconnue : {op}")

    @mcp.tool()
    def wordpress_publish(
        id: int,
        type: str = "posts",
        op: Literal["publish", "unpublish"] = "publish",
        at: Optional[str] = None,
        dry_run: bool = False,
    ) -> dict:
        """Put a WordPress post/page LIVE, schedule it, or take it back to
        draft — the only wordpress_* tool that changes what visitors see.
        `op=publish` without `at` publishes now; with `at` (ISO datetime in the
        site's timezone, e.g. `2026-10-01T09:00:00`) it schedules (status
        `future`). `op=unpublish` sets it back to draft. `dry_run` returns the
        current status and what would change.

        Args:
            id: the post/page id.
            type: `posts`, `pages`, or a custom type from `wordpress_site`.
            op: publish | unpublish.
            at: optional schedule datetime (site timezone) — publish only.
            dry_run: preview, no write.
        """
        if op == "unpublish" and at:
            raise _bad("op='unpublish' ne prend pas `at` : la dépublication est immédiate "
                       "(WordPress ne programme pas un retour en brouillon).")
        c = _client()
        route = _type_route(c, type)
        current = _run(lambda: c.get(route, id))
        if op == "unpublish":
            body = {"status": "draft"}
        elif at:
            body = {"status": "future", "date": at}
        elif current.get("status") == "future":
            # Programmé : WordPress garde sa date future et le laisse `future`
            # tant qu'on ne la ramène pas à maintenant (constaté en local).
            body = {"status": "publish",
                    "date_gmt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")}
        else:
            body = {"status": "publish"}
        if dry_run:
            return {"dry_run": True, "id": id, "title": _rendered(current.get("title")),
                    "status": current.get("status"), "would_set": body}
        out = _run(lambda: c.update(route, id, body))
        return {"id": out.get("id"), "status": out.get("status"), "date": out.get("date"),
                "link": out.get("link"), "title": _rendered(out.get("title"))}

    @mcp.tool()
    def wordpress_terms(
        op: Literal["list", "get", "create", "update", "delete"],
        taxonomy: str = "categories",
        id: Optional[int] = None,
        name: Optional[str] = None,
        slug: Optional[str] = None,
        parent: Optional[int] = None,
        description: Optional[str] = None,
        search: Optional[str] = None,
        page: int = 1,
        per_page: int = 100,
        dry_run: bool = False,
    ) -> dict:
        """WordPress categories, tags and custom taxonomies. Use it to map
        category names to ids, or to manage the terms themselves. Deleting a
        term is permanent (WordPress has no trash for terms) — posts lose that
        term; `dry_run` shows what would go.

        Args:
            op: list | get | create | update | delete.
            taxonomy: `categories`, `tags`, or a custom taxonomy from `wordpress_site`.
            id: the term — get/update/delete.
            name: create/update — display name.
            slug: create/update — URL slug.
            parent: create/update — parent term id (hierarchical taxonomies).
            description: create/update.
            search: list — name filter.
            page: list — 1-based page.
            per_page: list — up to 100.
            dry_run: preview, no write.
        """
        c = _client()
        route = _tax_route(c, taxonomy)
        if op == "list":
            out = _run(lambda: c.list(route, page=page, per_page=per_page, search=search,
                                      hide_empty="false"))
            out["items"] = [_slim(t, ("id", "name", "slug", "parent", "count"))
                            for t in out["items"]]
            return out
        if op == "get":
            return _strip(_run(lambda: c.get(route, _need(id, "id", op), context="view")))
        body = {k: v for k, v in {"name": name, "slug": slug, "parent": parent,
                                   "description": description}.items() if v is not None}
        if op == "create":
            _need(name, "name", op)
            if dry_run:
                return {"dry_run": True, "would_create": body, "route": route}
            return _strip(_run(lambda: c.create(route, body)))
        _need(id, "id", op)
        if op == "update":
            if dry_run:
                current = _run(lambda: c.get(route, id, context="view"))
                return {"dry_run": True, "id": id, "changes": _diff(current, body)}
            return _strip(_run(lambda: c.update(route, id, body)))
        if op == "delete":
            if dry_run:
                current = _run(lambda: c.get(route, id, context="view"))
                return {"dry_run": True, "id": id, "would": "delete permanently",
                        "term": {k: current.get(k) for k in ("id", "name", "slug", "count")}}
            return _strip(_run(lambda: c.delete(route, id, force=True)))
        raise _bad(f"op inconnue : {op}")

    @mcp.tool()
    def wordpress_media(
        op: Literal["list", "get", "upload", "update", "delete"],
        id: Optional[int] = None,
        source: Optional[Union[str, dict]] = None,
        filename: Optional[str] = None,
        alt_text: Optional[str] = None,
        caption: Optional[str] = None,
        title: Optional[str] = None,
        search: Optional[str] = None,
        page: int = 1,
        per_page: int = 20,
        dry_run: bool = False,
    ) -> dict:
        """WordPress media library — list, read, upload, edit alt text/caption,
        delete. `upload` takes a public URL or an oto file reference
        (`{"kind": "drive"|"gmail"|"url"|"project_file", …}`) and returns the
        media `id` — use it as a post's `featured_media`. ⚠️ An uploaded file is
        PUBLIC at once at its `source_url` (WordPress has no draft media), even
        if the post using it is a draft. Delete is permanent.

        Args:
            op: list | get | upload | update | delete.
            id: the media item — get/update/delete.
            source: upload — URL string, or oto file reference dict.
            filename: upload — override the stored file name.
            alt_text: upload/update — accessibility text (set it for images).
            caption: upload/update.
            title: upload/update.
            search: list — filter.
            page: list — 1-based page.
            per_page: list — up to 100.
            dry_run: preview, no write.
        """
        c = _client()
        route = "wp/v2/media"
        fields = {k: v for k, v in {"alt_text": alt_text, "caption": caption,
                                     "title": title}.items() if v is not None}
        if op == "list":
            out = _run(lambda: c.list(route, page=page, per_page=per_page, search=search))
            out["items"] = [_slim(m, ("id", "source_url", "mime_type", "alt_text", "date"))
                            for m in out["items"]]
            return out
        if op == "get":
            return _strip(_run(lambda: c.get(route, _need(id, "id", op))))
        if op == "upload":
            _need(source, "source", op)
            if dry_run:
                return {"dry_run": True, "would_upload": source, "fields": fields}
            m = _upload(c, source, filename=filename, **fields)
            return {"id": m.get("id"), "source_url": m.get("source_url"),
                    "mime_type": m.get("mime_type"), "alt_text": m.get("alt_text")}
        _need(id, "id", op)
        if op == "update":
            if dry_run:
                return {"dry_run": True, "id": id,
                        "changes": _diff(_run(lambda: c.get(route, id)), fields)}
            return _strip(_run(lambda: c.update(route, id, fields)))
        if op == "delete":
            if dry_run:
                m = _run(lambda: c.get(route, id))
                return {"dry_run": True, "id": id, "would": "delete permanently",
                        "media": {"title": _rendered(m.get("title")),
                                  "source_url": m.get("source_url")}}
            return _strip(_run(lambda: c.delete(route, id, force=True)))
        raise _bad(f"op inconnue : {op}")

    @mcp.tool()
    def wordpress_article(
        title: str,
        markdown: str,
        id: Optional[int] = None,
        type: str = "posts",
        status: Optional[Literal["draft", "pending"]] = None,
        categories: Optional[list[Union[str, int]]] = None,
        tags: Optional[list[Union[str, int]]] = None,
        featured_image: Optional[Union[str, dict]] = None,
        featured_image_alt: Optional[str] = None,
        excerpt: Optional[str] = None,
        slug: Optional[str] = None,
        seo_title: Optional[str] = None,
        seo_description: Optional[str] = None,
        focus_keyword: Optional[str] = None,
        dry_run: bool = False,
    ) -> dict:
        """Write a WordPress article in ONE call — the path to use for blog
        posts. Markdown becomes native editor blocks (headings, lists, quotes,
        code, tables, images stay editable in WordPress); raw HTML in it
        (`<script>`, `<iframe>`…) is kept as an HTML block unless the account
        lacks `unfiltered_html` — never paste HTML from an untrusted page.
        Categories and tags are given by NAME (a string, created if missing) or
        id (an integer), and must be taxonomies of `type`. `featured_image` is a
        URL or an oto file reference, uploaded and attached — the image is
        public at once, even on a draft. SEO title/description/
        keyword are written when Yoast or Rank Math expose them (else `seo`
        says why). A new article is saved as draft (or pending review) — then
        `wordpress_publish` to go live or schedule. Pass `id` to rewrite an
        existing article: its status is kept, so rewriting a PUBLISHED article
        changes it live (use `dry_run`, or `wordpress_publish op=unpublish`
        first). `dry_run` resolves everything, reports the terms it would
        create, and writes nothing.

        Args:
            title: article title.
            markdown: body in Markdown.
            id: existing post to update instead of creating.
            type: `posts` or a custom type from `wordpress_site`.
            status: draft | pending (review) — default draft on create, unchanged on update.
            categories: names (strings) or ids (integers).
            tags: names (strings) or ids (integers).
            featured_image: URL or oto file reference dict.
            featured_image_alt: alt text for the featured image.
            excerpt: summary shown in listings.
            slug: URL slug.
            seo_title: SEO title (Yoast / Rank Math).
            seo_description: meta description (Yoast / Rank Math).
            focus_keyword: focus keyphrase (Yoast / Rank Math).
            dry_run: resolve and preview, no write.
        """
        c = _client()
        route = _type_route(c, type)
        body: dict = {"title": title,
                      "content": _run(lambda: wordpress_blocks.markdown_to_blocks(markdown))}
        # Création : brouillon par défaut. Mise à jour : le statut n'est PAS
        # envoyé sans demande — l'envoyer d'office dépublierait un article en ligne.
        if status is not None or id is None:
            body["status"] = status or "draft"
        if excerpt is not None:
            body["excerpt"] = excerpt
        if slug:
            body["slug"] = slug
        if categories or tags:
            # Les termes d'un type sont CEUX que le site lui déclare : une page n'a pas
            # de catégories, un type personnalisé peut avoir les siennes.
            accepted = _type_taxonomies(c, type)
            for given, tax, label in ((categories, "category", "catégories"),
                                      (tags, "post_tag", "étiquettes")):
                if given and tax not in accepted:
                    raise _bad(f"le type « {type} » n'a pas de {label} sur ce site "
                               f"(taxonomies : {accepted}) — `wordpress_terms` et "
                               "`wordpress_content data=` pour une taxonomie propre au type.")
        cat_ids, cat_new, cat_made = _resolve_terms(
            c, _tax_route(c, "category"), categories or [], create=not dry_run)
        tag_ids, tag_new, tag_made = _resolve_terms(
            c, _tax_route(c, "post_tag"), tags or [], create=not dry_run)
        if categories:
            body["categories"] = cat_ids
        if tags:
            body["tags"] = tag_ids

        seo_in = {"title": seo_title, "description": seo_description,
                  "focus_keyword": focus_keyword}
        seo_wanted = any(v is not None for v in seo_in.values())
        plan = _seo_plan(c, route, (_run(c.index).get("namespaces") or [])) if seo_wanted else None
        if plan:
            meta = _seo_meta(plan, seo_in)
            if meta:
                body["meta"] = meta

        if dry_run:
            return {"dry_run": True, "would": "update" if id else "create", "id": id,
                    "route": route, "body": body,
                    "terms_to_create": {"categories": cat_new, "tags": tag_new},
                    "featured_image": featured_image, "seo": plan}

        media = None
        try:
            if featured_image:
                media = _upload(c, featured_image, alt_text=featured_image_alt)
                body["featured_media"] = media.get("id")
            post = _run(lambda: c.update(route, id, body) if id else c.create(route, body))
        except McpError as e:
            # Termes et image sont créés AVANT l'article : un échec ici les laisse en
            # place. On les nomme — jamais d'objets orphelins que personne ne connaît.
            left = []
            if cat_made or tag_made:
                left.append("termes créés : " + ", ".join(
                    f"{t['name']} (id {t['id']})" for t in cat_made + tag_made))
            if media:
                left.append(f"image téléversée : média id {media.get('id')} "
                            f"({media.get('source_url')}, déjà public)")
            if not left:
                raise
            raise _bad(f"{e.error.message} — l'article n'est pas écrit. Restent sur le "
                       f"site : {' ; '.join(left)}. Un nouvel essai retrouve les termes "
                       "par nom ; le média se rattache (`wordpress_content op=update "
                       "data={featured_media: id}`) ou se supprime (`wordpress_media "
                       "op=delete`).") from e
        pid = post.get("id")
        seo_out = None
        if seo_wanted:
            seo_out = {"plugin": plan.get("plugin"), "written": plan.get("via") == "meta"}
            if plan.get("via") != "meta":
                seo_out["reason"] = plan.get("reason")
        site = c.site_url
        return {
            "id": pid, "status": post.get("status"), "link": post.get("link"),
            "edit_link": f"{site}/wp-admin/post.php?post={pid}&action=edit",
            "categories": post.get("categories"), "tags": post.get("tags"),
            "featured_media": post.get("featured_media"), "seo": seo_out,
        }


# --- flux « Connecter » (écran d'autorisation natif du site) ------------------

def _start_flow(ctx, values: dict):
    """Point d'entrée du seam `connector_flow` — traduit les refus en réponses
    nommées (400 / 403) plutôt qu'en 500."""
    from ..auth import wordpress as wp_auth
    from ..capabilities._types import AuthzDenied

    try:
        return wp_auth.start(ctx, values)
    except PermissionError as e:
        raise AuthzDenied(403, "org_admin_required", str(e))
    except ValueError as e:   # ConnectRefused, EgressRefused, URL invalide
        raise AuthzDenied(400, "connect_refused", str(e))


def _declare_flow() -> None:
    from ..auth import wordpress as wp_auth
    from ..connectors import flow as connector_flow

    # `site_url` est un champ LIBRE : `declare` refuse un paramètre requis sans
    # options ni défaut (il ne sait rendre qu'un select) — il est donc déclaré
    # facultatif et `wp_auth.start` refuse lui-même une valeur vide, nommément.
    connector_flow.declare(
        "wordpress",
        start=_start_flow,
        params=(connector_flow.FlowParam(
            "site_url", "URL du site", required=False,
            help="ex. blog.example.com — tu approuveras l'accès dans ton wp-admin"),),
        label="Connecter mon site WordPress",
        callback_path=wp_auth.CALLBACK_PATH,
    )


_declare_flow()
