"""La RÉSERVATION ouvre la tentative, les verbes du bail agissent PAR elle.

Contrat runner §1 (bascule dure), contre un PostgreSQL jetable, par la capacité telle que
la route l'appelle (`runner.jobs` → `_jobs`) :

1. **le claim, une seule transaction** — `attempts + 1`, la tentative précédente close
   `lost`, la décision de clé, le jeton délégué ÉMIS sur la connexion du claim et
   rattaché à la tentative, les jetons précédents révoqués, la tentative insérée ; une
   réservation qui échoue ou qui refuse ne laisse AUCUN jeton ;
2. **le run réellement travaillé** — repris s'il est ouvert, aucun s'il est clos ;
3. **un refus du serveur au claim** se ferme `failed` à zéro attesté, écrit sur la
   connexion de la réservation. ⚠️ Chaque refus vérifie AUSSI que le travail est bien
   `failed` : un refus posé sur une seconde connexion ne verrait pas la réservation non
   validée, ne toucherait aucune ligne, et laisserait le travail `claimed` sans un mot ;
4. **le fencing** — sans `attempt_id`, refus nommé ; tentative remplacée, 409
   `attempt_superseded` et l'état intact ; un complément tardif ; un rejeu qui ne
   recompte rien.
"""
from __future__ import annotations

import os
import uuid

import pytest

from oto_mcp.capabilities import runner_jobs as RJ
from oto_mcp.capabilities._types import AuthzDenied, ResolvedCtx
from oto_mcp.db import runner_attempts as RA

ORG = 7301
PORTEUR = "usr_porteur_tentative"
ETRANGER = "usr_hors_org_tentative"
MACHINE = "worker:banc-tentative"
ATTESTE = {"usage_input": 1000, "usage_output": 200, "usage_cache_read": 50,
           "usage_cache_write": 10, "usage_input_total": 1060, "stopped": "end_turn",
           "usage_couverture": {"tours": 2}, "model": "claude-opus-5"}


@pytest.fixture(scope="module")
def base(pg_module_dsn):
    avant = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = pg_module_dsn
    try:
        from oto_mcp.db import _conn as dbconn
        from oto_mcp.db import init_db
        init_db()
        with dbconn._connect() as c:
            for sub in (PORTEUR, ETRANGER):
                c.execute("INSERT INTO users (sub) VALUES (%s) ON CONFLICT DO NOTHING", (sub,))
            c.execute("INSERT INTO orgs (id, name) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                      (ORG, "org du banc"))
            c.execute("INSERT INTO org_members (org_id, sub, org_role) VALUES (%s, %s, "
                      "'org_member') ON CONFLICT DO NOTHING", (ORG, PORTEUR))
        yield
    finally:
        if avant is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = avant


@pytest.fixture(autouse=True)
def _file_vide(base):
    _sql("DELETE FROM runner_jobs")
    _sql("DELETE FROM runner_job_attempts")
    _sql("DELETE FROM user_api_tokens WHERE kind = 'delegation'")


def _sql(requete: str, *args) -> list[dict]:
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        cur = conn.execute(requete, args)
        return [dict(r) for r in cur.fetchall()] if cur.description else []


def _machine() -> ResolvedCtx:
    return ResolvedCtx(sub=MACHINE, org_id=None, role="platform_worker", platform_worker=True)


def _jobs(ctx=None, **kw) -> dict:
    return RJ._jobs(ctx or _machine(), RJ.JobsInput(**kw))


def _enfiler(sub=PORTEUR, max_attempts=3, **charge) -> int:
    from oto_mcp import db
    return int(db.enqueue_job(ORG, "start", payload={"procedure": "p", **charge},
                              sub=sub, max_attempts=max_attempts)["id"])


def _tentatives(job_id: int) -> list[dict]:
    return _sql(f"SELECT {RA._TENTATIVE_COLONNES_LIGNE}, usage_couverture FROM "
                "runner_job_attempts WHERE job_id = %s ORDER BY attempt_no", job_id)


def _jetons(job_id: int) -> list[str]:
    """Les jetons délégués de TOUTES les tentatives de ce travail, en base."""
    from oto_mcp import db
    return [r["label"] for r in _sql(
        "SELECT label FROM user_api_tokens WHERE kind = 'delegation' AND label LIKE %s "
        "ORDER BY id", db.libelle_jeton_du_travail(job_id, "%"))]


def _statut(job_id: int) -> tuple:
    [r] = _sql("SELECT status, attempts FROM runner_jobs WHERE id = %s", job_id)
    return r["status"], r["attempts"]


def _bail_mort(job_id: int) -> None:
    _sql("UPDATE runner_jobs SET lease_until = NOW() - interval '1 second' WHERE id = %s",
         job_id)


def _run_ouvert() -> str:
    from oto_mcp import db
    run_id = uuid.uuid4().hex
    db.insert_run(run_id, sub=PORTEUR, org_id=ORG, label="banc des tentatives")
    db.insert_tool_call({"tool": "run_start", "sub": PORTEUR, "org_id": ORG,
                         "run_id": run_id, "args": {"label": "banc"}, "ok": True})
    return run_id


def _clore(run_id: str) -> None:
    from oto_mcp import db
    db.insert_tool_call({"tool": "run_finish", "sub": PORTEUR, "org_id": ORG,
                         "run_id": run_id, "args": {"run_id": run_id, "outcome": "failed"},
                         "ok": True})


def _prendre(**kw) -> dict:
    job = _jobs(op="claim", **kw)["job"]
    assert job and job.get("attempt_id"), job
    return job


# ── 1 et 2. le claim et le run réellement travaillé ───────────────────────────

@pytest.mark.parametrize("clos", [False, True])
def test_une_reprise_ouvre_une_NOUVELLE_tentative_et_clot_la_precedente_lost(base, clos):
    job_id = _enfiler()
    t1 = _prendre()
    r1 = _run_ouvert()
    assert _jobs(op="bind_run", job_id=job_id, run_id=r1, attempt_id=t1["attempt_id"])["ok"]
    if clos:
        _clore(r1)
    _bail_mort(job_id)

    t2 = _prendre()
    assert t2["id"] == job_id and t2["attempt_id"] != t1["attempt_id"]
    attendu = None if clos else r1
    assert t2["run_id"] == attendu, "run ouvert repris, run clos : aucun"
    lignes = _tentatives(job_id)
    assert [(l["attempt_no"], l["outcome"], l["run_id"]) for l in lignes] == [
        (1, "lost", r1), (2, "open", attendu)]
    assert all(lignes[0][p] is None for p in RA.POSTES_TENTATIVE)
    assert lignes[0]["usage_couverture"] is None, "une tentative perdue n'invente aucun usage"


def test_une_reservation_reussie_laisse_UN_jeton_rattache_a_SA_tentative(base):
    from oto_mcp import db
    job_id = _enfiler()
    t1 = _prendre()
    assert _jetons(job_id) == [db.libelle_jeton_du_travail(job_id, 1)]
    assert db.verify_api_token(t1["delegated_token"])["sub"] == PORTEUR
    _bail_mort(job_id)
    t2 = _prendre()
    assert _jetons(job_id) == [db.libelle_jeton_du_travail(job_id, 2)], \
        "un seul jeton vivant : celui de la tentative COURANTE"
    assert db.verify_api_token(t1["delegated_token"]) is None, "le zombie ne peut plus écrire"
    assert db.verify_api_token(t2["delegated_token"])["sub"] == PORTEUR


@pytest.mark.parametrize("qui,cle,payeur", [
    ("machine", "sk-de-l-org", "org"), ("machine", None, "platform"),
    ("membre", "sk-de-l-org", None)])
def test_le_payeur_s_ecrit_dans_la_transaction_du_claim_jamais_par_defaut(
        base, monkeypatch, qui, cle, payeur):
    monkeypatch.setattr(RJ, "_cle_de_modele", lambda org, depot: cle)
    job_id = _enfiler(model="claude-opus-5", model_family="anthropic")
    ctx = _machine() if qui == "machine" else ResolvedCtx(sub=PORTEUR, org_id=ORG)
    _prendre(ctx=ctx, provider="anthropic")
    [t] = _tentatives(job_id)
    assert (t["key_source"], t["provider_family"], t["model"]) == (
        payeur, "anthropic", "claude-opus-5")


def test_une_decision_qui_echoue_annule_TOUTE_la_reservation(base, monkeypatch):
    def coffre_en_panne(org, depot):
        raise RuntimeError("coffre en panne")
    monkeypatch.setattr(RJ, "_cle_de_modele", coffre_en_panne)
    job_id = _enfiler(model="claude-opus-5", model_family="anthropic")
    with pytest.raises(RuntimeError, match="coffre"):
        _jobs(op="claim", provider="anthropic")
    assert _statut(job_id) == ("pending", 0) and _tentatives(job_id) == []
    assert _jetons(job_id) == []


def test_une_reservation_qui_echoue_APRES_l_emission_ne_laisse_AUCUN_jeton(base, monkeypatch):
    """Le jeton est émis DANS la transaction : quand elle tombe, il n'a jamais existé."""
    def tentative_en_panne(*a, **k):
        raise RuntimeError("écriture de la tentative en panne")
    monkeypatch.setattr(RA, "ouvrir_tentative", tentative_en_panne)
    job_id = _enfiler()
    with pytest.raises(RuntimeError, match="tentative"):
        _jobs(op="claim")
    assert _statut(job_id) == ("pending", 0)
    assert _jetons(job_id) == [], "aucun jeton ne survit à une réservation annulée"


# ── 3. le refus du serveur au claim ───────────────────────────────────────────

def _zero_serveur(t: dict, code: str) -> None:
    assert (t["outcome"], t["stopped"]) == ("failed", code)
    assert all(t[p] == 0 for p in RA.POSTES_TENTATIVE), "des zéros STOCKÉS, pas des NULL"
    assert t["usage_couverture"] == RA.COUVERTURE_REFUS_SERVEUR
    assert t["usage_couverture"]["source"] == "serveur"
    assert set(t["usage_couverture"]["declares"]) == set(t["usage_couverture"]["sommes"])
    assert (t["nano_usd"], t["budget_units"], t["unpriced_reason"]) == (0, 0, None)
    assert t["attested"] is True and t["key_source"] is None


def test_un_refus_d_IDENTITE_au_claim_ferme_la_tentative_a_zero_atteste(base):
    job_id = _enfiler(sub=ETRANGER)
    servi = _prendre()
    assert servi["delegation_refusee"] and "_refus" not in servi
    assert _statut(job_id) == ("failed", 1), "le refus est ÉCRIT, sur la réservation"
    [t] = _tentatives(job_id)
    _zero_serveur(t, RJ.REFUS_IDENTITE)
    assert t["provider_family"] is None, "aucune famille inventée : le zéro suffit"
    assert _jetons(job_id) == [] and "delegated_token" not in servi


def test_une_cle_EXIGEE_absente_ferme_a_zero_et_n_EMET_aucun_jeton(base, monkeypatch):
    monkeypatch.setattr(RJ._cle_exigee, "cle_exigee", lambda org, fournisseur: True)
    monkeypatch.setattr(RJ, "_cle_de_modele", lambda org, depot: None)
    job_id = _enfiler(model="claude-opus-5", model_family="anthropic")
    servi = _prendre(provider="anthropic")
    assert servi["delegation_refusee"] and servi["delegated_token"] is None
    assert _statut(job_id) == ("failed", 1), "le refus est ÉCRIT, sur la réservation"
    [t] = _tentatives(job_id)
    _zero_serveur(t, RJ.REFUS_CLE_ABSENTE)
    assert t["provider_family"] == "anthropic"
    assert _jetons(job_id) == [], "un travail refusé ne reçoit aucun pouvoir d'agir"


def test_un_refus_du_WORKER_apres_service_n_est_pas_un_zero_du_serveur(base):
    """Témoin (contrat §1) : le serveur ignore ce qui a tourné, seul le complete déclare."""
    job_id = _enfiler()
    t1 = _prendre()
    _jobs(op="complete", job_id=job_id, ok=False, error="FamilleEtrangere",
          result={"stopped": "FamilleEtrangere"}, attempt_id=t1["attempt_id"])
    [t] = _tentatives(job_id)
    assert (t["outcome"], t["stopped"]) == ("failed", "FamilleEtrangere")
    assert t["usage_couverture"] is None and all(t[p] is None for p in RA.POSTES_TENTATIVE)
    assert t["nano_usd"] is None and t["unpriced_reason"] is not None


def test_une_charge_EMPOISONNEE_ne_casse_pas_le_claim(base):
    job_id = _enfiler(trigger_id="x", model=["pas", "un", "nom"])
    servi = _prendre()
    [t] = _tentatives(job_id)
    assert servi["id"] == job_id and t["outcome"] == "open"
    assert (t["trigger_id"], t["model"]) == (None, None)


def test_une_EPAVE_balayee_voit_sa_tentative_close_lost(base):
    job_id = _enfiler(max_attempts=1)
    _prendre()
    _bail_mort(job_id)
    assert _jobs(op="claim")["job"] is None
    assert _statut(job_id)[0] == "failed"
    assert [t["outcome"] for t in _tentatives(job_id)] == ["lost"]


# ── 4. le fencing ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("op,kw", [("bind_run", {"run_id": "r"}), ("extend", {}),
                                    ("complete", {"ok": True})])
def test_sans_attempt_id_les_trois_verbes_sont_refuses_et_rien_ne_bouge(base, op, kw):
    job_id = _enfiler()
    _prendre()
    avant = _sql("SELECT lease_until, run_id, status FROM runner_jobs WHERE id = %s", job_id)
    with pytest.raises(AuthzDenied) as e:
        _jobs(op=op, job_id=job_id, **kw)
    assert (e.value.status, e.value.code) == (400, "attempt_id_required")
    assert _sql("SELECT lease_until, run_id, status FROM runner_jobs WHERE id = %s",
                job_id) == avant
    assert [t["outcome"] for t in _tentatives(job_id)] == ["open"]


def test_un_ZOMBIE_recoit_409_l_etat_reste_intact_et_son_usage_complete_la_perdue(base):
    job_id = _enfiler()
    t1 = _prendre()
    _bail_mort(job_id)
    t2 = _prendre()

    with pytest.raises(AuthzDenied) as e:
        _jobs(op="complete", job_id=job_id, ok=True, result=ATTESTE,
              attempt_id=t1["attempt_id"])
    assert (e.value.status, e.value.code, e.value.details) == (
        409, "attempt_superseded", {"usage_enregistre": True})
    assert _statut(job_id) == ("claimed", 2), "le zombie ne conclut pas le travail repris"
    perdue, courante = _tentatives(job_id)
    assert (perdue["outcome"], perdue["usage_input"], perdue["attested"]) == ("lost", 1000, True)
    assert courante["outcome"] == "open" and courante["usage_input"] is None

    with pytest.raises(AuthzDenied) as tardif:
        _jobs(op="complete", job_id=job_id, ok=True, attempt_id=t1["attempt_id"],
              result={**ATTESTE, "usage_input": 1})
    assert tardif.value.details == {"usage_enregistre": False}
    assert _tentatives(job_id)[0]["usage_input"] == 1000, "rien d'écrasé"
    for op, kw in (("extend", {}), ("bind_run", {"run_id": _run_ouvert()})):
        with pytest.raises(AuthzDenied) as v:
            _jobs(op=op, job_id=job_id, attempt_id=t1["attempt_id"], **kw)
        assert (v.value.code, v.value.details) == ("attempt_superseded",
                                                   {"usage_enregistre": False}), op

    fin = _jobs(op="complete", job_id=job_id, ok=True, attempt_id=t2["attempt_id"])
    assert fin["status"] == "done", "la tentative COURANTE conclut"


def test_rejouer_le_meme_complete_rend_l_enregistre_sans_rien_recompter(base):
    job_id = _enfiler()
    t1 = _prendre()
    premier = _jobs(op="complete", job_id=job_id, ok=True, result=ATTESTE,
                    attempt_id=t1["attempt_id"])
    rejoue = _jobs(op="complete", job_id=job_id, ok=False,
                   result={**ATTESTE, "usage_input": 999_999}, attempt_id=t1["attempt_id"])
    assert premier["status"] == "done"
    assert rejoue == {"ok": True, "status": "done", "replayed": True, "run_id": None}
    [t] = _tentatives(job_id)
    assert (t["outcome"], t["usage_input"], t["model"]) == ("done", 1000, "claude-opus-5")
    [job] = _sql("SELECT result FROM runner_jobs WHERE id = %s", job_id)
    assert job["result"]["usage_input"] == 1000, "le résultat enregistré n'est pas réécrit"


def test_une_tentative_inconnue_rend_job_inconnu(base):
    job_id = _enfiler()
    _prendre()
    with pytest.raises(AuthzDenied) as e:
        _jobs(op="complete", job_id=job_id, ok=True, attempt_id=str(uuid.uuid4()))
    assert (e.value.status, e.value.code) == (404, "job_not_found")
