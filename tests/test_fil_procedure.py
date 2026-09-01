"""Un voisin du fil mène là où le rail le mène — la dernière surface où il ne menait nulle part.

#417 puis #619 ont posé la référence de procédure sur le rail et sur la fiche. Le fil
d'Ariane est resté en arrière : il savait dire `type: "agent"` (le rôle était lu) mais
pas vers quoi, et l'écran rendait donc un voisin agent **non cliquable alors que les
deux autres surfaces l'ouvraient** (#650 point 4, mesuré sur le servi le 2026-09-01 :
`TrailSibling` avait trois champs, `TrailCrumb` quatre).

⚠️ **Ce n'était pas seulement une projection manquante : la donnée n'était pas LUE.**
La requête de fratrie ne ramenait ni `legacy`, ni `legacy_id`, ni `slug`. D'où le
deuxième test, qui est le vrai garde-fou de ce lot : déclarer le champ sans élargir le
SELECT donnerait un contrat juste et un `null` permanent — la panne la plus
désagréable à diagnostiquer, parce que tout a l'air correct des deux côtés.

Éprouvé rouge le 2026-09-01 : les trois clés retirées du SELECT ⟹ deuxième test ;
`procedure` retiré de `TrailSibling` ⟹ premier.
"""
from __future__ import annotations

import inspect

from oto_mcp.capabilities import node_view
from oto_mcp.capabilities.node_procedure_ref import procedure_ref_of
from oto_mcp.capabilities.node_view import TrailCrumb, TrailSibling
from oto_mcp.db import node_view as db_node

# Une ligne de fratrie telle que la requête la rend : les clés d'origine y sont des
# COLONNES (`props->>`), pas un sous-dict — c'est la forme que `procedure_ref_of`
# accepte aussi bien que les `props` d'une fiche.
VOISIN_AGENT = {"parent_id": 7, "public_id": "nod_a", "kind": "page",
                "title": "Prospection", "role": "procedure",
                "legacy": "prc", "legacy_id": "4", "slug": "prospection"}
VOISIN_PAGE = {"parent_id": 7, "public_id": "nod_b", "kind": "page",
               "title": "Une note", "role": None,
               "legacy": "doc", "legacy_id": "1167", "slug": None}


def test_les_deux_modeles_du_fil_portent_la_reference():
    for modele in (TrailSibling, TrailCrumb):
        assert "procedure" in modele.model_fields, modele.__name__


def test_la_requete_de_fratrie_LIT_les_trois_cles():
    """Le garde-fou qui compte. Un champ déclaré et une requête inchangée donnent un
    `null` permanent : le contrat est juste, la donnée n'arrive jamais, et rien ne
    signale l'écart."""
    sql = inspect.getsource(db_node.siblings_of)
    for cle in ("'legacy'", "'legacy_id'", "'slug'"):
        assert cle in sql, (
            f"la fratrie ne lit pas {cle} : `procedure` resterait null pour toujours")


def test_un_voisin_agent_porte_sa_reference_et_une_page_non():
    """Jamais devinée : une page voisine a bien une clé d'origine (`doc`), mais ce
    n'est pas une procédure — la confondre ferait pointer le fil vers un guide qui
    n'existe pas."""
    ref = procedure_ref_of("agent", "org", VOISIN_AGENT)
    assert ref is not None and ref.id == 4 and ref.slug == "prospection"
    assert ref.scope == "org"
    assert procedure_ref_of("page", "org", VOISIN_PAGE) is None


def test_le_fil_passe_par_la_MEME_derivation_que_le_rail(monkeypatch):
    """Le fil, le rail et la fiche lisent la même fonction. Une quatrième dérivation
    recopiée ici divergerait sans symptôme visible — elle rendrait une référence, juste
    pas la bonne."""
    monkeypatch.setattr(db_node, "siblings_of",
                        lambda parents, owner, cap: {None: [VOISIN_AGENT, VOISIN_PAGE]})

    fil = node_view._fil(
        {"owner_type": "org", "owner_id": 2},
        [{"public_id": "nod_racine", "parent_id": None, "kind": "page",
          "props": {"title": "Racine"}}])

    voisins = {s.id: s for s in fil[0].siblings}
    assert voisins["nod_a"].procedure is not None
    assert voisins["nod_a"].procedure.slug == "prospection"
    assert voisins["nod_b"].procedure is None


def test_le_scope_dune_reference_est_le_proprietaire_du_noeud(monkeypatch):
    """Le `scope` sert à ouvrir le guide au bon palier. La fratrie est bornée au
    propriétaire de la fiche par la requête elle-même : c'est donc celui-là qui vaut,
    et le relire par nœud coûterait une colonne pour la même valeur."""
    monkeypatch.setattr(db_node, "siblings_of",
                        lambda parents, owner, cap: {None: [VOISIN_AGENT]})

    fil = node_view._fil(
        {"owner_type": "group", "owner_id": 3},
        [{"public_id": "nod_racine", "parent_id": None, "kind": "page", "props": {}}])

    assert fil[0].siblings[0].procedure.scope == "group"
