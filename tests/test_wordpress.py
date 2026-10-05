"""Connecteur WordPress — registre, surface MCP, et comportement des outils contre
un FAUX client (aucun réseau) : la frontière brouillon/publication, le dry_run qui
diffe sur la forme brute, la résolution des termes par nom, le SEO écrit seulement
quand le site l'expose, la traduction des erreurs `rest_*`.
"""
import asyncio

import pytest

from oto.tools.common.errors import UpstreamHTTPError
from oto_mcp import providers
from oto_mcp.mcp_errors import McpError

EXPECTED_TOOLS = {"wordpress_site", "wordpress_content", "wordpress_publish",
                  "wordpress_terms", "wordpress_media", "wordpress_article"}


class FakeWP:
    """Le sous-ensemble de `WordPressClient` que les outils appellent."""

    site_url = "https://blog.example.com"

    def __init__(self):
        self.calls = []
        self.posts = {7: {"id": 7, "status": "draft", "title": {"raw": "Old", "rendered": "Old"},
                          "content": {"raw": "<!-- wp:paragraph -->x", "rendered": "<p>x</p>"},
                          "link": "https://blog.example.com/?p=7", "_links": {"self": []}}}
        self.terms = {"wp/v2/categories": [{"id": 3, "name": "Guides"}], "wp/v2/tags": []}
        self.namespaces = ["wp/v2"]
        self.meta_schema = {}
        self._next = 100

    def _log(self, *a):
        self.calls.append(a)

    def index(self):
        return {"name": "Blog", "url": self.site_url, "namespaces": self.namespaces}

    def me(self):
        return {"id": 1, "name": "J", "roles": ["editor"],
                "capabilities": {"publish_posts": True, "upload_files": True}}

    def types(self):
        return {"post": {"rest_base": "posts", "rest_namespace": "wp/v2", "name": "Posts"},
                "book": {"rest_base": "books", "rest_namespace": "wp/v2", "name": "Books"},
                "attachment": {"rest_base": "media", "name": "Media"}}

    def taxonomies(self):
        return {"category": {"rest_base": "categories", "name": "Categories"},
                "genre": {"rest_base": "genres", "rest_namespace": "wp/v2", "name": "Genres"}}

    def request(self, method, route, **kw):
        self._log("request", method, route)
        if method == "OPTIONS":
            return {"schema": {"properties": {"meta": {"properties": self.meta_schema}}}}, {}
        return {}, {}

    def list(self, route, **params):
        self._log("list", route, params)
        if route in self.terms:
            s = (params.get("search") or "").lower()
            return {"items": [t for t in self.terms[route] if s in t["name"].lower()],
                    "total": None, "total_pages": None, "page": 1}
        return {"items": list(self.posts.values()), "total": 1, "total_pages": 1, "page": 1}

    def get(self, route, item_id, context="edit"):
        self._log("get", route, item_id)
        return self.posts[item_id]

    def create(self, route, body):
        self._log("create", route, body)
        self._next += 1
        if route in self.terms:
            self.terms[route].append({"id": self._next, "name": body["name"]})
        return {"id": self._next, **body, "link": f"https://blog.example.com/?p={self._next}"}

    def update(self, route, item_id, body):
        self._log("update", route, item_id, body)
        return {"id": item_id, **body}

    def delete(self, route, item_id, force=False):
        self._log("delete", route, item_id, force)
        return {"deleted": force}

    def upload_media(self, data, filename, mime, **fields):
        self._log("upload", filename, mime, fields)
        return {"id": 55, "source_url": "https://blog.example.com/cover.jpg", **fields}


@pytest.fixture
def wp(monkeypatch):
    from fastmcp import FastMCP
    from oto_mcp.tools import wordpress as mod

    fake = FakeWP()
    monkeypatch.setattr(mod, "_client", lambda account=None: fake)
    m = FastMCP("t")
    mod.register(m)

    def call(name, **kw):
        return asyncio.run(m.get_tool(name)).fn(**kw)

    call.fake = fake
    return call


# --- registre / surface ------------------------------------------------------

def test_registry_entry():
    c = providers.REGISTRY["wordpress"]
    assert c.kind == "tools" and c.secret_kind == "fields"
    assert c.auth_modes == frozenset({"byo_user", "byo_org"})
    fields = {f.name: f for f in c.credential_fields}
    assert set(fields) == {"site_url", "username", "application_password"}
    assert fields["application_password"].secret is True
    assert fields["site_url"].secret is False


def test_surface_and_descriptions():
    from fastmcp import FastMCP
    from oto_mcp.tools import wordpress as mod

    m = FastMCP("t")
    mod.register(m)
    tools = {t.name: t for t in asyncio.run(m.list_tools())}
    assert set(tools) == EXPECTED_TOOLS
    assert all(t.description for t in tools.values())


def test_connect_flow_declared():
    from oto_mcp.connectors import flow
    import oto_mcp.tools.wordpress  # noqa: F401 — déclare le flux à l'import

    d = flow.describe("wordpress")
    assert d and [p["name"] for p in d["params"]] == ["site_url"]
    assert flow.callback_url  # dérivée, jamais écrite


# --- frontière brouillon / en ligne -------------------------------------------

@pytest.mark.parametrize("status", ["publish", "future"])
def test_content_refuses_live_status(wp, status):
    with pytest.raises(McpError, match="wordpress_publish"):
        wp("wordpress_content", op="create", data={"title": "t", "status": status})
    with pytest.raises(McpError):
        wp("wordpress_content", op="update", id=7, data={"status": status})
    assert not [c for c in wp.fake.calls if c[0] in ("create", "update")]


def test_create_defaults_to_draft_and_converts_markdown(wp):
    out = wp("wordpress_content", op="create", data={"title": "T", "content": "## Hi"},
             content_format="markdown")
    body = [c for c in wp.fake.calls if c[0] == "create"][0][2]
    assert body["status"] == "draft"
    assert body["content"].startswith("<!-- wp:heading -->")
    assert "_links" not in out


def test_update_dry_run_diffs_raw_form(wp):
    out = wp("wordpress_content", op="update", id=7,
             data={"title": "New", "content": "<!-- wp:paragraph -->x"}, dry_run=True)
    assert out["changes"] == {"title": {"from": "Old", "to": "New"}}
    assert not [c for c in wp.fake.calls if c[0] == "update"]


def test_delete_trash_by_default(wp):
    wp("wordpress_content", op="delete", id=7)
    assert ("delete", "wp/v2/posts", 7, False) in wp.fake.calls


def test_custom_type_resolution_and_unknown_type(wp):
    wp("wordpress_content", op="list", type="book")
    assert any(c[0] == "list" and c[1] == "wp/v2/books" for c in wp.fake.calls)
    with pytest.raises(McpError, match="books"):
        wp("wordpress_content", op="list", type="recipes")


def test_publish_now_schedule_unpublish(wp):
    wp("wordpress_publish", id=7)
    wp("wordpress_publish", id=7, at="2026-10-01T09:00:00")
    wp("wordpress_publish", id=7, op="unpublish")
    bodies = [c[3] for c in wp.fake.calls if c[0] == "update"]
    assert bodies == [{"status": "publish"},
                      {"status": "future", "date": "2026-10-01T09:00:00"},
                      {"status": "draft"}]


def test_publish_dry_run_writes_nothing(wp):
    out = wp("wordpress_publish", id=7, dry_run=True)
    assert out["status"] == "draft" and out["would_set"] == {"status": "publish"}
    assert not [c for c in wp.fake.calls if c[0] == "update"]


# --- article composé ------------------------------------------------------------

def test_article_resolves_terms_by_name_and_uploads_image(wp, monkeypatch):
    from oto_mcp import file_source

    monkeypatch.setattr(file_source, "resolve",
                        lambda src, **k: file_source.ResolvedFile(b"JPG", "cover.jpg", "image/jpeg"))
    out = wp("wordpress_article", title="T", markdown="Body", categories=["guides", "News"],
             tags=["seo"], featured_image="https://img.example.com/c.jpg",
             featured_image_alt="alt")
    post = [c for c in wp.fake.calls if c[0] == "create" and c[1] == "wp/v2/posts"][0][2]
    assert post["status"] == "draft"
    assert post["categories"][0] == 3                      # « guides » ≈ « Guides » existant
    assert len(post["categories"]) == 2                     # « News » créé
    assert post["featured_media"] == 55
    assert out["edit_link"].endswith(f"post={out['id']}&action=edit")


def test_article_dry_run_creates_nothing(wp):
    out = wp("wordpress_article", title="T", markdown="x", categories=["Brand new"],
             dry_run=True)
    assert out["terms_to_create"]["categories"] == ["Brand new"]
    assert not [c for c in wp.fake.calls if c[0] in ("create", "update", "upload")]


def test_article_seo_written_only_when_exposed(wp):
    wp.fake.namespaces = ["wp/v2", "yoast/v1"]
    out = wp("wordpress_article", title="T", markdown="x", seo_description="d")
    body = [c for c in wp.fake.calls if c[0] == "create"][-1][2]
    assert "meta" not in body
    assert out["seo"]["written"] is False and "snippet" in out["seo"]["reason"]

    wp.fake.meta_schema = {"_yoast_wpseo_title": {}, "_yoast_wpseo_metadesc": {},
                           "_yoast_wpseo_focuskw": {}}
    out = wp("wordpress_article", title="T", markdown="x", seo_description="d")
    body = [c for c in wp.fake.calls if c[0] == "create"][-1][2]
    assert body["meta"] == {"_yoast_wpseo_metadesc": "d"}
    assert out["seo"] == {"plugin": "yoast", "written": True}


# --- erreurs -----------------------------------------------------------------------

@pytest.mark.parametrize("status,code,needle", [
    (401, "rest_not_logged_in", "Authorization"),
    (401, "incorrect_password", "mot de passe"),
    (403, "rest_cannot_create", "droits"),
    (404, "rest_no_route", "rest_no_route"),
    (502, "", "erreur"),
])
def test_error_translation(status, code, needle):
    from oto_mcp.tools.wordpress import _translate

    e = UpstreamHTTPError(status, {"code": code, "message": "m"}, service="wordpress")
    assert needle in _translate(e).error.message


def test_site_summary(wp):
    out = wp("wordpress_site")
    assert out["user"]["can"]["publish_posts"] is True
    assert {t["type"] for t in out["types"]} == {"posts", "books"}   # attachment masqué
    assert out["plugins"]["seo"]["plugin"] is None


def test_publish_now_on_scheduled_post_resets_date(wp):
    # WordPress garde un article `future` tant que sa date est future : publier
    # « maintenant » doit ramener la date, sinon rien ne bouge (constaté en local).
    wp.fake.posts[7]["status"] = "future"
    wp("wordpress_publish", id=7)
    body = [c[3] for c in wp.fake.calls if c[0] == "update"][0]
    assert body["status"] == "publish" and "date_gmt" in body


def test_article_update_keeps_status_of_live_post(wp):
    # Réécrire un article EN LIGNE ne doit pas le dépublier en silence.
    wp("wordpress_article", id=7, title="T", markdown="x")
    body = [c[3] for c in wp.fake.calls if c[0] == "update"][0]
    assert "status" not in body
    wp("wordpress_article", title="New", markdown="x")
    created = [c[2] for c in wp.fake.calls if c[0] == "create"][0]
    assert created["status"] == "draft"
