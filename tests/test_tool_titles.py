"""Chaque outil servi porte un `annotations.title` (annuaire des connecteurs Anthropic),
sans qu'un titre ne pose jamais d'indice de lecture ou d'écriture."""
from mcp.types import Tool, ToolAnnotations

from oto_mcp import tool_titles
from oto_mcp.tools.lecture import ACTE, LECTURE


def _outil(name, annotations=None):
    return Tool(name=name, description="d", inputSchema={"type": "object"},
                annotations=annotations)


def test_titre_derive_du_nom():
    assert tool_titles.titre_de("apollo_search_people") == "Apollo search people"
    assert tool_titles.titre_de("fr_get") == "FR get"
    assert tool_titles.titre_de("waterfall_email_act") == "Waterfall email (actions)"
    assert tool_titles.titre_de("linkedin_aiark_search") == "LinkedIn AI Ark search"


def test_titre_pose_sans_toucher_aux_indices():
    nu, lu, agit = tool_titles.avec_titres([
        _outil("oto_doc"), _outil("fr_get", LECTURE), _outil("slack_act", ACTE)])
    assert nu.annotations.title == "Oto doc"
    assert nu.annotations.readOnlyHint is None and nu.annotations.destructiveHint is None
    assert lu.annotations.title == "FR get" and lu.annotations.readOnlyHint is True
    assert agit.annotations.destructiveHint is True and agit.annotations.openWorldHint is True


def test_titre_declare_conserve():
    (t,) = tool_titles.avec_titres([_outil("x_y", ToolAnnotations(title="Mon titre"))])
    assert t.annotations.title == "Mon titre"


def test_payload_type():
    from oto_mcp.capabilities.org_console import OrgSettingsInput
    schema = OrgSettingsInput.model_json_schema()["properties"]["payload"]
    types = {s.get("type") for s in schema["anyOf"]}
    assert types == {"object", "array", "null"}
