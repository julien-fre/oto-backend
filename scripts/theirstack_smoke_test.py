"""Smoke LIVE du connecteur TheirStack — le tool layer réel + le client réel + la vraie clé.

Même astuce que les tests unitaires (register sur un FastMCP nu, appel du `fn` du tool),
mais SANS mock : `resolve_api_key` est remplacé par la clé lue dans l'env, l'appel part
vraiment chez TheirStack. UN seul appel de lecture : `theirstack_companies_search` sur
« PUIG & FILS » (correspondance exacte, `limit=5` pour borner la dépense — au plus 3
crédits API par entreprise rendue). `data: []` est un résultat NORMAL (couverture
partielle des PME), pas un échec.

Lancer :  set -a; . /chemin/vers/.env; set +a   # THEIRSTACK_API_KEY
          OTO_CONFIG_DISABLE_SOPS=1 .venv/bin/python -m scripts.theirstack_smoke_test

La clé n'est JAMAIS imprimée.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from unittest.mock import patch

from fastmcp import FastMCP


def main() -> int:
    key = os.environ.get("THEIRSTACK_API_KEY")
    if not key:
        print("✗ THEIRSTACK_API_KEY absent de l'env (source le .env d'abord)")
        return 2

    from oto_mcp.tools import theirstack

    m = FastMCP("smoke-theirstack")
    theirstack.register(m)
    tool = asyncio.run(m.get_tool("theirstack_companies_search"))

    print("→ theirstack_companies_search(company_names=['PUIG & FILS'], limit=5)")
    with patch("oto_mcp.access.resolve_api_key", return_value=(key, False)):
        out = tool.fn(company_names=["PUIG & FILS"], limit=5)

    meta = out.get("metadata") or {}
    data = out.get("data") or []
    print(f"  ✓ HTTP OK — metadata={json.dumps(meta, ensure_ascii=False)}")
    print(f"  ✓ {len(data)} entreprise(s) rendue(s)"
          + (" — data: [] est normal (couverture partielle)" if not data else ""))
    for c in data:
        print("   -", json.dumps(c, ensure_ascii=False)[:300])
    # Le contrat de projection : chaque item n'a que les 5 clés attendues.
    for c in data:
        assert set(c) <= {"name", "domain", "employee_count", "industry", "technology_names"}, c
    print("  ✓ projection {name, domain, employee_count, industry, technology_names}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
