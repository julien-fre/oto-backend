"""Smoke LIVE du connecteur Origami — le tool layer réel + le client réel + la vraie clé.

Même astuce que les tests unitaires (register sur un FastMCP nu, appel du `fn` du tool),
mais SANS mock : `resolve_api_key` est remplacé par la clé lue dans l'env, l'appel part
vraiment chez Origami. UN seul appel, en LECTURE : `origami_tables(op="list")`. Aucun
appel mutant ici — jamais (le connecteur écrit et ENVOIE ; ce smoke ne doit pas).

Lancer :  set -a; . /chemin/vers/.env; set +a   # ORIGAMI_API_KEY (+ ORIGAMI_TABLE_ID_PILOT facultatif)
          OTO_CONFIG_DISABLE_SOPS=1 .venv/bin/python -m scripts.origami_smoke_test

La clé n'est JAMAIS imprimée.
"""
from __future__ import annotations

import asyncio
import os
import sys
from unittest.mock import patch

from fastmcp import FastMCP


def main() -> int:
    key = os.environ.get("ORIGAMI_API_KEY")
    if not key:
        print("✗ ORIGAMI_API_KEY absent de l'env (source le .env d'abord)")
        return 2

    from oto_mcp.tools import origami

    m = FastMCP("smoke-origami")
    origami.register(m)
    tool = asyncio.run(m.get_tool("origami_tables"))

    print("→ origami_tables(op='list')  [lecture seule]")
    with patch("oto_mcp.access.resolve_api_key", return_value=(key, False)):
        out = tool.fn(op="list")

    items = out.get("items") if isinstance(out, dict) else None
    assert isinstance(items, list), f"enveloppe liste attendue, reçu : {type(out).__name__}"
    print(f"  ✓ HTTP OK — {len(items)} table(s) sur la première page, "
          f"nextCursor={'oui' if out.get('nextCursor') else 'non'}")
    for t in items[:10]:
        print(f"   - {t.get('id')}  «{t.get('name')}»  leads={t.get('leadCount')}  "
              f"workspace={t.get('workspaceId')}")

    pilot = os.environ.get("ORIGAMI_TABLE_ID_PILOT")
    if pilot:
        seen = any(t.get("id") == pilot for t in items)
        print(f"  {'✓' if seen else '·'} table pilote {pilot} "
              f"{'visible' if seen else 'PAS sur cette page (autre page, ou scope projet)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
