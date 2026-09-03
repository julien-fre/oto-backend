"""Qui peut mettre un compte en pause — la garde éprouvée sur chaque cas limite.

C'est l'axe le plus facile à rater, parce qu'une règle d'autorisation qui ne garde
rien a exactement la même forme qu'une règle qui garde : elle prend un contexte et
une entrée, et elle ne lève pas. Trois pièges précis sont donc épinglés ici :

1. **Le PÉRIMÈTRE ne vient pas de l'appelant** (ADR 0066-R3). `TENANT_ADMIN_OF` prend
   un slug dans la requête ; ici le tenant est DÉRIVÉ de la cible. Un admin de tenant
   qui vise un compte d'ailleurs doit être refusé, et il n'a aucun champ à remplir
   pour prétendre le contraire.
2. **Un compte nu est hors d'atteinte.** Le tenant primaire n'a pas d'admin de
   tenant : c'est ce qui empêche un partenaire d'agir sur un compte de la plateforme.
3. **Le rôle est NOMMÉ, pas déduit.** Appartenir au bon tenant ne suffit pas — il faut
   être déclaré dans `tenant_admins`. Sans cette moitié, tout compte du partenaire
   pourrait neutraliser ses collègues.

⚠️ La table `tenant_admins` est vide en production à ce jour. Ça ne change rien à ce
qui est testé ici : c'est un fait de peuplement, et nommer un admin de tenant est un
geste qui existe déjà (`admin.tenant_admins.add`, super admin).
"""
from __future__ import annotations

import pytest

from oto_mcp import access, tenancy
from oto_mcp.capabilities import _authz, account_suspension as cap
from oto_mcp.capabilities._types import AuthzDenied, RawCtx, ResolvedCtx
from oto_mcp.capabilities.registry import CAPABILITIES

PILOTE = "pilote"
ADMIN_T = f"{PILOTE}:admin"           # admin déclaré du tenant `pilote`
MEMBRE_T = f"{PILOTE}:membre"         # compte du même tenant, sans le rôle
CIBLE_T = f"{PILOTE}:cible"           # le compte à mettre en pause
CIBLE_AUTRE = "autre:cible"           # un compte d'un AUTRE tenant
CIBLE_NUE = "usr_nu"                  # un compte de la plateforme


def _cap():
    return next(c for c in CAPABILITIES if c.key == "admin.account")


def _inp(target, op="suspend", reason="motif"):
    return cap.AccountSuspensionInput(op=op, target=target, reason=reason)


@pytest.fixture
def monde(monkeypatch):
    monkeypatch.setattr(tenancy, "_INSTALLED", tenancy.IssuerRegistry(tenancy.build(
        "https://auth.oto.ninja/oidc",
        tenants=[{"slug": PILOTE, "issuer": "https://auth.pilote.test/oidc"},
                 {"slug": "autre", "issuer": "https://auth.autre.test/oidc"}])),
        raising=False)
    monkeypatch.setattr(access, "is_super_admin", lambda sub: sub == "operateur")
    monkeypatch.setattr(access, "is_platform_operator", lambda sub: sub == "operateur")
    monkeypatch.setattr(access, "current_org", lambda sub: None)
    monkeypatch.setattr(access, "get_user_role", lambda sub: "member")
    monkeypatch.setattr(_authz.db, "is_tenant_admin",
                        lambda slug, sub: (slug, sub) == (PILOTE, ADMIN_T))


def _autorise(appelant, cible, op="suspend"):
    return _cap().authz(RawCtx(sub=appelant), _inp(cible, op=op))


# ── Ce qui passe ────────────────────────────────────────────────────────────

def test_le_super_admin_de_plateforme_passe_sur_nimporte_qui(monde):
    for cible in (CIBLE_T, CIBLE_AUTRE, CIBLE_NUE):
        assert _autorise("operateur", cible).sub == "operateur"


def test_ladmin_de_tenant_passe_sur_un_compte_de_SON_tenant(monde):
    """Le cœur de la demande : le partenaire opère ses propres comptes SANS aucun
    privilège de plateforme. `is_super_admin` et `is_platform_operator` rendent False
    pour lui dans ce monde — s'il passe, c'est par le rôle de tenant et par lui seul."""
    assert _autorise(ADMIN_T, CIBLE_T).sub == ADMIN_T
    assert _autorise(ADMIN_T, CIBLE_T, op="resume").sub == ADMIN_T


# ── Ce qui ne passe pas ─────────────────────────────────────────────────────

def test_ladmin_de_tenant_est_refuse_sur_un_compte_dun_AUTRE_tenant(monde, monkeypatch):
    """⚠️ Le rôle est déclaré ICI sur le tenant de la CIBLE, et l'appel doit quand
    même être refusé.

    Sans cette précaution, le test serait vert pour la mauvaise raison : le refus
    tomberait sur « il n'est pas admin de ce tenant-là », et la branche qui compare
    le tenant de l'appelant à celui de la cible pourrait disparaître sans que rien ne
    rougisse. Le scénario n'est pas gratuit — le chemin déclaré refuse déjà de nommer
    un admin hors de son tenant, mais une ligne posée à la main en base ne passe par
    aucun chemin, et c'est précisément contre ça que cette branche existe."""
    monkeypatch.setattr(_authz.db, "is_tenant_admin",
                        lambda slug, sub: sub == ADMIN_T)   # admin PARTOUT en base
    with pytest.raises(AuthzDenied) as refus:
        _autorise(ADMIN_T, CIBLE_AUTRE)
    assert refus.value.status == 403


def test_le_tenant_primaire_na_pas_dadmin_de_tenant(monde, monkeypatch):
    """La branche qui ferme le trou le plus large du mécanisme.

    Un sub sans préfixe relève du tenant primaire. Si une ligne `tenant_admins('oto',
    …)` existait — rien en base ne l'interdit, `tenants` porte bien la ligne `oto` —
    son porteur pourrait neutraliser TOUS les comptes de la plateforme sans être
    super admin. Le refus sur `slug == PRIMARY_SLUG` est la seule chose qui s'y
    oppose : ici l'appelant est nu, déclaré admin du tenant `oto`, et il vise un
    compte nu — les deux autres branches du refus sont donc fausses."""
    monkeypatch.setattr(_authz.db, "is_tenant_admin",
                        lambda slug, sub: (slug, sub) == ("oto", "usr_declare"))
    with pytest.raises(AuthzDenied) as refus:
        _autorise("usr_declare", CIBLE_NUE)
    assert refus.value.status == 403


def test_ladmin_dun_tenant_tiers_natteint_pas_un_compte_de_la_plateforme(monde):
    """Le cas courant, celui du partenaire — vert par plusieurs branches à la fois, et
    c'est très bien : il dit ce qui est SERVI, pas quelle ligne le produit."""
    with pytest.raises(AuthzDenied) as refus:
        _autorise(ADMIN_T, CIBLE_NUE)
    assert refus.value.status == 403


def test_un_membre_du_bon_tenant_SANS_le_role_est_refuse(monde):
    """Appartenir au tenant ne suffit pas : le rôle est nommé dans `tenant_admins`.
    Sans cette moitié, tout compte du partenaire pourrait neutraliser ses collègues."""
    with pytest.raises(AuthzDenied) as refus:
        _autorise(MEMBRE_T, CIBLE_T)
    assert refus.value.status == 403


def test_un_compte_ordinaire_est_refuse(monde):
    with pytest.raises(AuthzDenied):
        _autorise(CIBLE_NUE, CIBLE_T)


def test_le_perimetre_ne_se_declare_pas_dans_la_requete(monde):
    """Le contrôle qui distingue cette règle de sa sœur `TENANT_ADMIN_OF`.

    L'`Input` ne porte AUCUN champ de tenant : il n'y a donc rien à remplir pour
    prétendre à un périmètre. Si un successeur en ajoutait un et le lisait, la garde
    se mettrait à vérifier que l'appelant a écrit deux fois le même mot."""
    champs = set(cap.AccountSuspensionInput.model_fields)
    assert champs == {"op", "target", "reason"}


# ── Les refus du handler ────────────────────────────────────────────────────

@pytest.fixture
def handler(monkeypatch):
    etat = {"users": {CIBLE_T: {"sub": CIBLE_T}}, "pause": None}
    monkeypatch.setattr(cap.db, "get_user", lambda s: etat["users"].get(s))
    monkeypatch.setattr(cap.db, "suspend_account",
                        lambda sub, by, reason: {"sub": sub, "suspended_at": "2026-09-03",
                                                 "suspended_by": by,
                                                 "suspended_reason": reason})
    monkeypatch.setattr(cap.db, "resume_account", lambda sub: True)
    return etat


def _ctx(sub=ADMIN_T):
    return ResolvedCtx(sub=sub, role="member")


def test_une_pause_sans_motif_est_refusee(handler):
    """Le motif est exigé : une pause sans motif écrit devient, six mois plus tard,
    une pause que personne n'ose lever et que personne ne sait expliquer."""
    with pytest.raises(AuthzDenied) as refus:
        cap._account(_ctx(), _inp(CIBLE_T, reason="   "))
    assert (refus.value.status, refus.value.code) == (400, "missing_reason")


def test_on_ne_met_pas_son_propre_compte_en_pause(handler):
    with pytest.raises(AuthzDenied) as refus:
        cap._account(_ctx(CIBLE_T), _inp(CIBLE_T))
    assert (refus.value.status, refus.value.code) == (409, "self_suspend")


def test_un_compte_inconnu_rend_404(handler):
    with pytest.raises(AuthzDenied) as refus:
        cap._account(_ctx(), _inp("pilote:fantome"))
    assert (refus.value.status, refus.value.code) == (404, "unknown_user")


def test_le_reveil_est_le_seul_chemin_de_retour(handler):
    vue = cap._account(_ctx(), _inp(CIBLE_T, op="resume"))
    assert vue["suspended"] is False and vue["changed"] is True


# ── Le contrat servi ────────────────────────────────────────────────────────

def test_la_forme_rendue_est_EXACTEMENT_celle_qui_est_declaree(handler):
    """`Output` DÉCRIT, il ne valide pas : une déclaration fausse ne se voit jamais à
    l'exécution, seulement chez l'intégrateur — qui écrit du code lisant une clé qui
    rend `undefined` en production. Égalité stricte, dans les deux sens."""
    for inp in (_inp(CIBLE_T), _inp(CIBLE_T, op="resume")):
        rendu = cap._account(_ctx(), inp)
        assert set(rendu) == set(cap.AccountSuspensionOut.model_fields)


def test_la_description_annonce_les_deux_verbes_et_ce_quils_changent():
    """Une capacité qu'aucun texte n'annonce n'existe pas pour un agent — et une
    description qui promet un `op` absent est pire encore que l'inverse, parce que
    rien ne dit au lecteur que c'est le texte qui ment."""
    d = _cap().description
    ops = set(cap.AccountSuspensionInput.model_fields["op"].annotation.__args__)
    for op in ops:
        assert f"op={op}" in d, f"`op={op}` existe mais n'est pas annoncé"
    for annonce in set(mot.split()[0] for mot in d.split("op=")[1:]):
        assert annonce.strip("( ") in ops, f"la description promet `op={annonce}`, absent"
    # …et elle dit l'EFFET, pas seulement le geste : ce qui s'arrête, et ce qui reste.
    assert "next request" in d and "NOTHING is deleted" in d


def test_le_geste_est_atteignable_depuis_un_agent():
    """⚠️ Le défaut qui a rendu inerte la capacité bornée existante : elle n'était
    servie que côté web, alors que le partenaire ne travaille QUE par MCP. Une
    capacité bornée inatteignable depuis la face qu'on utilise ne rend rien."""
    assert _cap().mcp == "oto_admin_account"
    assert _cap().rest is not None
