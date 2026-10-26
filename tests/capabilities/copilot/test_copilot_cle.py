"""Charging the in-app agent's model calls: never free on a re-billed key. A hold the
ledger could not take refuses the call (outside `off`); a ledger refusal keeps its
status; a key the org pays itself is never metered."""
from __future__ import annotations

import pytest

from oto_mcp.access import credits as budget
from oto_mcp.access import credits_tours as tours
from oto_mcp.capabilities.copilot import cle

REFACTUREE = cle.Cle("sk-test", None, "platform")
DE_L_ORG = cle.Cle("sk-test", None, None)


def test_la_cle_ne_se_montre_jamais():
    assert "sk-test" not in repr(REFACTUREE)


@pytest.mark.parametrize("mode,refuse", [("report", True), ("enforce", True), ("off", False)])
def test_reservation_non_tenue(monkeypatch, mode, refuse):
    monkeypatch.setattr(tours, "reserver_tour", lambda *a, **k: {"held": False, "reserved": 0})
    monkeypatch.setattr(budget, "mode", lambda: mode)
    if refuse:
        with pytest.raises(cle.SansCle) as e:
            cle.reserver(REFACTUREE, "u", "s", 1, "m", 10)
        assert e.value.statut == 422 and e.value.code == budget.SANS_BAREME
    else:
        cle.reserver(REFACTUREE, "u", "s", 1, "m", 10)


@pytest.mark.parametrize("code,statut", [(budget.INSUFFISANT, 402),
                                         (budget.SANS_BAREME, 422),
                                         (budget.INDISPONIBLE, 503)])
def test_refus_du_registre(monkeypatch, code, statut):
    def refuse(*a, **k):
        raise tours.TourRefuse(code, "no", False)
    monkeypatch.setattr(tours, "reserver_tour", refuse)
    with pytest.raises(cle.SansCle) as e:
        cle.reserver(REFACTUREE, "u", "s", 1, "m", 10)
    assert (e.value.code, e.value.statut) == (code, statut)


def test_reservation_tenue_passe(monkeypatch):
    monkeypatch.setattr(tours, "reserver_tour", lambda *a, **k: {"held": True, "reserved": 3})
    cle.reserver(REFACTUREE, "u", "s", 1, "m", 10)


def test_cle_de_l_org_jamais_mesuree(monkeypatch):
    def jamais(*a, **k):
        raise AssertionError("metered")
    monkeypatch.setattr(tours, "reserver_tour", jamais)
    monkeypatch.setattr(tours, "regler_tour", jamais)
    monkeypatch.setattr(tours, "clore_les_tours", jamais)
    cle.reserver(DE_L_ORG, "u", "s", 1, "m", 10)
    cle.regler(DE_L_ORG, "u", "s", 1, "m", {"input_tokens": 1}, "thr")
    cle.clore(DE_L_ORG, "u")
    cle.liberer(DE_L_ORG, "u")


def test_reglement_aux_quatre_comptes(monkeypatch):
    vu = {}
    monkeypatch.setattr(tours, "regler_tour",
                        lambda uid, sub, org, jetons, meta, modele: vu.update(
                            uid=uid, jetons=jetons, meta=meta))
    cle.regler(REFACTUREE, "copilot:thr:000001", "s", 1, "claude-opus-5-5",
               {"input_tokens": 5, "output_tokens": 7}, "thr")
    assert vu["jetons"] == {"input_tokens": 5, "output_tokens": 7,
                            "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
    assert vu["meta"]["key_mode"] == "platform"


def test_uid_jamais_prefixe_d_un_autre():
    assert not cle.uid_du_tour("t", 10).startswith(cle.uid_du_tour("t", 1))


def test_echec_http_libere_flux_coupe_regle(monkeypatch):
    """The HTTP-error path releases the hold; a stream cut after output settles it."""
    import asyncio

    from oto_mcp.capabilities.copilot import boucle, modele
    appels = []
    monkeypatch.setattr(cle, "reserver", lambda *a: None)
    monkeypatch.setattr(cle, "liberer", lambda c, uid: appels.append(("liberer", uid)))
    monkeypatch.setattr(cle, "clore", lambda c, uid: appels.append(("clore", uid)))

    async def sauver(_):
        return None
    monkeypatch.setattr(boucle, "sauver", sauver)

    def jouer(flux):
        monkeypatch.setattr(modele, "flux", flux)
        fil = {"id": "thr", "sub": "s", "org_id": 1, "model_calls": 0, "system": "",
               "messages": [], "client_tools": []}
        b = boucle.Boite(frozenset(), {}, [])

        async def go():
            with pytest.raises(modele.ErreurModele):
                async for _ in boucle._un_appel(fil, b, REFACTUREE, {}):
                    pass
        asyncio.run(go())

    async def refus_http(*a):
        raise modele.ErreurModele("bad", 400)
        yield  # pragma: no cover

    async def coupe(*a):
        yield ("text", "partial")
        raise modele.ErreurModele("cut")

    jouer(refus_http)
    jouer(coupe)
    assert [a[0] for a in appels] == ["liberer", "clore"]
