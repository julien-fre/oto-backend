"""Une seule règle pour une option payante : le droit déclaré de l'org (ADR 0070 §7).

`has_option` d'une option PAYANTE (`unipile`) = `org_has(org courante)`, quelle que
soit la source du droit (abonnement, don d'org, partenaire). **Le don fait à une
personne n'ouvre plus d'option payante.** Une option non payante (`beta`) reste la
marque du compte ou de l'org.
"""
from __future__ import annotations

from oto_mcp import access


def _wire(monkeypatch, *, droits=(), user_comp=False, org_comp=False, org=7):
    """`droits` = les (org, droit) vivants dans `org_entitlements`."""
    monkeypatch.setattr(access.db_entitlements, "has",
                        lambda oid, droit: (oid, droit) in set(droits))
    monkeypatch.setattr(access.db, "has_option_comp",
                        lambda et, eid, opt: user_comp if et == "user" else org_comp)
    monkeypatch.setattr(access, "current_org", lambda sub: org)


def test_le_droit_declare_de_l_org_ouvre_l_option_payante(monkeypatch):
    _wire(monkeypatch, droits=[(7, "unipile")])
    assert access.has_option("u1", "unipile") is True


def test_le_don_personnel_n_ouvre_plus_l_option_payante(monkeypatch):
    _wire(monkeypatch, user_comp=True)
    assert access.has_option("u1", "unipile") is False


def test_le_don_d_org_brut_ne_suffit_pas_c_est_le_droit_declare_qui_compte(monkeypatch):
    """`option_comps` n'est plus lu pour une option payante : le don d'org y est
    recopié en droit déclaré (`offered`) par la réconciliation du commerce."""
    _wire(monkeypatch, org_comp=True)
    assert access.has_option("u1", "unipile") is False


def test_sans_org_pas_d_option_payante(monkeypatch):
    called = {}
    _wire(monkeypatch, user_comp=True, org=None)
    monkeypatch.setattr(access.db_entitlements, "has",
                        lambda oid, droit: called.update(oid=oid) or True)
    assert access.has_option("u1", "unipile") is False
    assert called == {}, "sans org, aucun droit n'est interrogé"


def test_l_org_explicite_est_celle_qu_on_lit(monkeypatch):
    # fiche admin d'un tiers : l'org EXPLICITE est utilisée (pas current_org).
    _wire(monkeypatch, droits=[(99, "unipile")])
    monkeypatch.setattr(access, "current_org",
                        lambda sub: (_ for _ in ()).throw(AssertionError("ne doit pas être lu")))
    assert access.has_option("tiers", "unipile", org=99) is True


def test_une_option_non_payante_reste_la_marque_du_compte_ou_de_l_org(monkeypatch):
    _wire(monkeypatch, user_comp=True)
    assert access.has_option("u1", "beta") is True
    _wire(monkeypatch, org_comp=True)
    assert access.has_option("u1", "beta") is True
    _wire(monkeypatch, droits=[(7, "beta")])
    assert access.has_option("u1", "beta") is False, (
        "un drapeau de population n'est pas un droit déclaré de l'org")


def test_user_has_option_ne_regarde_que_le_compte(monkeypatch):
    _wire(monkeypatch, org_comp=True, droits=[(7, "beta")])
    assert access.user_has_option("u1", "beta") is False


def test_le_cockpit_d_org_lit_la_meme_regle(monkeypatch):
    from oto_mcp.capabilities.connectors import activation as cap

    _wire(monkeypatch, droits=[(9, "unipile")], user_comp=True)
    assert cap._org_subscribed(9, "unipile") is True
    assert cap._org_subscribed(8, "unipile") is False
