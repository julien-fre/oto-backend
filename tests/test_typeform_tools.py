"""Connecteur Typeform, les LECTURES — `typeform_workspaces`, `typeform_forms`
op list|get, `typeform_responses` (les écritures : `test_typeform_ecritures.py`).

Les tripwires génériques couvrent le registre, l'éditeur, le logo, la prose
servie et la jointure au client oto-core. Ce fichier verrouille ce qui est
PROPRE à ce module, par le VRAI chemin FastMCP (`mcp.call_tool`), le client
remplacé par un double en mémoire (`_typeform_banc.py`) :

- la région du credential choisit l'hôte, une région inconnue est refusée ;
- les vues resserrées (`full=True` rend le brut) et ce qu'elles nomment retiré ;
- les réponses lisibles : intitulé → valeur, homonymes départagés par l'id ;
- le refus d'un data center qui rendrait des réponses VIDES ;
- la pagination par `before`, la borne de page, les arguments ignorés refusés ;
- la traduction des refus amont (401/403/404) ; 429 et 5xx restent typés.
"""
from __future__ import annotations

import asyncio

import pytest

from _typeform_banc import EU, FORM, RESPONSE, appeler as _appeler, banc, refus as _refus  # noqa: F401
from oto_mcp.tools import typeform as T


def test_les_outils_du_connecteur(banc):
    """Les trois modules montés : lectures historiques inchangées, écritures à côté."""
    noms = {t.name for t in asyncio.run(banc.mcp.list_tools(run_middleware=False))}
    assert noms == {"typeform_workspaces", "typeform_forms", "typeform_responses",
                    "typeform_responses_summary", "typeform_delete_responses",
                    "typeform_webhooks"}


def test_les_outils_qui_ne_font_que_lire_le_declarent(banc):
    from oto_mcp.tools.lecture import en_lecture
    outils = {t.name: t for t in asyncio.run(banc.mcp.list_tools(run_middleware=False))}
    lecteurs = {n for n, t in outils.items() if en_lecture(t)}
    assert lecteurs == {"typeform_workspaces", "typeform_responses",
                        "typeform_responses_summary"}


def test_forms_garde_list_par_defaut(banc):
    _appeler(banc, "typeform_forms")
    assert banc.construits[0].noms() == ["list_forms"]


# --- région -----------------------------------------------------------------

def test_region_absente_vise_les_us(banc):
    _appeler(banc, "typeform_forms")
    assert banc.construits[0].region == "us"
    assert banc.construits[0].access_token == "tfp_test"


def test_la_region_posee_choisit_lhote(banc):
    banc.champs["region"] = "EU "
    _appeler(banc, "typeform_forms")
    assert banc.construits[0].BASE_URL == EU


def test_une_region_inconnue_est_refusee(banc):
    banc.champs["region"] = "asia"
    _refus(banc, "typeform_forms")
    assert banc.construits == []


# --- workspaces & formulaires ------------------------------------------------

def test_workspaces_vue_resserree(banc):
    r = _appeler(banc, "typeform_workspaces", search="Ventes", page_size=5)
    assert r["workspaces"] == [{"id": "w1", "name": "Ventes", "shared": False,
                                "forms_count": 3, "account_id": "a1"}]
    assert banc.construits[0].appels == [
        ("list_workspaces", (), {"search": "Ventes", "page": None, "page_size": 5})]


def test_forms_list_resserree_et_brut(banc):
    r = _appeler(banc, "typeform_forms", workspace_id="w1", sort_by="last_updated_at")
    assert r["forms"][0] == {"id": "f1", "title": "Satisfaction",
                             "last_updated_at": "2026-09-01T00:00:00Z",
                             "created_at": "2026-01-01T00:00:00Z", "is_public": True,
                             "url": "https://acme.typeform.com/to/f1"}
    brut = _appeler(banc, "typeform_forms", full=True)
    assert "self" in brut["items"][0]


def test_forms_get_rend_les_questions_et_nomme_le_retire(banc):
    r = _appeler(banc, "typeform_forms", op="get", form_id="f1")
    assert [f["id"] for f in r["fields"]] == ["q1", "q2", "g1"]
    assert r["fields"][0]["required"] is True
    assert r["fields"][1]["choices"] == ["Lyon", "Paris"]
    assert r["fields"][1]["multiple"] is True
    assert [f["id"] for f in r["fields"][2]["fields"]] == ["q3", "q4"]
    assert "logic" not in r and "settings" not in r and "welcome_screens" not in r
    assert "logic" in r["omitted"]
    assert r["hidden"] == ["utm_source"]


def test_forms_get_sans_form_id_refuse(banc):
    _refus(banc, "typeform_forms", op="get")


@pytest.mark.parametrize("arg", [{"search": "x"}, {"page": 2}, {"workspace_id": "w"}])
def test_forms_get_refuse_un_argument_de_liste(banc, arg):
    _refus(banc, "typeform_forms", op="get", form_id="f1", **arg)


def test_forms_list_refuse_form_id(banc):
    _refus(banc, "typeform_forms", op="list", form_id="f1")


# --- réponses ----------------------------------------------------------------

def test_reponses_lisibles(banc):
    r = _appeler(banc, "typeform_responses", form_id="f1")
    assert r["total_items"] == 1 and r["form_title"] == "Satisfaction"
    rep = r["responses"][0]
    assert rep["response_id"] == "r1"
    assert rep["submitted_at"] == "2026-09-30T10:02:00Z"
    assert rep["answers"]["Votre nom ?"] == "Jane Doe"
    assert rep["answers"]["Ville ?"] == ["Lyon", "Paris"]
    # Deux sous-questions au même intitulé : départagées par l'id, aucune écrasée.
    assert rep["answers"]["Commentaire [q3]"] == 5
    assert rep["answers"]["Commentaire [q4]"] == "RAS"
    assert rep["hidden"] == {"utm_source": "newsletter"}
    assert rep["variables"] == {"score": 4}
    assert "metadata" not in rep and "score" not in rep
    assert "metadata" in r["omitted"]


def test_reponses_sans_intitules_par_ref_et_sans_lire_le_formulaire(banc):
    r = _appeler(banc, "typeform_responses", form_id="f1", titles=False)
    assert r["responses"][0]["answers"]["nom"] == "Jane Doe"
    assert [a[0] for a in banc.construits[0].appels] == ["list_responses"]


def test_reponses_brutes(banc):
    r = _appeler(banc, "typeform_responses", form_id="f1", full=True)
    assert r["items"][0]["metadata"]["referer"] == "https://acme.test"
    assert [a[0] for a in banc.construits[0].appels] == ["list_responses"]


def test_reponses_transmet_les_filtres(banc):
    _appeler(banc, "typeform_responses", form_id="f1", page_size=50,
             since="2026-09-01T00:00:00", response_type=["partial"], query="Lyon",
             fields=["q1"], answered_fields=["q2"], included_response_ids=["r1"])
    nom, args, kw = banc.construits[0].appels[-1]
    assert (nom, args) == ("list_responses", ("f1",))
    assert kw["page_size"] == 50 and kw["since"] == "2026-09-01T00:00:00"
    assert kw["response_type"] == ["partial"] and kw["query"] == "Lyon"
    assert kw["fields"] == ["q1"] and kw["answered_fields"] == ["q2"]
    assert kw["included_response_ids"] == ["r1"]


def test_page_pleine_donne_le_curseur_before(banc):
    def _deux(c):
        c.page = {"total_items": 7, "page_count": 4, "items": [
            dict(RESPONSE, response_id="r9", token="t9"),
            dict(RESPONSE, response_id="r8", token="t8")]}
    banc.prepare = _deux
    r = _appeler(banc, "typeform_responses", form_id="f1", page_size=2)
    assert r["next_before"] == "t8"
    banc.prepare = None
    r = _appeler(banc, "typeform_responses", form_id="f1", page_size=2)
    assert "next_before" not in r      # page incomplète : rien après


@pytest.mark.parametrize("taille", [0, T.RESPONSES_MAX_PAGE + 1])
def test_page_bornee(banc, taille):
    _refus(banc, "typeform_responses", form_id="f1", page_size=taille)


def test_before_et_after_ensemble_refuses(banc):
    _refus(banc, "typeform_responses", form_id="f1", before="a", after="b")


def test_data_center_qui_rendrait_vide_est_refuse(banc):
    def _compte_eu(c):
        c.form = dict(FORM, _links={"responses": f"{EU}/forms/f1/responses"})
    banc.prepare = _compte_eu
    e = _refus(banc, "typeform_responses", form_id="f1")
    assert "« eu »" in e.error.message      # la phrase EST le remède : elle nomme la région
    assert [a[0] for a in banc.construits[0].appels] == ["get_form"]


def test_meme_data_center_passe(banc):
    banc.champs["region"] = "eu"

    def _compte_eu(c):
        c.form = dict(FORM, _links={"responses": f"{EU}/forms/f1/responses"})
    banc.prepare = _compte_eu
    r = _appeler(banc, "typeform_responses", form_id="f1")
    assert r["responses"][0]["response_id"] == "r1"


# --- valeurs de réponse ------------------------------------------------------

@pytest.mark.parametrize("answer,value", [
    ({"type": "choice", "choice": {"label": "Tokyo"}}, "Tokyo"),
    ({"type": "choice", "choice": {"other": "Lima"}}, "Lima"),
    ({"type": "choices", "choices": {"labels": ["A"], "other": "B"}}, ["A", "B"]),
    ({"type": "boolean", "boolean": False}, False),
    ({"type": "number", "number": 0}, 0),
    ({"type": "date", "date": "2012-03-20T00:00:00Z"}, "2012-03-20T00:00:00Z"),
    ({"type": "payment", "payment": {"amount": "10", "success": True}},
     {"amount": "10", "success": True}),
    ({"type": "nouveau", "x": 1}, {"x": 1}),
])
def test_valeur_dune_reponse(answer, value):
    assert T.answer_value({"field": {"id": "q"}, **answer}) == value


# --- refus amont ---------------------------------------------------------------

@pytest.mark.parametrize("status", [401, 403, 404, 400])
def test_un_4xx_devient_un_refus_nomme(banc, status):
    from oto.tools.common import UpstreamHTTPError

    def _leve(c):
        c.leve = UpstreamHTTPError(status, {"code": "X", "description": "d"},
                                   service="typeform")
    banc.prepare = _leve
    _refus(banc, "typeform_forms")


@pytest.mark.parametrize("status", [429, 503])
def test_429_et_5xx_restent_typés(banc, status):
    from oto.tools.common import UpstreamHTTPError

    def _leve(c):
        c.leve = UpstreamHTTPError(status, "busy", service="typeform")
    banc.prepare = _leve
    fn = {t.name: t for t in asyncio.run(banc.mcp._list_tools())}["typeform_forms"].fn
    with pytest.raises(UpstreamHTTPError) as e:
        fn()
    assert e.value.status_code == status
