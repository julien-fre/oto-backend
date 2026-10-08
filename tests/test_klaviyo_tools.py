"""Connecteur Klaviyo — les dix outils `klaviyo_*` (deux modules et leur socle).

Les tripwires génériques couvrent le registre, l'éditeur, le logo, la prose
servie et la jointure au client de la bibliothèque. Ce fichier verrouille ce qui
est PROPRE au connecteur, par le VRAI chemin FastMCP (`mcp.call_tool`), le client
remplacé par un double en mémoire :

- les vues resserrées (`full=True` rend le brut) et ce qu'elles nomment retiré ;
- la pagination dite honnêtement : `next_cursor` lu dans `links.next`, une
  traversée bornée qui le DIT (`complete`) ;
- les quatre écritures sensibles rendent un APERÇU tant que `confirm=True` n'est
  pas passé — aucun appel d'écriture ne part ; l'aperçu nomme ce qui atteint des
  personnes (double opt-in, désabonnement global hors liste) ;
- les corps JSON:API construits depuis les arguments nommés ;
- les arguments qu'un op n'utilise pas, refusés ;
- la traduction des refus amont (4xx → refus nommé ; 429 et 5xx restent typés).
"""
from __future__ import annotations

import asyncio

import pytest
from fastmcp import FastMCP
from mcp.types import INVALID_PARAMS

from oto_mcp import access
from oto_mcp.mcp_errors import McpError
from oto_mcp.tools import klaviyo as K
from oto_mcp.tools import klaviyo_marketing as KM
from oto_mcp.tools import klaviyo_socle as S

PID = "01HZX8A6R0J3VQ2N9P4K7T5M1B"
NEXT = "https://a.klaviyo.com/api/profiles/?page%5Bcursor%5D=bmV4dA&page%5Bsize%5D=20"

PROFILE = {"type": "profile", "id": PID, "links": {"self": "x"},
           "attributes": {"email": "jane@example.com", "first_name": "Jane",
                          "last_name": "Doe", "created": "2026-01-01T00:00:00Z",
                          "location": {"city": "Lyon"}, "properties": {"plan": "pro"},
                          "title": None}}


class _FauxClient:
    """Double en mémoire du client de la bibliothèque : rend des réponses, note les appels."""

    def __init__(self, api_key):
        self.api_key = api_key
        self.appels = []
        self.leve = None
        self.reponses = {}

    def _note(self, nom, *args, **kw):
        self.appels.append((nom, args, kw))
        if self.leve:
            raise self.leve
        return self.reponses.get(nom, {})

    def __getattr__(self, nom):
        if nom.startswith("_"):
            raise AttributeError(nom)
        return lambda *a, **kw: self._note(nom, *a, **kw)


class _Banc:
    prepare = None


@pytest.fixture
def banc(monkeypatch):
    b = _Banc()
    b.construits = []
    monkeypatch.setattr(access, "resolve_api_key",
                        lambda provider, account=None, units=1: ("pk_test", False))
    import oto.tools.klaviyo as pkg

    def _construire(api_key=None, **kw):
        c = _FauxClient(api_key)
        if b.prepare:
            b.prepare(c)
        b.construits.append(c)
        return c

    monkeypatch.setattr(pkg, "KlaviyoClient", _construire)
    b.mcp = FastMCP("banc-klaviyo")
    K.register(b.mcp)
    KM.register(b.mcp)
    return b


def _appeler(b, outil, **arguments):
    return asyncio.run(b.mcp.call_tool(outil, arguments)).structured_content


def _fn(b, outil):
    return {t.name: t for t in asyncio.run(b.mcp._list_tools())}[outil].fn


def _refus(b, outil, **arguments) -> McpError:
    """Le refus, lu sur la fonction de l'outil : à travers `call_tool`, fastmcp
    l'enveloppe en `ToolError` et son code ne se lit plus."""
    with pytest.raises(McpError) as e:
        _fn(b, outil)(**arguments)
    assert e.value.error.code == INVALID_PARAMS
    return e.value


def _appels(b):
    return [a[0] for c in b.construits for a in c.appels]


def _repondre(b, **reponses):
    b.prepare = lambda c: c.reponses.update(reponses)


# --- surface -------------------------------------------------------------------

def test_dix_outils_et_lecture_declaree_seulement_sur_les_lecteurs(banc):
    outils = {t.name: t for t in asyncio.run(banc.mcp.list_tools(run_middleware=False))}
    assert set(outils) == {
        "klaviyo_account", "klaviyo_profiles", "klaviyo_lists", "klaviyo_segments",
        "klaviyo_consent", "klaviyo_campaigns", "klaviyo_flows", "klaviyo_metrics",
        "klaviyo_events", "klaviyo_reports"}
    lecteurs = {n for n, t in outils.items()
                if getattr(t.annotations, "readOnlyHint", None) is True}
    assert lecteurs == {"klaviyo_account", "klaviyo_segments", "klaviyo_campaigns",
                        "klaviyo_flows", "klaviyo_metrics", "klaviyo_reports"}


def test_la_cle_resolue_est_celle_du_client(banc):
    _repondre(banc, get_account={"data": [{"id": "Ab1", "attributes": {
        "contact_information": {"organization_name": "Acme", "street_address": {}},
        "timezone": "Europe/Paris", "public_api_key": "Ab1"}}]})
    r = _appeler(banc, "klaviyo_account")
    assert banc.construits[0].api_key == "pk_test"
    assert r["organization_name"] == "Acme" and r["timezone"] == "Europe/Paris"
    assert "public_api_key" not in r and "public_api_key" in r["omitted"]


# --- profils & pagination -------------------------------------------------------

def test_profiles_list_vue_resserree_et_curseur(banc):
    _repondre(banc, list_profiles={"data": [PROFILE], "links": {"next": NEXT}})
    r = _appeler(banc, "klaviyo_profiles", filter='equals(email,"jane@example.com")',
                 page_size=20)
    assert r["profiles"] == [{"id": PID, "email": "jane@example.com", "first_name": "Jane",
                              "last_name": "Doe", "created": "2026-01-01T00:00:00Z"}]
    assert r["next_cursor"] == "bmV4dA" and r["count"] == 1
    assert "properties" in r["omitted"] and "complete" not in r
    nom, _, kw = banc.construits[0].appels[0]
    assert nom == "list_profiles" and kw["all_pages"] is False
    assert kw["filter"] == 'equals(email,"jane@example.com")' and kw["page_size"] == 20


def test_une_traversee_bornee_dit_quelle_nest_pas_complete(banc):
    _repondre(banc, list_profiles={"data": [PROFILE], "links": {"next": NEXT}, "pages": 3})
    r = _appeler(banc, "klaviyo_profiles", all_pages=True, max_pages=3)
    assert r["complete"] is False and r["pages"] == 3 and r["next_cursor"] == "bmV4dA"
    kw = banc.construits[0].appels[0][2]
    assert kw["all_pages"] is True and kw["max_pages"] == 3


def test_une_traversee_finie_se_dit_complete(banc):
    _repondre(banc, list_profiles={"data": [PROFILE], "links": {"next": None}, "pages": 1})
    r = _appeler(banc, "klaviyo_profiles", all_pages=True)
    assert r["complete"] is True and r["next_cursor"] is None


@pytest.mark.parametrize("args", [{"max_pages": 2}, {"all_pages": True, "max_pages": 0},
                                  {"all_pages": True, "max_pages": S.MAX_PAGES + 1}])
def test_max_pages_borne_et_seulement_en_traversee(banc, args):
    _refus(banc, "klaviyo_profiles", **args)
    assert banc.construits == []


def test_profiles_brut(banc):
    _repondre(banc, list_profiles={"data": [PROFILE], "links": {}})
    assert _appeler(banc, "klaviyo_profiles", full=True)["data"][0]["links"] == {"self": "x"}


def test_profiles_get_nomme_listes_et_segments(banc):
    _repondre(banc, get_profile={"data": PROFILE, "included": [
        {"type": "list", "id": "Y6nRLr", "attributes": {"name": "Newsletter"}},
        {"type": "segment", "id": "Xy12Ab", "attributes": {"name": "VIP"}}]})
    r = _appeler(banc, "klaviyo_profiles", op="get", profile_id=PID,
                 include=["lists", "segments"])
    assert r["location"] == {"city": "Lyon"} and r["properties"] == {"plan": "pro"}
    assert r["lists"] == [{"id": "Y6nRLr", "name": "Newsletter"}]
    assert r["segments"] == [{"id": "Xy12Ab", "name": "VIP"}]
    assert "title" not in r


@pytest.mark.parametrize("args", [{"op": "get"}, {"op": "get", "profile_id": PID, "filter": "x"},
                                  {"op": "list", "profile_id": PID},
                                  {"op": "list", "attributes": {"email": "a@b.test"}}])
def test_profiles_arguments_hors_op_refuses(banc, args):
    _refus(banc, "klaviyo_profiles", **args)


def test_upsert_construit_le_corps(banc):
    _repondre(banc, create_or_update_profile={"data": PROFILE})
    r = _appeler(banc, "klaviyo_profiles", op="upsert", profile_id=PID,
                 attributes={"first_name": "Jane", "phone_number": None},
                 patch_properties={"append": {"tags": "vip"}})
    nom, args, _ = banc.construits[0].appels[0]
    assert nom == "create_or_update_profile"
    assert args[0] == {"type": "profile", "id": PID,
                       "attributes": {"first_name": "Jane", "phone_number": None},
                       "meta": {"patch_properties": {"append": {"tags": "vip"}}}}
    assert r["profile"]["id"] == PID


def test_upsert_sans_identifiant_refuse(banc):
    _refus(banc, "klaviyo_profiles", op="upsert", attributes={"first_name": "Jane"})
    assert _appels(banc) == []


def test_upsert_le_consentement_nest_pas_un_attribut(banc):
    e = _refus(banc, "klaviyo_profiles", op="upsert",
               attributes={"email": "a@b.test", "subscriptions": {}})
    assert "klaviyo_consent" in e.error.message


# --- listes : l'ajout est un aperçu tant que confirm n'est pas passé -------------

def test_lists_add_sans_confirm_est_un_apercu(banc):
    _repondre(banc, get_list={"data": {"id": "Y6nRLr", "attributes": {
        "name": "Newsletter", "opt_in_process": "single_opt_in"}}})
    r = _appeler(banc, "klaviyo_lists", op="add", list_id="Y6nRLr", profile_ids=[PID])
    assert r["preview"] is True and r["sent"] is False
    assert r["list"]["name"] == "Newsletter" and r["profiles"] == 1
    assert _appels(banc) == ["get_list"]


def test_lists_add_confirme(banc):
    r = _appeler(banc, "klaviyo_lists", op="add", list_id="Y6nRLr", profile_ids=[PID],
                 confirm=True)
    assert r == {"added": 1, "list_id": "Y6nRLr"}
    assert banc.construits[0].appels == [
        ("add_profiles_to_list", ("Y6nRLr", [{"type": "profile", "id": PID}]), {})]


def test_un_email_nest_pas_un_id_de_profil(banc):
    _refus(banc, "klaviyo_lists", op="add", list_id="Y6nRLr",
           profile_ids=["jane@example.com"], confirm=True)
    assert _appels(banc) == []


def test_lists_remove_part_directement(banc):
    r = _appeler(banc, "klaviyo_lists", op="remove", list_id="Y6nRLr", profile_ids=[PID])
    assert r["removed"] == 1
    assert _appels(banc) == ["remove_profiles_from_list"]


def test_remove_ne_prend_pas_confirm(banc):
    _refus(banc, "klaviyo_lists", op="remove", list_id="Y6nRLr", profile_ids=[PID],
           confirm=True)


def test_lists_get_compte(banc):
    _repondre(banc, get_list={"data": {"id": "Y6nRLr", "attributes": {
        "name": "Newsletter", "profile_count": 42}}})
    r = _appeler(banc, "klaviyo_lists", op="get", list_id="Y6nRLr", profile_count=True)
    assert r["profile_count"] == 42
    assert banc.construits[0].appels[0][2] == {"additional_fields": ["profile_count"]}


def test_segments_members(banc):
    _repondre(banc, list_profiles_in_segment={"data": [PROFILE], "links": {}})
    r = _appeler(banc, "klaviyo_segments", op="members", segment_id="Xy12Ab")
    assert r["profiles"][0]["id"] == PID and r["next_cursor"] is None


# --- consentement ---------------------------------------------------------------

def test_subscribe_double_opt_in_annonce_le_message(banc):
    _repondre(banc, get_list={"data": {"id": "Y6nRLr", "attributes": {
        "name": "Newsletter", "opt_in_process": "double_opt_in"}}})
    r = _appeler(banc, "klaviyo_consent", op="subscribe", list_id="Y6nRLr",
                 profiles=[{"email": "jane@example.com"}])
    assert r["preview"] is True and "EMAILS" in r["effect"]
    assert _appels(banc) == ["get_list"]


def test_subscribe_confirme_construit_le_job(banc):
    r = _appeler(banc, "klaviyo_consent", op="subscribe", list_id="Y6nRLr",
                 channels=["email", "sms"], custom_source="Salon",
                 profiles=[{"email": "jane@example.com", "phone_number": "+33612345678"}],
                 confirm=True)
    assert r["accepted"] is True
    nom, args, _ = banc.construits[0].appels[0]
    assert nom == "subscribe_profiles"
    data = args[0]
    assert data["type"] == "profile-subscription-bulk-create-job"
    assert data["relationships"] == {"list": {"data": {"type": "list", "id": "Y6nRLr"}}}
    assert data["attributes"]["custom_source"] == "Salon"
    p = data["attributes"]["profiles"]["data"][0]["attributes"]
    assert p["subscriptions"] == {"email": {"marketing": {"consent": "SUBSCRIBED"}},
                                  "sms": {"marketing": {"consent": "SUBSCRIBED"}}}


def test_sms_sans_telephone_refuse(banc):
    _refus(banc, "klaviyo_consent", op="subscribe", channels=["sms"],
           profiles=[{"email": "jane@example.com"}], confirm=True)
    assert _appels(banc) == []


@pytest.mark.parametrize("args", [{"historical_import": True},
                                  {"consented_at": "2026-01-01T00:00:00Z"}])
def test_import_historique_et_date_vont_ensemble(banc, args):
    _refus(banc, "klaviyo_consent", op="subscribe",
           profiles=[{"email": "jane@example.com"}], **args)


def test_unsubscribe_apercu_nomme_le_desabonnement_global(banc):
    _repondre(banc, get_list={"data": {"id": "Y6nRLr", "attributes": {"name": "N"}}},
              list_profiles_in_list={"data": [
                  {"id": PID, "attributes": {"email": "jane@example.com"}}]})
    r = _appeler(banc, "klaviyo_consent", op="unsubscribe", list_id="Y6nRLr",
                 profiles=[{"email": "Jane@Example.com"}, {"email": "john@example.com"}])
    assert r["preview"] is True
    assert r["unsubscribed_globally"] == [{"email": "john@example.com"}]
    lecture = [a for a in banc.construits[0].appels if a[0] == "list_profiles_in_list"][0]
    assert lecture[2]["filter"] == 'any(email,["Jane@Example.com","john@example.com"])'
    assert "unsubscribe_profiles" not in _appels(banc)


def test_unsubscribe_sans_liste_est_global_pour_tous(banc):
    r = _appeler(banc, "klaviyo_consent", op="unsubscribe",
                 profiles=[{"email": "jane@example.com"}])
    assert "GLOBALLY" in r["effect"]
    assert r["unsubscribed_globally"] == [{"email": "jane@example.com"}]
    assert _appels(banc) == []


def test_unsubscribe_confirme(banc):
    _appeler(banc, "klaviyo_consent", op="unsubscribe",
             profiles=[{"email": "jane@example.com"}], confirm=True)
    nom, args, _ = banc.construits[0].appels[0]
    assert nom == "unsubscribe_profiles"
    assert args[0]["type"] == "profile-subscription-bulk-delete-job"
    assert "relationships" not in args[0]


def test_unsubscribe_ne_prend_pas_de_source(banc):
    _refus(banc, "klaviyo_consent", op="unsubscribe", custom_source="x",
           profiles=[{"email": "jane@example.com"}])


# --- campagnes, flows, évènements, rapports ---------------------------------------

def test_campaigns_canal_dans_le_filtre(banc):
    _repondre(banc, list_campaigns={"data": [{"id": "C1", "attributes": {
        "name": "Promo", "status": "Sent", "send_options": {}}}], "links": {}})
    r = _appeler(banc, "klaviyo_campaigns", channel="sms", filter="equals(status,'Sent')")
    nom, args, _ = banc.construits[0].appels[0]
    assert args[0] == "equals(messages.channel,'sms'),equals(status,'Sent')"
    assert r["campaigns"] == [{"id": "C1", "name": "Promo", "status": "Sent"}]
    assert "send_options" in r["omitted"]


def test_campaigns_le_canal_ne_va_pas_dans_le_filtre(banc):
    _refus(banc, "klaviyo_campaigns", filter="equals(messages.channel,'email')")


def test_events_list_nomme_metrique_et_profil(banc):
    _repondre(banc, list_events={"data": [{"id": "E1", "attributes": {
        "datetime": "2026-09-15T10:00:00+00:00", "uuid": "u", "timestamp": 1,
        "event_properties": {"order_id": "A-1"}},
        "relationships": {"metric": {"data": {"type": "metric", "id": "UxxK4u"}},
                          "profile": {"data": {"type": "profile", "id": PID}}}}],
        "included": [{"type": "metric", "id": "UxxK4u", "attributes": {"name": "Placed Order"}},
                     {"type": "profile", "id": PID, "attributes": {"email": "jane@example.com"}}],
        "links": {}})
    r = _appeler(banc, "klaviyo_events", include=["metric", "profile"])
    assert r["events"] == [{"id": "E1", "datetime": "2026-09-15T10:00:00+00:00",
                            "metric_id": "UxxK4u", "metric": "Placed Order",
                            "profile_id": PID, "profile_email": "jane@example.com",
                            "properties": {"order_id": "A-1"}}]


def test_event_create_apercu_puis_corps(banc):
    r = _appeler(banc, "klaviyo_events", op="create", metric_name="Placed Order",
                 profile={"email": "jane@example.com"}, properties={"order_id": "A-1"},
                 value=24.9, value_currency="EUR")
    assert r["preview"] is True and "START" in r["effect"]
    assert _appels(banc) == []
    _appeler(banc, "klaviyo_events", op="create", metric_name="Placed Order",
             profile={"email": "jane@example.com"}, properties={"order_id": "A-1"},
             backfill=True, confirm=True)
    nom, args, _ = banc.construits[-1].appels[0]
    assert nom == "create_event"
    a = args[0]["attributes"]
    assert a["metric"] == {"data": {"type": "metric", "attributes": {"name": "Placed Order"}}}
    assert a["profile"] == {"data": {"type": "profile",
                                     "attributes": {"email": "jane@example.com"}}}
    assert a["backfill"] is True and "value" not in a


def test_event_profil_inconnu_refuse(banc):
    _refus(banc, "klaviyo_events", op="create", metric_name="X", profile={"name": "Jane"})


def test_reports_campaigns_corps_et_periode(banc):
    _repondre(banc, query_campaign_values={"data": {"attributes": {"results": [
        {"groupings": {"campaign_id": "C1"}, "statistics": {"open_rate": 0.4}}]}},
        "links": {"next": None}})
    r = _appeler(banc, "klaviyo_reports", op="campaigns", statistics=["open_rate"],
                 conversion_metric_id="UxxK4u", timeframe="last_30_days")
    assert r["results"][0]["statistics"] == {"open_rate": 0.4}
    nom, args, kw = banc.construits[0].appels[0]
    assert args[0] == {"type": "campaign-values-report", "attributes": {
        "statistics": ["open_rate"], "conversion_metric_id": "UxxK4u",
        "timeframe": {"key": "last_30_days"}}}


@pytest.mark.parametrize("periode", [{}, {"timeframe": "last_7_days", "since": "2026-01-01"},
                                     {"since": "2026-01-01T00:00:00"}])
def test_reports_une_periode_exactement(banc, periode):
    _refus(banc, "klaviyo_reports", op="flows", statistics=["opens"],
           conversion_metric_id="UxxK4u", **periode)


def test_reports_metric_borne_les_dates_et_decoupe_le_filtre(banc):
    _appeler(banc, "klaviyo_reports", op="metric", metric_id="UxxK4u",
             measurements=["count"], since="2026-09-01T00:00:00",
             until="2026-10-01T00:00:00", interval="day",
             filter='equals($attributed_flow,"Ab12Cd"),any($message,["a","b"])')
    nom, args, _ = banc.construits[0].appels[0]
    assert nom == "query_metric_aggregates"
    assert args[0]["attributes"]["filter"] == [
        "greater-or-equal(datetime,2026-09-01T00:00:00)",
        "less-than(datetime,2026-10-01T00:00:00)",
        'equals($attributed_flow,"Ab12Cd")', 'any($message,["a","b"])']
    assert args[0]["attributes"]["interval"] == "day"


# --- refus amont --------------------------------------------------------------------

def _klaviyo_error(status, code="x"):
    from oto.tools.klaviyo import KlaviyoError
    return KlaviyoError(status, code, "Klaviyo said no.")


def test_un_403_nomme_le_scope_a_ajouter(banc):
    banc.prepare = lambda c: setattr(c, "leve", _klaviyo_error(403, "scope_missing"))
    e = _refus(banc, "klaviyo_flows")
    assert "flows:read" in e.error.message


@pytest.mark.parametrize("status", [400, 401, 404, 409])
def test_un_4xx_devient_un_refus_nomme(banc, status):
    banc.prepare = lambda c: setattr(c, "leve", _klaviyo_error(status))
    _refus(banc, "klaviyo_metrics")


def test_une_verification_locale_devient_un_refus(banc):
    banc.prepare = lambda c: setattr(c, "leve", ValueError("bad id"))
    _refus(banc, "klaviyo_metrics", op="get", metric_id="UxxK4u")


@pytest.mark.parametrize("status", [429, 503])
def test_429_et_5xx_restent_types(banc, status):
    from oto.tools.klaviyo import KlaviyoError
    banc.prepare = lambda c: setattr(c, "leve", _klaviyo_error(status))
    with pytest.raises(KlaviyoError) as e:
        _fn(banc, "klaviyo_segments")()
    assert e.value.status_code == status


# --- socle ------------------------------------------------------------------------

def test_split_clauses():
    assert S.split_clauses('equals(a,"x,y"),any(b,["p","q"]) , has(c)') == [
        'equals(a,"x,y")', 'any(b,["p","q"])', "has(c)"]
    assert S.split_clauses(None) == []


def test_desabonnement_borne_a_100_avant_tout_appel(banc):
    _refus(banc, "klaviyo_consent", op="unsubscribe",
           profiles=[{"email": f"p{i}@example.com"} for i in range(101)])
    assert _appels(banc) == []
