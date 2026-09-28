"""Un tableau renommé répond encore à son ANCIEN nom — et à rien d'autre (`datastore_aliases`).

Ce qui cite un tableau par son nom ne se réécrit pas toujours : corps de procédure,
guide, prompt planifié, flux externe. Renommer cassait tout cela. Le renommage dépose
maintenant l'ancien nom, que la résolution lit en DERNIER recours.

Contre un vrai PostgreSQL : ce qui se vérifie ici, c'est la PORTÉE (le même prédicat
de visibilité que le nom vivant) et les deux règles qui rendent un alias sûr — un nom
repris par un autre tableau lui appartient, même après la suppression de ce dernier ;
deux anciens porteurs au même rang ne se départagent pas.
"""
from __future__ import annotations

import uuid

from oto_mcp import db
from oto_mcp.capabilities import projects as projets


def _nom() -> str:
    return "t-" + uuid.uuid4().hex[:8]


def _org() -> str:
    return str(90_000 + uuid.uuid4().int % 9_000)


def _vu_par(org: str, nom: str):
    return db.resolve_datastore_ns(nom, sub="membre", org_ids=[int(org)], group_ids=[])


def test_l_ancien_nom_resout_vers_le_tableau_renomme(live):
    org, ancien, nouveau = _org(), _nom(), _nom()
    ns_id = db.create_datastore("org", org, ancien)
    db.rename_datastore_by_id(ns_id, nouveau)

    trouve = _vu_par(org, ancien)
    assert trouve is not None and int(trouve["id"]) == ns_id
    # La ligne rendue dit le nom CANONIQUE, pas l'adresse reçue.
    assert trouve["datastore"] == nouveau
    assert int(_vu_par(org, nouveau)["id"]) == ns_id


def test_un_nom_repris_appartient_a_son_nouveau_porteur_meme_apres_lui(live):
    """Le défaut que la purge ferme : sans elle, supprimer C ramenait « vivier » vers A,
    en silence, et ce qui écrivait dans C écrivait dans A."""
    org, vivier = _org(), _nom()
    a = db.create_datastore("org", org, vivier)
    db.rename_datastore_by_id(a, _nom())

    c = db.create_datastore("org", org, vivier)
    assert int(_vu_par(org, vivier)["id"]) == c          # le nom vivant gagne

    db.delete_datastore_by_id(c)
    assert _vu_par(org, vivier) is None                   # et ne retombe pas sur A


def test_revenir_a_un_ancien_nom_retire_son_propre_alias(live):
    org, x, y = _org(), _nom(), _nom()
    ns_id = db.create_datastore("org", org, x)
    db.rename_datastore_by_id(ns_id, y)
    db.rename_datastore_by_id(ns_id, x)

    assert db.get_datastore_by_alias("org", org, x) is None   # x est vivant, pas un alias
    assert int(db.get_datastore_by_alias("org", org, y)["id"]) == ns_id
    assert int(_vu_par(org, y)["id"]) == ns_id


def test_l_ancien_nom_d_un_tableau_d_une_autre_org_ne_resout_pas(live):
    """Même prédicat que le nom vivant : un alias ne rend jamais un tableau invisible."""
    chez_eux, chez_moi, ancien = _org(), _org(), _nom()
    ns_id = db.create_datastore("org", chez_eux, ancien)
    db.rename_datastore_by_id(ns_id, _nom())

    assert _vu_par(chez_moi, ancien) is None
    resolus, ambigus = db.resolve_datastore_ids_by_name(
        [ancien], sub="membre", org_ids=[int(chez_moi)], group_ids=[])
    assert (resolus, ambigus) == ({}, set())


def test_deux_anciens_porteurs_au_meme_rang_ne_se_departagent_pas(live):
    """#365 pour les anciens noms : choisir le plus récent ou le plus petit identifiant
    enverrait l'appel vers l'un des deux au hasard de l'histoire."""
    org_a, org_b, vivier = _org(), _org(), _nom()
    for org in (org_a, org_b):
        db.rename_datastore_by_id(db.create_datastore("org", org, vivier), _nom())

    portee = dict(sub="membre", org_ids=[int(org_a), int(org_b)], group_ids=[])
    assert db.resolve_datastore_ns(vivier, **portee) is None
    resolus, ambigus = db.resolve_datastore_ids_by_name([vivier], **portee)
    assert vivier not in resolus and vivier in ambigus


def test_le_lot_resout_les_anciens_noms_a_cote_des_vivants(live):
    org, vivant, ancien = _org(), _nom(), _nom()
    v = db.create_datastore("org", org, vivant)
    r = db.create_datastore("org", org, ancien)
    db.rename_datastore_by_id(r, _nom())

    resolus, ambigus = db.resolve_datastore_ids_by_name(
        [vivant, ancien, "inconnu-" + _nom()], sub="membre", org_ids=[int(org)], group_ids=[])
    assert resolus == {vivant: v, ancien: r} and ambigus == set()


def test_des_chiffres_n_empruntent_jamais_un_ancien_nom(live):
    """Des chiffres adressent un tableau par son numéro : un tableau jadis NOMMÉ « 77 »
    ne répond pas à la place du tableau 77."""
    org = _org()
    chiffres = str(8_000_000 + uuid.uuid4().int % 1_000_000)
    ns_id = db.create_datastore("org", org, chiffres)
    db.rename_datastore_by_id(ns_id, _nom())

    assert _vu_par(org, chiffres) is None


def test_un_transfert_prend_le_nom_chez_le_destinataire(live):
    source, dest, vivier = _org(), _org(), _nom()
    ancien_porteur = db.create_datastore("org", dest, vivier)
    db.rename_datastore_by_id(ancien_porteur, _nom())
    arrivant = db.create_datastore("org", source, vivier)

    db.reparent_datastore(arrivant, "org", dest)
    db.delete_datastore_by_id(arrivant)
    assert _vu_par(dest, vivier) is None


def test_un_lien_de_projet_par_ancien_nom_stocke_l_identifiant(live):
    """`oto_project(op=link)` par nom : un agent qui lie avec le nom que cite encore sa
    procédure obtient le lien vers le bon tableau, pas un `unknown_tableau`."""
    org, ancien = _org(), _nom()
    ns_id = db.create_datastore("org", org, ancien)
    db.rename_datastore_by_id(ns_id, _nom())

    projet = {"owner_type": "org", "owner_id": org}
    assert projets._resolve_tableau_id(projet, ancien) == str(ns_id)
    assert projets._resolve_tableau_id(projet, "inconnu-" + _nom()) is None
