"""Dispatch `op=` des tools `spott_*` (ADR 0047 §Amendement, appliqué au connecteur
spott : 20 tools → 9).

Ce que ce fichier verrouille, et que `test_spott.py` ne couvrait pas : il vérifiait le
registre, la jointure au client oto-core et les DEUX seuls endroits où le module faisait
autre chose que passer le plat. Une consolidation par `op=` déplace précisément le risque
ailleurs : une op mal câblée appelle silencieusement la mauvaise méthode du client, et
rien ne casse au boot. Ici c'est un ATS de cabinet de recrutement — la mauvaise méthode
CRÉE un candidat, DÉPLACE une candidature ou ÉCRIT une note dans des données réelles.

D'où, pour chaque op : la méthode client appelée, le refus d'une op inconnue (message qui
NOMME les ops valides), les arguments obligatoires, et pour chaque écriture un cas dédié
qui vérifie AUSSI le mutisme de ses voisines dangereuses.
"""
import asyncio
from unittest.mock import patch

import pytest
from mcp.shared.exceptions import McpError

# Les 5 écritures du connecteur — aucune ne doit être atteignable sans `op` explicite.
_WRITE_METHODS = ("create_candidate", "update_candidate", "create_application",
                  "move_application", "create_note")


def _tool(name: str):
    """Enregistre le module seul et rend la fonction du tool demandé."""
    from fastmcp import FastMCP
    from oto_mcp.tools import spott

    m = FastMCP("t")
    spott.register(m)
    return asyncio.run(m.get_tool(name)).fn


@pytest.fixture
def client():
    """Faux SpottClient + clé résolue (le module instancie le client à CHAQUE appel)."""
    with patch("oto_mcp.access.resolve_api_key", return_value=("fake-key", False)), \
            patch("oto.tools.spott.client.SpottClient") as cls:
        yield cls.return_value


# --- candidat -----------------------------------------------------------------

@pytest.mark.parametrize("op,kwargs,method", [
    ("list", {}, "list_candidates"),
    ("get", {"candidate_id": "c1"}, "get_candidate"),
    ("search", {"filters": [{"type": "text", "operator": "contains",
                             "path": "candidate.lastName", "value": "dup"}]},
     "search_candidates"),
    ("create", {"candidate": {"firstName": "Jean", "lastName": "Dupont"}},
     "create_candidate"),
    ("update", {"candidate_id": "c1", "patch": {"lastName": "Dupond"}},
     "update_candidate"),
])
def test_candidate_ops_route_to_the_right_client_method(client, op, kwargs, method):
    _tool("spott_candidate")(op=op, **kwargs)
    getattr(client, method).assert_called_once()


def test_candidate_list_passes_its_filters_through(client):
    """Les bornes de sync incrémentale et les plafonds (limit ≤ 50, 25 listes) sont
    la raison d'être de la liste : elles doivent atteindre le client telles quelles."""
    _tool("spott_candidate")(op="list", limit=50, cursor="cur",
                             modified_since="2026-01-01T00:00:00Z",
                             list_ids=["l1"], include=["skills"])
    kw = client.list_candidates.call_args.kwargs
    assert kw["limit"] == 50 and kw["cursor"] == "cur"
    assert kw["modified_since"] == "2026-01-01T00:00:00Z"
    assert kw["list_ids"] == ["l1"] and kw["include"] == ["skills"]


def test_candidate_search_passes_filters_and_page(client):
    flt = [{"type": "text", "operator": "equals",
            "path": "candidate.firstName", "value": "Jean"}]
    _tool("spott_candidate")(op="search", filters=flt, page=1, page_size=10)
    client.search_candidates.assert_called_once_with(filters=flt, page=1, page_size=10)


def test_candidate_create_writes_only_that(client):
    """⚠️ ÉCRIT : crée un candidat dans l'ATS. La création ne doit toucher NI la mise
    à jour d'un candidat existant, NI une candidature."""
    payload = {"firstName": "Jean", "lastName": "Dupont"}
    _tool("spott_candidate")(op="create", candidate=payload)
    client.create_candidate.assert_called_once_with(payload)
    client.update_candidate.assert_not_called()
    client.create_application.assert_not_called()


def test_candidate_update_is_a_patch_on_the_right_id(client):
    """⚠️ ÉCRIT : PATCH partiel. Un id mal câblé écraserait le mauvais dossier."""
    _tool("spott_candidate")(op="update", candidate_id="c1", patch={"lastName": "D."})
    client.update_candidate.assert_called_once_with("c1", {"lastName": "D."})
    client.create_candidate.assert_not_called()


# --- job ----------------------------------------------------------------------

@pytest.mark.parametrize("op,kwargs,method", [
    ("list", {}, "list_jobs"),
    ("get", {"job_id": "v1"}, "get_job"),
    ("search", {"filters": [{"type": "boolean", "operator": "equals",
                             "path": "vacancy.stage.isOpen", "value": True}]},
     "search_jobs"),
])
def test_job_ops_route_to_the_right_client_method(client, op, kwargs, method):
    _tool("spott_job")(op=op, **kwargs)
    getattr(client, method).assert_called_once()


def test_job_list_passes_its_filters_through(client):
    _tool("spott_job")(op="list", company_ids=["k1"],
                       candidate_emails=["a@b.co"], include=["jobBoards"])
    kw = client.list_jobs.call_args.kwargs
    assert kw["company_ids"] == ["k1"]
    assert kw["candidate_emails"] == ["a@b.co"]
    assert kw["include"] == ["jobBoards"]


# --- candidature --------------------------------------------------------------

@pytest.mark.parametrize("op,kwargs,method", [
    ("list", {}, "list_applications"),
    ("list", {"job_id": "v1"}, "applications_by_job"),
    ("list", {"candidate_id": "c1"}, "applications_by_candidate"),
    ("create", {"candidate_id": "c1", "stage_id": "s1", "job_id": "v1"},
     "create_application"),
    ("move", {"application_id": "a1", "stage_id": "s2"}, "move_application"),
])
def test_application_ops_route_to_the_right_client_method(client, op, kwargs, method):
    _tool("spott_application")(op=op, **kwargs)
    getattr(client, method).assert_called_once()


def test_application_list_routes_by_job_candidate_or_listing(client):
    """Les trois variantes de lecture d'un pipeline, dans un seul op — le routage
    était déjà le point sensible du module avant la consolidation."""
    t = _tool("spott_application")
    t(op="list", job_id="v1")
    client.applications_by_job.assert_called_once_with("v1")
    t(op="list", candidate_id="c1")
    client.applications_by_candidate.assert_called_once_with("c1")
    t(op="list", limit=10)
    assert client.list_applications.call_args.kwargs["limit"] == 10


def test_application_list_rejects_job_and_candidate_together(client):
    with pytest.raises(McpError, match="pas les deux"):
        _tool("spott_application")(op="list", job_id="v1", candidate_id="c1")
    client.applications_by_job.assert_not_called()
    client.applications_by_candidate.assert_not_called()


def test_application_create_puts_the_candidate_on_the_job(client):
    """⚠️ ÉCRIT : fait postuler un candidat. L'ordre des positionnels (candidat PUIS
    étape) est ce qui décide qui atterrit où — et `move` ne doit pas être touchée."""
    _tool("spott_application")(op="create", candidate_id="c1", stage_id="s1",
                               job_id="v1", status_id="st1")
    client.create_application.assert_called_once_with(
        "c1", "s1", job_id="v1", status_id="st1", client_id=None)
    client.move_application.assert_not_called()


def test_application_create_speculative_has_no_job(client):
    """Candidature spontanée : pas de job, un client — la variante documentée."""
    _tool("spott_application")(op="create", candidate_id="c1", stage_id="s1",
                               client_id="k1")
    kw = client.create_application.call_args.kwargs
    assert kw["job_id"] is None and kw["client_id"] == "k1"


def test_application_move_changes_the_stage_of_that_application(client):
    """⚠️ ÉCRIT : déplace une candidature dans le pipeline (visible du cabinet)."""
    _tool("spott_application")(op="move", application_id="a1", stage_id="s2",
                               status_id="st2")
    client.move_application.assert_called_once_with("a1", "s2", status_id="st2")
    client.create_application.assert_not_called()


def test_stages_stays_its_own_tool_and_forwards_the_entity(client):
    """`spott_stages` n'est pas fusionné (référentiel transverse aux 4 pipelines) :
    il reste appelable seul, et c'est lui qui fournit les `stage_id` des écritures."""
    _tool("spott_stages")(entity="vacancies", template_id="t1")
    client.pipeline_stages.assert_called_once_with("vacancies", template_id="t1")


# --- note ---------------------------------------------------------------------

@pytest.mark.parametrize("op,kwargs,method", [
    ("list", {}, "list_notes"),
    ("create", {"content": "appel du 3"}, "create_note"),
])
def test_note_ops_route_to_the_right_client_method(client, op, kwargs, method):
    _tool("spott_note")(op=op, **kwargs)
    getattr(client, method).assert_called_once()


def test_note_create_carries_links_source_and_labels(client):
    """⚠️ ÉCRIT : pose une note dans l'ATS. `source`/`label_ids` sont partagés avec
    `op='list'` (où ils FILTRENT) — la confusion des deux rôles est le risque."""
    links = [{"entityType": "candidate", "entityId": "c1"}]
    _tool("spott_note")(op="create", content="appel du 3", title="Suivi",
                        links=links, source="phone", label_ids=["lb1"])
    client.create_note.assert_called_once_with(
        "appel du 3", title="Suivi", links=links, source="phone",
        label_ids=["lb1"])
    client.list_notes.assert_not_called()


def test_note_list_filters_do_not_write(client):
    _tool("spott_note")(op="list", candidate_id="c1", source="phone")
    kw = client.list_notes.call_args.kwargs
    assert kw["candidate_id"] == "c1" and kw["source"] == "phone"
    client.create_note.assert_not_called()


# --- client (CRM du cabinet) ---------------------------------------------------

@pytest.mark.parametrize("op,kwargs,method", [
    ("list", {}, "list_clients"),
    ("get", {"client_id": "k1"}, "get_client"),
    ("search", {"filters": [{"type": "text", "operator": "contains",
                             "path": "client.company.name", "value": "acme"}]},
     "search_clients"),
    ("contacts", {}, "list_client_contacts"),
])
def test_client_ops_route_to_the_right_client_method(client, op, kwargs, method):
    _tool("spott_client")(op=op, **kwargs)
    getattr(client, method).assert_called_once()


def test_client_search_is_now_an_explicit_op(client):
    """Avant la consolidation, passer `filters` à la LISTE basculait implicitement en
    recherche. Le verbe est explicite : la liste refuse `filters` au lieu de les
    ignorer (une liste complète prise pour un résultat filtré est le pire des deux)."""
    t = _tool("spott_client")
    t()
    client.list_clients.assert_called_once()
    client.search_clients.assert_not_called()

    flt = [{"type": "text", "operator": "contains",
            "path": "client.company.name", "value": "acme"}]
    t(op="search", filters=flt, page=1)
    client.search_clients.assert_called_once_with(filters=flt, page=1, page_size=None)

    with pytest.raises(McpError, match="op='search'"):
        t(op="list", filters=flt)


@pytest.mark.parametrize("tool,op", [
    ("spott_candidate", "list"), ("spott_job", "list"),
    ("spott_client", "list"), ("spott_client", "contacts"),
])
def test_filters_are_never_silently_ignored(client, tool, op):
    with pytest.raises(McpError, match="filters"):
        _tool(tool)(op=op, filters=[{"type": "text", "operator": "contains",
                                     "path": "x", "value": "y"}])


def test_client_contacts_restricts_to_client_companies(client):
    _tool("spott_client")(op="contacts", client_ids=["k1"], list_ids=["l1"])
    kw = client.list_client_contacts.call_args.kwargs
    assert kw["client_ids"] == ["k1"] and kw["list_ids"] == ["l1"]


# --- tools laissés seuls (non fusionnés) ---------------------------------------

def test_people_and_users_and_placements_stay_standalone(client):
    """Trois surfaces dont les paramètres ne recouvrent pas ceux des objets fusionnés :
    recherche floue transverse, découverte de recruteurs, pagination par page."""
    _tool("spott_people")("jean dupont", limit=100)
    client.search_people.assert_called_once_with("jean dupont", limit=100)

    _tool("spott_users")(include_deactivated=True)
    client.list_users.assert_called_once_with(include_deactivated=True)

    _tool("spott_placements")(page=2, page_size=100, company_id="k1")
    kw = client.list_placements.call_args.kwargs
    assert kw["page"] == 2 and kw["page_size"] == 100 and kw["company_id"] == "k1"


# --- refus & garde-fous d'écriture ---------------------------------------------

@pytest.mark.parametrize("tool,ops", [
    ("spott_candidate", "'list', 'get', 'search', 'create' ou 'update'"),
    ("spott_job", "'list', 'get' ou 'search'"),
    ("spott_application", "'list', 'create' ou 'move'"),
    ("spott_note", "'list' ou 'create'"),
    ("spott_client", "'list', 'get', 'search' ou 'contacts'"),
])
def test_unknown_op_is_refused_with_the_allowed_list(client, tool, ops):
    """Une op inconnue doit lever en NOMMANT les ops valides — jamais retomber
    silencieusement sur le défaut (l'agent croirait sa demande honorée)."""
    with pytest.raises(McpError, match="op doit être") as e:
        _tool(tool)(op="nope")
    assert ops in str(e.value)


@pytest.mark.parametrize("tool", ["spott_candidate", "spott_job",
                                  "spott_application", "spott_note", "spott_client"])
def test_unknown_op_touches_nothing_upstream(client, tool):
    """Le refus tombe AVANT la résolution de clé : une op inconnue n'atteint jamais
    le client — donc jamais, par un chemin dérivé, une écriture."""
    with pytest.raises(McpError):
        _tool(tool)(op="nope")
    for m in _WRITE_METHODS:
        getattr(client, m).assert_not_called()


@pytest.mark.parametrize("tool,method", [
    ("spott_candidate", "list_candidates"),
    ("spott_job", "list_jobs"),
    ("spott_application", "list_applications"),
    ("spott_note", "list_notes"),
    ("spott_client", "list_clients"),
])
def test_default_op_is_a_read(client, tool, method):
    """INVARIANT : aucune écriture atteignable par défaut. Un appel sans `op` doit
    lire, et ne toucher aucune des 5 méthodes qui modifient l'ATS."""
    _tool(tool)()
    getattr(client, method).assert_called_once()
    for m in _WRITE_METHODS:
        getattr(client, m).assert_not_called()


@pytest.mark.parametrize("tool,op,kwargs,missing", [
    ("spott_candidate", "get", {}, "candidate_id"),
    ("spott_candidate", "create", {}, "candidate"),
    ("spott_candidate", "create", {"candidate": {}}, "candidate"),
    ("spott_candidate", "update", {"patch": {"a": 1}}, "candidate_id"),
    ("spott_candidate", "update", {"candidate_id": "c1"}, "patch"),
    ("spott_job", "get", {}, "job_id"),
    ("spott_application", "create", {"stage_id": "s1"}, "candidate_id"),
    ("spott_application", "create", {"candidate_id": "c1"}, "stage_id"),
    ("spott_application", "move", {"stage_id": "s2"}, "application_id"),
    ("spott_application", "move", {"application_id": "a1"}, "stage_id"),
    ("spott_note", "create", {}, "content"),
    ("spott_note", "create", {"content": ""}, "content"),
    ("spott_client", "get", {}, "client_id"),
])
def test_missing_required_arg_names_the_op_and_the_arg(client, tool, op, kwargs, missing):
    """Un manque sur une écriture ne doit JAMAIS être comblé par un défaut (candidat
    fantôme, note vide, candidature sur la mauvaise étape) : erreur qui nomme l'op ET
    l'argument."""
    with pytest.raises(McpError, match=missing) as e:
        _tool(tool)(op=op, **kwargs)
    assert f"op='{op}'" in str(e.value)
    for m in _WRITE_METHODS:
        getattr(client, m).assert_not_called()


# --- traduction des refus amont (préservée par la consolidation) ---------------

def test_upstream_401_becomes_a_readable_tool_error(client):
    from oto.tools.common.errors import UpstreamHTTPError

    client.list_candidates.side_effect = UpstreamHTTPError(
        401, {"message": "invalid api key"}, service="spott")
    with pytest.raises(McpError, match="clé API"):
        _tool("spott_candidate")()


def test_client_side_value_error_becomes_a_param_error(client):
    """Les validations du client oto-core (pipeline inconnu, `entityType` de note
    inconnu) doivent rester des erreurs de paramètre lisibles, pas un crash."""
    client.create_note.side_effect = ValueError("entityType Spott inconnu : 'lead'")
    with pytest.raises(McpError, match="entityType Spott inconnu"):
        _tool("spott_note")(op="create", content="x",
                            links=[{"entityType": "lead", "entityId": "1"}])
