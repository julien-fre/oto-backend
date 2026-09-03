"""La palette d'un partenaire se DÉCLARE, elle ne s'écrit pas dans notre code.

Même défaut que l'adresse de son tableau de bord, même remède. Tant que les sept
teintes d'un partenaire vivaient dans `email_brand.MARQUES`, accueillir le suivant
demandait d'éditer ce fichier et de redéployer pour lui — un partenaire qui attend
notre calendrier de livraison pour avoir sa couleur.

Trois choses sont éprouvées ici, et la deuxième est celle qui compte :

1. une palette déclarée est SERVIE, sans passer par notre code ;
2. une palette **incomplète est refusée EN ENTIER**, jamais complétée par la nôtre —
   sinon on fabrique un dessin que personne n'a dessiné, et le défaut ne se voit
   qu'à l'arrivée, chez le destinataire ;
3. le tenant primaire ne se surcharge pas : une ligne en base ne repeint pas oto.
"""
from __future__ import annotations

import pytest

from oto_mcp import email_brand, tenancy

# La charte d'un partenaire fictif : sept teintes, toutes valides.
PALETTE = {
    "nom": "Acme", "site": "acme.test",
    "fond": "#101014", "surface": "#18181d", "encre": "#f5f5f7",
    "discret": "#a0a0ab", "filet": "#2a2a31",
    "bouton_fond": "#f5f5f7", "bouton_encre": "#101014",
}


def _registre(**tenants_brand):
    """Un registre où chaque tenant nommé porte la palette donnée."""
    return tenancy.IssuerRegistry(tenancy.build(
        "https://auth.oto.ninja/oidc",
        tenants=[{"slug": slug, "issuer": f"https://auth.{slug}.test/oidc",
                  "brand": brand}
                 for slug, brand in tenants_brand.items()]))


@pytest.fixture
def pose():
    avant = tenancy.current()
    def _pose(**kw):
        tenancy.install(_registre(**kw))
    yield _pose
    tenancy.install(avant)


# --- ce qui est déclaré est servi -------------------------------------------

def test_une_palette_declaree_est_servie(pose):
    pose(acme=PALETTE)
    m = email_brand.marque("acme")
    assert (m.fond, m.encre, m.bouton_fond) == ("#101014", "#f5f5f7", "#f5f5f7")
    assert (m.nom, m.site) == ("Acme", "acme.test")


def test_la_palette_declaree_prime_sur_celle_du_code(pose):
    """Le point de tout le lot : la base gagne, sinon rien n'a changé.

    Sans cette assertion, tous les autres tests passeraient avec un code qui lit
    encore `MARQUES` en premier et ignore la déclaration."""
    pose(clonecharte=PALETTE)
    email_brand.MARQUES["clonecharte"] = email_brand.MARQUES["oto"]
    try:
        assert email_brand.marque("clonecharte").fond == "#101014", (
            "la palette déclarée par le tenant doit passer AVANT celle du code")
    finally:
        email_brand.MARQUES.pop("clonecharte", None)


# --- ce qui est mal déclaré ne casse rien, et ne se mélange pas --------------

@pytest.mark.parametrize("cassee, pourquoi", [
    ({**PALETTE, "filet": None}, "une teinte manquante"),
    ({**PALETTE, "encre": "rouge"}, "une valeur qui n'est pas une couleur"),
    ({**PALETTE, "fond": "#12"}, "une notation hexadécimale invalide"),
    ({"nom": "Acme"}, "un nom sans aucune teinte"),
])
def test_une_palette_incomplete_est_refusee_EN_ENTIER(pose, cassee, pourquoi):
    """Refusée entière, pas complétée : sept teintes de deux chartes mélangées
    donnent un dessin que personne n'a voulu, et qui ne se voit qu'à l'arrivée."""
    pose(acme=cassee)
    m = email_brand.marque("acme")
    assert m.fond == email_brand._NEUTRE.fond, (
        f"{pourquoi} : la palette doit être ignorée en entier, pas rapiécée")
    assert m.nom == "acme", "le gabarit neutre porte le nom du tenant, pas le nôtre"


def test_une_palette_absente_ne_change_rien(pose):
    """Le cas dominant : aucun tenant ne déclare de palette, tout reste comme avant."""
    pose(acme={})
    assert email_brand.marque("acme").fond == email_brand._NEUTRE.fond


# --- ce qui ne se surcharge pas ---------------------------------------------

def test_le_tenant_primaire_ne_se_repeint_pas_depuis_la_base(pose):
    """Notre charte n'est pas une configuration.

    `build` refuse déjà une ligne au slug `oto`, et `entry_for_slug` rend None pour
    lui : deux gardes indépendantes. On vérifie le RÉSULTAT — oto garde sa charte
    chaude — plutôt que laquelle des deux a tenu."""
    pose(acme=PALETTE)
    assert email_brand.marque("oto").fond == "#faf6ec"
    assert email_brand.marque(None).fond == "#faf6ec"
    assert email_brand.marque("").fond == "#faf6ec"


def test_un_slug_inconnu_du_registre_reste_neutre(pose):
    """Un tenant qui n'est pas au registre n'emprunte la couleur de personne."""
    pose(acme=PALETTE)
    m = email_brand.marque("jamais-vu")
    assert m.fond == email_brand._NEUTRE.fond
    assert m.nom == "jamais-vu"
    assert m.site == "", "on n'invente pas le site d'un produit qu'on ne connaît pas"
