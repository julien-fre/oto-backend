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
# Les codes de connexion d'un tenant partaient déjà sous SON expéditeur (hook Logto
# du mailer) ; les invitations, elles, sous `OTO_MAIL_FROM` pour tout le monde.

def test_expediteur_et_langue_declares_sont_servis(pose):
    pose(acme={**PALETTE, "expediteur": "Acme <noreply@acme.test>", "langue": "en"})
    m = email_brand.marque("acme")
    assert (m.expediteur, m.langue) == ("Acme <noreply@acme.test>", "en")


@pytest.mark.parametrize("val", [
    "Acme <noreply@acme.test>\r\nBcc: x@evil.test", "pas une adresse",
    "Acme <noreply@acme.test", "<a@b.test> <c@d.test>", 42,
    "Acme, Inc <noreply@acme.test>", "Acme; Bcc <noreply@acme.test>"])
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


# --- un seul envoi par marque (04/10/2026) ------------------------------------

_GABARITS = {
    "send_invite_email": lambda T: T.send_invite_email("x@y.test", "Org", "https://u",
                                                       brand="acme"),
    "send_resource_shared_email": lambda T: T.send_resource_shared_email(
        "x@y.test", type_label="doc", name="n", permission="read", app_url="https://u",
        brand="acme"),
    "send_resource_transferred_email": lambda T: T.send_resource_transferred_email(
        "x@y.test", type_label="doc", name="n", app_url="https://u", brand="acme"),
    "send_signal_digest_email": lambda T: T.send_signal_digest_email(
        "x@y.test", items=[{"status": "open", "target": "t", "body": "b"}], brand="acme"),
    "send_unipile_fin_de_droit_email": lambda T: T.send_unipile_fin_de_droit_email(
        "x@y.test", org_name="Org", canaux=["LINKEDIN"],
        supprime_le=__import__("datetime").datetime(2026, 11, 1), app_url="https://u",
        brand="acme"),
}


@pytest.mark.parametrize("gabarit", sorted(_GABARITS))
def test_chaque_gabarit_part_sous_l_expediteur_de_sa_marque_html_en_position(
        pose, monkeypatch, gabarit):
    """Les cinq gabarits passent par `_envoyer` : l'expéditeur du tenant, et le HTML
    en TROISIÈME position, là où `_send` le déclare."""
    from oto_mcp import email as E
    from oto_mcp import email_templates as T
    pose(acme={**PALETTE, "expediteur": "Acme <noreply@acme.test>"})
    envois = []
    monkeypatch.setattr(E, "_send", lambda *a, **kw: envois.append((a, kw)) or True)
    assert _GABARITS[gabarit](T)
    (a, kw), = envois
    assert len(a) == 3 and a[2].lstrip().lower().startswith("<!doctype")
    assert kw == {"from_email": "Acme <noreply@acme.test>"}


def test_aucun_gabarit_n_appelle_le_relais_en_direct():
    """Le choke-point est `_envoyer` : un gabarit qui rappellerait `_email._send`
    lui-même oublierait l'expéditeur du tenant."""
    import inspect
    from oto_mcp import email_templates as T
    code = inspect.getsource(T).replace(T.__doc__, "")
    assert code.count("_email._send(") == 1, "seul `_envoyer` appelle `_email._send`"


def test_un_403_du_relais_est_une_erreur_signalee_pas_un_alea(monkeypatch, caplog):
    """L'expéditeur d'un tenant hors de l'allowlist du relais : rien ne part, et ça se
    VOIT — erreur au journal et signal au suivi d'erreurs, pas l'avertissement d'un
    timeout."""
    import logging
    import sys
    import types
    from oto_mcp import email as E

    class _Resp:
        status_code = 403
        text = "from domain not allowed"
    monkeypatch.setitem(sys.modules, "httpx", types.SimpleNamespace(
        post=lambda url, headers=None, json=None, timeout=None: _Resp()))
    signaux = []

    class _Scope:
        def __enter__(self):
            return types.SimpleNamespace(set_tag=lambda k, v: signaux.append((k, v)))

        def __exit__(self, *a):
            return False
    monkeypatch.setitem(sys.modules, "sentry_sdk", types.SimpleNamespace(
        new_scope=_Scope,
        capture_message=lambda msg, level=None: signaux.append((level, msg))))
    monkeypatch.setenv("OTO_MAILER_SEND_BEARER", "tok")
    monkeypatch.setenv("OTO_MAILER_URL", "https://relais.test/send")
    with caplog.at_level(logging.ERROR, logger="oto_mcp.email"):
        assert E._send("x@y.test", "s", "<p>x</p>", from_email="Acme <noreply@acme.test>") is False
    assert any("refusé (403)" in r.getMessage() and r.levelno == logging.ERROR
               for r in caplog.records)
    assert ("oto.mailer", "expediteur_refuse") in signaux
    assert any(niveau == "error" and "noreply@acme.test" in msg for niveau, msg in signaux
               if niveau == "error")


# --- sans marque, celle de l'INSTANCE (08/10/2026) ---------------------------
# Les gabarits et leurs appelants repliaient sur le littéral `"oto"`. Sur une instance
# dont le primaire est un autre slug, `"oto"` est un tiers inconnu : chaque invitation
# d'une org sans `front_brand` partait au gabarit neutre « oto ».

@pytest.mark.parametrize("gabarit", sorted(_GABARITS))
def test_sans_marque_les_gabarits_portent_celle_de_l_instance(gabarit, monkeypatch):
    from oto_mcp import email as E
    from oto_mcp import email_templates
    monkeypatch.setenv("OTO_TENANT_PRIMAIRE_SLUG", "acme")
    monkeypatch.setenv("OTO_BRAND_NAME", "Acme")
    monkeypatch.setenv("OTO_BRAND_SITE", "acme.test")
    envois = []
    monkeypatch.setattr(E, "_send", lambda to, subject, html, **k:
                        envois.append(subject + html) or True)

    class _SansMarque:
        def __getattr__(self, nom):
            f = getattr(email_templates, nom)
            return lambda *a, **k: f(*a, **{c: v for c, v in k.items() if c != "brand"})

    assert _GABARITS[gabarit](_SansMarque())
    assert "Acme" in envois[0]
    assert ">oto<" not in envois[0] and " oto." not in envois[0]
