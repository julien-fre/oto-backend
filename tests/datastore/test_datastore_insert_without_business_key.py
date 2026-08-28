"""Une ligne créée SANS la clé métier déclarée est non rapprochable — le dire (#390).

Le signal a coûté un enrichissement complet : neuf minutes, dix-sept appels d'outils,
trois contacts dont un DRH avec e-mail vérifié, deux actualités sourcées. L'agent a
écrit l'identifiant DANS `row` au lieu du paramètre `id`, et une 501ᵉ ligne SANS SIREN
ni raison sociale est née en portant tout le travail, la ligne réservée restant vide.
Aucune erreur, aucun avertissement — 28 champs repris à la main.

Les deux premières demandes du signal sont servies depuis le 13-15/08 : le bail protège
l'ÉCRITURE et pas seulement l'attribution (`_lease_guard`, `_assert_writable`,
`writing_as`), et l'adresse égarée est refusée ou promue (`_id` dans `row` devient
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
    monkeypatch.setattr(dsm.db, "get_datastore_namespace_by_id",
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
    notices = store.off_schema_report()["notices"]
    assert any("siren" in n for n in notices), notices


def test_le_message_dit_la_CONSEQUENCE(store):
    """« Il manque un champ » n'apprend rien : ce qui compte est qu'aucune écriture
    ultérieure ne retrouvera cette ligne par sa clé."""
    store.append_row("viviers", {"raison_sociale": "ACME"})
    texte = " ".join(store.off_schema_report()["notices"])
    assert "rapproch" in texte and "data_write" in texte


def test_la_ligne_est_ECRITE_quand_meme(store):
    """On n'empêche rien : une table se remplit souvent avant d'avoir sa clé."""
    row = store.append_row("viviers", {"raison_sociale": "ACME"})
    assert row["raison_sociale"] == "ACME"


def test_une_insertion_AVEC_la_cle_ne_dit_rien(store):
    store.append_row("viviers", {"siren": "123456789", "raison_sociale": "ACME"})
    assert "notices" not in store.off_schema_report()


def test_une_cle_VIDE_compte_comme_absente(store):
    store.append_row("viviers", {"siren": "", "raison_sociale": "ACME"})
    assert store.off_schema_report().get("notices")


def test_un_tableau_SANS_cle_declaree_ne_dit_rien(store, monkeypatch):
    """Pas de clé métier = pas de rapprochement promis : avertir y serait du bruit
    sur le régime normal d'un tableau libre."""
    monkeypatch.setattr(dsm.db, "get_datastore_namespace_by_id",
                        lambda ns_id: {"id": ns_id,
                                       "schema": {"fields": [{"key": "x"}]}})
    store.append_row("viviers", {"x": "1"})
    assert "notices" not in store.off_schema_report()


def test_un_lot_ne_repete_pas_la_phrase(store):
    """Union sur un geste, comme `hors_schema` : cinq lignes sans clé ne rendent pas
    cinq fois le même message."""
    for i in range(5):
        store.append_row("viviers", {"raison_sociale": f"ACME {i}"})
    assert len(store.off_schema_report()["notices"]) == 1
