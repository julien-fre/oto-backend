"""Les anciens noms sont SERVIS pendant la migration — sur toutes les lectures.

C'est la moitié qui rend la bascule vivable : la colonne-tableau est la vérité, et les
écrans qui parlent encore `contact1_nom` continuent de répondre. Servie, jamais
stockée — deux vérités à réconcilier sinon, ce qui est l'écueil que ce double-service
existe pour éviter.

Le contrat que ces tests figent tient en trois points : les rangs suivent l'ORDRE de la
liste (un écran affiche « le premier contact » comme cible d'appel — un ordre instable
ferait appeler quelqu'un d'autre entre deux ouvertures de la même fiche), les couches
composent, et la lecture ne coûte pas une requête par ligne.
"""
from __future__ import annotations

import pytest

from oto_mcp.datastore.core import DatastorePg

_SCHEMA = {"fields": [
    {"key": "siren", "type": "text"},
    {"key": "contacts", "type": "list", "flat_alias": "contact{n}_{attr}",
     "of": {"type": "object", "fields": [
         {"key": "nom", "type": "text"}, {"key": "email", "type": "email"}]}}]}

_DATA = {"siren": "552081317", "contacts": [
    {"nom": "Dupont", "email": {"valeur": "d@x.fr", "origine": "socle client"}},
    {"nom": "Martin"},
]}


def _lu(data: dict, schema=_SCHEMA) -> dict:
    return DatastorePg._row_to_dict(
        {"row_id": "r1", "created_at": "t", "updated_at": "t", "data": data}, schema)


def test_the_old_names_are_served():
    out = _lu(_DATA)
    assert out["contact1_nom"] == "Dupont"
    assert out["contact1_email"] == "d@x.fr"
    assert out["contact2_nom"] == "Martin"


def test_the_layers_compose_through_the_alias():
    """Sans ça, les marques de provenance disparaîtraient des écrans pendant toute la
    fenêtre de migration, sans message."""
    assert _lu(_DATA)["contact1_email.origine"] == "socle client"


def test_the_ranks_follow_the_list_order():
    """L'ordre est un CONTRAT, pas une propriété du moteur : le rang 1 est le premier
    item écrit, et il le reste d'une lecture à l'autre."""
    inverse = {"contacts": [{"nom": "Martin"}, {"nom": "Dupont"}]}
    assert _lu(inverse)["contact1_nom"] == "Martin"
    assert _lu(inverse)["contact2_nom"] == "Dupont"


def test_the_truth_is_still_served_as_a_list():
    """Le double-service SERT les deux : la forme neuve ne disparaît pas derrière
    l'ancienne, sinon un consommateur déjà migré perdrait sa lecture."""
    out = _lu(_DATA)
    assert out["contacts"][0]["nom"] == "Dupont"
    assert out["contacts"][0]["email"] == "d@x.fr"


def test_a_missing_attribute_yields_no_column():
    """Le contact 2 n'a pas d'e-mail : on ne fabrique pas une clé vide. Une colonne
    absente et une colonne vide ne disent pas la même chose."""
    assert "contact2_email" not in _lu(_DATA)


def test_nothing_is_projected_without_a_declaration():
    """Aucun repli sur une convention supposée — la contrainte du barreau 1."""
    sans = {"fields": [{"key": "contacts", "type": "list",
                        "of": {"type": "object", "fields": []}}]}
    assert "contact1_nom" not in _lu(_DATA, sans)
    assert "contact1_nom" not in _lu(_DATA, None)


def test_a_column_that_is_not_a_list_is_left_alone():
    """Robustesse : un schéma déclare un alias, la donnée n'est pas (encore) une
    liste — pendant une conversion, c'est l'état NORMAL d'une partie des lignes."""
    assert _lu({"contacts": "pas encore converti"})["contacts"] == "pas encore converti"


# --- ce que ça coûte ---------------------------------------------------------------

def test_a_page_reads_the_schema_once(monkeypatch):
    """Le schéma se lit UNE fois par page. Dans la compréhension, il partait en une
    requête PAR LIGNE — invisible en test, 50 allers-retours sur une vraie page.
    C'est le genre de coût qu'aucune assertion fonctionnelle n'attrape."""
    from oto_mcp.datastore import core as ds
    s = DatastorePg("u-1")
    monkeypatch.setattr(s, "_resolve", lambda ns, write=False: 1)
    appels = []
    monkeypatch.setattr(s, "_ns_of",
                        lambda ns_id: appels.append(1) or {"schema": _SCHEMA})
    monkeypatch.setattr(ds.db, "datastore_list_rows", lambda *a, **k: [
        {"row_id": f"r{i}", "created_at": "t", "updated_at": "t", "data": _DATA}
        for i in range(20)])
    monkeypatch.setattr(ds.db, "datastore_count_rows", lambda *a, **k: 20)

    page = s.page_rows("t")
    assert len(page["rows"]) == 20
    assert page["rows"][0]["contact1_nom"] == "Dupont"
    assert len(appels) <= 1, f"{len(appels)} lectures de schéma pour une page"
