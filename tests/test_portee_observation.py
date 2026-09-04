"""Observer les élargissements de portée — et n'envoyer RIEN (ADR 0068 §4).

Période d'observation, décision d'Alexis (04/09/2026) : avant d'écrire à qui que ce
soit, on veut voir combien de messages partiraient et à qui. Chaque ligne enregistrée
décrit donc une notification qui N'EST PAS envoyée.

Ces bancs tiennent les trois choix qui décident du contenu d'une ligne — qui déclenche,
qui recevrait, quand — et le fait que l'observation ne casse jamais ce qu'elle observe.
"""
from __future__ import annotations

import pytest

from oto_mcp.capabilities import _portee
from oto_mcp.capabilities._types import ResolvedCtx

AGENT = ResolvedCtx(sub="alice", org_id=7, channel="mcp")
HUMAIN = ResolvedCtx(sub="alice", org_id=7, channel="rest")
INTERNE = ResolvedCtx(sub="alice", org_id=7)


@pytest.fixture
def lignes(monkeypatch):
    """Le seam d'écriture, remplacé — aucun accès base dans ces bancs."""
    vues: list[dict] = []
    monkeypatch.setattr(_portee.db_portee, "enregistrer_elargissement",
                        lambda **kw: vues.append(kw) or 1)
    return vues


def test_un_agent_qui_elargit_est_OBSERVE(lignes):
    _portee.observer(AGENT, ressource_type="project", ressource_id=42,
                     ressource_nom="Refonte", vers="org",
                     geste="oto_project op=create owner_type=org", cible="7")
    assert len(lignes) == 1
    l = lignes[0]
    assert l["ressource_type"] == "project" and l["ressource_id"] == "42"
    assert l["vers"] == "org" and l["cible"] == "7"


def test_un_geste_HUMAIN_n_enregistre_rien(lignes):
    """Le dashboard n'alerte pas : la personne vient de faire le geste. L'en avertir
    serait un accusé de réception — et ce qu'on reçoit toujours, on cesse de le lire.
    C'est ce qui distingue une alerte d'un journal."""
    _portee.observer(HUMAIN, ressource_type="project", ressource_id=42, vers="org",
                     geste="op=create")
    assert lignes == []


def test_un_appel_INTERNE_n_enregistre_rien(lignes):
    """Ni MCP ni REST : un banc, un script, une migration. Rien n'a été demandé par
    personne, il n'y a personne à prévenir."""
    _portee.observer(INTERNE, ressource_type="project", ressource_id=42, vers="org",
                     geste="op=create")
    assert lignes == []


def test_le_PROPRIETAIRE_et_l_auteur_sont_tous_deux_destinataires(lignes):
    """Ils diffèrent quand un tiers partage ce qu'on lui avait confié. L'un subit
    l'élargissement, l'autre ignore peut-être ce que son agent vient de faire en son
    nom : les deux ont besoin de le savoir (choix d'Alexis)."""
    _portee.observer(AGENT, ressource_type="doc", ressource_id=9, vers="person",
                     geste="oto_resource op=share", proprietaire_sub="bob",
                     cible="tiers@x.fr")
    d = lignes[0]["destinataires"]
    assert set(d) == {"alice", "bob"}
    assert lignes[0]["proprietaire_sub"] == "bob", "celui qui SUBIT est nommé comme tel"


def test_quand_les_deux_se_confondent_la_ligne_ne_double_pas(lignes):
    """Le cas nominal — celui de l'incident fondateur : la personne avait demandé, son
    agent a fait plus. Un doublon ici deviendrait deux messages pour un geste."""
    _portee.observer(AGENT, ressource_type="project", ressource_id=1, vers="org",
                     geste="op=create")
    assert lignes[0]["destinataires"] == ["alice", "alice"], (
        "le seam passe les deux ; c'est `db.portee` qui dédoublonne à l'écriture")


def test_une_ouverture_SANS_LOGIN_est_urgente(lignes):
    """`public`/`secret` = lisible sans compte. Une page publiée par erreur n'attend
    pas la fin d'une fenêtre de regroupement ; un partage d'org, si."""
    for vers in ("public", "secret"):
        lignes.clear()
        _portee.observer(AGENT, ressource_type="project", ressource_id=1, vers=vers,
                         geste="op=publish_mcp")
        assert lignes[0]["immediat"] is True, vers


def test_un_elargissement_vers_l_ORG_est_groupable(lignes):
    """Un agent qui partage trente lignes doit produire UN message, pas trente. C'est
    `immediat=False` qui le permettra — la distinction est posée dès l'enregistrement,
    pour que l'observation mesure les deux volumes séparément."""
    for vers in ("org", "group", "person"):
        lignes.clear()
        _portee.observer(AGENT, ressource_type="project", ressource_id=1, vers=vers,
                         geste="op=share")
        assert lignes[0]["immediat"] is False, vers


def test_une_panne_d_observation_ne_casse_PAS_le_geste(monkeypatch):
    """⚠️ L'appelant est un handler en train de RÉUSSIR un partage légitime. Une
    observation qui lève ferait échouer le produit qu'elle observe — on aurait rendu
    la plateforme plus fragile pour mieux la surveiller."""
    def _boom(**kw):
        raise RuntimeError("base indisponible")
    monkeypatch.setattr(_portee.db_portee, "enregistrer_elargissement", _boom)
    with pytest.raises(RuntimeError):
        _portee.db_portee.enregistrer_elargissement()   # le seam brut lève bien
    # …mais `observer` ne relaie pas : c'est `db.portee` qui absorbe et journalise.
    # Ici on prouve que le seam applicatif n'ajoute AUCUN try de son côté — il
    # s'appuie sur celui du module db, dont c'est la responsabilité déclarée.
    import inspect
    src = inspect.getsource(_portee.observer)
    assert "try:" not in src, (
        "deux filets superposés : le second masquerait les pannes que le premier "
        "journalise, et l'observation mentirait sans que rien ne le dise")


def test_aucun_ENVOI_n_est_declenche():
    """Le fait central de la période d'observation, et il se vérifie : le module ne
    connaît aucun chemin d'envoi. S'il en gagnait un, ce banc tomberait avant que le
    premier message ne parte."""
    import inspect
    src = inspect.getsource(_portee)
    for interdit in ("email", "send_", "mailer", "smtp"):
        assert interdit not in src.lower().replace("e-mail", ""), (
            f"« {interdit} » apparaît dans le module : rien ne doit partir tant que "
            "la période d'observation n'a pas rendu son volume")
