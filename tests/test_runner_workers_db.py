"""Les workers de plateforme en base, exercés en SQL RÉEL.

Déclarer, reconnaître, révoquer : trois requêtes, et un piège — la table
servait déjà de témoin de PRÉSENCE (écrite par `claim_next_job(None, …)`), et
l'upsert de présence ne doit pas effacer ce qu'on vient d'y déclarer. Patron de
base éphémère repris de `test_campagne_a_servir_db.py`.
"""
from __future__ import annotations

import os
import uuid
from contextlib import contextmanager
from unittest import mock

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


def test_declarer_puis_reconnaitre(live):
    from oto_mcp import db
    w = db.create_platform_worker("banc 1")
    assert w["secret"].startswith("otow_") and w["worker_sub"].startswith("worker:")

    vu = db.verify_worker_secret(w["secret"])

    assert vu == {"worker_sub": w["worker_sub"], "label": "banc 1"}


def test_le_secret_n_est_pas_stocke_en_clair(live):
    import psycopg
    from oto_mcp import db
    w = db.create_platform_worker("banc clair")
    with psycopg.connect(os.environ["DATABASE_URL"]) as c:
        row = c.execute("SELECT secret_hash FROM runner_platform_workers WHERE worker_sub=%s",
                        (w["worker_sub"],)).fetchone()
    assert row[0] != w["secret"] and len(row[0]) == 64


def test_un_secret_inconnu_ou_sans_prefixe_ne_reconnait_personne(live):
    from oto_mcp import db
    assert db.verify_worker_secret("otow_" + "x" * 40) is None
    assert db.verify_worker_secret("oto_" + "x" * 40) is None
    assert db.verify_worker_secret("") is None


def test_revoquer_ferme_l_authentification_et_ne_se_repete_pas(live):
    from oto_mcp import db
    w = db.create_platform_worker("banc révoqué")
    assert db.revoke_platform_worker(w["worker_sub"]) is True
    assert db.verify_worker_secret(w["secret"]) is None, "révoqué = inconnu pour l'auth"
    assert db.revoke_platform_worker(w["worker_sub"]) is False, (
        "une seconde révocation n'est pas un succès — c'est « déjà fait »")
    assert db.revoke_platform_worker("worker:inconnu") is False


def test_declare_mais_jamais_vu_ne_compte_PAS_comme_present(live):
    """`runner_arme` lit `last_seen_at` : un worker déclaré il y a dix secondes
    et jamais lancé aurait armé toutes les orgs pendant la fenêtre."""
    from oto_mcp import db
    db.create_platform_worker("banc jamais vu")
    ligne = [w for w in db.list_platform_workers() if w["label"] == "banc jamais vu"][0]
    # La couche db rend les dates en TEXTE : on lit l'année en tête.
    assert str(ligne["last_seen_at"]).startswith("1970")
    assert ligne["declared"] is True


def test_la_presence_n_efface_pas_la_declaration(live):
    """`claim_next_job` (17/09/2026, seul point d'écriture de la présence
    depuis la revue oto cd) écrit la présence par un upsert sur la MÊME ligne
    que la déclaration : il ne doit toucher que `last_seen_at`."""
    from oto_mcp import db
    w = db.create_platform_worker("banc présence")
    db.claim_next_job(None, w["worker_sub"], lease_seconds=60)
    ligne = [x for x in db.list_platform_workers() if x["worker_sub"] == w["worker_sub"]][0]
    assert not str(ligne["last_seen_at"]).startswith("1970")
    assert ligne["label"] == "banc présence"


def test_verify_worker_secret_authentifie_sans_ecrire(live):
    """oto-backend, lot perf 17/09/2026 (mesuré par oto cd, suite du lot posé
    sur `claim_next_job`) : `verify_worker_secret` authentifiait CHAQUE appel
    d'un worker (`take`/`beat`/`complete`/le sondage…) par un `UPDATE …
    RETURNING` synchrone sur l'UNIQUE ligne que partagent toutes les unités
    d'une même machine (12 `oto-runner@N` sur un seul secret) — 31 attentes de
    verrou mesurées sur 150 instantanés de 30s, hors du lot déjà posé sur la
    réservation. C'est maintenant une lecture PURE : elle n'écrit jamais
    `last_seen_at`, quel que soit le nombre d'appels — la marque de présence
    vit exclusivement dans `claim_next_job::_touch_platform_worker_presence`,
    le seul chemin commun à un worker de plateforme ET à un worker à jeton
    d'org (qui n'appelle jamais `verify_worker_secret`).

    Deux appels authentifiés consécutifs : ni l'un ni l'autre n'écrit, et le
    worker reste authentifié aux deux. Comparaison de VALEUR, pas de durée."""
    from oto_mcp import db

    w = db.create_platform_worker("banc auth sans écriture")
    avant = [x for x in db.list_platform_workers()
             if x["worker_sub"] == w["worker_sub"]][0]["last_seen_at"]
    assert str(avant).startswith("1970"), "jamais sondé : la déclaration seule ne marque rien"

    premier = db.verify_worker_secret(w["secret"])
    assert premier == {"worker_sub": w["worker_sub"], "label": "banc auth sans écriture"}
    second = db.verify_worker_secret(w["secret"])
    assert second == premier, "le worker reste authentifié, appel après appel"

    apres = [x for x in db.list_platform_workers()
             if x["worker_sub"] == w["worker_sub"]][0]["last_seen_at"]
    assert apres == avant, "verify_worker_secret ne doit JAMAIS écrire last_seen_at"


def test_la_presence_ne_partage_pas_la_transaction_de_reservation(live):
    """oto-backend, lot perf 17/09/2026 (mesuré par oto cd) : `runner_platform_workers`
    ne porte qu'UNE ligne par worker, partagée par toutes les unités qui présentent
    le même secret. Poser l'upsert de présence DANS la transaction de réservation
    (`FOR UPDATE SKIP LOCKED`) tient le verrou de cette ligne pendant toute la
    réservation — 12 unités passent alors une par une. La présence doit être sa
    PROPRE connexion, committée AVANT que la réservation n'ouvre la sienne.

    Banc structurel plutôt que temporel (pas de seuil de durée) : il rejoue
    `claim_next_job` en espionnant `_connect()` et exige deux ouvertures
    JAMAIS imbriquées — la présence se ferme avant que la réservation n'ouvre.
    Il rougit si quelqu'un remet l'upsert dans la même transaction (une seule
    ouverture) ou s'il en imbrique deux (une réservation ouverte pendant que la
    présence l'est encore)."""
    from oto_mcp.db import runner_jobs as RJ
    from oto_mcp import db

    w = db.create_platform_worker("banc ordre")

    evenements = []
    reel = RJ._connect

    @contextmanager
    def espion():
        evenements.append("open")
        with reel() as conn:
            yield conn
        evenements.append("close")

    with mock.patch.object(RJ, "_connect", espion):
        db.claim_next_job(None, w["worker_sub"], lease_seconds=60)

    assert evenements == ["open", "close", "open", "close"], (
        "la connexion de présence doit se FERMER avant que celle de réservation "
        f"n'OUVRE — séquence observée : {evenements}")


def test_la_marque_de_presence_est_evitee_sous_30s(live):
    """Sous la granularité (30s), la seconde marque ne réécrit pas `last_seen_at` —
    une écriture évitée ne prend aucun verrou. Comparaison de VALEUR, pas de
    durée : deux sondages rapprochés doivent laisser la même valeur en base."""
    from oto_mcp import db

    w = db.create_platform_worker("banc granularité")
    db.claim_next_job(None, w["worker_sub"], lease_seconds=60)
    avant = [x for x in db.list_platform_workers()
             if x["worker_sub"] == w["worker_sub"]][0]["last_seen_at"]

    db.claim_next_job(None, w["worker_sub"], lease_seconds=60)
    apres = [x for x in db.list_platform_workers()
             if x["worker_sub"] == w["worker_sub"]][0]["last_seen_at"]

    assert avant == apres, "sous 30s, la seconde marque ne doit pas réécrire last_seen_at"
