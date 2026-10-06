"""L'alerte hors bande : prévenir par un canal qui ne meurt pas avec la clé (oto#59).

Le 03/09/2026, une clé a disparu d'une org. Une douzaine de passages programmés ont
tourné à l'aveugle pendant **36 heures**. ⚠️ Le canal qui aurait annoncé la panne
tournait sur le credential tombé : six fois par jour, un run découvrait qu'il était
cassé, l'inscrivait sur une ligne que personne ne regardait, et se taisait —
**correctement, selon ses propres règles**. La panne était silencieuse *par
construction*.

Ces bancs tiennent les quatre propriétés qui font que ce lot répare quelque chose : le
courrier passe par la PLATEFORME, il y en a **un par org**, il ne part pas sans raison,
et il ne part pas du tout tant que l'interrupteur est fermé.
"""
from __future__ import annotations

import pytest

from oto_mcp import maintenance as M


@pytest.fixture()
def bureau(monkeypatch):
    """Une org à prévenir, un admin joignable, et un facteur qui compte ses lettres."""
    envois: list = []
    from oto_mcp.db import alertes_credential as A
    from oto_mcp.db import users as U
    from oto_mcp import email as E
    from oto_mcp import org_store as O

    monkeypatch.setattr(A, "a_notifier", lambda: [
        {"org_id": 7, "ids": [1, 2], "connectors": ["slack"], "agents_max": 3,
         "depuis": None},
    ])
    marques: list = []
    monkeypatch.setattr(A, "marquer_notifie", lambda ids: marques.extend(ids) or len(ids))
    # Aucune clé rouge par défaut : ces bancs-ci tiennent le RETRAIT ; les clés
    # refusées ou à sec ont les leurs plus bas.
    monkeypatch.setattr(A, "clore_episodes_gueris", lambda: 0)
    monkeypatch.setattr(A, "cles_ko_a_annoncer", lambda: [])
    monkeypatch.setattr(O, "list_org_members",
                        lambda org: [{"sub": "u-admin", "org_role": "org_admin"},
                                     {"sub": "u-membre", "org_role": "org_member"}])
    monkeypatch.setattr(U, "emails_by_subs", lambda subs: {"u-admin": "a@exemple.invalid"})
    monkeypatch.setattr(E, "_send", lambda **kw: envois.append(kw) or True)
    return envois, marques


def test_ferme_par_defaut_rien_ne_part(bureau, monkeypatch):
    """⚠️ LE banc du dispositif : le mécanisme tourne, l'effet attend une décision.
    Un canal qu'on ouvre en devinant son volume se referme au bout d'une semaine."""
    envois, marques = bureau
    monkeypatch.delenv(M._ENV_ALERTE, raising=False)
    out = M.alertes_credential()
    assert envois == [] and marques == []
    assert out["actif"] is False
    assert out["orgs_a_prevenir"] == 1, (
        "le travail doit DIRE ce qui partirait — rendre zéro se lirait « rien à "
        "signaler », ce qui est le contraire de la vérité")
    assert out["note"], "le silence doit s'expliquer, pas se deviner"


def test_ouvert_un_seul_courriel_par_ORG(bureau, monkeypatch):
    """Trois clés retirées le même jour font UN message. Trois messages pour un
    incident apprennent au destinataire à les ignorer."""
    envois, marques = bureau
    monkeypatch.setenv(M._ENV_ALERTE, "1")
    out = M.alertes_credential()
    assert len(envois) == 1 and out["envoyes"] == 1
    assert envois[0]["to"] == "a@exemple.invalid"
    assert marques == [1, 2], "les deux lignes groupées sont marquées, pas une seule"


def test_le_courriel_part_par_le_courrier_de_PLATEFORME(bureau, monkeypatch):
    """⚠️ La propriété qui distingue cette alerte du registre qu'elle remplace : le
    canal qui prévient ne doit pas pouvoir mourir avec ce dont il annonce la mort. On
    exige donc l'envoi par le mailer de plateforme, jamais par un connecteur de l'org."""
    envois, _ = bureau
    monkeypatch.setenv(M._ENV_ALERTE, "1")
    M.alertes_credential()
    assert "html" in envois[0] and "subject" in envois[0], (
        "l'envoi doit passer par `email._send` (mailer de plateforme) — un connecteur "
        "d'org n'a pas cette signature, et il serait mort avec la clé")


def test_rien_a_prevenir_rien_ne_part(bureau, monkeypatch):
    """Un avertissement qu'on reçoit toujours cesse d'être lu."""
    envois, _ = bureau
    from oto_mcp.db import alertes_credential as A
    monkeypatch.setattr(A, "a_notifier", lambda: [])
    monkeypatch.setenv(M._ENV_ALERTE, "1")
    out = M.alertes_credential()
    assert envois == [] and out["orgs_a_prevenir"] == 0 and out["envoyes"] == 0


def test_sans_destinataire_la_ligne_reste_A_NOTIFIER(bureau, monkeypatch):
    """⚠️ On ne marque PAS : le jour où l'org gagne un admin, l'alerte partira. La
    perdre ici serait la perdre exactement quand elle devient délivrable."""
    envois, marques = bureau
    from oto_mcp.db import users as U
    monkeypatch.setattr(U, "emails_by_subs", lambda subs: {})
    monkeypatch.setenv(M._ENV_ALERTE, "1")
    out = M.alertes_credential()
    assert envois == [] and marques == []
    assert out["sans_destinataire"] == 1, "et ça se compte, sinon ça ne se voit pas"


def test_dry_run_compte_sans_envoyer(bureau, monkeypatch):
    envois, marques = bureau
    monkeypatch.setenv(M._ENV_ALERTE, "1")
    out = M.alertes_credential(dry_run=True)
    assert envois == [] and marques == [] and out["orgs_a_prevenir"] == 1


def test_le_travail_tourne_dans_le_timer_quotidien():
    """Le mécanisme doit tourner dès le tag — sinon « l'effet attend une décision »
    voudrait dire « rien n'est prêt le jour de la décision »."""
    assert "alertes-credential" in M._TRAVAUX
    assert "alertes-credential" in M._ALL
    assert "alertes-credential" not in M._ACTES, (
        "ce n'est pas un acte d'opérateur : il tourne seul, gardé par son interrupteur")


# --- Clés REFUSÉES ou À SEC sous des agents programmés (signaux oto #1168, #1189) ------


def _rouge(connector="snitcher", org_id=7, verdict=None, raison="403 Forbidden"):
    return {"entity_type": "org", "entity_id": str(org_id), "connector": connector,
            "account": "", "raison": raison, "verdict": verdict, "org_id": org_id}


@pytest.fixture()
def cles_rouges(bureau, monkeypatch):
    """Une clé refusée dans l'org 7, un agent programmé qui en dépend, aucun retrait."""
    envois, _ = bureau
    from oto_mcp.db import alertes_credential as A
    from oto_mcp.db import runner_triggers as T
    lignes = [_rouge()]
    annoncees: list = []
    monkeypatch.setattr(A, "a_notifier", lambda: [])
    monkeypatch.setattr(A, "cles_ko_a_annoncer", lambda: lignes)
    monkeypatch.setattr(A, "marquer_cles_annoncees",
                        lambda ls: annoncees.extend(ls) or len(ls))
    agents = {("7", "snitcher"): [{"label": "veille quotidienne"}]}
    monkeypatch.setattr(T, "triggers_actifs_utilisant",
                        lambda org, c: agents.get((str(org), c), []))
    return envois, lignes, annoncees, agents


def test_une_cle_REFUSEE_sous_un_agent_programme_est_annoncee(cles_rouges, monkeypatch):
    """33 passages d'une même org ont buté sur une clé morte sans que personne soit
    prévenu (#1168). La marque rouge existait ; personne ne la portait hors bande."""
    envois, _, annoncees, _ = cles_rouges
    monkeypatch.setenv(M._ENV_ALERTE, "1")
    out = M.alertes_credential()
    assert out["envoyes"] == 1 and out["cles_en_panne"] == 1
    assert "snitcher" in envois[0]["html"] and "refusée" in envois[0]["html"]
    assert "1 agent(s)" in envois[0]["html"]
    assert [l["connector"] for l in annoncees] == ["snitcher"], (
        "la clé est marquée annoncée APRÈS l'envoi : c'est ce qui fait UNE alerte par "
        "épisode, pas une par passage")


def test_une_cle_A_SEC_dit_recharger_pas_reposer(cles_rouges, monkeypatch):
    envois, lignes, _, _ = cles_rouges
    from oto_mcp.credentials_store import NO_QUOTA_VERDICT
    lignes[0].update(verdict=NO_QUOTA_VERDICT, raison="Not enough credits")
    monkeypatch.setenv(M._ENV_ALERTE, "1")
    M.alertes_credential()
    assert "n'a plus de crédits" in envois[0]["html"]


def test_une_cle_rouge_que_RIEN_ne_fait_tourner_ne_part_pas(cles_rouges, monkeypatch):
    """Sans agent programmé, pas d'urgence hors bande : la carte rouge suffit. Et
    l'épisode n'est PAS marqué : le jour où un agent en dépend, l'alerte partira."""
    envois, _, annoncees, agents = cles_rouges
    agents.clear()
    monkeypatch.setenv(M._ENV_ALERTE, "1")
    out = M.alertes_credential()
    assert envois == [] and annoncees == [] and out["orgs_a_prevenir"] == 0


def test_retrait_et_cle_rouge_dans_la_meme_org_font_UN_courriel(cles_rouges, monkeypatch):
    envois, _, annoncees, _ = cles_rouges
    from oto_mcp.db import alertes_credential as A
    marques: list = []
    monkeypatch.setattr(A, "a_notifier", lambda: [
        {"org_id": 7, "ids": [4], "connectors": ["slack"], "agents_max": 1,
         "depuis": None}])
    monkeypatch.setattr(A, "marquer_notifie", lambda ids: marques.extend(ids) or len(ids))
    monkeypatch.setenv(M._ENV_ALERTE, "1")
    out = M.alertes_credential()
    assert len(envois) == 1 and out["orgs_a_prevenir"] == 1
    assert "retirée" in envois[0]["html"] and "snitcher" in envois[0]["html"]
    assert marques == [4] and len(annoncees) == 1


def test_ferme_par_defaut_la_cle_rouge_se_COMPTE_sans_partir(cles_rouges, monkeypatch):
    envois, _, annoncees, _ = cles_rouges
    monkeypatch.delenv(M._ENV_ALERTE, raising=False)
    out = M.alertes_credential()
    assert envois == [] and annoncees == []
    assert out["cles_en_panne"] == 1 and out["orgs_a_prevenir"] == 1 and out["note"]
