"""Dispatch `op=` des 4 tools `greenhouse_*` (ADR 0047 §Amendement, appliqué au
connecteur greenhouse : 9 tools → 4).

Le module n'avait AUCUN test : la consolidation déplace le risque exactement là où
rien ne regardait — une op mal câblée appelle silencieusement la mauvaise méthode du
client, et rien ne casse au boot. Deux d'entre elles ÉCRIVENT dans l'ATS d'un client
(créer une fiche candidat, publier une note dans son fil d'activité, lue par l'équipe
de recrutement) : d'où, pour chaque op, la méthode client appelée, le mutisme des
voisines dangereuses, et le refus explicite d'une op inconnue ou d'un argument
manquant — jamais un fallback muet.
"""
import asyncio
from unittest.mock import MagicMock

import pytest
from oto_mcp.mcp_errors import McpError
def _tool(name: str):
    from fastmcp import FastMCP
    from oto_mcp.tools import greenhouse as G

    m = FastMCP("t")
    G.register(m)
    return asyncio.run(m.get_tool(name)).fn


@pytest.fixture
def client(monkeypatch):
    """Faux `GreenhouseClient` — la clé Harvest est résolue par `access`, qu'on
    court-circuite (aucun coffre, aucun appel réseau)."""
    from oto.tools.greenhouse import client as gh_client

    inst = MagicMock()
    monkeypatch.setattr(gh_client, "GreenhouseClient", lambda **kw: inst)
    monkeypatch.setattr("oto_mcp.access.resolve_api_key", lambda p: ("k", None))
    return inst


# --- candidat : lectures ------------------------------------------------------

@pytest.mark.parametrize("op,kwargs,method", [
    ("list", {}, "list_candidates"),
    ("get", {"candidate_id": 456}, "get_candidate"),
])
def test_candidate_read_ops_route_to_the_right_client_method(client, op, kwargs, method):
    _tool("greenhouse_candidate")(op=op, **kwargs)
    getattr(client, method).assert_called_once()


def test_candidate_list_forwards_every_filter(client):
    """Les 6 filtres de la liste étaient la signature entière de l'ex-tool
    `greenhouse_candidates` : aucun ne doit se perdre dans le dispatch."""
    _tool("greenhouse_candidate")(
        op="list", per_page=200, page=3, job_id=12, email="a@b.com",
        created_after="2026-01-01T00:00:00Z", updated_after="2026-02-01T00:00:00Z")
    assert client.list_candidates.call_args.kwargs == {
        "per_page": 200, "page": 3, "job_id": 12, "email": "a@b.com",
        "created_after": "2026-01-01T00:00:00Z",
        "updated_after": "2026-02-01T00:00:00Z"}


def test_candidate_list_is_the_default_op(client):
    """Défaut = LECTURE : un appel sans `op` ne peut ni créer ni annoter."""
    _tool("greenhouse_candidate")()
    client.list_candidates.assert_called_once()
    client.add_candidate.assert_not_called()
    client.add_note.assert_not_called()


# --- candidat : ÉCRITURES -----------------------------------------------------

def test_candidate_create_writes_and_carries_the_acting_user(client):
    """ÉCRITURE : crée une fiche dans l'ATS. Greenhouse exige l'`On-Behalf-Of` —
    il doit atteindre le client, pas être avalé par le dispatch."""
    payload = {"first_name": "Marie", "last_name": "Dupont"}
    _tool("greenhouse_candidate")(op="create", candidate=payload, on_behalf_of=7)
    client.add_candidate.assert_called_once_with(payload, on_behalf_of=7)
    client.add_note.assert_not_called()
    client.list_candidates.assert_not_called()


def test_candidate_add_note_writes_with_author_and_visibility(client):
    """ÉCRITURE : publie une note dans le fil d'activité, lue par l'équipe. Le
    `user_id` (auteur, qui sert aussi d'`On-Behalf-Of`) et la `visibility` sont
    ce qui décide QUI verra la note — les figer ici."""
    _tool("greenhouse_candidate")(op="add_note", candidate_id=456, body="RDV pris",
                                  user_id=7, visibility="admin_only")
    client.add_note.assert_called_once_with(456, "RDV pris", 7,
                                            visibility="admin_only")
    client.add_candidate.assert_not_called()


def test_candidate_add_note_defaults_to_public_visibility(client):
    """Le défaut historique de `greenhouse_add_note` — une note visible de tous.
    S'il changeait en silence, des notes basculeraient de portée sans un mot."""
    _tool("greenhouse_candidate")(op="add_note", candidate_id=456, body="x", user_id=7)
    assert client.add_note.call_args.kwargs["visibility"] == "public"


@pytest.mark.parametrize("kwargs,missing", [
    ({"candidate": {"first_name": "M"}}, "on_behalf_of"),
    ({"on_behalf_of": 7}, "candidate"),
    ({"candidate": {}, "on_behalf_of": 7}, "candidate"),
])
def test_candidate_create_refuses_without_its_required_args(client, kwargs, missing):
    """Pas d'écriture partielle : un `candidate` vide créerait une fiche fantôme,
    un `on_behalf_of` absent ferait échouer Greenhouse avec un message opaque."""
    with pytest.raises(McpError, match=missing):
        _tool("greenhouse_candidate")(op="create", **kwargs)
    client.add_candidate.assert_not_called()


@pytest.mark.parametrize("kwargs,missing", [
    ({"body": "x", "user_id": 7}, "candidate_id"),
    ({"candidate_id": 456, "user_id": 7}, "body"),
    ({"candidate_id": 456, "body": "", "user_id": 7}, "body"),
    ({"candidate_id": 456, "body": "x"}, "user_id"),
])
def test_candidate_add_note_refuses_without_its_required_args(client, kwargs, missing):
    with pytest.raises(McpError, match=missing):
        _tool("greenhouse_candidate")(op="add_note", **kwargs)
    client.add_note.assert_not_called()


# --- job / candidature --------------------------------------------------------

@pytest.mark.parametrize("op,kwargs,method", [
    ("list", {}, "list_jobs"),
    ("get", {"job_id": 12}, "get_job"),
])
def test_job_ops_route_to_the_right_client_method(client, op, kwargs, method):
    _tool("greenhouse_job")(op=op, **kwargs)
    getattr(client, method).assert_called_once()


def test_job_list_forwards_its_status_filter(client):
    _tool("greenhouse_job")(op="list", per_page=100, page=2, status="open")
    assert client.list_jobs.call_args.kwargs == {
        "per_page": 100, "page": 2, "status": "open"}


@pytest.mark.parametrize("op,kwargs,method", [
    ("list", {}, "list_applications"),
    ("get", {"application_id": 99}, "get_application"),
])
def test_application_ops_route_to_the_right_client_method(client, op, kwargs, method):
    _tool("greenhouse_application")(op=op, **kwargs)
    getattr(client, method).assert_called_once()


def test_application_list_forwards_both_filters(client):
    _tool("greenhouse_application")(op="list", per_page=100, page=2, job_id=12,
                                    status="hired")
    assert client.list_applications.call_args.kwargs == {
        "per_page": 100, "page": 2, "job_id": 12, "status": "hired"}


def test_application_list_keeps_job_id_as_a_filter(client):
    """`job_id` est un vrai filtre de liste ici (≠ `greenhouse_job`, où c'est l'id
    de l'objet) : il ne doit surtout PAS tomber sous le refus d'id."""
    _tool("greenhouse_application")(op="list", job_id=12)
    assert client.list_applications.call_args.kwargs["job_id"] == 12


# --- annuaire des recruteurs (resté SEUL) -------------------------------------

def test_users_stays_a_tool_of_its_own(client):
    """Seul objet « utilisateur » du connecteur, un seul verbe : pas de `op=`.
    C'est lui qui fournit l'`on_behalf_of` / `user_id` des écritures."""
    _tool("greenhouse_users")(per_page=100, page=2)
    client.list_users.assert_called_once_with(per_page=100, page=2)


# --- refus --------------------------------------------------------------------

@pytest.mark.parametrize("tool,expected", [
    ("greenhouse_candidate", "'list', 'get', 'create' ou 'add_note'"),
    ("greenhouse_job", "'list' ou 'get'"),
    ("greenhouse_application", "'list' ou 'get'"),
])
def test_unknown_op_is_refused_with_the_allowed_list(client, tool, expected):
    """Une op inconnue doit lever en NOMMANT les ops valides — jamais retomber
    silencieusement sur le défaut (l'agent croirait sa demande honorée)."""
    with pytest.raises(McpError, match="op doit être") as e:
        _tool(tool)(op="nope")
    assert expected in str(e.value)


def test_unknown_op_never_reaches_a_write(client):
    with pytest.raises(McpError):
        _tool("greenhouse_candidate")(op="delete", candidate_id=456)
    client.add_candidate.assert_not_called()
    client.add_note.assert_not_called()


@pytest.mark.parametrize("tool,op,missing", [
    ("greenhouse_candidate", "get", "candidate_id"),
    ("greenhouse_job", "get", "job_id"),
    ("greenhouse_application", "get", "application_id"),
])
def test_missing_required_arg_names_the_op_and_the_arg(client, tool, op, missing):
    with pytest.raises(McpError, match=missing) as e:
        _tool(tool)(op=op)
    assert f"op='{op}'" in str(e.value)


@pytest.mark.parametrize("tool,arg,method", [
    ("greenhouse_candidate", "candidate_id", "list_candidates"),
    ("greenhouse_job", "job_id", "list_jobs"),
    ("greenhouse_application", "application_id", "list_applications"),
])
def test_an_object_id_under_op_list_is_refused_not_ignored(client, tool, arg, method):
    """Avant la consolidation, `greenhouse_candidate(candidate_id=456)` lisait UNE
    fiche. Sous le nouveau défaut `op='list'`, l'accepter en silence rendrait la
    page entière en faisant croire à l'agent qu'on a répondu à sa question — d'où
    un refus qui nomme `op='get'`."""
    with pytest.raises(McpError, match=arg) as e:
        _tool(tool)(**{arg: 1})
    assert "op='get'" in str(e.value)
    getattr(client, method).assert_not_called()
