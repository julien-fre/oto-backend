"""Un worker qui ne tient AUCUNE clé de modèle à lui — `org_key_only`.

C'est ce qui permet d'ouvrir une famille de modèles (Anthropic) aux clients qui
apportent leur clé, sans que la plateforme finance un seul jeton. Deux gardes,
et chacune ferme un défaut différent :

1. **il ne réserve que les travaux de SA famille** — jamais ceux d'un agent posé
   sans modèle, que les workers existants servent sur LEUR modèle. Sans elle, un
   pool Anthropic volerait les agents historiques, leur ferait changer de
   fournisseur en silence, et — faute de clé — les ferait échouer ;
2. **un travail dont l'org n'a pas déposé la clé est ARRÊTÉ à la réservation**,
   raison écrite, que le réglage `runner.org_key_required` soit posé ou non —
   jamais remis sans clé à un worker qui n'en a pas.

Ce fichier tient la capacité et la remise ; le filtre SQL vit dans
`test_worker_cles_clients_db.py`.
"""
from __future__ import annotations

import pytest

from oto_mcp.capabilities import runner_jobs as RJ
from oto_mcp.capabilities._types import AuthzDenied, ResolvedCtx

ORG = 42


@pytest.fixture(autouse=True)
def _reglage_eteint(monkeypatch):
    """`runner.org_key_required` NON posé : la garde de ce lot doit mordre SANS lui."""
    monkeypatch.setattr("oto_mcp.db.connector_settings.get_connector_setting",
                        lambda portee, ident, fournisseur, cle: None)


@pytest.fixture
def arret(monkeypatch):
    vu = {}
    monkeypatch.setattr(RJ.db, "arreter_definitivement",
                        lambda job_id, sub, raison: vu.update(job=job_id, raison=raison) or True)
    return vu


def _travail(**kw):
    return {"id": 9, "org_id": ORG, "sub": "alexis", "delegated_token": "otd_x",
            "payload": {"model": "claude-sonnet-5", "model_family": "anthropic"}, **kw}


def _remise(depot="anthropic", *, org_key_only, worker=True):
    return RJ._avec_cle(_travail(), depot, "worker:banc", worker=worker,
                        org_key_only=org_key_only)


# ── la remise ─────────────────────────────────────────────────────────────────

def test_sans_cle_deposee_le_travail_est_ARRETE_meme_reglage_eteint(monkeypatch, arret):
    """⚠️ LE banc du lot côté remise. Le réglage n'est pas posé : sans cette garde,
    le travail partirait sans clé vers un worker qui n'en a pas — il échouerait
    chez le fournisseur, ou trouverait une clé oubliée dans l'environnement et la
    ferait payer."""
    monkeypatch.setattr(RJ, "_cle_de_modele", lambda org, depot: None)
    rendu = _remise(org_key_only=True)
    assert arret["job"] == 9, "arrêté pour de bon, pas relâché dans la file"
    assert "anthropic" in rendu["delegation_refusee"]
    assert "Dépose" in rendu["delegation_refusee"], "le refus dit quoi faire"
    assert rendu["delegated_token"] is None, "un travail qui ne tournera pas n'a pas de pouvoir"
    assert "model_key" not in rendu


def test_avec_cle_deposee_la_cle_part_avec_le_travail(monkeypatch, arret):
    monkeypatch.setattr(RJ, "_cle_de_modele", lambda org, depot: "sk-ant-de-l-org")
    rendu = _remise(org_key_only=True)
    assert rendu["model_key"] == "sk-ant-de-l-org"
    assert "delegation_refusee" not in rendu and not arret


def test_un_worker_ORDINAIRE_sans_cle_deposee_garde_le_comportement_d_avant(monkeypatch, arret):
    """Rien ne change pour les workers existants : sans clé et sans réglage, le
    travail part sans clé et le worker tourne sur la sienne."""
    monkeypatch.setattr(RJ, "_cle_de_modele", lambda org, depot: None)
    rendu = _remise(org_key_only=False)
    assert "delegation_refusee" not in rendu and not arret
    assert rendu["delegated_token"] == "otd_x"


def test_un_MEMBRE_qui_reserve_n_est_jamais_arrete(monkeypatch, arret):
    """La garde vise le worker de plateforme : un membre ne reçoit jamais de clé,
    il n'y a donc rien à attendre ni à refuser."""
    monkeypatch.setattr(RJ, "_cle_de_modele", lambda org, depot: None)
    rendu = _remise(org_key_only=True, worker=False)
    assert "delegation_refusee" not in rendu and not arret


# ── la capacité ───────────────────────────────────────────────────────────────

def _claim(**kw):
    # Une org nommée : la capacité exige le périmètre AVANT d'examiner le claim.
    return RJ._jobs(ResolvedCtx(sub="worker:banc", org_id=ORG),
                    RJ.JobsInput(op="claim", **kw))


def test_org_key_only_SANS_provider_est_refuse_nommement(monkeypatch):
    """Sans dépôt nommé il n'y a aucune clé à attendre : le refus est fait avant
    toute lecture de la file."""
    monkeypatch.setattr(RJ.db, "claim_next_job",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("file lue")))
    with pytest.raises(AuthzDenied) as e:
        _claim(org_key_only=True)
    assert (e.value.status, e.value.code) == (400, "org_key_only_without_provider")


def test_la_capacite_transmet_famille_seule_aux_DEUX_reservations(monkeypatch):
    """Il y a deux réservations dans le claim (avant et après avoir fait avancer
    une campagne) : oublier le filtre sur la seconde rouvrirait le vol de travaux
    par l'autre porte."""
    vus = []
    monkeypatch.setattr(RJ.db, "claim_next_job",
                        lambda *a, **k: vus.append(k.get("famille_seule")) or None)
    monkeypatch.setattr(RJ, "_produire_pour_une_campagne", lambda org, bail: None)
    assert _claim(provider="anthropic", org_key_only=True) == {"job": None}
    assert vus == [True, True]


def test_par_defaut_la_capacite_ne_filtre_rien_de_plus(monkeypatch):
    vus = []
    monkeypatch.setattr(RJ.db, "claim_next_job",
                        lambda *a, **k: vus.append(k.get("famille_seule")) or None)
    monkeypatch.setattr(RJ, "_produire_pour_une_campagne", lambda org, bail: None)
    _claim(provider="mistral")
    assert vus == [False, False]


def test_la_capacite_REMET_le_mode_a_la_remise_de_cle(monkeypatch):
    """⚠️ Réclamé par une épreuve de chute : retirer `org_key_only` de l'appel à
    `_avec_cle` dans la capacité ne faisait rougir aucun banc — ceux du dessus
    rendent une file vide, donc la remise ne tourne jamais. Ici un travail est
    RÉSERVÉ, sans clé déposée, par un worker de plateforme en mode clés clients :
    il doit sortir arrêté, pas remis sans clé."""
    arrete = {}
    job = {"id": 11, "org_id": ORG, "sub": "alexis",
           "payload": {"model": "claude-sonnet-5", "model_family": "anthropic"}}
    monkeypatch.setattr(RJ.db, "claim_next_job", lambda *a, **k: dict(job))
    monkeypatch.setattr(RJ, "_charge_servie", lambda j: j)
    monkeypatch.setattr(RJ, "_delegue", lambda j, bail, sub: {**j, "delegated_token": "otd_x"})
    monkeypatch.setattr(RJ, "_cle_de_modele", lambda org, depot: None)
    monkeypatch.setattr(RJ.db, "arreter_definitivement",
                        lambda job_id, sub, raison: arrete.update(job=job_id) or True)
    rendu = RJ._jobs(ResolvedCtx(sub="worker:banc", org_id=ORG, platform_worker=True),
                     RJ.JobsInput(op="claim", provider="anthropic", org_key_only=True))
    assert arrete.get("job") == 11
    assert rendu["job"]["delegation_refusee"] and rendu["job"]["delegated_token"] is None
