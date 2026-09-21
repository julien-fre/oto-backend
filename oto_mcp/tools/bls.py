"""BLS — salaires et emploi par métier aux États-Unis (enquête OEWS, open data).

Wrappe `oto.tools.bls.client.BLSClient` (API publique BLS v2). Connecteur open-data :
pas de credential, pas de cascade. Exposé seulement si activé en DB (cran
d'activation, ADR 0010).

Un seul outil, `bls_oews_wages` : la distribution annuelle des salaires d'un métier
(SOC à 6 chiffres) pour une ou plusieurs zones, en une requête tant que possible.

⚠️ **Le quota est celui de la PLATEFORME, pas de l'appelant** : sans clé
d'enregistrement, l'API sert 25 requêtes par jour à l'adresse qui appelle — donc à
toutes les orgs réunies. L'exploitant lève ce plafond (500/jour, 50 séries par
requête) en posant `BLS_API_KEY` dans l'environnement du serveur : le client
oto-core la lit seul, rien ne change ici. C'est aussi pourquoi `bls` n'est PAS dans
`TESTABLE_NAMESPACES` : un bouton « tester » dépenserait le quota de tout le monde.

L'appel au client est écrit en clair (`_client().oews_wages(…)`) : c'est ce qui le
rend vérifiable par la sonde version-skew.
"""
from __future__ import annotations

from typing import List, Optional

from fastmcp import FastMCP
from mcp.types import ErrorData, INVALID_PARAMS

from ..mcp_errors import McpError

# 7 séries par zone, 25 par requête sans clé : 3 zones par requête. 12 zones = 4
# requêtes au pire, soit un sixième du quota journalier partagé — au-delà, l'appelant
# découpe et le voit.
_MAX_AREAS = 12

_SOURCE = "U.S. Bureau of Labor Statistics — Occupational Employment and Wage Statistics (OEWS)"


def _bad(msg: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=msg))


def _upstream_message(e) -> str:
    status = e.status_code
    if status == 429:
        return "BLS : trop de requêtes (429) — réessaie dans un instant."
    if status in (500, 502, 503, 504):
        return f"BLS est momentanément indisponible (HTTP {status}) — réessaie plus tard."
    return f"BLS a refusé la requête (HTTP {status}) : {e.body}"


def _refusal_message(e) -> str:
    detail = " ; ".join(e.messages) or e.status
    if "threshold" in detail.lower():
        return ("BLS : le quota journalier de l'API publique est épuisé (25 requêtes "
                "par jour sans clé d'enregistrement, partagées par toute la plateforme) "
                f"— réessaie demain. Détail : {detail}")
    return f"BLS a refusé la requête ({e.status}) : {detail}"


def register(mcp: FastMCP) -> None:
    from oto.tools.bls.client import BLSClient, BLSRequestError
    from oto.tools.common.errors import UpstreamHTTPError

    def _client() -> BLSClient:
        return BLSClient()

    @mcp.tool()
    def bls_oews_wages(soc: str, areas: Optional[List[str]] = None) -> dict:
        """US wages for one occupation — annual P10/P25/median/P75/P90, mean and
        employment, per area. Source: BLS OEWS (open data, no key needed).

        LATEST published year only (the API serves no OEWS history); the year is in
        the result. Wages are published at the 6-digit SOC: an 8-digit O*NET-SOC
        code ("15-1299.08") is accepted but its suffix is STRIPPED — the figures
        then cover the whole SOC ("15-1299"), and the result says so in `note`.

        Shared daily quota: one upstream request per 3 areas, 25 requests/day for
        the whole platform — batch areas in ONE call rather than one call per area.

        Returns — `{"source", "soc", "year", "note"?, "requests", "areas": [...],
        "messages"}`. Each area: `area`, `area_type` (N|S|M), `area_code`, `year`,
        `percentiles` {p10,p25,p50,p75,p90} and `mean` (annual USD), `employment`,
        `footnotes`, `raw`, `missing`, `series_ids`. A value BLS withholds or caps
        comes back `null` with its raw form ("-") in `raw` and the reason in
        `footnotes` — never a guessed number. `missing` lists measures with no
        series for that SOC × area (small metros, rare occupations).

        Args:
            soc: SOC code — "15-1299" or "151299" (or an O*NET-SOC, suffix stripped).
            areas: default ["US"]. Up to 12 of: "US", a state name or postal abbreviation
                ("Illinois", "IL"), or a 5-digit CBSA code for a metro area
                ("16980" = Chicago-Naperville-Elgin). Metro NAMES are not resolved.
        """
        if areas is None:
            areas = ["US"]
        if not areas:
            raise _bad("`areas` est vide — passe au moins une zone ('US', un État, "
                       "un code CBSA à 5 chiffres).")
        if len(areas) > _MAX_AREAS:
            raise _bad(f"{len(areas)} zones demandées — {_MAX_AREAS} au plus par appel "
                       "(quota journalier BLS partagé) : découpe la liste.")
        try:
            out = _client().oews_wages(soc, list(areas))
        except ValueError as e:
            raise _bad(str(e))
        except BLSRequestError as e:
            raise _bad(_refusal_message(e))
        except UpstreamHTTPError as e:
            raise _bad(_upstream_message(e))

        suffix = out.pop("onet_suffix_stripped", None)
        result = {"source": _SOURCE, **out}
        if suffix is not None:
            result["note"] = (
                f"O*NET-SOC suffix '.{suffix}' stripped: BLS publishes wages at the "
                f"6-digit SOC, so these figures cover all of {out['soc']}"
                + ("." if suffix == "00" else
                   f", not only the detailed occupation {out['soc']}.{suffix}."))
        return result
