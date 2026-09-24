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


def _travail(org, sub, famille=_FAMILLE):
    from oto_mcp import db
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
    assert conclu == {"sub": f, "model_family": _FAMILLE}, (
        "de quoi adresser le rapport à la bonne personne")
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
