"""La création d'un tableau ne dit plus que son propriétaire « ne se change pas » (oto#154).

La description servie de `POST /api/datastores` affirmait que seul `owner` fixe le
propriétaire, « et il ne se change pas après coup ». Faux : le transfert existe
(`oto_resource op=transfer`, `POST /api/resources`), et c'est la sortie du scénario de
l'issue — un tableau né personnel, partagé à un collègue qui ne le trouve pas. La phrase
fermait la seule porte qui marche. L'avertissement de création et `docs/rest-api.md`
avaient été corrigés le 08/09 ; cette description-ci était restée.

Lu sur le document OpenAPI SERVI (`openapi.build`), pas sur la déclaration : c'est lui
que lisent le tableau de bord, oto-core et les intégrateurs. Et la route nommée doit
exister dans ce même document, avec le transfert d'un tableau parmi ses gestes — sans
quoi la phrase corrigée renverrait vers une porte absente.
"""
from __future__ import annotations


def _doc() -> dict:
    from oto_mcp.openapi import build
    return build()


def _norme(texte: str) -> str:
    return " ".join((texte or "").split())


def test_la_creation_ne_dit_plus_que_le_proprietaire_est_fige():
    d = _norme(_doc()["paths"]["/api/datastores"]["post"]["description"])
    assert "ne se change pas" not in d, (
        "la phrase fausse est revenue : le propriétaire se change par transfert")


def test_la_creation_NOMME_le_transfert_et_sa_route():
    doc = _doc()
    d = _norme(doc["paths"]["/api/datastores"]["post"]["description"])
    assert "oto_resource op=transfer" in d, "le geste de sortie doit être nommé"
    assert "POST /api/resources" in d, "et sa route REST, pour qui n'est pas un agent"
    assert "post" in doc["paths"]["/api/resources"], (
        "la description renvoie vers une route que le document ne sert plus")


def test_le_transfert_vise_bien_un_tableau():
    """La route nommée transfère un TABLEAU : `transfer` est un de ses gestes et
    `datastore_namespace` un type qu'elle sert."""
    from oto_mcp.capabilities import resources

    assert "transfer" in resources.ResourceInput.model_fields["op"].annotation.__args__
    assert "datastore_namespace" in resources._OPS
