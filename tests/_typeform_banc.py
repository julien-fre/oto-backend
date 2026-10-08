"""Le banc des outils Typeform : le serveur monté par les TROIS modules du
connecteur, le client oto-core remplacé par un double en mémoire qui note ses
appels. Partagé par `test_typeform_tools.py` (lectures) et
`test_typeform_ecritures.py` (écritures)."""
from __future__ import annotations

import asyncio

import pytest
from fastmcp import FastMCP
from mcp.types import INVALID_PARAMS

from oto_mcp import access
from oto_mcp.mcp_errors import McpError
from oto_mcp.tools import typeform as T
from oto_mcp.tools import typeform_formulaires, typeform_webhooks

US = "https://api.typeform.com"
EU = "https://api.eu.typeform.com"

FORM = {
    "id": "f1", "title": "Satisfaction", "language": "fr",
    "_links": {"display": "https://acme.typeform.com/to/f1",
               "responses": f"{US}/forms/f1/responses"},
    "fields": [
        {"id": "q1", "ref": "nom", "type": "short_text", "title": "Votre nom ?",
         "validations": {"required": True}},
        {"id": "q2", "ref": "ville", "type": "multiple_choice", "title": "Ville ?",
         "properties": {"choices": [{"id": "c1", "label": "Lyon"},
                                    {"id": "c2", "label": "Paris"}],
                        "allow_multiple_selection": True}},
        {"id": "g1", "ref": "grp", "type": "group", "title": "Détails",
         "properties": {"fields": [
             {"id": "q3", "ref": "note", "type": "rating", "title": "Commentaire"},
             {"id": "q4", "ref": "autre", "type": "long_text", "title": "Commentaire"},
         ]}},
    ],
    "hidden": ["utm_source"],
    "logic": [{"type": "field"}], "settings": {"is_public": True},
    "welcome_screens": [{"title": "Bonjour"}],
}

RESPONSE = {
    "response_id": "r1", "token": "r1", "landing_id": "r1",
    "landed_at": "2026-09-30T10:00:00Z", "submitted_at": "2026-09-30T10:02:00Z",
    "metadata": {"user_agent": "Mozilla/5.0", "referer": "https://acme.test"},
    "hidden": {"utm_source": "newsletter"},
    "calculated": {"score": 0},
    "variables": [{"key": "score", "type": "number", "number": 4}],
    "answers": [
        {"field": {"id": "q1", "type": "short_text", "ref": "nom"},
         "type": "text", "text": "Jane Doe"},
        {"field": {"id": "q2", "type": "multiple_choice", "ref": "ville"},
         "type": "choices", "choices": {"labels": ["Lyon", "Paris"]}},
        {"field": {"id": "q3", "type": "rating", "ref": "note"},
         "type": "number", "number": 5},
        {"field": {"id": "q4", "type": "long_text", "ref": "autre"},
         "type": "text", "text": "RAS"},
    ],
}

#: Un webhook tel qu'un amont distrait pourrait le rendre : AVEC son secret.
WEBHOOK = {"id": "h1", "tag": "crm", "url": "https://hooks.acme.test/tf",
           "enabled": True, "verify_ssl": True, "form_id": "f1",
           "event_types": {"form_response": True, "form_response_partial": False},
           "secret": "s3cr3t", "created_at": "2026-10-01T00:00:00Z"}


class _FauxClient:
    """Double en mémoire du client oto-core : rend des pages, note les appels."""

    def __init__(self, access_token, region="us"):
        from oto.tools.typeform import REGIONS
        self.access_token = access_token
        self.region = region
        self.BASE_URL = REGIONS[region]
        self.appels = []
        self.form = dict(FORM)
        self.page = {"total_items": 1, "page_count": 1, "items": [RESPONSE]}
        self.webhook = dict(WEBHOOK)
        self.leve = None
        self.leve_sur = {}

    def _note(self, nom, *args, **kw):
        self.appels.append((nom, args, kw))
        if nom in self.leve_sur:
            raise self.leve_sur[nom]
        if self.leve:
            raise self.leve

    def noms(self):
        return [a[0] for a in self.appels]

    def list_workspaces(self, **kw):
        self._note("list_workspaces", **kw)
        return {"total_items": 1, "page_count": 1, "items": [
            {"id": "w1", "name": "Ventes", "account_id": "a1", "shared": False,
             "forms": {"count": 3, "href": f"{US}/workspaces/w1/forms"},
             "self": {"href": f"{US}/workspaces/w1"}}]}

    def list_forms(self, **kw):
        self._note("list_forms", **kw)
        return {"total_items": 1, "page_count": 1, "items": [
            {"id": "f1", "title": "Satisfaction", "created_at": "2026-01-01T00:00:00Z",
             "last_updated_at": "2026-09-01T00:00:00Z", "settings": {"is_public": True},
             "self": {"href": f"{US}/forms/f1"}, "theme": {"href": f"{US}/themes/t"},
             "_links": {"display": "https://acme.typeform.com/to/f1",
                        "responses": f"{US}/forms/f1/responses"}}]}

    def get_form(self, form_id):
        self._note("get_form", form_id)
        return self.form

    def list_responses(self, form_id, **kw):
        self._note("list_responses", form_id, **kw)
        return self.page

    def create_form(self, **kw):
        self._note("create_form", **kw)
        return dict(FORM, id="new", title=kw.get("title"),
                    settings=kw.get("settings") or {"is_public": True})

    def replace_form(self, form_id, **kw):
        self._note("replace_form", form_id, **kw)
        return dict(FORM, title=kw.get("title"))

    def update_form(self, form_id, operations):
        self._note("update_form", form_id, operations)

    def delete_form(self, form_id):
        self._note("delete_form", form_id)

    def delete_responses(self, form_id, included_response_ids):
        self._note("delete_responses", form_id, included_response_ids)

    def summarize_responses(self, form_id, **kw):
        self._note("summarize_responses", form_id, **kw)
        return {"form_id": form_id, "responses_analyzed": 1, "truncated": False,
                "fields": []}

    def list_webhooks(self, form_id):
        self._note("list_webhooks", form_id)
        return {"items": [dict(self.webhook)]}

    def get_webhook(self, form_id, tag):
        self._note("get_webhook", form_id, tag)
        return dict(self.webhook)

    def upsert_webhook(self, form_id, tag, **kw):
        self._note("upsert_webhook", form_id, tag, **kw)
        return dict(self.webhook, url=kw["url"], enabled=kw["enabled"])

    def delete_webhook(self, form_id, tag):
        self._note("delete_webhook", form_id, tag)


class _Banc:
    """Le serveur monté, les clients construits, les champs du credential, et
    `prepare(client)` — appliqué à chaque client construit."""
    prepare = None


@pytest.fixture
def banc(monkeypatch):
    b = _Banc()
    b.champs = {"key": "tfp_test"}
    b.construits = []
    monkeypatch.setattr(access, "resolve_credential_fields",
                        lambda provider, account=None: dict(b.champs))
    import oto.tools.typeform as pkg

    def _construire(**kw):
        c = _FauxClient(**kw)
        if b.prepare:
            b.prepare(c)
        b.construits.append(c)
        return c

    monkeypatch.setattr(pkg, "TypeformClient", _construire)
    b.mcp = FastMCP("banc-typeform")
    for module in (T, typeform_formulaires, typeform_webhooks):
        module.register(b.mcp)
    return b


def appeler(b, outil, **arguments):
    return asyncio.run(b.mcp.call_tool(outil, arguments)).structured_content


def refus(b, outil, **arguments) -> McpError:
    """Le refus, lu sur la fonction de l'outil : à travers `call_tool`, fastmcp
    l'enveloppe en `ToolError` et son code ne se lit plus."""
    fn = {t.name: t for t in asyncio.run(b.mcp._list_tools())}[outil].fn
    with pytest.raises(McpError) as e:
        fn(**arguments)
    assert e.value.error.code == INVALID_PARAMS
    return e.value
