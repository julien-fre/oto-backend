"""`workers` borne les travaux EN COURS d'une campagne (#907, oto#245), en SQL réel.

Jusqu'au 23/09/2026 le champ était accepté, stocké, rendu — et ne bornait rien :
huit unités qui sondaient une campagne déclarée `workers: 3` la tenaient à huit en
vol. Arbitrage d'Alexis : une campagne n'a jamais plus de `workers` travaux en
cours (`pending` + `claimed`).

Deux étages, éprouvés séparément parce qu'ils ne gardent pas la même chose :

- **l'élection** (`campagne_a_servir`) n'élit plus une campagne au plafond ;
- **l'enfilage** (`enqueue_job(..., seulement_si_servable=True)`) revérifie sous le
  verrou de campagne, dans la transaction de l'INSERT. Sans lui, deux sondages qui
  passent l'élection avant que l'un ait enfilé produisent chacun un travail : la
  borne serait LUE, pas TENUE. Le dernier banc rejoue exactement cette course.
"""
from __future__ import annotations

import os
import threading
import uuid

import pytest


@pytest.fixture(scope="module")
def live(pg_dsn):
    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    name = "oto_workers_" + uuid.uuid4().hex[:8]
    root = psycopg.connect(pg_dsn, autocommit=True)
    root.execute(f'CREATE DATABASE "{name}"')
    dsn = pg_dsn.rsplit("/", 1)[0] + "/" + name

    avant_url, avant_pool = os.environ.get("DATABASE_URL"), dbconn._pool
    avant_key = os.environ.get("OTO_MCP_MASTER_KEY")
    os.environ["DATABASE_URL"] = dsn
    os.environ["OTO_MCP_MASTER_KEY"] = "4" * 64
    dbconn._pool = None
    try:
        from oto_mcp.db import init_db
        init_db()
        yield
    finally:
        if dbconn._pool is not None:
            dbconn._pool.close()
        dbconn._pool = avant_pool
        for cle, valeur in (("DATABASE_URL", avant_url),
                            ("OTO_MCP_MASTER_KEY", avant_key)):
            if valeur is None:
                os.environ.pop(cle, None)
            else:
                os.environ[cle] = valeur
        root.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        root.close()


@pytest.fixture
def org_neuve(live):
    from oto_mcp import db, org_store
    uniq = uuid.uuid4().hex[:8]
    sub = f"workers_{uniq}"
    db.upsert_user(sub)
    return {"org": org_store.create_org(f"org_{uniq}", created_by=sub), "sub": sub}


def _campagne_armee(org: int, sub: str, workers: int) -> dict:
    from oto_mcp import db
    f = db.create_fleet(org, sub, label="essai", procedure="fleet-demo",
                        tools=["data_rows"], namespace="t", max_rows=None,
                        workers=workers)
    db.armer(f["id"], org)
    return f


def _dans_l_ordre(candidates):
    return [c["id"] for c in candidates]


def _travail_reserve(org: int, fleet_id: int) -> None:
    """Un travail de la campagne, déjà pris par un worker (`claimed`)."""
    from oto_mcp import db
    from oto_mcp.db._conn import _connect
    j = db.enqueue_job(org, "start", payload={"procedure": "fleet-demo"},
                       fleet_id=fleet_id)
    with _connect() as conn:
        conn.execute("UPDATE runner_jobs SET status = 'claimed' WHERE id = %s",
                     (j["id"],))


def _en_cours(fleet_id: int) -> int:
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        return conn.execute(
            "SELECT COUNT(*) AS n FROM runner_jobs "
            " WHERE fleet_id = %s AND status IN ('pending', 'claimed')",
            (fleet_id,)).fetchone()["n"]


def test_une_campagne_au_plafond_nest_plus_elue(org_neuve):
    """`workers=1` et un travail déjà pris : plus rien à fabriquer pour elle.
    Avant #907 elle était élue (aucun `pending`), et un second agent partait."""
    from oto_mcp import db
    f = _campagne_armee(org_neuve["org"], org_neuve["sub"], workers=1)
    _travail_reserve(org_neuve["org"], f["id"])

    assert db.campagne_a_servir(org_neuve["org"], _dans_l_ordre) is None, (
        "une campagne `workers=1` qui a déjà un travail en cours ne doit plus être "
        "servie")


def test_sous_le_plafond_la_campagne_reste_elue(org_neuve):
    """Le pendant : `workers=2` et un seul travail pris, il reste une place."""
    from oto_mcp import db
    f = _campagne_armee(org_neuve["org"], org_neuve["sub"], workers=2)
    _travail_reserve(org_neuve["org"], f["id"])

    servie = db.campagne_a_servir(org_neuve["org"], _dans_l_ordre)
    assert servie is not None and servie["id"] == f["id"]


def test_l_enfilage_revérifie_la_borne(org_neuve):
    """Deux sondages ont tous deux élu la campagne ; seul le premier enfile."""
    from oto_mcp import db
    f = _campagne_armee(org_neuve["org"], org_neuve["sub"], workers=1)

    premier = db.enqueue_job(org_neuve["org"], "start", payload={"procedure": "p"},
                             fleet_id=f["id"], sub=org_neuve["sub"],
                             seulement_si_servable=True)
    second = db.enqueue_job(org_neuve["org"], "start", payload={"procedure": "p"},
                            fleet_id=f["id"], sub=org_neuve["sub"],
                            seulement_si_servable=True)

    assert premier is not None and premier["fleet_id"] == f["id"]
    assert second is None, "la place prise par le premier ne se prend pas deux fois"
    assert _en_cours(f["id"]) == 1


def test_deux_sondages_concurrents_ne_depassent_pas_le_plafond(org_neuve, monkeypatch):
    """La course réelle, au niveau de la capacité, rendue DÉTERMINISTE : le second
    sondage s'exécute ENTIÈREMENT pendant que le premier compose sa consigne, soit
    entre son élection et son enfilage. Les deux ont donc élu la campagne avant
    que le premier ait enfilé — la fenêtre exacte que l'élection seule ne ferme pas.

    ⚠️ Deux fils lancés « en même temps » ne suffisent pas : le second perd le
    verrou d'élection du premier et repart les mains vides, et le banc passe sur le
    code fautif. Mesuré à la première écriture de ce banc.

    Avant #907 : deux travaux en cours pour une campagne `workers=1`."""
    from oto_mcp.capabilities import runner_jobs as RJ

    f = _campagne_armee(org_neuve["org"], org_neuve["sub"], workers=1)
    # L'ordre de service regarde les files des tableaux : hors sujet ici.
    monkeypatch.setattr(RJ._ordre_de_service, "ordonner", _dans_l_ordre)

    pannes = []
    appels = []

    def _sonder():
        panne = RJ._produire_pour_une_campagne(org_neuve["org"], 60)
        if panne:
            pannes.append(panne)

    def _consigne(campagne):
        appels.append(campagne["id"])
        if len(appels) == 1:
            # Premier sondage, élu, pas encore enfilé : le second passe en entier.
            # Dans un AUTRE fil, pour qu'il ait sa propre connexion.
            second = threading.Thread(target=_sonder)
            second.start()
            second.join(60)
        return "consigne"
    monkeypatch.setattr(RJ.runner_consigne, "composer", _consigne)

    _sonder()

    assert not pannes, pannes
    assert appels == [f["id"], f["id"]], (
        "[banc] les deux sondages devaient élire la campagne — sinon la course "
        f"n'est pas rejouée : {appels}")
    assert _en_cours(f["id"]) == 1, (
        "deux sondages concurrents ont dépassé le plafond `workers=1`")
