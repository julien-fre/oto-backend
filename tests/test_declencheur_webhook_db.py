"""Le déclencheur par webhook EN BASE — ce qu'une doublure ne peut pas prouver.

Quatre choses vivent dans le SQL et nulle part ailleurs :

1. **Le haché est comparé DANS le WHERE.** Une doublure voit qu'on le passe ; seule
   la base montre qu'un mauvais secret ne rend rien.
2. **Le relâchement des `NOT NULL` est sûr sur la base PARTAGÉE.** Un déclencheur
   webhook (`next_due IS NULL`) doit être INVISIBLE au tick de la prod, qui tourne
   l'ancien code pendant la fenêtre de déploiement. C'est l'affirmation de sûreté
   de la migration — elle mérite un banc, pas une promesse.
3. **La fenêtre de lissage compte ce qu'il faut**, et seulement les livraisons qui
   ont enfilé.
4. **Un travail retardé qui a trop attendu PÉRIME** à la réservation, au lieu de
   partir en retard.

Patron de base éphémère repris de `test_runner_workers_db.py`.
"""
from __future__ import annotations

import os
import uuid

import pytest


@pytest.fixture(scope="module")
def live(pg_dsn):
    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    name = "oto_hook_" + uuid.uuid4().hex[:8]
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


ORG = 8100


def _webhook(db, procedure="veille", **kw):
    t = db.create_trigger(ORG, "alexis", procedure=procedure, tz="UTC",
                          tools=["a"], kind="webhook", **kw)
    from oto_mcp import runner_hook
    secret, hache = runner_hook.nouveau_secret()
    db.poser_secret_de_hook(t["id"], ORG, hache)
    return t, secret


# ── 1. le secret, comparé en SQL ──────────────────────────────────────────────

def test_le_bon_secret_trouve_le_declencheur(live):
    from oto_mcp import db, runner_hook
    t, secret = _webhook(db)
    trouve = db.trigger_par_secret(t["id"], runner_hook.hacher(secret))
    assert trouve and trouve["id"] == t["id"] and trouve["kind"] == "webhook"


def test_un_MAUVAIS_secret_ne_trouve_rien(live):
    """⚠️ LE banc de sécurité. La comparaison est dans le WHERE : un prédicat qui
    accepterait n'importe quoi ouvrirait chaque déclencheur à chaque secret."""
    from oto_mcp import db, runner_hook
    t, _ = _webhook(db, procedure="veille-2")
    assert db.trigger_par_secret(t["id"], runner_hook.hacher("otoh_faux")) is None


def test_le_bon_secret_sur_le_MAUVAIS_id_ne_trouve_rien(live):
    from oto_mcp import db, runner_hook
    t, secret = _webhook(db, procedure="veille-3")
    assert db.trigger_par_secret(t["id"] + 9999, runner_hook.hacher(secret)) is None


def test_un_agent_PROGRAMME_n_est_jamais_trouve_par_secret(live):
    """`kind = 'webhook'` est dans le WHERE : un agent programmé n'a pas de porte."""
    from oto_mcp import db, runner_hook
    import datetime
    t = db.create_trigger(ORG, "alexis", procedure="programme", cron="5 6 * * *",
                          tz="UTC", tools=["a"],
                          next_due=datetime.datetime.now(datetime.timezone.utc))
    db.poser_secret_de_hook(t["id"], ORG, runner_hook.hacher("otoh_x"))
    assert db.trigger_par_secret(t["id"], runner_hook.hacher("otoh_x")) is None


def test_un_declencheur_en_PAUSE_est_trouve_quand_meme(live):
    """Il doit l'être pour que la route réponde 409 (« il existe, il est en
    pause ») plutôt que 404 : c'est une information que son propriétaire a le
    droit de recevoir — c'est lui qui a donné le secret à la source."""
    from oto_mcp import db, runner_hook
    t, secret = _webhook(db, procedure="veille-pause")
    db.update_trigger(t["id"], ORG, {"enabled": False})
    trouve = db.trigger_par_secret(t["id"], runner_hook.hacher(secret))
    assert trouve and trouve["enabled"] is False


# ── 2. la migration est sûre sur la base partagée ─────────────────────────────

def test_un_webhook_est_INVISIBLE_au_tick(live):
    """⚠️ L'affirmation de sûreté de la migration, éprouvée plutôt que promise.

    `cron` et `next_due` deviennent NULLABLES. Pendant la fenêtre de déploiement,
    la PROD tourne l'ancien code et son tick lit
    `WHERE enabled AND next_due <= NOW()` — qu'un NULL ne satisfait jamais. Une
    ligne webhook lui est donc invisible, jamais mal traitée. Si ce banc rougit,
    la migration n'est pas déployable."""
    from oto_mcp import db
    t, _ = _webhook(db, procedure="veille-invisible")
    dus = [d["id"] for d in db.due_triggers(limit=500)]
    assert t["id"] not in dus, (
        "un déclencheur webhook ne doit JAMAIS être sélectionné par le tick")


def test_un_agent_programme_reste_VU_par_le_tick(live):
    """Le bord opposé : sans lui, le banc ci-dessus passerait si le tick ne voyait
    plus rien du tout."""
    from oto_mcp import db
    import datetime
    hier = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=1)
    t = db.create_trigger(ORG, "alexis", procedure="programme-du", cron="5 6 * * *",
                          tz="UTC", tools=["a"], next_due=hier)
    assert t["id"] in [d["id"] for d in db.due_triggers(limit=500)]


# ── 3. la fenêtre de lissage ──────────────────────────────────────────────────

def test_la_fenetre_compte_les_livraisons_ENFILEES(live):
    from oto_mcp import db
    t, _ = _webhook(db, procedure="veille-fenetre")
    with db._connect() as conn:
        for _ in range(3):
            db.enregistrer(conn, t["id"], ORG, db.QUEUED, job_id=1)
        db.enregistrer(conn, t["id"], ORG, db.DELAYED, job_id=2)
        # Un refus n'a rien coûté : le faire compter retarderait des travaux
        # légitimes à cause d'une source qui présente un mauvais secret.
        db.enregistrer(conn, t["id"], ORG, db.REFUSE_PAUSED)
        assert db.compter_dans_la_fenetre(conn, t["id"], 3600) == 4


def test_la_fenetre_ne_voit_pas_au_dela_de_son_horizon(live):
    from oto_mcp import db
    t, _ = _webhook(db, procedure="veille-horizon")
    with db._connect() as conn:
        db.enregistrer(conn, t["id"], ORG, db.QUEUED, job_id=1)
        conn.execute("UPDATE runner_hook_deliveries SET received_at = NOW() - "
                     "INTERVAL '2 hours' WHERE trigger_id = %s", (t["id"],))
        assert db.compter_dans_la_fenetre(conn, t["id"], 3600) == 0
        assert db.compter_dans_la_fenetre(conn, t["id"], 10800) == 1


def test_la_fenetre_est_par_DECLENCHEUR(live):
    """Le lissage d'un agent ne doit pas retarder celui d'un autre."""
    from oto_mcp import db
    a, _ = _webhook(db, procedure="veille-a")
    b, _ = _webhook(db, procedure="veille-b")
    with db._connect() as conn:
        for _ in range(5):
            db.enregistrer(conn, a["id"], ORG, db.QUEUED, job_id=1)
        assert db.compter_dans_la_fenetre(conn, b["id"], 3600) == 0


def test_les_livraisons_se_lisent_et_se_comptent(live):
    from oto_mcp import db
    t, _ = _webhook(db, procedure="veille-journal")
    with db._connect() as conn:
        db.enregistrer(conn, t["id"], ORG, db.QUEUED, job_id=7, source="n8n/1.0")
        db.enregistrer(conn, t["id"], ORG, db.REFUSE_PAUSED)
    lues = db.livraisons(t["id"], ORG)
    assert [l["outcome"] for l in lues] == [db.REFUSE_PAUSED, db.QUEUED]
    assert lues[1]["job_id"] == 7 and lues[1]["source"] == "n8n/1.0"
    compte = db.comptage_livraisons(t["id"], ORG)
    assert compte == {"recues_24h": 2, "refusees_24h": 1,
                      "derniere": compte["derniere"]}
    assert compte["derniere"] is not None


def test_les_livraisons_d_une_AUTRE_org_sont_invisibles(live):
    from oto_mcp import db
    t, _ = _webhook(db, procedure="veille-autre-org")
    with db._connect() as conn:
        db.enregistrer(conn, t["id"], ORG, db.QUEUED, job_id=1)
    assert db.livraisons(t["id"], ORG + 1) == []
    assert db.comptage_livraisons(t["id"], ORG + 1)["recues_24h"] == 0


def test_deux_livraisons_SIMULTANEES_ne_lisent_pas_le_meme_compte(live):
    """⚠️ Le banc qu'aucun test mono-fil ne peut rendre.

    Une rafale est CONCURRENTE par définition. Sans le verrou sur la ligne du
    déclencheur, deux livraisons simultanées lisent en READ COMMITTED le même
    compte (zéro), se croient toutes deux sous le débit, et partent ensemble : le
    lissage serait inerte exactement au moment où il sert.

    Ici : A prend le verrou et écrit ; B doit ATTENDRE, puis voir le travail de A.
    """
    import threading
    from oto_mcp import db
    t, _ = _webhook(db, procedure="veille-concurrente")

    vus, erreurs = [], []
    depart = threading.Barrier(2)
    a_ecrit = threading.Event()

    def livrer(tag, lent):
        try:
            with db._connect() as conn:
                db.verrouiller_le_declencheur(conn, t["id"])
                depart.wait(timeout=5) if lent else None
                vus.append((tag, db.compter_dans_la_fenetre(conn, t["id"], 3600)))
                db.enregistrer(conn, t["id"], ORG, db.QUEUED, job_id=1)
                if lent:
                    a_ecrit.set()
        except Exception as e:  # noqa: SILENT — relayé par l'assert final
            erreurs.append(f"{tag}: {e!r}")

    A = threading.Thread(target=livrer, args=("A", True))
    A.start()
    depart.wait(timeout=5)
    B = threading.Thread(target=livrer, args=("B", False))
    B.start()
    A.join(timeout=10); B.join(timeout=10)

    assert not erreurs, erreurs
    comptes = sorted(n for _, n in vus)
    assert comptes == [0, 1], (
        f"les deux livraisons ont lu {comptes} — sans sérialisation elles "
        "liraient toutes deux 0 et la rafale passerait entière")


# ── 4. le retard et la péremption, à la réservation ───────────────────────────

def test_un_travail_RETARDE_n_est_pas_reservable_avant_l_heure(live):
    from oto_mcp import db
    db.enqueue_job(8201, "start", payload={"procedure": "p"}, delai_s=3600)
    assert db.claim_next_job(8201, "w", lease_seconds=60) is None, (
        "un travail lissé ne part pas avant son heure")


def test_sans_delai_le_travail_part_tout_de_suite(live):
    from oto_mcp import db
    j = db.enqueue_job(8202, "start", payload={"procedure": "p"})
    pris = db.claim_next_job(8202, "w", lease_seconds=60)
    assert pris and pris["id"] == j["id"]


def test_un_travail_trop_VIEUX_perime_au_lieu_de_partir(live):
    """⚠️ Ce qui empêche le lissage de devenir un ARRIÉRÉ. Sans lui, une rafale
    retardée se déverserait le lendemain et un agent traiterait l'événement
    d'hier comme s'il venait d'arriver — un résultat faux, pas tardif."""
    from oto_mcp import db
    j = db.enqueue_job(8203, "start", payload={"procedure": "p"}, perime_apres_s=60)
    with db._connect() as conn:
        conn.execute("UPDATE runner_jobs SET created_at = NOW() - INTERVAL '2 hours' "
                     "WHERE id = %s", (j["id"],))
    assert db.claim_next_job(8203, "w", lease_seconds=60) is None
    assert db.get_job(j["id"], 8203)["status"] == "expired"


def test_un_travail_FRAIS_avec_une_peremption_part_normalement(live):
    """Le bord opposé : la péremption ne doit pas manger ce qui est à l'heure."""
    from oto_mcp import db
    j = db.enqueue_job(8204, "start", payload={"procedure": "p"}, perime_apres_s=3600)
    pris = db.claim_next_job(8204, "w", lease_seconds=60)
    assert pris and pris["id"] == j["id"]


def test_un_travail_SANS_peremption_ne_perime_jamais(live):
    """Tout ce qui existait avant ce lot : aucune charge ne porte la clé."""
    from oto_mcp import db
    j = db.enqueue_job(8205, "start", payload={"procedure": "p"})
    with db._connect() as conn:
        conn.execute("UPDATE runner_jobs SET created_at = NOW() - INTERVAL '30 days' "
                     "WHERE id = %s", (j["id"],))
    pris = db.claim_next_job(8205, "w", lease_seconds=60)
    assert pris and pris["id"] == j["id"]
