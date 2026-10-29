"""Les waterfalls asynchrones sur un TABLEAU : `update_rows(asynchronous=true)` SOUMET
l'étape asynchrone (Pipecorn) sans l'attendre et écrit la ligne `pending` avec sa
relève ; `collect_rows` relit (une lecture par travail), écrit la trouvaille, poursuit
la cascade quand le travail n'a rien trouvé, laisse en attente ce qui n'est pas fini et
remet en file un travail échoué. Des centaines de lignes passent en quelques appels."""
from __future__ import annotations

import asyncio
import time

import pytest
from fastmcp import FastMCP

from oto_mcp.tools import waterfall_lignes
from oto_mcp.tools.meta import IssueCible

from _waterfall_faux import calls  # noqa: F401 — fournisseurs simulés
from test_waterfall_lignes import _Store, table  # noqa: F401 — le tableau simulé

PHONE = dict(datastore="42", inputs={"linkedin_url": "linkedin"},
             output={"phone": "phone", "provider": "phone_source"},
             status_column="phone_status")


def _act(**kw):
    m = FastMCP("t")
    waterfall_lignes.register(m)
    return asyncio.run(asyncio.run(m.get_tool("waterfall_phone_act")).fn(**kw))


def _id(url: str) -> str:
    """L'identifiant de travail Pipecorn d'un profil (un UUID par profil)."""
    n = int(url.rstrip("/").rsplit("-", 1)[1])
    return f"00000000-0000-4000-8000-{n:012d}"


def _soumission(args):
    return IssueCible(ok=True, result={"enrichment_id": _id(args["linkedin_url"]),
                                       "status": "pending", "find": ["phone"]})


def _lignes(n: int, doublons: int = 0) -> list[dict]:
    lignes = [{"_id": f"r{i}", "linkedin": f"https://www.linkedin.com/in/jane-{i}/"}
              for i in range(1, n + 1)]
    lignes += [{"_id": f"d{i}", "linkedin": f"https://www.linkedin.com/in/jane-{i}/"}
               for i in range(1, doublons + 1)]
    return lignes


def _soumettre(calls, table, n: int, **kw):
    table["lignes"] = _lignes(n, kw.pop("doublons", 0))
    calls["answers"]["pipecorn"] = _soumission
    return _act(op="update_rows", filter={}, asynchronous=True, **PHONE, **kw)


# --- l'envoi -------------------------------------------------------------------------

def test_l_envoi_ecrit_pending_et_la_releve_sur_chaque_ligne(calls, table):
    out = _soumettre(calls, table, 2, doublons=1)
    # Deux profils distincts, une soumission chacun ; le doublon partage la sienne.
    assert [a["op"] for n, a in calls["made"]] == ["enrich", "enrich"]
    assert out["submitted"] == 3 and out["unique_lookups"] == 2
    assert out["left_out_async_steps"] == []
    assert "collect_rows" in out["next_step"]
    e = table["store"].ecrites
    assert e["r1"]["phone_status"] == "pending" and e["r1"]["phone_status.comment"] == "pipecorn"
    job = dict(e["r1"]["phone_status_job"])
    assert abs(job.pop("submitted_at") - time.time()) < 60
    assert job == {"provider": "pipecorn", "tool": "pipecorn",
                   "arguments": {"op": "result", "enrichment_id": _id("x-1"),
                                 "find": ["phone"]}}
    assert e["d1"]["phone_status_job"] == e["r1"]["phone_status_job"]
    # La colonne de relève est déclarée, en json.
    assert {"key": "phone_status_job", "type": "json"} in table["store"].patches[0]


def test_l_envoi_de_200_lignes_tient_dans_un_appel(calls, table):
    debut = time.monotonic()
    out = _soumettre(calls, table, 200, batch_size=200)
    assert out["submitted"] == 200 and out["stopped"] is None
    assert time.monotonic() - debut < 10
    assert all(r["phone_status"] == "pending" for r in table["lignes"])


def test_sans_asynchronous_un_lot_reste_borne_a_50(calls, table):
    table["lignes"] = _lignes(3)
    with pytest.raises(Exception, match="1 à 50"):
        _act(op="update_rows", filter={}, batch_size=100, **PHONE)


# --- la relève -------------------------------------------------------------------------

def _releve(par_id: dict):
    """Réponse de `pipecorn op=result` par identifiant : `None` = encore en cours."""
    def repondre(args):
        r = par_id.get(args["enrichment_id"])
        if r is None:
            return IssueCible(ok=True, result={"done": False, "status": "pending"})
        if isinstance(r, IssueCible):
            return r
        return IssueCible(ok=True, result={"done": True, "status": "finished",
                                           "phone_details": r})
    return repondre


def test_la_releve_ecrit_les_trouvailles_et_laisse_ce_qui_n_est_pas_fini(calls, table):
    _soumettre(calls, table, 3, doublons=1)
    calls["made"].clear()
    calls["answers"]["pipecorn"] = _releve({
        _id("x-1"): [{"number": "+33612345678", "type": "mobile"}],
        _id("x-2"): None,
        _id("x-3"): [{"number": "+33600000003", "type": "mobile"}]})
    out = _act(op="collect_rows", filter={}, **PHONE)
    lus = [a["enrichment_id"] for n, a in calls["made"] if n == "pipecorn"]
    assert sorted(lus) == sorted([_id("x-1"), _id("x-2"), _id("x-3")])   # une par travail
    e = table["store"].ecrites
    assert e["r1"]["phone"] == "+33612345678" and e["r1"]["phone_status"] == "found"
    assert e["d1"]["phone"] == "+33612345678"              # le doublon aussi
    assert e["r1"]["phone_status_job"] is None              # relève consommée
    assert out["found"] == 3 and out["still_pending"] == 1
    assert e["r2"]["phone_status"] == "pending"


def test_un_travail_sans_mobile_poursuit_la_cascade_dans_le_meme_appel(calls, table):
    _soumettre(calls, table, 1)
    calls["answers"]["pipecorn"] = _releve({_id("x-1"): []})
    calls["answers"]["kaspr_enrich_linkedin"] = IssueCible(
        ok=True, result={"profile": {"phone": "+33 6 11 22 33 44"}})
    out = _act(op="collect_rows", filter={}, **PHONE)
    e = table["store"].ecrites["r1"]
    assert e["phone_status"] == "found" and e["phone_source"] == "kaspr"
    assert out["found"] == 1


def test_une_poursuite_qui_atteint_une_autre_asynchrone_soumet_a_nouveau(calls, table):
    _soumettre(calls, table, 1, providers=["pipecorn", "lemlist"])
    calls["answers"]["pipecorn"] = _releve({_id("x-1"): []})
    calls["answers"]["lemlist_enrich"] = IssueCible(ok=True, result={"enrichment_id": "enr_9"})
    out = _act(op="collect_rows", filter={}, providers=["pipecorn", "lemlist"], **PHONE)
    e = table["store"].ecrites["r1"]
    assert e["phone_status"] == "pending" and e["phone_status.comment"] == "lemlist"
    assert e["phone_status_job"]["provider"] == "lemlist"
    assert out["submitted"] == 1


def test_sans_trouvaille_et_sans_suite_no_match(calls, table):
    _soumettre(calls, table, 1, providers=["pipecorn"])
    calls["answers"]["pipecorn"] = _releve({_id("x-1"): []})
    out = _act(op="collect_rows", filter={}, providers=["pipecorn"], **PHONE)
    assert table["store"].ecrites["r1"]["phone_status"] == "no_match"
    assert out["no_match"] == 1


def test_un_travail_echoue_remet_la_ligne_en_file(calls, table):
    _soumettre(calls, table, 1)
    calls["answers"]["pipecorn"] = _releve({_id("x-1"): IssueCible(
        ok=False, code="invalid_params", retryable=False, message="balance ran out")})
    out = _act(op="collect_rows", filter={}, **PHONE)
    e = table["store"].ecrites["r1"]
    assert e["phone_status"] == "" and e["phone_status_job"] is None
    assert {"row_id": "r1", "code": "collect_invalid_params"} in out["errors"]


def test_un_travail_echoue_remet_toutes_les_lignes_de_son_groupe(calls, table):
    _soumettre(calls, table, 1, doublons=1)
    calls["answers"]["pipecorn"] = _releve({_id("x-1"): IssueCible(
        ok=False, code="invalid_params", retryable=False, message="failed")})
    _act(op="collect_rows", filter={}, **PHONE)
    for rid in ("r1", "d1"):
        e = table["store"].ecrites[rid]
        assert e["phone_status"] == "" and e["phone_status_job"] is None


def test_un_travail_jamais_fini_revient_en_file_apres_le_plafond(calls, table, monkeypatch):
    from oto_mcp.tools import waterfall_releve as wr
    _soumettre(calls, table, 1)
    calls["answers"]["pipecorn"] = _releve({})          # jamais fini
    out = _act(op="collect_rows", filter={}, **PHONE)
    assert out["still_pending"] == 1                    # avant le plafond : attend
    monkeypatch.setattr(wr, "_maintenant", lambda: time.time() + wr.ATTENTE_MAX_S + 1)
    out = _act(op="collect_rows", filter={}, **PHONE)
    e = table["store"].ecrites["r1"]
    assert e["phone_status"] == "" and e["phone_status_job"] is None
    assert {"row_id": "r1", "code": "collect_timeout"} in out["errors"]


def test_un_echec_passager_laisse_la_ligne_en_attente(calls, table):
    _soumettre(calls, table, 1)
    calls["answers"]["pipecorn"] = _releve({_id("x-1"): IssueCible(
        ok=False, code="upstream_unavailable", retryable=True, message="503")})
    _act(op="collect_rows", filter={}, **PHONE)
    assert table["store"].ecrites["r1"]["phone_status"] == "pending"


def test_la_releve_de_200_lignes_tient_dans_un_appel(calls, table):
    _soumettre(calls, table, 200, batch_size=200)
    calls["answers"]["pipecorn"] = _releve({
        _id(f"x-{i}"): [{"number": f"+3361234{i:04d}", "type": "mobile"}]
        for i in range(1, 201)})
    debut = time.monotonic()
    out = _act(op="collect_rows", filter={}, batch_size=200, **PHONE)
    assert out["found"] == 200 and out["stopped"] is None
    assert time.monotonic() - debut < 10


def test_dry_run_compte_les_travaux_sans_rien_lire(calls, table):
    _soumettre(calls, table, 3, doublons=2)
    calls["made"].clear()
    out = _act(op="collect_rows", filter={}, dry_run=True, **PHONE)
    assert out["would_collect"] == 3 and out["jobs"] == 3 and calls["made"] == []


def test_asynchronous_n_appartient_qu_a_update_rows(calls, table):
    with pytest.raises(Exception, match="belongs to update_rows"):
        _act(op="collect_rows", filter={}, asynchronous=True, **PHONE)


def test_une_ligne_sans_releve_est_nommee(calls, table):
    table["lignes"] = [{"_id": "r1", "linkedin": "https://www.linkedin.com/in/jane-1/",
                        "phone_status": "pending"}]
    out = _act(op="collect_rows", filter={}, **PHONE)
    assert {"row_id": "r1", "code": "no_job"} in out["errors"]
