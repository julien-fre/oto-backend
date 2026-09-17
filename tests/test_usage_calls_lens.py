"""`org.usage.calls` — la lentille MEMBRE du relevé de consommation.

Ce qu'elle doit tenir, et pourquoi c'est une lentille à part :

1. **Étroite** : `call_id`, `tool`, `created_at`, `quantity`, `key_mode` — jamais
   `sub`, `email` ni `error`. Un membre lit ce que son org consomme, pas qui a
   fait quoi ; ouvrir `org.monitoring.calls` aux membres aurait donné ça en effet
   de bord d'une page de facturation.
2. **Complète et vérifiable** : même contrat que l'export d'audit (#770) —
   `total` de la FENÊTRE, curseur keyset à la microseconde, borne haute gelée.
   La première version de cette lentille reposait sur `list_tool_calls`, qui
   plafonne à 1000 EN SILENCE et sans curseur : une page tronquée y a l'air
   complète et sous-facture sans erreur. Un client à 3× le volume de l'org 196
   sur `linkedin_aiark_person` (334/mois) y perdait des lignes. Plus jamais.
3. **Filtrée par outil et/ou par run, et par succès** : un échec n'a rien consommé
   chez le fournisseur ; le relevé se lit outil par outil, ou run par run (ce qu'UN
   agent hébergé a consommé), jamais sur tout le journal.
"""
from __future__ import annotations

import os
import uuid

import pytest

from oto_mcp.capabilities import org_monitoring as om
from oto_mcp.capabilities._types import ResolvedCtx

CTX = ResolvedCtx(sub="membre", org_id=7)


# ── La forme servie (store simulé) ─────────────────────────────────────────────

def _fake(monkeypatch, *, calls, total, suivant=None, until="2026-09-01T10:00:00.0Z"):
    vu: dict = {}

    def faux(org_id, tool, **kw):
        vu.update(org_id=org_id, tool=tool, **kw)
        return {"until_effectif": until, "total": total,
                "calls": [dict(c) for c in calls], "next": suivant}

    monkeypatch.setattr(om.db, "list_billable_calls_for_org", faux)
    return vu


def test_la_lentille_ne_rend_QUE_ce_qu_un_metrage_somme(monkeypatch):
    """Aucune identité, aucun texte d'erreur — même si le store en rendait."""
    _fake(monkeypatch, total=1, calls=[{
        "id": 9, "tool": "linkedin_aiark_search", "created_at": "2026-09-01T09:00:00.000000Z",
        "quantity": 47, "key_mode": "platform",
        # ce que le store pourrait laisser fuiter, et que la projection doit ignorer
        "sub": "acme:abc", "email": "x@y.z", "error": "boom"}])
    out = om._billable_calls(CTX, om.OrgBillableCallsInput(org_id=7, tool="linkedin_aiark_search"))
    assert out["calls"] == [{"call_id": 9, "tool": "linkedin_aiark_search",
                             "created_at": "2026-09-01T09:00:00.000000Z",
                             "quantity": 47, "key_mode": "platform", "job_id": None,
                             "found": None, "run_id": None}]
    assert not {"sub", "email", "error"} & set(out["calls"][0])


def test_la_lentille_rend_le_job_d_un_releve_et_rien_d_autre_des_args(monkeypatch):
    """`job_id` sert au consommateur à compter UNE fois un job relevé plusieurs fois ;
    `found` porte ce que le job a trouvé, en contacts par sorte. Aucun autre argument
    ne passe, même si le store en laissait remonter."""
    trouve = {"work_emails": 2, "personal_emails": 1, "phones": 2}
    _fake(monkeypatch, total=1, calls=[{
        "id": 11, "tool": "fullenrich_result", "created_at": "2026-09-01T09:05:00.000000Z",
        "quantity": 14, "key_mode": "platform", "job_id": "enr-0001", "found": trouve,
        "args": {"enrichment_id": "enr-0001"}, "contacts": [{"first_name": "A"}],
        "found_phones": "2"}])
    out = om._billable_calls(CTX, om.OrgBillableCallsInput(org_id=7, tool="fullenrich_result"))
    assert out["calls"] == [{"call_id": 11, "tool": "fullenrich_result",
                             "created_at": "2026-09-01T09:05:00.000000Z",
                             "quantity": 14, "key_mode": "platform", "job_id": "enr-0001",
                             "found": trouve, "run_id": None}]
    assert not {"args", "contacts", "found_phones"} & set(out["calls"][0])


@pytest.mark.parametrize("colonnes, attendu", [
    ({"found_work_emails": "2", "found_personal_emails": "0", "found_phones": "1"},
     {"work_emails": 2, "personal_emails": 0, "phones": 1}),
    ({}, None),                                                        # autre outil / non terminé
    ({"found_work_emails": "2", "found_personal_emails": None, "found_phones": "1"}, None),
    ({"found_work_emails": "2", "found_personal_emails": "-1", "found_phones": "1"}, None),
    ({"found_work_emails": "x", "found_personal_emails": "0", "found_phones": "1"}, None),
])
def test_found_est_tout_ou_rien_et_retire_ses_colonnes_de_la_ligne(colonnes, attendu):
    from oto_mcp.db import usage as dbu

    ligne = {"id": 1, "tool": "fullenrich_result", **colonnes}
    assert dbu._found_from_row(ligne) == attendu
    assert not set(dbu.BILLABLE_FOUND_ARGS.values()) & set(ligne)


def test_la_reponse_porte_le_total_et_la_position_suivante(monkeypatch):
    _fake(monkeypatch, total=4485, calls=[{"id": 1, "tool": "t", "created_at": "a"}],
          suivant=("2026-08-30T09:00:00.000000Z", 91))
    out = om._billable_calls(CTX, om.OrgBillableCallsInput(org_id=7, tool="t"))
    assert out["total"] == 4485
    assert (out["next_at"], out["next_id"]) == ("2026-08-30T09:00:00.000000Z", 91)
    assert out["until_effectif"] == "2026-09-01T10:00:00.0Z"


def test_le_curseur_et_la_fenetre_sont_transmis_tels_quels(monkeypatch):
    vu = _fake(monkeypatch, total=0, calls=[])
    om._billable_calls(CTX, om.OrgBillableCallsInput(
        org_id=7, tool="t", since="2026-09-01T00:00:00Z", until="2026-09-09T00:00:00.0Z",
        before_at="2026-09-05T00:00:00.000000Z", before_id=12, limit=250))
    assert vu["since"] == "2026-09-01T00:00:00Z"
    assert vu["until"] == "2026-09-09T00:00:00.0Z"
    assert vu["before"] == ("2026-09-05T00:00:00.000000Z", 12)
    assert vu["limit"] == 250


def test_l_outil_ou_le_run_est_obligatoire():
    """Le relevé se lit par outil ou par run — c'est ce qui borne la fenêtre."""
    with pytest.raises(Exception):
        om.OrgBillableCallsInput(org_id=7)
    om.OrgBillableCallsInput(org_id=7, tool="t")
    om.OrgBillableCallsInput(org_id=7, run_id="r-1")


def test_plusieurs_runs_en_une_lecture_et_une_borne():
    """La forme répétée de la query string arrive en liste, la forme à virgule en
    chaîne : les deux donnent la même liste, dédoublonnée. Au-delà de la borne,
    refus — jamais une troncature silencieuse."""
    assert om.OrgBillableCallsInput(org_id=7, run_id=["a", "b", "a"]).run_id == ["a", "b"]
    assert om.OrgBillableCallsInput(org_id=7, run_id="a, b").run_id == ["a", "b"]
    with pytest.raises(Exception):
        om.OrgBillableCallsInput(org_id=7, run_id=[f"r{i}" for i in range(om.MAX_BILLABLE_RUNS + 1)])
    with pytest.raises(Exception):
        om.OrgBillableCallsInput(org_id=7, run_id=" , ")


def test_le_run_est_transmis_et_rendu_sur_chaque_appel(monkeypatch):
    """Ce qu'UN run a consommé : le filtre descend au store, et chaque ligne dit
    de quel run elle relève."""
    vu = _fake(monkeypatch, total=1, calls=[{
        "id": 12, "tool": "linkedin_aiark_person", "created_at": "a",
        "quantity": 1, "key_mode": "tenant", "run_id": "r-1"}])
    out = om._billable_calls(CTX, om.OrgBillableCallsInput(org_id=7, run_id="r-1"))
    assert vu["tool"] is None and vu["run_ids"] == ["r-1"]
    assert out["calls"][0]["run_id"] == "r-1"


# ── Le store, contre un vrai PostgreSQL ────────────────────────────────────────


def _poser(sub, org_id, *, quand, tool="linkedin_aiark_search", ok=True,
           quantity=None, key_mode=None, kind="mcp", args=None, run_id=None):
    from oto_mcp import db
    from oto_mcp.db._conn import _connect

    db.insert_tool_call({"sub": sub, "kind": kind, "tool": tool, "ok": ok,
                         "org_id": org_id, "duration_ms": 3, "args": args,
                         "quantity": quantity, "key_mode": key_mode, "run_id": run_id})
    with _connect() as conn:
        conn.execute(
            "UPDATE tool_calls SET created_at = %s::timestamptz WHERE id = ("
            "SELECT max(id) FROM tool_calls)", (quand,))


def test_le_store_rend_le_job_d_un_releve_sans_aucun_autre_argument(live):
    """Contre la base : `job_id` vient des args JOURNALISÉS (tels que
    `calllog.truncated_args` les écrit), pour la seule liste fermée
    `BILLABLE_JOB_ARGS` — le reste des args ne sort pas, et un outil sans job
    rend `None`."""
    from oto_mcp import db, org_store, server
    from oto_mcp.calllog import apply_call_trace, truncated_args

    sub = "sub-job-" + uuid.uuid4().hex[:6]
    org = org_store.create_org("Jobs relevés", created_by=sub)
    # La ligne telle que le sink l'écrit : args tronqués, puis le relevé versé par la
    # liste fermée `server._TRACED_ARGS`.
    args = apply_call_trace(
        {"args": truncated_args({"enrichment_id": "8f14e45f-ceea-467e-a9b4-2f0e0f8b1c7d"},
                                tool="fullenrich_result")},
        {"quantity": 14, "found_work_emails": 2, "found_personal_emails": 0,
         "found_phones": 1},
        server._TRACED_ARGS)["args"]
    for minute in (1, 2):                          # le MÊME job relevé deux fois
        _poser(sub, org, quand=f"2026-08-20T10:0{minute}:00+00:00",
               tool="fullenrich_result", quantity=14, key_mode="platform", args=args)
    _poser(sub, org, quand="2026-08-20T10:03:00+00:00", tool="fullenrich_enrich_linkedin",
           quantity=3, key_mode="platform",
           args=truncated_args({"contacts": [{"first_name": "Ada", "last_name": "L"}]},
                               tool="fullenrich_enrich_linkedin"))

    fenetre = {"since": "2026-08-10T00:00:00+00:00", "until": "2026-08-21T00:00:00+00:00"}
    releves = db.list_billable_calls_for_org(org, "fullenrich_result", **fenetre)
    assert releves["total"] == 2
    assert {c["job_id"] for c in releves["calls"]} == {"8f14e45f-ceea-467e-a9b4-2f0e0f8b1c7d"}
    assert [c["found"] for c in releves["calls"]] == [
        {"work_emails": 2, "personal_emails": 0, "phones": 1}] * 2
    assert not {"args", "contacts", "found_work_emails", "found_phones"} & set(releves["calls"][0])

    soumis = db.list_billable_calls_for_org(org, "fullenrich_enrich_linkedin", **fenetre)
    assert [c["job_id"] for c in soumis["calls"]] == [None]
    assert [c["found"] for c in soumis["calls"]] == [None]
    assert "contacts" not in soumis["calls"][0] and "args" not in soumis["calls"][0]


@pytest.fixture
def journal(live):
    """Sept appels facturables de l'outil dans la fenêtre — dont trois à la MÊME
    seconde — plus tout ce que la lentille doit EXCLURE : un autre outil, un
    échec, une autre org, un `kind='rest'`, hors fenêtre haute et basse."""
    from oto_mcp import org_store

    sub = "sub-uc-" + uuid.uuid4().hex[:6]
    org = org_store.create_org("Usage calls", created_by=sub)
    autre = org_store.create_org("Ailleurs", created_by=sub)

    for i in range(4):
        _poser(sub, org, quand=f"2026-08-20T10:{i:02d}:00+00:00",
               quantity=10 + i, key_mode="platform")
    for micro in (100000, 200000, 300000):        # même seconde : piège du curseur
        _poser(sub, org, quand=f"2026-08-20T11:00:00.{micro:06d}+00:00",
               quantity=1, key_mode="org")

    _poser(sub, org, quand="2026-08-20T10:30:00+00:00", tool="fullenrich_enrich_linkedin")
    _poser(sub, org, quand="2026-08-20T10:31:00+00:00", ok=False)      # échec
    _poser(sub, autre, quand="2026-08-20T10:32:00+00:00")              # autre org
    _poser(sub, org, quand="2026-08-20T10:33:00+00:00", kind="rest")   # pas un outil
    _poser(sub, org, quand="2026-08-25T10:00:00+00:00")                # hors fenêtre haute
    _poser(sub, org, quand="2026-08-01T10:00:00+00:00")                # hors fenêtre basse
    return {"org": org, "since": "2026-08-10T00:00:00+00:00",
            "until": "2026-08-21T00:00:00+00:00"}


def test_le_total_et_la_page_decrivent_le_MEME_jeu(journal):
    from oto_mcp import db

    p = db.list_billable_calls_for_org(journal["org"], "linkedin_aiark_search",
                                       since=journal["since"], until=journal["until"])
    assert p["total"] == len(p["calls"]) == 7 and p["next"] is None
    # les colonnes du métrage voyagent, à la valeur près
    assert sorted(c["quantity"] for c in p["calls"]) == [1, 1, 1, 10, 11, 12, 13]
    assert {c["key_mode"] for c in p["calls"]} == {"platform", "org"}
    assert not {"sub", "email", "error"} & set(p["calls"][0])


def test_le_curseur_parcourt_toute_la_fenetre_sans_trou_ni_doublon(journal):
    """Y compris les trois lignes de la même seconde — un curseur bâti sur un
    horodatage tronqué à la seconde en sauterait deux."""
    from oto_mcp import db

    vus, before, total = [], None, None
    for _ in range(20):
        p = db.list_billable_calls_for_org(journal["org"], "linkedin_aiark_search",
                                           since=journal["since"], until=journal["until"],
                                           limit=2, before=before)
        total = p["total"] if total is None else total
        assert p["total"] == total, "le total ne bouge pas d'une page à l'autre"
        vus += [c["id"] for c in p["calls"]]
        before = p["next"]
        if before is None:
            break
    assert len(vus) == len(set(vus)) == total == 7, vus


def test_la_borne_haute_est_gelee_quand_elle_est_omise(journal):
    from oto_mcp import db

    p = db.list_billable_calls_for_org(journal["org"], "linkedin_aiark_search",
                                       since=journal["since"], limit=3)
    assert p["until_effectif"].endswith("Z")
    assert p["total"] == 8     # les 7 + celui du 25/08, avant l'instant gelé


def test_le_store_rend_ce_qu_UN_run_a_consomme_tous_outils_confondus(live):
    """Filtre run : tous les outils du run, sous l'org seulement, succès seulement —
    et combinable avec l'outil."""
    from oto_mcp import db, org_store

    sub = "sub-run-" + uuid.uuid4().hex[:6]
    org = org_store.create_org("Run credits", created_by=sub)
    autre = org_store.create_org("Autre org", created_by=sub)
    run, voisin = "run-" + uuid.uuid4().hex[:8], "run-" + uuid.uuid4().hex[:8]

    _poser(sub, org, quand="2026-08-20T10:00:00+00:00", tool="linkedin_aiark_search",
           quantity=25, key_mode="tenant", run_id=run)
    _poser(sub, org, quand="2026-08-20T10:01:00+00:00", tool="fullenrich_result",
           quantity=3, key_mode="platform", run_id=run)
    _poser(sub, org, quand="2026-08-20T10:02:00+00:00", tool="serper_search",
           ok=False, run_id=run)                                        # échec
    _poser(sub, org, quand="2026-08-20T10:03:00+00:00", run_id=voisin)  # autre run
    _poser(sub, org, quand="2026-08-20T10:04:00+00:00")                 # hors run
    _poser(sub, autre, quand="2026-08-20T10:05:00+00:00", run_id=run)   # autre org

    fenetre = {"since": "2026-08-10T00:00:00+00:00", "until": "2026-08-21T00:00:00+00:00"}
    p = db.list_billable_calls_for_org(org, run_ids=[run], **fenetre)
    assert p["total"] == len(p["calls"]) == 2 and p["next"] is None
    assert {c["tool"] for c in p["calls"]} == {"linkedin_aiark_search", "fullenrich_result"}
    assert {c["run_id"] for c in p["calls"]} == {run}

    seul = db.list_billable_calls_for_org(org, "fullenrich_result", run_ids=[run], **fenetre)
    assert [c["tool"] for c in seul["calls"]] == ["fullenrich_result"]

    deux = db.list_billable_calls_for_org(org, run_ids=[run, voisin], **fenetre)
    assert deux["total"] == 3 and {c["run_id"] for c in deux["calls"]} == {run, voisin}

    hors = db.list_billable_calls_for_org(org, "linkedin_aiark_search", **fenetre)
    assert {c["run_id"] for c in hors["calls"]} == {run, voisin, None}

    with pytest.raises(ValueError):
        db.list_billable_calls_for_org(org, **fenetre)

