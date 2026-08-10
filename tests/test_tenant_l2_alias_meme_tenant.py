"""Lot L2 (ADR 0052, R3) — l'alias de sub reste borné à UN MÊME tenant.

`sub_aliases` réécrit un ancien sub au 1er login en **fusionnant par email** ; il a
été écrit pour la bascule `auth.oto.zone` → `auth.oto.ninja`, où les deux émetteurs
servaient le même tenant. Avec un second émetteur qui sert un AUTRE tenant, le même
merge par email devient une **fédération d'identités** : l'utilisateur d'un tenant
tiers absorberait le compte oto qui partage son adresse — rôle, orgs, ressources,
coffre. C'est ce que l'ADR 0052 §6 interdit nommément, et le tripwire de
non-collision ne couvre PAS ce chemin (les deux subs sont légitimes chacun de son
côté ; c'est leur rapprochement qui ne l'est pas).

La garde vit dans `migrate_sub` parce que c'est le seul endroit qui écrit
`sub_aliases` : un alias cross-tenant ne peut donc pas naître, et `resolve_sub` ne
peut pas en drainer un.
"""
from __future__ import annotations

import pytest

from oto_mcp import tenancy
from oto_mcp.db import users

_TIERS = "https://auth.tulina.ai/oidc"


@pytest.fixture
def registre_avec_un_tiers(monkeypatch):
    """Un tenant tiers `tulina` déclaré — sinon tout sub est du tenant `oto` et la
    garde n'a rien à distinguer (l'état d'avant ce lot)."""
    registre = tenancy.IssuerRegistry(
        tenancy.build("https://auth.oto.ninja/oidc",
                      tenants=[{"slug": "tulina", "issuer": _TIERS}]))
    monkeypatch.setattr(tenancy, "_INSTALLED", registre)
    return registre


def _db_interdite(monkeypatch):
    """La base ne doit PAS être touchée : le refus se prend avant toute écriture."""
    def _boom(*a, **k):
        raise AssertionError("migrate_sub a ouvert une transaction malgré le refus")

    monkeypatch.setattr(users, "_connect", _boom)


@pytest.mark.parametrize("old_sub, new_sub", [
    ("abc123", "tulina:abc123"),        # le compte oto absorbé par le tiers
    ("tulina:abc123", "abc123"),        # et l'inverse
    ("tulina:abc123", "acme:abc123"),   # entre deux tiers (acme = tenant inconnu)
])
def test_un_alias_cross_tenant_est_refuse(old_sub, new_sub, registre_avec_un_tiers,
                                          monkeypatch, caplog):
    _db_interdite(monkeypatch)
    assert users.migrate_sub(old_sub, new_sub) is False
    assert "REFUSÉE" in caplog.text, "le refus doit être tracé, pas silencieux"


def test_un_merge_dans_le_meme_tenant_reste_possible(registre_avec_un_tiers,
                                                     monkeypatch):
    """La garde ne ferme PAS la bascule d'instance Logto pour laquelle `migrate_sub`
    a été écrit (deux émetteurs, un tenant) : on vérifie qu'elle laisse passer, en
    faisant échouer la transaction sur une sentinelle."""
    class _Sentinelle(RuntimeError):
        pass

    def _passe(*a, **k):
        raise _Sentinelle

    monkeypatch.setattr(users, "_connect", _passe)
    for old_sub, new_sub in (("abc123", "def456"),
                             ("tulina:abc123", "tulina:def456")):
        with pytest.raises(_Sentinelle):
            users.migrate_sub(old_sub, new_sub)


def test_sans_tenant_tiers_declare_rien_ne_change(monkeypatch):
    """Registre vide (l'état de la production aujourd'hui) : tout sub relève du
    tenant `oto`, donc la garde est un no-op — L2 ne modifie aucun comportement
    existant."""
    monkeypatch.setattr(tenancy, "_INSTALLED", tenancy.IssuerRegistry())

    class _Sentinelle(RuntimeError):
        pass

    monkeypatch.setattr(users, "_connect", lambda *a, **k: (_ for _ in ()).throw(
        _Sentinelle()))
    with pytest.raises(_Sentinelle):
        users.migrate_sub("abc123", "tulina:abc123")


def test_le_classement_dun_sub_ne_le_decoupe_pas(registre_avec_un_tiers):
    """Le classement se fait par PRÉFIXE : un `split` fabriquerait deux moitiés dont
    l'une ressemble à un sub sans en être un — ce que le tripwire d'opacité
    interdit par ailleurs."""
    r = registre_avec_un_tiers
    assert r.tenant_of("tulina:abc123") == "tulina"
    assert r.tenant_of("abc123") == tenancy.PRIMARY_SLUG
    # Ni un sub qui COMMENCE par le slug sans le séparateur…
    assert r.tenant_of("tulinaabc123") == tenancy.PRIMARY_SLUG
    # …ni un tenant non déclaré ne prennent le préfixe pour eux.
    assert r.tenant_of("acme:abc123") == tenancy.PRIMARY_SLUG
    assert not r.same_tenant("tulina:abc123", "abc123")
    assert r.same_tenant("tulina:abc123", "tulina:def456")
