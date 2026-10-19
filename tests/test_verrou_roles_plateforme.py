"""Les rôles PLATEFORME ne passent pas un jeton de délégation (`verrou_org.role_plateforme`).

Le verrou d'org bornait le rôle d'ORG du porteur ; `super_admin`/`admin` restaient les
siens sous le jeton d'un travail du runner, et avec eux toute garde plateforme. Un seul
point les borne, `access.scope.role_effectif`, d'où dérivent `get_user_role`,
`is_super_admin`, `is_platform_operator`, `roles.is_platform_admin` et
`is_operator_role` — les règles `PLATFORM_ADMIN`/`SUPER_ADMIN` le lisent.

`OTO_VERROU_ROLES_PLATEFORME` : `enforce` les retire, `report` (défaut) le journalise
sans rien retirer, `off` le coupe. Aucun accès base : `db.get_user` est remplacé.
"""
from __future__ import annotations

import logging

import pytest

from oto_mcp import access, roles, verrou_org
from oto_mcp.capabilities import _authz
from oto_mcp.capabilities._types import AuthzDenied, RawCtx

PORTEUR = "sub-roles-porteur"
TIERS = "sub-roles-tiers"
ENV = "OTO_VERROU_ROLES_PLATEFORME"


@pytest.fixture
def comptes(monkeypatch):
    rangs = {PORTEUR: "super_admin", TIERS: "admin"}
    # La FAÇADE, jamais un sous-module (`tests/test_facades_lecteurs_cible.py`).
    monkeypatch.setattr(access.db, "get_user",
                        lambda sub: {"sub": sub, "role": rangs.get(sub, "member")})
    # Les règles lisent aussi l'org de l'appel : hors sujet ici.
    monkeypatch.setattr(access, "current_org", lambda sub: 42)
    monkeypatch.delenv("OTO_MCP_ADMIN_SUB", raising=False)
    monkeypatch.delenv(ENV, raising=False)
    monkeypatch.delenv("OTO_VERROU_ROLES_PLATEFORME_ORGS", raising=False)
    monkeypatch.delenv("OTO_VERROU_ORG_DELEGATION", raising=False)


@pytest.fixture
def delegue(comptes):
    """Le porteur sous le jeton d'un travail de l'org 42 — posé comme le bord le pose."""
    t = verrou_org.poser(verrou_org.Verrou(sub=PORTEUR, org_id=42, job_id=7))
    yield
    verrou_org.lever(t)


def _raw(sub):
    return RawCtx(sub=sub)


def test_enforce_ramene_le_porteur_a_member(delegue, monkeypatch):
    monkeypatch.setenv(ENV, "enforce")
    assert access.get_user_role(PORTEUR) == "member"
    assert not access.is_super_admin(PORTEUR)
    assert not access.is_platform_operator(PORTEUR)
    assert not roles.is_platform_admin(PORTEUR)
    assert not access.is_operator_role(PORTEUR, "super_admin")
    with pytest.raises(AuthzDenied):
        _authz.SUPER_ADMIN(_raw(PORTEUR))
    with pytest.raises(AuthzDenied):
        _authz.PLATFORM_ADMIN(_raw(PORTEUR))


def test_enforce_ne_touche_pas_un_tiers(delegue, monkeypatch):
    """Lister les opérateurs d'une org, lire le rôle d'un autre compte : exact."""
    monkeypatch.setenv(ENV, "enforce")
    assert access.get_user_role(TIERS) == "admin"
    assert access.is_platform_operator(TIERS)
    assert access.is_operator_role(TIERS, "admin")


def test_enforce_borne_aussi_le_sub_d_amorcage(delegue, monkeypatch):
    """`OTO_MCP_ADMIN_SUB` force super_admin AVANT la borne : il ne lui échappe pas."""
    monkeypatch.setenv(ENV, "enforce")
    monkeypatch.setenv("OTO_MCP_ADMIN_SUB", PORTEUR)
    assert access.get_user_role(PORTEUR) == "member"
    assert access.role_effectif(PORTEUR, None) == "member"
    assert not access.is_operator_role(PORTEUR, None)


def test_report_par_defaut_laisse_passer_et_le_dit_une_fois(delegue, caplog):
    with caplog.at_level(logging.WARNING, logger="oto_mcp.verrou_org"):
        assert access.is_super_admin(PORTEUR)
        assert access.is_platform_operator(PORTEUR)
        assert roles.is_platform_admin(PORTEUR)
        assert _authz.SUPER_ADMIN(_raw(PORTEUR)).sub == PORTEUR
    lignes = [r.getMessage() for r in caplog.records
              if "rôles plateforme (report)" in r.getMessage()]
    assert len(lignes) == 1, lignes   # une ligne par requête, pas une par prédicat
    assert "travail 7" in lignes[0] and "super_admin" in lignes[0]


def test_une_valeur_inconnue_vaut_report(delegue, monkeypatch):
    monkeypatch.setenv(ENV, "strict")
    assert verrou_org.mode_roles() == "report"
    assert access.is_super_admin(PORTEUR)


def test_off_coupe(delegue, monkeypatch, caplog):
    monkeypatch.setenv(ENV, "off")
    with caplog.at_level(logging.WARNING, logger="oto_mcp.verrou_org"):
        assert access.is_super_admin(PORTEUR)
    assert not [r for r in caplog.records if "rôles plateforme" in r.getMessage()]


def test_le_verrou_d_org_coupe_ne_rend_pas_les_roles(delegue, monkeypatch):
    """Deux réglages, deux verrous : `OTO_VERROU_ORG_DELEGATION=off` ne rend pas au
    porteur ses rôles plateforme."""
    monkeypatch.setenv("OTO_VERROU_ORG_DELEGATION", "off")
    monkeypatch.setenv(ENV, "enforce")
    assert not access.is_super_admin(PORTEUR)


def test_sans_jeton_de_delegation_rien_ne_change(comptes, monkeypatch):
    """Un humain (OAuth) ou un jeton d'API ordinaire : aucun verrou posé."""
    monkeypatch.setenv(ENV, "enforce")
    t = verrou_org.poser(None)
    try:
        assert access.get_user_role(PORTEUR) == "super_admin"
        assert _authz.SUPER_ADMIN(_raw(PORTEUR)).sub == PORTEUR
    finally:
        verrou_org.lever(t)


def test_un_member_reste_member(delegue, monkeypatch):
    monkeypatch.setenv(ENV, "enforce")
    assert access.get_user_role("sub-quelconque") == "member"


ENV_ORGS = "OTO_VERROU_ROLES_PLATEFORME_ORGS"


def test_org_exemptee_garde_le_role_sans_le_journaliser(delegue, monkeypatch, caplog):
    """L'org de l'opérateur : ses agents hébergés servent des `oto_admin_*`."""
    monkeypatch.setenv(ENV, "enforce")
    monkeypatch.setenv(ENV_ORGS, "7, 42")
    assert access.get_user_role(PORTEUR) == "super_admin"
    assert _authz.SUPER_ADMIN(_raw(PORTEUR)).sub == PORTEUR
    monkeypatch.setenv(ENV, "report")
    with caplog.at_level(logging.WARNING, logger="oto_mcp.verrou_org"):
        assert access.is_super_admin(PORTEUR)
    assert not [r for r in caplog.records if "rôles plateforme" in r.getMessage()]


def test_une_autre_org_reste_bornee(delegue, monkeypatch):
    monkeypatch.setenv(ENV, "enforce")
    monkeypatch.setenv(ENV_ORGS, "43")
    assert access.get_user_role(PORTEUR) == "member"


def test_un_travail_sans_org_n_est_jamais_exempte(comptes, monkeypatch):
    monkeypatch.setenv(ENV, "enforce")
    monkeypatch.setenv(ENV_ORGS, "42")
    t = verrou_org.poser(verrou_org.Verrou(sub=PORTEUR, org_id=None, job_id=8))
    try:
        assert access.get_user_role(PORTEUR) == "member"
    finally:
        verrou_org.lever(t)


def test_un_element_non_entier_n_exempte_rien(delegue, monkeypatch, caplog):
    monkeypatch.setenv(ENV, "enforce")
    monkeypatch.setenv(ENV_ORGS, "quarante-deux")
    with caplog.at_level(logging.WARNING, logger="oto_mcp.verrou_org"):
        assert verrou_org.orgs_exemptees() == frozenset()
        assert access.get_user_role(PORTEUR) == "member"
    assert any("quarante-deux" in r.getMessage() for r in caplog.records)
