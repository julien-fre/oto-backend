"""Smoke LIVE du connecteur BLS — le tool layer réel + le client réel, SANS clé.

Même astuce que les tests unitaires (register sur un FastMCP nu, appel du `fn` du
tool), mais sans mock : l'appel part vraiment sur api.bls.gov. UN seul appel,
`bls_oews_wages(soc="15-1299.08", areas=["US", "IL", "16980"])` = 21 séries en UNE
requête amont. ⚠️ Elle compte dans le quota public de 25 requêtes par jour de
l'adresse appelante : ne pas lancer en boucle.

Vérité terrain (OEWS 2025, SOC 15-1299) — à réviser quand le BLS publie l'année
suivante, l'API ne servant que la dernière : le script le dit alors au lieu
d'échouer sur les valeurs.

Lancer :  PYTHONPATH=$PWD OTO_CONFIG_DISABLE_SOPS=1 .venv/bin/python -m scripts.bls_smoke_test
"""
from __future__ import annotations

import asyncio
import json
import sys

from fastmcp import FastMCP

_YEAR = "2025"
_EXPECTED = {            # (zone, mesure) → dollars annuels
    ("US", "p90"): 188470, ("US", "p50"): 116580, ("US", "p75"): 157500,
    ("Illinois", "p90"): 167080, ("CBSA 16980", "p90"): 170090,
}


def main() -> int:
    from oto_mcp.tools import bls

    m = FastMCP("smoke-bls")
    bls.register(m)
    tool = asyncio.run(m.get_tool("bls_oews_wages"))

    print("→ bls_oews_wages(soc='15-1299.08', areas=['US', 'IL', '16980'])")
    out = tool.fn(soc="15-1299.08", areas=["US", "IL", "16980"])
    print(json.dumps(out, ensure_ascii=False, indent=1))

    assert out["requests"] == 1, "21 séries doivent tenir en UNE requête"
    assert out["soc"] == "15-1299" and "'.08' stripped" in out.get("note", "")
    if out["year"] != _YEAR:
        print(f"⚠ année servie = {out['year']} (vérité terrain écrite pour {_YEAR}) — "
              "forme validée, valeurs à réviser dans ce script")
        return 0
    by_area = {a["area"]: a for a in out["areas"]}
    failed = 0
    for (area, measure), expected in _EXPECTED.items():
        got = by_area[area]["percentiles"][measure]
        ok = got == expected
        failed += not ok
        print(f"  {'✓' if ok else '✗'} {area} {measure} = {got} (attendu {expected})")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
