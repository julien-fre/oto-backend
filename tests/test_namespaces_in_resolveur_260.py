"""Les connecteurs cités par une procédure se résolvent par `namespace_of` (oto#260).

`namespaces_in` découpait chaque outil cité au premier `_`. Deux connecteurs
distincts qui partagent leur premier mot — `linkedin_unipile_*` et
`linkedin_aiark_*` — tombaient donc tous deux sous `linkedin`, un connecteur qui
n'existe pas, et le compteur « référencé par N guides » comptait à côté.

Le résolveur correct, `namespace_of`, était déjà importé dans ce module et utilisé
partout ailleurs dans le registre d'appel ; il n'était pas appelé à cet endroit.

Éprouvé rouge le 2026-09-16 : `n.split("_", 1)[0]` rétabli ⟹ le premier test rend
`{"linkedin"}`.
"""
from __future__ import annotations

from oto_mcp import tool_registry


def test_deux_connecteurs_au_meme_premier_mot_restent_DISTINCTS():
    corps = "Sourcer avec <tool:linkedin_unipile_search> puis <tool:linkedin_aiark_search>."
    assert tool_registry.namespaces_in(corps) == {"linkedin_unipile", "linkedin_aiark"}


def test_un_namespace_a_un_seul_mot_ne_change_pas():
    assert tool_registry.namespaces_in("<tool:fr_search> et <tool:data_write>") == {"fr", "data"}


def test_la_derivation_suit_le_RESOLVEUR_et_non_une_copie_de_sa_regle():
    """Le défaut était une règle recopiée à côté du résolveur. On compare donc au
    résolveur lui-même : si sa règle évolue, ce banc suit au lieu de figer l'ancienne."""
    from oto_mcp.tool_visibility import namespace_of
    noms = ["linkedin_unipile_search", "linkedin_aiark_search", "fr_search"]
    corps = " ".join(f"<tool:{n}>" for n in noms)
    assert tool_registry.namespaces_in(corps) == {namespace_of(n) for n in noms}
