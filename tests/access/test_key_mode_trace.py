"""`tool_calls.key_mode` — sous quelle clé l'appel est passé.

Le fait est posé au RÉSOLVEUR UNIQUE (ADR 0024), pas dans les tools : c'est ce qui
le rend vrai pour les ~15 connecteurs keyed sans travail par connecteur, et vrai
gratuitement pour celui qu'on ajoutera demain. Ces bancs tiennent ce contrat.

Ce que le mode décide, côté consommateur (la facturation du partenaire) : seul `platform` est
facturé. Un client sur SA propre clé paie déjà le fournisseur — lui compter des
crédits en plus n'aurait pas de sens.
"""
from unittest.mock import patch

from oto_mcp.access import resolve
from oto_mcp.access.resolved_credential import ResolvedCredential


def _rc(mode: str, is_platform: bool) -> ResolvedCredential:
    return ResolvedCredential("fullenrich", "sk-test", is_platform, mode,
                              "org", "42", None)


def test_le_resolveur_verse_le_MODE_au_releve_de_l_appel():
    for mode, is_platform in [("platform", True), ("org", False),
                              ("user", False), ("group", False), ("tenant", False)]:
        with patch("oto_mcp.access.resolve.session_org.note_call_trace") as trace, \
             patch("oto_mcp.access.resolve.instance_refs.ref_for_credential",
                   return_value="ref-1"):
            resolve._note_resolved_instance(_rc(mode, is_platform))
        assert trace.call_args.kwargs["key_mode"] == mode, mode


def test_c_est_le_mode_qui_est_trace_PAS_le_booleen():
    """`is_platform` écrase user/group/org/tenant en un seul « non ». Une facture
    peut avoir à distinguer une clé d'ORG d'une clé de MEMBRE — la colonne garde
    donc l'origine, pas le résumé."""
    modes = set()
    for mode in ("user", "group", "org", "tenant"):
        with patch("oto_mcp.access.resolve.session_org.note_call_trace") as trace, \
             patch("oto_mcp.access.resolve.instance_refs.ref_for_credential",
                   return_value="ref-1"):
            resolve._note_resolved_instance(_rc(mode, False))
        modes.add(trace.call_args.kwargs["key_mode"])
    # quatre origines, quatre valeurs distinctes — pas quatre fois `False`
    assert modes == {"user", "group", "org", "tenant"}


def test_un_releve_qui_echoue_ne_fait_jamais_echouer_la_resolution():
    """Le relevé est best-effort : journaliser ne doit jamais casser un appel."""
    with patch("oto_mcp.access.resolve.instance_refs.ref_for_credential",
               side_effect=RuntimeError("coffre indisponible")):
        rc = resolve._note_resolved_instance(_rc("platform", True))
    assert rc.key == "sk-test"
