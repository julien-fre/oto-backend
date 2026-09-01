"""Écrire dans le NOUVEL univers — et n'y écrire que ce qui lui appartient.

Arbitrage d'Alexis (31/08/2026) : on ne migre pas, on arrête la recopie, la surface
nœud vit **à côté** de l'ancienne et part de vide. Ce qui s'écrit ici est donc NATIF —
aucune source dans l'ancien monde, rien qui le rafraîchisse, et les anciennes surfaces
ne le voient pas.

Trois invariants que ces tests tiennent, chacun payé ailleurs :

1. **Aucun nœud natif ne porte `delivery`** — la recherche discrimine une couche de
   contexte par cette propriété, PAS par le genre. En poser une sur une page ordinaire
   la ferait remonter dans le périmètre injecté au handshake.
2. **Trois genres, jamais un genre pour un rôle** (ADR 0054-D5). Une page reste une
   page, même quand elle joue autre chose.
3. **Un nœud COPIÉ de l'ancien monde ne s'écrit pas ici** — sa source y est la vérité,
   et l'écrire des deux côtés ferait diverger les deux.
"""
from __future__ import annotations

import inspect

import pytest

from oto_mcp.capabilities import node_edit
from oto_mcp.db import nodes as db_nodes


# --- ce que la couche d'écriture refuse d'introduire ---------------------------

def test_une_page_native_ne_porte_JAMAIS_delivery():
    """L'invariant qui protège le périmètre des couches de contexte : `delivery` est
    ce qui distingue un guide, et il n'appartient qu'à eux."""
    src = inspect.getsource(db_nodes.create_page)
    assert "delivery" not in src, (
        "la création pose `delivery` : la page remonterait comme une couche de "
        "contexte dans la recherche, donc dans ce qu'on injecte au handshake")


def test_la_creation_nintroduit_aucun_genre_neuf():
    """ADR 0054-D5 : le genre dit ce que l'objet EST, le rôle est porté en propriété.
    Un `kind='projet'` ou `kind='procédure'` réintroduirait l'objet que le modèle
    retire."""
    src = inspect.getsource(db_nodes.create_page)
    assert "_KIND" in src, "la création doit poser le genre canonique, pas un littéral"
    assert db_nodes._KIND == "page"
    for role in ("project", "projet", "guide", "procedure", "procédure", "agent"):
        assert f"'{role}'" not in src and f'"{role}"' not in src, (
            f"« {role} » apparaît comme genre : c'est un RÔLE, jamais un `kind`")


def test_une_page_native_na_pas_de_source_dans_lancien_monde():
    """C'est ce qui la distingue d'une copie : rien ne la rafraîchit, et la purge des
    copies (qui cible `props.legacy`) ne doit jamais l'emporter."""
    src = inspect.getsource(db_nodes.create_page)
    assert "legacy" not in src, (
        "la création pose une clé d'origine : la page serait prise pour une copie, "
        "donc écrasée par une re-projection ou emportée par la purge")


# --- l'identité, et pourquoi elle est tirée ------------------------------------

def test_lidentifiant_est_TIRÉ_jamais_calculé():
    """Une identité dérivée du contenu ou du rang se recalcule — donc elle casse au
    premier renommage ou réordonnancement, et toute référence externe part avec.
    Les nœuds CONVERTIS dérivent la leur (c'est ce qui rend leur conversion
    idempotente) ; un natif n'a pas de clé naturelle."""
    a, b = db_nodes._new_node_id(), db_nodes._new_node_id()
    assert a != b, "deux créations produisent le même identifiant"
    assert a.startswith("nod_") and len(a) > 20
    src = inspect.getsource(db_nodes._new_node_id)
    assert "md5" not in src and "sha" not in src, "l'identité est dérivée du contenu"


def test_le_corps_nest_reecrit_que_sil_change():
    """Les blocs portent des identifiants stables qu'une prose peut citer. Les
    réécrire à chaque édition de titre les ferait tous changer, et les citations
    tomberaient."""
    src = inspect.getsource(db_nodes.update_page)
    assert "if body_md is not None" in src, (
        "le corps est réécrit inconditionnellement : éditer un titre ré-identifierait "
        "tous les paragraphes")


def test_la_suppression_ramasse_la_descendance():
    """L'arbre n'a PAS de clé étrangère (arbitrage M-e, ouvert) : sans ramassage, un
    parent supprimé laisse des enfants rattachés à un identifiant disparu — des
    orphelins qu'aucun lecteur ne trouve et qu'aucune purge ne voit."""
    src = inspect.getsource(db_nodes.delete_page)
    assert "RECURSIVE" in src, "la descendance n'est pas ramassée"


# --- l'écriture ne franchit pas la frontière des deux univers ------------------

def test_un_noeud_COPIÉ_ne_sécrit_pas_ici():
    """LE point de couture entre les deux mondes. Sa source est la vérité dans
    l'ancien ; l'écrire des deux côtés ferait diverger les deux, et une
    re-projection écraserait silencieusement ce qu'on vient d'écrire."""
    from oto_mcp.capabilities._types import AuthzDenied
    fiche = {"id": 1, "public_id": "nod_x", "owner_type": "org", "owner_id": "7",
             "props": {"legacy": "doc", "legacy_id": 42}}
    with pytest.raises(AuthzDenied) as err:
        node_edit._mien(object(), fiche)
    assert err.value.status == 409
    assert err.value.code == "node_projete"


def test_un_noeud_NATIF_passe_par_le_palier_des_guides(monkeypatch):
    """L'autorisation n'est pas réécrite : c'est `guides._owner_for_write`, qui porte
    déjà plateforme / org / chef d'équipe / soi. Une seconde version divergerait de la
    première au premier changement — c'est la faute qu'on répare ailleurs."""
    vus: list = []
    from oto_mcp.capabilities import guides
    monkeypatch.setattr(guides, "_owner_for_write",
                        lambda ctx, scope, owner=None: vus.append((scope, owner)) or "7")
    node_edit._mien(object(), {"id": 1, "public_id": "nod_y", "owner_type": "group",
                               "owner_id": "3", "props": {}})
    assert vus == [("group", "3")], "le palier réel du nœud n'est pas celui qu'on teste"


# --- la couture : la capacité est-elle réellement montée ? ---------------------

def test_le_HUB_declare_le_module_dedition():
    """⚠️ LE test de couture, et ma première version ne mordait pas.

    Elle lisait le registre après que CE fichier ait importé `node_edit` — donc elle
    prouvait son propre import, pas celui du démarrage. En retirant la ligne du hub,
    elle restait verte. C'est la troisième fois dans la journée que le même piège se
    referme : un banc qui charge plus que le boot certifie une couverture qui n'existe
    pas.

    On lit donc le HUB, qui est ce que le serveur importe. Une capacité qu'aucun
    module chargé au boot ne déclare n'existe pas pour le produit, aussi juste soit
    son code."""
    import pathlib as _p
    hub = (_p.Path(__file__).resolve().parent.parent
           / "oto_mcp" / "capabilities" / "__init__.py").read_text(encoding="utf-8")
    assert "import node_edit" in hub, (
        "`node_edit` n'est déclaré par aucun module chargé au démarrage : la surface "
        "d'écriture n'est pas servie, quels que soient ses tests")


def test_la_capacite_declare_ses_deux_faces():
    from oto_mcp.capabilities import registry
    cap = next((c for c in registry.CAPABILITIES if c.key == "me.node.edit"), None)
    assert cap is not None, "la capacité d'écriture n'est pas déclarée au registre"
    assert cap.mcp == "oto_node_edit"
    assert cap.rest is not None and cap.rest.verb == "POST"
    assert cap.authz is not None, "une capacité sans autz est refusée par le moule"


def test_les_quatre_verbes_sont_joignables():
    """Un `op` déclaré mais non routé rend une erreur de validation brute au lieu d'un
    refus métier — la scène qu'on répare justement ailleurs ce soir."""
    declares = set(NodeEditInput_ops())
    assert declares == set(node_edit._OPS), (
        "un verbe est déclaré au schéma sans être routé, ou l'inverse")


def NodeEditInput_ops() -> list:
    champ = node_edit.NodeEditInput.model_fields["op"]
    return list(getattr(champ.annotation, "__args__", ()))
