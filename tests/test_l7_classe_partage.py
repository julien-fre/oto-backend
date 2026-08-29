"""Une clé plateforme FERMÉE que rien n'exprime a un nom — les 17 `inconnu` du 29/08.

**Ce qui s'est passé.** La première nuit de fenêtre en prod a rendu 19 890 observations,
dont **17 `inconnu`** — donc `porte_ouverte: false`, donc le retrait bloqué. Or les 17
étaient parfaitement explicables : deux clés plateforme (`aiark` et `apify`) **fermées
sur une allowlist** qui contient l'org de l'appelant, et **aucune arête** pour le dire.
L'ancien chemin accordait, la chaîne se taisait.

**Ce n'était donc pas une divergence du modèle, mais un trou du CLASSEUR.** 0053 sait
parfaitement exprimer ça : une clé fermée accordée à l'org X, c'est une arête
`platform → org:X`. Il manquait les arêtes — le semis de L5 ne couvrait que
`CHAIN_CONNECTORS`, et ni `aiark` ni `apify` n'en font partie. Le classeur, lui, ne
reconnaissait que la variante OUVERTE (`free_tier_hors_modele`) et rangeait l'autre en
`inconnu` : la classe qui doit rester à zéro pour autoriser le retrait fermait la porte
pour une raison fausse.

⚠️ **Les 17 ne sont PAS requalifiés.** Le compteur est un journal : on ne réécrit pas
l'histoire. Ils restent `inconnu` sur leur jour, et c'est la fenêtre SUIVANTE qui juge.

Les formes jouées ici sont celles relevées sur la base servie le 2026-08-29.
"""
from __future__ import annotations

import pytest

from oto_mcp import credentials_store, grants_chain, group_store, org_store
from oto_mcp.access import cascade, chain_shadow
from oto_mcp.db import grants as db_grants

# Les deux clés, telles qu'elles sont en prod : FERMÉES, une allowlist d'orgs qui
# contient l'org 196, et zéro arête.
AIARK = [{"label": "tulina", "share_mode": "closed",
          "share_down": ["org:262", "org:269", "org:196", "org:178", "org:192",
                         "org:264", "org:270", "org:273", "org:255"],
          "share_side": [], "meta": {}}]
APIFY = [{"label": "tulina", "share_mode": "closed",
          "share_down": ["org:196", "org:264", "org:270", "org:269", "org:255"],
          "share_side": [], "meta": {}}]
# La clé serper : OUVERTE à tous. Même symptôme vu du classeur, autre remède.
SERPER = [{"label": "env", "share_mode": "open", "share_down": [], "share_side": [],
           "meta": {"rate_limit": 200}}]


@pytest.fixture
def sans_arete(monkeypatch):
    """Aucune clé BYO, aucune arête — l'état exact des deux connecteurs en prod."""
    monkeypatch.setattr(credentials_store, "has_credential",
                        lambda et, eid, p, account=None: False)
    monkeypatch.setattr(credentials_store, "instance_suspended",
                        lambda et, eid, p, account="": False)
    monkeypatch.setattr(group_store, "list_groups_for_user", lambda s, o=None: [])
    monkeypatch.setattr(group_store, "has_group_secret", lambda g, p: False)
    monkeypatch.setattr(org_store, "has_org_secret", lambda o, p: False)
    monkeypatch.setattr(cascade, "current_group", lambda sub: None, raising=False)
    monkeypatch.setattr(db_grants, "edges_for", lambda ref, grantees: [])
    yield


def _instances(monkeypatch, par_connecteur: dict):
    monkeypatch.setattr(credentials_store, "list_platform_instances",
                        lambda p: [dict(i) for i in par_connecteur.get(p, [])])


# Le barreau que l'ancien chemin a rendu, tel que l'échantillon de prod le montre :
# `ancien: platform/platform`.
def _legacy(label):
    return cascade.CascadeRung("platform", credentials_store.PLATFORM, label, {})


@pytest.mark.parametrize("connecteur,instances", [("aiark", AIARK), ("apify", APIFY)])
def test_les_deux_cas_de_prod_sont_NOMMES_et_plus_inconnus(sans_arete, monkeypatch,
                                                           connecteur, instances):
    """Le rejeu exact. Avant : `inconnu`, donc porte fermée. Après : une classe qui
    dit ce qui manque — les arêtes nominatives de l'allowlist."""
    _instances(monkeypatch, {connecteur: instances})
    pick, hors_modele = chain_shadow._platform_pick("d57fbbb3", connecteur, 196)
    assert pick is None, "aucune arête ⟹ la chaîne se tait"
    assert hors_modele == chain_shadow.PARTAGE_HORS_MODELE

    classe = chain_shadow.classify(_legacy("tulina"), None, acl_refus=False,
                                   hors_modele=hors_modele)
    assert classe == chain_shadow.PARTAGE_HORS_MODELE
    assert classe != chain_shadow.INCONNU, (
        "une divergence explicable ne doit jamais fermer la porte du retrait")


def test_la_cle_OUVERTE_garde_sa_propre_classe(sans_arete, monkeypatch):
    """Les deux nuances ne se confondent pas : elles n'ont pas le même remède —
    l'arête « tout le monde » d'un côté, les nominatives de l'autre."""
    _instances(monkeypatch, {"serper": SERPER})
    _, hors_modele = chain_shadow._platform_pick("u", "serper", 196)
    assert hors_modele == chain_shadow.FREE_TIER_HORS_MODELE


def test_une_cle_FERMEE_avec_son_arete_ne_diverge_plus(sans_arete, monkeypatch):
    """Le remède, vérifié : dès que l'arête existe, la chaîne accorde et l'accord
    revient. C'est ce que la commande de semis produira sur les deux connecteurs."""
    _instances(monkeypatch, {"aiark": AIARK})
    monkeypatch.setattr(db_grants, "edges_for", lambda ref, grantees: [
        {"id": 1, "resource_id": ref, "grantee_kind": "org", "grantee_id": "196",
         "constraints": {}, "parent_id": None, "source": "manual",
         "created_by": None, "created_at": None, "revoked_at": None}]
        if ("org", "196") in set(grantees) else [])
    pick, hors_modele = chain_shadow._platform_pick("d57fbbb3", "aiark", 196)
    assert pick is not None and pick.entity_id == "tulina"
    assert hors_modele is None
    assert chain_shadow.classify(_legacy("tulina"), pick, acl_refus=False,
                                 hors_modele=hors_modele) == chain_shadow.ACCORD


def test_inconnu_reste_ATTEIGNABLE(sans_arete):
    """La porte doit toujours pouvoir se fermer : élargir la reconnaissance ne doit
    pas rendre `inconnu` inatteignable, sinon le garde-fou ne garde plus rien."""
    autre = chain_shadow.ChainPick("user", credentials_store.MEMBER, "196:u")
    assert chain_shadow.classify(cascade.CascadeRung("org", "org", "196", "K"), autre,
                                 acl_refus=False,
                                 hors_modele=None) == chain_shadow.INCONNU


def test_la_nuance_ne_se_lit_QUE_si_la_chaine_se_tait(sans_arete, monkeypatch):
    """Garde-fou de portée : un palier gagnant ne porte aucune nuance, sans quoi une
    résolution parfaitement d'accord pourrait se voir rangée dans un trou."""
    _instances(monkeypatch, {"aiark": AIARK})
    monkeypatch.setattr(org_store, "has_org_secret", lambda o, p: True)
    monkeypatch.setattr(credentials_store, "list_platform_instances",
                        lambda p: [dict(i) for i in AIARK])
    pick, hors_modele = chain_shadow.chain_verdict("u", "aiark", org=196)
    assert pick is not None and pick.mode == "org" and hors_modele is None
