"""Lot 0 du retrait des NOMS de tableau (29/09/2026) : le nom résout encore, et le DIT.

Un tableau s'adresse par son numéro (`ns_id`). Jusqu'à `RETRAIT_NOM_DE_TABLEAU`, un nom
résout encore — mais jamais en silence : la réponse MCP porte en tête l'avis qui donne
le numéro et la date, la route REST répond `Deprecation`/`Sunset`, les textes servis
portent la date. Ce banc fige les quatre, sans base.
"""
from __future__ import annotations

import datetime
import importlib.util
import pathlib
from collections import Counter

import pytest

from oto_mcp import deprecations, session_org

RACINE = pathlib.Path(__file__).resolve().parents[1]


# ── La date : une seule, et le plancher du préavis tient ──────────────────────

def test_la_date_est_celle_du_retrait_de_namespace():
    """Décision d'Alexis du 29/09/2026 : un seul geste pour les deux retraits."""
    assert deprecations.RETRAIT_NOM_DE_TABLEAU == deprecations.RETRAIT_DATASTORE
    assert deprecations.RETRAIT_NOM_DE_TABLEAU == datetime.date(2026, 11, 8)
    assert deprecations.date_retrait_nom_de_tableau() == "2026-11-08"


def test_le_plancher_du_preavis_tient_depuis_l_annonce():
    """Le retrait est servi depuis le 07/09/2026 (sans date) : la date choisie ne
    tombe pas avant les deux mois dus depuis ce jour. `PREAVIS_MOIS` n'a pas bougé."""
    assert deprecations.PREAVIS_MOIS >= 2
    plancher = deprecations._plus_de_mois(deprecations.ANNONCE_NOM_DE_TABLEAU,
                                          deprecations.PREAVIS_MOIS)
    assert deprecations.RETRAIT_NOM_DE_TABLEAU >= plancher


@pytest.mark.parametrize("adresse,ns_id,par_nom", [
    ("edition-vivier", 204, True),
    ("204", 204, False),
    (" 204 ", 204, False),
    (204, 204, False),
    ("2024", 204, True),       # des chiffres qui NOMMENT un tableau sont un nom
])
def test_adresse_par_nom_juge_le_resultat_pas_la_forme(adresse, ns_id, par_nom):
    assert deprecations.adresse_par_nom(adresse, ns_id) is par_nom


def test_l_avis_donne_le_numero_et_la_date():
    avis = deprecations.avis_nom_de_tableau("edition-vivier", 204)
    assert "`datastore=204`" in avis
    assert "2026-11-08" in avis
    assert "edition-vivier" in avis


# ── Les textes servis portent la date, plus « being retired » ─────────────────

def _textes_servis() -> dict:
    """Chaque phrase servie qui dit qu'un nom « still resolves ». Les docstrings des
    outils `data_*` sont définies dans une fonction d'enregistrement : on les lit dans
    la source, phrase par phrase."""
    import re

    from oto_mcp.capabilities.registry import CAPABILITIES
    from oto_mcp.datastore import identite

    out = {"identite.DESCRIPTION": identite.DESCRIPTION}
    schema = next(c for c in CAPABILITIES if c.key == "me.datastore.get_schema")
    out["me.datastore.get_schema"] = schema.description
    source = (RACINE / "oto_mcp/tools/datastore.py").read_text()
    for i, m in enumerate(re.finditer(r"still resolves", source)):
        out[f"tools/datastore.py#{i}"] = source[m.start():m.start() + 120]
    assert len(out) >= 7, "les descriptions d'outils ne disent plus où le nom en est"
    return out


def test_chaque_texte_servi_porte_la_date_du_refus():
    """La date vit dans `deprecations` ; les docstrings d'outils, qui ne peuvent pas
    la lire, la recopient — et ce test rougit le jour où l'une ne suit plus."""
    date = deprecations.date_retrait_nom_de_tableau()
    sans = [n for n, t in _textes_servis().items() if date not in t]
    assert not sans, f"textes servis sans la date du refus ({date}) : {sans}"
    encore = [n for n, t in _textes_servis().items() if "being retired" in t]
    assert not encore, f"« being retired » sans date, encore servi par : {encore}"


def test_le_guide_dit_la_date():
    guide = (RACINE / "oto_mcp/guides/datastore-semantics.md").read_text()
    assert deprecations.date_retrait_nom_de_tableau() in guide


# ── MCP : l'avis arrive EN TÊTE de la réponse ────────────────────────────────

def _serveur(monkeypatch):
    from fastmcp import FastMCP

    from oto_mcp import rappel_contexte
    from oto_mcp.middleware.empty_result import EmptyResultMiddleware
    from oto_mcp.middleware.rappel_contexte import (ConstatContexteMiddleware,
                                                    RappelContexteMiddleware)
    monkeypatch.setattr(rappel_contexte, "pour_l_appel", lambda outil: (None, None))
    mcp = FastMCP("tableau-par-numero")

    @mcp.tool
    def par_nom() -> str:
        # Un seam de résolution, dans le thread du handler, comme `_resolve`.
        session_org.noter_avis(deprecations.avis_nom_de_tableau("vivier", 204))
        session_org.noter_avis(deprecations.avis_nom_de_tableau("vivier", 204))
        return "ligne"

    @mcp.tool
    def par_numero() -> str:
        return "ligne"

    mcp.add_middleware(RappelContexteMiddleware())
    mcp.add_middleware(EmptyResultMiddleware())
    mcp.add_middleware(ConstatContexteMiddleware())
    return mcp


def _textes(res):
    return [b.text for b in res.content if getattr(b, "text", None) is not None]


@pytest.mark.asyncio
async def test_l_avis_arrive_en_tete_une_seule_fois(monkeypatch):
    from fastmcp import Client

    async with Client(_serveur(monkeypatch)) as c:
        res = await c.call_tool("par_nom", {})
    assert _textes(res) == [deprecations.avis_nom_de_tableau("vivier", 204), "ligne"]


@pytest.mark.asyncio
async def test_un_appel_par_numero_ne_recoit_rien(monkeypatch):
    from fastmcp import Client

    async with Client(_serveur(monkeypatch)) as c:
        res = await c.call_tool("par_numero", {})
    assert _textes(res) == ["ligne"]


def test_noter_un_avis_hors_appel_mcp_ne_fait_rien():
    """La face REST dit la même chose par ses en-têtes : ici, aucun holder."""
    session_org.noter_avis("rien")


# ── Le store : `_resolve` note l'avis quand, et seulement quand, un NOM a résolu ──

@pytest.fixture()
def store(monkeypatch):
    from oto_mcp import access, group_store, roles
    from oto_mcp.datastore import core as D

    monkeypatch.setattr(access, "current_org", lambda sub: 99)
    monkeypatch.setattr(roles, "is_org_admin", lambda sub, oid: False)
    monkeypatch.setattr(group_store, "list_groups_for_user", lambda sub, org_id=None: [])
    monkeypatch.setattr(D.ownership, "vue_bornee", lambda: None)
    monkeypatch.setattr(D.db, "resolve_datastore_ns",
                        lambda *a, **k: {"id": 204, "datastore": "vivier"})
    return D.make_store("u-1")


@pytest.mark.parametrize("adresse,avis", [("vivier", True), ("204", False)])
def test_resoudre_par_nom_pose_l_avis(store, adresse, avis):
    holder: list = []
    jeton = session_org.set_call_avis(holder)
    try:
        assert store._resolve(adresse) == 204
    finally:
        session_org.reset_call_avis(jeton)
    assert holder == ([deprecations.avis_nom_de_tableau("vivier", 204)] if avis else [])


# ── REST : `Deprecation` / `Sunset` sur une route adressée par un nom ────────

@pytest.mark.parametrize("adresse,date", [("vivier", "08/11/2026"), ("204", None)])
def test_la_route_rest_adressee_par_nom_est_datee(monkeypatch, adresse, date):
    from pydantic import BaseModel
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from starlette.testclient import TestClient

    from oto_mcp.capabilities import _rest_adapter
    from oto_mcp.capabilities._types import Capability, ResolvedCtx, RestBinding

    class Entree(BaseModel):
        datastore: str

    class Sortie(BaseModel):
        ok: bool

    cap = Capability(
        key="t.tableau", handler=lambda ctx, inp: {"ok": True}, Input=Entree,
        Output=Sortie,
        authz=lambda raw, inp: ResolvedCtx(sub=raw.sub, org_id=None),
        rest=RestBinding(verb="GET", path="/api/datastores/{datastore}/t"))

    async def authenticate(request, verifier, **kw):
        return "u-1", None

    async def options(request):
        return JSONResponse({})

    routes = _rest_adapter.make_routes(
        None, authenticate,
        lambda request, data, status=200: JSONResponse(data, status),
        lambda request, status, code, detail=None, **kw: JSONResponse({"error": code},
                                                                      status),
        options, [cap])
    r = TestClient(Starlette(routes=routes)).get(f"/api/datastores/{adresse}/t")
    assert r.status_code == 200, r.text
    assert r.headers.get("Sunset") == date
    assert (r.headers.get("Deprecation") == "true") is (date is not None)


# ── Le script de migration : ne devine jamais ────────────────────────────────

@pytest.fixture()
def script():
    spec = importlib.util.spec_from_file_location(
        "tableaux_par_numero", RACINE / "scripts/tableaux_par_numero.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_le_motif_reecrit_un_nom_resolu_et_laisse_le_reste(script, monkeypatch):
    monkeypatch.setattr(script, "_resoudre",
                        lambda noms, portee: ({"vivier": 204, "homonyme": 9},
                                              {"homonyme"}))
    c = Counter()
    texte = ('data_claim_next(datastore="vivier") puis '
             "data_write(datastore='inconnu') puis data_rows(datastore=\"homonyme\") "
             'et data_rows(datastore="317") et slot : datastore="slot:file". '
             "La prose qui cite vivier reste.")
    neuf = script._reecrire(texte, {"sub": "u"}, c)
    assert "data_claim_next(datastore=204)" in neuf
    assert "datastore='inconnu'" in neuf
    assert 'datastore="homonyme"' in neuf
    assert 'datastore="317"' in neuf and 'datastore="slot:file"' in neuf
    assert "La prose qui cite vivier reste." in neuf
    assert c == Counter({"occurrences réécrites": 1,
                         "occurrences INTROUVABLES (laissées)": 1,
                         "occurrences AMBIGUËS (laissées)": 1})


def test_les_props_d_un_bloc_se_reecrivent_dans_leurs_chaines(script, monkeypatch):
    """Sur le JSON sérialisé les guillemets sont échappés : le motif ne se verrait
    pas. La réécriture descend dans les chaînes, et le JSON reste un JSON."""
    monkeypatch.setattr(script, "_resoudre", lambda noms, portee: ({"vivier": 204}, set()))
    props = {"text": 'appelle data_rows(datastore="vivier")', "n": 3,
             "liste": ['datastore="vivier"']}
    assert script._cite_un_nom(props)
    neuf = script._reecrire_json(props, {"sub": "u"}, Counter())
    assert neuf == {"text": "appelle data_rows(datastore=204)", "n": 3,
                    "liste": ["datastore=204"]}


def test_un_proprietaire_sans_portee_n_est_jamais_reecrit(script):
    """Plateforme, tenant : pas de portée de tableaux où résoudre — listé, pas deviné."""
    assert script._portee_du_proprietaire(None, "platform", "0") is None
    assert script._portee_du_proprietaire(None, "tenant", "t") is None
