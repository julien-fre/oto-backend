"""Un seul canal porte la donnée : le canal structuré se mérite, il ne se déduit pas.

Deux gestes à prouver, et une INTERACTION — c'est pourquoi le banc fait traverser la
chaîne RÉELLE montée sur `_test_mcp()` plutôt que d'appeler le middleware seul :

- au montage, le schéma de sortie DÉDUIT par FastMCP d'un `-> dict` (« un objet, tout
  est permis ») est effacé ; une enveloppe `x-fastmcp-wrap-result` et un vrai schéma
  (un modèle pydantic rendu) sont gardés ;
- à l'appel, `structuredContent` est retiré des outils sans schéma — APRÈS que le rendu
  du vide et la rédaction ont réémis le résultat sur les deux canaux. Plus interne,
  l'un d'eux rétablirait le canal qu'on vient de retirer : c'est l'ordre qu'on prouve.

Ce qui a rendu ce lot nécessaire est mesuré, pas supposé : Claude Code et oto-runner
donnent au modèle le canal structuré À LA PLACE du texte. Une fois le canal retiré,
Claude Code recopie le marqueur du TEXTE (sonde du 10/09/2026, deux runs sur deux).
"""
from __future__ import annotations

import asyncio
import json
import pathlib

from _mcp_app import static_mcp as _test_mcp

import pytest
from fastmcp import Client, FastMCP
from oto.tools.common import FieldFilter
from pydantic import BaseModel

from oto_mcp import redaction
from oto_mcp.middleware import un_seul_canal

_DEBT_FILE = pathlib.Path(__file__).resolve().parent.parent / "structured_output_debt.txt"

# Plafond de la dette : il ne peut que BAISSER. Mesuré le 2026-09-10 sur le montage réel.
_PLAFOND = 120


def _banc(fn, *, nom: str = "recherche", montage: bool = True, app: bool = False):
    """Un serveur d'un seul outil, sous la chaîne de middlewares du VRAI serveur — et,
    comme `_build_mcp`, le schéma déduit retiré au montage (`montage=False` pour
    reproduire un serveur qui aurait le middleware SANS le geste de montage)."""
    m = FastMCP("banc")
    for mw in _test_mcp().middleware:
        m.add_middleware(mw)
    m.tool(name=nom, **({"app": True} if app else {}))(fn)
    if montage:
        un_seul_canal.retirer_les_schemas_vides(m)
    return m


def _servir(m: FastMCP, nom: str = "recherche"):
    async def appel():
        async with Client(m) as c:
            return await c.call_tool(nom, {}, raise_on_error=False)
    r = asyncio.run(appel())
    return "".join(getattr(b, "text", "") for b in r.content), r.structured_content, r.is_error


def _debt() -> set[str]:
    lines = _DEBT_FILE.read_text(encoding="utf-8").splitlines()
    return {ln.strip() for ln in lines if ln.strip() and not ln.startswith("#")}


# ── Le schéma : effacé quand il ne dit rien, gardé quand il dit quelque chose ───

def test_le_schema_deduit_d_un_dict_est_efface_et_ne_revient_pas_par_tools_list():
    m = FastMCP("x")

    @m.tool
    def a() -> dict:
        return {"k": 1}

    gardes = un_seul_canal.retirer_les_schemas_vides(m)
    assert gardes == frozenset()

    async def liste():
        async with Client(m) as c:
            return {t.name: t.outputSchema for t in await c.list_tools()}

    assert asyncio.run(liste()) == {"a": None}


class Fiche(BaseModel):
    """Un VRAI schéma de sortie : des champs décrits. Au niveau du module, parce que
    `from __future__ import annotations` rend l'annotation de retour paresseuse et
    FastMCP la résout dans les globales."""
    id: int
    nom: str


def test_une_enveloppe_et_un_vrai_schema_sont_gardes():
    m = FastMCP("x")

    @m.tool
    def liste_nue() -> list:
        return [1]

    @m.tool
    def fiche() -> Fiche:
        return Fiche(id=1, nom="x")

    assert un_seul_canal.retirer_les_schemas_vides(m) == frozenset({"liste_nue", "fiche"})


def test_ce_qui_est_vide_au_sens_de_la_regle():
    assert un_seul_canal.schema_est_vide(None)
    assert un_seul_canal.schema_est_vide({"type": "object", "additionalProperties": True})
    assert not un_seul_canal.schema_est_vide(
        {"type": "object", "properties": {"result": {"type": "array"}},
         "x-fastmcp-wrap-result": True})
    assert not un_seul_canal.schema_est_vide(
        {"type": "object", "properties": {"id": {"type": "integer"}}})


# ── Le canal, à travers la chaîne RÉELLE ────────────────────────────────────────

def test_un_dict_perd_son_canal_structure_et_garde_son_texte():
    charge = {"rows": [{"id": 1, "nom": "a"}], "count": 1}

    def recherche() -> dict:
        return charge

    texte, structure, err = _servir(_banc(recherche))
    assert not err
    assert json.loads(texte) == charge
    assert structure is None


def test_une_liste_nue_garde_son_enveloppe():
    """Les deux canaux n'ont pas la même forme : le texte porte la liste nue, le
    structuré `{"result": [...]}`. Un client qui parse `.result` ne retrouverait pas
    la donnée dans le texte sans la désemballer — l'enveloppe reste, et elle est
    comptée dans la dette."""
    def recherche() -> list[int]:
        return [1]

    texte, structure, _ = _servir(_banc(recherche))
    assert texte == "[1]"
    assert structure == {"result": [1]}


def test_le_vide_reste_une_phrase_et_part_sans_canal():
    """Le rendu du vide (plus interne) écrit la phrase et garde la structure vide sur
    le canal structuré ; ce middleware, plus externe, retire ensuite ce canal. L'ordre
    inverse rendrait au client la structure vide qu'on vient de retirer du texte."""
    texte, structure, _ = _servir(_banc(lambda: {"total_count": 0, "rows": []}))
    assert texte == redaction.EMPTY_MESSAGE_DEFAULT
    assert structure is None


def test_la_redaction_s_applique_puis_le_canal_part(monkeypatch):
    monkeypatch.setattr(redaction, "_resolve_field_filter",
                        lambda _s: FieldFilter(rules=[{"fields": ["secret"], "action": "drop"}]))

    def recherche() -> dict:
        return {"rows": [{"id": 1, "secret": "NE-DOIT-PAS-SORTIR"}], "count": 1}

    texte, structure, _ = _servir(_banc(recherche))
    assert "NE-DOIT-PAS-SORTIR" not in texte
    assert json.loads(texte)["rows"] == [{"id": 1}]
    assert structure is None


def test_un_schema_encore_declare_garde_son_canal():
    """Le middleware sans le geste de montage : le schéma vide est encore ANNONCÉ, donc
    un client qui valide exige le canal — le client FastMCP refuse sinon le résultat
    (« outputSchema defined but no structured output returned »). Le middleware ne
    juge que l'absence de schéma, et le résultat passe intact."""
    charge = {"rows": [{"id": 1}], "count": 1}

    def recherche() -> dict:  # annoté : c'est l'annotation qui fait DÉDUIRE le schéma
        return charge

    texte, structure, err = _servir(_banc(recherche, montage=False))
    assert not err
    assert structure == charge


def test_une_app_garde_son_canal_structure():
    """Une MCP App (`_meta.ui.resourceUri`) n'a pas de schéma de sortie, et son canal
    structuré n'est pas une copie du texte : c'est l'UNIQUE entrée de sa carte. Le
    retirer laissait le renderer Prefab sur « Waiting for content… » — toutes les
    apps, du 10/09 au 18/09/2026 (signal #1083, `oto_doc_app`)."""
    pytest.importorskip("prefab_ui")
    from prefab_ui.components import Card, Text

    def recherche():
        with Card() as carte:
            Text("SENTINELLE-CARTE")
        return carte

    m = _banc(recherche, app=True)
    assert un_seul_canal.est_une_app(asyncio.run(m.get_tool("recherche")))
    _, structure, err = _servir(m)
    assert not err
    assert structure is not None and "$prefab" in structure
    assert "SENTINELLE-CARTE" in json.dumps(structure)


def test_une_erreur_n_est_pas_touchee():
    def recherche() -> dict:
        raise ValueError("boum")

    _, _, err = _servir(_banc(recherche))
    assert err


# ── Le montage réel : aucun schéma vide servi, et une dette qui ne grossit pas ──

def _servis():
    return asyncio.run(_test_mcp().list_tools(run_middleware=False))


def test_aucun_outil_monte_ne_sert_le_schema_vide():
    tools = _servis()
    assert len(tools) > 300, f"registre d'outils suspect ({len(tools)}) — banc invalide"
    vides = sorted(t.name for t in tools
                   if t.output_schema == un_seul_canal._OBJET_VIDE)
    assert not vides, (
        f"Schéma de sortie DÉDUIT encore servi par : {vides[:10]}{'…' if len(vides) > 10 else ''}. "
        "Un outil monté après `retirer_les_schemas_vides` garde le sien : le montage doit "
        "précéder l'appel dans `server._build_mcp`.")


def test_un_outil_neuf_ne_gagne_pas_de_canal_structure_sans_vrai_schema():
    """Une liste nue rendue par un outil NEUF lui donnerait une enveloppe — et un
    canal structuré que Claude Code lirait à la place du texte. Deux issues : rendre
    un dict aux clés nommées (leçon `pennylaneged`), ou déclarer un vrai `Output`."""
    porteurs = {t.name for t in _servis() if t.output_schema is not None}
    intrus = sorted(porteurs - _debt())
    assert not intrus, (
        f"Outils avec un schéma de sortie hors dette connue : {intrus}. Si c'est une "
        "enveloppe `x-fastmcp-wrap-result`, rends un dict aux clés nommées plutôt "
        "qu'une liste nue ; si c'est un vrai schéma (des champs décrits), c'est ce "
        "qu'on veut — nomme-le dans `structured_output_debt.txt` avec sa raison, il "
        "n'est pas une dette.")


def test_la_dette_ne_ment_pas():
    """Une ligne payée doit quitter la liste — sinon la marge libérée se remplirait en
    silence. Seuls les outils SERVIS ICI sont jugés : un poste en retard sur le pin
    oto-core en monte moins que la CI, et une ligne dont l'outil n'est pas monté n'est
    ni disparue ni payée. La décroissance est portée par `_PLAFOND`."""
    tools = _servis()
    porteurs = {t.name for t in tools if t.output_schema is not None}
    servis = {t.name for t in tools}
    payes = sorted((_debt() & servis) - porteurs)
    assert not payes, (
        f"Ces outils n'ont plus d'enveloppe : {payes}. Retire-les de "
        f"{_DEBT_FILE.name} ET baisse `_PLAFOND` d'autant.")


def test_la_dette_ne_fait_que_baisser():
    assert len(_debt()) <= _PLAFOND, (
        f"la dette d'enveloppes a grossi ({len(_debt())} pour un plafond de {_PLAFOND}). "
        "Elle doit DÉCROÎTRE : rends un dict aux clés nommées plutôt qu'une liste nue.")
