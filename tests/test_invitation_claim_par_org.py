"""`org.invite.claim` — l'invité DÉJÀ inscrit qui ouvre un lien vers l'org.

Cas vécu (29/10/2026) : invité par mail, la personne reçoit ensuite un lien vers la
page de l'org, le clique sans avoir ouvert le mail, et le front la renvoie à
l'accueil (non-membre). La réconciliation par email n'existe qu'au SIGNUP (gate
`inserted` d'`upsert_user`) : un compte existant n'en profite jamais. La réclamation
rejoue la même correspondance d'email, bornée à l'org ouverte.

Ce qui est figé ici, dans l'ordre où ça mordrait :
- l'invitation adressée à mon adresse POUR CETTE ORG est honorée, et consommée ;
- celle d'une AUTRE org ne l'est pas — ouvrir X ne fait pas rejoindre Y ;
- une invitation à une autre adresse, ou REFUSÉE, ne se réclame pas ;
- déjà membre = succès (double clic, deux onglets) ;
- la maison établie ne bouge pas (oto#161 — même corps que l'acceptation).

Vrai PostgreSQL : `_PENDING`, `lower(email)` et le filtre d'org vivent dans le SQL.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest


def _compte(sub: str) -> str:
    """Un compte inscrit AVANT l'invitation — sinon la réconciliation de signup
    l'aurait déjà honorée, et le banc ne mesurerait plus rien."""
    from oto_mcp.db import upsert_user
    email = f"{sub}@x.tld"
    upsert_user(sub, email=email)
    return email


def _org(nom: str) -> int:
    from oto_mcp import org_store
    oid = org_store.create_org(nom, created_by="u-claim-admin")
    org_store.add_org_member(oid, "u-claim-admin", "org_admin")
    return oid


def _inviter(org_id: int, email: str) -> str:
    from oto_mcp import org_store
    _id, token = org_store.create_invitation(org_id, email, "org_member", "u-claim-admin")
    return token


def _claim(sub: str, org_id: int) -> dict:
    from oto_mcp.capabilities.orgs import invites as cap
    return cap._invite_claim(SimpleNamespace(sub=sub), cap.InviteClaimInput(org_id=org_id))


def test_l_invitation_qui_m_attend_dans_l_org_est_honoree(live):
    from oto_mcp import org_store
    sub = "u-claim-ok"
    email = _compte(sub)
    org = _org("Claim ok")
    token = _inviter(org, email)

    out = _claim(sub, org)

    assert out["ok"] and out["org_id"] == org and out["name"] == "Claim ok"
    assert org_store.get_org_role(org, sub) == "org_member"
    # Consommée : plus dans la file de l'émetteur, et le lien du mail ouvert après
    # coup reste un succès idempotent pour la même personne.
    assert org_store.find_pending_invitation(org, email) is None
    assert org_store.accept_invitation(token, sub)["org_id"] == org


def test_ouvrir_une_org_ne_fait_pas_rejoindre_une_autre(live):
    from oto_mcp import org_store
    from oto_mcp.capabilities._types import AuthzDenied
    sub = "u-claim-borne"
    email = _compte(sub)
    ouverte, invitante = _org("Claim ouverte"), _org("Claim invitante")
    _inviter(invitante, email)

    with pytest.raises(AuthzDenied) as e:
        _claim(sub, ouverte)

    assert e.value.status == 404 and e.value.code == "no_pending_invitation"
    assert org_store.get_org_role(invitante, sub) is None
    assert org_store.find_pending_invitation(invitante, email) is not None


def test_une_invitation_a_une_autre_adresse_ne_se_reclame_pas(live):
    from oto_mcp import org_store
    from oto_mcp.capabilities._types import AuthzDenied
    sub = "u-claim-autre"
    _compte(sub)
    org = _org("Claim autre adresse")
    _inviter(org, "quelqu-un-d-autre@x.tld")

    with pytest.raises(AuthzDenied):
        _claim(sub, org)
    assert org_store.get_org_role(org, sub) is None


def test_une_invitation_refusee_ne_se_reclame_pas(live):
    from oto_mcp import org_store
    from oto_mcp.capabilities._types import AuthzDenied
    sub = "u-claim-refus"
    email = _compte(sub)
    org = _org("Claim refus")
    inv = org_store.peek_invitation(token=_inviter(org, email))
    org_store.mark_invitation_declined(inv["id"], sub)

    with pytest.raises(AuthzDenied):
        _claim(sub, org)
    assert org_store.get_org_role(org, sub) is None


def test_deja_membre_est_un_succes(live):
    sub = "u-claim-membre"
    email = _compte(sub)
    org = _org("Claim membre")
    _inviter(org, email)
    _claim(sub, org)

    out = _claim(sub, org)

    assert out["ok"] and out["org_id"] == org and out["org_role"] == "org_member"


def test_la_maison_etablie_ne_bouge_pas(live):
    from oto_mcp import org_store
    sub = "u-claim-maison"
    email = _compte(sub)
    maison = org_store.create_org("Claim maison", created_by=sub)
    org_store.add_org_member(maison, sub, "org_admin")
    org_store.set_active_org(sub, maison)
    invitante = _org("Claim invitante maison")
    _inviter(invitante, email)

    out = _claim(sub, invitante)

    assert out["org_id"] == invitante
    assert out["active_org"] == maison == org_store.get_active_org(sub)
