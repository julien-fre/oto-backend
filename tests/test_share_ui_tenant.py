"""La page publique d'un projet partagé porte la marque de SON produit, pas la nôtre.

Le destinataire d'un lien de partage n'est **pas authentifié** : il n'a ni compte, ni
session, ni org. Les cinq surfaces déjà tenant-isées ont toutes une identité sous la
main ; celle-ci n'en a aucune. Le tenant s'y résout donc par la DONNÉE — le projet
partagé appartient à une org, qui relève d'un tenant. Cette chaîne est vraie quel que
soit le chemin d'entrée du visiteur, alors que l'hôte ne dit que par où il est arrivé.

Aujourd'hui la page sert notre nom dans son titre, « Partagé via Oto » en pied, un lien
vers `oto.cx`, notre favicon et notre palette — au client d'un partenaire, à qui on n'a
jamais été présenté.

Le patron est celui des emails (`email_brand.marque`), même cas exactement : un rendu
HTML pour un destinataire non authentifié, dont le tenant vient d'un OBJET. Il tranche
déjà les deux bords, et on ne les réinvente pas ici — **absence de tenant ⟹ notre marque
EXPLICITEMENT**, parce que « pas de tenant » est une déclaration et non un silence ;
**tenant sans palette déclarée ⟹ gabarit neutre à SON nom**, jamais nos couleurs.

Invariant : un projet d'une de NOS orgs rend la page d'avant, à l'octet.
"""
from __future__ import annotations

import pytest

from oto_mcp import db, share_ui

_PROJECT = {"id": 5, "name": "Projet démo", "brief_md": "Un projet de démonstration.",
            "mcp_access": "secret", "mcp_expose_datastore": False,
            "mcp_expose_docs": False, "owner_type": "org", "owner_id": 42}
NOS_MARQUES = ("oto.cx", "Oto", "otomata")


@pytest.fixture
def seams(monkeypatch):
    monkeypatch.setattr(db, "list_project_links", lambda pid: [])
    monkeypatch.setattr(db, "list_docs_for_project", lambda pid: [])
    return monkeypatch


@pytest.fixture
def chez_acme(seams):
    """L'org 42 relève du tenant `acme`, qui a déclaré son nom et rien d'autre."""
    from oto_mcp import tenancy
    seams.setattr(db, "org_tenant_slug", lambda org_id: "acme" if org_id == 42 else "oto")
    avant = tenancy.current()
    tenancy.install(tenancy.IssuerRegistry(tenancy.build(
        "https://auth.oto.ninja/oidc",
        tenants=[{"slug": "acme", "name": "Acme", "issuer": "https://auth.acme.test/oidc"}])))
    yield seams
    tenancy.install(avant)


def test_la_page_d_un_partenaire_ne_porte_PAS_notre_nom(chez_acme):
    html, status = share_ui.build_page(_PROJECT, "/", connect_url="https://x.share.acme.test/mcp")
    assert status == 200
    for mot in NOS_MARQUES:
        assert mot not in html, f"« {mot} » servi au client d'un partenaire"


def test_elle_porte_le_SIEN(chez_acme):
    html, _ = share_ui.build_page(_PROJECT, "/", connect_url="https://x.share.acme.test/mcp")
    assert "Acme" in html


def test_elle_ne_sert_pas_NOS_couleurs(chez_acme):
    """Une palette est une marque. Le gabarit neutre porte son nom sans emprunter notre
    dessin — c'est la règle qu'`email_brand` tient depuis le 03/09."""
    html, _ = share_ui.build_page(_PROJECT, "/", connect_url="https://x.share.acme.test/mcp")
    assert "#f0b41e" not in html, "le saffran d'Otomata sur la page d'un partenaire"
    assert "#fefcf5" not in html


def test_un_projet_de_CHEZ_NOUS_est_inchange_a_l_octet(seams, monkeypatch):
    """Le garde-fou d'inertie : sans tenant tiers, l'octet servi est celui d'avant."""
    monkeypatch.setattr(db, "org_tenant_slug", lambda org_id: "oto")
    html, status = share_ui.build_page(_PROJECT, "/", connect_url="https://x.share.oto.cx/mcp")
    assert status == 200
    assert "Partagé via" in html and "oto.cx" in html
    assert "#f0b41e" in html, "notre palette, chez nous"


def test_un_projet_SANS_ORG_rend_NOTRE_marque_explicitement(seams, monkeypatch):
    """« Aucun tenant » est une déclaration, pas un silence : un projet personnel relève
    de la plateforme, et la page doit le dire par le même chemin que les autres — pas en
    tombant dans une branche par défaut."""
    monkeypatch.setattr(db, "org_tenant_slug",
                        lambda org_id: pytest.fail("ne doit pas être interrogé sans org"))
    perso = {**_PROJECT, "owner_type": "user", "owner_id": "u-1"}
    html, status = share_ui.build_page(perso, "/", connect_url="https://x.share.oto.cx/mcp")
    assert status == 200 and "oto.cx" in html


def test_une_page_de_PROSE_suit_la_meme_marque(chez_acme):
    """Le pied et le titre vivent dans le shell, partagé par tous les rendus — un seul
    d'entre eux qui garderait notre nom suffirait à trahir la page."""
    chez_acme.setattr(db, "list_project_links",
                      lambda pid: [{"target_type": "doc", "target_ref": "44", "label": "Notes"}])
    chez_acme.setattr(db, "get_doc_by_id",
                      lambda did: {"id": 44, "title": "Notes", "body_md": "Du texte."})
    html, status = share_ui.build_page({**_PROJECT, "mcp_expose_docs": True}, "/docs/44")
    if status == 200:
        for mot in NOS_MARQUES:
            assert mot not in html, f"« {mot} » dans une page de prose de partenaire"
