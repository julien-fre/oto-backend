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
3. **Filtrée par outil et par succès** : un échec n'a rien consommé chez le
   fournisseur, et le relevé se lit outil par outil.
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
        "sub": "tulina:abc", "email": "x@y.z", "error": "boom"}])
    out = om._billable_calls(CTX, om.OrgBillableCallsInput(org_id=7, tool="linkedin_aiark_search"))
    assert out["calls"] == [{"call_id": 9, "tool": "linkedin_aiark_search",
                             "created_at": "2026-09-01T09:00:00.000000Z",
                             "quantity": 47, "key_mode": "platform"}]
    assert not {"sub", "email", "error"} & set(out["calls"][0])


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


def test_l_outil_est_obligatoire():
    """Le relevé se lit outil par outil — c'est ce qui borne la fenêtre."""
    with pytest.raises(Exception):
        om.OrgBillableCallsInput(org_id=7)  # type: ignore[call-arg]


# ── Le store, contre un vrai PostgreSQL ────────────────────────────────────────

@pytest.fixture(scope="module")
def live(pg_dsn):
    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    nom = "oto_usagecalls_" + uuid.uuid4().hex[:8]
    racine = psycopg.connect(pg_dsn, autocommit=True)
    racine.execute(f'CREATE DATABASE "{nom}"')
    dsn = pg_dsn.rsplit("/", 1)[0] + "/" + nom
    url_avant, pool_avant = os.environ.get("DATABASE_URL"), dbconn._pool
    os.environ["DATABASE_URL"] = dsn
    dbconn._pool = None
    try:
        from oto_mcp.db import init_db
        init_db()
        yield
    finally:
        if dbconn._pool is not None:
            dbconn._pool.close()
        dbconn._pool = pool_avant
        if url_avant is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = url_avant
        racine.execute(f'DROP DATABASE IF EXISTS "{nom}" WITH (FORCE)')
        racine.close()


def _poser(sub, org_id, *, quand, tool="linkedin_aiark_search", ok=True,
           quantity=None, key_mode=None, kind="mcp", run_id=None):
    from oto_mcp import db
    from oto_mcp.db._conn import _connect

    db.insert_tool_call({"sub": sub, "kind": kind, "tool": tool, "ok": ok,
                         "org_id": org_id, "duration_ms": 3, "run_id": run_id,
                         "quantity": quantity, "key_mode": key_mode})
    with _connect() as conn:
        conn.execute(
            "UPDATE tool_calls SET created_at = %s::timestamptz WHERE id = ("
            "SELECT max(id) FROM tool_calls)", (quand,))


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


# ── L'org EFFECTIVE : un appel de run résolu ailleurs ──────────────────────────

@pytest.fixture
def runs_ailleurs(live):
    """Un agent déroule le run de l'org CLIENTE sans poser `_org` : ses appels
    retombent sur son org MAISON. Plus deux cas qui ne doivent PAS migrer : un
    `_run_id` emprunté par un autre `sub`, et un run qui ne porte aucune org."""
    from oto_mcp import db, org_store

    agent = "sub-run-" + uuid.uuid4().hex[:6]
    intrus = "sub-intrus-" + uuid.uuid4().hex[:6]
    cliente = org_store.create_org("Cliente", created_by=agent)
    maison = org_store.create_org("Maison", created_by=agent)

    run = "run-" + uuid.uuid4().hex
    db.insert_run(run, sub=agent, org_id=cliente, label="sourcing")
    sans_org = "run-" + uuid.uuid4().hex
    db.insert_run(sans_org, sub=agent, org_id=None, label="ad hoc")

    q = "2026-08-20T10:{:02d}:00+00:00"
    _poser(agent, maison, quand=q.format(1), run_id=run, quantity=40, key_mode="platform")
    _poser(agent, maison, quand=q.format(2), run_id=run, quantity=2, key_mode="platform")
    _poser(agent, cliente, quand=q.format(3), run_id=run, quantity=5, key_mode="platform")
    _poser(intrus, maison, quand=q.format(4), run_id=run, quantity=9)   # run d'autrui
    _poser(agent, maison, quand=q.format(5), run_id=sans_org, quantity=7)
    _poser(agent, maison, quand=q.format(6), quantity=1)                # hors run
    return {"cliente": cliente, "maison": maison,
            "since": "2026-08-20T00:00:00+00:00", "until": "2026-08-21T00:00:00+00:00"}


def _quantites(org, w):
    from oto_mcp import db

    p = db.list_billable_calls_for_org(org, "linkedin_aiark_search",
                                       since=w["since"], until=w["until"])
    assert p["total"] == len(p["calls"]), "le total et la page décrivent le même jeu"
    return sorted(c["quantity"] for c in p["calls"])


def test_un_appel_de_run_resolu_ailleurs_est_facture_a_l_org_du_run(runs_ailleurs):
    assert _quantites(runs_ailleurs["cliente"], runs_ailleurs) == [2, 5, 40]


def test_il_quitte_l_org_maison_un_appel_ne_compte_jamais_deux_fois(runs_ailleurs):
    """Restent à la maison : l'appel hors run, celui d'un run SANS org, et celui
    qui a emprunté le `_run_id` d'autrui — `_run_id` est déclaré par l'appelant,
    le poser ne doit pas suffire à faire payer une autre org."""
    assert _quantites(runs_ailleurs["maison"], runs_ailleurs) == [1, 7, 9]


def test_l_export_d_audit_garde_l_org_d_EMISSION(runs_ailleurs):
    """Le relevé change de périmètre, l'audit non : il dit toujours sous quelle
    org un appel a été émis."""
    from oto_mcp import db

    w = runs_ailleurs
    audit = db.export_tool_calls_for_org(w["maison"], since=w["since"], until=w["until"])
    assert audit["total"] == 5
