"""Les défauts des droits déclarés : DÉCLARÉS par l'instance, jamais un défaut du code
(ADR 0070 §7, #1066).

Ce que ces bancs tiennent, sans base :
- une déclaration absente, illisible, incomplète ou hors genre refuse le démarrage
  (`verifier_defauts`) en nommant ce qui manque — jamais un 0 silencieux ;
- le joker `platform_key:*` couvre tout connecteur, une surcharge le précise ;
- `unlimited` s'écrit en toutes lettres et vaut `SANS_PLAFOND` ;
- le script d'amorçage imprime une déclaration que la garde accepte ;
- `server.main` joue la garde AVANT de préparer la base.
"""
from __future__ import annotations

import ast
import inspect
import json

import pytest

from oto_mcp import entitlements_catalogue as C
from oto_mcp import server
from oto_mcp.access import entitlements as A

COMPLETE = {"unipile": 0, "platform_unmetered": 0, "unipile_seats": 5,
            "members_max": "unlimited", "platform_key:*": 0}


def _declarer(monkeypatch, valeur) -> None:
    monkeypatch.setenv("OTO_ENTITLEMENT_DEFAULTS",
                       valeur if isinstance(valeur, str) else json.dumps(valeur))


def test_absente_le_demarrage_est_refuse(monkeypatch):
    monkeypatch.delenv("OTO_ENTITLEMENT_DEFAULTS", raising=False)
    with pytest.raises(RuntimeError, match="OTO_ENTITLEMENT_DEFAULTS absente"):
        A.verifier_defauts()


@pytest.mark.parametrize("retiree", sorted(COMPLETE))
def test_une_cle_du_catalogue_sans_defaut_refuse_le_demarrage(monkeypatch, retiree):
    _declarer(monkeypatch, {k: v for k, v in COMPLETE.items() if k != retiree})
    with pytest.raises(RuntimeError) as e:
        A.verifier_defauts()
    assert "défaut non déclaré pour" in str(e.value) and retiree in str(e.value)


@pytest.mark.parametrize("ajout, code", [
    ({"beta": 1}, "entitlement_unknown_key"),
    ({"platform_key:connecteur-inconnu": 3}, "entitlement_unknown_key"),
    ({"unipile": 2}, "entitlement_value_invalid"),
    ({"unipile_seats": None}, "entitlement_value_required"),
    ({"unipile_seats": -1}, "entitlement_value_invalid"),
])
def test_une_declaration_hors_catalogue_ou_hors_genre_est_refusee(monkeypatch, ajout, code):
    _declarer(monkeypatch, {**COMPLETE, **ajout})
    with pytest.raises(RuntimeError, match=code):
        A.verifier_defauts()


@pytest.mark.parametrize("brut", ["pas du json", "[1, 2]"])
def test_une_declaration_illisible_est_refusee(monkeypatch, brut):
    _declarer(monkeypatch, brut)
    with pytest.raises(RuntimeError, match="OTO_ENTITLEMENT_DEFAULTS"):
        A.verifier_defauts()


def test_joker_surcharge_et_sans_plafond(monkeypatch):
    _declarer(monkeypatch, {**COMPLETE, "platform_key:*": 3, "platform_key:hunter": 50})
    A.verifier_defauts()
    assert A.value_for(None, None, C.platform_key("hunter")) == 50
    assert A.value_for(None, None, C.platform_key("serper")) == 3
    assert A.value_for(None, None, C.MEMBERS_MAX) == C.SANS_PLAFOND


def test_sans_joker_chaque_connecteur_doit_etre_declare(monkeypatch):
    sans_joker = {k: v for k, v in COMPLETE.items() if k != C.PLATFORM_KEY_JOKER}
    _declarer(monkeypatch, sans_joker)
    with pytest.raises(RuntimeError, match=r"platform_key:\*"):
        A.verifier_defauts()
    from oto_mcp import providers
    _declarer(monkeypatch, {**sans_joker,
                            **{C.platform_key(c): 0 for c in providers.REGISTRY}})
    A.verifier_defauts()


def test_le_script_d_amorcage_imprime_une_declaration_acceptee(monkeypatch):
    from scripts import defauts_des_droits
    sortie = defauts_des_droits.defauts()
    _declarer(monkeypatch, sortie)
    A.verifier_defauts()
    assert sortie["platform_key:*"] == 0 and sortie["unipile"] == 0


def test_main_joue_la_garde_avant_de_preparer_la_base():
    """Lu dans le SOURCE de `main` (l'appeler monterait tout le serveur) : la garde
    est appelée, et avant `_prepare_database`."""
    arbre = ast.parse(inspect.getsource(server.main))
    appels = [n for n in ast.walk(arbre) if isinstance(n, ast.Call)]

    def ligne(nom):
        return min(n.lineno for n in appels
                   if (getattr(n.func, "attr", None) or getattr(n.func, "id", None)) == nom)

    assert ligne("verifier_defauts") < ligne("_prepare_database")
