"""`oto_list_my_tools` rend TOUT le catalogue, avec l'état de chaque outil (oto#170).

Le fait : la description promettait « every tool », et un outil non monté « with
`enabled: false` » ; la réponse partait de la liste DÉJÀ FILTRÉE par la session —
un connecteur non installé, non exposé ou réservé n'y figurait pas du tout, et
`catalog_disabled_count` valait 0 par construction. Les 10 et 11/09/2026, deux agents
ont conclu qu'aucun outil WhatsApp, puis Google Chat, n'existait : les deux existent.
Chaque fois l'agent a fait ce que la réponse lui disait de conclure.

Décision d'Alexis (12/09/2026) : « un vrai verbe de recherche et de list comme
`oto_connector` » — sur le verbe existant : `op=list` = le catalogue entier avec
l'état (`installed` / `installable` / `not_exposed`), `op=search` = par mot.

Mesuré le 12/09/2026 sur 724 outils avant de choisir la projection par défaut :
une entrée par outil avec sa ligne = 115 k caractères, sans la ligne = 53 k, un
groupe par connecteur avec ses outils par nom = 25 k. Le défaut est le groupe ;
`full=True` rend la ligne. Une liste déclare ce que son défaut retire.

⚠️ Ces bancs exécutent le tool RÉELLEMENT MONTÉ, et l'état est DÉRIVÉ des couches
de masquage de la session (`session_visibility.compute_hidden_layers`) — les entrées
de ces couches sont remplacées ici, jamais la règle qui les lit.
"""
from __future__ import annotations

from _mcp_app import static_mcp as _test_mcp

import fastmcp as _fc
import pytest
from mcp.types import INVALID_PARAMS

from oto_mcp import providers, session_visibility as sv
from oto_mcp.mcp_errors import McpError
from oto_mcp.tools import meta as _meta

_SUB = "u-catalogue-etat"
_ORG = 7
# Le connecteur INSTALLÉ dans la boîte de ce compte, celui qui ne l'est pas, et
# celui que l'org n'EXPOSE pas — les trois états, un connecteur chacun.
_INSTALLE, _INSTALLABLE, _NON_EXPOSE = "apollo", "lemlist", "whatsapp"


@pytest.fixture()
def compte(monkeypatch):
    """Un compte dont l'org expose tout SAUF `whatsapp`, et qui n'a installé
    qu'`apollo` : le reste est installable, whatsapp non exposé."""
    monkeypatch.setattr(_meta, "current_user_sub_from_token", lambda: _SUB)
    monkeypatch.setattr(sv.db, "list_user_disabled_tools", lambda s, o=None: [])
    monkeypatch.setattr(sv.db, "list_user_enabled_tools", lambda s, o=None: [])
    monkeypatch.setattr(sv.access, "current_org", lambda s: _ORG)
    monkeypatch.setattr(sv.access, "current_group", lambda s: None)
    monkeypatch.setattr(sv.access, "get_user_role", lambda s: "member")
    monkeypatch.setattr(sv.access, "org_admin_hidden_tools", lambda o: set())
    monkeypatch.setattr(sv.access, "group_admin_hidden_tools", lambda g: set())
    monkeypatch.setattr(sv.access, "has_option", lambda s, opt, org=None: True)
    exposes = {c.name for c in providers._REGISTRY_LIST} - {_NON_EXPOSE}
    monkeypatch.setattr(sv.connector_activation, "exposed_connectors", lambda org: exposes)
    monkeypatch.setattr(sv.connector_selection, "is_seeded", lambda s, o: True)
    monkeypatch.setattr(sv.connector_selection, "list_selection",
                        lambda s, o: {_INSTALLE: sv.connector_selection.ACTIVE})
    from oto_mcp.capabilities.connectors import selection
    monkeypatch.setattr(selection, "_toolbox_scope", lambda sub: None)


async def _appelle(args: dict) -> dict:
    tool = await _test_mcp().get_tool("oto_list_my_tools")
    async with _fc.Context(fastmcp=_test_mcp()):
        return (await tool.run(args)).structured_content


async def _brut() -> int:
    """Ce que le montage sert AVANT tout masquage de session (le banc n'a pas de
    session : rien n'y est masqué)."""
    return len(await _test_mcp().list_tools(run_middleware=False))


# ── le fait : le catalogue est ENTIER, et l'état dit le geste ────────────────

@pytest.mark.asyncio
async def test_un_outil_dont_le_connecteur_n_est_pas_installe_FIGURE_au_catalogue(compte):
    """LE défaut : lemlist n'est pas installé chez ce compte → ses outils étaient
    absents, et « 0 désactivé » disait « il n'y en a pas »."""
    out = await _appelle({"full": True})
    par_nom = {e["name"]: e for e in out["tools"]}
    assert "lemlist_campaign" in par_nom
    assert par_nom["lemlist_campaign"]["state"] == "installable"
    assert par_nom["apollo_search_people"]["state"] == "installed"


@pytest.mark.asyncio
async def test_un_connecteur_non_expose_est_dit_NON_EXPOSE_pas_absent(compte):
    """WhatsApp (10/09) : l'org ne l'expose pas — il n'est pas appelable, mais il
    existe, et la réponse dit qui l'ouvre."""
    out = await _appelle({"query": "whatsapp"})
    assert out["total"] >= 1, "un outil non exposé doit se TROUVER"
    etats = {e["name"]: e["state"] for e in out["tools"]}
    assert etats["whatsapp_chat"] == "not_exposed"
    assert "oto_connector_activation" in out["legend"]["not_exposed"]
    assert "oto_call" in out["legend"]["installable"]


@pytest.mark.asyncio
async def test_le_catalogue_rendu_est_le_registre_entier(compte):
    out = await _appelle({})
    assert out["catalog_total"] == await _brut()
    assert sum(out["catalog_by_state"].values()) == out["catalog_total"]
    assert out["catalog_by_state"]["not_exposed"] >= 1
    assert out["catalog_by_state"]["installable"] > out["catalog_by_state"]["installed"]


# ── op=list : la projection par défaut, et ce qu'elle retire ─────────────────

@pytest.mark.asyncio
async def test_op_list_groupe_par_connecteur_et_dit_l_etat_du_groupe(compte):
    out = await _appelle({})
    assert out["op"] == "list" and "tools" not in out
    groupes = {g["namespace"]: g for g in out["connectors"]}
    assert groupes["apollo"]["state"] == "installed"
    assert groupes["lemlist"]["state"] == "installable"
    assert groupes["whatsapp"]["state"] == "not_exposed"
    assert "apollo_search_people" in groupes["apollo"]["tools"]
    assert groupes["apollo"]["connector"] == "apollo" and groupes["apollo"]["label"]
    assert "full=True" in out["projection"], "le défaut DIT ce qu'il retire"


@pytest.mark.asyncio
async def test_un_outil_dans_un_autre_etat_que_son_groupe_est_nomme(compte, monkeypatch):
    """Un outil que la personne a désactivé est « installable » dans un groupe
    « installé » : l'écart est dit sous `states`, pas fondu dans l'état du groupe."""
    monkeypatch.setattr(sv.db, "list_user_disabled_tools",
                        lambda s, o=None: ["apollo_search_people"])
    out = await _appelle({})
    groupes = {g["namespace"]: g for g in out["connectors"]}
    g = groupes["apollo"]
    assert g["state"] == "installed"
    assert g["states"] == {"apollo_search_people": "installable"}
    assert "apollo_search_people" in g["tools"], "l'écart ne retire pas l'outil de la liste"


@pytest.mark.asyncio
async def test_full_rend_une_ligne_par_outil(compte):
    out = await _appelle({"full": True})
    assert "connectors" not in out
    e = next(x for x in out["tools"] if x["name"] == "apollo_search_people")
    assert set(e) == {"name", "namespace", "state", "description"}
    assert e["description"] and len(e["description"]) <= 101


@pytest.mark.asyncio
async def test_state_filtre_le_catalogue(compte):
    out = await _appelle({"state": "installed"})
    assert out["state"] == "installed"
    assert {g["state"] for g in out["connectors"]} == {"installed"}
    assert out["total"] == out["catalog_by_state"]["installed"]


# ── op=search ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_search_cherche_sur_le_nom_le_connecteur_et_la_description(compte):
    out = await _appelle({"op": "search", "query": "apollo"})
    assert out["tools"][0]["name"].startswith("apollo_")
    assert out["total"] < out["catalog_total"]
    assert all(set(e) == {"name", "namespace", "state", "description"} for e in out["tools"])


@pytest.mark.asyncio
async def test_search_full_rend_la_description_entiere(compte):
    court = await _appelle({"query": "apollo", "limit": 1})
    long = await _appelle({"query": "apollo", "limit": 1, "full": True})
    assert len(long["tools"][0]["description"]) > len(court["tools"][0]["description"])


@pytest.mark.asyncio
async def test_op_est_derive_de_query_quand_il_manque(compte):
    assert (await _appelle({"query": "apollo"}))["op"] == "search"
    assert (await _appelle({}))["op"] == "list"


@pytest.mark.asyncio
@pytest.mark.parametrize("args", [{"op": "search"}, {"op": "list", "query": "x"},
                                  {"state": "enabled"}])
async def test_un_appel_incoherent_est_refuse_pas_ignore(compte, args):
    """`op=list` avec `query` ne filtre pas en silence (le piège d'`oto_connector`
    op=list, selection.py) : il refuse, et dit le verbe."""
    with pytest.raises(McpError) as e:
        await _appelle(args)
    assert e.value.error.code == INVALID_PARAMS


# ── le texte servi ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_la_description_dit_les_trois_etats_et_les_deux_verbes():
    prose = (await _test_mcp().get_tool("oto_list_my_tools")).description or ""
    plat = " ".join(prose.split())
    for mot in ("installed", "installable", "not_exposed", "op=list", "op=search",
                "oto_call", "oto_connector(op='select'"):
        assert mot in plat, mot
    assert "never a missing capability" in plat
    # Claude Code coupe une description d'outil à 2 048 caractères (mesuré 10/09).
    assert len(prose) < 2048, len(prose)
    assert "enabled: false" not in plat, "la promesse fausse est retirée"
