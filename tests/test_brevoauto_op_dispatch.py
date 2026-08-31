"""Dispatch `op=` des tools `brevoauto_*` (ADR 0047 §Amendement, appliqué au
connecteur brevoauto le 2026-08-11 : 13 tools → 5).

Ce que ce fichier verrouille, et qu'aucun test brevo ne couvrait : `test_brevo_tools.py`
vérifie le REGISTRE (brevoauto est bien le connecteur à session, distinct de `brevo`,
namespace non absorbé) — rien de la SURFACE. Or la consolidation par `op=` déplace
précisément le risque là, et ici il n'est pas cosmétique : **créer, configurer et
surtout SUPPRIMER un trigger ou une étape** modifient un scénario marketing réel, et
`op="status"` peut **activer** une automation qui enverra de vrais emails. Une op mal
câblée partirait silencieusement sur le mauvais verbe HTTP / la mauvaise route, et
rien ne casserait au boot.

Le connecteur n'a pas de « client » : son unique seam vers Brevo est
`browserbase.run_fetch(ctx_id, method, path, body, …)`. C'est donc le triplet
(méthode, route, corps) qui joue ici le rôle de « méthode client appelée » — et ce
qu'on refuse de voir partir, c'est un `DELETE`/`PUT` là où une lecture était demandée.
"""
import asyncio
import inspect
from unittest.mock import AsyncMock, MagicMock

import pytest
from oto_mcp.mcp_errors import McpError
# Verbes HTTP qui MUTENT le compte Brevo : aucun ne doit partir sur une lecture, ni
# sur une op inconnue, ni sur un appel sans `op`.
_MUTATING = ("POST", "PUT", "DELETE")


def _tool(name: str):
    from fastmcp import FastMCP
    from oto_mcp.tools import brevoauto as B

    m = FastMCP("t")
    B.register(m)
    return asyncio.run(m.get_tool(name)).fn


def _call(tool: str, /, **kwargs):
    """Les tools métier sont `async` (fetch dans la session Browserbase) → on les
    déroule. `ctx` est injecté par fastmcp en vrai ; ici il n'est pas lu.

    Le nom du tool est **positional-only** : `name` est un vrai paramètre métier
    (`op='create'`), il ne doit pas entrer en collision avec le helper.
    """
    return asyncio.run(_tool(tool)(ctx=None, **kwargs))


@pytest.fixture
def fetch(monkeypatch):
    """Substrat Browserbase simulé : `run_fetch` est le SEUL point par lequel ce
    connecteur atteint Brevo. Le credential est un MagicMock à part, pour pouvoir
    prouver qu'une op refusée ne le résout même pas (donc ne déchiffre rien)."""
    from oto_mcp.tools import brevoauto as B

    spy = AsyncMock(return_value={"status": 200, "data": {"ok": True}})
    cred = MagicMock(return_value=MagicMock(key="bb-context-1"))
    monkeypatch.setattr(B.browserbase, "is_configured", lambda: True)
    monkeypatch.setattr(B.browserbase, "run_fetch", spy)
    monkeypatch.setattr(B.access, "resolve_credential", cred)
    spy.credential = cred
    return spy


def _sent(spy):
    """(méthode, route, corps) du dernier appel parti vers Brevo."""
    a = spy.call_args.args
    return a[1], a[2], a[3]


def _only(spy, method: str, path: str):
    """UN seul appel est parti, et c'est celui-là. Le « une seule fois » compte
    autant que la route : un dispatch qui enchaînerait deux ops enverrait un write
    de plus que demandé."""
    assert spy.call_count == 1
    m, p, body = _sent(spy)
    assert (m, p) == (method, path)
    return body


# --- routage op → (verbe, route) ----------------------------------------------

@pytest.mark.parametrize("tool,kwargs,method,path", [
    # automation : le scénario lui-même
    ("brevoauto_automation", {"op": "list"}, "GET", "/workflow/listing"),
    ("brevoauto_automation", {"op": "get", "workflow_id": 7}, "GET", "/workflow/7"),
    ("brevoauto_automation", {"op": "catalog"}, "GET",
     "/workflow/getCategoryData?workflow_id=1"),
    ("brevoauto_automation", {"op": "catalog", "workflow_id": 7}, "GET",
     "/workflow/getCategoryData?workflow_id=7"),
    ("brevoauto_automation", {"op": "create", "name": "Onboarding"}, "POST",
     "/workflow/createcustom"),
    ("brevoauto_automation", {"op": "status", "workflow_id": 7, "status": "paused"},
     "PUT", "/workflow/7/status"),
    # trigger : la porte d'entrée
    ("brevoauto_trigger", {"op": "add", "workflow_id": 7,
                           "trigger_name": "contact_match_one_segment",
                           "internal_action_id": 19}, "POST",
     "/workflow/7/trigger?platform=web"),
    ("brevoauto_trigger", {"op": "configure", "workflow_id": 7, "trigger_point_id": 3,
                           "internal_action_id": 19, "event_name": "segment",
                           "config": {"segment_id": 1}}, "PUT",
     "/workflow/update/trigger"),
    ("brevoauto_trigger", {"op": "delete", "workflow_id": 7, "trigger_point_id": 3},
     "DELETE", "/workflow/trigger"),
    # step : l'étape
    ("brevoauto_step", {"op": "add", "workflow_id": 7, "step_type": "wait_until",
                        "internal_action_id": 21}, "POST",
     "/workflow/7/step?platform=web"),
    ("brevoauto_step", {"op": "configure", "workflow_id": 7, "step_id": 5,
                        "step_name": "wait_until", "internal_action_id": 21,
                        "config": {"wait_for": []}}, "PUT", "/workflow/7/step"),
    ("brevoauto_step", {"op": "delete", "workflow_id": 7, "step_id": 5}, "DELETE",
     "/workflow/7/step"),
])
def test_ops_route_to_the_right_call(fetch, tool, kwargs, method, path):
    _call(tool, **kwargs)
    _only(fetch, method, path)


def test_every_declared_op_is_routed(fetch):
    """Aucune op annoncée dans la docstring/les constantes ne doit tomber dans un
    trou du dispatch : la liste déclarée EST la liste couverte ci-dessus."""
    from oto_mcp.tools import brevoauto as B

    assert set(B._AUTOMATION_OPS) == {"list", "get", "catalog", "create", "status"}
    assert set(B._TRIGGER_OPS) == set(B._STEP_OPS) == {"add", "configure", "delete"}


# --- lectures : jamais un verbe mutant ----------------------------------------

@pytest.mark.parametrize("kwargs", [
    {"op": "list"}, {"op": "get", "workflow_id": 7}, {"op": "catalog"},
    {"op": "catalog", "workflow_id": 7},
])
def test_read_ops_never_send_a_mutating_verb(fetch, kwargs):
    """Le cœur du filet : une LECTURE ne doit produire aucun POST/PUT/DELETE. Un
    `op='catalog'` qui partirait en DELETE détruirait un scénario, pas un affichage."""
    _call("brevoauto_automation", **kwargs)
    method, _, _ = _sent(fetch)
    assert method == "GET"
    assert method not in _MUTATING


def test_default_op_is_a_read_and_writes_nothing(fetch):
    """Un appel sans `op` liste — il ne peut ni créer, ni activer, ni supprimer."""
    _call("brevoauto_automation")
    _only(fetch, "GET", "/workflow/listing")


def test_the_declared_defaults_encode_the_write_safety(fetch):
    """Contrat lisible dans le module, pas seulement dans ce test : le défaut de
    `brevoauto_automation.op` est une LECTURE, et les deux tools qui n'ont QUE des
    écritures n'ont AUCUN défaut d'`op` (donc rien d'atteignable par omission)."""
    from oto_mcp.tools import brevoauto as B

    auto = inspect.signature(_tool("brevoauto_automation")).parameters["op"]
    assert auto.default in B._AUTOMATION_READ_OPS
    assert set(B._AUTOMATION_WRITE_OPS) == {"create", "status"}
    for name in ("brevoauto_trigger", "brevoauto_step"):
        assert inspect.signature(_tool(name)).parameters["op"].default \
            is inspect.Parameter.empty


# --- lectures : ce qui part vraiment ------------------------------------------

def test_list_sends_no_body_and_returns_the_decoded_data(fetch):
    fetch.return_value = {"status": 200, "data": {"workflows": [{"id": 7}]}}
    out = _call("brevoauto_automation", op="list")
    assert _sent(fetch)[2] is None
    assert out == {"workflows": [{"id": 7}]}


def test_get_resolves_the_user_session_from_the_vault(fetch):
    """La route est exécutée DANS le Context Browserbase de l'utilisateur : le
    credential résolu doit être celui du connecteur, et il doit arriver au substrat."""
    _call("brevoauto_automation", op="get", workflow_id=7)
    assert fetch.credential.call_args.args[0] == "brevoauto"
    assert fetch.credential.call_args.kwargs.get("want") == "byo"
    assert fetch.call_args.args[0] == "bb-context-1"


# --- écritures : un cas par op, voisines muettes ------------------------------

def test_create_posts_the_scenario_and_deletes_nothing(fetch):
    _call("brevoauto_automation", op="create", name="Onboarding",
          description="bienvenue")
    body = _only(fetch, "POST", "/workflow/createcustom")
    assert body == {"workflow_name": "Onboarding", "workflow_desc": "bienvenue",
                    "multiple_trigger": False, "is_default": True}


def test_create_trims_the_name_and_refuses_a_blank_one(fetch):
    """Un scénario sans nom est ingérable côté Brevo : refus explicite, jamais un
    write qui crée une ligne anonyme."""
    _call("brevoauto_automation", op="create", name="  Onboarding  ")
    assert _sent(fetch)[2]["workflow_name"] == "Onboarding"
    fetch.reset_mock()
    for blank in ("", "   "):
        with pytest.raises(McpError, match="name") as e:
            _call("brevoauto_automation", op="create", name=blank)
        assert "op='create'" in str(e.value)
    fetch.assert_not_called()


@pytest.mark.parametrize("status", ["active", "paused", "draft"])
def test_status_puts_the_requested_state(fetch, status):
    """`op='status'` ACTIVE (ou suspend) un scénario qui enverra de vrais emails :
    l'état demandé doit être celui envoyé, et rien d'autre ne doit partir."""
    _call("brevoauto_automation", op="status", workflow_id=7, status=status)
    assert _only(fetch, "PUT", "/workflow/7/status") == {"status": status}


def test_status_keeps_its_historical_default_and_normalises(fetch):
    """Capacité conservée telle quelle : `op='status'` sans `status` active (défaut
    historique de l'ex-`brevoauto_set_status`), et la casse est normalisée."""
    _call("brevoauto_automation", op="status", workflow_id=7)
    assert _sent(fetch)[2] == {"status": "active"}
    fetch.reset_mock()
    _call("brevoauto_automation", op="status", workflow_id=7, status="  PAUSED ")
    assert _sent(fetch)[2] == {"status": "paused"}


def test_status_refuses_an_unknown_state(fetch):
    with pytest.raises(McpError, match="active"):
        _call("brevoauto_automation", op="status", workflow_id=7, status="on")
    fetch.assert_not_called()


def test_trigger_add_posts_the_catalog_entry(fetch):
    _call("brevoauto_trigger", op="add", workflow_id=7,
          trigger_name="contact_match_one_segment", internal_action_id=19)
    body = _only(fetch, "POST", "/workflow/7/trigger?platform=web")
    assert body == {"trigger_name": "contact_match_one_segment", "multiple_entry": False,
                    "internal_action_id": 19, "source": "contacts"}


def test_trigger_configure_merges_the_config_and_never_deletes(fetch):
    """`config` est FUSIONNÉ au corps (pas imbriqué) — c'est ce que l'API attend.
    Et une configuration ne doit jamais partir sur la route de suppression."""
    _call("brevoauto_trigger", op="configure", workflow_id=7, trigger_point_id=3,
          internal_action_id=19, event_name="segment",
          config={"segment_id": 1, "is_bulk": True})
    body = _only(fetch, "PUT", "/workflow/update/trigger")
    assert body == {"trigger_point_id": 3, "workflow_id": 7,
                    "trigger_point_type": "start_workflow", "internal_action_id": 19,
                    "source": "contacts", "event_name": "segment",
                    "segment_id": 1, "is_bulk": True}
    assert _sent(fetch)[0] != "DELETE"


def test_trigger_delete_targets_both_ids_and_stays_on_the_trigger_route(fetch):
    """Suppression irréversible : les DEUX ids partent, et jamais sur la route des
    étapes (supprimer l'étape 3 au lieu du trigger 3 serait indétectable)."""
    _call("brevoauto_trigger", op="delete", workflow_id=7, trigger_point_id=3)
    assert _only(fetch, "DELETE", "/workflow/trigger") == {"trigger_point_id": 3,
                                                           "workflow_id": 7}
    assert "step" not in _sent(fetch)[1]


def test_step_add_posts_the_wiring(fetch):
    _call("brevoauto_step", op="add", workflow_id=7,
          step_type="if_else_bool_segmentation", internal_action_id=18,
          is_condition=True, prev=4, next=0, condition_node="0")
    body = _only(fetch, "POST", "/workflow/7/step?platform=web")
    assert body == {"next": 0, "prev": 4, "type": "if_else_bool_segmentation",
                    "internal_action_id": 18, "is_condition": True,
                    "source": "contacts", "condition_node": "0"}


def test_step_add_omits_condition_node_when_absent(fetch):
    """Un `condition_node` envoyé à vide brancherait le nœud sous la branche « oui »
    d'une condition inexistante : il ne part QUE s'il est demandé."""
    _call("brevoauto_step", op="add", workflow_id=7, step_type="wait_until",
          internal_action_id=21)
    body = _sent(fetch)[2]
    assert "condition_node" not in body
    assert body["prev"] is None and body["is_condition"] is False


def test_step_configure_puts_the_config_under_the_step_name(fetch):
    """La forme exacte attendue par Brevo : le bloc de réglage est posé sous une clé
    NOMMÉE `step_name` (`wait_until`, `send_email`…), pas sous « config »."""
    _call("brevoauto_step", op="configure", workflow_id=7, step_id=5,
          step_name="wait_until", internal_action_id=21,
          config={"wait_for": [{"unit": "Hours", "delay": "2"}]})
    body = _only(fetch, "PUT", "/workflow/7/step")
    assert body == {"step_id": 5, "step_name": "wait_until", "step_type": "",
                    "wait_until": {"wait_for": [{"unit": "Hours", "delay": "2"}]},
                    "workflowId": 7, "internal_action_id": 21}


def test_step_configure_sends_optional_wiring_only_when_given(fetch):
    """`is_condition`/`source`/`next_steps` ne partent que s'ils sont demandés —
    envoyer `next_steps` par défaut recâblerait les sorties d'un nœud condition."""
    _call("brevoauto_step", op="configure", workflow_id=7, step_id=5,
          step_name="if_else_bool_segmentation", internal_action_id=18,
          config={"branches": []}, is_condition=True, source="messaging",
          next_steps=[6, 7])
    body = _sent(fetch)[2]
    assert body["is_condition"] is True and body["source"] == "messaging"
    assert body["next_steps"] == [6, 7]


def test_step_source_asymmetry_is_preserved(fetch):
    """Asymétrie héritée des deux tools d'origine, conservée à l'identique :
    `op='add'` envoie TOUJOURS `source` (défaut `contacts`), `op='configure'` ne
    l'envoie QUE s'il est fourni."""
    _call("brevoauto_step", op="add", workflow_id=7, step_type="wait_until",
          internal_action_id=21)
    assert _sent(fetch)[2]["source"] == "contacts"
    fetch.reset_mock()
    _call("brevoauto_step", op="configure", workflow_id=7, step_id=5,
          step_name="wait_until", internal_action_id=21, config={})
    assert "source" not in _sent(fetch)[2]


def test_step_delete_targets_the_step_route_only(fetch):
    _call("brevoauto_step", op="delete", workflow_id=7, step_id=5)
    assert _only(fetch, "DELETE", "/workflow/7/step") == {"step_id": 5}


# --- refus : op inconnue -------------------------------------------------------

@pytest.mark.parametrize("tool,kwargs", [
    ("brevoauto_automation", {}),
    ("brevoauto_trigger", {"workflow_id": 7}),
    ("brevoauto_step", {"workflow_id": 7}),
])
def test_unknown_op_is_refused_with_the_allowed_list(fetch, tool, kwargs):
    """Une op inconnue doit lever en NOMMANT les ops valides — jamais retomber
    silencieusement sur un défaut (l'agent croirait sa demande honorée)."""
    with pytest.raises(McpError, match="op doit être"):
        _call(tool, op="nope", **kwargs)


def test_refusal_message_lists_every_valid_op(fetch):
    with pytest.raises(McpError, match=r"'list'.*'get'.*'catalog'.*'create'.*'status'"):
        _call("brevoauto_automation", op="delete")
    with pytest.raises(McpError, match=r"'add'.*'configure'.*'delete'"):
        _call("brevoauto_trigger", op="remove", workflow_id=7)
    with pytest.raises(McpError, match=r"'add'.*'configure'.*'delete'"):
        _call("brevoauto_step", op="update", workflow_id=7)


@pytest.mark.parametrize("tool,kwargs", [
    ("brevoauto_automation", {"op": "remove", "workflow_id": 7}),
    ("brevoauto_trigger", {"op": "drop", "workflow_id": 7, "trigger_point_id": 3}),
    ("brevoauto_step", {"op": "purge", "workflow_id": 7, "step_id": 5}),
])
def test_unknown_op_reaches_neither_brevo_nor_the_vault(fetch, tool, kwargs):
    """La garde est AVANT `_api` : une op inconnue ne résout même pas le credential
    (donc ne déchiffre pas la session de l'utilisateur), et rien ne part vers Brevo."""
    with pytest.raises(McpError, match="op doit être"):
        _call(tool, **kwargs)
    fetch.assert_not_called()
    fetch.credential.assert_not_called()


# --- refus : arguments obligatoires -------------------------------------------

@pytest.mark.parametrize("tool,kwargs,missing", [
    ("brevoauto_automation", {"op": "get"}, "workflow_id"),
    ("brevoauto_automation", {"op": "create"}, "name"),
    ("brevoauto_automation", {"op": "status"}, "workflow_id"),
    ("brevoauto_trigger", {"op": "add", "workflow_id": 7}, "trigger_name"),
    ("brevoauto_trigger", {"op": "add", "workflow_id": 7,
                           "trigger_name": "x"}, "internal_action_id"),
    ("brevoauto_trigger", {"op": "configure", "workflow_id": 7}, "trigger_point_id"),
    ("brevoauto_trigger", {"op": "configure", "workflow_id": 7, "trigger_point_id": 3,
                           "internal_action_id": 19}, "event_name"),
    ("brevoauto_trigger", {"op": "configure", "workflow_id": 7, "trigger_point_id": 3,
                           "internal_action_id": 19,
                           "event_name": "segment"}, "config"),
    ("brevoauto_trigger", {"op": "delete", "workflow_id": 7}, "trigger_point_id"),
    ("brevoauto_step", {"op": "add", "workflow_id": 7}, "step_type"),
    ("brevoauto_step", {"op": "add", "workflow_id": 7,
                        "step_type": "wait_until"}, "internal_action_id"),
    ("brevoauto_step", {"op": "configure", "workflow_id": 7}, "step_name"),
    ("brevoauto_step", {"op": "configure", "workflow_id": 7,
                        "step_name": "wait_until"}, "step_id"),
    ("brevoauto_step", {"op": "configure", "workflow_id": 7, "step_id": 5,
                        "step_name": "wait_until"}, "config"),
    ("brevoauto_step", {"op": "delete", "workflow_id": 7}, "step_id"),
])
def test_missing_required_arg_names_the_op_and_the_arg(fetch, tool, kwargs, missing):
    """Fusionner par `op=` rend tous ces paramètres optionnels DANS LE SCHÉMA : la
    garde serveur remplace ce que le schéma des 13 tools d'origine imposait. Sans
    elle, un `op='configure'` sans `config` écraserait la config d'une étape par les
    seuls défauts, en silence."""
    with pytest.raises(McpError, match=missing) as e:
        _call(tool, **kwargs)
    assert f"op='{kwargs['op']}'" in str(e.value)
    fetch.assert_not_called()


def test_an_empty_config_is_a_legitimate_write(fetch):
    """Nuance assumée : `config={}` est un bloc VIDE (l'API l'accepte), `config=None`
    est un paramètre OUBLIÉ. Seul le second est refusé."""
    _call("brevoauto_step", op="configure", workflow_id=7, step_id=5,
          step_name="wait_until", internal_action_id=21, config={})
    assert _sent(fetch)[2]["wait_until"] == {}


# --- erreurs amont : le message reste actionnable ------------------------------

@pytest.mark.parametrize("status", [401, 403])
def test_expired_session_tells_the_agent_to_reconnect(fetch, status):
    """Le cookie Brevo meurt régulièrement (session navigateur) : l'agent doit lire
    quoi faire, pas un code HTTP."""
    fetch.return_value = {"status": status, "data": "forbidden"}
    with pytest.raises(McpError, match="brevoauto_connect_start"):
        _call("brevoauto_automation", op="list")


def test_upstream_error_surfaces_the_status_and_the_body(fetch):
    fetch.return_value = {"status": 500, "data": {"msg": "boom"}}
    with pytest.raises(McpError, match="500"):
        _call("brevoauto_automation", op="get", workflow_id=7)


def test_missing_credential_points_at_the_connect_flow(monkeypatch):
    """Brevo non connecté : message actionnable (le flux de connexion), jamais une
    erreur de coffre brute."""
    from mcp.types import ErrorData, INVALID_PARAMS

    from oto_mcp.tools import brevoauto as B

    monkeypatch.setattr(B.browserbase, "is_configured", lambda: True)
    monkeypatch.setattr(B.browserbase, "run_fetch", AsyncMock())

    def _boom(*a, **k):
        raise McpError(ErrorData(code=INVALID_PARAMS, message="pas de credential"))
    monkeypatch.setattr(B.access, "resolve_credential", _boom)
    with pytest.raises(McpError, match="brevoauto_connect_start"):
        _call("brevoauto_automation", op="list")


# --- la surface elle-même ------------------------------------------------------

def _tool_names():
    from fastmcp import FastMCP
    from oto_mcp.tools import brevoauto as B

    m = FastMCP("t")
    B.register(m)
    return {t.name for t in asyncio.run(m.list_tools())}


def test_surface_is_the_consolidated_one():
    """13 → 5. Rupture assumée sans alias (ADR 0047) : les anciens noms ne répondent
    plus, ils n'ont pas été gardés en doublon silencieux."""
    assert _tool_names() == {
        "brevoauto_connect_start", "brevoauto_connect_status",
        "brevoauto_automation", "brevoauto_trigger", "brevoauto_step"}


def test_the_connect_pair_stays_alone_with_its_disjoint_parameters():
    """Le flux de connexion garde ses deux tools : `context_id`/`session_id` (rendus
    par le start) ne recouvrent AUCUN paramètre du métier automation — variante
    disjointe, et patron plateforme des `*_connect_start`/`*_connect_status`."""
    from fastmcp import FastMCP
    from oto_mcp.tools import brevoauto as B

    m = FastMCP("t")
    B.register(m)
    status = set(asyncio.run(m.get_tool("brevoauto_connect_status"))
                 .parameters["properties"])
    metier = set()
    for name in ("brevoauto_automation", "brevoauto_trigger", "brevoauto_step"):
        metier |= set(asyncio.run(m.get_tool(name)).parameters["properties"])
    assert status == {"context_id", "session_id"}
    assert status & metier == set()
    assert not (asyncio.run(m.get_tool("brevoauto_connect_start"))
                .parameters.get("properties") or {})


def test_trigger_and_step_stay_two_tools():
    """Deux OBJETS distincts (identifiants et routes distincts) : les fusionner ne
    partagerait que `workflow_id` — sur-fusionner est pire que sous-fusionner."""
    from fastmcp import FastMCP
    from oto_mcp.tools import brevoauto as B

    m = FastMCP("t")
    B.register(m)
    trig = set(asyncio.run(m.get_tool("brevoauto_trigger")).parameters["properties"])
    step = set(asyncio.run(m.get_tool("brevoauto_step")).parameters["properties"])
    assert "trigger_point_id" in trig and "trigger_point_id" not in step
    assert "step_id" in step and "step_id" not in trig
    assert trig & step == {"op", "workflow_id", "internal_action_id", "config",
                           "source"}
