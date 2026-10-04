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


# --- l'expéditeur et la langue déclarés (04/10/2026) -------------------------
# Les codes de connexion partaient déjà sous `Tulina <noreply@tulina.ai>` (hook
# Logto du mailer) ; les invitations, elles, sous `OTO_MAIL_FROM` pour tout le monde.

def test_expediteur_et_langue_declares_sont_servis(pose):
    pose(acme={**PALETTE, "expediteur": "Acme <noreply@acme.test>", "langue": "en"})
    m = email_brand.marque("acme")
    assert (m.expediteur, m.langue) == ("Acme <noreply@acme.test>", "en")


@pytest.mark.parametrize("val", [
    "Acme <noreply@acme.test>\r\nBcc: x@evil.test", "pas une adresse",
    "Acme <noreply@acme.test", "<a@b.test> <c@d.test>", 42])
def test_un_expediteur_invalide_est_ignore_pas_la_palette(pose, val):
    pose(acme={**PALETTE, "expediteur": val, "langue": "de"})
    m = email_brand.marque("acme")
    assert (m.expediteur, m.langue) == ("", "")
    assert m.fond == "#101014"


def test_l_invitation_part_sous_l_expediteur_et_la_langue_du_tenant(pose, monkeypatch):
    from oto_mcp import email as E
    from oto_mcp import email_templates
    pose(acme={**PALETTE, "expediteur": "Acme <noreply@acme.test>", "langue": "en"})
    envois = []
    monkeypatch.setattr(E, "_send", lambda to, subject, html, **k:
                        envois.append((subject, k.get("from_email"))) or True)
    assert email_templates.send_invite_email("x@y.test", "Org", "https://u", brand="acme")
    assert envois == [("invitation to join Org on Acme", "Acme <noreply@acme.test>")]
    # La préférence du DESTINATAIRE prime sur la langue du tenant.
    email_templates.send_invite_email("x@y.test", "Org", "https://u", brand="acme", locale="fr")
    assert envois[-1][0].startswith("invitation à rejoindre")


def test_sans_declaration_rien_ne_change(pose, monkeypatch):
    from oto_mcp import email as E
    from oto_mcp import email_templates
    pose(acme=PALETTE)
    envois = []
    monkeypatch.setattr(E, "_send", lambda to, subject, html, **k:
                        envois.append((subject, k.get("from_email"))) or True)
    email_templates.send_invite_email("x@y.test", "Org", "https://u", brand="acme")
    assert envois == [("invitation à rejoindre Org sur Acme", None)]
