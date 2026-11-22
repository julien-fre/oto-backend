"""Une invitation expire en 24 h (CASA 1.1.2) — et se réémet avec un jeton NEUF.

Le guide de test ADA veut qu'un code d'activation expire vite : 24 h recommandées, 48 h
au plus. Une invitation en est un (son lien suffit à entrer, modèle bearer) ; elle valait
7 jours, son magic-link Logto aussi. Ce qui est figé ici :

- UNE constante (`config.INVITATION_TTL_HOURS`), lue par ses trois porteurs : l'invitation,
  le partage en attente, le magic-link — et le plafond de 48 h est REFUSÉ au-delà ;
- le réglage d'environnement qui la portait à 7 jours n'est plus lu ;
- le mail DIT l'échéance, dans les deux langues ;
- sur une vraie base : l'échéance posée est bien de 24 h, et une invitation échue ne
  bloque pas la suivante, qui porte un jeton neuf pendant que l'ancien n'ouvre plus rien.
"""
from __future__ import annotations

import inspect
import pathlib
from datetime import timedelta

import pytest

from oto_mcp import config, email


def test_une_seule_echeance_de_24_h_plafonnee_a_48():
    assert config.INVITATION_TTL_HOURS == 24
    assert config.INVITATION_TTL_MAX_HOURS == 48
    assert config.heures_d_invitation(24) == "24"
    assert config.heures_d_invitation(48) == "48"
    for hors_bornes in (0, -1, 49, 7 * 24):
        with pytest.raises(ValueError):
            config.heures_d_invitation(hors_bornes)


def test_les_trois_porteurs_lisent_la_constante():
    from oto_mcp import org_store, partage_en_attente
    from oto_mcp.auth import annuaire
    assert (inspect.signature(org_store.create_invitation).parameters["ttl_hours"].default
            == config.INVITATION_TTL_HOURS)
    assert (inspect.signature(partage_en_attente.creer).parameters["heures"].default
            == config.INVITATION_TTL_HOURS)
    for fn in (annuaire.mint_one_time_token, annuaire.magic_url):
        assert (inspect.signature(fn).parameters["expires_in"].default
                == config.INVITATION_TTL_HOURS * 3600), fn.__name__


def test_le_magic_link_est_mint_pour_24_h(monkeypatch):
    """Le corps envoyé à Logto, pas la signature : c'est `expiresIn` qui fait foi."""
    from oto_mcp.auth import annuaire
    import requests
    vus = {}

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
    assert vus["expiresIn"] == 24 * 3600


def test_le_reglage_de_7_jours_n_est_plus_lu():
    racine = pathlib.Path(__file__).resolve().parent.parent / "oto_mcp"
    lecteurs = [p for p in racine.rglob("*.py")
                if "OTO_MCP_INVITE_TTL_DAYS" in p.read_text(encoding="utf-8")
                and p.name != "config.py"]   # config.py le NOMME, retiré
    assert not lecteurs, lecteurs


def _envoye(monkeypatch) -> dict:
    sent: dict = {}
    monkeypatch.setattr(email, "_send", lambda to, subject, html, **k:
                        sent.update(html=html) or True)
    return sent


@pytest.mark.parametrize("locale,phrase", [
    ("en", "this link is valid for 24 hours"),
    (None, "ce lien est valable 24 heures")])
def test_le_mail_d_invitation_dit_l_echeance(monkeypatch, locale, phrase):
    sent = _envoye(monkeypatch)
    email.send_invite_email("a@b.c", "acme", "https://x/invitation/T", "Alice",
                            brand="oto", locale=locale)
    assert phrase in sent["html"]


def test_le_mail_d_un_partage_en_attente_dit_l_echeance_et_lui_seul(monkeypatch):
    sent = _envoye(monkeypatch)
    kw = dict(type_label="projet", name="P", permission="read",
              app_url="https://x/invitation/T", brand="oto")
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


def test_l_invitation_posee_vit_24_h(live):
    from oto_mcp import org_store
    org = org_store.create_org("casa-24h", created_by="u-casa-admin")
    inv_id, _ = org_store.create_invitation(org, "invite@casa.test", "org_member",
                                            "u-casa-admin")
    vie = _vie(inv_id)
    assert timedelta(hours=23, minutes=59) <= vie <= timedelta(hours=24, minutes=1), vie


def test_une_invitation_echue_se_reemet_avec_un_jeton_neuf(live, monkeypatch):
    """Le geste de l'admin après 24 h : réinviter la même adresse. Pas de 409
    `already_invited` (l'échue n'est plus en attente), un jeton NEUF, et l'ancien
    n'ouvre plus rien."""
    from types import SimpleNamespace

    from oto_mcp import db, org_store
    from oto_mcp.capabilities.orgs import invites as oi
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
