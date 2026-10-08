"""Connecteur Typeform, les ÉCRITURES — `typeform_forms` op create|update|replace|
delete, `typeform_delete_responses`, `typeform_responses_summary`,
`typeform_webhooks`.

Par le VRAI chemin FastMCP, le client remplacé par le double de
`_typeform_banc.py`. Ce fichier verrouille :

- l'irréversible en DEUX temps : sans `confirm=True`, AUCUN appel d'écriture ne
  part, et l'aperçu est LU chez Typeform (champs perdus, réponses en jeu, id
  inconnus), pas l'écho de la demande ;
- les arguments qu'une op n'utilise pas sont refusés, `list`/`get` compris
  (non-régression des lectures) ;
- une définition : clé inconnue refusée, clés posées par Typeform non envoyées
  et NOMMÉES ; un formulaire créé dit s'il est public ;
- le data center : une suppression de réponses ailleurs que chez le compte
  serait un succès qui ne fait rien — refusée ;
- le secret de signature d'un webhook ne sort JAMAIS, brut compris, ni du
  double ni du vrai client oto-core ; et le journal d'appels le masque.
"""
from __future__ import annotations

import json

import pytest

from _typeform_banc import EU, FORM, WEBHOOK, appeler, banc, refus  # noqa: F401


def _client(b):
    return b.construits[-1]


# --- formulaires : création, patch ---------------------------------------------

def test_create_envoie_la_definition_et_dit_que_le_formulaire_est_public(banc):
    r = appeler(banc, "typeform_forms", op="create", definition={
        "title": "Avis", "fields": [{"title": "Note ?", "type": "nps"}]})
    nom, _, kw = _client(banc).appels[-1]
    assert nom == "create_form"
    assert kw == {"title": "Avis", "fields": [{"title": "Note ?", "type": "nps"}]}
    assert r["created"] is True and r["is_public"] is True
    assert "PUBLIC" in r["note"]


def test_create_prive_ne_porte_pas_davertissement(banc):
    r = appeler(banc, "typeform_forms", op="create",
                definition={"title": "Brouillon", "settings": {"is_public": False}})
    assert r["is_public"] is False and "note" not in r


def test_create_nomme_les_cles_posees_par_typeform_sans_les_envoyer(banc):
    """La forme que rend `get` (full=True) se renvoie telle quelle."""
    lu = {k: v for k, v in FORM.items() if k != "language"}
    r = appeler(banc, "typeform_forms", op="create", definition={**lu, "title": "Copie"})
    _, _, kw = _client(banc).appels[-1]
    assert "id" not in kw and "_links" not in kw
    assert r["not_sent"] == ["_links", "id"]


@pytest.mark.parametrize("definition", [
    {"title": "X", "colour": "red"},       # clé inconnue : jamais avalée
    {"fields": []},                         # sans titre
    {},
])
def test_create_definition_invalide_refusee_avant_tout_appel(banc, definition):
    refus(banc, "typeform_forms", op="create", definition=definition)
    assert all(c.appels == [] for c in banc.construits)


def test_update_applique_le_patch(banc):
    ops = [{"op": "replace", "path": "/settings/is_public", "value": False}]
    r = appeler(banc, "typeform_forms", op="update", form_id="f1", operations=ops)
    assert _client(banc).appels == [("update_form", ("f1", ops), {})]
    assert r == {"updated": True, "form_id": "f1", "paths": ["/settings/is_public"]}


def test_update_sans_operations_refuse(banc):
    refus(banc, "typeform_forms", op="update", form_id="f1")


# --- formulaires : l'irréversible en deux temps ---------------------------------

NOUVELLE = {"title": "Satisfaction 2026", "fields": [
    {"id": "q1", "title": "Votre nom ?", "type": "short_text"},
    {"title": "Recommanderiez-vous ?", "type": "nps"}]}


def test_replace_sans_confirm_rend_un_apercu_lu_et_necrit_rien(banc):
    r = appeler(banc, "typeform_forms", op="replace", form_id="f1", definition=NOUVELLE)
    assert _client(banc).noms() == ["get_form"]
    assert r["dry_run"] is True
    w = r["would_replace"]
    assert w["title"] == {"from": "Satisfaction", "to": "Satisfaction 2026"}
    assert w["fields_kept"] == 1
    # Les champs absents de la définition, sous-questions de groupe comprises.
    assert {f["id"] for f in w["fields_removed"]} == {"q2", "g1", "q3", "q4"}
    assert w["fields_added"] == ["Recommanderiez-vous ?"]
    assert "theme" in w and "settings" in w     # ce qui retombe au défaut est DIT


def test_replace_confirme_remplace(banc):
    r = appeler(banc, "typeform_forms", op="replace", form_id="f1",
                definition=NOUVELLE, confirm=True)
    nom, args, kw = _client(banc).appels[-1]
    assert (nom, args) == ("replace_form", ("f1",)) and kw["title"] == "Satisfaction 2026"
    assert r["replaced"] is True


def test_replace_apercu_refuse_full(banc):
    refus(banc, "typeform_forms", op="replace", form_id="f1", definition=NOUVELLE,
          full=True)


def test_delete_sans_confirm_compte_les_reponses_en_jeu(banc):
    r = appeler(banc, "typeform_forms", op="delete", form_id="f1")
    assert _client(banc).noms() == ["get_form", "list_responses"]
    w = r["would_delete"]
    assert w["title"] == "Satisfaction" and w["completed_responses"] == 1
    assert w["fields"] == 5


def test_delete_apercu_sur_un_autre_data_center_ne_compte_pas_zero(banc):
    def _compte_eu(c):
        c.form = dict(FORM, _links={"responses": f"{EU}/forms/f1/responses"})
    banc.prepare = _compte_eu
    w = appeler(banc, "typeform_forms", op="delete", form_id="f1")["would_delete"]
    assert w["completed_responses"] is None and "« eu »" in w["count_unavailable"]
    assert _client(banc).noms() == ["get_form"]


def test_delete_apercu_sans_le_scope_de_lecture_dit_pourquoi(banc):
    from oto.tools.common import UpstreamHTTPError

    def _sans_scope(c):
        c.leve_sur["list_responses"] = UpstreamHTTPError(403, {}, service="typeform")
    banc.prepare = _sans_scope
    w = appeler(banc, "typeform_forms", op="delete", form_id="f1")["would_delete"]
    assert w["completed_responses"] is None and "responses:read" in w["count_unavailable"]


def test_delete_confirme_supprime(banc):
    r = appeler(banc, "typeform_forms", op="delete", form_id="f1", confirm=True)
    assert _client(banc).appels == [("delete_form", ("f1",), {})]
    assert r == {"deleted": True, "form_id": "f1"}


def test_un_403_en_ecriture_nomme_le_scope(banc):
    from oto.tools.common import UpstreamHTTPError

    def _lecture_seule(c):
        c.leve_sur["delete_form"] = UpstreamHTTPError(403, {}, service="typeform")
    banc.prepare = _lecture_seule
    e = refus(banc, "typeform_forms", op="delete", form_id="f1", confirm=True)
    assert "forms:write" in e.error.message


# --- formulaires : arguments d'une autre op, refusés ----------------------------

@pytest.mark.parametrize("op,args", [
    ("list", {"definition": {"title": "x"}}),
    ("list", {"operations": [{}]}),
    ("list", {"confirm": True}),
    ("get", {"form_id": "f1", "definition": {"title": "x"}}),
    ("get", {"form_id": "f1", "confirm": True}),
    ("create", {"definition": {"title": "x"}, "form_id": "f1"}),
    ("create", {"definition": {"title": "x"}, "confirm": True}),
    ("create", {"definition": {"title": "x"}, "search": "y"}),
    ("update", {"form_id": "f1", "operations": [{}], "definition": {"title": "x"}}),
    ("update", {"form_id": "f1", "operations": [{}], "full": True}),
    ("delete", {"form_id": "f1", "operations": [{}]}),
    ("delete", {"form_id": "f1", "full": True}),
    ("replace", {"definition": {"title": "x"}}),               # sans form_id
])
def test_forms_refuse_ce_que_lop_nutilise_pas(banc, op, args):
    refus(banc, "typeform_forms", op=op, **args)
    assert all(not set(c.noms()) - {"get_form"} for c in banc.construits)


# --- réponses : suppression et synthèse -----------------------------------------

def test_delete_responses_sans_confirm_dit_ce_qui_existe(banc):
    r = appeler(banc, "typeform_delete_responses", form_id="f1",
                response_ids=["r1", "zz", "r1"])
    c = _client(banc)
    assert c.noms() == ["get_form", "list_responses"]
    _, _, kw = c.appels[-1]
    assert kw["included_response_ids"] == ["r1", "zz"]
    assert set(kw["response_type"]) == {"completed", "partial", "started"}
    w = r["would_delete"]
    assert [f["response_id"] for f in w["found"]] == ["r1"]
    assert w["not_found"] == ["zz"]
    assert "answers" not in w["found"][0]       # identifier, pas recopier


def test_delete_responses_confirme(banc):
    r = appeler(banc, "typeform_delete_responses", form_id="f1",
                response_ids=["r1", "r2"], confirm=True)
    assert _client(banc).noms() == ["get_form", "delete_responses"]
    assert _client(banc).appels[-1][1] == ("f1", ["r1", "r2"])
    assert r["deletion_registered"] is True


def test_delete_responses_sur_un_autre_data_center_est_refusee(banc):
    def _compte_eu(c):
        c.form = dict(FORM, _links={"responses": f"{EU}/forms/f1/responses"})
    banc.prepare = _compte_eu
    refus(banc, "typeform_delete_responses", form_id="f1", response_ids=["r1"],
          confirm=True)
    assert _client(banc).noms() == ["get_form"]


@pytest.mark.parametrize("ids", [[], [f"r{i}" for i in range(1001)]])
def test_delete_responses_bornee(banc, ids):
    refus(banc, "typeform_delete_responses", form_id="f1", response_ids=ids)
    assert banc.construits == []


def test_summary_verifie_le_data_center_puis_agrege(banc):
    r = appeler(banc, "typeform_responses_summary", form_id="f1",
                since="2026-09-01T00:00:00", response_type=["completed"], max_pages=3)
    nom, args, kw = _client(banc).appels[-1]
    assert (nom, args) == ("summarize_responses", ("f1",))
    assert kw == {"since": "2026-09-01T00:00:00", "until": None,
                  "response_type": ["completed"], "max_pages": 3}
    assert r["responses_analyzed"] == 1


def test_summary_sur_un_autre_data_center_ne_rend_pas_un_zero(banc):
    def _compte_eu(c):
        c.form = dict(FORM, _links={"responses": f"{EU}/forms/f1/responses"})
    banc.prepare = _compte_eu
    refus(banc, "typeform_responses_summary", form_id="f1")
    assert _client(banc).noms() == ["get_form"]


# --- webhooks -------------------------------------------------------------------

def _sans_le_secret(payload) -> bool:
    return WEBHOOK["secret"] not in json.dumps(payload) and '"secret"' not in json.dumps(payload)


@pytest.mark.parametrize("args", [{}, {"full": True}])
def test_webhooks_list_sans_secret(banc, args):
    r = appeler(banc, "typeform_webhooks", form_id="f1", **args)
    assert _sans_le_secret(r)
    if not args:
        assert r["count"] == 1 and r["webhooks"][0]["tag"] == "crm"


@pytest.mark.parametrize("args", [{}, {"full": True}])
def test_webhooks_get_sans_secret(banc, args):
    r = appeler(banc, "typeform_webhooks", op="get", form_id="f1", tag="crm", **args)
    assert _sans_le_secret(r) and r["url"] == WEBHOOK["url"]


def test_upsert_sans_confirm_rend_le_diff_et_necrit_rien(banc):
    r = appeler(banc, "typeform_webhooks", op="upsert", form_id="f1", tag="crm",
                url="https://hooks.acme.test/v2", enabled=True,
                event_types=["form_response", "form_response_partial"], secret="neuf")
    assert _client(banc).noms() == ["get_webhook"]
    w = r["would_replace"]
    assert w["changes"]["url"] == {"from": WEBHOOK["url"], "to": "https://hooks.acme.test/v2"}
    assert w["changes"]["event_types"]["to"] == {"form_response": True,
                                                 "form_response_partial": True}
    assert "enabled" not in w["changes"]        # inchangé : pas un changement
    assert w["secret"] == "set" and "neuf" not in json.dumps(r)


def test_upsert_dun_tag_libre_annonce_une_creation(banc):
    from oto.tools.common import UpstreamHTTPError

    def _libre(c):
        c.leve_sur["get_webhook"] = UpstreamHTTPError(404, {}, service="typeform")
    banc.prepare = _libre
    r = appeler(banc, "typeform_webhooks", op="upsert", form_id="f1", tag="neuf",
                url="https://hooks.acme.test/tf", enabled=False)
    assert r["would_create"]["url"] == "https://hooks.acme.test/tf"
    assert "once enabled" in r["note"]


def test_upsert_confirme_envoie_les_evenements_en_drapeaux(banc):
    r = appeler(banc, "typeform_webhooks", op="upsert", form_id="f1", tag="crm",
                url="https://hooks.acme.test/tf", enabled=True,
                event_types=["form_response"], secret="sig", confirm=True)
    nom, args, kw = _client(banc).appels[-1]
    assert (nom, args) == ("upsert_webhook", ("f1", "crm"))
    assert kw["event_types"] == {"form_response": True, "form_response_partial": False}
    assert kw["secret"] == "sig"
    assert r["upserted"] is True and _sans_le_secret(r)


@pytest.mark.parametrize("args", [
    {"url": "https://h.test"},                                   # sans enabled
    {"enabled": True},                                           # sans url
    {"url": "http://h.test", "enabled": True},                   # pas https
    {"url": "https://h.test", "enabled": True, "event_types": []},
])
def test_upsert_incomplet_refuse_avant_tout_appel(banc, args):
    refus(banc, "typeform_webhooks", op="upsert", form_id="f1", tag="crm", **args)
    assert banc.construits == []


def test_delete_webhook_en_deux_temps(banc):
    r = appeler(banc, "typeform_webhooks", op="delete", form_id="f1", tag="crm")
    assert _client(banc).noms() == ["get_webhook"]
    assert r["would_delete"]["url"] == WEBHOOK["url"] and _sans_le_secret(r)
    r = appeler(banc, "typeform_webhooks", op="delete", form_id="f1", tag="crm",
                confirm=True)
    assert _client(banc).appels == [("delete_webhook", ("f1", "crm"), {})]
    assert r == {"deleted": True, "form_id": "f1", "tag": "crm"}


@pytest.mark.parametrize("op,args", [
    ("list", {"tag": "crm"}),
    ("list", {"enabled": False}),            # un False explicite est un argument
    ("list", {"confirm": True}),
    ("get", {"tag": "crm", "secret": "x"}),
    ("get", {"tag": "crm", "verify_ssl": False}),
    ("get", {}),                              # sans tag
    ("delete", {"tag": "crm", "url": "https://h.test"}),
    ("delete", {"tag": "crm", "full": True}),
    ("upsert", {"tag": "crm", "url": "https://h.test", "enabled": True, "full": True}),
])
def test_webhooks_refuse_ce_que_lop_nutilise_pas(banc, op, args):
    refus(banc, "typeform_webhooks", op=op, form_id="f1", **args)


def test_le_vrai_client_ne_rend_pas_le_secret_non_plus(monkeypatch):
    """Sans double : le client oto-core réel, la réponse HTTP simulée PORTE le
    secret — ni la vue, ni le brut ne le servent."""
    import asyncio

    import requests
    from fastmcp import FastMCP

    from oto_mcp import access
    from oto_mcp.tools import typeform_webhooks

    class _Reponse:
        status_code = 200
        content = b"x"

        def json(self):
            return {"items": [dict(WEBHOOK)]}

    monkeypatch.setattr(access, "resolve_credential_fields",
                        lambda provider, account=None: {"key": "tfp_test"})
    monkeypatch.setattr(requests.Session, "request", lambda self, *a, **kw: _Reponse())
    mcp = FastMCP("banc-typeform-reel")
    typeform_webhooks.register(mcp)
    for args in ({}, {"full": True}):
        r = asyncio.run(mcp.call_tool("typeform_webhooks",
                                      {"form_id": "f1", **args})).structured_content
        assert _sans_le_secret(r)


def test_le_journal_dappels_masque_le_secret_du_webhook():
    """L'argument `secret` est une vraie credential (qui l'a forge les envois) :
    il ne part pas en clair dans `tool_calls`."""
    from oto_mcp.calllog import truncated_args

    ligne = truncated_args({"form_id": "f1", "secret": "sig-key"}, tool="typeform_webhooks")
    assert "sig-key" not in str(ligne) and "f1" in str(ligne)
