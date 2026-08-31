"""La FENÊTRE de lecture des listings attio, au niveau du tool.

Quatre signaux d'usage d'une même procédure quotidienne, dont deux sur ce
connecteur : `attio_note op=list` (#586, resignalé #597 onze jours plus tard)
n'exposait ni `limit`, ni `offset`, ni filtre de date et ne rendait que les DIX
notes les plus ANCIENNES du workspace — les comptes rendus d'appels du jour,
l'objet le plus utile de la procédure, étaient inatteignables. Un `op=list` qui
rend silencieusement les dix plus anciens éléments est un filtre caché non
déclaré : la pire des formes.

Ce que le connecteur peut offrir est borné par l'amont, relevé le 27/08/2026 par
différentiel contre l'API réelle (cf. `tests/test_attio_listing_window.py` dans
oto-core pour le tableau complet) : `/notes` ne sait QUE paginer, `/tasks` sait
paginer et trier, `/meetings` sait paginer au CURSEUR, trier, et borner en date.
Aucun des trois n'a de filtre de date sur les notes — donc on n'en expose pas :
un filtre simulé côté client sur une page tronquée mentirait.

Ce test verrouille donc trois choses : les bornes atteignent bien le client,
`attio_meeting` ne prend plus d'`offset` (l'amont l'ignorait), et le défaut de
chaque `op="list"` reste une lecture non contrainte.
"""
import asyncio
from unittest.mock import MagicMock

import pytest
from oto_mcp.mcp_errors import McpError
def _tool(name: str):
    from fastmcp import FastMCP
    from oto_mcp.tools import attio as A

    m = FastMCP("t")
    A.register(m)
    return asyncio.run(m.get_tool(name)).fn


@pytest.fixture
def client(monkeypatch):
    import oto.tools.attio.client as core

    inst = MagicMock()
    monkeypatch.setattr(core, "AttioClient", lambda **kw: inst)
    monkeypatch.setattr("oto_mcp.access.resolve_api_key", lambda *a, **k: ("k", False))
    return inst


# --- notes --------------------------------------------------------------------

def test_note_list_transmet_la_page_demandee(client):
    """Sans `offset`, les notes récentes — en FIN de collection, l'API sortant
    des plus anciennes — restaient hors d'atteinte (#586, #597)."""
    _tool("attio_note")(op="list", limit=50, offset=150)
    assert client.notes.list.call_args.kwargs == {
        "parent_object": None, "parent_record_id": None, "limit": 50, "offset": 150,
    }


def test_note_list_sans_borne_n_invente_rien(client):
    """Le défaut reste celui d'Attio (10, les plus anciennes) — annoncé dans la
    docstring plutôt que masqué par un défaut maison."""
    _tool("attio_note")(op="list")
    assert client.notes.list.call_args.kwargs == {
        "parent_object": None, "parent_record_id": None, "limit": None, "offset": None,
    }


def test_note_ne_prend_pas_de_filtre_de_date(client):
    """`/v2/notes` avale les paramètres qu'il ne connaît pas en rendant 200 :
    exposer `created_after` produirait un filtre qui MENT. Le tool le refuse en
    nommant l'argument, la docstring dit pourquoi."""
    with pytest.raises(TypeError, match="created_after"):
        _tool("attio_note")(op="list", created_after="2026-08-26")


# --- tâches -------------------------------------------------------------------

def test_task_list_transmet_page_et_tri(client):
    _tool("attio_task")(op="list", limit=100, offset=100, sort="created_at:desc")
    kwargs = client.tasks.list.call_args.kwargs
    assert kwargs["limit"] == 100 and kwargs["offset"] == 100
    assert kwargs["sort"] == "created_at:desc"


def test_task_completed_filtre_toujours_la_liste(client):
    """Non-régression du contrat public : le paramètre du tool garde son nom,
    même si le fil porte désormais `is_completed` (le nom d'Attio)."""
    _tool("attio_task")(op="list", completed=False)
    assert client.tasks.list.call_args.kwargs["completed"] is False
    client.tasks.update.assert_not_called()


# --- réunions -----------------------------------------------------------------

def test_meeting_list_pagine_au_curseur_et_borne_les_dates(client):
    """Les réunions sont le SEUL objet de ce connecteur dont l'amont sait borner
    une date : `ends_from`/`starts_before`."""
    _tool("attio_meeting")(op="list", limit=200, cursor="cur-2", sort="start_desc",
                            ends_from="2026-08-26T00:00:00Z",
                            starts_before="2026-08-28T00:00:00Z")
    assert client.meetings.list.call_args.kwargs == {
        "limit": 200, "cursor": "cur-2", "sort": "start_desc",
        "ends_from": "2026-08-26T00:00:00Z", "starts_before": "2026-08-28T00:00:00Z",
    }


def test_meeting_n_accepte_plus_offset(client):
    """L'API ignorait `offset` (offset=2000 rendait la même première page) :
    l'accepter était l'accepter-et-l'ignorer. Le refus NOMME l'argument, et la
    docstring pointe `cursor`."""
    with pytest.raises(TypeError, match="offset"):
        _tool("attio_meeting")(op="list", offset=2000)


# --- le défaut reste une lecture ---------------------------------------------

@pytest.mark.parametrize("tool", ["attio_note", "attio_task", "attio_meeting"])
def test_le_defaut_de_chaque_listing_reste_une_lecture(client, tool):
    _tool(tool)()
    for name, _a, _k in client.mock_calls:
        assert name.rsplit(".", 1)[-1] not in ("create", "update", "delete"), name
