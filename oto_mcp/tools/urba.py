"""Urban planning — what qualifies / encumbers a place (France open data, no key).

Counterpart of the `foncier` namespace (which describes the **physical site**: geocoding,
cadastre, buildings, solar, consumption). `urba` covers the **regulatory and
territorial envelope** of a point or a commune:
- enforceable PLU/PLUi zoning (Géoportail de l'Urbanisme),
- recorded natural/technological risks + clay shrink-swell hazard,
- Quartiers Prioritaires de la Ville (tax zoning),
- EPFIF intervention sectors (land control, Île-de-France),
- commune-level socio-demographics (INSEE Mélodi) and at IRIS/neighbourhood level (bundled INSEE parquet).

All clients come from `france-opendata` (open data, no key). Geocode the
address beforehand via `foncier_geocode` (→ lat/lon + INSEE code).

Open-data connector: no credential. Exposed only if enabled in DB
(activation gate, ADR 0010) — register_all gates on `connector_activation`.
"""
from __future__ import annotations

from typing import Literal, Optional

from fastmcp import FastMCP
from mcp.types import INVALID_PARAMS, ErrorData

from .. import output_projection
from ..mcp_errors import McpError


def register(mcp: FastMCP) -> None:
    from ..fod import urba as fod_urba

    # Regulatory envelope served by the dedicated FOD service (ADR 0028 B3) — the
    # backend no longer runs these calls (including the IRIS DuckDB) in-process. Proxy objects
    # with the same surface as the france_opendata clients → only these bindings change.
    gpu = fod_urba.gpu
    georisques = fod_urba.georisques
    qpv = fod_urba.qpv
    insee = fod_urba.insee
    iris = fod_urba.iris
    elus = fod_urba.elus
    annuaire = fod_urba.annuaire
    epfif = fod_urba.epfif

    # --- PLU/PLUi zoning (Géoportail de l'Urbanisme) -------------------------

    @mcp.tool()
    def urba_zonage(lat: float, lon: float) -> dict:
        """Opposable urban-planning zoning at a point (lat, lon), via the GPU.

        Returns the primary PLU/PLUi zone (libellé, type, dominant destination,
        direct règlement PDF URL when available), superimposed zones, prescriptions,
        information layers, public-utility easements, and covering documents. `zone`
        is null if no digitized document covers the point (commune under RNU, or PLU
        not published on the GPU — see `avertissements`). Geocode the address first.
        """
        return gpu.zonage(lon, lat)

    @mcp.tool()
    def urba_reglement(idurba: str, zone: Optional[str] = None, query: Optional[str] = None,
                       max_extraits: int = 8, context_lignes: int = 30) -> dict:
        """Targeted excerpts of a PLU/PLUi written règlement, via the shared FOD service.

        Intercommunal règlements are huge (often >50 MB, >1000 pages, many zones):
        the FOD service parses and caches each document **once** (keyed by `idurba`)
        and this tool serves the relevant **excerpts** for a zone / keyword — never the
        whole text. READ the excerpts to lift the rules (max height, ground coverage,
        setback, parking) — never invent a figure that is absent.

        `cached=false` means the règlement is not yet ingested in the service (batch
        ingestion, not on-the-fly): report that rather than guessing.

        Args:
            idurba: document version id — the `zone.idurba` field from `urba_zonage`.
            zone: zone label to search for (e.g. "UCt2", "UM").
            query: alternative keyword (e.g. "hauteur maximale", "emprise au sol").
            max_extraits: max passages returned (1-50). context_lignes: lines kept after each hit.
        """
        from oto_mcp.fod import reglement as fod_reglement

        return fod_reglement.extraits(idurba, zone=zone, query=query,
                                      max_extraits=max_extraits, context_lignes=context_lignes)

    # --- risks (Géorisques) ------------------------------------------------

    @mcp.tool()
    def urba_risques(code_insee: str) -> dict:
        """Natural & technological risks recorded for a commune (Géorisques GASPAR).

        Returns distinct long labels: flooding, ground movement, clay shrink-swell,
        seismicity, hazardous-materials transport, ICPE/Seveso… Empty list if none
        recorded. Takes the INSEE commune code (5 chars).
        """
        return georisques.risques_commune(code_insee)

    @mcp.tool()
    def urba_argiles(lat: float, lon: float) -> dict:
        """Clay shrink-swell hazard (RGA) at a point (lat, lon), via Géorisques.

        Returns exposure level (faible / moyen / fort). High clay exposure is a
        foundation-cost driver. Geocode the address first.
        """
        return georisques.alea_argiles(lon, lat)

    # --- Quartiers Prioritaires de la Ville (QPV) ----------------------------

    @mcp.tool()
    def urba_annuaire(
        op: Literal["services", "maires", "presidents_epci"] = "services",
        code_commune: Optional[str] = None,
        siren: Optional[str] = None,
        departement: Optional[str] = None,
        type_service: Optional[str] = None,
        limit: int = 20,
        fields: Optional[list[str]] = None,
    ) -> dict:
        """Who decides and who answers on a PUBLIC target: services, mayors, EPCI presidents.

        On a public-sector prospect the decision-maker is an elected official and the
        contact a public service — neither is a company director, and paid enrichment
        finds nothing.

        `op="services"` (default) — the DILA administration directory (~36,000 town
        halls plus prefectures, tax offices, departmental directorates) by
        `code_commune` or `siren`, optionally narrowed by `type_service` (the
        directory's « pivot »: `mairie`, `prefecture`, `dd_fip`…). Returns switchboard,
        generic e-mail, website and `responsables` — the NAMED head of the service with
        their role. A value the source serialised badly is listed in
        `champs_illisibles`, never silently emptied.

        `op="maires"` — the mayor, by `code_commune` or `departement`.
        `op="presidents_epci"` — the intercommunality president, by `siren` (the EPCI's)
        or `departement`; only the PRESIDENT is kept, not the thousands of community
        councillors. Both return the start date of the office — useful to tell whether
        the contact changed since the last campaign. Birth date and sex are in the
        source file and deliberately NOT returned.

        Match on the INSEE code, never on the commune NAME: "Sainte-Marie" exists
        dozens of times. A parameter the chosen op cannot honour is REFUSED.
        `fields` keeps only these keys in each record; the envelope always stays.

        Args:
            op: "services" (default), "maires" or "presidents_epci".
            code_commune: INSEE commune code (services, maires).
            siren: organisation SIREN (services) or EPCI SIREN (presidents_epci).
            departement: department code (maires, presidents_epci).
            type_service: directory « pivot » type (services only).
            limit: records returned.
            fields: keys kept in each record.
        """
        acceptes = {
            "services": {"code_commune", "siren", "type_service"},
            "maires": {"code_commune", "departement"},
            "presidents_epci": {"siren", "departement"},
        }[op]
        poses = {n for n, v in (("code_commune", code_commune), ("siren", siren),
                                ("departement", departement), ("type_service", type_service)) if v}
        if poses - acceptes:
            raise McpError(ErrorData(code=INVALID_PARAMS, message=(
                f"op='{op}' n'accepte pas {', '.join(sorted(poses - acceptes))} "
                f"(accepte : {', '.join(sorted(acceptes))})")))
        if not poses & (acceptes - {"type_service"}):
            raise McpError(ErrorData(code=INVALID_PARAMS, message=(
                f"op='{op}' requiert au moins : {', '.join(sorted(acceptes - {'type_service'}))}")))
        if op == "maires":
            res = elus.maires(code_commune=code_commune, departement=departement, limit=limit)
        elif op == "presidents_epci":
            res = elus.presidents_epci(siren=siren, departement=departement, limit=limit)
        else:
            res = annuaire.services(siren=siren, code_commune=code_commune,
                                    type_service=type_service, limit=limit)
        return output_projection.project(res, items_path="signaux", fields=fields)

    @mcp.tool()
    def urba_qpv(code_insee: str) -> dict:
        """Priority urban districts (QPV) of a commune (national dataset).

        Returns the QPV list and count. Presence of a QPV is the geographic
        condition for several incentives (e.g. reduced-VAT home ownership), not a
        full eligibility check. Takes the INSEE commune code.
        """
        return qpv.by_commune(code_insee)

    @mcp.tool()
    def urba_qpv_proximite(lat: float, lon: float, rayon_m: int = 300) -> dict:
        """QPV within `rayon_m` metres of a point (lat, lon) — server-side geo filter.

        `eligible_geo`=True if at least one QPV falls within the radius (300 m is the
        default regulatory perimeter). Geocode the address first.
        """
        return qpv.near_point(lon, lat, radius_m=rayon_m)

    # --- EPFIF (land control, Île-de-France) ---------------------------------

    @mcp.tool()
    def urba_epfif(code_insee: str) -> dict:
        """EPFIF land-control intervention status of a commune (Île-de-France only).

        Returns whether the commune is under an EPFIF sector (veille / maîtrise /
        ORCOD-IN) — a land-pressure signal the GPU zoning does not carry (pre-emption
        often delegated to the EPFIF). `secteur_epfif` is False outside any known
        sector, null if the source is unavailable. Data scraped live from the EPFIF
        cartography page and cached. Takes the INSEE commune code.
        """
        return epfif.lookup(code_insee)

    # --- commune socio-demographics (INSEE Mélodi) ---------------------------

    @mcp.tool()
    def urba_socio(code_insee: str) -> dict:
        """Commune socio-demographic profile (INSEE Mélodi, open data).

        Aggregates, best-effort (a failing block is reported per section, not fatal):
        population (last 3 census millésimes → trend), households by family type,
        one-person households, income (median standard of living, poverty rate) and
        housing (main/vacant/secondary dwellings, tenure split). Takes the INSEE
        commune code — for Paris/Lyon/Marseille an ARRONDISSEMENT code (e.g. 13201 =
        Marseille 1er) works too. For a finer, within-commune breakdown use urba_iris.
        """
        out: dict = {"code_insee": code_insee}
        blocks = {
            "population": lambda: insee.population(code_insee),
            "familles": lambda: insee.familles(code_insee),
            "personnes_seules": lambda: insee.personnes_seules(code_insee),
            "revenus": lambda: insee.revenus(code_insee),
            "logement": lambda: insee.logement(code_insee),
        }
        for key, fn in blocks.items():
            try:
                out[key] = fn()
            # noqa: SILENT — the per-layer failure is rendered in the result row
            except Exception as e:  # noqa: BLE001 — degrade per block
                out[key] = {"error": f"{type(e).__name__}: {e}"}
        return out

    # --- census at IRIS / neighbourhood level (INSEE, bundled parquet) --------

    @mcp.tool()
    def urba_iris(code: str) -> dict:
        """INSEE census at the IRIS ('quartier') level — finer than a commune.

        The IRIS (~2 000 inhabitants) is INSEE's neighbourhood mesh: communes of
        ≥10 000 inhabitants (and most of 5 000-10 000) are split into IRIS. `urba_socio`
        stops at the commune; this drills inside it.

        `code` accepts either:
        - a 5-digit COMMUNE / arrondissement code → returns ALL IRIS of that commune
          plus commune totals (e.g. '13201' = Marseille 1er, '75112' = Paris 12e) ;
        - a 9-digit IRIS code → returns that single IRIS.

        Per IRIS (RP 2021 counts): population (+ age bands 0-19/20-64/65+), dwellings,
        main/secondary/vacant residences, houses vs flats, and households living in a
        flat (rp_en_appartement — a proxy for laundromat/shared-service demand).
        `typ_iris`: H habitat / A activité / D divers / Z whole undivided commune.
        Neighbourhood NAMES aren't in this file (lab_iris is INSEE's numeric label).
        For population TREND per quartier, note this millésime is single-year; use
        urba_socio at the arrondissement level for evolution.
        """
        code = str(code).strip()
        if len(code) >= 9:
            row = iris.by_iris(code)
            return row or {"code": code, "found": False,
                           "note": "code IRIS (9 chiffres) inconnu"}
        return iris.by_commune(code)
