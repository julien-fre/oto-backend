"""Une ligne créée SANS la clé métier déclarée est non rapprochable — le dire (#390).

Le signal a coûté un enrichissement complet : neuf minutes, dix-sept appels d'outils,
trois contacts dont un DRH avec e-mail vérifié, deux actualités sourcées. L'agent a
écrit l'identifiant DANS `row` au lieu du paramètre `id`, et une 501ᵉ ligne SANS SIREN
ni raison sociale est née en portant tout le travail, la ligne réservée restant vide.
Aucune erreur, aucun avertissement — 28 champs repris à la main.

Les deux premières demandes du signal sont servies depuis le 13-15/08 : le bail protège
l'ÉCRITURE et pas seulement l'attribution (`_lease_guard`, `_assert_writable`,
le titulaire étant reconnu par son run), et l'adresse égarée est refusée ou promue (`_id` dans `row` devient
l'adresse de fusion ; un `id` nu non déclaré est refusé en nommant la ligne fantôme).

Reste le troisième cas, celui qui n'a pas d'adresse du tout : une insertion FRANCHE sur
un tableau dont le schéma déclare une clé métier, mais dont la ligne ne la porte pas.
Elle est légitime — on n'empêche rien —, seulement elle ne pourra jamais être
dédupliquée ni rapprochée, et rien ne le disait.

⚠️ Mesuré en production le 28/08 avant de poser l'avertissement : 197 tableaux à clé
métier déclarée, 50 024 lignes, **3** sans clé. Il ne parlera donc quasiment jamais —
c'est ce qui le rend lisible le jour où il parle.
"""
from __future__ import annotations

import pytest

from oto_mcp.datastore import core as dsm


_SCHEMA = {"key": "siren",
           "fields": [{"key": "siren", "type": "text"},
                      {"key": "raison_sociale", "type": "text"}]}


@pytest.fixture()
def store(monkeypatch):
    st = dsm.DatastorePg("u", acting_org=35)
    monkeypatch.setattr(st, "_resolve", lambda ns, write=False: 7)
    monkeypatch.setattr(dsm.db, "get_datastore_by_id",
                        lambda ns_id: {"id": ns_id, "schema": _SCHEMA})
    monkeypatch.setattr(dsm.db, "datastore_find_row_id_by_key",
                        lambda *a, **k: None)
    monkeypatch.setattr(dsm.db, "datastore_insert_row",
                        lambda ns_id, rid, data, *a, **k: {
                            "row_id": rid, "created_at": "t", "updated_at": "t",
                            "data": data})
    return st


def test_une_insertion_sans_cle_metier_le_DIT(store):
    store.append_row("viviers", {"raison_sociale": "ACME"})
    rapport = store.off_schema_report()
    # ⚠️ Au PREMIER NIVEAU depuis le 09/09/2026, plus dans `notices`. Le message y
    # était exact et il n'a rien empêché : servi dix fois en une soirée à une campagne
    # qui lisait le statut et l'`_id`. Un fait qui contredit le succès annoncé ne se
    # range pas dans une liste qui sonne comme « informations diverses ».
    assert rapport["non_rapprochable"] == ["siren"], rapport
    assert "siren" in rapport["non_rapprochable_hint"]


def test_le_message_dit_la_CONSEQUENCE(store):
    """« Il manque un champ » n'apprend rien : ce qui compte est qu'aucune écriture
    ultérieure ne retrouvera cette ligne par sa clé."""
    store.append_row("viviers", {"raison_sociale": "ACME"})
    texte = store.off_schema_report()["non_rapprochable_hint"]
    assert "rapproch" in texte and "data_write" in texte


def test_la_ligne_est_ECRITE_quand_meme(store):
    """On n'empêche rien : une table se remplit souvent avant d'avoir sa clé."""
    row = store.append_row("viviers", {"raison_sociale": "ACME"})
    assert row["raison_sociale"] == "ACME"


def test_une_insertion_AVEC_la_cle_ne_dit_rien(store):
    store.append_row("viviers", {"siren": "123456789", "raison_sociale": "ACME"})
    assert "non_rapprochable" not in store.off_schema_report()


def test_une_cle_VIDE_est_refusee_et_non_plus_comptee_absente(store):
    """Signal feedback 994 : `""` entre dans l'index d'unicité, deux lignes « à clé
    vide » s'y confondraient. Refusée à l'entrée — bancs réels dans
    `test_cle_metier_vide_signal_994.py`. Seule l'ABSENCE de clé reste permise."""
    with pytest.raises(ValueError, match="clé métier vide ne désigne aucune entité"):
        store.append_row("viviers", {"siren": "", "raison_sociale": "ACME"})


def test_un_tableau_SANS_cle_declaree_ne_dit_rien(store, monkeypatch):
    """Pas de clé métier = pas de rapprochement promis : avertir y serait du bruit
    sur le régime normal d'un tableau libre."""
    monkeypatch.setattr(dsm.db, "get_datastore_by_id",
                        lambda ns_id: {"id": ns_id,
                                       "schema": {"fields": [{"key": "x"}]}})
    store.append_row("viviers", {"x": "1"})
    assert "non_rapprochable" not in store.off_schema_report()


def test_un_lot_ne_repete_pas_la_phrase(store):
    """Union sur un geste, comme `hors_schema` : cinq lignes sans clé ne rendent pas
    cinq fois le même message."""
    for i in range(5):
        store.append_row("viviers", {"raison_sociale": f"ACME {i}"})
    assert store.off_schema_report()["non_rapprochable"] == ["siren"]
