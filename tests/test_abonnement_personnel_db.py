"""La file d'un ABONNEMENT personnel, EN BASE (OTO-130) — ce qu'une doublure ne prouve pas.

Deux clauses vivent en SQL dans `claim_next_job` et ne se jugent que là : la
sérialisation par PERSONNE, et l'attente de l'échéance d'un forfait épuisé. Le
troisième banc tient la couture entre la base et la garde : un forfait dont
l'échéance est PASSÉE redevient servable des deux côtés à la fois — sinon la file
rend le travail et la garde le tue.

Patron de base éphémère repris de `test_worker_cles_clients_db.py`. Tous les claims
sont scopés à leur org : un claim de plateforme prendrait le travail des autres bancs.
"""
from __future__ import annotations

import os
import threading
import uuid

import pytest

_FAMILLE = "claude_subscription"


@pytest.fixture(scope="module")
def live(pg_dsn):
    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    name = "oto_abonnement_" + uuid.uuid4().hex[:8]
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


def _personne(sub):
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        conn.execute("INSERT INTO users (sub) VALUES (%s) ON CONFLICT DO NOTHING", (sub,))
    return sub


def _ouvert_ici(org, sub, famille):
    """Depuis le 08/10/2026 un abonnement ne sert que les orgs où la personne l'a
    ouvert, membre : ces bancs jugent la file, ils ouvrent donc l'org du travail."""
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        conn.execute("INSERT INTO orgs (id, name) VALUES (%s, %s) ON CONFLICT (id) DO NOTHING",
                     (org, f"Org {org}"))
        conn.execute("INSERT INTO org_members (org_id, sub) VALUES (%s, %s) "
                     "ON CONFLICT DO NOTHING", (org, sub))
        conn.execute("INSERT INTO user_model_subscription_orgs (sub, famille, org_id) "
                     "VALUES (%s, %s, %s) ON CONFLICT DO NOTHING", (sub, famille, org))


def _travail(org, sub, famille=_FAMILLE):
    from oto_mcp import db
    _ouvert_ici(org, sub, famille)
    return db.enqueue_job(org, "start", sub=sub,
                          payload={"procedure": "p", "model": "sub:sonnet",
                                   "model_family": famille})["id"]


def _claim(org, worker="w-abonnement"):
    from oto_mcp import db
    return db.claim_next_job(org, worker, lease_seconds=60,
                             depot=_FAMILLE, famille_seule=True)


def test_un_seul_travail_a_la_fois_par_personne(live):
    from oto_mcp import db
    a, b = _personne("abo-a"), _personne("abo-b")
    premier, second = _travail(9401, a), _travail(9401, a)
    de_b = _travail(9401, b)

    assert _claim(9401)["id"] == premier
    # Le second travail de A ATTEND ; celui de B, plus récent, passe devant.
    assert _claim(9401)["id"] == de_b
    assert _claim(9401) is None, "le second travail de A attend la fin du premier"

    db.complete_job(premier, "w-abonnement", ok=True)
    assert _claim(9401)["id"] == second, "et il part dès que le premier conclut"


def test_une_autre_famille_n_est_JAMAIS_serialisee(live):
    """Les deux clauses sont éteintes hors abonnement : deux travaux `anthropic`
    d'une même personne partent ensemble, comme avant ce lot."""
    from oto_mcp import db
    c = _personne("abo-c")
    un, deux = _travail(9402, c, "anthropic"), _travail(9402, c, "anthropic")
    pris = [db.claim_next_job(9402, "w", lease_seconds=60, depot="anthropic")["id"]
            for _ in range(2)]
    assert pris == [un, deux]


def test_un_forfait_epuise_attend_son_echeance_puis_REPART(live):
    """⚠️ La couture base ↔ garde. Tant que l'échéance est future, la file saute la
    personne. Une fois PASSÉE, la file rend le travail — et la garde du claim doit le
    servir aussi : si elle le refusait, le travail serait ARRÊTÉ DÉFINITIVEMENT par un
    plafond qui n'existe plus."""
    from oto_mcp import db
    from oto_mcp.capabilities import _abonnement
    from oto_mcp.db import user_subscriptions as US
    from oto_mcp.db._conn import _connect
    d = _personne("abo-d")
    US.upsert_sandbox(d, _FAMILLE, "sandbox-d")
    with _connect() as conn:
        futur = conn.execute("SELECT NOW() + interval '1 hour' AS t").fetchone()["t"]
        passe = conn.execute("SELECT NOW() - interval '1 minute' AS t").fetchone()["t"]
    US.marquer_statut(d, _FAMILLE, US.PLAFOND, limit_reset_at=futur)
    travail = _travail(9403, d)
    assert _claim(9403) is None, "échéance future : la personne attend"

    US.marquer_statut(d, _FAMILLE, US.PLAFOND, limit_reset_at=passe)
    pris = _claim(9403)
    assert pris and pris["id"] == travail, "échéance passée : la file le rend"
    servable, _, sandbox = _abonnement.servable(d, _FAMILLE)
    assert servable and sandbox == "sandbox-d", (
        "la garde doit servir ce que la file vient de rendre — sinon le travail "
        "est tué par un plafond expiré")


def test_une_pause_ANCIENNE_n_est_servie_qu_au_worker_qui_SONDE(live):
    """Une pause plus vieille que la fenêtre de revérification ne freine plus un worker
    qui sonde le forfait avant le run (`forfait_sonde`) : la sonde tranchera. Un worker
    qui ne sonde pas attend toujours l'échéance — il lancerait un run refusé."""
    from oto_mcp import db
    from oto_mcp.db import user_subscriptions as US
    from oto_mcp.db._conn import _connect
    s = _personne("abo-sonde")
    US.upsert_sandbox(s, _FAMILLE, "sandbox-s")
    with _connect() as conn:
        futur = conn.execute("SELECT NOW() + interval '5 days' AS t").fetchone()["t"]
    US.marquer_statut(s, _FAMILLE, US.PLAFOND, limit_reset_at=futur, epuise=True)
    travail = _travail(9406, s)

    def _prise(sonde):
        return db.claim_next_job(9406, "w-sonde", lease_seconds=60, depot=_FAMILLE,
                                 famille_seule=True, sonde=sonde)
    assert _prise(True) is None, "pause FRAÎCHE : même le worker qui sonde attend"

    with _connect() as conn:
        conn.execute("UPDATE user_model_subscriptions SET updated_at = NOW() - "
                     "make_interval(secs => %s) WHERE sub = %s",
                     (US.VERIFICATION_S + 60, s))
    assert _prise(False) is None, "sans sonde, l'échéance stockée fait foi"
    pris = _prise(True)
    assert pris and pris["id"] == travail, "pause ANCIENNE : la sonde tranchera"


@pytest.mark.parametrize("fenetre,epuise,org,sondee", [
    ("seven_day", False, 9407, True),        # mesuré le 28/10 : 27 % sous 25 %, relevé à 75 %
    ("five_hour", False, 9408, True),
    ("seven_day_opus", False, 9409, False),  # propre à un modèle : la sonde n'en mesure rien
    ("seven_day", True, 9420, False),        # refus du FOURNISSEUR : son échéance fait foi
])
def test_une_pause_ANCIENNE_du_COMPTE_est_sondee_meme_de_fenetre_connue(
        live, fenetre, epuise, org, sondee):
    """Fenêtre connue ou non (28/10/2026) : une pause de NOTRE seuil se sonde, la sonde lit
    la capacité réelle sous le plafond du moment. Gardent leur échéance : une fenêtre
    propre à un modèle, et un refus du fournisseur sur une fenêtre connue."""
    from oto_mcp import db
    from oto_mcp.db import user_subscriptions as US
    from oto_mcp.db._conn import _connect
    s = _personne(f"abo-nomme-{fenetre}-{epuise}")
    US.upsert_sandbox(s, _FAMILLE, f"sandbox-{fenetre}-{epuise}")
    with _connect() as conn:
        futur = conn.execute("SELECT NOW() + interval '5 days' AS t").fetchone()["t"]
    US.marquer_statut(s, _FAMILLE, US.PLAFOND, limit_reset_at=futur, epuise=epuise,
                      fenetre=fenetre)
    assert US.get_subscription(s, _FAMILLE)["limit_fenetre"] == fenetre
    travail = _travail(org, s)
    prise = lambda: db.claim_next_job(org, "w-sonde", lease_seconds=60, depot=_FAMILLE,
                                      famille_seule=True, sonde=True)
    assert prise() is None, "pause FRAÎCHE : elle freine, même pour qui sonde"
    with _connect() as conn:
        conn.execute("UPDATE user_model_subscriptions SET updated_at = NOW() - "
                     "make_interval(secs => %s) WHERE sub = %s",
                     (US.VERIFICATION_S + 60, s))
    pris = prise()
    assert (pris and pris["id"]) == (travail if sondee else None), fenetre
    US.marquer_statut(s, _FAMILLE, US.CONNECTE, observe=True)
    assert US.get_subscription(s, _FAMILLE)["limit_fenetre"] is None, (
        "la fenêtre ne survit pas à la pause")


def test_deux_workers_SIMULTANES_ne_prennent_pas_deux_travaux_d_une_personne(live):
    """La sérialisation tient-elle sous concurrence réelle ? `NOT EXISTS` lit un
    instantané : deux transactions parallèles ne voient pas la prise de l'autre."""
    e = _personne("abo-e")
    for _ in range(6):
        _travail(9404, e)
    pris, barriere = [], threading.Barrier(3)

    def _un(i):
        barriere.wait()
        j = _claim(9404, worker=f"w-{i}")
        if j:
            pris.append(j["id"])

    fils = [threading.Thread(target=_un, args=(i,)) for i in range(3)]
    [f.start() for f in fils]
    [f.join() for f in fils]
    assert len(pris) == 1, f"{len(pris)} travaux de la même personne en vol : {pris}"


def test_la_boucle_entiere_un_rapport_met_en_attente_et_la_file_SAUTE(live):
    """Conclusion → rapport → statut → réservation : les quatre maillons, en base.
    Chacun a son banc ; celui-ci tient qu'ils se PARLENT (mêmes noms de colonnes,
    même fuseau, même famille)."""
    from oto_mcp import db
    from oto_mcp.capabilities import _abonnement
    from oto_mcp.db import user_subscriptions as US
    from oto_mcp.db._conn import _connect
    f = _personne("abo-f")
    US.upsert_sandbox(f, _FAMILLE, "sandbox-f")
    US.marquer_statut(f, _FAMILLE, US.CONNECTE, ok=True)
    premier, second = _travail(9405, f), _travail(9405, f)
    assert _claim(9405)["id"] == premier

    db.complete_job(premier, "w-abonnement", ok=True)
    conclu = db.porteur_et_famille(premier)
    assert conclu == {"sub": f, "org_id": 9405, "model_family": _FAMILLE,
                      "abonnement": f, "pool": False}, (
        "de quoi adresser le rapport à l'abonnement qui a servi — en mode personnel, "
        "celui du demandeur — et lire le plafond de SON org")
    with _connect() as conn:
        dans_une_heure = conn.execute(
            "SELECT EXTRACT(EPOCH FROM NOW() + interval '1 hour')::bigint AS t"
        ).fetchone()["t"]
    _abonnement.noter_rapport(conclu, True, {"abonnement": {
        "etat": "allowed",
        "fenetres": {"five_hour": {"utilization": 0.99, "resetsAt": dans_une_heure}}}})

    assert US.get_subscription(f, _FAMILLE)["statut"] == US.PLAFOND
    assert _claim(9405) is None, "le second travail attend l'échéance du forfait"
    with _connect() as conn:
        assert conn.execute("SELECT status FROM runner_jobs WHERE id = %s",
                            (second,)).fetchone()["status"] == "pending", (
            "en ATTENTE — ni échoué, ni tentative brûlée")


@pytest.mark.parametrize("org_pct, perso_pct, en_pause", [
    (80, None, True),     # 0.81 franchit le plafond de l'org
    (90, None, False),    # l'org laisse de la marge, personne ne resserre
    (90, 75, True),       # la personne resserre pour elle-même : son plafond s'applique
    (70, 95, False),      # un perso plus haut relève le seuil : il remplace celui de l'org
    (None, None, True),   # rien de réglé : le défaut, 80
])
def test_le_plafond_de_L_ORG_DU_TRAVAIL_se_lit_en_base(live, org_pct, perso_pct, en_pause):
    """Conclusion → org du travail → plafonds (table d'org, colonne perso) → statut."""
    from oto_mcp import db, org_store
    from oto_mcp.capabilities import _abonnement
    from oto_mcp.db import org_subscription_limits as OL
    from oto_mcp.db import user_subscriptions as US
    from oto_mcp.db._conn import _connect
    p = _personne(f"abo-plafond-{org_pct}-{perso_pct}")
    oid = org_store.create_org(f"Org plafond {org_pct}-{perso_pct}", created_by=p)
    if org_pct is not None:
        OL.poser_limite(oid, _FAMILLE, org_pct, p)
    US.upsert_sandbox(p, _FAMILLE, f"sandbox-{p}")
    US.marquer_statut(p, _FAMILLE, US.CONNECTE, ok=True)
    US.poser_limite(p, _FAMILLE, perso_pct)
    travail = _travail(oid, p)
    assert _claim(oid)["id"] == travail
    db.complete_job(travail, "w-abonnement", ok=True)
    with _connect() as conn:
        dans_une_heure = conn.execute(
            "SELECT EXTRACT(EPOCH FROM NOW() + interval '1 hour')::bigint AS t"
        ).fetchone()["t"]

    _abonnement.noter_rapport_du_travail(travail, True, {"abonnement": {
        "etat": "allowed",
        "fenetres": {"five_hour": {"utilization": 0.81, "resetsAt": dans_une_heure},
                     "seven_day": {"utilization": 0.30, "resetsAt": dans_une_heure + 9}}}})

    statut = US.get_subscription(p, _FAMILLE)["statut"]
    assert statut == (US.PLAFOND if en_pause else US.CONNECTE), (org_pct, perso_pct)


@pytest.mark.parametrize("epuise,utilisation,nouveau,levee", [
    (False, 0.27, 75, True),    # mesuré le 28/10 : 27 % sous 25 %, relevé à 75 %
    (False, 0.27, 26, False),   # relevé, mais pas assez : la pause tient
    (True, 0.27, 75, False),    # le fournisseur a refusé : le plafond n'y peut rien
    (False, 0.27, None, False),  # retirer le perso ne lit pas l'org : rien ne bouge
])
def test_relever_SON_plafond_leve_la_pause_qu_il_ne_justifie_plus(
        live, epuise, utilisation, nouveau, levee):
    from datetime import datetime, timedelta, timezone
    from oto_mcp.db import user_subscriptions as US
    p = _personne(f"abo-releve-{epuise}-{nouveau}")
    US.upsert_sandbox(p, _FAMILLE, f"sandbox-{p}")
    US.poser_limite(p, _FAMILLE, 25)
    US.marquer_statut(p, _FAMILLE, US.PLAFOND,
                      limit_reset_at=datetime.now(timezone.utc) + timedelta(days=6),
                      epuise=epuise, utilisation=utilisation, fenetre="seven_day")

    ligne = US.poser_limite(p, _FAMILLE, nouveau)

    assert ligne["limite_pct"] == nouveau
    assert ligne["statut"] == (US.CONNECTE if levee else US.PLAFOND)
    assert US.get_subscription(p, _FAMILLE)["statut"] == ligne["statut"]
    if levee:
        assert ligne["limit_reset_at"] is None


def test_un_travail_EN_VOL_ne_reconnecte_PAS_qui_vient_de_se_deconnecter(live):
    """La personne se déconnecte pendant qu'un de ses travaux tourne. La conclusion
    de ce travail — succès, ou plafond rapporté — ne doit PAS la remettre en
    service : `paused_limit` est servable une fois l'échéance passée, donc un
    rapport tardif rouvrirait le forfait de quelqu'un qui a dit non."""
    from oto_mcp.capabilities import _abonnement
    from oto_mcp.db import user_subscriptions as US
    g = _personne("abo-g")
    US.upsert_sandbox(g, _FAMILLE, "sandbox-g")
    US.marquer_statut(g, _FAMILLE, US.CONNECTE, ok=True)
    conclu = {"sub": g, "model_family": _FAMILLE}

    US.marquer_statut(g, _FAMILLE, US.DECONNECTE)      # elle se déconnecte…
    _abonnement.noter_rapport(conclu, True, None)       # …le travail en vol réussit
    assert US.get_subscription(g, _FAMILLE)["statut"] == US.DECONNECTE

    _abonnement.noter_rapport(conclu, False, {"abonnement": {   # …ou rapporte un plafond
        "etat": "rejected",
        "fenetres": {"five_hour": {"utilization": 1.0, "resetsAt": 1}}}})
    assert US.get_subscription(g, _FAMILLE)["statut"] == US.DECONNECTE
    assert _abonnement.servable(g, _FAMILLE)[0] is False


@pytest.mark.parametrize("etat", ["needs_login", "disconnected"])
def test_qui_doit_se_reconnecter_voit_ses_travaux_ATTENDRE_puis_REPARTIR(live, etat):
    """Décidé le 21/09/2026 : une session perdue (ou une déconnexion voulue) ne tue
    plus les travaux un par un. Ils attendent, et repartent TOUT SEULS à la
    reconnexion — sans que personne ait à les relancer."""
    from oto_mcp import db
    from oto_mcp.db import user_subscriptions as US
    from oto_mcp.db._conn import _connect
    org = 9410 + ["needs_login", "disconnected"].index(etat)
    h = _personne(f"abo-h-{etat}")
    US.upsert_sandbox(h, _FAMILLE, "sandbox-h")
    US.marquer_statut(h, _FAMILLE, etat)
    travail = _travail(org, h)

    assert _claim(org) is None, "personne ne le prend tant qu'elle n'est pas revenue"
    with _connect() as conn:
        ligne = conn.execute("SELECT status, attempts FROM runner_jobs WHERE id = %s",
                             (travail,)).fetchone()
    assert (ligne["status"], ligne["attempts"]) == ("pending", 0), (
        "en ATTENTE : ni échoué, ni tentative brûlée")
    assert db.travaux_en_attente_d_abonnement(h, _FAMILLE) == 1, "et l'écran le dit"

    US.marquer_statut(h, _FAMILLE, US.CONNECTE, ok=True)      # elle se reconnecte
    pris = _claim(org)
    assert pris and pris["id"] == travail, "le travail repart tout seul"


def test_une_prise_rendue_a_la_file_ne_coute_AUCUNE_tentative(live):
    """La course : l'état change entre la prise et la garde. Le travail est rendu,
    pas arrêté — et trois de ces courses ne doivent pas le tuer."""
    from oto_mcp import db
    from oto_mcp.db._conn import _connect
    i = _personne("abo-i")
    travail = _travail(9412, i)
    assert _claim(9412)["id"] == travail
    assert db.rendre_a_la_file(travail, "w-abonnement", "en attente de reconnexion")
    with _connect() as conn:
        ligne = conn.execute(
            "SELECT status, attempts, claimed_by, last_error, due_at > NOW() AS plus_tard "
            "FROM runner_jobs WHERE id = %s", (travail,)).fetchone()
    assert ligne["status"] == "pending" and ligne["attempts"] == 0
    assert ligne["claimed_by"] is None and ligne["plus_tard"]
    assert "reconnexion" in ligne["last_error"], "un travail qui attend dit pourquoi"
    # Un autre worker ne peut pas rendre la prise de quelqu'un d'autre.
    assert db.rendre_a_la_file(travail, "un-autre", "x") is False


def test_un_abonnement_NON_OUVERT_dans_l_org_fait_attendre_puis_part_a_l_ouverture(live):
    """08/10/2026 : une connexion ne sert que les orgs où la personne l'a ouverte. Ses
    travaux d'une autre org ATTENDENT (aucune tentative brûlée) et partent dès
    qu'elle l'y ouvre."""
    from oto_mcp import db
    from oto_mcp.db import user_subscriptions as US
    p = _personne("abo-org-p")
    US.upsert_sandbox(p, _FAMILLE, "sandbox-org-p")
    US.marquer_statut(p, _FAMILLE, US.CONNECTE, ok=True)
    _ouvert_ici(9481, p, _FAMILLE)          # ouverte ici…
    _ouvert_ici(9482, p, "autre_famille")   # …et l'org 9482 existe, sans l'ouvrir
    jid = db.enqueue_job(9482, "start", sub=p,
                         payload={"procedure": "p", "model": "sub:sonnet",
                                  "model_family": _FAMILLE})["id"]

    assert _claim(9482) is None, "pas ouvert dans cette org : le travail attend"
    assert US.sert_dans(p, _FAMILLE, 9481) and not US.sert_dans(p, _FAMILLE, 9482)

    US.poser_orgs_servies(p, _FAMILLE, [9481, 9482])
    assert _claim(9482)["id"] == jid, "ouvert : il part"


def test_se_deconnecter_referme_toutes_les_orgs(live):
    from oto_mcp.db import user_subscriptions as US
    p = _personne("abo-org-q")
    US.upsert_sandbox(p, _FAMILLE, "sandbox-org-q")
    _ouvert_ici(9483, p, _FAMILLE)
    assert US.orgs_servies(p, _FAMILLE) == [9483]
    US.oublier(p, _FAMILLE)
    assert US.orgs_servies(p, _FAMILLE) == []


def test_la_reprise_garde_l_abonnement_LA_OU_IL_SERT(live):
    """La révision 0046 ouvre chaque abonnement existant là où il sert déjà (un agent
    posé dessus, un prêt), et nulle part ailleurs — rien de ce qui tourne ne s'arrête."""
    import importlib

    from oto_mcp.db import user_subscriptions as US
    from oto_mcp.db._conn import _connect
    rev = importlib.import_module(
        "oto_mcp.db.migrations.versions.20261008_0046_abonnement_par_org")
    p = _personne("abo-reprise")
    US.upsert_sandbox(p, _FAMILLE, "sandbox-reprise")
    with _connect() as conn:
        for org in (9491, 9492, 9493):
            conn.execute("INSERT INTO orgs (id, name) VALUES (%s, %s) "
                         "ON CONFLICT (id) DO NOTHING", (org, f"Org {org}"))
            conn.execute("INSERT INTO org_members (org_id, sub) VALUES (%s, %s) "
                         "ON CONFLICT DO NOTHING", (org, p))
        conn.execute("INSERT INTO runner_triggers (org_id, sub, procedure, tools, cron, tz, model) "
                     "VALUES (9491, %s, 'p', '[]', '0 8 * * 1', 'Europe/Paris', 'sub:sonnet')", (p,))
        conn.execute("INSERT INTO user_model_subscription_loans (sub, famille, org_id) "
                     "VALUES (%s, %s, 9492)", (p, _FAMILLE))
        conn.execute(rev._REPRISE)

    assert US.orgs_servies(p, _FAMILLE) == [9491, 9492], "9493 : rien n'y sert, rien d'ouvert"
