"""`org.invite.accept` — un espace perso SUSPENDU n'empêche pas de rejoindre une org.

Cas vécu (04/11/2026) : invité par mail dans l'org de son équipe, la personne a déjà un
compte, dont l'espace perso a fini son essai sans abonnement (donc suspendu). Le lien
du mail affichait « Invitation invalid — L'espace #N est suspendu » : l'acceptation
est `SUB_ONLY`, elle résout sous l'org MAISON du compte, et la garde des orgs
suspendues la refusait là. `org.invite.claim` avait déjà son exemption, pas elle.

Figé ici :
- la garde de l'adaptateur laisse passer accept et reject sous une maison suspendue ;
- le handler juge l'org de l'INVITATION : suspendue, elle ne se rejoint pas, et
  l'invitation reste en attente ;
- active, elle se rejoint malgré la maison suspendue, et DEVIENT la maison : la
  connexion suivante atterrit dessus, pas sur le mur de l'essai fini ;
- une maison réelle ACTIVE, elle, ne bouge pas (oto#161).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("cle", ["org.invite.accept", "org.invite.reject"])
def test_une_maison_suspendue_laisse_passer_l_invitation(monkeypatch, cle):
    """Sans base : l'adaptateur garde l'org sous laquelle l'appel RÉSOUT (la maison)."""
    from oto_mcp import org_suspension
    monkeypatch.setattr(org_suspension, "_suspendues", lambda: frozenset({41}))
    org_suspension.garde_capacite(cle, SimpleNamespace(platform_worker=False, org_id=41))


def _org(nom: str) -> int:
    from oto_mcp import org_store
    oid = org_store.create_org(nom, created_by="u-acc-admin")
    org_store.add_org_member(oid, "u-acc-admin", "org_admin")
    return oid


def _compte_avec_maison(sub: str) -> tuple[str, int]:
    """Un compte inscrit AVANT l'invitation, avec son espace perso comme maison."""
    from oto_mcp import org_store
    from oto_mcp.db import upsert_user
    email = f"{sub}@x.tld"
    upsert_user(sub, email=email)
    maison = org_store.create_org(f"perso {sub}", created_by=sub)
    org_store.add_org_member(maison, sub, "org_admin")
    org_store.set_active_org(sub, maison)
    return email, maison


def _accepter(sub: str, token: str) -> dict:
    from oto_mcp.capabilities.orgs import invites as cap
    return cap._invite_accept(SimpleNamespace(sub=sub), cap.InviteAcceptInput(token=token))


def test_maison_suspendue_l_org_active_se_rejoint(live):
    from oto_mcp import org_store, org_suspension
    sub = "u-acc-maison-susp"
    email, maison = _compte_avec_maison(sub)
    org = _org("Accept équipe active")
    _id, token = org_store.create_invitation(org, email, "org_member", "u-acc-admin")
    org_store.suspend_org(maison, by="svc", reason="trial_ended")
    org_suspension.invalider()
    try:
        org_suspension.garde_capacite(
            "org.invite.accept", SimpleNamespace(platform_worker=False, org_id=maison))
        res = _accepter(sub, token)
        assert res["ok"] and res["org_id"] == org
        assert org_store.get_org_role(org, sub) == "org_member"
        assert org_store.get_active_org(sub) == org
        assert res["active_org"] == org
    finally:
        org_store.resume_org(maison)
        org_suspension.invalider()


def test_l_org_de_l_invitation_suspendue_ne_se_rejoint_pas(live):
    from oto_mcp import org_store, org_suspension
    from oto_mcp.capabilities._types import AuthzDenied
    sub = "u-acc-cible-susp"
    email, _maison = _compte_avec_maison(sub)
    org = _org("Accept équipe suspendue")
    _id, token = org_store.create_invitation(org, email, "org_member", "u-acc-admin")
    org_store.suspend_org(org, by="svc", reason="trial_ended")
    org_suspension.invalider()
    try:
        with pytest.raises(AuthzDenied) as e:
            _accepter(sub, token)
        assert (e.value.status, e.value.code) == (403, "org_suspended")
        assert org_store.get_org_role(org, sub) is None
        assert org_store.get_invitation_by_token(token) is not None
    finally:
        org_store.resume_org(org)
        org_suspension.invalider()


def test_une_maison_reelle_active_ne_bouge_pas(live):
    from oto_mcp import org_store
    sub = "u-acc-maison-active"
    email, maison = _compte_avec_maison(sub)
    org = _org("Accept équipe, maison active")
    _id, token = org_store.create_invitation(org, email, "org_member", "u-acc-admin")
    res = _accepter(sub, token)
    assert res["org_id"] == org
    assert org_store.get_active_org(sub) == maison
