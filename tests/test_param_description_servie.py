"""La description d'un champ d'`Input` atteint le SCHÉMA SERVI (#627).

Mesuré les 29-30/08/2026 (#609, #625) : `apply_flat_signature` ne recopiait
qu'annotation et défaut d'un champ d'`Input`. Un `Field(description=…)` posé sur
une capacité était donc **accepté-inerte** — schéma servi 621 caractères avant,
621 après. La règle de `docs/conventions.md` « ce qu'un préambule d'outil AUTORISE
se répète dans la description du PARAMÈTRE concerné » (#517) était inapplicable aux
outils portés par une capacité, alors que les outils écrits à la main la tenaient :
une consigne écrite dans le vide, sur le texte que l'agent relit à chaque appel.

Le contrôle porte sur le **montage réel** (`_mcp_adapter.register` → `tools/list`),
jamais sur `apply_flat_signature` seul : c'est FastMCP qui dérive le schéma de la
signature, et c'est SON schéma qui part sur le fil. Un test posé sur la fonction
d'aplatissement dirait que l'annotation est bien construite sans rien dire de ce
que le modèle reçoit.
"""
import asyncio
from typing import Optional

from fastmcp import FastMCP
from pydantic import BaseModel, Field

from oto_mcp.capabilities import _mcp_adapter, registry
from oto_mcp.capabilities._types import Capability, ResolvedCtx


def _schema(tool) -> Optional[dict]:
    for attr in ("parameters", "input_schema", "inputSchema"):
        s = getattr(tool, attr, None)
        if isinstance(s, dict):
            return s
    return None


_MARQUEUR_SECRET = "ceci-ne-doit-jamais-partir-dans-tools-list"


class _SondeInput(BaseModel):
    decrit: str = Field(description="CE QUE LE PRÉAMBULE AUTORISE, redit ICI.")
    nu: Optional[str] = None
    # `examples` et `json_schema_extra` ne sont PAS recopiés : ils voyagent avec la
    # déclaration REST (où le document est un contrat d'intégration) mais n'ont rien à
    # faire dans `tools/list`, qui part à tout client MCP connecté. Un champ d'`Input`
    # peut porter un `json_schema_extra` de service — le recopier ferait du schéma
    # d'outil un canal de sortie qu'aucune relecture de description ne surveille (#582).
    marque: Optional[str] = Field(
        default=None, description="Décrit, et rien de plus.",
        examples=[_MARQUEUR_SECRET],
        json_schema_extra={"x-oto-interne": _MARQUEUR_SECRET})


def _sonde_cap() -> Capability:
    return Capability(
        key="_sonde.description_servie",
        handler=lambda ctx, inp: {},
        Input=_SondeInput,
        authz=lambda raw, inp: ResolvedCtx(sub=raw.sub),
        description="Sonde de test — jamais montée par le serveur.",
        mcp="_sonde_description_servie",
    )


def _monter_sonde() -> dict:
    m = FastMCP("t")
    _mcp_adapter.register(m, [_sonde_cap()])

    async def go():
        tool = await m.get_tool("_sonde_description_servie")
        s = _schema(tool)
        assert s is not None
        return s

    return asyncio.run(go())


def test_la_description_dun_champ_input_atteint_le_schema_servi():
    props = _monter_sonde()["properties"]
    assert props["decrit"].get("description") == \
        "CE QUE LE PRÉAMBULE AUTORISE, redit ICI."
    # Un champ sans description reste nu — l'adaptateur ne fabrique rien.
    assert "description" not in props["nu"]


def test_le_schema_servi_ne_porte_ni_examples_ni_extra():
    s = _monter_sonde()
    import json
    brut = json.dumps(s, ensure_ascii=False)
    assert _MARQUEUR_SECRET not in brut, \
        "examples/json_schema_extra ont fui dans le schéma servi"
    assert "x-oto-interne" not in brut
    assert s["properties"]["marque"].get("description") == "Décrit, et rien de plus."


def test_tout_champ_decrit_du_registre_est_servi_decrit():
    """Le cliquet : ce qui rend la convention #517 applicable aux capacités.

    Sans lui, une description de paramètre peut redevenir inerte sans qu'un test
    rougisse — le mode de panne d'origine était précisément muet (le champ est
    accepté, le schéma ne bouge pas)."""
    m = FastMCP("t")
    _mcp_adapter.register(m, registry.CAPABILITIES)

    async def go():
        manquants = []
        for cap in registry.caps_with_mcp():
            if not cap.is_exposed():
                continue
            attendu = {k: f.description for k, f in cap.Input.model_fields.items()
                       if f.description}
            if not attendu:
                continue
            props = (_schema(await m.get_tool(cap.mcp)) or {}).get("properties", {})
            for champ, texte in attendu.items():
                if props.get(champ, {}).get("description") != texte:
                    manquants.append(f"{cap.mcp}.{champ}")
        assert not manquants, \
            f"descriptions déclarées mais NON servies : {', '.join(manquants)}"

    asyncio.run(go())
