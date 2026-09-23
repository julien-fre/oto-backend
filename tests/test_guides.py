"""Guides d'usage (oto-backend#111, tout-DB 2026-07-16) : seeds fichiers + tool oto_guide.

Les fichiers `guides/*.md` ne sont plus la surface de lecture : ils sont les SEEDS
du boot (`seed_platform_guides`, idempotent). Ici on prouve le parsing, la lecture
des seeds livrés, et le seed idempotent (DO NOTHING simulé).
"""
from oto_mcp import guide_store as G


# ── parsing front-matter ──

def test_parse_with_front_matter():
    meta, body = G._parse("---\ntitle: T\ndescription: D\n---\nle corps")
    assert meta == {"title": "T", "description": "D"} and body == "le corps"


def test_parse_without_front_matter():
    meta, body = G._parse("juste du texte")
    assert meta == {} and body == "juste du texte"


# ── seeds sur le vrai dossier guides/ (bulk-load.md, mcp-apps.md livrés) ──

def test_file_seeds_include_shipped_guides():
    by_slug = {g["slug"]: g for g in G.list_file_guides()}
    assert "bulk-load" in by_slug and "mcp-apps" in by_slug
    g = by_slug["bulk-load"]
    assert g["title"] and g["description"]            # front-matter lu
    assert "sous-agent" in g["body_md"]
    assert not g["body_md"].startswith("---")         # front-matter retiré


def test_le_semis_passe_chaque_fichier_avec_son_empreinte(monkeypatch):
    """Tous les fichiers de la racine partent au semis, scope/owner plateforme, et
    chacun porte l'empreinte de CE qu'il pose — sans elle, le démarrage suivant ne
    saurait pas distinguer une base intacte d'une base éditée (oto#236)."""
    vus = {}

    def faux_semis(scope, owner, slug, body_md, title="", description="", *,
                   seed_sha256):
        vus[(scope, owner, slug)] = seed_sha256
        return "seme"

    import oto_mcp.db as db
    monkeypatch.setattr(db, "seed_guide_db", faux_semis)
    rapport = G.seed_platform_guides()
    assert ("platform", G.PLATFORM_OWNER, "bulk-load") in vus
    assert ("platform", G.PLATFORM_OWNER, "mcp-apps") in vus
    fichiers = {g["slug"]: g for g in G.list_file_guides()}
    assert vus[("platform", G.PLATFORM_OWNER, "bulk-load")] == fichiers["bulk-load"]["seed_sha256"]
    assert "bulk-load" in rapport["semes"] and not rapport["echecs"]


def test_index_lists_guides(monkeypatch):
    import oto_mcp.db as db
    monkeypatch.setattr(db, "list_guides_db",
                        lambda scope, owner: [{"slug": "bulk-load", "title": "T",
                                               "description": "D"}]
                        if scope == "platform" else [])
    idx = G.guides_index_md()
    assert "bulk-load" in idx and "oto_guide" in idx


# ── enregistrement du tool (capacité, ADR 0042 §Convergence des surfaces) ──

def test_tool_registers_on_fastmcp():
    from fastmcp import FastMCP

    from oto_mcp.capabilities._mcp_adapter import register
    from oto_mcp.capabilities.registry import CAPABILITIES
    caps = [c for c in CAPABILITIES if c.mcp == "oto_guide"]
    assert len(caps) == 1                    # une seule face MCP, plus de tool main-écrit
    mcp = FastMCP("probe")
    register(mcp, caps)   # ne lève pas ; l'index est ajouté par le middleware, pas ici
