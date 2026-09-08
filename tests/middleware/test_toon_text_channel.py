"""Le canal texte part en TOON quand la charge y gagne, et en JSON sinon.

Le banc fait traverser à un résultat la chaîne RÉELLE montée sur `_test_mcp()` — les
instances de middleware du vrai serveur, dans leur vrai ordre — et lit ce qui sort
côté client. C'est indispensable ici : la moitié de ce qu'il y a à prouver est une
INTERACTION, pas un encodage. Deux voisins liraient mal du TOON, et chacun échoue
dans une direction différente :

- la rédaction, plus INTERNE, doit avoir déjà retiré les champs sensibles quand on
  encode — sinon `extract_payload` rendrait `None` sur du TOON, la policy ne
  s'appliquerait plus, et la sortie partirait en clair (échec OUVERT) ;
- le rendu du vide, plus EXTERNE, juge en relisant le texte en JSON — un vide rendu
  en TOON le rendrait aveugle et la phrase ne partirait plus.

Un banc qui n'appellerait que `toon.encode` ne dirait rien de tout ça.
"""
from __future__ import annotations

import asyncio
import json

from _mcp_app import static_mcp as _test_mcp

from fastmcp import Client, FastMCP
from fastmcp.tools.tool import ToolResult
from mcp.types import TextContent
from oto.tools.common import FieldFilter

from oto_mcp import redaction

# Assez de lignes uniformes pour passer le plancher de taille et gagner largement.
TABLE = {
    "rows": [
        {"id": i, "nom": f"ligne numero {i}", "statut": "actif", "compte": i * 7}
        for i in range(40)
    ],
    "count": 40,
    "next_cursor": None,
}

# Uniforme aussi, mais chaque valeur pèse bien plus que le nom de sa colonne : le
# tabulaire n'y gagne presque rien. C'est la charge qui a dicté la décision par
# charge — mesurée à 12,5 % au mieux sur une vraie table à prose.
PROSE = {
    "rows": [
        {"id": i, "texte": "  ".join(["une phrase de contexte qui pese"] * 14)}
        for i in range(8)
    ],
    "count": 8,
}


def _banc(fn, *, nom: str = "recherche"):
    m = FastMCP("banc")
    for mw in _test_mcp().middleware:
        m.add_middleware(mw)
    m.tool(name=nom)(fn)
    return m


def _servir(m: FastMCP, nom: str = "recherche"):
    async def appel():
        async with Client(m) as c:
            return await c.call_tool(nom, {})
    r = asyncio.run(appel())
    return "".join(getattr(b, "text", "") for b in r.content), r.structured_content


def _outil(payload):
    def recherche() -> dict:
        return payload
    return recherche


def _allumer(monkeypatch):
    monkeypatch.setenv("OTO_TOON_TEXT_CHANNEL", "1")


# ── Inerte tant qu'on ne l'allume pas ───────────────────────────────────────────

def test_inerte_par_defaut(monkeypatch):
    monkeypatch.delenv("OTO_TOON_TEXT_CHANNEL", raising=False)
    texte, _ = _servir(_banc(_outil(TABLE)))
    assert json.loads(texte)["count"] == 40


# ── Ce qui gagne part en TOON, ce qui ne gagne pas reste en JSON ────────────────

def test_une_table_uniforme_part_en_toon(monkeypatch):
    _allumer(monkeypatch)
    texte, structure = _servir(_banc(_outil(TABLE)))
    assert texte.startswith("rows[40]{id,nom,statut,compte}:")
    assert "  0,ligne numero 0,actif,0" in texte
    # Le canal structuré garde son JSON pour les clients qui parsent.
    assert structure == TABLE


def test_une_charge_a_prose_reste_en_json(monkeypatch):
    _allumer(monkeypatch)
    texte, _ = _servir(_banc(_outil(PROSE)))
    assert json.loads(texte)["count"] == 8


def test_le_texte_servi_n_est_jamais_plus_long_qu_avant(monkeypatch):
    for charge in (TABLE, PROSE):
        monkeypatch.delenv("OTO_TOON_TEXT_CHANNEL", raising=False)
        avant, _ = _servir(_banc(_outil(charge)))
        monkeypatch.setenv("OTO_TOON_TEXT_CHANNEL", "1")
        apres, _ = _servir(_banc(_outil(charge)))
        assert len(apres) <= len(avant)


# ── Les deux interactions d'ordre ───────────────────────────────────────────────

def test_la_redaction_s_applique_avant_l_encodage(monkeypatch):
    """L'échec OUVERT que la place dans la chaîne empêche : sous la rédaction, le
    TOON la rendrait aveugle et la valeur sensible partirait en clair."""
    _allumer(monkeypatch)
    monkeypatch.setattr(
        redaction, "_resolve_field_filter",
        lambda _s: FieldFilter(rules=[{"fields": ["secret"], "action": "drop"}]))
    charge = {
        "rows": [{"id": i, "nom": f"ligne numero {i}", "secret": "NE-DOIT-PAS-SORTIR"}
                 for i in range(40)],
        "count": 40,
    }
    texte, _ = _servir(_banc(_outil(charge)))
    assert "NE-DOIT-PAS-SORTIR" not in texte
    assert "secret" not in texte


def test_le_vide_reste_rendu_en_phrase(monkeypatch):
    """Le rendu du vide est plus EXTERNE : il doit encore pouvoir juger. On ne
    touche donc jamais un résultat vide, même allumé."""
    _allumer(monkeypatch)
    texte, _ = _servir(_banc(_outil({"total_count": 0, "rows": []})))
    assert texte == redaction.EMPTY_MESSAGE_DEFAULT


def test_une_erreur_n_est_pas_reecrite(monkeypatch):
    _allumer(monkeypatch)

    def recherche() -> dict:
        raise ValueError("boum")

    m = _banc(recherche)

    async def appel():
        async with Client(m) as c:
            return await c.call_tool("recherche", {}, raise_on_error=False)

    assert asyncio.run(appel()).is_error


# ── Le détour par `oto_call` ────────────────────────────────────────────────────

def test_un_resultat_deja_emis_par_le_handler_est_reecrit(monkeypatch):
    """La forme que rend `oto_call` traverse quand même l'encodage.

    `oto_call` atteint sa cible par `Tool.run`, donc HORS chaîne de middleware — ce
    qui a obligé la rédaction à se ré-appliquer à la main dans le handler, parce
    qu'elle a besoin du namespace de la CIBLE, que la chaîne ne voit pas (elle voit
    `oto_call`). L'encodage, lui, ne dépend d'aucun namespace : il lit le canal texte
    qui sort du handler, quel que soit ce qui l'a produit. Ce test le PROUVE au lieu
    de le déduire, en rendant depuis le handler un `ToolResult` déjà bâti, la forme
    exacte que rend `oto_call` (cf. `redaction.rebuild_result`).

    ⚠️ Et c'est ICI que ça doit vivre, pas dans `tests/test_oto_call_dispatch.py`
    malgré son nom : ce banc-là appelle la fonction `oto_call` DIRECTEMENT
    (`asyncio.run(fn(ctx=…))`), donc sans chaîne de middleware. Il ne peut
    structurellement rien dire d'un middleware.
    """
    _allumer(monkeypatch)

    def recherche() -> ToolResult:
        brut = json.dumps(TABLE, ensure_ascii=False)
        return ToolResult(content=[TextContent(type="text", text=brut)],
                          structured_content=TABLE)

    texte, structure = _servir(_banc(recherche))
    assert texte.startswith("rows[40]{id,nom,statut,compte}:")
    assert structure == TABLE
