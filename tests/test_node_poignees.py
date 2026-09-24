"""Deux poignées déclarées au contrat plutôt que devinées par le client.

Le nœud servi portait déjà de quoi répondre, mais pas de quoi le DIRE :

- **l'épingle** est posée depuis la conversion des projets et n'était lue par personne.
  Comme le modèle a retiré le genre `project` exprès (0054-D5 : le genre dit ce que
  l'objet EST, pas ce qu'il joue), l'épingle est **la seule chose** qui distingue une
  racine d'une page ordinaire — et elle n'était pas servie ;
- **l'adresse du tableau** (`datastore`) a valu le NOM du tableau jusqu'au 24/09/2026 :
  la projection pose `title = datastore`, et c'était une coïncidence vouée à
  disparaître. Elle a disparu pour une raison de sécurité (#365) — un nom se résout
  dans la portée de l'appelant, donc chez son homonyme — et c'est désormais
  l'IDENTIFIANT du tableau recopié. La poignée tenue à part de `name` a joué son rôle :
  une seule ligne a changé.

La clé s'appelait `namespace` jusqu'au 10/09/2026. Le renommage du sens « tableau » l'a
trouvée en dernier : elle avait échappé au tri parce qu'on triait sur le nom du module
(`node_view` ⇒ « autre sens ») au lieu de ce que la clé désigne.

Déclarer la poignée maintenant, tant qu'elle est facile à tenir, c'est le même geste
que `doc_id` / `project_id` : le contrat porte l'adresse, le client n'a plus à deviner.
"""
from __future__ import annotations

import inspect

from oto_mcp.capabilities import node_view
from oto_mcp.capabilities.node_view import NodeOut


def test_le_contrat_porte_les_deux_poignees():
    for champ in ("pinned", "datastore"):
        assert champ in NodeOut.model_fields, f"`{champ}` n'est pas au contrat"


def test_lepingle_est_un_booleen_toujours_servi():
    """Jamais `None` : « je ne sais pas » et « ce n'est pas une racine » ne se disent
    pas pareil, et le second est la vérité pour tout nœud."""
    champ = NodeOut.model_fields["pinned"]
    assert champ.default is False
    assert champ.annotation is bool


def test_l_adresse_nest_servie_QUE_pour_un_tableau():
    """Sur une page, `null` — pas une chaîne vide : une adresse absente doit se lire
    comme absente, pas comme une adresse qui ne mène nulle part."""
    src = inspect.getsource(node_view._compose)
    assert 'if nature == "table" else None' in src, (
        "l'adresse du tableau est servie hors d'un tableau : le client croirait pouvoir "
        "la repasser aux surfaces de données")


def test_l_adresse_du_tableau_a_UN_SEUL_point_de_resolution():
    """TRIPWIRE — tout l'objet du lot. Le jour où `title` cesse d'être l'adresse du
    tableau, UNE ligne change et aucun client ne bouge. Si elle se résout à deux
    endroits, la dissolution de la coïncidence en cassera un et pas l'autre."""
    src = inspect.getsource(node_view)
    assert src.count('"datastore":') == 1, (
        "l'adresse du tableau se résout à plusieurs endroits : la poignée n'est plus "
        "tenue en un point, et le jour de la bascule l'un d'eux mentira")
    # La clé morte ne revient pas par une relecture distraite de l'historique.
    assert '"namespace":' not in src, (
        "`namespace` est resservi comme clé : le sens « tableau » a basculé à sec le "
        "10/09/2026, sans doublon — deux noms pour la même chose promettent que les "
        "deux durent")


def test_lepingle_vient_des_props_et_pas_du_genre():
    """0054-D5 : un `kind='project'` réintroduirait l'objet que le modèle retire.
    L'épingle doit donc se lire dans les propriétés, jamais se déduire du genre."""
    src = inspect.getsource(node_view._compose)
    assert 'props.get("pinned")' in src
    for interdit in ('kind == "project"', "kind == 'project'"):
        assert interdit not in src, "l'épingle est déduite du genre — le genre n'en a plus"


def test_l_adresse_est_un_IDENTIFIANT_et_le_contrat_le_dit():
    """#365 : la poignée était un NOM, résolu dans la portée de l'appelant — deux
    homonymes atteignables (« vivier » perso et « vivier » d'org) suffisaient à écrire
    dans l'autre, sans erreur. Elle est désormais l'identifiant du tableau recopié, et
    la description doit le dire : un client qui la lirait comme un nom la recomposerait
    à partir de `name`, et rouvrirait la faille de son côté."""
    description = (NodeOut.model_json_schema()["properties"]["datastore"]
                   .get("description") or "")
    assert "IDENTIFIER" in description and "homonym" in description, (
        "la description ne dit pas que la poignée est un identifiant, ni pourquoi un nom "
        "ne suffit pas")
    src = inspect.getsource(node_view._compose)
    assert "datastore_de(" in src and '"datastore": (props.get("title")' not in src, (
        "l'adresse du tableau est redevenue son titre")
