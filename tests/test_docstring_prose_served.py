"""Aucune prose de docstring d'outil ne se perd au parsing fastmcp (#761).

`fastmcp.utilities.docstring_parsing.parse_docstring` ne sert que la PREMIÈRE
section de prose + les descriptions de paramètres : tout paragraphe placé APRÈS le
bloc `Args:`, et toute section `Returns:`/`Examples:`/`Note:`, sont JETÉS en
silence. Mesuré le 2026-09-01 : 61 outils sur 637 perdaient de la prose (~9 600
caractères), dont `oto_call` — dont le paragraphe des jetons `_group`/`_project`/
`_instance`/`_account`/`_run_id` n'a jamais atteint aucun agent (trouvaille de la
session movinmotion-bridge), `fr_search`, `run_start` et `oto_list_my_tools` ; et
15 autres perdaient leur section `Returns:` depuis toujours.

La règle de maison qui en sort : dans un docstring d'outil, TOUTE la prose vit
AVANT le bloc `Args:` (qui se place en dernier), et aucun titre de section
Google (`Returns:`, `Examples:`…) — un « Returns — … » en prose sert la même
information sans être découpé. Ce test s'exerce sur le MONTAGE RÉEL (register_all
+ capacités), pas sur une liste de fichiers : un outil neuf est couvert d'office.
"""
import asyncio
import inspect

from fastmcp import FastMCP
from fastmcp.utilities.docstring_parsing import _PARSERS
from griffe import Docstring, DocstringSectionKind


def _dropped_sections(fn) -> list[str]:
    """Les sections que `parse_docstring` jetterait pour `fn` — même cascade de
    parsers que le code servi : le parser retenu est le premier qui extrait des
    paramètres ; seules la 1re section `text` et les `parameters` survivent."""
    doc = inspect.getdoc(fn)
    if not doc:
        return []
    for parser in _PARSERS:
        sections = Docstring(doc, lineno=1, parser=parser).parse()
        if not any(s.kind == DocstringSectionKind.parameters for s in sections):
            continue
        dropped, seen_text = [], False
        for s in sections:
            if s.kind == DocstringSectionKind.parameters:
                continue
            if s.kind == DocstringSectionKind.text and not seen_text:
                seen_text = True
                continue
            dropped.append(s.kind.value)
        return dropped
    return []      # aucun parser ne voit de paramètres → docstring servi entier


def _mounted_tools():
    from oto_mcp.capabilities import _mcp_adapter, registry
    from oto_mcp.tools import register_all
    mcp = FastMCP("test-docstring-prose")
    register_all(mcp)
    _mcp_adapter.register(mcp, registry.CAPABILITIES)
    return asyncio.run(mcp.list_tools())


def test_aucun_tool_monte_ne_perd_de_prose():
    tools = _mounted_tools()
    assert len(tools) > 500, "montage partiel — le banc ne promet plus le tout"
    pertes = {t.name: d for t in tools
              if getattr(t, "fn", None) and (d := _dropped_sections(t.fn))}
    assert not pertes, (
        f"prose de docstring JETÉE en silence par le parsing fastmcp : {pertes}. "
        f"Déplace le bloc `Args:` en FIN de docstring et remplace tout titre de "
        f"section (`Returns:` multi-lignes, `Examples:`…) par de la prose "
        f"(« Returns — … »).")


def test_le_detecteur_mord():
    """Preuve de morsure : les trois formes que le test prétend attraper."""
    def apres_args():
        """Résumé.

        Args:
            x: un champ.

        Cette phrase serait jetée.
        """
    def section_returns():
        """Résumé.

        Args:
            x: un champ.

        Returns:
            {une: forme}
        """
    def conforme():
        """Résumé, avec un Returns — `{une: forme}` en prose.

        Args:
            x: un champ.
        """
    assert _dropped_sections(apres_args) == ["text"]
    assert _dropped_sections(section_returns) == ["returns"]
    assert _dropped_sections(conforme) == []
