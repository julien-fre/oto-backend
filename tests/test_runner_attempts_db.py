"""La tentative EN BASE — ce qu'une doublure ne peut pas prouver.

1. **une donnée empoisonnée n'avorte pas la transaction de l'appelant** : l'écriture
   voisine (la réservation, la conclusion) est bien COMMISE ;
2. **une vraie erreur de base n'est PAS avalée** : la transaction échoue, et
   l'écriture voisine est annulée avec elle — la tentative est le socle du fencing ;
3. **une seule tentative ouverte par travail**, une conclusion rejouée ne recompte
   rien, un complément tardif ne remplit que ce qui manque ;
4. **aucune clé étrangère, aucun identifiant de personne, des types alignés sur leurs
   sources**, et aucun historique reconstitué.
"""
from __future__ import annotations

import itertools
import os
import uuid

import pytest

from oto_mcp import runner_prix
from oto_mcp.db import runner_attempts as RA

ORG = 7100
_JOBS = itertools.count(910_000)
ATTESTE = {"usage_input": 1000, "usage_output": 200, "usage_cache_read": 50,
           "usage_cache_write": 10, "usage_input_total": 1060, "steps": 3,
           "usage_couverture": {"tours": 3, "declares": {"input_tokens": 3}},
           "stopped": "end_turn", "model": "claude-opus-5"}


@pytest.fixture(scope="module")
def base(pg_module_dsn):
    avant = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = pg_module_dsn
    try:
        from oto_mcp.db import init_db
        init_db()
        yield
    finally:
        if avant is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = avant


def _ouvrir(conn, **kw) -> dict:
    kw.setdefault("job_id", next(_JOBS))
    kw.setdefault("attempt_no", 1)
    kw.setdefault("org_id", ORG)
    kw.setdefault("worker_sub", "worker:banc")
    kw.setdefault("provider_family", "anthropic")
    return RA.ouvrir_tentative(conn, **kw)


def _travail(conn, statut: str = "claimed") -> int:
    return conn.execute(
        "INSERT INTO runner_jobs (org_id, kind, payload, status, attempts) "
        "VALUES (%s, 'start', '{}'::jsonb, %s, 1) RETURNING id", (ORG, statut)).fetchone()["id"]


def _tentatives(job_id: int) -> list[dict]:
    from oto_mcp import db
    with db._connect() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM runner_job_attempts WHERE job_id = %s ORDER BY attempt_no",
            (job_id,)).fetchall()]


# ── 4. la forme de la table ───────────────────────────────────────────────────

def test_la_table_n_a_ni_cle_etrangere_ni_identifiant_de_personne(base):
    from oto_mcp import db
    with db._connect() as c:
        fk = c.execute(
            "SELECT COUNT(*)::int AS n FROM information_schema.table_constraints "
            "WHERE table_name = 'runner_job_attempts' AND constraint_type = 'FOREIGN KEY'"
        ).fetchone()["n"]
        colonnes = {r["column_name"] for r in c.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'runner_job_attempts'").fetchall()}
    assert fk == 0
    assert "sub" not in colonnes and not {"owner_sub", "created_by", "email"} & colonnes


@pytest.mark.parametrize("colonne,table_source,colonne_source", [
    ("job_id", "runner_jobs", "id"), ("attempt_no", "runner_jobs", "attempts"),
    ("org_id", "runner_jobs", "org_id"), ("fleet_id", "runner_fleets", "id"),
    ("trigger_id", "runner_triggers", "id"), ("run_id", "runs", "run_id"),
    ("worker_sub", "runner_platform_workers", "worker_sub"),
])
def test_chaque_rattachement_a_le_type_de_sa_source(base, colonne, table_source, colonne_source):
    """Un rattachement recopié sans clé étrangère n'a que son TYPE pour rester joignable."""
    from oto_mcp import db
    sql = ("SELECT data_type FROM information_schema.columns "
           "WHERE table_name = %s AND column_name = %s")
    with db._connect() as c:
        ici = c.execute(sql, ("runner_job_attempts", colonne)).fetchone()["data_type"]
        la = c.execute(sql, (table_source, colonne_source)).fetchone()["data_type"]
    assert ici == la


def test_aucun_historique_n_est_reconstitue_au_boot(base):
    """Un travail conclu AVANT la table ne reçoit pas de tentative inventée : ni au
    boot, ni au rejeu du DDL."""
    from oto_mcp import db
    with db._connect() as c:
        job = _travail(c, "done")
        c.execute("UPDATE runner_jobs SET result = %s::jsonb WHERE id = %s",
                  ('{"usage_input": 5, "usage_output": 5}', job))
    db.init_db()
    assert _tentatives(job) == []


def test_la_tentative_survit_au_run_efface(base):
    from oto_mcp import db
    rid = "run_" + uuid.uuid4().hex[:10]
    db.insert_run(rid, sub=None, org_id=ORG, label="banc des tentatives")
    with db._connect() as c:
        t = _ouvrir(c, run_id=rid)
        c.execute("DELETE FROM runs WHERE run_id = %s", (rid,))
    assert [x["run_id"] for x in _tentatives(t["job_id"])] == [rid]


# ── 1 et 2. donnée empoisonnée contre vraie erreur ────────────────────────────

def test_une_charge_EMPOISONNEE_n_avorte_ni_la_reservation_ni_la_conclusion(base):
    from oto_mcp import db
    with db._connect() as c:
        job = _travail(c)
        c.execute("UPDATE runner_jobs SET status = 'done' WHERE id = %s", (job,))
        t = _ouvrir(c, job_id=job, trigger_id="x", key_source="gratuit",
                    provider_family="fam\x00ille", model="m\ud800", run_id="run\x00z")
        RA.conclure_tentative(c, attempt_id=t["attempt_id"], ok=True, resultat={
            "usage_input": "12k", "usage_output": 2**70, "usage_cache_read": -5,
            "usage_cache_write": "²", "usage_input_total": 2.5, "steps": "abc",
            "stopped": "fin\x00", "model": ["pas", "un", "nom"],
            "usage_couverture": {"k\x00": float("nan")}})
        statut = c.execute("SELECT status FROM runner_jobs WHERE id = %s",
                           (job,)).fetchone()["status"]
    assert statut == "done", "l'écriture voisine doit être COMMISE"
    [ligne] = _tentatives(job)
    assert (ligne["trigger_id"], ligne["key_source"], ligne["provider_family"],
            ligne["run_id"]) == (None, None, "famille", "runz")
    assert all(ligne[p] is None for p in RA.POSTES_TENTATIVE)
    assert (ligne["steps"], ligne["stopped"], ligne["usage_couverture"],
            ligne["outcome"]) == (None, "fin", None, "done")


def test_une_vraie_erreur_de_base_n_est_PAS_avalee(base):
    psycopg = pytest.importorskip("psycopg")
    from oto_mcp import db
    with db._connect() as c:
        job = _travail(c)
    with pytest.raises(psycopg.errors.UniqueViolation):
        with db._connect() as c:
            c.execute("UPDATE runner_jobs SET status = 'done' WHERE id = %s", (job,))
            _ouvrir(c, job_id=job, attempt_no=1)
            _ouvrir(c, job_id=job, attempt_no=1)
    with db._connect() as c:
        statut = c.execute("SELECT status FROM runner_jobs WHERE id = %s",
                           (job,)).fetchone()["status"]
    assert statut == "claimed", "l'écriture voisine part avec la transaction"
    assert _tentatives(job) == []


# ── 3. le cycle d'une tentative ───────────────────────────────────────────────

def test_une_seconde_tentative_OUVERTE_sur_le_meme_travail_est_refusee(base):
    psycopg = pytest.importorskip("psycopg")
    from oto_mcp import db
    job = next(_JOBS)
    with db._connect() as c:
        _ouvrir(c, job_id=job, attempt_no=1)
    with pytest.raises(psycopg.errors.UniqueViolation):
        with db._connect() as c:
            _ouvrir(c, job_id=job, attempt_no=2)
    with db._connect() as c:
        assert len(RA.clore_tentatives_perdues(c, job)) == 1
        assert _ouvrir(c, job_id=job, attempt_no=2)["outcome"] == "open"
    assert [t["outcome"] for t in _tentatives(job)] == ["lost", "open"]


def test_ouvrir_recopie_les_rattachements_et_n_invente_aucun_montant(base):
    from oto_mcp import db
    with db._connect() as c:
        t = _ouvrir(c, run_id="run_r", trigger_id="42", fleet_id=9, key_source="org",
                    provider_family="mistral", model="mistral-large-2512")
    uuid.UUID(t["attempt_id"])
    assert (t["trigger_id"], t["fleet_id"], t["key_source"], t["source"]) == (42, 9, "org", "batch")
    assert t["outcome"] == "open" and t["attested"] is False
    assert t["nano_usd"] is None and t["unpriced_reason"] == runner_prix.POSTE_INCONNU


def test_un_bail_mort_ferme_la_tentative_perdue_dans_le_run_ou_elle_a_travaille(base):
    from oto_mcp import db
    with db._connect() as c:
        t = _ouvrir(c)
        [perdue] = RA.clore_tentatives_perdues(c, t["job_id"], run_id="run_repris")
        assert RA.clore_tentatives_perdues(c, t["job_id"]) == []
    assert (perdue["outcome"], perdue["run_id"]) == ("lost", "run_repris")
    assert perdue["ended_at"] is not None


def test_une_conclusion_REJOUEE_rend_l_enregistre_sans_rien_recompter(base):
    from oto_mcp import db
    with db._connect() as c:
        t = _ouvrir(c)
        premiere = RA.conclure_tentative(c, attempt_id=t["attempt_id"], ok=True, resultat=ATTESTE)
        rejouee = RA.conclure_tentative(c, attempt_id=t["attempt_id"], ok=False,
                                        resultat={**ATTESTE, "usage_input": 999_999})
    assert premiere["conclue"] is True and rejouee["conclue"] is False
    assert rejouee["tentative"] == premiere["tentative"]
    attendu = runner_prix.tarifer("anthropic", "claude-opus-5", ATTESTE)
    assert premiere["tentative"]["nano_usd"] == attendu.nano_usd
    assert premiere["tentative"]["bareme"] == attendu.bareme


def test_un_echec_garde_ses_postes_et_son_montant(base):
    from oto_mcp import db
    with db._connect() as c:
        t = _ouvrir(c)
        fin = RA.conclure_tentative(c, attempt_id=t["attempt_id"], ok=False, resultat=ATTESTE)
    ligne = fin["tentative"]
    assert ligne["outcome"] == "failed" and ligne["usage_input"] == 1000
    assert ligne["attested"] is True and ligne["nano_usd"] is not None


def test_un_REFUS_au_claim_se_ferme_failed_a_zero_atteste_par_le_serveur(base):
    """Contrat §1 : aucun travail servi, donc zéro certain. Sans famille, le zéro attesté
    vaut quand même U = 0 et un coût nul (§3)."""
    from oto_mcp import db
    with db._connect() as c:
        t = _ouvrir(c, provider_family=None)
        ligne = RA.refuser_tentative(c, t["attempt_id"], "requester_invalid")
        couverture = c.execute("SELECT usage_couverture FROM runner_job_attempts WHERE id = %s",
                               (t["attempt_id"],)).fetchone()["usage_couverture"]
    assert (ligne["outcome"], ligne["stopped"]) == ("failed", "requester_invalid")
    assert all(ligne[p] == 0 for p in RA.POSTES_TENTATIVE)
    assert couverture == RA.COUVERTURE_REFUS_SERVEUR and couverture["source"] == "serveur"
    assert set(couverture["declares"]) == set(couverture["sommes"]) and \
        not any(couverture["declares"].values())
    assert (ligne["attested"], ligne["budget_units"], ligne["nano_usd"],
            ligne["unpriced_reason"]) == (True, 0, 0, None)


def test_un_montant_au_prix_PROVISOIRE_s_enregistre_avec_son_marqueur(base):
    from oto_mcp import db
    mistral = {**ATTESTE, "usage_cache_read": 0, "usage_cache_write": None,
               "model": "mistral-large-2512"}
    with db._connect() as c:
        t_m = _ouvrir(c, provider_family="mistral")
        t_o = _ouvrir(c)
        provisoire = RA.conclure_tentative(c, attempt_id=t_m["attempt_id"], ok=True,
                                           resultat=mistral)["tentative"]
        verifie = RA.conclure_tentative(c, attempt_id=t_o["attempt_id"], ok=True,
                                        resultat=ATTESTE)["tentative"]
    assert t_m["price_unverified"] is False, "aucun montant, aucun prix utilisé"
    assert provisoire["nano_usd"] == 1000 * 500 + 200 * 1_500
    assert (provisoire["price_unverified"], provisoire["unpriced_reason"]) == (True, None)
    assert verifie["price_unverified"] is False


def test_un_resultat_sans_couverture_garde_ses_nombres_mais_n_est_pas_atteste(base):
    from oto_mcp import db
    sans = {k: v for k, v in ATTESTE.items() if k != "usage_couverture"}
    with db._connect() as c:
        t = _ouvrir(c)
        ligne = RA.conclure_tentative(c, attempt_id=t["attempt_id"], ok=True,
                                      resultat=sans)["tentative"]
    assert ligne["usage_input"] == 1000 and ligne["attested"] is False


def test_un_complement_TARDIF_ne_remplit_que_les_postes_restes_NULL(base):
    from oto_mcp import db
    with db._connect() as c:
        t = _ouvrir(c)
        RA.clore_tentatives_perdues(c, t["job_id"])
        premier = RA.completer_tentative_perdue(c, attempt_id=t["attempt_id"], resultat=ATTESTE)
        second = RA.completer_tentative_perdue(
            c, attempt_id=t["attempt_id"],
            resultat={**ATTESTE, "usage_input": 1, "usage_output": 1})
    assert premier["usage_enregistre"] is True and second["usage_enregistre"] is False
    ligne = second["tentative"]
    assert ligne["outcome"] == "lost", "le complément ne change jamais l'état"
    assert (ligne["usage_input"], ligne["usage_output"]) == (1000, 200)
    assert ligne["attested"] is True and ligne["nano_usd"] is not None


def test_un_complement_tardif_sur_une_tentative_non_perdue_n_ecrit_rien(base):
    from oto_mcp import db
    with db._connect() as c:
        ouverte = _ouvrir(c)
        conclue = _ouvrir(c)
        RA.conclure_tentative(c, attempt_id=conclue["attempt_id"], ok=True, resultat={})
        r1 = RA.completer_tentative_perdue(c, attempt_id=ouverte["attempt_id"], resultat=ATTESTE)
        r2 = RA.completer_tentative_perdue(c, attempt_id=conclue["attempt_id"], resultat=ATTESTE)
    assert r1["usage_enregistre"] is False and r1["tentative"]["usage_input"] is None
    assert r2["usage_enregistre"] is False and r2["tentative"]["usage_input"] is None


def test_un_identifiant_de_tentative_illisible_ou_inconnu_rend_None(base):
    from oto_mcp import db
    with db._connect() as c:
        assert RA.conclure_tentative(c, attempt_id="pas-un-uuid", ok=True) is None
        assert RA.conclure_tentative(c, attempt_id=str(uuid.uuid4()), ok=True) is None
        assert RA.completer_tentative_perdue(c, attempt_id=None, resultat=ATTESTE) is None
