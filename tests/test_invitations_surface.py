"""Surface d'invitation (feature cascade plateforme/org/équipe + code court).

Niveau contrat (sans DB) : présence des capacités MCP/REST consommées par
oto-dashboard et la forme des inputs (toggle mail, email optionnel, accept
par token/code). Cf. capabilities/{orgs,groups,platform}_invites.py.
"""
import pytest

from oto_mcp import org_store
from oto_mcp.capabilities.orgs import invites as oi
from oto_mcp.capabilities import registry


def test_invite_caps_present():
    mcp = {c.mcp for c in registry.caps_with_mcp()}
    # ADR 0047 B3 : invite/accept vivent dans la console oto_org (op=invite /
    # op=accept_invite) ; referral/alpha sont RETIRÉS (ADR 0013 supersédé).
    assert "oto_org" in mcp
    # Feature cascade : invite d'équipe via oto_group (op=invite), invite plateforme
    # via oto_admin_invite (op=create/list/revoke).
    assert "oto_admin_invite" in mcp
    assert {"oto_referral_link", "oto_invite_to_alpha",
            "oto_accept_invite", "oto_invite_member"} & mcp == set()


def test_rest_routes_preserved():
    pairs = {(b.verb, b.path) for c in registry.caps_with_rest() for b in c.rest_bindings()}
    for vp in [
        ("POST", "/api/me/invitations/accept"),
        ("POST", "/api/orgs/{id}/invitations"),
        ("GET", "/api/orgs/{id}/invitations"),
        # Cascade équipe + plateforme.
        ("POST", "/api/groups/{id}/invitations"),
        ("GET", "/api/groups/{id}/invitations"),
        ("DELETE", "/api/groups/{id}/invitations/{inv}"),
        ("POST", "/api/admin/invitations"),
        ("GET", "/api/admin/invitations"),
        ("DELETE", "/api/admin/invitations/{inv}"),
    ]:
        assert vp in pairs, vp


def test_group_console_has_invite_op():
    from oto_mcp.capabilities import org_console
    ops = org_console.GroupInput.model_fields["op"].annotation
    # Literal[...] — l'op 'invite' doit être admissible.
    assert "invite" in getattr(ops, "__args__", ())


def test_scope_derived_from_targets():
    # Le scope d'une invitation est DÉRIVÉ des cibles (comme la cascade connecteurs).
    assert org_store._scope_of({"org_id": None, "group_id": None}) == "platform"
    assert org_store._scope_of({"org_id": 1, "group_id": None}) == "org"
    assert org_store._scope_of({"org_id": 1, "group_id": 2}) == "team"


def test_team_invite_requires_parent_org():
    # Une invitation d'équipe SANS org parente est incohérente (invariant équipe ⊂ org)
    # → rejet avant tout accès DB.
    with pytest.raises(ValueError):
        org_store.create_invitation(None, "x@y.z", "org_member", invited_by="s",
                                    group_id=7, group_role="group_member")


def test_send_email_toggle_defaults_true_email_optional():
    f = oi.InviteCreateInput.model_fields
    assert f["send_email"].default is True
    # email optionnel (None autorisé) → émission « code à partager soi-même »
    assert f["email"].default is None


def test_accept_input_multiform():
    f = oi.InviteAcceptInput.model_fields
    assert {"token", "code"} <= set(f)
    assert all(f[k].default is None for k in ("token", "code"))


def test_org_invite_create_requires_org_id():
    assert oi.InviteCreateInput.model_fields["org_id"].is_required()


def test_emit_invitation_sends_email(monkeypatch):
    """Régression (Sentry PYTHON-STARLETTE-3N) : le param `email` d'emit_invitation
    masquait le module `email` → AttributeError sur send_invite_email dès que
    send_email=True, sur les 3 niveaux de la cascade."""
    from oto_mcp import db, email
    from oto_mcp.capabilities._types import ResolvedCtx

    monkeypatch.setattr(org_store, "create_invitation",
                        lambda *a, **k: (1, "tok", "CODE1234"))
    monkeypatch.setattr(org_store, "org_front", lambda org_id: (None, None))
    monkeypatch.setattr(db, "get_user", lambda sub: {"email": "admin@org.test"})
    # Adresse jamais vue : pas de ligne `users` ⟹ locale=None, comportement FR
    # d'avant ce lot (oto-backend#700).
    monkeypatch.setattr(db, "get_user_by_email", lambda e: None)
    sent = {}
    monkeypatch.setattr(email, "send_invite_email",
                        lambda to, name, url, inviter, **kw: sent.update(to=to, name=name) or True)
    out = oi.emit_invitation(ResolvedCtx(sub="s1"), org_id=35, email="Invitee@Org.Test",
                             send_email=True, source="org_admin", role="org_member",
                             target_name="acme")
    assert out["emailed"] is True
    assert sent["to"] == "invitee@org.test" and sent["name"] == "acme"
    assert out["code"] == "CODE1234" and "/invitation/CODE1234" in out["invite_url"]


# --- Front qui héberge l'org (colonnes `orgs.front_*`) ----------------------

def test_invite_base_defaults_to_oto(monkeypatch):
    monkeypatch.delenv("OTO_INVITE_BASE_URL", raising=False)
    assert oi._invite_base() == "https://oto.cx"
    assert oi._invite_base(None) == "https://oto.cx"


def test_invite_base_uses_org_front():
    assert oi._invite_base("https://app.acme.test/") == "https://app.acme.test"


def test_nominal_url_skips_magic_link_for_third_party_front(monkeypatch):
    """Régression : l'OTT est minté sur NOTRE Logto (`LOGTO_ENDPOINT`, un seul global)
    et n'authentifie pas contre l'émetteur dédié d'un front tiers (Acme :
    `auth.acme.test`). Une org sous front tiers ne doit JAMAIS produire de magic-link,
    seulement le lien nu — sinon échec de connexion silencieux pour l'invité."""
    def _boom(*a, **k):
        raise AssertionError("magic_url ne doit pas être appelé sous un front tiers")
    monkeypatch.setattr(oi.oauth_facade, "magic_url", _boom)
    url = oi._nominal_url("CODE1234", "invitee@org.test", front_base="https://app.acme.test")
    assert url == "https://app.acme.test/invitation/CODE1234"


def test_nominal_url_keeps_magic_link_for_oto(monkeypatch):
    monkeypatch.setattr(oi.oauth_facade, "magic_url", lambda url, email: f"{url}?otl=stub")
    assert oi._nominal_url("CODE1234", "invitee@org.test").endswith("?otl=stub")


def test_emit_invitation_derives_front_from_org(monkeypatch):
    """Le lien ET la marque du mail viennent de l'org cible — pas l'un sans l'autre,
    et sans que l'appelant ne déclare quoi que ce soit (les 3 niveaux de la cascade
    en héritent)."""
    from oto_mcp import db, email
    from oto_mcp.capabilities._types import ResolvedCtx

    monkeypatch.setattr(org_store, "create_invitation",
                        lambda *a, **k: (1, "tok", "CODE1234"))
    monkeypatch.setattr(org_store, "org_front",
                        lambda org_id: ("https://app.acme.test", "acme"))
    monkeypatch.setattr(db, "get_user", lambda sub: {"email": "admin@org.test"})
    monkeypatch.setattr(db, "get_user_by_email", lambda e: None)
    sent = {}
    monkeypatch.setattr(email, "send_invite_email",
                        lambda to, name, url, inviter, **kw: sent.update(url=url, **kw) or True)
    out = oi.emit_invitation(ResolvedCtx(sub="s1"), org_id=178, email="invitee@org.test",
                             send_email=True, source="org_admin", role="org_member",
                             target_name="globex")
    assert out["invite_url"] == "https://app.acme.test/invitation/CODE1234"
    assert sent["url"] == "https://app.acme.test/invitation/CODE1234"  # pas d'OTT
    assert sent["brand"] == "acme"


def test_emit_invitation_passes_recipient_locale(monkeypatch):
    """oto-backend#700 : un invité qui a DÉJÀ un compte oto (ré-invitation, autre
    org) a peut-être posé sa préférence de langue via `me.locale.set` — elle doit
    suivre jusqu'au gabarit."""
    from oto_mcp import db, email
    from oto_mcp.capabilities._types import ResolvedCtx

    monkeypatch.setattr(org_store, "create_invitation",
                        lambda *a, **k: (1, "tok", "CODE1234"))
    monkeypatch.setattr(org_store, "org_front", lambda org_id: (None, None))
    monkeypatch.setattr(db, "get_user", lambda sub: {"email": "admin@org.test"})
    monkeypatch.setattr(db, "get_user_by_email",
                        lambda e: {"sub": "u9", "email": e, "locale": "en"})
    sent = {}
    monkeypatch.setattr(email, "send_invite_email",
                        lambda to, name, url, inviter, **kw: sent.update(**kw) or True)
    oi.emit_invitation(ResolvedCtx(sub="s1"), org_id=35, email="invitee@org.test",
                       send_email=True, source="org_admin", role="org_member",
                       target_name="acme")
    assert sent["locale"] == "en"


def test_emit_invitation_locale_none_for_unknown_email(monkeypatch):
    """Une adresse jamais vue n'a pas de ligne `users` : locale=None, et le
    gabarit sert FR — la détection de langue pour un contact jamais loggé reste
    hors scope (oto-backend#700)."""
    from oto_mcp import db, email
    from oto_mcp.capabilities._types import ResolvedCtx

    monkeypatch.setattr(org_store, "create_invitation",
                        lambda *a, **k: (1, "tok", "CODE1234"))
    monkeypatch.setattr(org_store, "org_front", lambda org_id: (None, None))
    monkeypatch.setattr(db, "get_user", lambda sub: {"email": "admin@org.test"})
    monkeypatch.setattr(db, "get_user_by_email", lambda e: None)
    sent = {}
    monkeypatch.setattr(email, "send_invite_email",
                        lambda to, name, url, inviter, **kw: sent.update(**kw) or True)
    oi.emit_invitation(ResolvedCtx(sub="s1"), org_id=35, email="jamais-vue@org.test",
                       send_email=True, source="org_admin", role="org_member",
                       target_name="acme")
    assert sent["locale"] is None


def test_invite_create_input_carries_no_front_field():
    """Le front est DÉRIVÉ de l'org, jamais déclaré par le client : aucun champ de
    ce genre ne doit apparaître au contrat (une invitation ne peut pas prétendre
    venir d'un front auquel l'org n'appartient pas)."""
    fields = set(oi.InviteCreateInput.model_fields)
    assert fields == {"org_id", "email", "role", "send_email"}
