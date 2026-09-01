"""Deux poignées déclarées au contrat plutôt que devinées par le client.

Le nœud servi portait déjà de quoi répondre, mais pas de quoi le DIRE :

- **l'épingle** est posée depuis la conversion des projets et n'était lue par personne.
  Comme le modèle a retiré le genre `project` exprès (0054-D5 : le genre dit ce que
  l'objet EST, pas ce qu'il joue), l'épingle est **la seule chose** qui distingue une
  racine d'une page ordinaire — et elle n'était pas servie ;
- **le namespace** d'un tableau vaut aujourd'hui la même chose que son nom, parce que
  la projection pose `title = namespace`. **C'est une coïncidence, et elle est vouée à
  disparaître** : le modèle veut que le namespace devienne une position dans l'arbre.
  Le jour où c'est fait, un client qui lisait `name` comme une adresse casse **sans
  que rien ne le prévienne**.

Déclarer la poignée maintenant, tant qu'elle est facile à tenir, c'est le même geste
que `doc_id` / `project_id` : le contrat porte l'adresse, le client n'a plus à deviner.
"""
from __future__ import annotations

import inspect

from oto_mcp.capabilities import node_view
from oto_mcp.capabilities.node_view import NodeOut


def test_le_contrat_porte_les_deux_poignees():
    for champ in ("pinned", "namespace"):
        assert champ in NodeOut.model_fields, f"`{champ}` n'est pas au contrat"


def test_lepingle_est_un_booleen_toujours_servi():
    """Jamais `None` : « je ne sais pas » et « ce n'est pas une racine » ne se disent
    pas pareil, et le second est la vérité pour tout nœud."""
    champ = NodeOut.model_fields["pinned"]
    assert champ.default is False
    assert champ.annotation is bool


def test_le_namespace_nest_servi_QUE_pour_un_tableau():
    """Sur une page, `null` — pas une chaîne vide : une adresse absente doit se lire
    comme absente, pas comme une adresse qui ne mène nulle part."""
    src = inspect.getsource(node_view._compose)
    assert 'if nature == "table" else None' in src, (
        "le namespace est servi hors d'un tableau : le client croirait pouvoir le "
        "repasser aux surfaces de données")


def test_le_namespace_a_UN_SEUL_point_de_resolution():
    """TRIPWIRE — tout l'objet du lot. Le jour où `title` cesse d'être le namespace,
    UNE ligne change et aucun client ne bouge. S'il se résout à deux endroits, la
    dissolution de la coïncidence en cassera un et pas l'autre."""
    src = inspect.getsource(node_view)
    assert src.count('"namespace":') == 1, (
        "le namespace se résout à plusieurs endroits : la poignée n'est plus tenue "
        "en un point, et le jour de la bascule l'un d'eux mentira")


def test_lepingle_vient_des_props_et_pas_du_genre():
    """0054-D5 : un `kind='project'` réintroduirait l'objet que le modèle retire.
    L'épingle doit donc se lire dans les propriétés, jamais se déduire du genre."""
    src = inspect.getsource(node_view._compose)
    assert 'props.get("pinned")' in src
    for interdit in ('kind == "project"', "kind == 'project'"):
        assert interdit not in src, "l'épingle est déduite du genre — le genre n'en a plus"
