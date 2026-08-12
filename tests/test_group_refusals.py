"""Deux refus métier d'équipe qui remontaient en 500 (#281).

Le motif est le même des deux côtés : le store lève, personne ne traduit, et
`_rest_adapter` n'attrape qu'`AuthzDenied`/`ValidationError` — donc l'appelant reçoit
une erreur SERVEUR pour une entrée refusée. Deux conséquences : le refus part dans
Sentry comme un bug, et le client n'a rien d'actionnable.

Ces tests exercent les handlers de capacité pour de vrai (pas de monkeypatch du seam
qu'ils vérifient) : seul l'accès au store est stubbé, parce qu'il demanderait une base.
"""
from __future__ import annotations

import pytest

from oto_mcp import group_store, org_store
from oto_mcp.capabilities import groups, groups_doctrine
from oto_mcp.capabilities._types import AuthzDenied


class _Ctx:
    sub = "user-1"
    org_id = 2
    group_id = None


def test_ecrire_le_readme_comme_procedure_est_un_400_nomme(monkeypatch):
    """Le readme d'équipe est réservé : le refus doit être déclaré, pas une 500."""
    appels = []
    monkeypatch.setattr(group_store, "set_group_instruction",
                        lambda *a, **k: appels.append(a) or 1)

    inp = groups_doctrine.InstrSetInput(
        group_id=7, slug=org_store.BASE_SLUG, body_md="peu importe")
    with pytest.raises(AuthzDenied) as e:
        groups_doctrine._set(_Ctx(), inp)

    assert e.value.status == 400
    assert e.value.code == "reserved_slug"
    # Le message doit dire OÙ écrire, sinon le refus n'est pas actionnable.
    assert "oto_guide" in str(e.value)
    # Et le store ne doit pas avoir été touché : on refuse avant, pas après.
    assert appels == []


def test_un_slug_normal_passe_toujours(monkeypatch):
    """Garde-fou du garde-fou : le refus ne doit pas manger les procédures légitimes."""
    monkeypatch.setattr(group_store, "set_group_instruction", lambda *a, **k: 3)
    out = groups_doctrine._set(
        _Ctx(), groups_doctrine.InstrSetInput(group_id=7, slug="relance", body_md="x"))
    assert out["version"] == 3 and out["slug"] == "relance"


def test_renommer_vers_un_nom_pris_rend_409_comme_la_creation(monkeypatch):
    """Même conflit métier que `group.create`, donc même réponse — c'était 500."""
    monkeypatch.setattr(group_store, "get_group", lambda gid: {"id": gid, "org_id": 2})
    monkeypatch.setattr(group_store, "list_groups",
                        lambda oid: [{"id": 9, "name": "Finance"}, {"id": 7, "name": "Sales"}])
    touche = []
    monkeypatch.setattr(group_store, "update_group",
                        lambda *a, **k: touche.append(a) or True)

    with pytest.raises(AuthzDenied) as e:
        groups._update_group(_Ctx(), groups.UpdateGroupInput(group_id=7, name="Finance"))

    assert e.value.status == 409
    assert e.value.code == "group_exists"
    assert touche == []


def test_la_casse_ne_contourne_pas_le_conflit(monkeypatch):
    """`finance` et `Finance` sont le même nom — sinon le contrôle est décoratif."""
    monkeypatch.setattr(group_store, "get_group", lambda gid: {"id": gid, "org_id": 2})
    monkeypatch.setattr(group_store, "list_groups", lambda oid: [{"id": 9, "name": "Finance"}])
    monkeypatch.setattr(group_store, "update_group", lambda *a, **k: True)

    with pytest.raises(AuthzDenied) as e:
        groups._update_group(_Ctx(), groups.UpdateGroupInput(group_id=7, name="  finance  "))
    assert e.value.code == "group_exists"


def test_se_renommer_en_soi_meme_n_est_pas_un_conflit(monkeypatch):
    """Le groupe s'exclut lui-même : re-poser son propre nom doit passer."""
    monkeypatch.setattr(group_store, "get_group", lambda gid: {"id": gid, "org_id": 2})
    monkeypatch.setattr(group_store, "list_groups", lambda oid: [{"id": 7, "name": "Sales"}])
    monkeypatch.setattr(group_store, "update_group", lambda *a, **k: True)

    out = groups._update_group(_Ctx(), groups.UpdateGroupInput(group_id=7, name="Sales"))
    assert out["ok"] is True


def test_changer_la_description_seule_ne_lit_pas_les_noms(monkeypatch):
    """Sans `name`, aucun contrôle de collision — et donc aucun appel au store pour ça."""
    lectures = []
    monkeypatch.setattr(group_store, "get_group", lambda gid: lectures.append(gid) or None)
    monkeypatch.setattr(group_store, "update_group", lambda *a, **k: True)

    out = groups._update_group(
        _Ctx(), groups.UpdateGroupInput(group_id=7, description="équipe commerciale"))
    assert out["ok"] is True
    assert lectures == []
