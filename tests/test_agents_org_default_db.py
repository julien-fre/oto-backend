"""Le partage d'un agent par le réglage de son org (23/10/2026) — en base.

1. `visibilites_d_agents` dit, en UNE requête, qui voit chaque agent au-delà de son
   propriétaire : le rôle du partage VIVANT à l'org, et le nombre de personnes nommées.
2. `orgs.default_sharing` se pose et se relit tel quel (la validation est au module de
   capacité, `test_partage_par_defaut.py`).
3. Le partage né du réglage est MARQUÉ : il se retire sans toucher aux partages faits à
   la main. En lecture il ne prête rien ; en écriture il prête le forfait du
   propriétaire COURANT — une reprise le lui retire.
4. Un partage `write` à l'org prête le forfait s'il est posé par le propriétaire ; ni
   celui de la révision 0035, ni celui d'un admin, ni celui d'une autre org.
Joué sur une vraie base.
"""
from __future__ import annotations

import psycopg
import pytest

ORG = 8310


def _agent(db, org=ORG, **kw):
    return db.create_trigger(org, "proprio", procedure="veille", tz="UTC", tools=["a"],
                             cron="0 8 * * *", **kw)


def test_qui_voit_chaque_agent(live, pg_module_dsn):
    from oto_mcp import db
    a, b, c, d = _agent(db), _agent(db), _agent(db), _agent(db)
    db.grant_resource("runner_trigger", str(a["id"]), "org", str(ORG), role="editor")
    db.grant_resource("runner_trigger", str(a["id"]), "user", "x", role="viewer")
    db.grant_resource("runner_trigger", str(b["id"]), "org", str(ORG), role="viewer")
    db.grant_resource("runner_trigger", str(c["id"]), "org", str(ORG), role="editor")
    with psycopg.connect(pg_module_dsn, autocommit=True) as conn:
        conn.execute("UPDATE resource_grants SET expires_at = NOW() - interval '1 day' "
                     "WHERE resource_id = %s", (str(c["id"]),))
    assert db.visibilites_d_agents([a["id"], b["id"], c["id"], d["id"]], ORG) == {
        a["id"]: {"org": "editor", "people": 1},
        b["id"]: {"org": "viewer", "people": 0},
        c["id"]: {"org": None, "people": 0},   # échu : ne donne plus rien
        d["id"]: {"org": None, "people": 0},
    }
    # Un partage à une AUTRE org ne rend pas l'agent visible dans la sienne.
    assert db.visibilites_d_agents([b["id"]], ORG + 1)[b["id"]]["org"] is None
    assert db.visibilites_d_agents([], ORG) == {}


def test_le_reglage_se_pose_et_se_relit(live, pg_module_dsn):
    from oto_mcp import db
    with psycopg.connect(pg_module_dsn, autocommit=True) as c:
        org = c.execute("INSERT INTO orgs (name) VALUES ('Réglage') RETURNING id"
                        ).fetchone()[0]
    assert db.partage_par_defaut_stocke(org) is None, "une org d'avant : rien de posé"
    assert db.poser_partage_par_defaut(org, {"agent": "lecture"})
    assert db.partage_par_defaut_stocke(org) == {"agent": "lecture"}
    assert db.poser_partage_par_defaut(org, None)
    assert db.partage_par_defaut_stocke(org) is None
    assert db.poser_partage_par_defaut(10**12, {"agent": "lecture"}) is False


def test_le_partage_du_reglage_en_lecture_est_marque_et_ne_prete_aucun_forfait(live):
    from oto_mcp import db
    t = _agent(db, model="sub:sonnet")
    rid, tid = str(t["id"]), t["id"]
    db.grant_resource("runner_trigger", rid, "org", str(ORG), role="viewer",
                      granted_by=db.MARQUE_PARTAGE_PAR_DEFAUT)
    db.grant_resource("runner_trigger", rid, "user", "nomme", role="editor",
                      granted_by="proprio")
    db.grant_resource("runner_trigger", rid, "group", "12", role="viewer",
                      granted_by="proprio")
    assert db.forfaits_pretes([tid], "quelqu-un-de-l-org") == set(), (
        "le partage né du réglage en lecture ne prête pas le forfait")
    assert db.forfaits_pretes([tid], "nomme") == {tid}, "le prêt nommé reste le seul"

    # Le retirer — par sa marque — laisse les partages faits à la main.
    from oto_mcp.db import _conn as dbconn
    with dbconn._connect() as c:
        retires = c.execute(
            "DELETE FROM resource_grants WHERE resource_type = 'runner_trigger' "
            "AND granted_by = %s AND resource_id = %s",
            (db.MARQUE_PARTAGE_PAR_DEFAUT, rid)).rowcount
    assert retires == 1
    assert db.visibilites_d_agents([tid], ORG)[tid] == {"org": None, "people": 2}


def _prete_a(db, t, ptype, pid, granted_by, role="editor"):
    db.grant_resource("runner_trigger", str(t["id"]), ptype, pid, role=role,
                      granted_by=granted_by)


def test_le_reglage_en_ecriture_prete_le_forfait_a_toute_l_org(live):
    from oto_mcp import db
    t = _agent(db, model="sub:sonnet")
    _prete_a(db, t, "org", str(ORG), db.MARQUE_PARTAGE_PAR_DEFAUT)
    assert db.forfaits_pretes([t["id"]], "quelqu-un-de-l-org") == {t["id"]}
    # La garde d'écriture lit le même prédicat.
    assert db.update_trigger(t["id"], ORG, {"label": "retouche"},
                             hors_abonnement_d_autrui="quelqu-un-de-l-org") is not None


@pytest.mark.parametrize("granted_by,prete", [
    ("proprio", True),                                   # le propriétaire l'ouvre
    ("un-admin", False),                                 # un admin ne dispose pas du forfait
    ("migration:0035_agents_partages_a_l_org", False),   # posé sur chaque agent d'avant
])
def test_qui_a_pose_le_partage_a_l_org_decide_du_pret(live, granted_by, prete):
    from oto_mcp import db
    t = _agent(db, model="sub:sonnet")
    _prete_a(db, t, "org", str(ORG), granted_by)
    assert (db.forfaits_pretes([t["id"]], "membre") == {t["id"]}) is prete


def test_un_partage_a_l_org_en_lecture_ou_a_une_autre_org_ne_prete_rien(live):
    from oto_mcp import db
    a, b = _agent(db, model="sub:sonnet"), _agent(db, model="sub:sonnet")
    _prete_a(db, a, "org", str(ORG), db.MARQUE_PARTAGE_PAR_DEFAUT, role="viewer")
    _prete_a(db, b, "org", str(ORG + 1), "proprio")
    assert db.forfaits_pretes([a["id"], b["id"]], "membre") == set()


def test_une_reprise_rend_le_partage_du_reglage_a_l_ancien_proprietaire(live):
    from oto_mcp import db
    t = _agent(db, model="sub:sonnet")
    _prete_a(db, t, "org", str(ORG), db.MARQUE_PARTAGE_PAR_DEFAUT)
    assert db.reprendre_trigger(t["id"], ORG, "repreneur") is not None
    assert db.forfaits_pretes([t["id"]], "membre") == set(), (
        "le forfait du repreneur ne se prête pas sans lui")
    assert db.visibilites_d_agents([t["id"]], ORG)[t["id"]]["org"] == "editor", (
        "l'agent reste ouvert à l'org")
