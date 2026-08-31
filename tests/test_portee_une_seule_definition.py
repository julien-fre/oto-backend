"""« Qui a le droit de voir quoi » se décide à UN seul endroit.

La plateforme porte deux mondes de contenu — les nœuds (pages, projets, procédures) et
le datastore (tableaux, lignes). Le socle de propriété commun a été construit par et
pour le second ; le premier l'a réutilisé **en partie**, et la règle de portée s'est
retrouvée écrite deux fois. Elle avait déjà divergé, et les deux écarts étaient
SERVIS EN PRODUCTION (#682) :

1. un nœud possédé par la **plateforme** — les guides de la bibliothèque — était
   **invisible par `oto_node` et lisible par `oto_resource`**. Deux portes, deux
   réponses, pour la même page et la même personne ;
2. un **administrateur d'org** voyait les tableaux de toutes ses équipes, mais pas
   leurs pages. Même rôle, même org, deux réponses selon le monde.

Le remède n'est pas de recopier les branches manquantes : ce serait rouvrir l'écart au
prochain changement, exactement ce que le commentaire du code annonçait avant de le
faire quand même. Il n'y a plus qu'une règle, `ownership.owner_in_scope`, et chaque
monde garde SA résolution de grants — qui, elle, diffère légitimement.
"""
from __future__ import annotations

import ast
import inspect
import textwrap

import pytest

from oto_mcp import ownership


# --- la règle, exercée sur ses quatre voies ------------------------------------

@pytest.fixture
def sans_db(monkeypatch):
    """Les quatre voies se décident sans toucher la base, sauf celle de l'équipe."""
    monkeypatch.setattr(ownership, "active_owner", lambda org_id:
                        None if org_id is None else ("org", str(org_id)))


def test_lorg_active_est_a_portee(sans_db):
    assert ownership.owner_in_scope("u-1", 7, ("org", "7"))
    assert not ownership.owner_in_scope("u-1", 7, ("org", "8")), (
        "une AUTRE org n'est pas à portée — c'est la fuite cross-org que le seam ferme")


def test_ma_ressource_perso_me_suit_dans_tout_contexte(sans_db):
    assert ownership.owner_in_scope("u-1", 7, ("user", "u-1"))
    assert not ownership.owner_in_scope("u-1", 7, ("user", "u-2"))


def test_le_cran_PLATEFORME_est_lisible_partout(sans_db):
    """LA voie qui manquait aux nœuds : les 9 guides de la bibliothèque étaient
    invisibles par une porte et lisibles par l'autre."""
    assert ownership.owner_in_scope("u-1", 7, ("platform", "platform"))
    assert ownership.owner_in_scope("u-1", None, ("platform", "x")), (
        "le cran plateforme ne dépend pas d'une org active")


def test_une_equipe_passe_par_can_read_group(sans_db, monkeypatch):
    """L'autre voie qui manquait : `can_read_group` porte l'escalade d'admin d'org,
    donc un admin lit les nœuds d'une équipe dont il n'est PAS membre."""
    monkeypatch.setattr(ownership.group_store, "get_group",
                        lambda gid: {"id": gid, "org_id": 7})
    monkeypatch.setattr(ownership.roles, "can_read_group", lambda sub, gid: sub == "admin")
    assert ownership.owner_in_scope("admin", 7, ("group", "3"))
    assert not ownership.owner_in_scope("autre", 7, ("group", "3"))


def test_une_equipe_dune_AUTRE_org_reste_hors_portee(sans_db, monkeypatch):
    monkeypatch.setattr(ownership.group_store, "get_group",
                        lambda gid: {"id": gid, "org_id": 99})
    monkeypatch.setattr(ownership.roles, "can_read_group", lambda sub, gid: True)
    assert not ownership.owner_in_scope("admin", 7, ("group", "3")), (
        "lire une équipe ne suffit pas : elle doit appartenir à l'org du CONTEXTE")


def test_un_proprietaire_absent_nest_jamais_a_portee(sans_db):
    assert not ownership.owner_in_scope("u-1", 7, None)


def _code_seul(fonction) -> str:
    """Le CODE d'une fonction, docstring et commentaires retirés.

    ⚠️ Sans ça, ce garde-fou se déclenche sur sa propre explication : la docstring de
    `_lisible` NOMME le piège qu'elle a corrigé (« comparait à `active_org_principals` »),
    et un test qui cherche cette chaîne dans la source entière rougit sur le texte qui
    documente le correctif. Un tripwire qui accuse le commentaire au lieu du code
    finit désactivé — donc faux deux fois.
    """
    arbre = ast.parse(textwrap.dedent(inspect.getsource(fonction)))
    corps = arbre.body[0].body
    if (corps and isinstance(corps[0], ast.Expr)
            and isinstance(corps[0].value, ast.Constant)
            and isinstance(corps[0].value.value, str)):
        corps = corps[1:]                       # la docstring
    return "\n".join(ast.unparse(n) for n in corps)


# --- une seule définition, et le cliquet qui l'y garde -------------------------

def test_les_deux_mondes_appellent_la_MEME_regle():
    """TRIPWIRE — le cœur du lot. Si l'un des deux se remet à comparer un propriétaire
    à une liste de principals, l'écart de #682 revient : `active_org_principals` ne
    connaît ni le cran plateforme ni l'escalade d'équipe."""
    from oto_mcp.capabilities import node_view
    for fonction in (ownership.visible_in_org, node_view._lisible, node_view._compose):
        src = _code_seul(fonction)
        assert "owner_in_scope" in src, (
            f"{fonction.__name__} ne passe plus par la règle commune")

    assert "active_org_principals" not in _code_seul(node_view._lisible), (
        "`_lisible` compare de nouveau à une liste de principals — c'est la seconde "
        "définition de « à portée », et elle diverge sur plateforme et sur l'escalade "
        "d'équipe (les deux écarts servis de #682).")


def test_la_regle_ignore_les_grants_et_le_type_de_ressource():
    """Ce qui la rend PARTAGEABLE : les deux mondes ne rangent pas leurs partages au
    même endroit (le datastore les lit dans `resource_grants`, les nœuds les traduisent
    depuis leurs types d'origine). Une règle qui connaîtrait les grants ne pourrait pas
    servir aux deux — et c'est en la spécialisant qu'on a créé le doublon."""
    src = _code_seul(ownership.owner_in_scope)
    for interdit in ("get_resource_grant", "resource_type", "resolve_grant_nodes"):
        assert interdit not in src, f"la règle de portée touche à « {interdit} »"
