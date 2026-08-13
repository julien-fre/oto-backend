"""Pas de patron ⟹ pas de lien. Et une redirection aboutit toujours.

La correction précédente faisait suivre l'ADRESSE au tenant. Le code du partenaire a
montré que ça ne suffit pas : **aucun de ses chemins ne ressemble aux nôtres**
(`/network/<org>/knowledge/<id>` contre `/docs/<id>`), et il n'a **aucun équivalent**
de nos tableaux. Coller nos chemins sous son domaine aurait fabriqué des liens morts —
pire qu'un lien à notre marque, parce qu'un lien mort ne se diagnostique pas.
"""
from __future__ import annotations

import pytest

from oto_mcp import config, links, tenancy


@pytest.fixture
def registre():
    avant = tenancy.current()
    tenancy.install(tenancy.IssuerRegistry(tenancy.build(
        "https://auth.oto.ninja/oidc",
        tenants=[
            # Un partenaire calqué sur le vrai : ses chemins portent l'org, et il n'a
            # ni vue tableau ni page publique.
            {"slug": "acme", "issuer": "https://auth.acme.test/oidc",
             "dashboard_url": "https://app.acme.test",
             "link_paths": {"doc": "/network/{org}/knowledge/{id}",
                            "connectors": "/network/{org}/connectors",
                            "project": "/network/{org}/projects/{id}"}},
            # Déclaré mais sans rien : ne doit produire aucun lien, jamais les nôtres.
            {"slug": "beta", "issuer": "https://auth.beta.test/oidc"},
        ])))
    yield
    tenancy.install(avant)


# --- ce que le partenaire a --------------------------------------------------

def test_un_type_declare_utilise_SON_chemin(registre):
    assert links.link_for("doc", sub="acme:u", org=7, id=12) == \
        "https://app.acme.test/network/7/knowledge/12"


def test_le_chemin_de_lorg_est_rempli(registre):
    assert links.link_for("connectors", sub="acme:u", org=42) == \
        "https://app.acme.test/network/42/connectors"


# --- ce qu'il n'a PAS --------------------------------------------------------

def test_un_type_absent_ne_rend_AUCUN_lien(registre):
    """LE cœur du lot : son produit n'a pas de vue tableau. On n'écrit rien plutôt
    que d'envoyer chez nous ou vers une page inexistante."""
    assert links.link_for("table", sub="acme:u", id=203) is None
    assert links.link_for("public_doc", sub="acme:u", token="tok") is None


def test_un_tenant_sans_aucun_patron_ne_rend_rien(registre):
    """Déclarer un tenant ne doit pas lui faire hériter de NOS chemins : ils
    mèneraient à des pages qui n'existent pas chez lui."""
    for kind in ("doc", "table", "project", "connectors"):
        assert links.link_for(kind, sub="beta:u", org=1, id=2) is None, kind


def test_un_patron_sans_adresse_ne_mene_nulle_part(registre):
    entries = tenancy.build("https://auth.oto.ninja/oidc",
                            tenants=[{"slug": "gamma", "issuer": "https://g.test/oidc",
                                      "link_paths": {"doc": "/k/{id}"}}])
    avant = tenancy.current()
    tenancy.install(tenancy.IssuerRegistry(entries))
    try:
        assert links.link_for("doc", sub="gamma:u", id=1) is None
    finally:
        tenancy.install(avant)


def test_un_parametre_manquant_annule_le_lien(registre):
    """Une adresse à trous (`/network//knowledge/12`) mène à une page d'erreur en se
    faisant passer pour un lien valide. Mieux vaut pas de lien."""
    assert links.link_for("doc", sub="acme:u", id=12) is None       # `org` absent
    assert links.link_for("doc", sub="acme:u", org=None, id=12) is None


# --- nous, inchangés ---------------------------------------------------------

def test_le_tenant_primaire_garde_nos_chemins(registre):
    assert links.link_for("table", sub="bn01jfy76a5n", id=203) == \
        f"{config.dashboard_url()}/data/203"
    assert links.link_for("public_doc", sub="bn01jfy76a5n", token="tok") == \
        f"{config.dashboard_url()}/p/d/tok"


def test_sans_compte_on_garde_nos_chemins(registre):
    assert links.link_for("table", sub=None, id=1) == f"{config.dashboard_url()}/data/1"


def test_un_type_inconnu_ne_rend_rien(registre):
    assert links.link_for("licorne", sub=None) is None


# --- la redirection, elle, aboutit toujours ----------------------------------

def test_une_redirection_replie_sur_la_notre(registre):
    """On ne peut pas « ne pas rediriger » : au retour d'un consentement, le
    navigateur doit atterrir. Voir notre marque une fois vaut mieux qu'une page
    blanche au milieu d'une connexion."""
    url = links.redirect_for("connector_return", sub="acme:u", connector="zoho")
    assert url == f"{config.dashboard_url()}/connectors?connector=zoho"
    assert url  # jamais None, jamais vide


def test_une_redirection_utilise_le_patron_du_tenant_sil_existe(registre):
    url = links.redirect_for("connectors", sub="acme:u", org=7)
    assert url == "https://app.acme.test/network/7/connectors"


def test_une_redirection_sans_type_connu_mene_au_moins_quelque_part(registre):
    assert links.redirect_for("licorne", sub="acme:u") == config.dashboard_url()


# --- les surfaces --------------------------------------------------------------

def test_le_lien_dun_tableau_disparait_chez_un_partenaire_sans_tableaux(registre):
    from oto_mcp.datastore import _ns_url
    assert _ns_url(203, "acme:u") is None
    assert _ns_url(203, "bn01jfy76a5n") == f"{config.dashboard_url()}/data/203"


def test_le_lien_public_dune_page_disparait_de_meme(registre):
    from oto_mcp.capabilities.docs import _public_doc_url
    assert _public_doc_url("tok", "acme:u") is None
    assert _public_doc_url("tok", "bn01jfy76a5n") == f"{config.dashboard_url()}/p/d/tok"


def test_des_patrons_illisibles_valent_aucun_lien():
    """Une valeur qu'on n'a pas su lire ne doit jamais devenir un lien construit."""
    for valeur in ("pas du json", 42, [], None, {"doc": ""}, {"doc": 12}):
        entries = tenancy.build("https://auth.oto.ninja/oidc",
                                tenants=[{"slug": "delta", "issuer": "https://d.test/oidc",
                                          "dashboard_url": "https://app.d.test",
                                          "link_paths": valeur}])
        avant = tenancy.current()
        tenancy.install(tenancy.IssuerRegistry(entries))
        try:
            assert links.link_for("doc", sub="delta:u", id=1) is None, valeur
        finally:
            tenancy.install(avant)
