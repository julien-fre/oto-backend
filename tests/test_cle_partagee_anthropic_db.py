"""La clé partagée EN BASE, de bout en bout : l'estampille, la réservation et le
règlement de chaque tour au budget d'unités, le rejeu, la conclusion qui clôt les tours
jamais rapportés.

Une doublure ne prouve ni l'estampille relue par la tentative en cours, ni que la
réservation, le règlement et le solde s'écrivent ensemble — c'est pourtant ce qui fait
la facture.
"""
from __future__ import annotations

import uuid

import pytest

PORTEUR, AUTRE = "worker:banc", "worker:autre"
TOUR = {"model": "claude-sonnet-5-5", "usage_input": 100, "usage_output": 50,
        "usage_cache_write": 0, "usage_cache_read": 1000}
# Une unité de budget par paquet : 100 jetons d'entrée, 10 de sortie, 100 d'écriture de
# cache, 1 000 de lecture.
LIGNES = [("input_tokens", 1, 100), ("output_tokens", 1, 10),
          ("cache_creation_input_tokens", 1, 100), ("cache_read_input_tokens", 1, 1000)]


@pytest.fixture
def travail(live, monkeypatch):
    """Un travail réservé par `PORTEUR`, sur un run réel (clé étrangère), et un barème
    qui prix les appels au modèle."""
    from oto_mcp.access import credits
    from oto_mcp.db import credits as db_credits
    from oto_mcp.db._conn import _connect
    monkeypatch.setenv("OTO_CREDITS_MODE", "enforce")
    db_credits.poser_bareme(1, [{"connector": "anthropic",
                                 "function": "messages:claude-sonnet-5-5",
                                 "unit": u, "budget": b, "per_n": n} for u, b, n in LIGNES])
    credits.oublier()
    org = 42
    run_id = f"r-{uuid.uuid4().hex[:8]}"
    with _connect() as c:
        c.execute("INSERT INTO runs (run_id, sub, org_id, label) VALUES (%s, %s, %s, %s)",
                  (run_id, "alexis", org, "banc"))
        jid = c.execute(
            """INSERT INTO runner_jobs (org_id, kind, payload, status, claimed_by, attempts,
                                        sub, run_id)
               VALUES (%s, 'start',
                       '{"model": "claude-sonnet-5-5", "model_family": "anthropic"}',
                       'claimed', %s, 1,
                       'alexis', %s) RETURNING id""", (org, PORTEUR, run_id)).fetchone()["id"]
    db_credits.verser(f"org:{org}", 1000, "banc", f"banc-{jid}")
    yield jid
    credits.oublier()


def _jobs(op, jid, porteur=PORTEUR, **champs):
    from oto_mcp.capabilities import runner_jobs as RJ
    from oto_mcp.capabilities._types import ResolvedCtx
    return RJ._jobs(ResolvedCtx(sub=porteur, org_id=None, role="platform_worker",
                                platform_worker=True),
                    RJ.JobsInput(op=op, job_id=jid, **champs))


def _solde():
    from oto_mcp.db import credits as db_credits
    s = db_credits.solde("org:42")
    return s["balance"], s["holds_open"]


def _reglements(jid):
    from oto_mcp.db._conn import _connect
    with _connect() as c:
        return [dict(r) for r in c.execute(
            "SELECT call_uid, amount, held, quantity, meta FROM credit_movements "
            "WHERE kind = 'settle' AND call_uid LIKE %s ORDER BY id", (f"runner:{jid}:%",))]


def test_chaque_tour_se_reserve_puis_se_regle_une_fois(travail):
    from oto_mcp import db
    from oto_mcp.capabilities._types import AuthzDenied
    jid = travail
    avant = _solde()
    assert db.estampiller_cle_partagee(jid, PORTEUR, "anthropic", "platform")
    assert not db.estampiller_cle_partagee(jid, AUTRE, "anthropic", "platform")

    assert _jobs("reserve", jid, turn=0, max_tokens=100) == {"ok": True, "held": True,
                                                             "reserved": 10}
    assert _solde() == (avant[0], avant[1] + 10)
    out = _jobs("meter", jid, turn=0, result=TOUR)
    # entrée 100 → 1, sortie 50 → 5, écriture 0 → 0, lecture 1 000 → 1 : 7 ≤ 10 réservés.
    assert out == {"ok": True, "metered": True, "amount": 7, "exceeded": False,
                   "duplicate": False}
    assert _solde() == (avant[0] - 7, avant[1])
    assert _jobs("meter", jid, turn=0, result=TOUR)["duplicate"] is True
    assert _solde() == (avant[0] - 7, avant[1]), "un rapport rejoué ne règle pas deux fois"

    _jobs("reserve", jid, turn=1, max_tokens=10)
    out = _jobs("meter", jid, turn=1, result={**TOUR, "usage_input": 5_000})
    assert out["exceeded"] is True and out["amount"] == 50 + 5 + 1, \
        "l'entrée ne se réserve pas : au-delà, réglée telle quelle"
    with pytest.raises(AuthzDenied) as e:
        _jobs("meter", jid, AUTRE, turn=2, result=TOUR)
    assert e.value.status == 404, "le travail n'est pas à lui"

    [r0, r1] = _reglements(jid)
    assert r0["call_uid"] == f"runner:{jid}:1:0" and r0["quantity"] == 50
    assert r0["meta"]["tokens"] == {"input_tokens": 100, "output_tokens": 50,
                                    "cache_creation_input_tokens": 0,
                                    "cache_read_input_tokens": 1000}
    assert (r0["meta"]["key_mode"], r0["meta"]["model"], r0["meta"]["turn"]) == \
        ("platform", "claude-sonnet-5-5", 0)


def test_le_rapprochement_lit_les_jetons_regles_du_rang(travail):
    """Ce que `admin.model_spend` compare au relevé d'Anthropic : la somme des jetons des
    règlements `anthropic.messages:<modèle>` du jour, par modèle, sous le rang
    plateforme seul."""
    from datetime import datetime, timezone

    from oto_mcp import db
    from oto_mcp.access import credits_tours
    from oto_mcp.db import credits as db_credits
    jid = travail

    zero = {u: 0 for u in credits_tours.UNITES_DU_MODELE}

    def _lu(rang):
        return db_credits.jetons_du_jour(
            "anthropic", "messages:", rang, datetime.now(timezone.utc).date(),
            credits_tours.UNITES_DU_MODELE).get("messages:claude-sonnet-5-5", zero)
    avant = _lu("platform")
    db.estampiller_cle_partagee(jid, PORTEUR, "anthropic", "platform")
    _jobs("meter", jid, turn=0, result=TOUR)       # un tour jamais réservé se règle aussi
    apres = _lu("platform")
    assert {u: apres[u] - avant[u] for u in apres} == {
        "input_tokens": 100, "output_tokens": 50, "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 1000}
    assert _lu("tenant") == zero


def test_un_tour_au_dela_du_solde_est_refuse_en_application(travail):
    from oto_mcp import db
    from oto_mcp.capabilities._types import AuthzDenied
    jid = travail
    db.estampiller_cle_partagee(jid, PORTEUR, "anthropic", "platform")
    with pytest.raises(AuthzDenied) as e:
        _jobs("reserve", jid, turn=0, max_tokens=10 ** 6)
    assert (e.value.status, e.value.code) == (402, "insufficient_credits")


def test_le_tour_porte_le_modele_du_travail_et_un_modele_sans_ligne_est_refuse(travail):
    """La réservation est sous `messages:<modèle>` — le modèle de la charge du travail ;
    un travail sur un modèle que le barème ne prix pas est `credits_unpriced`."""
    from oto_mcp import db
    from oto_mcp.capabilities._types import AuthzDenied
    from oto_mcp.db._conn import _connect
    jid = travail
    db.estampiller_cle_partagee(jid, PORTEUR, "anthropic", "platform")
    _jobs("reserve", jid, turn=0, max_tokens=100)
    with _connect() as c:
        fonction = c.execute("SELECT function FROM credit_holds WHERE call_uid = %s",
                             (f"runner:{jid}:1:0",)).fetchone()["function"]
        c.execute("UPDATE runner_jobs SET payload = jsonb_set(payload, '{model}', "
                  "'\"claude-opus-5-5\"') WHERE id = %s", (jid,))
    assert fonction == "messages:claude-sonnet-5-5"
    with pytest.raises(AuthzDenied) as e:
        _jobs("reserve", jid, turn=1, max_tokens=100)
    assert (e.value.status, e.value.code) == (422, "credits_unpriced")


def test_la_conclusion_regle_les_tours_jamais_rapportes(travail):
    from oto_mcp import db
    jid = travail
    db.estampiller_cle_partagee(jid, PORTEUR, "anthropic", "platform")
    avant = _solde()
    _jobs("reserve", jid, turn=0, max_tokens=100)
    _jobs("meter", jid, turn=0, result=TOUR)
    _jobs("reserve", jid, turn=1, max_tokens=40)      # l'appel qui a coupé la boucle
    _jobs("complete", jid, ok=False, error="coupé")
    assert [(r["amount"], r["meta"]["from"]) for r in _reglements(jid)] == [
        (-7, "response"), (-4, "unreported")]
    assert _solde() == (avant[0] - 11, avant[1])


def test_une_tentative_non_estampillee_ne_reserve_ni_ne_regle_rien(travail):
    from oto_mcp import db
    from oto_mcp.db._conn import _connect
    jid = travail
    db.estampiller_cle_partagee(jid, PORTEUR, "anthropic", "platform")
    with _connect() as c:   # repris plus tard sur la clé de l'org : tentative suivante
        c.execute("UPDATE runner_jobs SET attempts = 2 WHERE id = %s", (jid,))
    avant = _solde()
    assert _jobs("reserve", jid, turn=0, max_tokens=100) == {"ok": True, "held": False}
    assert _jobs("meter", jid, turn=0, result=TOUR) == {"ok": True, "metered": False}
    assert _reglements(jid) == [] and _solde() == avant
