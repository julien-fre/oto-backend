"""La liste fermée des écritures chez un tiers (`recipes/ecritures.py`, #1202 lot 3) ne
ment pas : chaque outil est servi par le catalogue monté, chaque op qu'elle nomme est
une op que l'outil connaît, aucune ne détruit, et un outil sans `op` n'en a vraiment
pas."""
from __future__ import annotations

import pytest

from _mcp_app import static_mcp
from oto_mcp.recipes import ecritures as ec


def _ops(tool) -> set:
    props = (getattr(tool, "parameters", None) or {}).get("properties") or {}
    op = props.get("op") or {}
    enum = list(op.get("enum") or [])
    for alt in op.get("anyOf") or []:
        enum += alt.get("enum") or []
    return {str(o) for o in enum}


@pytest.mark.asyncio
async def test_chaque_outil_est_servi_et_ses_ops_existent():
    servis = {t.name: t for t in await static_mcp().list_tools(run_middleware=False)}
    assert not set(ec.ECRITURES) - set(servis), sorted(set(ec.ECRITURES) - set(servis))
    for nom, e in ec.ECRITURES.items():
        connues = _ops(servis[nom])
        if e.ecrire is None:
            assert not connues, f"{nom} a une `op` : nommer ses ops"
        else:
            assert (e.lire or set()) | e.ecrire <= connues, (nom, connues)


def test_aucune_op_ne_detruit_et_lire_n_ecrit_pas():
    for nom, e in ec.ECRITURES.items():
        assert not (e.lire or set()) & ec.OPS_DESTRUCTRICES, nom
        assert not (e.ecrire or set()) & ec.OPS_DESTRUCTRICES, nom
        assert not (e.lire or set()) & (e.ecrire or set()), nom


def test_juger_exige_une_op_litterale():
    assert ec.juger("folk_record", {})[0] == "refus"
    assert ec.juger("folk_record", {"op": "delete"})[0] == "refus"
    assert ec.juger("folk_record", {"op": "search"}) == ("lire", None)
    assert ec.juger("folk_record", {"op": "create"}) == ("ecrire", "folk_record:create")
    assert ec.juger("lemlist_create_lead", {"campaign_id": "c"}) == (
        "ecrire", "lemlist_create_lead")


def test_les_declarables_et_les_envois():
    assert "folk_record:create" in ec.declarables()
    assert "folk_record:search" not in ec.declarables()
    assert "folk_record:delete" not in ec.declarables()
    assert ec.envoient(["folk_record:create", "lemlist_create_lead"]) == [
        "lemlist_create_lead"]


def test_allo_act_ecrit_la_fiche_et_la_file_jamais_un_envoi():
    assert ec.juger("allo_act", {"op": "create_person"}) == ("ecrire", "allo_act:create_person")
    assert ec.juger("allo_act", {"op": "add_queue_numbers"}) == (
        "ecrire", "allo_act:add_queue_numbers")
    for op in ("send_sms", "create_queue", "remove_queue_numbers"):
        assert ec.juger("allo_act", {"op": op})[0] == "refus", op
    assert ec.envoient(["allo_act:create_person"]) == []


def test_les_numeros_d_une_file_se_comptent_un_par_un():
    from oto_mcp.recipes.pont import _fiches
    assert _fiches({"op": "add_queue_numbers", "entries": [{"number": "+33"}] * 3}) == 3
    assert _fiches({"op": "add_queue_numbers", "entries": "x"}) is None
    assert _fiches({"op": "create_person"}) == 1
