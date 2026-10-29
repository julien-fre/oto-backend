"""Le passage asynchrone d'un waterfall sur un VRAI tableau, à l'échelle : 150 contacts
soumis à Pipecorn en un appel (`update_rows(asynchronous=true)`), relevés en un appel
(`collect_rows`). Les fournisseurs sont simulés à leur frontière (`meta.executer_cible`),
le store est réel : la colonne de relève (json), son effacement, la sélection des lignes
`pending`, le compte de ce qui reste."""
from __future__ import annotations

import asyncio
import time
import uuid

import pytest

# Importés AVANT tout patch du lecteur d'identité (#1111).
import oto_mcp.call_axes  # noqa: F401,E402
import oto_mcp.capabilities._mcp_adapter  # noqa: F401,E402
import oto_mcp.tools.meta  # noqa: F401,E402
import oto_mcp.tools.waterfall_lignes  # noqa: F401,E402
import oto_mcp.tools.waterfall_releve  # noqa: F401,E402
from oto_mcp.tools.meta import IssueCible

from _waterfall_faux import calls  # noqa: F401 — fournisseurs simulés

SUB = "sub-waterfall-releve"
N = 150


@pytest.fixture(scope="module")
def compte(live):
    from oto_mcp import db
    db.upsert_user(SUB, email=f"{SUB}@acme.test", name=SUB)
    return SUB


@pytest.fixture
def tableau(compte, monkeypatch):
    from oto_mcp import access, db
    from oto_mcp.auth import hooks
    from oto_mcp.datastore.core import make_store
    monkeypatch.setattr(access, "current_user_sub_or_raise", lambda: SUB)
    monkeypatch.setattr(hooks, "current_user_sub_from_token", lambda: SUB)
    nom = f"wr-{uuid.uuid4().hex[:6]}"
    ns = db.create_datastore("user", SUB, nom)
    store = make_store(SUB)
    store.set_schema(nom, {"fields": [{"key": "linkedin", "type": "text"}]})
    store.write_rows(nom, [{"linkedin": f"https://www.linkedin.com/in/jane-{i}/"}
                           for i in range(1, N + 1)])
    return ns, nom


def _id(url: str) -> str:
    return f"00000000-0000-4000-8000-{int(url.rstrip('/').rsplit('-', 1)[1]):012d}"


def _act(ns, **kw):
    from fastmcp import FastMCP

    from oto_mcp.tools import waterfall_lignes
    m = FastMCP("t")
    waterfall_lignes.register(m)
    return asyncio.run(asyncio.run(m.get_tool("waterfall_phone_act")).fn(
        datastore=str(ns), inputs={"linkedin_url": "linkedin"},
        output={"phone": "phone", "provider": "phone_source"},
        status_column="phone_status", filter={}, batch_size=200, **kw))


def _lignes(nom) -> list[dict]:
    from oto_mcp.datastore.core import make_store
    return make_store(SUB).cursor_rows(nom, limit=500)["rows"]


def test_150_contacts_soumis_puis_releves_en_deux_appels(calls, tableau):
    ns, nom = tableau
    calls["answers"]["pipecorn"] = lambda a: IssueCible(ok=True, result=(
        {"enrichment_id": _id(a["linkedin_url"]), "status": "pending", "find": ["phone"]}
        if a["op"] == "enrich" else
        # Un sur trois sans mobile (puis rien chez Kaspr), un sur dix pas encore fini.
        {"done": False} if int(a["enrichment_id"][-12:]) % 10 == 0 else
        {"done": True, "status": "finished", "phone_details": [] if int(
            a["enrichment_id"][-12:]) % 3 == 0 else [{"number": "+33612345678",
                                                      "type": "mobile"}]}))
    calls["answers"]["kaspr_enrich_linkedin"] = IssueCible(ok=True, result={})

    debut = time.monotonic()
    out = _act(ns, op="update_rows", asynchronous=True)
    assert out["submitted"] == N and out["remaining"] == 0, out
    lignes = _lignes(nom)
    assert {l["phone_status"] for l in lignes} == {"pending"}
    assert all(l["phone_status_job"]["provider"] == "pipecorn" for l in lignes)

    out = _act(ns, op="collect_rows")
    pas_finis = N // 10
    sans = len([i for i in range(1, N + 1) if i % 3 == 0 and i % 10])
    assert out["still_pending"] == pas_finis and out["remaining"] == pas_finis, out
    assert out["found"] == N - pas_finis - sans and out["no_match"] == sans
    assert time.monotonic() - debut < 30
    par_etat: dict = {}
    for l in _lignes(nom):
        par_etat.setdefault(l["phone_status"], []).append(l)
    assert len(par_etat["found"]) == N - pas_finis - sans
    assert all(not l.get("phone_status_job") for l in par_etat["found"])   # consommée
    assert all(l["phone"] == "+33612345678" for l in par_etat["found"])

    # Les pas-finis aboutissent à la relève suivante ; rien n'est relu deux fois.
    calls["answers"]["pipecorn"] = lambda a: IssueCible(ok=True, result={
        "done": True, "status": "finished",
        "phone_details": [{"number": "+33600000000", "type": "mobile"}]})
    calls["made"].clear()
    out = _act(ns, op="collect_rows")
    assert out["found"] == pas_finis and out["remaining"] == 0
    assert len([n for n, _ in calls["made"] if n == "pipecorn"]) == pas_finis


def test_un_travail_echoue_revient_en_file_sur_le_vrai_store(calls, tableau):
    ns, nom = tableau
    calls["answers"]["pipecorn"] = lambda a: (
        IssueCible(ok=True, result={"enrichment_id": _id(a["linkedin_url"]),
                                    "status": "pending"})
        if a["op"] == "enrich" else
        IssueCible(ok=False, code="invalid_params", retryable=False, message="failed"))
    _act(ns, op="update_rows", asynchronous=True, providers=["pipecorn"])
    out = _act(ns, op="collect_rows", providers=["pipecorn"])
    assert out["remaining"] == 0
    lignes = _lignes(nom)
    assert all(not l.get("phone_status") and not l.get("phone_status_job") for l in lignes)
    # Remises en file : un nouvel envoi les reprend.
    out = _act(ns, op="update_rows", asynchronous=True, providers=["pipecorn"])
    assert out["submitted"] == N
