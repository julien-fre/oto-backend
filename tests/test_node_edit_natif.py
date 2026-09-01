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
4. **On n'écrit que ce dont on est PROPRIÉTAIRE**, et ça se compare (2026-09-01). Le
   palier des guides résout une identité, il n'autorise pas : l'appeler et jeter sa
   réponse n'était pas une garde. Le refus est indistinct de l'introuvable.

Le REJEU de bout en bout sur la route servie vit dans
`tests/api/test_node_edit_proprietaire.py` : ici on tient les invariants au grain de
la fonction, là-bas on prouve les codes de retour réellement servis.
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

class _Ctx:
    """Le porteur minimal — `_owner_for_write` ne lit que ça au cran personne."""
    sub = "usr_moi"
    org_id = None
    group_id = None


def _fiche(**kw) -> dict:
    base = {"id": 1, "public_id": "nod_x", "owner_type": "user",
            "owner_id": "usr_moi", "props": {}}
    base.update(kw)
    return base


def test_un_noeud_COPIÉ_ne_sécrit_pas_ici():
    """LE point de couture entre les deux mondes. Sa source est la vérité dans
    l'ancien ; l'écrire des deux côtés ferait diverger les deux, et une
    re-projection écraserait silencieusement ce qu'on vient d'écrire."""
    from oto_mcp.capabilities._types import AuthzDenied
    with pytest.raises(AuthzDenied) as err:
        node_edit._mien(_Ctx(), _fiche(props={"legacy": "doc", "legacy_id": 42}))
    assert err.value.status == 409
    assert err.value.code == "node_projete"


def test_le_palier_est_appelé_avec_le_propriétaire_RÉEL_du_noeud(monkeypatch):
    """L'autorisation n'est pas réécrite : c'est `guides._owner_for_write`, qui porte
    déjà plateforme / org / chef d'équipe / soi. Une seconde version divergerait de la
    première au premier changement — c'est la faute qu'on répare ailleurs."""
    vus: list = []
    from oto_mcp.capabilities import guides
    monkeypatch.setattr(
        guides, "_owner_for_write",
        lambda ctx, scope, owner=None: vus.append((scope, owner)) or owner)
    node_edit._mien(_Ctx(), _fiche(owner_type="group", owner_id="3"))
    assert vus == [("group", "3")], "le palier réel du nœud n'est pas celui qu'on teste"


def test_un_palier_qui_résout_un_AUTRE_propriétaire_est_REFUSÉ(monkeypatch):
    """⚠️ **Le test qui vivait ici ne voyait pas le défaut, et c'est pour ça qu'il a
    vécu.** Il bouchonnait le palier par une lambda qui rendait « 7 » pour un nœud
    possédé par « 3 », puis n'assertait que l'APPEL — donc il restait vert alors que la
    valeur rendue était jetée sans comparaison. Un appel n'est pas un contrôle.

    C'est exactement la faute du code qu'il couvrait : `_owner_for_write` RÉSOUT une
    identité (au cran personne, `return ctx.sub`, sans regarder la cible), il
    n'autorise pas. La garde est la comparaison, et c'est elle qu'on prouve ici.
    """
    from oto_mcp.capabilities._types import AuthzDenied
    from oto_mcp.capabilities import guides
    monkeypatch.setattr(guides, "_owner_for_write", lambda ctx, scope, owner=None: "7")
    with pytest.raises(AuthzDenied) as err:
        node_edit._mien(_Ctx(), _fiche(owner_type="group", owner_id="3"))
    assert (err.value.status, err.value.code) == (404, "not_found")


def test_le_refus_de_propriété_PRÉCÈDE_celui_de_la_copie():
    """L'ordre est une garde à lui seul : un tiers qui sonde un identifiant ne doit
    pas apprendre par un 409 que le nœud existe ET qu'il est une copie. Inversé,
    l'ordre rend l'oracle que le 404 indistinct retire."""
    from oto_mcp.capabilities._types import AuthzDenied
    with pytest.raises(AuthzDenied) as err:
        node_edit._mien(_Ctx(), _fiche(owner_id="usr_quelquun_dautre",
                                       props={"legacy": "doc", "legacy_id": 42}))
    assert (err.value.status, err.value.code) == (404, "not_found")


def test_le_refus_décriture_est_le_MÊME_OBJET_que_celui_de_lintrouvable():
    """Même statut, même code, même message — au caractère près. Deux messages
    différents referaient du CORPS de la réponse l'oracle que le code d'état n'est
    plus."""
    from oto_mcp.capabilities._types import AuthzDenied
    inconnu = node_edit._introuvable()
    with pytest.raises(AuthzDenied) as err:
        node_edit._mien(_Ctx(), _fiche(owner_id="usr_quelquun_dautre"))
    assert (err.value.status, err.value.code, str(err.value)) == \
           (inconnu.status, inconnu.code, str(inconnu))


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
