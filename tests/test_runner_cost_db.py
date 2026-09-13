"""Coûts et Consommation LUS PAR LEUR ROUTE, contre un vrai PostgreSQL.

Le banc part de la table de routes réelle (`make_routes` + adaptateur de capacités) :
c'est le seul niveau qui prouve ce que la face SERT. Ce qu'il tient :

1. **seuls les administrateurs de l'org lisent** — un membre est refusé nommément, un
   super_admin est servi par escalade, l'admin d'une autre org est refusé ;
2. **aucune part ne compte la voisine** — ni le total, ni la ventilation, ni un
   regroupement par run, agent ou passage (une ventilation sans filtre d'org resterait
   verte sur un banc à une seule org : ici, la voisine porte le MÊME modèle) ;
3. **un total amputé le dit, un montant provisoire aussi** — `incomplete` et ses
   raisons, `price_unverified`, à la ligne, à la part et au total ;
4. **la forme servie est celle que le modèle déclare**, dans les deux sens, et une page
   de lignes tronquée le dit.
"""
from __future__ import annotations

import itertools
import os
import uuid

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from oto_mcp import runner_prix
from oto_mcp.db import runner_attempts as RA

_JOBS = itertools.count(950_000)
ATTESTE = {"usage_input": 1000, "usage_output": 200, "usage_cache_read": 50,
           "usage_cache_write": 10, "usage_input_total": 1060, "steps": 3,
           "usage_couverture": {"tours": 3}, "stopped": "end_turn", "model": "claude-opus-5"}
MISTRAL = {**ATTESTE, "usage_cache_read": 0, "usage_cache_write": None,
           "model": "mistral-large-2512"}
UN_OPUS = runner_prix.tarifer("anthropic", "claude-opus-5", ATTESTE).nano_usd
UN_MISTRAL = runner_prix.tarifer("mistral", "mistral-large-2512", MISTRAL).nano_usd


class _Claims:
    def __init__(self, sub: str):
        self.claims = {"sub": sub, "email": f"{sub}@runner-cost.invalid", "name": sub}


class _Verifier:
    async def verify_token(self, token: str):
        return _Claims(token)


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


@pytest.fixture(scope="module")
def client(base):
    """La table de routes SOUS le garde de consultation (`X-Oto-Org`), comme `server.py`
    la monte. Sans lui, l'en-tête serait simplement ignoré : un banc « autre org
    refusée » verrait l'appelant servi… de SES propres Coûts, et croirait à une fuite."""
    from oto_mcp.api import routes as api_routes
    app = Starlette(routes=api_routes.make_routes(_Verifier(), mcp_instance=None))
    return TestClient(api_routes.ViewAsMiddleware(app, verifier=_Verifier()))


def _compte(nom: str) -> str:
    from oto_mcp import db
    sub = f"usr_cost_{nom}"
    db.upsert_user(sub, email=f"{sub}@runner-cost.invalid", name=sub)
    return sub


def _admin(nom: str) -> dict:
    from oto_mcp import org_store
    sub = _compte(nom)
    oid = org_store.create_org(f"Org {nom}", created_by=sub)
    org_store.add_org_member(oid, sub, "org_admin")
    org_store.set_active_org(sub, oid)
    return {"sub": sub, "org": oid}


def _dans(org: int, nom: str, role: str = "org_member") -> dict:
    from oto_mcp import org_store
    sub = _compte(nom)
    org_store.add_org_member(org, sub, role)
    org_store.set_active_org(sub, org)
    return {"sub": sub, "org": org}


def _tentative(org: int, *, fin: str = "done", resultat=ATTESTE, model="claude-opus-5",
               famille="anthropic", **rattachements) -> dict:
    from oto_mcp import db
    with db._connect() as c:
        t = RA.ouvrir_tentative(c, job_id=next(_JOBS), attempt_no=1, org_id=org,
                                worker_sub="worker:banc", key_source="platform",
                                provider_family=famille, model=model, **rattachements)
        if fin in ("done", "failed"):
            RA.conclure_tentative(c, attempt_id=t["attempt_id"], ok=fin == "done",
                                  resultat=resultat)
        elif fin == "lost":
            RA.clore_tentatives_perdues(c, t["job_id"])
    return t


@pytest.fixture(scope="module")
def monde(base):
    from oto_mcp import db
    a, voisine, plancher, longue, provisoire = (
        _admin(n) for n in ("a", "voisine", "plancher", "longue", "provisoire"))
    membre = _dans(a["org"], "membre_a")
    tout_puissant = _dans(a["org"], "super_a")
    db.set_user_role(tout_puissant["sub"], "super_admin")
    # L'org A : un run continué (3 travaux), un agent (2), un passage (2) — tout attesté.
    for _ in range(3):
        _tentative(a["org"], run_id="run_a")
    for _ in range(2):
        _tentative(a["org"], run_id="run_t", trigger_id=42)
    for _ in range(2):
        _tentative(a["org"], run_id="run_f", fleet_id=555)
    # La VOISINE porte les mêmes identifiants et le même modèle.
    for _ in range(5):
        _tentative(voisine["org"], run_id="run_a", trigger_id=42, fleet_id=555)
    # Le PLANCHER : chaque façon d'être incomplet, plus une part complète.
    _tentative(plancher["org"], fin="open", model="claude-sonnet-5", run_id="run_p")
    _tentative(plancher["org"], fin="lost", model="claude-sonnet-5", run_id="run_p")
    _tentative(plancher["org"], model="claude-sonnet-5", run_id="run_p",
               resultat={k: v for k, v in ATTESTE.items() if k != "usage_couverture"})
    _tentative(plancher["org"], model="modele-inedit", run_id="run_p",
               resultat={**ATTESTE, "model": "modele-inedit"})
    _tentative(plancher["org"], model="claude-haiku-4-5", run_id="run_p",
               resultat={**ATTESTE, "model": "claude-haiku-4-5"})
    # Le PROVISOIRE : un Mistral tarifé avec réserve, un Mistral au cache non sourcé, un Opus.
    _tentative(provisoire["org"], famille="mistral", model="mistral-large-2512",
               run_id="run_m", resultat=MISTRAL)
    _tentative(provisoire["org"], famille="mistral", model="mistral-large-2512",
               run_id="run_m", resultat={**MISTRAL, "usage_cache_read": 40})
    _tentative(provisoire["org"], run_id="run_m")
    # Une page plus longue que le plafond des lignes.
    for _ in range(RA.LIGNES_TENTATIVES_MAX + 1):
        _tentative(longue["org"], run_id="run_long")
    # Un fait ANCIEN : la mesure commence bien avant la fenêtre de 30 jours.
    ancien = _tentative(longue["org"], run_id="run_ancien")
    # Un run qui a commencé AVANT toute mesure.
    rid = "run_" + uuid.uuid4().hex[:10]
    db.insert_run(rid, sub=a["sub"], org_id=a["org"], label="avant la mesure")
    _tentative(a["org"], run_id=rid)
    with db._connect() as c:
        c.execute("UPDATE runner_job_attempts SET claimed_at = NOW() - INTERVAL '400 days' "
                  "WHERE id = %s", (ancien["attempt_id"],))
        c.execute("UPDATE runs SET started_at = NOW() - INTERVAL '500 days' WHERE run_id = %s",
                  (rid,))
    return {"a": a, "voisine": voisine, "plancher": plancher, "longue": longue,
            "provisoire": provisoire, "membre": membre, "super": tout_puissant,
            "run_avant_mesure": rid}


def _poste(client, qui: dict, corps: dict, org_consultee=None):
    entetes = {"Authorization": f"Bearer {qui['sub']}"}
    if org_consultee is not None:
        entetes["X-Oto-Org"] = str(org_consultee)
    return client.post("/api/me/runner/cost", headers=entetes, json=corps)


def _cout(client, qui: dict, **corps) -> dict:
    from oto_mcp.capabilities.runner_cost import CostOut
    r = _poste(client, qui, corps)
    assert r.status_code == 200, r.text
    corps_servi = r.json()
    CostOut.model_validate(corps_servi)
    return corps_servi


# ── 1. qui lit ────────────────────────────────────────────────────────────────

def test_un_simple_membre_est_refuse_et_le_refus_dit_qui_peut_lire(client, monde):
    r = _poste(client, monde["membre"], {"op": "org"})
    assert (r.status_code, r.json().get("error")) == (403, "forbidden"), r.text
    assert "administrateur" in r.json().get("detail", "")


def test_l_admin_de_l_org_est_servi(client, monde):
    assert _cout(client, monde["a"], op="org")["total"]["attempts"] == 8


def test_un_super_admin_est_servi_par_escalade(client, monde):
    assert _cout(client, monde["super"], op="org")["total"]["attempts"] == 8


def test_l_admin_d_une_AUTRE_org_est_refuse(client, monde):
    r = _poste(client, monde["voisine"], {"op": "org"}, org_consultee=monde["a"]["org"])
    assert r.status_code == 403, r.text


# ── 2. aucune part ne compte la voisine ───────────────────────────────────────

def test_l_org_se_ventile_sans_jamais_compter_la_voisine(client, monde):
    rendu = _cout(client, monde["a"], op="org", breakdown="model")
    assert rendu["total"]["attempts"] == 8
    assert rendu["total"]["nano_usd"] == 8 * UN_OPUS
    [part] = rendu["breakdown"]
    assert (part["key"], part["attempts"], part["nano_usd"]) == ("claude-opus-5", 8, 8 * UN_OPUS)


@pytest.mark.parametrize("corps,chez_a", [
    ({"op": "run", "run_id": "run_a"}, 3),
    ({"op": "agent", "trigger_id": 42}, 2),
    ({"op": "fleet", "fleet_id": 555}, 2),
])
def test_chaque_regroupement_est_scope_a_l_org_de_l_appelant(client, monde, corps, chez_a):
    assert _cout(client, monde["a"], **corps)["total"]["attempts"] == chez_a
    assert _cout(client, monde["voisine"], **corps)["total"]["attempts"] == 5


def test_un_run_continue_additionne_tous_ses_travaux(client, monde):
    total = _cout(client, monde["a"], op="run", run_id="run_a")["total"]
    assert (total["runs"], total["usage_input"], total["nano_usd"]) == (1, 3000, 3 * UN_OPUS)
    assert total["budget_units"] == 3 * (1000 + 10 + 200)


# ── 3. un total amputé le dit, un montant provisoire aussi ────────────────────

def test_un_total_complet_se_dit_complet(client, monde):
    total = _cout(client, monde["a"], op="run", run_id="run_a")["total"]
    assert (total["incomplete"], total["incomplete_reasons"]) == (False, [])
    assert total["price_unverified"] is False


def test_un_total_ampute_se_dit_PLANCHER_avec_chacune_de_ses_raisons(client, monde):
    rendu = _cout(client, monde["plancher"], op="org")
    total = rendu["total"]
    assert total["incomplete"] is True
    assert total["incomplete_reasons"] == ["open_attempts", "unmeasured_attempts",
                                           "not_attested", "unpriced"]
    assert total["unpriced_reasons"] == {runner_prix.MODELE_NON_TARIFE: 1}
    assert (total["open"], total["lost"], total["not_attested"], total["unmeasured"]) == (1, 1, 1, 1)
    assert total["usage_input"] == 2000, "les nombres non attestés ne s'additionnent pas"
    assert rendu["not_attested"]["attempts"] == 1
    assert rendu["not_attested"]["usage_input"] == 1000, "ils sont gardés à part"


def test_chaque_part_avoue_sa_propre_incompletude(client, monde):
    parts = {p["key"]: p for p in _cout(client, monde["plancher"], op="org",
                                        breakdown="model")["breakdown"]}
    assert parts["modele-inedit"]["unpriced"] == 1 and parts["modele-inedit"]["nano_usd"] is None
    assert parts["modele-inedit"]["incomplete"] is True
    assert parts["claude-sonnet-5"]["incomplete"] is True
    assert parts["claude-haiku-4-5"]["incomplete"] is False


def test_un_montant_PROVISOIRE_se_dit_a_la_ligne_a_la_part_et_au_total(client, monde):
    rendu = _cout(client, monde["provisoire"], op="run", run_id="run_m",
                  breakdown="model", detail=True)
    total = rendu["total"]
    assert (total["price_unverified"], total["price_unverified_attempts"]) == (True, 1)
    assert total["nano_usd"] == UN_MISTRAL + UN_OPUS, "le provisoire est COMPTÉ"
    assert total["unpriced_reasons"] == {runner_prix.PRIX_ABSENT: 1}, \
        "le cache non sourcé reste absent, pas provisoire"
    parts = {p["key"]: p for p in rendu["breakdown"]}
    assert (parts["mistral-large-2512"]["price_unverified"],
            parts["mistral-large-2512"]["unpriced"]) == (True, 1)
    assert parts["claude-opus-5"]["price_unverified"] is False
    lignes = {(ligne["model"], ligne["usage_cache_read"]): ligne for ligne in rendu["lines"]}
    assert lignes[("mistral-large-2512", 0)]["price_unverified"] is True
    assert lignes[("mistral-large-2512", 0)]["nano_usd"] == UN_MISTRAL
    absent = lignes[("mistral-large-2512", 40)]
    assert (absent["nano_usd"], absent["price_unverified"], absent["unpriced_reason"]) == (
        None, False, runner_prix.PRIX_ABSENT)


def test_un_run_commence_avant_la_premiere_mesure_ne_se_dit_pas_complet(client, monde):
    rendu = _cout(client, monde["a"], op="run", run_id=monde["run_avant_mesure"])
    assert rendu["total"]["attempts"] == 1 and rendu["measured_since"]
    assert rendu["total"]["incomplete_reasons"] == ["before_measurement"]


# ── 4. la forme servie ────────────────────────────────────────────────────────

@pytest.mark.parametrize("corps,fenetre", [
    ({"op": "org", "days": 100_000}, 366), ({"op": "org", "days": 0}, 1),
    ({"op": "org"}, 30), ({"op": "run", "run_id": "run_a", "days": 7}, None),
])
def test_la_fenetre_est_bornee_et_celle_qui_est_appliquee_est_servie(client, monde, corps, fenetre):
    assert _cout(client, monde["a"], **corps)["window_days"] == fenetre


def test_chaque_forme_servie_est_exactement_celle_que_le_modele_declare(client, monde):
    from oto_mcp.capabilities.runner_cost import CostLine, CostNotAttested, CostPart, CostTotal
    rendu = _cout(client, monde["plancher"], op="run", run_id="run_p", detail=True,
                  breakdown="source")
    assert set(rendu["total"]) == set(CostTotal.model_fields)
    assert set(rendu["not_attested"]) == set(CostNotAttested.model_fields)
    assert all(set(p) == set(CostPart.model_fields) for p in rendu["breakdown"])
    assert rendu["lines"] and all(set(ligne) == set(CostLine.model_fields)
                                  for ligne in rendu["lines"])
    assert rendu["lines_truncated"] is False


def test_une_page_de_lignes_tronquee_le_dit(client, monde):
    rendu = _cout(client, monde["longue"], op="run", run_id="run_long", detail=True)
    assert len(rendu["lines"]) == RA.LIGNES_TENTATIVES_MAX
    assert rendu["lines_truncated"] is True
    assert rendu["total"]["attempts"] == RA.LIGNES_TENTATIVES_MAX + 1


@pytest.mark.parametrize("corps,code", [
    ({"op": "fleet"}, "missing_fields"), ({"op": "agent", "detail": True, "trigger_id": 1},
                                          "detail_requires_run")])
def test_les_refus_declares_sont_rendus_par_la_route(client, monde, corps, code):
    r = _poste(client, monde["a"], corps)
    assert (r.status_code, r.json().get("error")) == (400, code)
