"""L'échéance d'une invitation est un RÉGLAGE D'INSTANCE (CASA 1.1.2) — le tronc reste à
7 jours ; une instance qui déclare `OTO_INVITATION_TTL_HOURS` la raccourcit.

Le guide de test ADA veut qu'un code d'activation expire vite : 24 h recommandées, 48 h
au plus. Une invitation en est un (son lien suffit à entrer, modèle bearer). Ce qui
change le produit pour toutes les instances passe par un réglage, pas en dur dans le
tronc : ce qui est figé ici —

- sans déclaration, le comportement du tronc : 7 jours (`OTO_MCP_INVITE_TTL_DAYS`, lu
  comme avant), pour ses trois porteurs (l'invitation, le partage en attente, le
  magic-link), et un mail inchangé (aucune phrase d'échéance) ;
- `OTO_INVITATION_TTL_HOURS` posé : il prime, il est borné à 1..48 (au-delà ou illisible :
  refus de démarrer, jamais raboté), le magic-link le suit et le mail DIT l'échéance ;
- le réglage est lu à CHAQUE usage, jamais figé à l'import ;
- sur une vraie base : l'échéance posée est celle du réglage, et une invitation échue ne
  bloque pas la suivante, qui porte un jeton neuf pendant que l'ancien n'ouvre plus rien.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from oto_mcp import config, email

_HEURES = "OTO_INVITATION_TTL_HOURS"
_JOURS = "OTO_MCP_INVITE_TTL_DAYS"


@pytest.fixture(autouse=True)
def _aucun_reglage(monkeypatch):
    """Chaque test part d'une instance qui ne déclare rien."""
    monkeypatch.delenv(_HEURES, raising=False)
    monkeypatch.delenv(_JOURS, raising=False)


# ── Le résolveur ─────────────────────────────────────────────────────────────

def test_sans_reglage_le_tronc_reste_a_7_jours():
    assert config.invitation_ttl_declaree() is None
    assert config.invitation_ttl_hours() == 7 * 24
    assert config.heures_d_invitation() == "168"
    config.verifier_reglages_invitation()


def test_le_reglage_historique_en_jours_est_toujours_lu(monkeypatch):
    monkeypatch.setenv(_JOURS, "3")
    assert config.invitation_ttl_hours() == 72
    assert config.invitation_ttl_declaree() is None   # pas une échéance « courte »


@pytest.mark.parametrize("brut,heures", [("24", 24), (" 1 ", 1), ("48", 48)])
def test_l_echeance_declaree_prime(monkeypatch, brut, heures):
    monkeypatch.setenv(_JOURS, "7")
    monkeypatch.setenv(_HEURES, brut)
    assert config.invitation_ttl_declaree() == heures
    assert config.invitation_ttl_hours() == heures
    assert config.heures_d_invitation() == str(heures)


@pytest.mark.parametrize("brut", ["0", "-1", "49", "168", "24h", "un jour"])
def test_une_echeance_declaree_hors_bornes_refuse_le_demarrage(monkeypatch, brut):
    monkeypatch.setenv(_HEURES, brut)
    with pytest.raises(ValueError):
        config.verifier_reglages_invitation()


@pytest.mark.parametrize("brut", ["0", "-2", "sept"])
def test_un_reglage_en_jours_illisible_refuse_le_demarrage(monkeypatch, brut):
    monkeypatch.setenv(_JOURS, brut)
    with pytest.raises(ValueError):
        config.verifier_reglages_invitation()


def test_une_echeance_explicite_reste_permise_au_dela_de_48_h():
    """Le plafond borne le RÉGLAGE déclaré, pas un appelant qui passe la sienne."""
    assert config.heures_d_invitation(168) == "168"
    for nulle in (0, -1):
        with pytest.raises(ValueError):
            config.heures_d_invitation(nulle)


def test_les_trois_porteurs_lisent_le_reglage_a_l_usage():
    """Aucun défaut figé à l'import : un défaut `None` résolu à chaque appel."""
    import inspect

    from oto_mcp import org_store, partage_en_attente
    from oto_mcp.auth import annuaire
    assert inspect.signature(org_store.create_invitation).parameters["ttl_hours"].default is None
    assert inspect.signature(partage_en_attente.creer).parameters["heures"].default is None
    for fn in (annuaire.mint_one_time_token, annuaire.magic_url):
        assert inspect.signature(fn).parameters["expires_in"].default is None, fn.__name__


# ── Le magic-link ────────────────────────────────────────────────────────────

def _mint(monkeypatch) -> dict:
    """Le corps envoyé à Logto, pas la signature : c'est `expiresIn` qui fait foi."""
    from oto_mcp.auth import annuaire
    import requests
    vus: dict = {}

    class _Rep:
        def raise_for_status(self):
            pass

        def json(self):
            return {"token": "OTT"}

    monkeypatch.setattr(annuaire, "_logto_base", lambda: "https://annuaire.test")
    monkeypatch.setattr(annuaire, "_mgmt_token", lambda: "m2m")
    monkeypatch.setattr(requests, "post", lambda url, json, **kw: vus.update(json) or _Rep())
    assert annuaire.magic_url("https://front.test/invitation/T", "a@b.test").startswith(
        "https://front.test/invitation/T?otl=OTT")
    return vus


def test_sans_reglage_le_magic_link_vit_7_jours(monkeypatch):
    assert _mint(monkeypatch)["expiresIn"] == 7 * 24 * 3600


def test_le_magic_link_suit_l_echeance_declaree(monkeypatch):
    monkeypatch.setenv(_HEURES, "24")
    assert _mint(monkeypatch)["expiresIn"] == 24 * 3600


# ── Le mail ──────────────────────────────────────────────────────────────────

def _envoye(monkeypatch) -> dict:
    sent: dict = {}
    monkeypatch.setattr(email, "_send", lambda to, subject, html, **k:
                        sent.update(html=html) or True)
    return sent


@pytest.mark.parametrize("locale", ["en", None])
def test_sans_reglage_le_mail_d_invitation_est_inchange(monkeypatch, locale):
    sent = _envoye(monkeypatch)
    email.send_invite_email("a@b.c", "acme", "https://x/invitation/T", "Alice",
                            brand="oto", locale=locale)
    assert "valid for" not in sent["html"] and "valable" not in sent["html"]


@pytest.mark.parametrize("locale,phrase", [
    ("en", "this link is valid for 24 hours"),
    (None, "ce lien est valable 24 heures")])
def test_sous_reglage_le_mail_d_invitation_dit_l_echeance(monkeypatch, locale, phrase):
    monkeypatch.setenv(_HEURES, "24")
    sent = _envoye(monkeypatch)
    email.send_invite_email("a@b.c", "acme", "https://x/invitation/T", "Alice",
                            brand="oto", locale=locale)
    assert phrase in sent["html"]


def test_le_mail_d_un_partage_en_attente_dit_l_echeance_et_lui_seul(monkeypatch):
    kw = dict(type_label="projet", name="P", permission="read",
              app_url="https://x/invitation/T", brand="oto")
    sent = _envoye(monkeypatch)
    email.send_resource_shared_email("a@b.c", lien_d_invitation=True, **kw)
    assert "valable" not in sent["html"]              # sans réglage : inchangé
    monkeypatch.setenv(_HEURES, "24")
    email.send_resource_shared_email("a@b.c", lien_d_invitation=True, **kw)
    assert "ce lien est valable 24 heures" in sent["html"]
    email.send_resource_shared_email("a@b.c", **kw)   # partage à un compte existant
    assert "valable" not in sent["html"]


# ── Vrai PostgreSQL ──────────────────────────────────────────────────────────

def _vie(inv_id: int):
    from oto_mcp.db import _connect
    with _connect() as conn:
        return conn.execute(
            "SELECT expires_at - created_at AS vie FROM org_invitations WHERE id = %s",
            (inv_id,)).fetchone()["vie"]


def _echoir(inv_id: int) -> None:
    from oto_mcp.db import _connect
    with _connect() as conn:
        conn.execute("UPDATE org_invitations SET expires_at = NOW() - interval '1 minute' "
                     "WHERE id = %s", (inv_id,))


def _environ(vie, heures: int) -> bool:
    return timedelta(hours=heures, minutes=-1) <= vie <= timedelta(hours=heures, minutes=1)


def test_sans_reglage_l_invitation_posee_vit_7_jours(live):
    from oto_mcp import org_store
    org = org_store.create_org("casa-7j", created_by="u-casa-admin")
    inv_id, _ = org_store.create_invitation(org, "invite7@casa.test", "org_member",
                                            "u-casa-admin")
    assert _environ(_vie(inv_id), 7 * 24), _vie(inv_id)


def test_sous_reglage_l_invitation_posee_vit_24_h(live, monkeypatch):
    from oto_mcp import org_store
    monkeypatch.setenv(_HEURES, "24")
    org = org_store.create_org("casa-24h", created_by="u-casa-admin")
    inv_id, _ = org_store.create_invitation(org, "invite@casa.test", "org_member",
                                            "u-casa-admin")
    assert _environ(_vie(inv_id), 24), _vie(inv_id)


def test_une_invitation_echue_se_reemet_avec_un_jeton_neuf(live, monkeypatch):
    """Le geste de l'admin après l'échéance : réinviter la même adresse. Pas de 409
    `already_invited` (l'échue n'est plus en attente), un jeton NEUF, et l'ancien
    n'ouvre plus rien."""
    from types import SimpleNamespace

    from oto_mcp import db, org_store
    from oto_mcp.capabilities.orgs import invites as oi
    monkeypatch.setenv(_HEURES, "24")
    monkeypatch.setenv("OTO_INVITE_BASE_URL", "https://front.casa.test")
    monkeypatch.setattr(oi.annuaire, "magic_url", lambda url, email, **_: url)
    monkeypatch.setattr(oi.email_mod, "send_invite_email", lambda *a, **k: True)
    db.upsert_user("u-casa-hote", email="hote@casa.test")
    org = org_store.create_org("casa-reemission", created_by="u-casa-hote")
    org_store.add_org_member(org, "u-casa-hote", "org_admin")
    ctx = SimpleNamespace(sub="u-casa-hote")
    entree = oi.InviteCreateInput(org_id=org, email="neuf@casa.test", send_email=False)

    premier = oi._invite_create(ctx, entree)["invite_url"].rsplit("/", 1)[-1]
    # Encore valide : la seconde émission est un doublon, refusé (#622).
    with pytest.raises(oi.AuthzDenied) as e:
        oi._invite_create(ctx, entree)
    assert e.value.code == "already_invited"

    _echoir(org_store.find_pending_invitation(org, "neuf@casa.test")["id"])
    assert org_store.find_pending_invitation(org, "neuf@casa.test") is None

    second = oi._invite_create(ctx, entree)["invite_url"].rsplit("/", 1)[-1]
    assert second != premier and second.startswith("inv_")
    assert org_store.get_invitation_by_token(premier) is None
    assert org_store.get_invitation_by_token(second) is not None
    db.upsert_user("u-casa-neuf", email="neuf@casa.test")
    assert org_store.accept_invitation(premier, "u-casa-neuf") is None
