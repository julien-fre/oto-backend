"""Déclencheurs du runner — le fuseau, la cadence plancher, et le tick qui enfile.

Trois familles : la validation à la pose (un cron d'arrosage est refusé, un fuseau
inconnu aussi, et le message NOMME le fautif), le calcul d'échéance dans le fuseau
déclaré (l'heure d'été ne décale rien en silence), et le tick — qui n'enfile que
s'il GAGNE le compare-and-swap (prod et preprod partagent la base : deux ticks,
un gagnant par échéance).
"""
from __future__ import annotations

import asyncio
import datetime
from zoneinfo import ZoneInfo

import pytest

from oto_mcp import runner_tick
from oto_mcp.capabilities import _outils_plateforme as OP
from oto_mcp.capabilities import runner_triggers as RT
from oto_mcp.capabilities._types import AuthzDenied, ResolvedCtx

# Ce banc double la base des agents et ne parle pas de leur partage (conftest).
pytestmark = pytest.mark.usefixtures("agents_sans_partages")



@pytest.fixture(autouse=True)
def _l_appelant_possede_l_agent(monkeypatch):
    """Ce fichier ne parle pas du PARTAGE d'agents — il a son banc
    (`test_partage_agents.py`). L'appelant y est lu propriétaire de tout agent
    qu'on lui présente ; sans cette doublure, la lecture de rôle irait chercher la
    vraie base."""
    from oto_mcp.capabilities import _acces_agent
    monkeypatch.setattr(_acces_agent, "niveaux",
                        lambda sub, org_id, agents: {int(t["id"]): "owner" for t in agents})

@pytest.fixture(autouse=True)
def _cle_de_modele_non_exigee(monkeypatch):
    """Ce fichier ne parle pas de la garde de clé de modèle — elle a son propre banc
    (`test_cle_de_modele_exigee.py`). Le réglage est lu ÉTEINT, comme sur toute
    plateforme qui ne l'a pas allumé : sans cette doublure, la lecture irait
    chercher la vraie base et chaque banc tomberait sur une raison qui n'est pas
    la sienne."""
    monkeypatch.setattr("oto_mcp.db.connector_settings.get_connector_setting",
                        lambda *a, **k: None)



def _ctx(sub="alexis", org_id=2):
    return ResolvedCtx(sub=sub, org_id=org_id)


def _appel(ctx, **kw):
    # Un agent hébergé déclare son modèle (24/09/2026) : sa règle a son banc.
    if kw.get("op") == "create":
        kw.setdefault("model", "claude-sonnet-5")
    return asyncio.run(RT._triggers(ctx, RT.TriggerInput(**kw)))


# ── la validation nomme le fautif ─────────────────────────────────────────────

def test_un_cron_darrosage_est_refuse():
    with pytest.raises(ValueError) as e:
        runner_tick.validate_cron("* * * * *", "Europe/Paris")
    assert "cadence trop serrée" in str(e.value)


def test_un_fuseau_inconnu_est_refuse_et_nomme():
    with pytest.raises(ValueError) as e:
        runner_tick.validate_cron("5 6 * * *", "Paris/France")
    assert "Paris/France" in str(e.value)


def test_une_expression_invalide_est_refusee_et_nommee():
    with pytest.raises(ValueError) as e:
        runner_tick.validate_cron("99 99 * * *", "Europe/Paris")
    assert "99 99" in str(e.value)


# ── l'échéance vit dans le fuseau déclaré ─────────────────────────────────────

def test_lecheance_sevalue_dans_le_fuseau_du_declencheur():
    """8h05 Europe/Paris un jour d'été = 6h05 UTC ; le même cron en UTC = 8h05 UTC.
    Si ces deux-là coïncidaient, le fuseau serait décoratif — et l'heure d'été
    décalerait les veilles en silence."""
    apres = datetime.datetime(2026, 8, 14, 0, 0, tzinfo=datetime.timezone.utc)
    paris = runner_tick.next_due("5 8 * * *", "Europe/Paris", apres=apres)
    utc = runner_tick.next_due("5 8 * * *", "UTC", apres=apres)
    assert paris.astimezone(datetime.timezone.utc).hour == 6
    assert utc.astimezone(datetime.timezone.utc).hour == 8


# ── la capacité : exigences et 404 org-scopé ──────────────────────────────────

def test_create_exige_la_procedure_et_le_cadencement(monkeypatch):
    """⚠️ `tools` N'EST PLUS exigé depuis le 03/09 : il se DÉDUIT de la procédure.
    Sans ça, le bouton « en faire un agent programmé » demanderait une liste
    d'outils — c'est-à-dire ne serait pas un bouton."""
    with pytest.raises(AuthzDenied) as e:
        _appel(_ctx(), op="create", procedure="veille-linkedin")
    assert e.value.code == "missing_fields"
    assert "cron" in str(e.value.message)
    assert "tools" not in str(e.value.message), (
        "le message exige encore les outils alors qu'ils se déduisent")


def test_create_valide_puis_pose_avec_le_fuseau_par_defaut(monkeypatch):
    vu = {}
    # Poser exige désormais un runner armé (`test_runner_trigger_sans_worker`) :
    # ce test-ci parle du fuseau, on lui donne l'org servie qu'il suppose.
    monkeypatch.setattr(RT.db, "runner_arme",
                        lambda org: {"armed": True, "workers": 1,
                                     "last_seen": "2026-09-02 07:00:00",
                                     "families": ["anthropic"]})
    # ⚠️ Un objet ne porte qu'UN agent : la création lit d'abord ce qui
    # existe. Sans doublure, ce banc irait interroger la vraie base.
    monkeypatch.setattr(RT.db, "triggers_for_procedure", lambda o, p: [])
    monkeypatch.setattr(RT.db, "create_trigger",
                        lambda org, sub, **kw: vu.update(kw, org=org) or {"id": 1, **kw})
    out = _appel(_ctx(), op="create", procedure="veille-linkedin",
                 cron="5 6 * * *", tools=["linkedin_unipile_post", "data_write"])
    assert vu["tz"] == "Europe/Paris", "le défaut est ÉCRIT, pas supposé"
    assert vu["next_due"] is not None and out["trigger"]["id"] == 1


@pytest.mark.parametrize("enabled, attendu", [(False, False), (True, True), (None, True)])
def test_create_respecte_enabled(monkeypatch, enabled, attendu):
    """Feedback 1297 : `create enabled=false` naissait allumé."""
    vu = {}
    monkeypatch.setattr(RT.db, "runner_arme",
                        lambda org: {"armed": True, "workers": 1,
                                     "last_seen": "2026-09-02 07:00:00",
                                     "families": ["anthropic"]})
    monkeypatch.setattr(RT.db, "triggers_for_procedure", lambda o, p: [])
    monkeypatch.setattr(RT.db, "create_trigger",
                        lambda org, sub, **kw: vu.update(kw) or {"id": 1, **kw})
    _appel(_ctx(), op="create", procedure="veille-linkedin", cron="5 6 * * *",
           tools=["data_write"], enabled=enabled)
    assert vu["enabled"] is attendu


def test_un_cadencement_invalide_rend_la_cause(monkeypatch):
    with pytest.raises(AuthzDenied) as e:
        _appel(_ctx(), op="create", procedure="x", cron="* * * * *", tools=["a"])
    assert e.value.code == "invalid_schedule"


def test_update_du_cron_seul_revalide_avec_le_fuseau_effectif(monkeypatch):
    """Changer le cron sans toucher au fuseau doit revalider avec le fuseau STOCKÉ —
    valider l'un sans l'autre laisserait passer un couple incohérent."""
    monkeypatch.setattr(RT.db, "get_trigger",
                        lambda i, o: {"id": i, "cron": "5 6 * * *", "tz": "UTC"})
    vu = {}
    monkeypatch.setattr(RT.db, "update_trigger",
                        lambda i, o, champs, **k: vu.update(champs) or {"id": i, **champs})
    _appel(_ctx(), op="update", trigger_id=3, cron="10 7 * * *")
    assert vu["tz"] == "UTC" and vu["cron"] == "10 7 * * *"
    assert vu["next_due"].astimezone(datetime.timezone.utc).hour == 7


def test_le_404_est_le_meme_pour_autrui_et_pour_linexistant(monkeypatch):
    monkeypatch.setattr(RT.db, "get_trigger", lambda i, o: None)
    with pytest.raises(AuthzDenied) as e:
        _appel(_ctx(), op="get", trigger_id=999)
    assert (e.value.status, e.value.code) == (404, "trigger_not_found")


# ── le tick : CAS gagné = enfile, CAS perdu = passe ───────────────────────────

def _declencheur(**kw):
    base = {"id": 5, "org_id": 2, "cron": "5 6 * * *", "tz": "Europe/Paris",
            "next_due": "2026-08-14 04:05:00", "procedure": "veille-linkedin",
            "project_id": None, "tools": ["data_write"], "input": None,
            "label": None, "max_steps": None, "model": "claude-sonnet-5"}
    base.update(kw)
    return base


def test_le_tick_enfile_quand_il_gagne_le_cas(monkeypatch):
    enfile = {}
    monkeypatch.setattr(runner_tick.db, "due_triggers", lambda limit=50: [_declencheur()])
    monkeypatch.setattr(runner_tick.db, "consume_due", lambda i, vu, prochaine: True)
    monkeypatch.setattr(runner_tick.db, "perimer_travaux_du_declencheur",
                        lambda t, o: 0)
    monkeypatch.setattr(runner_tick.db, "enqueue_job",
                        lambda org, kind, payload=None, **_: enfile.update(
                            org=org, kind=kind, payload=payload) or {"id": 9})
    assert runner_tick._tick() == 1
    assert enfile["org"] == 2 and enfile["kind"] == "start"
    assert enfile["payload"]["procedure"] == "veille-linkedin"
    assert enfile["payload"]["trigger_id"] == 5
    # La règle tient toujours — mais `input` n'en est plus l'exemple : il est
    # désormais COMPOSÉ quand le déclencheur n'en porte pas (14/09/2026), sans
    # quoi le worker refuse le travail. `max_steps` est resté nullable.
    assert "max_steps" not in enfile["payload"], "les champs None ne voyagent pas"
    assert enfile["payload"]["input"], (
        "un travail sans instruction de départ se fait refuser par le worker : "
        "le tick en dérive une quand le déclencheur n'en a pas")


def test_le_tick_passe_quand_le_cas_est_perdu(monkeypatch):
    """L'autre environnement (même base) a consommé l'échéance : on n'enfile PAS —
    sinon chaque échéance produirait deux runs, un par tick."""
    monkeypatch.setattr(runner_tick.db, "due_triggers", lambda limit=50: [_declencheur()])
    monkeypatch.setattr(runner_tick.db, "consume_due", lambda i, vu, prochaine: False)
    monkeypatch.setattr(runner_tick.db, "enqueue_job",
                        lambda *a, **k: pytest.fail("CAS perdu ⟹ aucun job"))
    assert runner_tick._tick() == 0


def test_un_cron_devenu_invalide_ne_bloque_pas_les_autres(monkeypatch):
    bons = {}
    monkeypatch.setattr(runner_tick.db, "due_triggers", lambda limit=50: [
        _declencheur(id=1, cron="pas un cron"),
        _declencheur(id=2),
    ])
    monkeypatch.setattr(runner_tick.db, "consume_due", lambda i, vu, p: True)
    monkeypatch.setattr(runner_tick.db, "perimer_travaux_du_declencheur",
                        lambda t, o: 0)
    monkeypatch.setattr(runner_tick.db, "enqueue_job",
                        lambda org, kind, payload=None, **_: bons.update(t=payload["trigger_id"]))
    assert runner_tick._tick() == 1
    assert bons["t"] == 2, "le déclencheur sain du tour est passé malgré le cassé"


# ── le dossier d'affichage ────────────────────────────────────────────────────

@pytest.mark.parametrize("sent, stored", [("  Clients A  ", "Clients A"), ("", None)])
def test_update_du_dossier(monkeypatch, sent, stored):
    """Le dossier d'affichage se pose rogné ; `""` le retire (NULL)."""
    monkeypatch.setattr(RT.db, "get_trigger", lambda i, o: {"id": i, "sub": "alexis"})
    vu = {}
    monkeypatch.setattr(RT.db, "update_trigger",
                        lambda i, o, champs, **k: vu.update(champs) or {"id": i, **champs})
    _appel(_ctx(), op="update", trigger_id=3, folder=sent)
    assert vu == {"folder": stored}


def test_dossier_absent_nest_pas_touche(monkeypatch):
    monkeypatch.setattr(RT.db, "get_trigger", lambda i, o: {"id": i, "sub": "alexis"})
    vu = {}
    monkeypatch.setattr(RT.db, "update_trigger",
                        lambda i, o, champs, **k: vu.update(champs) or {"id": i, **champs})
    _appel(_ctx(), op="update", trigger_id=3, label="Veille")
    assert "folder" not in vu


def test_dossier_trop_long(monkeypatch):
    """Un libellé, pas une phrase : refus nommé, et rien n'est écrit."""
    monkeypatch.setattr(RT.db, "get_trigger", lambda i, o: {"id": i, "sub": "alexis"})
    ecrit = []
    monkeypatch.setattr(RT.db, "update_trigger",
                        lambda *a, **k: ecrit.append(a) or {"id": 3})
    with pytest.raises(AuthzDenied) as e:
        _appel(_ctx(), op="update", trigger_id=3, label="x",
               folder="x" * (RT.FOLDER_MAX + 1))
    assert (e.value.status, e.value.code) == (400, "folder_too_long")
    assert ecrit == []


# ── ranger en lot ─────────────────────────────────────────────────────────────

def test_ranger_en_lot_chaque_agent_comme_seul(monkeypatch):
    """N agents, un appel : chacun passe par le geste unitaire ; un refus ne coupe pas
    le lot, il revient avec son code."""
    monkeypatch.setattr(RT.db, "get_trigger",
                        lambda i, o: None if i == 8 else {"id": i, "sub": "alexis"})
    ecrits = []
    monkeypatch.setattr(RT.db, "update_trigger",
                        lambda i, o, champs, **k: ecrits.append((i, champs)) or {"id": i, **champs})
    rep = _appel(_ctx(), op="update", trigger_ids=[3, 8, 5, 3], folder=" Clients A ")
    assert ecrits == [(3, {"folder": "Clients A"}), (5, {"folder": "Clients A"})]
    assert rep["total"] == 3 and rep["succeeded"] == 2
    assert rep["failed"] == [{"index": 1, "id": 8, "error": "trigger_not_found"}]


@pytest.mark.parametrize("kw, code", [
    ({"op": "get", "trigger_ids": [3]}, "bulk_unsupported"),
    # `create` et `list` passent AVANT la branche du lot : sans refus en amont, ils
    # ignoraient `trigger_ids` sans rien dire.
    ({"op": "create", "trigger_ids": [3], "folder": "A"}, "bulk_unsupported"),
    ({"op": "list", "trigger_ids": [3], "folder": "A"}, "bulk_unsupported"),
    ({"trigger_id": 3, "trigger_ids": [4], "folder": "A"}, "ambiguous_target"),
    ({"trigger_ids": [3]}, "bulk_folder_only"),
    ({"trigger_ids": [3], "folder": "A", "label": "x"}, "bulk_folder_only"),
    ({"trigger_ids": [], "folder": "A"}, "missing_fields"),
    ({"trigger_ids": list(range(RT._LOT_MAX + 1)), "folder": "A"}, "too_many_ids"),
    ({"trigger_ids": [3], "folder": "x" * (RT.FOLDER_MAX + 1)}, "folder_too_long"),
])
def test_ranger_en_lot_refus_avant_toute_ecriture(monkeypatch, kw, code):
    ecrit = []
    monkeypatch.setattr(RT.db, "update_trigger", lambda *a, **k: ecrit.append(a) or {"id": 3})
    monkeypatch.setattr(RT.db, "create_trigger", lambda *a, **k: ecrit.append(a) or {"id": 3})
    kw = {"op": "update", **kw}
    with pytest.raises(AuthzDenied) as e:
        _appel(_ctx(), **kw)
    assert (e.value.status, e.value.code) == (400, code)
    assert ecrit == []


def test_ranger_en_lot_une_panne_ne_coupe_pas_le_lot(monkeypatch):
    """Une panne sur UN agent (base, valeur inattendue) n'arrête pas le lot à moitié
    rangé : elle revient dans `failed` avec un code nommé, et les suivants passent."""
    monkeypatch.setattr(RT.db, "get_trigger", lambda i, o: {"id": i, "sub": "alexis"})
    ecrits = []

    def _ecrire(i, o, champs, **k):
        if i == 8:
            raise RuntimeError("connexion perdue")
        ecrits.append(i)
        return {"id": i, **champs}

    monkeypatch.setattr(RT.db, "update_trigger", _ecrire)
    rep = _appel(_ctx(), op="update", trigger_ids=[3, 8, 5], folder="Clients A")
    assert ecrits == [3, 5]
    assert rep["total"] == 3 and rep["succeeded"] == 2
    assert rep["failed"] == [{"index": 1, "id": 8, "error": "bulk_item_failed"}]


def test_le_dossier_d_un_agent_et_d_un_projet_ont_une_seule_definition():
    """Même borne, même refus, même `""` : une seule validation, partagée — deux
    copies finiraient par accepter pour un agent ce qu'elles refusent pour un projet."""
    from oto_mcp.capabilities import projects as P
    assert RT.dossier_valide is P.dossier_valide


# ── les outils d'admin d'un agent (`platform_tools`, revue de #1274) ──────────

@pytest.fixture
def agent_d_operateur(monkeypatch):
    """L'agent 3 d'`alexis`, opérateur plateforme, qui a deux `oto_admin_*` dans ses
    outils. Rend ce que `update_trigger` a reçu."""
    monkeypatch.setattr(RT.db, "get_trigger", lambda i, o: {
        "id": i, "sub": "alexis", "tools": ["fr_siret", "oto_admin_org", "oto_admin_signal"]})
    monkeypatch.setattr(RT.tool_alias, "canonical_names", lambda noms, sub: list(noms))
    monkeypatch.setattr(OP.access, "is_platform_operator", lambda sub: sub == "alexis")
    vu: dict = {}
    monkeypatch.setattr(RT.db, "update_trigger",
                        lambda i, o, champs, **k: vu.update(champs) or {"id": i, **champs})
    return vu


def test_platform_tools_se_pose_par_le_proprietaire_operateur(agent_d_operateur):
    _appel(_ctx(), op="update", trigger_id=3,
           platform_tools=["oto_admin_org", " oto_admin_org", "oto_admin_signal"])
    assert agent_d_operateur == {"outils_plateforme": ["oto_admin_org", "oto_admin_signal"]}


def test_platform_tools_vide_se_retire_sans_etre_operateur(agent_d_operateur, monkeypatch):
    monkeypatch.setattr(OP.access, "is_platform_operator", lambda sub: False)
    _appel(_ctx(), op="update", trigger_id=3, platform_tools=[])
    assert agent_d_operateur == {"outils_plateforme": []}


@pytest.mark.parametrize("kw, code", [
    ({"platform_tools": ["fr_siret"]}, "platform_tools_not_admin"),
    ({"platform_tools": ["oto_admin_user"]}, "platform_tools_not_in_tools"),
    ({"platform_tools": [f"oto_admin_{i}" for i in range(RT.PLATFORM_TOOLS_MAX + 1)]},
     "platform_tools_too_many"),
])
def test_platform_tools_est_une_liste_bornee(agent_d_operateur, kw, code):
    """Des `oto_admin_*` NOMMÉS, déjà dans les outils de l'agent — jamais tout l'admin."""
    with pytest.raises(AuthzDenied) as e:
        _appel(_ctx(), op="update", trigger_id=3, **kw)
    assert e.value.code == code
    assert agent_d_operateur == {}, "rien n'est écrit"


def test_platform_tools_suit_les_outils_poses_dans_le_meme_appel(agent_d_operateur):
    _appel(_ctx(), op="update", trigger_id=3, tools=["oto_admin_user"],
           platform_tools=["oto_admin_user"])
    assert agent_d_operateur["outils_plateforme"] == ["oto_admin_user"]


def test_platform_tools_refuse_a_un_membre(agent_d_operateur, monkeypatch):
    """Un membre n'a aucun rôle plateforme à prêter ; posée d'avance, la liste
    s'armerait le jour où il le devient."""
    monkeypatch.setattr(OP.access, "is_platform_operator", lambda sub: False)
    with pytest.raises(AuthzDenied) as e:
        _appel(_ctx(), op="update", trigger_id=3, platform_tools=["oto_admin_org"])
    assert (e.value.status, e.value.code) == (403, "platform_tools_operator_only")


def test_platform_tools_refuse_a_un_autre_que_le_proprietaire(agent_d_operateur):
    """C'est le rôle du PROPRIÉTAIRE qu'elle prête : un éditeur ne la pose pas."""
    with pytest.raises(AuthzDenied) as e:
        _appel(_ctx(sub="editeur"), op="update", trigger_id=3,
               platform_tools=["oto_admin_org"])
    assert (e.value.status, e.value.code) == (403, "platform_tools_owner_only")
    assert agent_d_operateur == {}


def test_platform_tools_refuse_sous_un_jeton_de_delegation(agent_d_operateur):
    """Un agent qui a `oto_trigger` dans sa liste n'élargit pas la sienne : une
    injection de prompt suffirait."""
    from oto_mcp import verrou_org
    t = verrou_org.poser(verrou_org.Verrou(sub="alexis", org_id=2, job_id=9))
    try:
        with pytest.raises(AuthzDenied) as e:
            _appel(_ctx(), op="update", trigger_id=3, platform_tools=["oto_admin_org"])
    finally:
        verrou_org.lever(t)
    assert (e.value.status, e.value.code) == (403, "platform_tools_human_only")
    assert agent_d_operateur == {}


def test_platform_tools_ne_se_pose_pas_a_la_creation():
    with pytest.raises(AuthzDenied) as e:
        _appel(_ctx(), op="create", procedure="veille", cron="0 8 * * *",
               platform_tools=["oto_admin_org"])
    assert e.value.code == "platform_tools_update_only"
