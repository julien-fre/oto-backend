"""`annotations.title` on EVERY served tool — a human-readable name for the client.

The Anthropic connectors directory requires a `title` on each tool (review criteria,
"Provide tool annotations"), and clients show it in place of the raw name. A tool that
declares its own title keeps it; the others get one DERIVED from the name served
(after the tenant prefix, so a title never names another brand than the tool).

Only `title` is touched: the hints (`readOnlyHint`, `destructiveHint`, `openWorldHint`)
are what `tools/lecture.py` and recipes read, and a title never sets one — a tool that
declared nothing still declares nothing (`tool_registry._effet` stays None).
"""
from __future__ import annotations

from mcp.types import ToolAnnotations

# Words written in capitals (or a fixed spelling) rather than capitalised.
_SPELLING = {
    "fr": "FR", "uk": "UK", "ie": "IE", "api": "API", "url": "URL", "mcp": "MCP",
    "crm": "CRM", "csv": "CSV", "pdf": "PDF", "sms": "SMS", "kb": "KB", "id": "ID",
    "dpe": "DPE", "dvf": "DVF", "icpe": "ICPE", "qpv": "QPV", "iris": "IRIS",
    "ccn": "CCN", "osm": "OSM", "poi": "POI", "pois": "POIs", "bls": "BLS",
    "oews": "OEWS", "beges": "BEGES", "epfif": "EPFIF", "essms": "ESSMS",
    "jev": "JEV", "gr": "GR", "ai": "AI", "aiark": "AI Ark", "linkedin": "LinkedIn",
    "hubspot": "HubSpot", "elevenlabs": "ElevenLabs", "theirstack": "TheirStack",
    "fullenrich": "FullEnrich", "frenchtech": "French Tech", "boamp": "BOAMP",
    "bodacc": "BODACC", "siren": "SIREN", "siret": "SIRET", "sirene": "SIRENE",
}


def titre_de(name: str) -> str:
    """`apollo_search_people` → "Apollo search people"; `waterfall_email_act` →
    "Waterfall email (actions)"; `fr_get` → "FR get"."""
    mots = [m for m in name.split("_") if m]
    actions = len(mots) > 1 and mots[-1] == "act"
    if actions:
        mots = mots[:-1]
    rendus = [_SPELLING.get(m.lower(), m.lower()) for m in mots]
    if rendus and rendus[0] == rendus[0].lower():
        rendus[0] = rendus[0][:1].upper() + rendus[0][1:]
    titre = " ".join(rendus) or name
    return f"{titre} (actions)" if actions else titre


def avec_titres(tools):
    """The list, each tool without a title given one. Never raises on a single tool:
    a title is cosmetic, a tool list is not."""
    out = []
    for t in tools:
        a = getattr(t, "annotations", None)
        if getattr(a, "title", None):
            out.append(t)
            continue
        base = a.model_dump(exclude_none=True) if a is not None else {}
        nouvelles = ToolAnnotations(**{**base, "title": titre_de(t.name)})
        out.append(t.model_copy(update={"annotations": nouvelles}))
    return out
