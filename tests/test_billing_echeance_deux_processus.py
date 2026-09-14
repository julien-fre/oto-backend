"""Deux processus de production, une échéance : un seul appel au prestataire, une seule clé.

Pendant une bascule bleu/vert — ou si l'ancienne unité simple revient, relancée par un
autre déploiement (relevé le 10/09/2026) — deux processus de production tournent sur la
même base. `due_subscriptions` sélectionne sans verrou : les deux prenaient la même
échéance, le second comptait la ligne `processing` du premier, prenait la tentative `a2`,
donc une autre clé d'idempotence — et Mollie acceptait un second débit réel.

Deux mécanismes, deux épreuves :
- la RÉSERVATION (`db/billing_reservation.py`) : un seul processus appelle le prestataire ;
- la CLÉ dérivée de la ligne (`billing_runner._cle_echeance`), le filet si la réservation
  manque : les deux processus envoient la même clé, et le prestataire ne débite qu'une fois.

Le banc lance de VRAIS processus (`subprocess`, interpréteurs neufs) sur une vraie base
PostgreSQL (`pg_module_dsn`). Le prestataire simulé vit dans cette base, partagé par les
deux : il fait ce que la doc de Mollie décrit (« Idempotency ») — une clé déjà reçue rend
le MÊME paiement, ou un 409 tant que la première requête est en cours. Un débit = une clé
nouvelle ; un appel = une requête reçue.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from oto_mcp import billing_runner
from oto_mcp.db import billing as db_billing
from oto_mcp.db import billing_reservation
from oto_mcp.mollie_client import MollieError

psycopg = pytest.importorskip("psycopg")

RACINE = Path(__file__).resolve().parent.parent
IDENTITE = dict(legal_name="ACME SAS", country_code="FR", address_line="1 rue de la Paix",
                postal_code="13001", city="Marseille")


# ══ 1. la clé et le filet, sans base ═════════════════════════════════════════

def _ligne(**sur) -> dict:
    """Une échéance telle que la base la rend : dates normalisées par le row factory."""
    return {"org_id": 219, "plan": "standard", "method": "card", "status": "active",
            "customer_id": "cst_1", "mandate_id": "mdt_1",
            "current_period_end": "2026-09-25 10:36:26",
            "next_billing_at": "2026-09-25 10:36:26", **sur}


@pytest.fixture
def monde(monkeypatch):
    """Le store et le prestataire simulés ; la réservation obtenue, rien n'a bougé."""
    etat = {"cles": [], "journal": [], "maj": [], "cycle": [], "relance": [],
            "statut": [], "compte": 0, "deja": None, "reponse": None}
    monkeypatch.setattr(db_billing, "get_billing_identity", lambda org: IDENTITE)
    monkeypatch.setattr(db_billing, "count_renewal_attempts",
                        lambda org, since: etat["compte"])
    monkeypatch.setattr(db_billing, "insert_billing_payment",
                        lambda *a, **k: etat["journal"].append(a) or 11)
    monkeypatch.setattr(db_billing, "update_billing_payment",
                        lambda rid, **k: etat["maj"].append((rid, k)) or True)
    monkeypatch.setattr(db_billing, "schedule_next_billing",
                        lambda *a: etat["cycle"].append(a) or True)
    monkeypatch.setattr(db_billing, "retry_billing_at",
                        lambda *a: etat["relance"].append(a) or True)
    monkeypatch.setattr(db_billing, "set_subscription_status",
                        lambda *a, **k: etat["statut"].append(a) or True)
    monkeypatch.setattr(db_billing, "get_billing_payment_by_ref", lambda ref: etat["deja"])
    monkeypatch.setattr(billing_reservation, "reserver_echeance",
                        lambda row: nullcontext(row))

    def psp(montant, *, idempotency_key, **_):
        etat["cles"].append(idempotency_key)
        if isinstance(etat["reponse"], Exception):
            raise etat["reponse"]
        return etat["reponse"] or {"id": "tr_1", "status": "paid"}

    monkeypatch.setattr(billing_runner.mollie_client, "create_recurring_payment", psp)
    return etat


NOW = datetime(2026, 9, 25, 11, 0, tzinfo=timezone.utc)


def test_la_cle_se_lit_sur_la_ligne():
    cle = billing_runner._cle_echeance(_ligne(), "2026-09-25")
    assert cle == "org219-2026-09-25-d20260925103626"
    # Même instant porté par un `datetime` (un test) ou par la chaîne de la base : mêmes chiffres.
    assert billing_runner._cle_echeance(
        _ligne(next_billing_at=datetime(2026, 9, 25, 10, 36, 26, tzinfo=timezone.utc)),
        "2026-09-25") == cle
    # Une relance à J+3 déplace l'instant dû : nouvelle tentative, nouvelle clé.
    assert billing_runner._cle_echeance(
        _ligne(next_billing_at="2026-09-28 11:00:00"), "2026-09-25") != cle


def test_la_cle_ne_depend_pas_du_compte_des_tentatives(monde):
    # Le compte voit la ligne `processing` d'un processus concurrent : il vaut 0 pour le
    # premier, 1 pour le second. C'est ce qui donnait `a1` puis `a2`, donc deux débits.
    billing_runner._charge_one(_ligne(), NOW)
    monde["compte"] = 1
    billing_runner._charge_one(_ligne(), NOW)
    assert len(monde["cles"]) == 2 and len(set(monde["cles"])) == 1, monde["cles"]


def test_sans_instant_du_rien_n_est_tire(monde):
    with pytest.raises(RuntimeError, match="sans instant dû"):
        billing_runner._charge_one(_ligne(next_billing_at=None), NOW)
    assert monde["cles"] == [] and monde["journal"] == []


def test_une_echeance_tenue_ailleurs_ne_part_pas(monde, monkeypatch):
    monkeypatch.setattr(billing_reservation, "reserver_echeance",
                        lambda row: nullcontext(None))
    assert billing_runner._charge_one(_ligne(), NOW) == "busy"
    assert monde["cles"] == [] and monde["journal"] == [] and monde["maj"] == []


def test_un_409_ne_touche_ni_au_cycle_ni_a_l_impaye(monde):
    # La requête jumelle est en cours chez Mollie. Écrire `failed` + relance à J+3 ici,
    # APRÈS l'encaissement de l'autre, ramènerait l'échéance suivante dans trois jours.
    monde["reponse"] = MollieError(409, "A request with this idempotency key is in progress")
    assert billing_runner._charge_one(_ligne(), NOW) == "busy"
    assert monde["maj"] == [(11, {"status": "canceled"})]
    assert monde["relance"] == [] and monde["statut"] == [] and monde["cycle"] == []


def test_une_reponse_rejouee_deja_journalisee_ne_compte_pas_deux_fois(monde):
    # Mollie rend le paiement créé par l'autre tentative : il est déjà au journal.
    monde["deja"] = {"id": 5, "payment_id": "tr_1", "status": "paid"}
    assert billing_runner._charge_one(_ligne(), NOW) == "busy"
    assert monde["maj"] == [(11, {"status": "canceled"})]
    assert monde["cycle"] == [], "le cycle a déjà été avancé par la tentative qui a débité"


def test_un_autre_refus_reste_un_echec(monde):
    # Le 409 est le seul refus traité en doublon ; une carte refusée reste un échec.
    monde["reponse"] = MollieError(422, "declined")
    assert billing_runner._charge_one(_ligne(), NOW) == "retry"
    assert monde["maj"] == [(11, {"status": "failed"})] and monde["relance"]


# ══ 2. le banc : deux vrais processus, une vraie base ════════════════════════

_PSP = """
CREATE TABLE psp_appels (id BIGSERIAL PRIMARY KEY, cle TEXT NOT NULL, pid INT NOT NULL);
CREATE TABLE psp_debits (cle TEXT PRIMARY KEY, paiement TEXT NOT NULL,
                         fini BOOLEAN NOT NULL DEFAULT FALSE);
"""

# Le processus de production, réduit à ce qui compte : il sélectionne les échéances dues,
# attend le signal, puis les tire par le vrai `_charge_one`. Seul le prestataire est simulé.
_ENFANT = r'''
import json, os, sys, time
from datetime import datetime, timezone
from pathlib import Path

import psycopg

from oto_mcp import billing_runner
from oto_mcp.db import billing as db_billing

rdv, nom = Path(os.environ["BANC_RDV"]), os.environ["BANC_NOM"]
org, dsn = int(os.environ["BANC_ORG"]), os.environ["DATABASE_URL"]
lent = float(os.environ["BANC_PSP_LENT"])


def psp(montant, *, idempotency_key, **_):
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute("INSERT INTO psp_appels (cle, pid) VALUES (%s, %s)",
                  (idempotency_key, os.getpid()))
        neuf = c.execute(
            "INSERT INTO psp_debits (cle, paiement) VALUES (%s, %s) "
            "ON CONFLICT (cle) DO NOTHING RETURNING paiement",
            (idempotency_key, "tr_" + idempotency_key)).fetchone()
        if neuf is None:
            fini, paiement = c.execute(
                "SELECT fini, paiement FROM psp_debits WHERE cle = %s",
                (idempotency_key,)).fetchone()
            if not fini:
                raise billing_runner.mollie_client.MollieError(409, "request in progress")
            return {"id": paiement, "status": "paid", "mode": "live"}
        time.sleep(lent)
        c.execute("UPDATE psp_debits SET fini = TRUE WHERE cle = %s", (idempotency_key,))
        return {"id": neuf[0], "status": "paid", "mode": "live"}


billing_runner.mollie_client.create_recurring_payment = psp
if os.environ.get("BANC_SANS_RESERVATION") == "1":
    # Le filet seul : la réservation « obtenue » sans verrou, la ligne telle que lue.
    from contextlib import nullcontext
    from oto_mcp.db import billing_reservation
    billing_reservation.reserver_echeance = lambda ligne: nullcontext(ligne)


lignes = [l for l in db_billing.due_subscriptions() if l["org_id"] == org]
if not lignes:
    # Rouge intermittent le 14/09/2026 sous CI parallèle (pytest-xdist -n 4) :
    # `test_sans_reservation_la_meme_cle_ne_debite_qu_une_fois[perime]` a lu 0 ici.
    # ⚠️ Un premier correctif (une boucle de poll) a été écarté par relecture (cd,
    # 14/09) : il SUPPOSAIT une ligne pas encore visible, alors que le parent la
    # committe avant le `Popen` — une absence à cet instant n'est donc PAS un
    # « pas encore là », et une boucle qui la masquerait cacherait un défaut RÉEL
    # sans jamais le nommer. Ce qui suit ne corrige rien : ça consigne, pour que
    # le PROCHAIN rouge s'explique de lui-même au lieu de rouvrir la même enquête.
    with psycopg.connect(dsn, autocommit=True) as c:
        horloge = c.execute("SELECT NOW()").fetchone()[0]
        brute = c.execute(
            "SELECT status, next_billing_at, current_period_end FROM "
            "org_subscriptions WHERE org_id = %s", (org,)).fetchone()
    (rdv / f"diag-{nom}.json").write_text(json.dumps({
        "pid": os.getpid(), "org": org, "dsn": dsn,
        "now_transaction_enfant": str(horloge),
        "ligne_brute_org_subscriptions": list(brute) if brute else None,
    }, default=str))
(rdv / f"lu-{nom}").write_text(str(len(lignes)))
limite = time.monotonic() + 90
while not (rdv / f"go-{nom}").exists():
    if time.monotonic() > limite:
        sys.exit("rendez-vous manqué")
    time.sleep(0.01)
print(json.dumps([billing_runner._charge_one(l, datetime.now(timezone.utc))
                  for l in lignes]))
sys.stdout.flush()
# Sortie immédiate : le pool ouvert par le store retenait l'interpréteur une vingtaine de
# secondes à la fermeture (mesuré le 10/09/2026), sans rien apporter à l'épreuve.
os._exit(0)
'''


@pytest.fixture(scope="module")
def base(pg_module_dsn):
    avant = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = pg_module_dsn
    try:
        from oto_mcp.db import init_db
        init_db()
        with psycopg.connect(pg_module_dsn, autocommit=True) as c:
            c.execute(_PSP)
        yield pg_module_dsn
    finally:
        if avant is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = avant


def _echeance_due() -> int:
    """Une org, son identité de facturation, un abonnement échu depuis une heure."""
    from oto_mcp.db._conn import _connect

    with _connect() as conn:
        org = conn.execute("INSERT INTO orgs (name) VALUES ('banc-echeance') "
                           "RETURNING id").fetchone()["id"]
    db_billing.upsert_billing_identity(org, **IDENTITE)
    du = (datetime.now(timezone.utc) - timedelta(hours=1)).replace(microsecond=0)
    db_billing.upsert_org_subscription(org, plan="standard", customer_id="cst_banc",
                                       mandate_id="mdt_banc", status="active",
                                       current_period_end=du, next_billing_at=du)
    return org


def _lancer(dsn, rdv, nom, org, *, lent, sans_reservation):
    env = {**os.environ, "DATABASE_URL": dsn, "BANC_RDV": str(rdv), "BANC_NOM": nom,
           "BANC_ORG": str(org), "BANC_PSP_LENT": str(lent),
           "OTO_MCP_PUBLIC_URL": "https://mcp.oto.cx", "OTO_CONFIG_DISABLE_SOPS": "1",
           "PYTHONPATH": os.pathsep.join(
               p for p in (str(RACINE), os.environ.get("PYTHONPATH", "")) if p)}
    if sans_reservation:
        env["BANC_SANS_RESERVATION"] = "1"
    return subprocess.Popen([sys.executable, "-c", _ENFANT], cwd=RACINE, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def _issues(p) -> list:
    out, err = p.communicate(timeout=120)
    assert p.returncode == 0, err[-4000:]
    return json.loads(out.strip().splitlines()[-1])


def _attendre_en_vol(dsn, org):
    """Jusqu'à ce que l'appel de A soit EN COURS chez le prestataire."""
    limite = time.monotonic() + 60
    with psycopg.connect(dsn, autocommit=True) as c:
        while not c.execute("SELECT 1 FROM psp_debits WHERE cle LIKE %s AND NOT fini",
                            (f"org{org}-%",)).fetchone():
            assert time.monotonic() < limite, "l'appel de A n'a jamais été vu en cours"
            time.sleep(0.01)


def _jouer(dsn, rdv, *, ordre, sans_reservation=False):
    org = _echeance_due()
    lent = 0.0 if ordre == "perime" else 3.0
    procs = {n: _lancer(dsn, rdv, n, org, lent=lent, sans_reservation=sans_reservation)
             for n in ("A", "B")}
    limite = time.monotonic() + 120
    while not all((rdv / f"lu-{n}").exists() for n in procs):
        for n, p in procs.items():
            if p.poll() is not None and not (rdv / f"lu-{n}").exists():
                pytest.fail(f"processus {n} mort avant d'avoir lu : "
                            f"{p.communicate()[1][-4000:]}")
        assert time.monotonic() < limite, "un processus n'a jamais lu ses échéances"
        time.sleep(0.02)
    diagnostics = {n: (rdv / f"diag-{n}.json").read_text()
                  for n in procs if (rdv / f"diag-{n}.json").exists()}
    assert [(rdv / f"lu-{n}").read_text() for n in procs] == ["1", "1"], (
        "chacun doit avoir sélectionné l'échéance avant que l'autre ne la tire"
        + (f" — diagnostic : {diagnostics}" if diagnostics else ""))
    if ordre == "simultane":
        (rdv / "go-A").touch()
        (rdv / "go-B").touch()
        issues = _issues(procs["A"]) + _issues(procs["B"])
    elif ordre == "en_vol":
        # B part pendant que l'appel de A est en cours : le moment exact où le compte des
        # tentatives de B voyait la ligne `processing` de A, et lui donnait `a2`.
        (rdv / "go-A").touch()
        _attendre_en_vol(dsn, org)
        (rdv / "go-B").touch()
        issues = _issues(procs["A"]) + _issues(procs["B"])
    else:
        # « périmé » : B a lu l'échéance AVANT que A la tire, et la tire APRÈS.
        (rdv / "go-A").touch()
        issues = _issues(procs["A"])
        (rdv / "go-B").touch()
        issues += _issues(procs["B"])
    with psycopg.connect(dsn, autocommit=True) as c:
        motif = f"org{org}-%"
        appels = [r[0] for r in c.execute(
            "SELECT cle FROM psp_appels WHERE cle LIKE %s ORDER BY id", (motif,))]
        debits = c.execute("SELECT count(*) FROM psp_debits WHERE cle LIKE %s",
                           (motif,)).fetchone()[0]
        journal = [r[0] for r in c.execute(
            "SELECT status FROM billing_payments WHERE org_id = %s AND kind = 'renewal'",
            (org,))]
    return sorted(issues), appels, debits, sorted(journal)


@pytest.mark.parametrize("ordre", ["simultane", "en_vol", "perime"])
def test_deux_processus_une_echeance_un_seul_appel(base, tmp_path, ordre):
    issues, appels, debits, journal = _jouer(base, tmp_path, ordre=ordre)
    assert len(appels) == 1, f"le prestataire a été appelé {len(appels)} fois : {appels}"
    assert debits == 1
    assert issues == ["busy", "renewed"]
    assert journal == ["paid"], "le processus écarté n'écrit rien au journal"


@pytest.mark.parametrize("ordre", ["simultane", "en_vol", "perime"])
def test_sans_reservation_la_meme_cle_ne_debite_qu_une_fois(base, tmp_path, ordre):
    """Le filet seul : la réservation neutralisée, les deux processus appellent."""
    issues, appels, debits, journal = _jouer(base, tmp_path, ordre=ordre,
                                             sans_reservation=True)
    assert len(appels) == 2, "sans réservation, les deux processus appellent"
    assert len(set(appels)) == 1, f"deux clés pour une même échéance : {appels}"
    assert debits == 1, f"{debits} débits pour une échéance"
    assert issues == ["busy", "renewed"]
    assert journal == ["canceled", "paid"]
