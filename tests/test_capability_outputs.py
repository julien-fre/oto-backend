"""Garde-fou : une capacité NEUVE déclare la forme de sa réponse.

L'entrée d'une capacité est dérivable depuis `Input` — le schéma OpenAPI et le
schéma de tool MCP en sortent tout seuls. **La sortie ne l'était par rien** : elle
n'existait que dans les `return` du handler. Mesuré le 2026-08-08 sur le document
servi en prod : 229 opérations, **0 schéma de réponse, 0 `components/schemas`,
0 exemple**. Un intégrateur savait donc APPELER sans savoir ce qu'il recevrait —
c'est la moitié qui manque pour écrire un client, et c'est celle qui coûte le plus
cher à deviner (il faut un compte, un jeton, et une donnée de test réelle).

C'est aussi la contrainte d'ADR 0059 prise au mot : **on ne fige que ce qui est
généré**. Rien n'étant généré côté réponse, rien n'y était figeable — donc aucun
contrat n'était opposable à un tiers.

**Rattrapage progressif, pas de grand soir** : les 204 capacités qui n'avaient rien
sont nommées dans `capability_output_debt.txt` et restent tolérées. Ce fichier ne
peut que RÉTRÉCIR (la déclaration se fait au fil des passages sur chaque domaine) ;
toute capacité **hors liste** doit porter son `Output`, sinon la CI casse.

Même patron que `test_rest_modules_are_capabilities.py` : la dette est **nommée et
comptée**, jamais un plafond tu — un plafond tu est un plafond oublié.
"""
from __future__ import annotations

import pathlib

from oto_mcp.capabilities import registry

_DEBT_FILE = pathlib.Path(__file__).resolve().parent / "capability_output_debt.txt"


def _debt() -> set[str]:
    """Clés tolérées sans `Output` (commentaires `#` et lignes vides ignorés)."""
    lines = _DEBT_FILE.read_text(encoding="utf-8").splitlines()
    return {ln.strip() for ln in lines if ln.strip() and not ln.startswith("#")}


def _without_output() -> set[str]:
    return {c.key for c in registry.CAPABILITIES if c.Output is None}


def test_new_capability_declares_its_output():
    unexpected = sorted(_without_output() - _debt())
    assert not unexpected, (
        f"Capacités sans `Output` hors dette connue : {unexpected}. Déclare le "
        "modèle pydantic de la RÉPONSE (`Output=…`) : c'est lui qui met un schéma "
        "sur la 200 de `/openapi.json`, donc ce qui rend la surface consommable "
        "par un tiers. Une réponse non déclarée n'est pas figeable, donc pas "
        "opposable (ADR 0059). Si la forme varie selon `op`, déclarer l'enveloppe "
        "commune plutôt que rien.")


def test_debt_list_does_not_lie():
    """Une clé disparue (renommée, retirée) doit quitter la liste : une dette qui
    ne correspond plus au réel masque la vraie."""
    gone = sorted(_debt() - {c.key for c in registry.CAPABILITIES})
    assert not gone, (
        f"Ces capacités n'existent plus : {gone}. Retire-les de "
        f"{_DEBT_FILE.name} — la liste doit refléter le réel.")


def test_output_debt_only_shrinks():
    assert len(_debt()) <= 73, (
        f"la dette de sortie a grossi ({len(_debt())} capacités). Elle doit "
        "DÉCROÎTRE : déclare l'`Output` plutôt que d'élargir le plafond.")


def test_declared_output_reaches_the_openapi_document():
    """Le garde-fou ne vaut que si `Output` produit vraiment un schéma servi — sinon
    on collectionne des déclarations décoratives."""
    from oto_mcp import openapi

    doc = openapi.build()
    schema = (doc["paths"]["/api/me/profile"]["get"]["responses"]["200"]
              .get("content", {}).get("application/json", {}).get("schema", {}))
    assert schema.get("properties", {}).keys() >= {"profile", "fields", "missing"}, (
        "la 200 de `GET /api/me/profile` ne porte pas le schéma de `ProfileView` : "
        "le pont `Capability.Output` → OpenAPI est cassé.")
    # Les sous-modèles sont hissés en composants réutilisables, pas inlinés N fois.
    assert "ProfileField" in (doc["components"]["schemas"] or {})
