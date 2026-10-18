"""La clé de modèle PARTAGÉE : une clé Anthropic de plateforme (ou du tenant), servie
à une org qui n'a pas déposé la sienne et à qui elle est ACCORDÉE — puis mesurée au
journal pour être refacturée.

Ce que ces bancs tiennent :

1. **le coût se calcule au tarif du fournisseur, ou pas du tout** — un poste non
   déclaré ou un modèle sans tarif ne fabrique jamais un coût, nul ou non ;
2. **à la réservation, la clé de l'org passe d'abord** ; la clé partagée ne sert
   qu'une org qui n'a RIEN déposé, et le travail est estampillé avant de partir ;
3. **une clé partagée accordée vaut une clé** pour la garde d'argent : ni le
   réglage `runner.org_key_required`, ni un worker « clés d'org seules » ne
   refusent l'org à qui elle est accordée ;
4. **à la conclusion, une ligne par tentative estampillée** — `key_mode` du rang,
   `quantity` en micro-dollars, sous `anthropic_tokens`, et rien d'autre.
"""
from __future__ import annotations

import logging

import pytest

from oto_mcp import credentials_store, providers, runner_models
from oto_mcp.capabilities import _cle_exigee as CE
from oto_mcp.capabilities import runner_jobs as RJ
from oto_mcp.capabilities._types import ResolvedCtx

ORG = 42


# ── 1. le coût ───────────────────────────────────────────────────────────────

def _cout(model="claude-sonnet-5", **kw):
    postes = dict(entree=1_000, sortie=1_000, ecriture_cache=1_000, lecture_cache=1_000)
    postes.update(kw)
    return runner_models.cout_en_microdollars(model, **postes)


def test_le_cout_est_la_somme_des_postes_au_tarif():
    # Sonnet 5 : 2 / 10 / 2,5 / 0,2 $ par million → autant de µ$ par jeton.
    assert _cout() == 2_000 + 10_000 + 2_500 + 200
    assert _cout("claude-opus-5", entree=1, sortie=0, ecriture_cache=0,
                 lecture_cache=0) == 5


def test_le_cout_s_arrondit_au_dessus_sans_bruit_flottant():
    assert _cout(entree=0, sortie=0, ecriture_cache=0, lecture_cache=5) == 1, \
        "0,20 × 5 vaut 1 µ$, pas 2"
    assert _cout(entree=0, sortie=0, ecriture_cache=0, lecture_cache=1) == 1


@pytest.mark.parametrize("poste", ["entree", "sortie", "ecriture_cache", "lecture_cache"])
def test_un_poste_non_declare_ne_se_facture_pas(poste):
    assert _cout(**{poste: None}) is None, "None n'est pas zéro"


def test_un_id_DATE_prend_le_tarif_de_son_alias_et_rien_d_autre():
    assert _cout("claude-sonnet-5-20260601") == _cout("claude-sonnet-5")
    assert _cout("claude-opus-5-5") is None, "Opus 5.5 n'est pas Opus 5 daté"


def test_un_modele_sans_tarif_ne_se_facture_pas():
    assert _cout("mistral-large-2512") is None
    assert _cout(None) is None


def test_chaque_modele_anthropic_du_catalogue_a_son_tarif():
    """Un modèle ajouté au catalogue sans tarif serait servi sur la clé partagée et
    jamais mesuré : il rougit ici, pas sur une facture."""
    anthropic = {m.id for m in runner_models.MODELES if m.family == "anthropic"}
    assert anthropic <= set(runner_models.TARIFS_ANTHROPIC)


def test_le_connecteur_declare_une_cle_de_plateforme_FERMEE():
    c = providers.connector_for_provider("anthropic")
    assert "platform" in c.auth_modes and "byo_org" in c.auth_modes
    assert c.platform_key_open is False, "le grant de l'org EST le droit"
    assert c.kind == "credential" and c.cardinality == "mono"


# ── la résolution de la clé partagée ────────────────────────────────────────

class _RC:
    def __init__(self, mode, key="sk-partagee"):
        self.mode, self.key, self.config = mode, key, {}


def test_un_fournisseur_sans_cle_de_plateforme_n_a_pas_de_cle_partagee(monkeypatch):
    from oto_mcp import access
    monkeypatch.setattr(access, "resolve_credential",
                        lambda *a, **k: pytest.fail("rien n'est résolu"))
    assert CE.cle_partagee(ORG, "mistral", sub="alexis") is None


@pytest.mark.parametrize("mode,attendu", [
    (credentials_store.PLATFORM, True), (credentials_store.TENANT, True),
    ("org", False), ("group", False)])
def test_seul_un_rang_partage_est_une_cle_partagee(monkeypatch, mode, attendu):
    from oto_mcp import access
    vu = {}

    def resoudre(provider, sub=None, **k):
        from oto_mcp import session_org
        vu.update(org=session_org.current_call_org(), sub=sub, k=k)
        return _RC(mode)
    monkeypatch.setattr(access, "resolve_credential", resoudre)
    rc = CE.cle_partagee(ORG, "anthropic", sub="alexis")
    assert (rc is not None) is attendu
    assert vu["org"] == ORG and vu["sub"] == "alexis", "dans l'org DU TRAVAIL"
    assert vu["k"]["check_usage"] is False and vu["k"]["emit_on_failure"] is False


def test_pas_de_grant_pas_de_cle_partagee(monkeypatch):
    from oto_mcp import access
    from oto_mcp.mcp_errors import McpError
    from mcp.types import ErrorData, INVALID_PARAMS

    def refus(*a, **k):
        raise McpError(ErrorData(code=INVALID_PARAMS, message="no key"))
    monkeypatch.setattr(access, "resolve_credential", refus)
    assert CE.cle_partagee(ORG, "anthropic", sub="alexis") is None


def test_sans_demandeur_la_cascade_de_l_org_seule(monkeypatch):
    from oto_mcp import access
    vu = {}
    monkeypatch.setattr(access, "_resolve_credential_anon",
                        lambda p, want, org, check_usage=True:
                        vu.update(org=org, check=check_usage) or _RC("platform"))
    assert CE.cle_partagee(ORG, "anthropic") is not None
    assert vu == {"org": ORG, "check": False}


def test_une_cle_partagee_accordee_n_est_pas_manquante(monkeypatch):
    monkeypatch.setattr(CE, "cle_exigee", lambda org, f: True)
    monkeypatch.setattr(CE, "cle_deposee", lambda org, f: False)
    monkeypatch.setattr(CE, "cle_partagee", lambda org, f, sub=None: _RC("platform"))
    assert CE.manquantes(ORG, ["anthropic"]) == []
    monkeypatch.setattr(CE, "cle_partagee", lambda org, f, sub=None, strict=False: None)
    assert CE.manquantes(ORG, ["anthropic"]) == ["anthropic"]


# ── 2–3. à la réservation ───────────────────────────────────────────────────

@pytest.fixture
def remise(monkeypatch):
    """Rien de déposé par l'org, aucune exigence, une clé partagée accordée au rang
    plateforme. Chaque banc retouche ce qu'il juge."""
    vu = {"estampilles": []}
    monkeypatch.setattr(RJ, "_cle_de_modele", lambda org, depot: (None, None))
    monkeypatch.setattr(RJ, "_depot_pose", lambda org, depot: False)
    monkeypatch.setattr(CE, "cle_exigee", lambda org, f: False)
    monkeypatch.setattr(CE, "cle_partagee",
                        lambda org, f, sub=None, strict=False: vu.update(demande=(org, f, sub))
                        or _RC(credentials_store.PLATFORM))
    monkeypatch.setattr(RJ.db, "estampiller_cle_partagee",
                        lambda job_id, sub, depot, mode:
                        vu["estampilles"].append((job_id, sub, depot, mode)) or True)
    monkeypatch.setattr(RJ.db, "arreter_definitivement",
                        lambda job_id, sub, raison: vu.update(arret=raison) or True)
    monkeypatch.setattr(RJ.db, "rendre_a_la_file",
                        lambda job_id, sub, raison, delai_s=60: vu.update(rendu=raison) or True)
    monkeypatch.setattr(CE, "cle_de_plateforme_posee", lambda f: True)
    return vu


def _travail(**kw):
    return {"id": 9, "org_id": ORG, "sub": "alexis", "delegated_token": "otd_x",
            "payload": {"model_family": "anthropic"}, **kw}


def _reserver(**kw):
    kw.setdefault("metre", True)
    return RJ._avec_cle(_travail(), "anthropic", "worker:banc", worker=True, **kw)


def test_sans_cle_d_org_la_cle_partagee_part_ESTAMPILLEE(remise):
    rendu = _reserver()
    assert rendu["model_key"] == "sk-partagee"
    assert rendu["meter_usage"] is True, "le worker doit rapporter chaque appel"
    assert remise["demande"] == (ORG, "anthropic", "alexis"), "au nom du demandeur"
    assert remise["estampilles"] == [(9, "worker:banc", "anthropic", "platform")]


def test_une_estampille_qui_ne_s_ecrit_pas_ne_sert_RIEN(remise, monkeypatch):
    monkeypatch.setattr(RJ.db, "estampiller_cle_partagee", lambda *a: False)
    assert _reserver() is None, "jamais une clé qu'on ne saurait pas refacturer"


def test_la_cle_de_l_org_passe_d_abord(remise, monkeypatch):
    monkeypatch.setattr(RJ, "_cle_de_modele", lambda org, depot: ("sk-de-l-org", None))
    rendu = _reserver()
    assert rendu["model_key"] == "sk-de-l-org" and "meter_usage" not in rendu
    assert "demande" not in remise and remise["estampilles"] == []


def test_une_cle_DEPOSEE_mais_illisible_ne_bascule_pas_sur_la_partagee(remise, monkeypatch):
    """L'org paie déjà son fournisseur : la facturer sur la nôtre pour un hoquet du
    coffre la ferait payer deux fois."""
    monkeypatch.setattr(RJ, "_depot_pose", lambda org, depot: True)
    rendu = _reserver()
    assert "model_key" not in rendu and "demande" not in remise


def test_l_exigence_ne_refuse_pas_l_org_a_qui_la_cle_est_accordee(remise, monkeypatch):
    monkeypatch.setattr(CE, "cle_exigee", lambda org, f: True)
    rendu = _reserver()
    assert rendu["model_key"] == "sk-partagee" and "arret" not in remise


def test_un_worker_cles_d_org_seules_sert_la_cle_partagee(remise):
    rendu = _reserver(org_key_only=True)
    assert rendu["model_key"] == "sk-partagee" and "arret" not in remise


def test_un_worker_qui_ne_mesure_pas_ne_recoit_JAMAIS_la_cle_partagee(remise):
    """Un runner d'avant la mesure par tour tournerait sur notre clé en ne rapportant
    qu'à la fin — ou jamais. L'org y a droit : le travail retourne à la file, sans
    compter la tentative, pour un worker qui mesure."""
    assert _reserver(metre=False) is None
    assert remise["estampilles"] == [] and "rendu" in remise and "arret" not in remise


def test_la_ferme_ne_recoit_jamais_la_cle_partagee(remise):
    rendu = _reserver(ferme=True)
    assert "model_key" not in rendu and "ferme" in rendu["delegation_refusee"]
    assert remise["estampilles"] == []


def test_une_org_SANS_acces_ne_tourne_pas_sur_la_cle_du_worker(remise, monkeypatch):
    """⚠️ Le trou que la clé partagée ouvrait : l'instance paie Anthropic, l'org n'a
    ni clé ni accès (jamais accordé, ou retiré crédits épuisés) — servie sans clé,
    elle tournait sur la clé d'ENVIRONNEMENT du worker, sans rien mesurer."""
    monkeypatch.setattr(CE, "cle_partagee", lambda org, f, sub=None, strict=False: None)
    rendu = _reserver()
    assert "model_key" not in rendu
    assert "déposer" in rendu["delegation_refusee"] and "accès" in rendu["delegation_refusee"]


def test_sans_cle_de_plateforme_posee_rien_ne_change(remise, monkeypatch):
    monkeypatch.setattr(CE, "cle_partagee", lambda org, f, sub=None, strict=False: None)
    monkeypatch.setattr(CE, "cle_de_plateforme_posee", lambda f: False)
    rendu = _reserver()
    assert "delegation_refusee" not in rendu and "model_key" not in rendu


def test_sans_grant_l_exigence_arrete_toujours(remise, monkeypatch):
    monkeypatch.setattr(CE, "cle_exigee", lambda org, f: True)
    monkeypatch.setattr(CE, "cle_partagee", lambda org, f, sub=None, strict=False: None)
    rendu = _reserver()
    assert rendu["delegation_refusee"] and remise["estampilles"] == []


# ── 4. à la conclusion ──────────────────────────────────────────────────────

_RESULTAT = {"model": "claude-sonnet-5", "usage_input": 1_000, "usage_output": 500,
             "usage_cache_write": 0, "usage_cache_read": 10_000, "usage_tokens": 1_500}


def _conclu(**kw):
    return {"status": "done", "run_id": "r1", "org_id": ORG, "sub": "alexis",
            "attempts": 2, "cle_partagee": {"depot": "anthropic", "mode": "platform",
                                            "tentative": 2}, **kw}


@pytest.fixture
def journal(monkeypatch):
    lignes = []
    monkeypatch.setattr(RJ.db, "insert_tool_call", lignes.append)
    return lignes


def test_une_tentative_estampillee_s_inscrit_au_journal(journal):
    RJ._mesurer_cle_partagee(9, _conclu(), _RESULTAT)
    [ligne] = journal
    assert ligne["tool"] == "anthropic_tokens"
    assert ligne["quantity"] == 1_000 * 2 + 500 * 10 + 10_000 * 0.2
    assert ligne["key_mode"] == "platform" and ligne["ok"] is True
    assert (ligne["org_id"], ligne["sub"], ligne["run_id"]) == (ORG, "alexis", "r1")
    assert ligne["args"]["runner_job_id"] == 9
    from oto_mcp.db import usage
    assert not set(ligne["args"]) & set(usage.BILLABLE_JOB_ARGS), \
        "deux tentatives sont deux dépenses : jamais dédupliquées comme un job"


def test_un_echec_qui_a_consomme_se_mesure_aussi(journal):
    RJ._mesurer_cle_partagee(9, _conclu(status="pending"), _RESULTAT)
    assert journal and journal[0]["ok"] is True, "la dépense est réelle"


@pytest.mark.parametrize("conclu", [
    _conclu(cle_partagee=None),
    _conclu(attempts=3),   # estampille d'une tentative précédente
])
def test_une_tentative_non_estampillee_ne_coute_rien(journal, conclu):
    RJ._mesurer_cle_partagee(9, conclu, _RESULTAT)
    assert journal == []


def test_un_cout_inconnu_ne_s_invente_pas_et_se_dit(journal, caplog):
    with caplog.at_level(logging.ERROR):
        RJ._mesurer_cle_partagee(9, _conclu(), {**_RESULTAT, "usage_cache_read": None})
    assert journal == [] and "NON mesuré" in caplog.text


def test_un_journal_qui_echoue_ne_casse_pas_la_conclusion(monkeypatch, caplog):
    def explose(_):
        raise RuntimeError("base")
    monkeypatch.setattr(RJ.db, "insert_tool_call", explose)
    with caplog.at_level(logging.ERROR):
        RJ._mesurer_cle_partagee(9, _conclu(), _RESULTAT)
    assert "NON journalisé" in caplog.text


def test_complete_mesure_ce_que_la_base_a_conclu(monkeypatch, journal):
    monkeypatch.setattr(RJ.db, "complete_job",
                        lambda job_id, sub, ok, error=None, run_id=None, result=None:
                        _conclu(run_id=None))
    RJ._jobs(ResolvedCtx(sub="worker:banc", org_id=ORG),
             RJ.JobsInput(op="complete", job_id=9, ok=True, result=_RESULTAT))
    assert [l["tool"] for l in journal] == ["anthropic_tokens"]


# ── 5. la mesure PAR TOUR ───────────────────────────────────────────────────

_TOUR = {"model": "claude-sonnet-5", "usage_input": 100, "usage_output": 50,
         "usage_cache_write": 0, "usage_cache_read": 1_000}


@pytest.fixture
def tours(monkeypatch):
    vu = []

    def metrer(job_id, sub, tour, cout, postes, args):
        vu.append(dict(job=job_id, sub=sub, tour=tour, cout=cout, postes=postes, args=args))
        return "inscrit"
    monkeypatch.setattr(RJ.db, "metrer_un_tour", metrer)
    return vu


def _metrer(tour=0, **usage):
    return RJ._jobs(ResolvedCtx(sub="worker:banc", org_id=ORG),
                    RJ.JobsInput(op="meter", job_id=9, turn=tour,
                                 result={**_TOUR, **usage}))


def test_chaque_tour_s_inscrit_avant_de_continuer(tours):
    out = _metrer(3)
    assert out == {"ok": True, "metered": True, "microdollars": 100 * 2 + 50 * 10 + 200,
                   "duplicate": False}
    [t] = tours
    assert (t["job"], t["sub"], t["tour"]) == (9, "worker:banc", 3)
    assert t["args"]["model"] == "claude-sonnet-5" and "cache_non_declare" not in t["args"]


@pytest.mark.parametrize("poste", ["usage_input", "usage_output"])
def test_un_tour_sans_entree_ou_sortie_est_REFUSE(tours, poste):
    """Le worker s'arrête sur ce refus : un travail ne continue pas sur notre clé
    sans que son coût soit inscrit."""
    from oto_mcp.capabilities._types import AuthzDenied
    with pytest.raises(AuthzDenied) as e:
        _metrer(**{poste: None})
    assert e.value.code == "usage_unmeasured" and tours == []


def test_un_modele_sans_tarif_est_REFUSE(tours):
    from oto_mcp.capabilities._types import AuthzDenied
    with pytest.raises(AuthzDenied) as e:
        _metrer(model="claude-inconnu")
    assert e.value.code == "model_unpriced" and tours == []


def test_un_cache_non_declare_se_lit_zero_et_le_dit(tours):
    _metrer(usage_cache_read=None)
    assert tours[0]["cout"] == 100 * 2 + 50 * 10
    assert tours[0]["args"]["cache_non_declare"] is True


def test_un_rapport_rejoue_ne_s_inscrit_pas_deux_fois(monkeypatch):
    monkeypatch.setattr(RJ.db, "metrer_un_tour", lambda *a: "deja_inscrit")
    assert _metrer()["duplicate"] is True


def test_la_cle_de_l_org_n_a_rien_a_mesurer(monkeypatch):
    monkeypatch.setattr(RJ.db, "metrer_un_tour", lambda *a: "sans_cle_partagee")
    assert _metrer() == {"ok": True, "metered": False}


def test_un_travail_qui_n_est_plus_au_worker_rend_job_inconnu(monkeypatch):
    from oto_mcp.capabilities._types import AuthzDenied
    monkeypatch.setattr(RJ.db, "metrer_un_tour", lambda *a: None)
    with pytest.raises(AuthzDenied) as e:
        _metrer()
    assert e.value.status == 404


def test_la_conclusion_n_inscrit_que_le_RESTE(journal):
    """Les tours font la facture ; la conclusion la rapproche du total déclaré."""
    total = 1_000 * 2 + 500 * 10 + 10_000 * 0.2
    est = {"depot": "anthropic", "mode": "platform", "tentative": 2,
           "microdollars": total - 300}
    RJ._mesurer_cle_partagee(9, _conclu(cle_partagee=est), _RESULTAT)
    [ligne] = journal
    assert ligne["quantity"] == 300 and ligne["args"]["turn"] == "reste"


def test_des_tours_qui_couvrent_le_total_ne_laissent_aucun_reste(journal):
    est = {"depot": "anthropic", "mode": "platform", "tentative": 2,
           "microdollars": 10**9}
    RJ._mesurer_cle_partagee(9, _conclu(cle_partagee=est), _RESULTAT)
    assert journal == [], "l'arrondi par tour ne rembourse rien et ne facture rien de plus"


# ── 6. un coffre qui ne répond pas : ni servi, ni arrêté ────────────────────

@pytest.mark.parametrize("panne", ["depot", "partagee", "posee"])
def test_un_doute_du_coffre_rend_le_travail_a_la_file(remise, monkeypatch, panne):
    """Un doute ne doit ni faire payer notre clé sans mesure, ni arrêter un travail
    avec une raison fausse (« pas d'accès, crédits ») — il retourne à la file, sans
    compter la tentative."""
    if panne == "depot":
        monkeypatch.setattr(RJ, "_depot_pose", lambda org, depot: None)
    elif panne == "partagee":
        def illisible(org, f, sub=None, strict=False):
            raise CE.CleIllisible("coffre")
        monkeypatch.setattr(CE, "cle_partagee", illisible)
    else:
        monkeypatch.setattr(CE, "cle_partagee", lambda org, f, sub=None, strict=False: None)
        monkeypatch.setattr(CE, "cle_de_plateforme_posee", lambda f: None)
    assert _reserver() is None
    assert "coffre illisible" in remise["rendu"]
    assert "arret" not in remise and remise["estampilles"] == []


def test_la_reservation_demande_le_doute_au_lieu_de_le_taire(remise, monkeypatch):
    vu = {}
    monkeypatch.setattr(CE, "cle_partagee",
                        lambda org, f, sub=None, strict=False: vu.update(strict=strict)
                        or _RC("platform"))
    _reserver()
    assert vu["strict"] is True


def test_cle_partagee_stricte_leve_sur_un_coffre_qui_casse(monkeypatch):
    from oto_mcp import access

    def casse(*a, **k):
        raise RuntimeError("pool")
    monkeypatch.setattr(access, "resolve_credential", casse)
    assert CE.cle_partagee(ORG, "anthropic", sub="alexis") is None
    with pytest.raises(CE.CleIllisible):
        CE.cle_partagee(ORG, "anthropic", sub="alexis", strict=True)
