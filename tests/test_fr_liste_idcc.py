"""La convention collective (IDCC) survit à la projection d'identité de `fr_get`.

`fr_search` accepte l'IDCC comme FILTRE, mais aucun outil `fr_*` ne restituait celui
d'une entreprise donnée : l'allowlist `_IDENTITY_KEEP` ne gardait pas
`complements.liste_idcc`, que l'API publique amont expose pourtant (vérifié sur
l'API : Danone → ['9999'], Danone Produits Frais France → ['0112']).

L'asymétrie était le piège — pouvoir filtrer par convention laisse croire qu'on peut
la lire. Conséquence mesurée : un champ « IDCC vérifié » resté à 0 % sur 500 lignes,
alors que le client l'avait demandé explicitement.

Ces tests exercent le VRAI `fr_get` (proxies FOD stubés, comme test_fr_get_batch) —
pas une réplique de sa logique, qui passerait même si le mapping restait cassé.
"""
from __future__ import annotations

import pytest


class _Reg:
    """FastMCP minimal : capture les fonctions décorées par @mcp.tool()."""

    def __init__(self):
        self.tools = {}

    def tool(self, *a, **k):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        if a and callable(a[0]):
            return deco(a[0])
        return deco


# Payload amont RÉEL (extrait de recherche-entreprises), avec le bruit d'annuaire
# qui cohabite avec l'IDCC dans `complements`.
_UPSTREAM = {
    "552032534": {
        "siren": "552032534", "nom_complet": "DANONE",
        "complements": {
            "liste_idcc": ["9999"],
            "est_bio": False, "est_qualiopi": False, "est_rge": False,
            "convention_collective_renseignee": True,
        },
    },
    # Convention non renseignée en amont — la clé ne doit pas apparaître à null.
    "531722742": {
        "siren": "531722742", "nom_complet": "OLIVIA DANON",
        "complements": {"liste_idcc": None, "est_bio": False},
    },
    # Aucun bloc `complements` du tout.
    "000000001": {"siren": "000000001", "nom_complet": "SANS COMPLEMENTS"},
}


class _Entreprises:
    def __init__(self, *a, **k): ...

    def get_by_siren(self, siren):
        return _UPSTREAM[siren]


class _Inpi:
    def __init__(self, *a, **k): ...

    def list_exercises(self, siren):
        return []


class _Bodacc:
    def __init__(self, *a, **k): ...

    def search_by_siren(self, siren, famille, limit):
        return {"results": [], "total_count": 0}


class _Noop:
    def __init__(self, *a, **k): ...


@pytest.fixture()
def fr_get(monkeypatch):
    monkeypatch.setattr("oto_mcp.fod.fr.entreprises", _Entreprises())
    monkeypatch.setattr("oto_mcp.fod.fr.inpi", _Inpi())
    monkeypatch.setattr("oto_mcp.fod.fr.bodacc", _Bodacc())
    monkeypatch.setattr("oto_mcp.fod.fr.egapro", _Noop())
    monkeypatch.setattr("oto.tools.sirene.SireneClient", _Noop)
    from oto_mcp.tools import fr
    reg = _Reg()
    fr.register(reg)
    return reg.tools["fr_get"]


def test_idcc_is_returned(fr_get):
    assert fr_get(siren="552032534")["identity"]["liste_idcc"] == ["9999"]


def test_idcc_is_flat_not_nested(fr_get):
    """L'agent lit `identity.liste_idcc`, pas `identity.complements.liste_idcc`."""
    ident = fr_get(siren="552032534")["identity"]
    assert "complements" not in ident


def test_the_rest_of_complements_is_not_dragged_in(fr_get):
    """~30 booléens d'annuaire vivent dans `complements` : les remonter gonflerait
    chaque profil sans que personne ne les ait demandés."""
    ident = fr_get(siren="552032534")["identity"]
    assert "est_bio" not in ident
    assert "convention_collective_renseignee" not in ident


def test_an_unset_idcc_omits_the_key(fr_get):
    """Pas de clé à `None` : elle laisserait croire à une convention absente là où la
    donnée n'est simplement pas renseignée en amont."""
    assert "liste_idcc" not in fr_get(siren="531722742")["identity"]


def test_no_complements_block_at_all_is_fine(fr_get):
    ident = fr_get(siren="000000001")["identity"]
    assert ident["nom_complet"] == "SANS COMPLEMENTS"
    assert "liste_idcc" not in ident
