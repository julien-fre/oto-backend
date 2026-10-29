"""`org.invite.claim` — l'invité DÉJÀ inscrit qui ouvre un lien vers l'org.

Cas vécu (29/10/2026) : invité par mail, la personne reçoit ensuite un lien vers la
page de l'org, le clique sans avoir ouvert le mail, et le front la renvoie à
l'accueil (non-membre). La réconciliation par email n'existe qu'au SIGNUP (gate
`inserted` d'`upsert_user`) : un compte existant n'en profite jamais. La réclamation
rejoue la même correspondance d'email, bornée à l'org ouverte.

Ce qui est figé ici, dans l'ordre où ça mordrait :
- l'invitation adressée à mon adresse POUR CETTE ORG est honorée, et consommée ;
- celle d'une AUTRE org ne l'est pas — ouvrir X ne fait pas rejoindre Y ;
- une invitation à une autre adresse, REFUSÉE, EXPIRÉE ou RÉVOQUÉE ne se réclame pas ;
- déjà membre = succès (double clic, deux onglets) ;
- la maison établie ne bouge pas (oto#161 — même corps que l'acceptation) ;
- l'adresse est celle que l'annuaire GARANTIT : absente ou non vérifiée, rien ;
- un compte d'un autre tenant que l'org, une org SUSPENDUE : refus nommés ;
- deux consommations de la même invitation : une seule gagne (lien mail compris) ;
- un compte en pause n'atteint pas la route.

Vrai PostgreSQL : `_PENDING`, `lower(email)`, le filtre d'org, le tenant de l'org et
la consommation (`UPDATE … RETURNING`) vivent dans le SQL. L'annuaire, lui, est un
service réseau : il est doublé (`_annuaire_garantit_l_adresse_inscrite`).
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def _annuaire_garantit_l_adresse_inscrite(monkeypatch):
    """Par défaut l'annuaire garantit l'adresse du compte, telle qu'inscrite. Les
    tests de l'adresse non vérifiée reposent ce double."""
    from oto_mcp import db
    from oto_mcp.auth import annuaire
    monkeypatch.setattr(annuaire, "logto_user_primary_email",
                        lambda sub: (db.get_user(sub) or {}).get("email"))


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


def _refus(sub: str, org_id: int) -> tuple[int, str]:
    from oto_mcp.capabilities._types import AuthzDenied
    with pytest.raises(AuthzDenied) as e:
        _claim(sub, org_id)
    return e.value.status, e.value.code


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


def test_une_invitation_expiree_ne_se_reclame_pas(live):
    from oto_mcp import org_store
    sub = "u-claim-expiree"
    email = _compte(sub)
    org = _org("Claim expirée")
    inv = org_store.peek_invitation(token=_inviter(org, email))
    with org_store._connect() as conn:
        conn.execute("UPDATE org_invitations SET expires_at = NOW() - interval '1 day' "
                     "WHERE id = %s", (inv["id"],))

    assert _refus(sub, org) == (404, "no_pending_invitation")
    assert org_store.get_org_role(org, sub) is None


def test_une_invitation_revoquee_ne_se_reclame_pas(live):
    from oto_mcp import org_store
    sub = "u-claim-revoquee"
    email = _compte(sub)
    org = _org("Claim révoquée")
    inv = org_store.peek_invitation(token=_inviter(org, email))
    assert org_store.revoke_invitation(org, inv["id"])

    assert _refus(sub, org) == (404, "no_pending_invitation")
    assert org_store.get_org_role(org, sub) is None


@pytest.mark.parametrize("garantie", ["absente", "vide", "tenant_tiers"])
def test_une_adresse_que_l_annuaire_ne_garantit_pas_ne_designe_rien(
        live, monkeypatch, garantie):
    """L'adresse inscrite (`users.email`, recopiée du claim du jeton) correspond à
    l'invitation, mais l'annuaire ne la garantit pas : rien n'est consommé."""
    from oto_mcp import org_store, tenancy
    from oto_mcp.auth import annuaire
    sub = f"u-claim-non-verifiee-{garantie}"
    email = _compte(sub)
    org = _org(f"Claim non vérifiée {garantie}")
    _inviter(org, email)

    def _annuaire(s):
        if garantie == "tenant_tiers":
            raise tenancy.ForeignTenantDirectory("compte d'un annuaire tiers")
        return None if garantie == "absente" else ""
    monkeypatch.setattr(annuaire, "logto_user_primary_email", _annuaire)

    assert _refus(sub, org) == (403, "email_not_verified")
    assert org_store.get_org_role(org, sub) is None
    assert org_store.find_pending_invitation(org, email) is not None


def test_un_compte_d_un_autre_tenant_que_l_org_est_refuse(live):
    """L'org est chez un tenant tiers (axe de la marque), le compte est du tenant
    primaire (sub nu) : même adresse, même invitation — refus nommé, rien consommé."""
    from oto_mcp import db, org_store
    sub = "u-claim-tenant"
    email = _compte(sub)
    org = _org("Claim tenant tiers")
    with db._connect() as conn:
        conn.execute("UPDATE orgs SET front_brand = 'tenant-claim' WHERE id = %s", (org,))
    _inviter(org, email)

    assert _refus(sub, org) == (403, "tenant_mismatch")
    assert org_store.get_org_role(org, sub) is None
    assert org_store.find_pending_invitation(org, email) is not None


def test_une_org_suspendue_ne_se_rejoint_pas(live):
    """L'org jugée est `{id}` — celle qu'on rejoindrait —, par la garde des orgs
    suspendues : l'appel, lui, résout sous l'org maison du compte, active."""
    from oto_mcp import org_store, org_suspension
    sub = "u-claim-suspendue"
    email = _compte(sub)
    org = _org("Claim suspendue")
    _inviter(org, email)
    org_store.suspend_org(org, by="svc", reason="trial_ended")
    org_suspension.invalider()
    try:
        assert _refus(sub, org) == (403, "org_suspended")
        assert org_store.get_org_role(org, sub) is None
        assert org_store.find_pending_invitation(org, email) is not None
    finally:
        org_store.resume_org(org)
        org_suspension.invalider()


def test_une_maison_suspendue_n_empeche_pas_de_rejoindre_une_org_active(monkeypatch):
    """L'adaptateur garde l'org sous laquelle l'appel RÉSOUT (la maison, pour une
    capacité `SUB_ONLY`) : la réclamation lui échappe, sinon un espace perso suspendu
    interdirait de rejoindre l'org qui paie. C'est le handler qui juge `{id}`. Sans
    base."""
    from oto_mcp import org_suspension
    monkeypatch.setattr(org_suspension, "_suspendues", lambda: frozenset({41}))
    org_suspension.garde_capacite(
        "org.invite.claim", SimpleNamespace(platform_worker=False, org_id=41))


def test_deux_consommations_une_seule_gagne(live):
    """La course, rejouée pas à pas : deux appels ont LU la même invitation en
    attente, puis écrivent. Le premier la consomme et rejoint ; le second ne trouve
    plus rien à consommer et n'écrit RIEN — ni adhésion, ni écrasement de
    `accepted_sub`. Par l'adresse (réclamation) comme par le jeton (lien du mail)."""
    from oto_mcp import org_store
    a, b = "u-claim-course-a", "u-claim-course-b"
    _compte(b)
    email = _compte(a)
    org = _org("Claim course")
    token = _inviter(org, email)
    lue_par_adresse = org_store._pending_for_email(email, org_id=org)
    lue_par_jeton = org_store.get_invitation_by_token(token)
    assert lue_par_adresse["id"] == lue_par_jeton["id"]

    assert org_store._accept_invitation_row(lue_par_adresse, a, actor=a)["org_id"] == org
    assert org_store._accept_invitation_row(lue_par_jeton, b, actor=b) is None

    assert org_store.get_org_role(org, a) == "org_member"
    assert org_store.get_org_role(org, b) is None
    assert org_store.peek_invitation(token=token)["accepted_sub"] == a
    # Le lien du mail ouvert par l'autre porteur après coup : 410, pas une adhésion.
    assert org_store.accept_invitation(token, b) is None


def test_la_course_perdue_par_le_meme_compte_reste_un_succes(live, monkeypatch):
    """Double clic : les deux réclamations du MÊME compte ont lu l'invitation, la
    première l'a consommée. La seconde perd la consommation — et rend le succès
    idempotent, pas un 404, puisque l'org est rejointe."""
    from oto_mcp import org_store
    sub = "u-claim-double-clic"
    email = _compte(sub)
    org = _org("Claim double clic")
    _inviter(org, email)
    lue = org_store._pending_for_email(email, org_id=org)
    org_store._accept_invitation_row(lue, sub, actor=sub)
    monkeypatch.setattr(org_store, "_pending_for_email", lambda *a, **k: lue)

    out = org_store.claim_invitation_for_org(sub, email, org)

    assert out == {"org_id": org, "org_role": "org_member",
                   "group_id": None, "group_role": None}


def test_un_compte_en_pause_n_atteint_pas_la_route(monkeypatch):
    """La pause se juge à la PORTE, une fois pour toutes les routes
    (`garde_identite`, seul appelant admis du prédicat) : la réclamation n'a pas à la
    recopier. Ce banc prouve que la porte la refuse sur CETTE route, avant que rien
    ne soit lu ni écrit. Sans base."""
    from starlette.requests import Request

    from oto_mcp.api import routes as api_routes
    from oto_mcp.auth import token_scopes

    class _Verifier:
        async def verify_token(self, token):
            return SimpleNamespace(claims={"sub": "u-claim-pause",
                                                 "email": "u-claim-pause@x.tld"})

    def _jamais(*a, **k):
        raise AssertionError("un compte en pause ne doit rien écrire")

    monkeypatch.setattr(api_routes.db, "get_suspension", lambda sub: {
        "sub": sub, "suspended_at": "2026-10-29T10:00:00+00:00",
        "suspended_by": "op-1", "suspended_reason": "en pause"})
    monkeypatch.setattr(api_routes.db, "upsert_user", _jamais)
    req = Request({
        "type": "http", "method": "POST", "query_string": b"", "root_path": "",
        "path": "/api/me/orgs/7/invitations/claim", "scheme": "http",
        "server": ("test", 80), "http_version": "1.1",
        "headers": [(b"authorization", b"Bearer eyJ-un-jwt")]})
    try:
        sub, err = asyncio.run(api_routes._authenticate(req, verifier=_Verifier()))
    finally:
        token_scopes.set_current(None)

    assert sub is None and err.status_code == 403
    assert json.loads(bytes(err.body).decode())["error"] == "account_suspended"
