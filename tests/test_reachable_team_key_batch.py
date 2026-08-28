"""La carte préchargée des secrets d'équipe rend EXACTEMENT ce que la lecture unitaire rend.

`status_for` construisait déjà l'inventaire du coffre par équipe pour le barreau
`group` de sa sonde, puis redemandait la même chose à la base, connecteur par
connecteur, pour le hint `team_key_group` — et comme ce hint ne se déclenche que
sur les `forbidden`, c'est-à-dire la majorité d'un compte réel, il coûtait à lui
seul 67 allers-retours sur une seule équipe.

Ce fichier est le cliquet de l'accélération, sur le modèle de
`test_presence_batch.py` : la carte est confrontée à la lecture d'origine sur TOUT
le registre, même contexte, et doit rendre le même verdict — y compris là où le
verdict est « aucune équipe ». Un barreau ajouté demain casse ce différentiel au
lieu de produire deux vérités.
"""
from __future__ import annotations

import pytest

from oto_mcp import access
from oto_mcp import providers


SALES = {"group_id": 2, "org_id": 35, "name": "sales"}
OPS = {"group_id": 5, "org_id": 35, "name": "ops"}
SUPPORT = {"group_id": 9, "org_id": 35, "name": "support"}


def _wire(monkeypatch, *, groups, secrets):
    """`secrets` = {group_id: {connecteurs}} — la VÉRITÉ du coffre pour ce test.
    Les deux chemins la lisent différemment : l'un par `has_group_secret` (une
    question par connecteur), l'autre par `list_credentials` (une lecture par
    équipe, celle que la carte consomme)."""
    monkeypatch.setattr(access.group_store, "list_groups_for_user",
                        lambda sub, org_id=None: list(groups))
    monkeypatch.setattr(access.group_store, "has_group_secret",
                        lambda gid, prov: prov in secrets.get(int(gid), set()))
    monkeypatch.setattr(
        access.credentials_store, "list_credentials",
        lambda entity_type, entity_id: (
            [{"connector": c} for c in sorted(secrets.get(int(entity_id), set()))]
            if entity_type == "group" else []))


def _carte(groups):
    return access.cascade.group_secret_map(groups)


# Tout le registre, pas seulement les org-partageables : le gate ORG_SHAREABLE fait
# partie de ce qui doit rester identique.
TOUS = sorted(providers.REGISTRY)


@pytest.mark.parametrize("groups,secrets", [
    # Rien nulle part — le cas le plus courant d'un compte réel.
    ([SALES], {}),
    # Une équipe, une clé.
    ([SALES], {2: {"zoho"}}),
    # Plusieurs équipes, la clé sur la DERNIÈRE : vérifie qu'on ne s'arrête pas trop tôt.
    ([SALES, OPS, SUPPORT], {9: {"zoho"}}),
    # Plusieurs équipes en détiennent une : c'est l'ORDRE de `groups` qui tranche,
    # et la carte ne doit pas le renverser (un set n'a pas d'ordre, la liste si).
    ([SALES, OPS, SUPPORT], {5: {"zoho"}, 9: {"zoho"}, 2: {"stripe"}}),
    # Aucune équipe du tout.
    ([], {2: {"zoho"}}),
])
def test_la_carte_rend_le_meme_verdict_que_la_lecture_unitaire(monkeypatch, groups, secrets):
    _wire(monkeypatch, groups=groups, secrets=secrets)
    carte = _carte(groups)
    divergents = []
    for provider in TOUS:
        vivant = access.reachable_team_key("u1", 35, provider, groups=groups)
        prechauffe = access.reachable_team_key("u1", 35, provider, groups=groups,
                                               secrets_by_group=carte)
        if vivant != prechauffe:
            divergents.append((provider, vivant, prechauffe))
    assert not divergents, (
        f"{len(divergents)} connecteur(s) où la carte et la base ne disent pas la "
        f"même chose : {divergents[:5]}")


def test_sans_carte_le_comportement_est_inchange(monkeypatch):
    """Les autres appelants (et les tests existants) n'ont pas de carte : le défaut
    doit rester la lecture unitaire, pas un « aucune équipe » silencieux."""
    lues = []
    _wire(monkeypatch, groups=[SALES], secrets={2: {"zoho"}})
    vraie = access.group_store.has_group_secret
    monkeypatch.setattr(access.group_store, "has_group_secret",
                        lambda gid, prov: (lues.append((gid, prov)), vraie(gid, prov))[1])
    assert access.reachable_team_key("u1", 35, "zoho") == {"id": 2, "name": "sales"}
    assert lues, "sans carte, la base doit être lue"


def test_avec_la_carte_la_base_n_est_plus_lue(monkeypatch):
    """Le but même du lot : zéro aller-retour par connecteur sur le chemin /api/me."""
    _wire(monkeypatch, groups=[SALES], secrets={2: {"zoho"}})
    carte = _carte([SALES])
    def _interdit(*a, **k):
        raise AssertionError("has_group_secret ne doit plus être appelé avec une carte")
    monkeypatch.setattr(access.group_store, "has_group_secret", _interdit)
    assert access.reachable_team_key("u1", 35, "zoho", groups=[SALES],
                                     secrets_by_group=carte) == {"id": 2, "name": "sales"}
    assert access.reachable_team_key("u1", 35, "stripe", groups=[SALES],
                                     secrets_by_group=carte) is None
