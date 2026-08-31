"""Dispatch `op=` des tools `foncier_*` (ADR 0047 §Amendement, appliqué au connecteur
foncier : 14 tools JSON → 8).

Ce que ce fichier verrouille, et que les deux tests foncier existants ne couvraient
PAS : ils exercent UN comportement chacun (le repli code postal du géocodage, le
filtre par demandeur des permis). Aucun ne touchait la SURFACE. Une consolidation
par `op=` déplace précisément le risque là : une op mal câblée appelle silencieusement
la mauvaise méthode du client (ou la bonne avec le mauvais défaut), et rien ne casse
au boot. D'où, pour chaque op : la méthode client appelée, le refus explicite d'une
op inconnue, et les arguments obligatoires par op.

Deux invariants propres à ce connecteur :
- **lecture seule** : open data, sans clé, sans crédit — aucune op n'écrit. Le test
  `test_no_op_reaches_a_non_read_method` énumère TOUTES les ops et fige l'ensemble
  des méthodes amont atteignables ; câbler une écriture demanderait d'y toucher.
- **les gardes anti-scan** (le coût ici est le VOLUME balayé, pas un crédit) :
  `foncier_permis_search` sans scope refuse, `foncier_conso_elec` exige `dept`.
"""
import asyncio
import inspect
from unittest.mock import MagicMock

import pytest
from oto_mcp.mcp_errors import McpError
# Surface amont réellement consommée par le connecteur, par proxy FOD. Aucune de
# ces méthodes n'écrit (elles tapent des API open data en GET/POST de requête) —
# c'est l'allowlist que `test_no_op_reaches_a_non_read_method` oppose au dispatch.
_READ_ONLY_SURFACE = {
    "ban": {"search", "reverse"},
    "cadastre": {"parcelle_at"},
    "bdtopo": {"bati_parcelle"},
    "pvgis": {"productible"},
    "ign": {"isochrone"},
    "sitadel": {"search"},
    "enedis": {"consommation_par_adresse"},
    "dvf": {"stats", "comparables", "comparables_by_address"},
    "dpe": {"by_address", "stats"},
    "georisques": {"installations_classees"},
}


@pytest.fixture
def clients(monkeypatch):
    """Remplace les proxies FOD par des mocks et les rend par nom.

    `register()` lit `fod_foncier.<proxy>` À L'APPEL (bindings locaux) : patcher les
    attributs de module avant l'enregistrement suffit, sans toucher aux corps.
    """
    from oto_mcp.fod import foncier as fod_foncier
    from oto_mcp.fod import urba as fod_urba

    mocks = {name: MagicMock(name=name) for name in _READ_ONLY_SURFACE}
    for name, mock in mocks.items():
        target = fod_urba if name == "georisques" else fod_foncier
        monkeypatch.setattr(target, name, mock)

    # Retours plausibles là où le tool RELIT ce qu'il reçoit (sinon un MagicMock
    # traverse et le test ne prouve rien sur la composition parcelle → bâti).
    mocks["cadastre"].parcelle_at.return_value = {
        "idu": "13201000AB0001", "geometry": {"type": "Polygon"}, "contenance_m2": 420}
    mocks["dvf"].comparables_by_address.return_value = {"mutations": [{"id": 1}]}
    mocks["dpe"].by_address.return_value = {"dpe": [{"etiquette_dpe": "D"}]}
    mocks["georisques"].installations_classees.return_value = {
        "results": 1, "page": 1, "total_pages": 1, "data": []}
    mocks["sitadel"].search.return_value = {"total": 3, "permis": []}
    return mocks


def _tool(name: str):
    from fastmcp import FastMCP
    from oto_mcp.tools import foncier as F

    m = FastMCP("t")
    F.register(m)
    return asyncio.run(m.get_tool(name)).fn


def _tool_names() -> set[str]:
    from fastmcp import FastMCP
    from oto_mcp.tools import foncier as F

    m = FastMCP("t")
    F.register(m)
    return {t.name for t in asyncio.run(m.list_tools())}


# --- inventaire de la surface -------------------------------------------------

def test_the_consolidated_surface_is_exactly_these_tools(clients):
    """Le comptage n'est pas le critère, mais un tool qui APPARAÎT ou DISPARAÎT sans
    qu'on le veuille (renommage manqué, corps oublié) doit casser ici."""
    assert _tool_names() == {
        # 8 tools JSON
        "foncier_geocode", "foncier_site", "foncier_isochrone",
        "foncier_permis_search", "foncier_conso_elec", "foncier_icpe",
        "foncier_dvf", "foncier_dpe",
        # 3 MCP Apps, HORS consolidation (elles rendent un composant d'UI)
        "foncier_site_app", "foncier_comparables_app", "foncier_prix_m2_app",
    }


# --- foncier_site : le point (lat, lon) ---------------------------------------

@pytest.mark.parametrize("op,kwargs,proxy,method", [
    ("parcelle", {}, "cadastre", "parcelle_at"),
    ("adresse", {}, "ban", "reverse"),
    ("solaire", {"kwc": 9.0}, "pvgis", "productible"),
    ("bati", {}, "bdtopo", "bati_parcelle"),
])
def test_site_ops_route_to_the_right_client_method(clients, op, kwargs, proxy, method):
    _tool("foncier_site")(lat=43.29, lon=5.37, op=op, **kwargs)
    getattr(clients[proxy], method).assert_called_once()


def test_site_default_op_is_the_cadastral_parcel(clients):
    """Un appel sans `op` doit rester la lecture documentée par défaut — c'est aussi
    ce qui garantit qu'aucun défaut ne peut dériver vers autre chose."""
    _tool("foncier_site")(lat=43.29, lon=5.37)
    clients["cadastre"].parcelle_at.assert_called_once_with(43.29, 5.37)
    clients["ban"].reverse.assert_not_called()
    clients["pvgis"].productible.assert_not_called()


def test_site_bati_composes_parcel_then_buildings(clients):
    """`op='bati'` résout la parcelle PUIS somme le bâti dedans : la géométrie et la
    contenance de la parcelle doivent bien être passées à BDTOPO (sans quoi le CES
    réel serait calculé sur rien)."""
    _tool("foncier_site")(lat=43.29, lon=5.37, op="bati")
    clients["cadastre"].parcelle_at.assert_called_once_with(43.29, 5.37)
    call = clients["bdtopo"].bati_parcelle.call_args
    assert call.args[0] == {"type": "Polygon"}
    assert call.kwargs["contenance_m2"] == 420


def test_site_bati_without_a_parcel_returns_an_error_key(clients):
    """Contrat conservé : pas de parcelle au point ⟹ clé `error`, pas une exception
    ni un bâti vide qui passerait pour « aucun bâtiment »."""
    clients["cadastre"].parcelle_at.return_value = None
    out = _tool("foncier_site")(lat=0.0, lon=0.0, op="bati")
    assert out == {"error": "no_parcel_at_point", "lat": 0.0, "lon": 0.0}
    clients["bdtopo"].bati_parcelle.assert_not_called()


def test_site_solaire_requires_kwc(clients):
    with pytest.raises(McpError, match="kwc"):
        _tool("foncier_site")(lat=43.29, lon=5.37, op="solaire")
    clients["pvgis"].productible.assert_not_called()


def test_site_solaire_passes_the_peak_power(clients):
    _tool("foncier_site")(lat=43.29, lon=5.37, op="solaire", kwc=36.0)
    assert clients["pvgis"].productible.call_args.args == (43.29, 5.37, 36.0)


# --- foncier_dvf : les mutations DVF+ -----------------------------------------

@pytest.mark.parametrize("op,kwargs,method", [
    ("prix_m2", {"code_commune": "13201"}, "stats"),
    ("comparables", {"code_commune": "13201"}, "comparables"),
    ("comparables_adresse", {"adresse": "44 la canebière marseille"},
     "comparables_by_address"),
])
def test_dvf_ops_route_to_the_right_client_method(clients, op, kwargs, method):
    _tool("foncier_dvf")(op=op, **kwargs)
    getattr(clients["dvf"], method).assert_called_once()


def test_dvf_default_op_is_the_commune_price_stats(clients):
    _tool("foncier_dvf")(code_commune="13201")
    clients["dvf"].stats.assert_called_once()
    clients["dvf"].comparables.assert_not_called()
    clients["dvf"].comparables_by_address.assert_not_called()


@pytest.mark.parametrize("op,kwargs,method,expected", [
    ("prix_m2", {"code_commune": "13201"}, "stats", 3),
    ("comparables", {"code_commune": "13201"}, "comparables", 2),
    ("comparables_adresse", {"adresse": "44 la canebière"},
     "comparables_by_address", 3),
])
def test_dvf_keeps_the_per_op_lookback_default(clients, op, kwargs, method, expected):
    """Les trois lectures fusionnées n'avaient PAS le même défaut de `years`
    (2 ans pour les mutations brutes d'une commune, 3 ailleurs). Unifier le
    paramètre sans unifier le défaut est tout l'enjeu : un `years=3` glissé sur
    `comparables` élargirait silencieusement la fenêtre de toutes les requêtes."""
    _tool("foncier_dvf")(op=op, **kwargs)
    assert getattr(clients["dvf"], method).call_args.kwargs["years"] == expected


def test_dvf_explicit_years_wins_over_the_default(clients):
    _tool("foncier_dvf")(op="comparables", code_commune="13201", years=7)
    assert clients["dvf"].comparables.call_args.kwargs["years"] == 7


def test_dvf_comparables_adresse_forwards_its_own_params(clients):
    _tool("foncier_dvf")(op="comparables_adresse", adresse="44 la canebière",
                         radius_m=250, type_local="Maison", surface_min=40,
                         surface_max=200, limit=10)
    kw = clients["dvf"].comparables_by_address.call_args.kwargs
    assert kw == {"adresse": "44 la canebière", "radius_m": 250,
                  "type_local": "Maison", "surface_min": 40, "surface_max": 200,
                  "years": 3, "limit": 10}


def test_dvf_with_dpe_enriches_the_sales(clients, monkeypatch):
    """`with_dpe=True` va chercher la zone DPE au MÊME rayon et l'apparie aux ventes
    (maison = match 1:1 indicatif, appartement = DPE de l'immeuble)."""
    attached = {}
    monkeypatch.setattr(
        "oto_mcp.dpe_match.attach_dpe_to_sales",
        lambda sales, dpe: attached.update(sales=sales, dpe=dpe))
    _tool("foncier_dvf")(op="comparables_adresse", adresse="44 la canebière",
                         radius_m=300, with_dpe=True)
    assert clients["dpe"].by_address.call_args.kwargs == {"radius_m": 300, "limit": 1000}
    assert attached["sales"] == [{"id": 1}]
    assert attached["dpe"] == [{"etiquette_dpe": "D"}]


def test_dvf_without_with_dpe_does_not_call_ademe(clients):
    _tool("foncier_dvf")(op="comparables_adresse", adresse="44 la canebière")
    clients["dpe"].by_address.assert_not_called()


# --- foncier_dpe : les diagnostics ADEME --------------------------------------

@pytest.mark.parametrize("op,kwargs,method", [
    ("adresse", {"adresse": "44 la canebière"}, "by_address"),
    ("stats", {"code_commune": "13201"}, "stats"),
])
def test_dpe_ops_route_to_the_right_client_method(clients, op, kwargs, method):
    _tool("foncier_dpe")(op=op, **kwargs)
    getattr(clients["dpe"], method).assert_called_once()


def test_dpe_default_op_is_the_address_lookup(clients):
    _tool("foncier_dpe")(adresse="44 la canebière")
    clients["dpe"].by_address.assert_called_once()
    clients["dpe"].stats.assert_not_called()


def test_dpe_adresse_forwards_its_own_filters(clients):
    _tool("foncier_dpe")(adresse="44 la canebière", radius_m=120,
                         type_batiment="appartement", etiquette="F",
                         surface_min=20, surface_max=80, limit=5)
    assert clients["dpe"].by_address.call_args.kwargs == {
        "adresse": "44 la canebière", "radius_m": 120,
        "type_batiment": "appartement", "etiquette": "F",
        "surface_min": 20, "surface_max": 80, "limit": 5}


# --- refus : op inconnue ------------------------------------------------------

@pytest.mark.parametrize("tool,expected_ops", [
    ("foncier_site", ("parcelle", "bati", "solaire", "adresse")),
    ("foncier_dvf", ("prix_m2", "comparables", "comparables_adresse")),
    ("foncier_dpe", ("adresse", "stats")),
])
def test_unknown_op_is_refused_and_names_the_valid_ops(clients, tool, expected_ops):
    """Une op inconnue doit lever EN NOMMANT les ops valides — jamais retomber
    silencieusement sur le défaut (l'agent croirait sa demande honorée)."""
    kwargs = {"lat": 43.29, "lon": 5.37} if tool == "foncier_site" else {}
    with pytest.raises(McpError) as e:
        _tool(tool)(op="nope", **kwargs)
    msg = str(e.value)
    assert "op doit être" in msg
    for op in expected_ops:
        assert f"'{op}'" in msg, op


def test_unknown_op_never_reaches_a_client(clients):
    """Le refus est posé AVANT toute résolution amont : une op inconnue ne peut donc
    pas atteindre une méthode par un chemin dérivé."""
    for tool, kwargs in (("foncier_site", {"lat": 1.0, "lon": 2.0}),
                         ("foncier_dvf", {}), ("foncier_dpe", {})):
        with pytest.raises(McpError):
            _tool(tool)(op="nope", **kwargs)
    for mock in clients.values():
        assert mock.mock_calls == []


# --- refus : argument obligatoire manquant ------------------------------------

@pytest.mark.parametrize("tool,op,kwargs,missing", [
    ("foncier_site", "solaire", {"lat": 1.0, "lon": 2.0}, "kwc"),
    ("foncier_dvf", "prix_m2", {}, "code_commune"),
    ("foncier_dvf", "comparables", {}, "code_commune"),
    ("foncier_dvf", "comparables_adresse", {}, "adresse"),
    ("foncier_dpe", "adresse", {}, "adresse"),
    ("foncier_dpe", "stats", {}, "code_commune"),
])
def test_missing_required_arg_names_the_op_and_the_arg(clients, tool, op, kwargs, missing):
    with pytest.raises(McpError) as e:
        _tool(tool)(op=op, **kwargs)
    assert f"op='{op}'" in str(e.value) and missing in str(e.value)


@pytest.mark.parametrize("tool,op,kwargs", [
    ("foncier_dvf", "prix_m2", {"code_commune": ""}),
    ("foncier_dvf", "comparables_adresse", {"adresse": ""}),
    ("foncier_dpe", "adresse", {"adresse": ""}),
])
def test_an_empty_scope_counts_as_missing(clients, tool, op, kwargs):
    """Une chaîne vide n'est pas un scope : `adresse=""` partirait géocoder le vide
    et rendrait un voisinage arbitraire, qui passerait pour une réponse."""
    with pytest.raises(McpError, match="requiert"):
        _tool(tool)(op=op, **kwargs)
    for mock in clients.values():
        assert mock.mock_calls == []


# --- lecture seule + gardes anti-scan -----------------------------------------

def test_no_op_reaches_a_non_read_method(clients, monkeypatch):
    """Connecteur open data : AUCUNE op n'écrit ni ne consomme de crédit. On énumère
    toutes les ops de la surface et on fige l'ensemble des méthodes amont atteintes —
    câbler une écriture (ou un appel à un client non prévu) casserait ici."""
    monkeypatch.setattr("oto_mcp.dpe_match.attach_dpe_to_sales", lambda *a, **k: None)
    site, dvf_t, dpe_t = _tool("foncier_site"), _tool("foncier_dvf"), _tool("foncier_dpe")
    for op in ("parcelle", "bati", "adresse"):
        site(lat=43.29, lon=5.37, op=op)
    site(lat=43.29, lon=5.37, op="solaire", kwc=9.0)
    dvf_t(op="prix_m2", code_commune="13201")
    dvf_t(op="comparables", code_commune="13201")
    dvf_t(op="comparables_adresse", adresse="44 la canebière", with_dpe=True)
    dpe_t(op="adresse", adresse="44 la canebière")
    dpe_t(op="stats", code_commune="13201")
    _tool("foncier_geocode")("44 la canebière marseille")
    _tool("foncier_isochrone")(lat=43.29, lon=5.37, minutes=10)
    _tool("foncier_permis_search")(code_commune="13201")
    _tool("foncier_conso_elec")(annee="2024", dept="13")
    _tool("foncier_icpe")(code_insee="13201")

    for name, mock in clients.items():
        called = {c[0].split(".")[0].split("(")[0] for c in mock.mock_calls if c[0]}
        assert called <= _READ_ONLY_SURFACE[name], (name, called)
        assert called, f"{name} n'a été exercé par aucune op — allowlist périmée ?"


def test_permis_search_still_refuses_a_national_scan(clients):
    """Le coût de ce connecteur est le VOLUME balayé, pas un crédit : la garde
    anti-scan reste le vrai garde-fou (contrat inchangé, ValueError)."""
    with pytest.raises(ValueError, match="siren"):
        _tool("foncier_permis_search")()
    clients["sitadel"].search.assert_not_called()


def test_conso_elec_still_requires_a_department(clients):
    """Même raison : `dept` reste OBLIGATOIRE dans la signature (donc dans le schéma
    MCP), un scan national ne peut pas être demandé par omission."""
    sig = inspect.signature(_tool("foncier_conso_elec"))
    assert sig.parameters["dept"].default is inspect.Parameter.empty
    assert sig.parameters["annee"].default is inspect.Parameter.empty


# --- les MCP Apps restent hors périmètre --------------------------------------

def test_app_variants_are_untouched(clients):
    """Les trois `*_app` rendent un composant d'UI : elles ne sont NI fusionnées NI
    renommées par la consolidation, et gardent leur signature d'origine."""
    assert inspect.signature(_tool("foncier_site_app")).parameters.keys() == {"adresse"}
    assert "code_commune" in inspect.signature(_tool("foncier_prix_m2_app")).parameters
    assert "radius_m" in inspect.signature(_tool("foncier_comparables_app")).parameters
