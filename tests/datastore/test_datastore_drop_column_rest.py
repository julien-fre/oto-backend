"""La face REST de `drop_column` — celle que le cockpit appelle (#296).

Le geste existait déjà comme capacité, sans route : `rest=None`, opt-out assumé
tant que rien ne l'affichait. Le cockpit affiche désormais « supprimer cette
colonne » dans le menu ⋯ d'un en-tête, donc la ligne est posée — et ce qu'il faut
garder est la FORME choisie, pas la logique du store (celle-ci a déjà sa suite,
`test_datastore_drop_column.py`) : le corps porte `{key, confirm}`, le chemin ne
porte que le namespace.

Deux propriétés qui coûteraient cher à re-découvrir depuis le front :
`confirm` omis est un refus (la garde vit dans le store, aucune surface ne peut
l'oublier), et une clé pointée passe INTACTE — c'est toute la raison de ne pas
l'avoir mise dans un segment de chemin.
"""
from __future__ import annotations

import pytest

from _datastore_rest import call, stub_authz

from oto_mcp.capabilities.datastore import columns as dsc


class _Store:
    """Le store réduit à ce que la route en attend : l'appel qu'elle lui passe."""

    def __init__(self):
        self.vu = []

    def drop_column(self, namespace, key, *, confirm):
        self.vu.append((namespace, key, confirm))
        if not confirm:
            raise ValueError(
                f"purge de la colonne `{key}` non confirmée — c'est irréversible")
        return {"namespace": namespace, "key": key, "rows": 12}


@pytest.fixture()
def store(monkeypatch):
    st = _Store()
    stub_authz(monkeypatch)
    monkeypatch.setattr(dsc, "make_store", lambda sub: st)
    return st


def test_le_corps_porte_la_cle_et_la_confirmation(store):
    status, corps = call("me.datastore.drop_column",
                         path_params={"namespace": "160"},
                         body={"key": "actualite_sociale", "confirm": True})
    assert status == 200
    assert store.vu == [("160", "actualite_sociale", True)]
    # `rows` est ce qui permet à l'appelant de dire « 12 lignes purgées » plutôt que
    # « c'est fait » : le compte fait partie du contrat, pas de l'habillage.
    assert corps == {"namespace": "160", "key": "actualite_sociale", "rows": 12}


def test_sans_confirmation_c_est_un_400_qui_dit_quoi_faire(store):
    status, corps = call("me.datastore.drop_column",
                         path_params={"namespace": "160"},
                         body={"key": "actualite_sociale"})
    assert (status, corps["error"]) == (400, "invalid_drop_column")
    # La phrase du store arrive JUSQU'À l'appelant — le front la rend telle quelle.
    assert "non confirmée" in corps["detail"]


def test_une_cle_pointee_traverse_intacte(store):
    """`site_web.comment` est la raison de ne pas router par `…/columns/{key}` : un
    point dans un segment de chemin est une invitation à le perdre en route."""
    call("me.datastore.drop_column", path_params={"namespace": "160"},
         body={"key": "site_web.comment", "confirm": True})
    assert store.vu == [("160", "site_web.comment", True)]


def test_un_champ_inconnu_est_refuse_jamais_ignore(store):
    """La garde générale de l'adaptateur vaut ici aussi : un front qui se trompe de
    nom de champ doit l'apprendre, pas recevoir un 200 et un défaut."""
    status, corps = call("me.datastore.drop_column",
                         path_params={"namespace": "160"},
                         body={"column": "actualite_sociale", "confirm": True})
    assert status == 400
    assert store.vu == []
