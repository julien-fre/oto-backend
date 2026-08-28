"""Refermer un déroulé dont on a perdu l'identifiant (signal #473).

Le manque, tel qu'il a été vécu le 16/08 : le bloc de contexte annonce bien « derniers
déroulés » — mais par leur INTITULÉ, jamais par leur `run_id`. Or `run_finish` n'accepte
qu'un `run_id`. Un agent qui reprend une session, ou qui a simplement perdu le fil, ne
peut donc plus clore ce qu'il a ouvert : **le déroulé reste « en cours » pour toujours**.

Et ce n'est pas un cas rare. Le module `run_status` le mesure : en production, 15 des 16
runs affichés « en cours » n'avaient plus donné signe de vie depuis 1 jour à 1 mois. Un
run muet est d'abord un run que personne ne pouvait fermer.

Trois portes existaient, aucune n'ouvrait :

- `oto_context` — l'intitulé et l'état, pas l'identifiant ;
- `oto_project op=runs` — exige un `project_id`, or un run ouvert HORS projet n'est
  rattaché à aucun ; il n'est énumérable nulle part ;
- `oto_org_monitoring op=runs` — porte bien le `run_id`, mais c'est une lentille
  d'ADMINISTRATEUR D'ORG. Un membre ordinaire ne l'a pas, et c'est très bien ainsi :
  elle montre les déroulés de TOUT LE MONDE.

D'où la quatrième, ici : `oto_project op=runs` **sans** `project_id` rend MES déroulés
ouverts, avec leur `run_id`. Deux propriétés portent tout le lot :

1. **le scope de propriété est dur** — on ne liste que ce qu'on aurait le droit de
   clore (`sub`), donc lister n'ouvre aucun accès qui n'existait pas ;
2. **le scope d'ORG, lui, est volontairement absent** — un run s'ouvre dans l'org
   active et l'agent en change en cours de route ; borner à l'org courante rendrait
   invisible exactement le run qu'on ne retrouve plus.
"""
from __future__ import annotations

import pytest

from oto_mcp.capabilities import projects as P
from oto_mcp.capabilities._types import AuthzDenied, ResolvedCtx

CTX = ResolvedCtx(sub="u1", org_id=7)

# Un run ouvert dans une AUTRE org que l'org active, et sans projet : le cas exact du
# signal, celui qu'aucune surface n'atteignait.
RUNS = [
    {"run_id": "r-ancien", "label": "Prospect KB: OQY Labs", "doctrine": "prospect-kb",
     "doctrine_version": 2, "org_id": 3, "project_id": None,
     "started_at": "2026-08-10 09:00:00", "finished_at": None, "outcome": None,
     "last_seen_at": "2026-08-10 09:30:00"},
    {"run_id": "r-recent", "label": "Campagne Audiens", "doctrine": None,
     "doctrine_version": None, "org_id": 7, "project_id": 152,
     "started_at": "2026-08-16 08:00:00", "finished_at": None, "outcome": None,
     "last_seen_at": "2026-08-16 08:05:00"},
]


@pytest.fixture
def journal(monkeypatch):
    vus: dict = {}

    def _my_runs(sub, limit=20, *, open_only=False):
        vus.update(sub=sub, limit=limit, open_only=open_only)
        return [dict(r) for r in RUNS]

    monkeypatch.setattr(P.db, "my_runs", _my_runs)
    return vus


# ── Ce que le signal demande mot pour mot ────────────────────────────────────────────

def test_sans_projet_je_recupere_MES_deroules_avec_leur_identifiant(journal):
    """« op=list for runs at org scope, returning run_id » — la phrase du signal."""
    out = P._project(CTX, P.ProjectInput(op="runs"))
    assert [r["run_id"] for r in out["runs"]] == ["r-ancien", "r-recent"]
    # Et l'identifiant est bien celui que `run_finish` réclame : rien d'autre à faire
    # que de le lui repasser.
    assert all(r.get("run_id") for r in out["runs"])


def test_la_liste_dit_de_QUI_elle_parle(journal):
    """Sans ce mot, la réponse se lit « les déroulés du projet » et un agent conclut
    qu'il n'y en a pas ailleurs. La portée est un fait de la réponse, pas une
    convention à retenir."""
    out = P._project(CTX, P.ProjectInput(op="runs"))
    assert out["scope"] == "mine"
    assert out["open_only"] is True


def test_seuls_les_deroules_OUVERTS_remontent(journal):
    """C'est la question posée — « qu'est-ce qu'il me reste à refermer ? » —, pas
    « qu'ai-je fait récemment ». Un historique complet noierait les deux runs à clore
    sous vingt runs déjà clos."""
    P._project(CTX, P.ProjectInput(op="runs"))
    assert journal["open_only"] is True


def test_le_scope_est_MOI_et_pas_mon_org(journal):
    """Les deux moitiés de la règle, sur le même appel : la propriété borne (on ne
    liste que ce qu'on pourrait clore), l'org NON (le run perdu est souvent dans une
    autre org que l'active — ici `r-ancien`, ouvert sous l'org 3 alors que l'org
    active est la 7)."""
    out = P._project(CTX, P.ProjectInput(op="runs"))
    assert journal["sub"] == "u1"
    assert {r["org_id"] for r in out["runs"]} == {3, 7}


def test_chaque_deroule_porte_son_etat_LISIBLE(journal):
    """Même dérivation que partout ailleurs (`run_status`) : « en cours » vs « sans
    nouvelles depuis le … ». Un run silencieux depuis un mois n'a pas à s'annoncer
    « en cours » ici alors qu'il ne le fait plus nulle part."""
    out = P._project(CTX, P.ProjectInput(op="runs"))
    etats = {r["run_id"]: r["status"] for r in out["runs"]}
    assert etats["r-ancien"].startswith("(sans nouvelles")
    assert etats["r-recent"]                       # une phrase, toujours


# ── Ce qui ne change pas : nommer un projet garde le sens d'avant ────────────────────

def test_nommer_un_projet_rend_toujours_les_deroules_DU_PROJET(monkeypatch):
    """L'ajout est additif : `op=runs` avec un `project_id` continue de répondre ce
    qu'il répondait — les runs du projet, tous, clos compris (c'est la pastille
    ok/échec du viewer, pas une file à refermer)."""
    monkeypatch.setattr(P.db, "get_project_by_id",
                        lambda pid: {"id": 12, "name": "P", "owner_type": "org",
                                     "owner_id": "1", "brief_md": ""})
    monkeypatch.setattr(P, "_require_active_org_visible", lambda ctx, row: None)
    monkeypatch.setattr(P.db, "project_runs",
                        lambda pid, doctrine=None: [{"run_id": "r-projet"}])
    out = P._project(CTX, P.ProjectInput(op="runs", project_id=12))
    assert out["id"] == 12
    assert [r["run_id"] for r in out["runs"]] == ["r-projet"]
    assert "scope" not in out


def test_les_autres_ops_exigent_toujours_un_projet_et_le_DISENT():
    """La détente ne vaut que pour `runs`. Toute autre op sans cible reste un refus
    NOMMÉ — c'est ce qui distingue une souplesse décidée d'un repli silencieux."""
    with pytest.raises(AuthzDenied) as e:
        P._project(CTX, P.ProjectInput(op="get"))
    assert e.value.code == "missing_project"


# ── La requête elle-même, contre un vrai PostgreSQL ─────────────────────────────────
#
# Les tests ci-dessus stubent `db.my_runs` : ils décrivent la SURFACE, et ne diraient
# rien d'une requête qui ne s'exécute pas. Or c'est du SQL neuf, écrit autour d'un
# helper partagé (`_runs_from_journal`) dont la CTE a ses alias à elle — exactement le
# genre d'endroit où un prédicat mal placé passe la relecture et tombe en prod. Même
# posture que `test_typed_sort.py` : « le banc stubbé a déjà menti une fois ».

import re
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from oto_mcp.db import _schema, usage


def _real_ddl(table: str) -> str:
    m = re.search(rf"^CREATE TABLE IF NOT EXISTS {table} \(.*?^\);",
                  _schema._SCHEMA, re.S | re.M)
    assert m, f"DDL de `{table}` introuvable dans _schema.py"
    return m.group(0)


@pytest.fixture()
def live(pg_dsn, monkeypatch):
    """Les deux tables du run, avec le DDL RÉEL, et `my_runs` branché dessus."""
    psycopg = pytest.importorskip("psycopg")
    from psycopg.rows import dict_row
    with psycopg.connect(pg_dsn, row_factory=dict_row, autocommit=True) as c:
        c.execute("DROP TABLE IF EXISTS runs")
        c.execute("DROP TABLE IF EXISTS tool_calls")
        c.execute(_real_ddl("tool_calls"))
        c.execute(_real_ddl("runs"))

        @contextmanager
        def _connect_test():
            yield c

        monkeypatch.setattr(usage, "_connect", _connect_test)
        yield c


def _ouvre(conn, run_id, *, sub="u1", org_id=None, label="un déroulé",
           minutes_ago=60) -> None:
    quand = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    conn.execute(
        "INSERT INTO tool_calls (created_at, sub, tool, run_id, org_id, args) "
        "VALUES (%s, %s, 'run_start', %s, %s, %s::jsonb)",
        (quand, sub, run_id, org_id, '{"label": "%s"}' % label))


def _ferme(conn, run_id, *, sub="u1", outcome="done", minutes_ago=5) -> None:
    quand = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    conn.execute(
        "INSERT INTO tool_calls (created_at, sub, tool, args) "
        "VALUES (%s, %s, 'run_finish', %s::jsonb)",
        (quand, sub, '{"run_id": "%s", "outcome": "%s"}' % (run_id, outcome)))


def test_la_requete_s_execute_et_ne_rend_QUE_mes_deroules_ouverts(live):
    """Le lot entier tient à cette requête. Quatre runs, un seul doit sortir."""
    _ouvre(live, "mien-ouvert", sub="u1", org_id=3)
    _ouvre(live, "mien-clos", sub="u1", org_id=3)
    _ferme(live, "mien-clos", sub="u1")
    _ouvre(live, "autre-ouvert", sub="u2", org_id=3)      # pas le mien
    _ouvre(live, "mien-autre-org", sub="u1", org_id=99)   # autre org que l'active

    ouverts = usage.my_runs("u1", open_only=True)
    assert {r["run_id"] for r in ouverts} == {"mien-ouvert", "mien-autre-org"}, \
        "le run d'un tiers ou déjà clos n'a rien à faire dans la file à refermer"
    # Sans le filtre, le run clos revient — avec son issue, lue du FAIT de clôture.
    tous = {r["run_id"]: r["outcome"] for r in usage.my_runs("u1")}
    assert tous["mien-clos"] == "done" and tous["mien-ouvert"] is None


def test_la_cloture_d_un_TIERS_ne_ferme_pas_mon_deroule(live):
    """La même règle de propriété que `finish_run`, tenue jusque dans la lecture : un
    `run_finish` tapé par quelqu'un d'autre sur un run_id deviné donnerait sinon au
    journal une issue que la table refuse — le run resterait à refermer sans le dire."""
    _ouvre(live, "le-mien", sub="u1")
    _ferme(live, "le-mien", sub="u2")            # un tiers prétend le clore
    assert [r["run_id"] for r in usage.my_runs("u1", open_only=True)] == ["le-mien"]
