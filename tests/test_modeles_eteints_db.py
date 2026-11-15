"""Les modèles qu'une org ÉTEINT pour ses agents, SUR UNE VRAIE BASE : la colonne (tous
allumés par défaut), l'interrupteur et ses refus, le catalogue qui les marque et redérive
le défaut, la pose refusée, le travail d'un agent éteint parti sur le défaut, et le travail
déjà en file arrêté à la réservation.
"""
from __future__ import annotations

import pytest

from oto_mcp.capabilities._types import AuthzDenied

OPUS, SONNET, HAIKU = "claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5"


@pytest.fixture
def org(live, monkeypatch):
    from oto_mcp import db
    from oto_mcp.capabilities import _cle_exigee
    from oto_mcp.db._conn import _connect
    # Anthropic servie, aucune clé exigée : le défaut de l'org est Sonnet.
    monkeypatch.setattr(_cle_exigee, "manquantes", lambda *a, **k: [])
    monkeypatch.setattr(db, "runner_arme", lambda org_id: {"families": ["anthropic"]})
    with _connect() as c:
        return c.execute("INSERT INTO orgs (name) VALUES ('banc-modeles-eteints') "
                         "RETURNING id").fetchone()["id"]


def _reglage(op, org_id, **champs):
    from oto_mcp.capabilities import org_console as oc
    from oto_mcp.capabilities._types import ResolvedCtx
    return oc._org_settings(ResolvedCtx(sub="admin", org_id=org_id, role="org_admin"),
                            oc.OrgSettingsInput(op=op, domain="models", org_id=org_id,
                                                **champs))


def test_tous_allumes_par_defaut_puis_un_eteint_et_rallume(org):
    assert _reglage("get", org) == {"org_id": org, "disabled": []}
    assert _reglage("set", org, model=OPUS, enabled=False) == {"org_id": org,
                                                               "disabled": [OPUS]}
    # Idempotent : l'éteindre deux fois ne le liste qu'une.
    assert _reglage("set", org, model=OPUS, enabled=False)["disabled"] == [OPUS]
    assert _reglage("set", org, model=OPUS, enabled=True)["disabled"] == []


def test_un_modele_inconnu_ou_un_abonnement_ne_se_bascule_pas(org):
    for model in ("gpt-5", "sub:sonnet"):
        with pytest.raises(AuthzDenied) as e:
            _reglage("set", org, model=model, enabled=False)
        assert (e.value.status, e.value.code) == (400, "unknown_model")


def test_une_org_inconnue_est_404(live):
    from oto_mcp.capabilities.orgs import models as om
    with pytest.raises(AuthzDenied) as e:
        om.lire(999_999)
    assert (e.value.status, e.value.code) == (404, "unknown_org")


def test_le_modele_par_defaut_ne_s_eteint_pas(org):
    with pytest.raises(AuthzDenied) as e:
        _reglage("set", org, model=SONNET, enabled=False)
    assert (e.value.status, e.value.code) == (409, "model_is_default")
    # Le rallumer (ou le laisser allumé) reste permis.
    assert _reglage("set", org, model=SONNET, enabled=True)["disabled"] == []


def test_le_catalogue_marque_l_eteint_et_redérive_le_defaut(org):
    from oto_mcp import db
    from oto_mcp.capabilities import _modele
    db.modeles_eteints.poser(org, SONNET, False)   # posé en base, hors de la garde 409
    etat = _modele.etat_servi({"families": ["anthropic"]}, org)
    par_id = {m["id"]: m for m in etat["models"]}
    assert par_id[SONNET]["allowed"] is False and par_id[SONNET]["disabled"] is True
    assert par_id[SONNET]["default"] is False
    assert par_id[OPUS]["default"] is True and "disabled" not in par_id[OPUS]
    assert sum(1 for m in etat["models"] if m["default"]) == 1


def test_la_pose_sur_un_modele_eteint_est_refusee(org):
    from oto_mcp.capabilities import _modele
    _reglage("set", org, model=OPUS, enabled=False)
    with pytest.raises(AuthzDenied) as e:
        _modele.exige_modele_permis(org, OPUS)
    assert (e.value.status, e.value.code) == (403, "model_disabled")
    _modele.exige_modele_permis(org, HAIKU)


def test_l_agent_d_un_modele_eteint_part_sur_le_defaut(org):
    from oto_mcp.capabilities import _modele
    assert _modele.charge_de_l_org(org, OPUS)["model"] == OPUS
    _reglage("set", org, model=OPUS, enabled=False)
    charge = _modele.charge_de_l_org(org, OPUS)
    assert charge["model"] == SONNET and charge["model_family"] == "anthropic"
    assert charge["modele_remplace"]["de"] == OPUS
    assert charge["modele_remplace"]["par"] == SONNET
    assert charge["modele_remplace"]["raison"] == _modele.RAISON_MODELE_REMPLACE


def test_l_horloge_enfile_le_defaut_pour_un_agent_eteint(org, monkeypatch):
    from oto_mcp import runner_tick
    from oto_mcp.capabilities import _projet_de_l_agent
    import datetime
    monkeypatch.setattr(_projet_de_l_agent, "de_l_agent",
                        lambda t, sub: type("P", (), {"project_id": None,
                                                      "illisible": False})())
    _reglage("set", org, model=OPUS, enabled=False)
    t = {"id": 5, "org_id": org, "cron": "5 6 * * *", "tz": "Europe/Paris",
         "procedure": "veille", "project_id": None, "tools": [], "input": "go",
         "label": None, "max_steps": None, "model": OPUS}
    charge = runner_tick.charge_du_declencheur(
        t, datetime.datetime(2026, 11, 15, 6, 5, tzinfo=datetime.timezone.utc))
    assert charge["model"] == SONNET and charge["modele_remplace"]["de"] == OPUS


def test_un_travail_deja_en_file_est_arrete_a_la_reservation(org, monkeypatch):
    from oto_mcp.capabilities import _modele, runner_jobs
    vu = {}

    def refuser(job, appelant, raison):
        vu["raison"] = raison
        return {"refuse": True}
    monkeypatch.setattr(runner_jobs, "_refuser_sans_cle", refuser)
    _reglage("set", org, model=OPUS, enabled=False)
    job = {"id": 1, "org_id": org, "sub": "admin",
           "payload": {"model": OPUS, "model_family": "anthropic"}}
    assert runner_jobs._avec_cle(job, "anthropic", "worker", worker=True) == {"refuse": True}
    assert vu["raison"] == _modele.RAISON_MODELE_ETEINT
