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


def test_le_forfait_ne_se_prete_que_par_son_proprietaire(live, pg_module_dsn):
    """Le propriétaire prête son forfait à une personne nommée ; un partage posé par un
    admin, en lecture, ou à l'org par la révision 0035 ne prête rien. (Ouvert par le
    propriétaire à toute l'org en écriture, il prête : `test_agents_org_default_db.py`.)"""
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
                      granted_by="migration:0035_agents_partages_a_l_org")
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


def test_les_outils_plateforme_ne_valent_que_pour_l_agent_au_seul_proprietaire(live):
    """Ce qui permet à un travail d'exercer des `oto_admin_*` avec le rôle plateforme de
    son porteur (`verrou_org.outils_permis`) : la liste de son agent, SEULEMENT si cet agent
    n'est modifiable que par son porteur — abonnement personnel, aucun éditeur nommé par
    lui ni ouvert par lui à toute l'org en écriture. Le partage de la révision 0035 ou
    posé par un admin ne prête rien. La liste ne suit pas l'agent à un transfert."""
    from oto_mcp import db
    liste = ["oto_admin_org", "oto_admin_signal"]

    def travail(t, sub="proprio", org=ORG):
        # `start` : le seul coup d'envoi d'un agent programmé (`runner_jobs.kind`).
        return db.enqueue_job(org, "start", {"trigger_id": t["id"]}, sub=sub)["id"]

    def arme(t):
        return db.update_trigger(t["id"], ORG, {"outils_plateforme": liste})

    seul = db.create_trigger(ORG, "proprio", procedure="veille", tz="UTC",
                             tools=["a", *liste], cron="0 8 * * *", model="sub:sonnet")
    assert seul["platform_tools"] == [], "aucun outil d'admin par défaut"
    assert db.outils_plateforme_du_travail(travail(seul)) == []
    assert arme(seul)["platform_tools"] == liste
    db.grant_resource("runner_trigger", str(seul["id"]), "org", str(ORG), role="editor",
                      granted_by="migration:0035_agents_partages_a_l_org")
    db.grant_resource("runner_trigger", str(seul["id"]), "user", "par-admin",
                      role="editor", granted_by="un-admin")
    assert db.outils_plateforme_du_travail(travail(seul)) == liste

    for pose_par in ("proprio", db.MARQUE_PARTAGE_PAR_DEFAUT):
        ouvert = db.create_trigger(ORG, "proprio", procedure="veille", tz="UTC",
                                   tools=["a", *liste], cron="0 8 * * *", model="sub:sonnet")
        arme(ouvert)
        db.grant_resource("runner_trigger", str(ouvert["id"]), "org", str(ORG),
                          role="editor", granted_by=pose_par)
        assert db.outils_plateforme_du_travail(travail(ouvert)) == [], (
            f"ouvert à l'org en écriture ({pose_par}) : le forfait est prêté")

    prete = db.create_trigger(ORG, "proprio", procedure="veille", tz="UTC",
                              tools=["a", *liste], cron="0 8 * * *", model="sub:sonnet")
    arme(prete)
    db.grant_resource("runner_trigger", str(prete["id"]), "user", "nomme", role="editor",
                      granted_by="proprio")
    assert db.outils_plateforme_du_travail(travail(prete)) == [], "prêté à un éditeur"

    cle_d_org = _agent(db)   # tout admin de l'org le retouche
    arme(cle_d_org)
    assert db.outils_plateforme_du_travail(travail(cle_d_org)) == [], "clé d'org"
    assert db.outils_plateforme_du_travail(travail(seul, sub="autre")) == [], "autre porteur"
    assert db.outils_plateforme_du_travail(
        db.enqueue_job(ORG, "start", {}, sub="proprio")["id"]) == [], "enfilé à la main"
    assert db.outils_plateforme_du_travail(-1) == [], "introuvable"

    repris = db.reprendre_trigger(seul["id"], ORG, "nouveau")
    assert repris[0]["platform_tools"] == [], "la liste ne suit pas l'agent"
    assert db.outils_plateforme_du_travail(travail(seul, sub="nouveau")) == []
