"""foncier_geocode — repli quand le code postal fait rater le numéro (feedback #324).

Cas source, mesuré sur la BAN le 30/07/2026 : « 227 rue Saint-Fuscien 80000 Amiens »
(CP repris de SIRENE) ne rend qu'une `locality` à 0.57, silencieusement ; la même
requête SANS le 80000 rend le `housenumber` exact à 0.98 (CP réel du tronçon : 80090).
Le CP se cache à deux endroits — l'argument ET la chaîne — et c'est celui de la chaîne
qui pèse : retirer le seul argument ne changeait rien.
"""
from __future__ import annotations

import pytest


class _Reg:
    def __init__(self):
        self.tools = {}

    def tool(self, *a, **k):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        if a and callable(a[0]):
            return deco(a[0])
        return deco


def _cand(type_, score, postcode="80000"):
    return {"label": f"{type_} label", "score": score, "type": type_,
            "citycode": "80021", "postcode": postcode, "lat": 49.88, "lon": 2.30}


LOCALITY = _cand("locality", 0.574)
HOUSENUMBER = _cand("housenumber", 0.985, postcode="80090")


@pytest.fixture()
def geocode(monkeypatch):
    """Enregistre les tools foncier avec un client BAN scriptable ; renvoie
    (fonction du tool, journal des appels amont)."""
    calls = []

    class _Ban:
        def __init__(self, script):
            self.script = script

        def search(self, adresse, limit=5, postcode=None, citycode=None):
            calls.append({"adresse": adresse, "postcode": postcode, "citycode": citycode})
            return self.script(adresse, postcode)

    from oto_mcp import fod_foncier
    from oto_mcp.tools import foncier

    def _make(script):
        monkeypatch.setattr(fod_foncier, "ban", _Ban(script))
        reg = _Reg()
        foncier.register(reg)
        return reg.tools["foncier_geocode"]

    return _make, calls


def test_retries_without_postcode_and_tags_the_result(geocode, monkeypatch):
    make, calls = geocode
    # La BAN ne rend le numéro que si le CP a disparu de la CHAÎNE.
    fn = make(lambda adresse, postcode: [] if "80000" in adresse else [HOUSENUMBER])
    out = fn("227 rue Saint-Fuscien 80000 Amiens", code_postal="80000")

    assert [c["type"] for c in out] == ["housenumber"]
    assert out[0]["relaxed"] == "postcode"       # l'agent voit POURQUOI le CP diffère
    assert out[0]["postcode"] == "80090"         # le vrai CP du tronçon, pas celui demandé
    assert len(calls) == 2
    assert calls[1]["adresse"] == "227 rue Saint-Fuscien Amiens"   # CP retiré du texte
    assert calls[1]["postcode"] is None                            # et de l'argument


def test_warns_when_no_housenumber_survives_the_retry(geocode):
    make, calls = geocode
    fn = make(lambda adresse, postcode: [LOCALITY])
    out = fn("227 rue Saint-Fuscien 80000 Amiens")

    # Le repli a bien été tenté, et son échec est DIT — une locality à 0.57 rendue
    # nue est exactement ce qui a fait conclure « adresse introuvable » à tort.
    assert len(calls) == 2
    assert out[0]["warning"] == "no_housenumber_match"


def test_direct_hit_costs_one_call_and_is_untagged(geocode):
    make, calls = geocode
    fn = make(lambda adresse, postcode: [HOUSENUMBER])
    out = fn("227 rue Saint-Fuscien 80000 Amiens", code_postal="80000")

    assert len(calls) == 1                        # pas de surcoût sur le cas nominal
    assert "relaxed" not in out[0] and "warning" not in out[0]


def test_query_without_a_street_number_is_left_alone(geocode):
    """Une requête sans numéro n'a aucune raison d'attendre un housenumber :
    ni repli, ni avertissement (sinon toute recherche de rue serait taguée)."""
    make, calls = geocode
    fn = make(lambda adresse, postcode: [_cand("street", 0.81)])
    out = fn("rue Saint-Fuscien 80000 Amiens")

    assert len(calls) == 1
    assert "warning" not in out[0]
