"""Lot L2 (ADR 0052) / oto-backend#274 — écrire dans NOTRE annuaire Logto suppose un
sub de NOTRE tenant.

Deux chemins postent un `sub` à notre Logto en supposant qu'il y désigne un
utilisateur : le miroir d'enforcement MFA (`mfa_mirror`) et la lecture de l'email
autoritatif (`oauth_facade.logto_user_primary_email`). Depuis L2, un compte d'un
tenant tiers porte un sub **qualifié** (`slug:sub`) qui n'existe pas dans notre
annuaire.

**L'arbitrage (11/08) est que le MFA obligatoire par org est une capacité du tenant
`oto`** : on l'impose en créant une org MIROIR chez nous et en y inscrivant les
membres — un compte d'un tenant tiers n'y est pas inscriptible, et il n'en a pas
besoin (son émetteur a sa politique). Il n'y a donc rien à router.

Ce qu'il fallait fermer n'est pas un refus mais un **mode d'échec** : `sync_members`
postait TOUS les membres en un appel suivi d'un `raise_for_status()`, donc un seul sub
qualifié faisait échouer la synchro de **toute l'organisation** — sur un chemin
best-effort (`org_store._sync_mfa_mirror` à chaque ajout de membre), c'est-à-dire en
silence. D'où les trois faits vérifiés ici :

1. une org MIXTE se synchronise sans erreur, et seul le membre `oto` atteint le miroir ;
2. le filtrage se CONSTATE (`org.mfa.get` → `members_other_tenant`) — muet, il ferait
   dire « MFA actif » à une org dont des membres n'y sont pas soumis ;
3. un sub qualifié sur le chemin de l'email lève une erreur **nommée**, sans appel
   réseau, au lieu de laisser Logto répondre « utilisateur inconnu » loin de la cause.

Aucun tenant tiers n'a de compte en base : l'état de test est construit
explicitement (registre stubbé), jamais déduit d'une donnée existante.
"""
from __future__ import annotations

import pytest

from oto_mcp import mfa_mirror, oauth_facade, tenancy
from oto_mcp.capabilities import orgs_mfa
from oto_mcp.capabilities._types import ResolvedCtx
from oto_mcp.db import users

ORG = 8
CTX = ResolvedCtx(sub="abc123", org_id=ORG, role="org_admin")

_TIERS = "https://auth.partenaire.test/oidc"
_OTO = "abc123"                  # sub NU = tenant `oto`
_ETRANGER = "partenaire:xyz789"  # sub QUALIFIÉ = tenant tiers


@pytest.fixture
def registre_avec_un_tiers(monkeypatch):
    """Un tenant tiers déclaré — sans lui, tout sub est du tenant `oto` et il n'y a
    rien à distinguer (l'état de la production aujourd'hui)."""
    monkeypatch.setattr(tenancy, "_INSTALLED", tenancy.IssuerRegistry(
        tenancy.build("https://auth.oto.ninja/oidc",
                      tenants=[{"slug": "partenaire", "issuer": _TIERS}])))


@pytest.fixture
def org_mixte(monkeypatch):
    """Un membre du tenant `oto` + un membre d'un tenant tiers. Le second est
    `is_active=False` (son org active est ailleurs) : le roster ne filtre TOUJOURS pas
    sur ce flag — le seul filtre ajouté est le tenant."""
    monkeypatch.setattr(mfa_mirror.org_store, "list_org_members", lambda oid: [
        {"sub": _OTO, "is_active": True},
        {"sub": _ETRANGER, "is_active": False}])


@pytest.fixture
def miroir_provisionne(monkeypatch):
    monkeypatch.setattr(mfa_mirror.org_store, "get_org_mfa",
                        lambda oid: {"require_mfa": True, "logto_org_id": "L1"})


def _reseau_interdit(monkeypatch, module):
    """Aucune préparation d'appel Management API ne doit avoir lieu — ni l'endpoint ni
    le jeton M2M. Sans ça, le refus d'un sub qualifié serait indistinguable d'un « Logto
    indisponible », qui est justement le mode d'échec opaque qu'on ferme."""
    for helper in ("_logto_base", "_mgmt_token"):
        monkeypatch.setattr(module, helper,
                            lambda *a, **k: pytest.fail("appel Logto interdit"))


# ─── 1. l'org MIXTE se synchronise, seul le membre `oto` atteint le miroir ────

def test_org_mixte_se_synchronise_sans_erreur(registre_avec_un_tiers, org_mixte,
                                              miroir_provisionne, monkeypatch):
    monkeypatch.setattr(mfa_mirror, "_list_logto_members", lambda lid: set())
    added, removed = [], []
    monkeypatch.setattr(mfa_mirror, "_add_logto_members",
                        lambda lid, subs: added.extend(subs))
    monkeypatch.setattr(mfa_mirror, "_remove_logto_member",
                        lambda lid, sub: removed.append(sub))

    mfa_mirror.sync_members(ORG)   # ne lève pas — c'est tout le sujet

    assert added == [_OTO], "seul le membre du tenant `oto` est miroitable"
    # …et le membre du tenant tiers n'est pas non plus « retiré » : il n'a jamais
    # été là, et un DELETE sur son sub échouerait pareil.
    assert removed == []


def test_le_filtrage_est_trace(registre_avec_un_tiers, org_mixte, miroir_provisionne,
                               monkeypatch, caplog):
    monkeypatch.setattr(mfa_mirror, "_list_logto_members", lambda lid: {_OTO})
    monkeypatch.setattr(mfa_mirror, "_add_logto_members", lambda lid, subs: None)
    with caplog.at_level("INFO", logger="oto_mcp.mfa_mirror"):
        mfa_mirror.sync_members(ORG)
    assert "non miroité" in caplog.text


def test_sans_tenant_tiers_le_roster_est_inchange(org_mixte, monkeypatch):
    """Registre vide (production d'aujourd'hui) : le `:` d'un sub ne veut rien dire,
    aucun préfixe n'est déclaré → tout le monde est miroité, comme avant L2."""
    monkeypatch.setattr(tenancy, "_INSTALLED", tenancy.IssuerRegistry())
    assert mfa_mirror._member_subs(ORG) == {_OTO, _ETRANGER}
    assert mfa_mirror.foreign_tenant_members(ORG) == set()


def test_les_helpers_de_fil_refusent_un_sub_qualifie(registre_avec_un_tiers,
                                                     monkeypatch):
    """Invariant de fil : même appelés directement (futur call-site qui oublierait le
    filtre), les deux helpers Management API refusent AVANT de partir sur le réseau."""
    _reseau_interdit(monkeypatch, mfa_mirror)
    with pytest.raises(tenancy.ForeignTenantDirectory):
        mfa_mirror._add_logto_members("L1", [_OTO, _ETRANGER])
    with pytest.raises(tenancy.ForeignTenantDirectory):
        mfa_mirror._remove_logto_member("L1", _ETRANGER)


# ─── 2. le compte de non-miroités est exposé là où l'org lit son état MFA ─────

def test_org_mfa_get_expose_les_membres_dun_autre_tenant(registre_avec_un_tiers,
                                                         org_mixte, monkeypatch):
    monkeypatch.setattr(orgs_mfa.org_store, "get_org", lambda oid: {"name": "Acme"})
    monkeypatch.setattr(orgs_mfa.org_store, "get_org_mfa",
                        lambda oid: {"require_mfa": True, "logto_org_id": "L1"})

    out = orgs_mfa._get_org_mfa(CTX, orgs_mfa.GetOrgMfaInput(org_id=ORG))

    assert out == {"org_id": ORG, "require_mfa": True, "provisioned": True,
                   "members_other_tenant": 1}
    # Le champ est DÉCLARÉ au contrat de sortie (sinon `/openapi.json` le tairait).
    assert orgs_mfa.OrgMfaState(**out).members_other_tenant == 1


def test_org_mfa_get_vaut_zero_sans_tenant_tiers(org_mixte, monkeypatch):
    monkeypatch.setattr(tenancy, "_INSTALLED", tenancy.IssuerRegistry())
    monkeypatch.setattr(orgs_mfa.org_store, "get_org", lambda oid: {"name": "Acme"})
    monkeypatch.setattr(orgs_mfa.org_store, "get_org_mfa",
                        lambda oid: {"require_mfa": False, "logto_org_id": None})
    out = orgs_mfa._get_org_mfa(CTX, orgs_mfa.GetOrgMfaInput(org_id=ORG))
    assert out["members_other_tenant"] == 0


# ─── 3. le chemin de l'email : erreur nommée, pas un appel réseau ─────────────

def test_email_primaire_leve_sur_un_sub_qualifie(registre_avec_un_tiers, monkeypatch):
    _reseau_interdit(monkeypatch, oauth_facade)
    with pytest.raises(tenancy.ForeignTenantDirectory) as ei:
        oauth_facade.logto_user_primary_email(_ETRANGER)
    # Le message doit nommer le tenant et dire ce qui manque, pas « utilisateur
    # inconnu » : c'est toute la valeur du garde-fou.
    assert "partenaire" in str(ei.value) and "management" in str(ei.value)


def test_reconcile_ignore_un_compte_dun_tenant_tiers(registre_avec_un_tiers,
                                                     monkeypatch, caplog):
    """L'unique appelant (fusion de comptes) n'a rien à réconcilier : l'email
    autoritatif d'un compte tiers vit dans SON annuaire. On le dit en une ligne, sans
    toucher la base — ce chemin est sur le trajet chaud d'`upsert_user`."""
    _reseau_interdit(monkeypatch, oauth_facade)
    monkeypatch.setattr(users, "_connect", lambda *a, **k: pytest.fail(
        "aucune requête DB ne doit partir"))
    with caplog.at_level("WARNING", logger=users.logger.name):
        assert users.reconcile_tenant_migration(_ETRANGER) is False
    # Le log dit le tenant ET ce qui manque — pas « lookup Logto échoué », qui
    # laisserait croire à une panne.
    assert "ignorée" in caplog.text and "partenaire" in caplog.text
    assert "credential de management" in caplog.text
