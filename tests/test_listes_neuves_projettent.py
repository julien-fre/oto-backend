"""Les cinq outils de liste arrivés le 11/09 déclarent ce que leur défaut retire.

`tests/test_sorties_listes_projetees.py` refuse un outil NEUF qui pagine sans
projeter : `foncier_proprietaire`, `foncier_emissions`, `urba_elus`, `urba_annuaire`
et `fr_tenders_awarded` y tombaient tous les cinq. Le cliquet ne juge que la FORME
(un paramètre, un appel) ; ce banc juge ce que l'outil REND.

Le même jour, trois d'entre eux sont devenus des `op` d'outils existants, pour ne pas
multiplier les verbes MCP : les émissions IREP sont `foncier_icpe(op="emissions")`, les
marchés attribués `fr_tenders_search(op="awarded")`, les élus
`urba_annuaire(op="maires"|"presidents_epci")`. Le banc suit : chaque cas porte les
arguments de son `op`.

- `foncier_proprietaire` : chaque enregistrement portait `raw`, la ligne BDNB dont
  toutes les colonnes sont déjà rendues, remises en forme, à côté. Duplication pure :
  le défaut la retire, `full=True` la rend ;
- les quatre autres n'ont rien de dupliqué — leur enregistrement est déjà une vue
  choisie par le client de la source. Ils offrent `fields` : omis, rien ne bouge ;
  posé, les enregistrements se resserrent et l'enveloppe reste.

Doublures au niveau des proxies FOD, comme `test_foncier_op_dispatch` : l'outil lit
le proxy à l'appel ou à l'enregistrement, on le remplace avant d'enregistrer.
"""
from __future__ import annotations

import asyncio
import copy

import pytest


def _outil(module, nom):
    from fastmcp import FastMCP

    m = FastMCP("banc-listes-neuves")
    module.register(m)
    return asyncio.run(m.get_tool(nom)).fn


class _Proxy:
    """Rend une copie du payload donné, quelle que soit la méthode appelée."""

    def __init__(self, payload):
        self._payload = payload

    def __getattr__(self, _nom):
        return lambda *a, **k: copy.deepcopy(self._payload)


_BATIMENT = {
    "ref_key": "bdnb-bg-1", "batiment_groupe_id": "bdnb-bg-1", "code_commune": "59350",
    "proprietaire": {"siren": "123456789", "denomination": "SCI X"},
    "bati": {"emprise_m2": 1200.0}, "energie": {"conso_pro_elec_kwh": 1.0},
    "raw": {"batiment_groupe_id": "bdnb-bg-1", "siren": "123456789",
            "surface_emprise_sol": 1200},
}
_BDNB = {"total": 1, "requetes": 1, "tronque": False,
         "couverture_partielle": "personnes morales seules", "signaux": [_BATIMENT]}


def test_le_proprietaire_ne_rend_plus_sa_ligne_brute_par_defaut(monkeypatch):
    from oto_mcp.fod import foncier as fod_foncier
    from oto_mcp.tools import foncier as F

    monkeypatch.setattr(fod_foncier, "bdnb", _Proxy(_BDNB))
    out = _outil(F, "foncier_proprietaire")(code_commune="59350")
    assert "raw" not in out["signaux"][0]
    # Seule la copie brute part : le reste de l'enregistrement et l'enveloppe restent.
    attendu = {k: v for k, v in _BATIMENT.items() if k != "raw"}
    assert out["signaux"][0] == attendu
    assert {k: v for k, v in out.items() if k != "signaux"} == \
        {k: v for k, v in _BDNB.items() if k != "signaux"}


def test_le_proprietaire_rend_sa_ligne_brute_sur_full(monkeypatch):
    from oto_mcp.fod import foncier as fod_foncier
    from oto_mcp.tools import foncier as F

    monkeypatch.setattr(fod_foncier, "bdnb", _Proxy(_BDNB))
    assert _outil(F, "foncier_proprietaire")(code_commune="59350", full=True) == _BDNB


def _cas():
    """(module d'outils, nom, comment poser la doublure, payload) — un par outil."""
    from oto_mcp.fod import foncier as fod_foncier
    from oto_mcp.fod import fr as fod_fr
    from oto_mcp.fod import urba as fod_urba
    from oto_mcp.tools import foncier as F
    from oto_mcp.tools import fr as FR
    from oto_mcp.tools import urba as U

    irep = {"annee": 2024, "total": 1, "tronque": False, "sous_seuil": 0,
            "signaux": [{"siret": "1", "nom": "Usine", "quantite": 5.0, "unite": "kg"}]}
    decp = {"total": 1, "rendus": 1, "tronque": False,
            "signaux": [{"objet": "Toiture", "montant": 10, "titulaires": []}]}
    elus = {"total": 1, "tronque": False,
            "signaux": [{"nom": "Durand", "prenom": "Ana", "code_commune": "59350"}]}
    annuaire = {"total": 1, "signaux": [{"nom": "Mairie", "courriel": "m@x.fr",
                                         "responsables": []}]}
    return [
        (F, "foncier_icpe",
         lambda mp, p: mp.setattr(fod_foncier, "irep", _Proxy(p)), irep, "siret",
         {"op": "emissions", "departement": "59"}),
        (FR, "fr_tenders_search",
         lambda mp, p: mp.setattr(fod_fr, "search_decp",
                                  lambda **k: copy.deepcopy(p)), decp, "objet",
         {"op": "awarded", "query": "toiture"}),
        (U, "urba_annuaire",
         lambda mp, p: mp.setattr(fod_urba, "elus", _Proxy(p)), elus, "nom",
         {"op": "maires", "code_commune": "59350"}),
        (U, "urba_annuaire",
         lambda mp, p: mp.setattr(fod_urba, "annuaire", _Proxy(p)), annuaire, "nom",
         {"code_commune": "59350"}),
    ]


@pytest.mark.parametrize("i", range(4))
def test_sans_fields_rien_ne_bouge(monkeypatch, i):
    module, nom, poser, payload, _, kw = _cas()[i]
    poser(monkeypatch, payload)
    assert _outil(module, nom)(**kw) == payload


@pytest.mark.parametrize("i", range(4))
def test_fields_resserre_les_enregistrements_et_garde_l_enveloppe(monkeypatch, i):
    module, nom, poser, payload, cle, kw = _cas()[i]
    poser(monkeypatch, payload)
    out = _outil(module, nom)(**kw, fields=[cle])
    assert out["signaux"] == [{cle: payload["signaux"][0][cle]}]
    assert {k: v for k, v in out.items() if k != "signaux"} == \
        {k: v for k, v in payload.items() if k != "signaux"}


# --- une op n'avale pas en silence un paramètre qu'elle ne sait pas honorer ----------
# Trois outils ont gagné une `op` qui change ce que chaque paramètre veut dire. Le
# risque de la fusion : `fr_tenders_search(op="awarded", type_marche="TRAVAUX")` qui
# ignorerait le filtre et rendrait tous les marchés — une réponse fausse qui a l'air
# juste. Chaque paramètre hors de son op est donc refusé en le nommant.

@pytest.mark.parametrize("module_nom,kw,mot", [
    ("fr", {"op": "awarded", "query": "x", "type_marche": "TRAVAUX"}, "type_marche"),
    ("fr", {"op": "notices", "titulaire_siret": "12345678901234"}, "titulaire_siret"),
    ("fr", {"op": "awarded"}, "at least one criterion"),
    ("foncier", {"op": "emissions", "departement": "59", "page": 2}, "page"),
    ("foncier", {"op": "installations", "departement": "59"}, "departement"),
    ("foncier", {"op": "emissions"}, "requiert"),
    ("urba", {"op": "maires", "type_service": "mairie", "code_commune": "59350"}, "type_service"),
    ("urba", {"op": "presidents_epci", "code_commune": "59350"}, "code_commune"),
    ("urba", {"op": "services"}, "requiert"),
])
def test_un_parametre_hors_de_son_op_est_refuse_en_le_nommant(module_nom, kw, mot):
    from oto_mcp.mcp_errors import McpError
    from oto_mcp.tools import foncier as F, fr as FR, urba as U

    module, nom = {"fr": (FR, "fr_tenders_search"), "foncier": (F, "foncier_icpe"),
                   "urba": (U, "urba_annuaire")}[module_nom]
    with pytest.raises(McpError) as exc:
        _outil(module, nom)(**kw)
    assert mot in str(exc.value)
