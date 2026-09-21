"""`scripts/contrat-front.py` — ce que le front LIT dans une réponse est jugé comme ce qu'il ENVOIE.

Jusqu'au 21/09/2026, seules les entrées rougissaient : un champ de réponse retiré sortait en
simple avertissement, et deux retraits réellement lus par le front (le `code` d'une
invitation, les `args` d'un appel) ont atteint la production sans arrêter aucun déploiement.
La règle du lecteur : le serveur peut rendre PLUS, jamais MOINS ni AUTRE CHOSE.
"""
from __future__ import annotations

import copy
import importlib.util
import pathlib

import pytest

_CHEMIN = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "contrat-front.py"
_spec = importlib.util.spec_from_file_location("contrat_front", _CHEMIN)
contrat = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(contrat)


def _op(schema: dict) -> dict:
    return {"operationId": "x",
            "responses": {"200": {"content": {"application/json": {"schema": schema}}},
                          "404": {"description": "introuvable"}}}


EPINGLE = _op({
    "type": "object",
    "required": ["invitations"],
    "properties": {
        "invitations": {"type": "array", "items": {
            "type": "object",
            "required": ["id", "code"],
            "properties": {"id": {"type": "integer"}, "code": {"type": "string"},
                           "note": {"anyOf": [{"type": "string"}, {"type": "null"}]}},
        }},
    },
})


def _servie(modif) -> dict:
    op = copy.deepcopy(EPINGLE)
    modif(op["responses"]["200"]["content"]["application/json"]["schema"])
    return op


def _item(sch: dict) -> dict:
    return sch["properties"]["invitations"]["items"]


@pytest.mark.parametrize("modif, attendu", [
    (lambda s: _item(s)["properties"].pop("code"), "200.invitations[] : le champ « code » a disparu"),
    (lambda s: _item(s)["required"].remove("code"), "le champ « code » n'est plus garanti"),
    (lambda s: _item(s)["properties"].__setitem__(
        "id", {"anyOf": [{"type": "integer"}, {"type": "null"}]}), "peut maintenant valoir ['null']"),
    (lambda s: _item(s)["properties"].__setitem__("id", {"type": "string"}), "valoir ['string']"),
])
def test_ce_que_le_front_lit_et_ne_trouvera_plus_est_rouge(modif, attendu):
    raisons = contrat.casse_les_appels(EPINGLE, _servie(modif))
    assert any(attendu in r for r in raisons), raisons


def test_une_reponse_reussie_disparue_est_rouge():
    servie = copy.deepcopy(EPINGLE)
    servie["responses"] = {"201": servie["responses"].pop("200")}
    assert "la réponse 200 a disparu" in contrat.casse_les_appels(EPINGLE, servie)


@pytest.mark.parametrize("modif", [
    lambda s: _item(s)["properties"].__setitem__("expire_le", {"type": "string"}),
    lambda s: s["properties"].__setitem__("total", {"type": "integer"}),
    lambda s: _item(s)["properties"].__setitem__("note", {"type": "string"}),
    lambda s: _item(s)["required"].append("note"),
], ids=["champ-ajoute", "champ-ajoute-a-la-racine", "type-resserre", "champ-desormais-garanti"])
def test_rendre_plus_n_est_pas_une_casse(modif):
    assert contrat.casse_les_appels(EPINGLE, _servie(modif)) == []


def test_une_reponse_d_erreur_n_est_pas_jugee():
    servie = copy.deepcopy(EPINGLE)
    del servie["responses"]["404"]
    assert contrat.casse_les_appels(EPINGLE, servie) == []
