"""Le partage d'agents EN BASE — ce qu'une doublure ne peut pas prouver.

1. `partages_d_agents` lit en UNE requête le meilleur partage VIVANT parmi les
   principaux de l'appelant (`write` l'emporte, un partage échu ne compte pas).
2. Supprimer un agent retire ses partages.
3. Le forfait d'un propriétaire ne se prête qu'à la PERSONNE qu'il a nommée
   éditrice (`forfaits_pretes`), et la garde d'écriture de `update_trigger` le lit
   elle aussi.
"""
from __future__ import annotations

import psycopg

ORG = 8200


def _agent(db, sub="proprio", org=ORG):
    return db.create_trigger(org, sub, procedure="veille", tz="UTC", tools=["a"],
                             cron="0 8 * * *")


def test_le_meilleur_partage_vivant_parmi_les_principaux(live, pg_module_dsn):
    from oto_mcp import db
    a, b, c = _agent(db), _agent(db), _agent(db)
    db.grant_resource("runner_trigger", str(a["id"]), "user", "lecteur", role="viewer")
    db.grant_resource("runner_trigger", str(a["id"]), "org", str(ORG), role="editor")
    db.grant_resource("runner_trigger", str(b["id"]), "user", "lecteur", role="viewer")
    db.grant_resource("runner_trigger", str(c["id"]), "user", "lecteur", role="editor")
    with psycopg.connect(pg_module_dsn, autocommit=True) as conn:
        conn.execute("UPDATE resource_grants SET expires_at = NOW() - interval '1 day' "
                     "WHERE resource_id = %s", (str(c["id"]),))
    principaux = [("user", "lecteur"), ("org", str(ORG))]
    assert db.partages_d_agents([a["id"], b["id"], c["id"]], principaux) == {
        a["id"]: "write", b["id"]: "read"}, (
        "l'org en écriture l'emporte sur la personne en lecture ; un partage échu ne "
        "donne rien")
    assert db.partages_d_agents([a["id"]], [("user", "autre")]) == {}
    assert db.partages_d_agents([], principaux) == {}


def test_supprimer_un_agent_retire_ses_partages(live):
    from oto_mcp import db
    t = _agent(db)
    db.grant_resource("runner_trigger", str(t["id"]), "user", "editeur", role="editor")
    assert db.delete_trigger(t["id"], ORG)
    assert db.list_resource_grants("runner_trigger", str(t["id"])) == []


def test_le_forfait_ne_se_prete_que_par_son_proprietaire_a_une_personne(live, pg_module_dsn):
    from oto_mcp import db
    t = db.create_trigger(ORG, "proprio", procedure="veille", tz="UTC", tools=["a"],
                          cron="0 8 * * *", model="sub:sonnet")
    autre = _agent(db)   # un agent sur une clé d'org : rien à prêter
    rid, tid = str(t["id"]), t["id"]
    db.grant_resource("runner_trigger", rid, "user", "nomme", role="editor",
                      granted_by="proprio")
    db.grant_resource("runner_trigger", rid, "user", "par-admin", role="editor",
                      granted_by="un-admin")
    db.grant_resource("runner_trigger", rid, "user", "lecteur", role="viewer",
                      granted_by="proprio")
    db.grant_resource("runner_trigger", rid, "org", str(ORG), role="editor",
                      granted_by="proprio")
    assert db.forfaits_pretes([tid, autre["id"]], "nomme") == {tid}
    for sub in ("par-admin", "lecteur", "quelqu-un-de-l-org"):
        assert db.forfaits_pretes([tid], sub) == set(), sub

    # La garde d'écriture lit le même prêt — et seulement lui.
    for sub in ("par-admin", "lecteur", "quelqu-un-de-l-org"):
        assert db.update_trigger(tid, ORG, {"input": "x"},
                                 hors_abonnement_d_autrui=sub) is None, sub
    assert db.update_trigger(tid, ORG, {"input": "par le nommé"},
                             hors_abonnement_d_autrui="nomme")["input"] == "par le nommé"
    assert db.update_trigger(tid, ORG, {"input": "par lui"},
                             hors_abonnement_d_autrui="proprio")["input"] == "par lui"

    # Échu, le partage ne prête plus rien.
    with psycopg.connect(pg_module_dsn, autocommit=True) as conn:
        conn.execute("UPDATE resource_grants SET expires_at = NOW() - interval '1 day' "
                     "WHERE resource_id = %s AND principal_id = 'nomme'", (rid,))
    assert db.forfaits_pretes([tid], "nomme") == set()
    assert db.update_trigger(tid, ORG, {"input": "y"},
                             hors_abonnement_d_autrui="nomme") is None
