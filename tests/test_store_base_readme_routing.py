"""ADR 0042 §Convergence des surfaces : le readme n'est PLUS servi par le store de
procédures.

Jusqu'au 2026-07-28, `org_store`/`group_store` interceptaient le slug `claude_md` et le
servaient depuis `guides` sous la FORME d'une instruction (compat de migration du barreau
2). Cette fiction — « le readme est une procédure sans version ni slots » — est retirée :
la prose injectée se lit et s'écrit sur la surface guide (`scope`, `delivery='init'`), et
le store de procédures ne connaît que des procédures.

On vérifie ici la FRONTIÈRE (ce que le store refuse) ; le comportement produit (le bundle
de session porte toujours le readme) est vérifié au niveau de la capacité, plus bas.
"""
import pytest

import oto_mcp.group_store as group_store
import oto_mcp.org_store as org_store


# ── la frontière : écrire un readme via la surface procédure est refusé ──

def test_org_set_base_is_refused(monkeypatch):
    """Refus AVANT tout accès DB ou guide_store — pas de redirection silencieuse."""
    import oto_mcp.guide_store as G
    monkeypatch.setattr(G, "set_init_guide",
                        lambda *a, **k: pytest.fail("le store de procédures ne doit plus "
                                                    "écrire de readme"))
    with pytest.raises(ValueError) as e:
        org_store.set_instruction(42, "claude_md", "README")
    assert "readme" in str(e.value) and "guide" in str(e.value)


def test_group_set_base_is_refused(monkeypatch):
    import oto_mcp.guide_store as G
    monkeypatch.setattr(G, "set_init_guide",
                        lambda *a, **k: pytest.fail("idem côté équipe"))
    with pytest.raises(ValueError) as e:
        group_store.set_group_instruction(7, "claude_md", "README")
    assert "readme" in str(e.value) and "guide" in str(e.value)


def test_base_slug_has_no_procedure_history():
    """Inchangé : la prose plate n'a pas d'historique de versions."""
    assert org_store.list_instruction_versions(42, "claude_md") == []
    assert group_store.list_group_instruction_versions(7, "claude_md") == []


def test_stores_no_longer_carry_a_readme_shim():
    """Le helper de compat a disparu des deux stores (la liste doit décroître)."""
    assert not hasattr(org_store, "_base_readme")
    assert not hasattr(group_store, "_base_readme")


# ── le comportement produit : le bundle de session porte TOUJOURS le readme ──

def test_session_bundle_reads_readmes_from_the_guide_surface(monkeypatch):
    import anyio

    import oto_mcp.capabilities.orgs_instructions as OI
    from oto_mcp.capabilities._types import ResolvedCtx

    monkeypatch.setattr(OI.org_store, "get_org", lambda oid: {"name": "Acme"})
    monkeypatch.setattr(OI.org_store, "list_instructions", lambda oid: [])
    monkeypatch.setattr(OI.access, "current_group", lambda sub: 7)
    monkeypatch.setattr(OI.group_store, "get_group", lambda gid: {"name": "Sales"})
    monkeypatch.setattr(OI.group_store, "list_group_instructions", lambda gid: [])
    monkeypatch.setattr(OI.tool_registry, "manifest_for", _async_return([]))
    monkeypatch.setattr(OI, "_project_instance", lambda member_mode: None)
    # La SEULE source des readmes : la surface guide.
    monkeypatch.setattr(OI.guide_store, "init_guide_body",
                        lambda scope, oid: {"org": "README ORG", "group": "README ÉQUIPE"}[scope])

    class _In:
        slug = None
        scope = "org"
        version = None
        doctrine_id = None

    out = anyio.run(OI._get_doctrine, ResolvedCtx(sub="u1", org_id=42), _In())
    assert out["doctrine"] == "README ORG"
    assert out["group_doctrine"] == "README ÉQUIPE"


def _async_return(value):
    async def _fn(*a, **k):
        return value
    return _fn
