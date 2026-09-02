"""Le DESSIN d'un email suit la marque du DESTINATAIRE — comme le texte depuis
7d10a798, et par le même paramètre `brand`.

La faute réparée n'était pas une faute de goût : un client de Tulina lisait
« sur tulina » écrit en brun otomata, sur un gabarit qui ne ressemblait à rien de ce
qu'il voyait en cliquant. Le mot suivait le tenant, la couleur non — donc il y avait
un endroit du système où les deux pouvaient diverger, et c'est ça qu'on ferme ici.

Ces tests décrivent des PROPRIÉTÉS du HTML servi, jamais un golden : figer un octet
gèlerait la charte et rendrait rouge tout changement de gabarit, y compris juste.
Deux familles :

- **la marque** — la palette servie est celle du destinataire, et jamais celle d'un
  autre produit (le cas qui blesse : le nom d'oto chez un partenaire) ;
- **le client mail** — ce qu'un `<div>` avec un `max-width` ne survivait pas :
  tables, styles en ligne uniquement, `color-scheme` déclaré, ligne d'aperçu.
"""
from __future__ import annotations

import re

import pytest

from oto_mcp import email as E
from oto_mcp import email_brand as B

_URL = "https://exemple.test/x"


@pytest.fixture
def envoye(monkeypatch):
    """Capture le `html` du dernier `_send` sans jamais sortir sur le réseau."""
    boite: dict = {}
    monkeypatch.setattr(E, "_send",
                        lambda to, subject, html, **k: boite.update(
                            to=to, subject=subject, html=html) or True)
    return boite


def _tous_les_envois(brand: str, locale: str | None = None) -> list:
    """Un appel par gabarit transactionnel, tous paramètres nominaux — la liste que
    parcourt chaque propriété ci-dessous. Elle est écrite ici plutôt que dans un
    `parametrize` de haut niveau pour qu'un gabarit ajouté sans être ajouté ICI se
    voie : `test_les_sept_gabarits_sont_couverts` compte les fonctions du module."""
    return [
        lambda: E.send_invite_email("q@e.test", "Acme", _URL, "alex",
                                    brand=brand, locale=locale),
        lambda: E.send_resource_shared_email("q@e.test", type_label="projet", name="Plan",
                                             permission="read", app_url=_URL,
                                             sharer="alex", brand=brand, locale=locale),
        lambda: E.send_resource_transferred_email("q@e.test", type_label="projet",
                                                  name="Plan", app_url=_URL, sharer="alex",
                                                  brand=brand, locale=locale),
        lambda: E.send_change_request_email("q@e.test", project_name="Plan",
                                            doc_title="page", proposer="alex",
                                            is_create=False, app_url=_URL,
                                            brand=brand, locale=locale),
        lambda: E.send_change_request_resolved_email("q@e.test", project_name="Plan",
                                                     doc_title="page", accepted=True,
                                                     app_url=_URL, brand=brand,
                                                     locale=locale),
        lambda: E.send_signal_digest_email(
            "q@e.test", items=[{"status": "resolved", "target": "oto_call",
                                "created_at": "2026-08-30 10:00:00", "body": "ça coince",
                                "resolution": "corrigé"}],
            brand=brand, locale=locale),
        lambda: E.send_composed_email("q@e.test", "objet", "bonjour,\n\nvoilà.",
                                      cta_text="ouvrir", cta_url=_URL, brand=brand),
    ]


# --- la marque --------------------------------------------------------------

def test_un_email_tulina_ne_porte_aucune_couleur_d_oto(envoye):
    """La propriété qui compte : chez un destinataire Tulina, RIEN du dessin d'oto ne
    doit ressortir. Assertion par l'absence, parce que c'est l'absence qui manquait."""
    oto, tulina = B.MARQUES["oto"], B.MARQUES["tulina"]
    couleurs_oto = {oto.fond, oto.surface, oto.encre, oto.discret, oto.filet}
    for envoi in _tous_les_envois("tulina"):
        envoi()
        html = envoye["html"]
        assert tulina.encre in html and tulina.filet in html
        for couleur in couleurs_oto:
            assert couleur not in html, f"{couleur} (oto) dans un email tulina"


def test_la_marque_ecrite_est_le_nom_du_produit_pas_le_slug(envoye):
    for envoi in _tous_les_envois("tulina"):
        envoi()
        assert "Tulina" in envoye["html"]
    for envoi in _tous_les_envois("oto"):
        envoi()
        assert "oto" in envoye["html"]


def test_un_slug_inconnu_prend_le_gabarit_neutre_et_SON_nom(envoye):
    """`front_brand` est une colonne, pas une énum : un tenant déclaré demain y écrira
    son slug avant que `MARQUES` le connaisse. Le repli n'est PAS oto — écrire le nom
    d'oto chez un partenaire est exactement le faux qu'on répare."""
    for envoi in _tous_les_envois("partenaire"):
        envoi()
        html = envoye["html"]
        assert "partenaire" in html
        assert "oto.cx" not in html
        assert B.MARQUES["oto"].encre not in html


def test_sans_marque_c_est_oto_le_defaut():
    """`orgs.front_brand IS NULL` veut dire « la plateforme », pas « inconnu »."""
    assert B.marque(None) is B.MARQUES["oto"]
    assert B.marque("") is B.MARQUES["oto"]
    assert B.marque("TULINA") is B.MARQUES["tulina"]


def test_une_marque_sans_site_ne_signe_pas_un_domaine_invente(envoye):
    E.send_invite_email("q@e.test", "Acme", _URL, brand="partenaire")
    pied = envoye["html"].rsplit("<tr>", 1)[-1]
    assert "·" not in pied, "une signature « nom · site » sans site connu"


# --- ce qu'un client mail sait rendre ---------------------------------------

@pytest.mark.parametrize("brand", ["oto", "tulina", "partenaire"])
def test_chaque_email_est_un_document_complet_en_tables(envoye, brand):
    for envoi in _tous_les_envois(brand):
        envoi()
        html = envoye["html"]
        assert html.startswith("<!DOCTYPE html>") and html.endswith("</html>")
        assert '<meta name="color-scheme" content="light">' in html, (
            "sans elle, Outlook mobile et Apple Mail repeignent le fond en sombre "
            "et laissent le texte en place")
        # La mise en page est en TABLES : Outlook (moteur Word) ne met en page que ça,
        # et ignorait le `max-width` du `<div>` d'avant.
        assert html.count("<table") >= 2 and 'role="presentation"' in html
        assert f'width="{B.LARGEUR}"' in html


@pytest.mark.parametrize("brand", ["oto", "tulina"])
def test_aucun_style_qui_ne_survit_pas_a_un_client_mail(envoye, brand):
    """Styles EN LIGNE uniquement. Un `<style>` est retiré par plusieurs webmails, une
    variable CSS n'est jamais résolue, `flex`/`grid` ne sont pas implémentés par le
    moteur Word — chacun rend une mise en page qui s'effondre sans erreur."""
    interdits = ("<style", "var(--", "display:flex", "display:grid", "@media")
    for envoi in _tous_les_envois(brand):
        envoi()
        for motif in interdits:
            assert motif not in envoye["html"], motif


@pytest.mark.parametrize("brand", ["oto", "tulina"])
def test_chaque_email_declare_sa_ligne_d_apercu(envoye, brand):
    """La ligne d'aperçu est la deuxième chose qu'on lit d'un email — avant de
    l'ouvrir. Sans bloc dédié, la boîte y mettait le premier texte venu, c'est-à-dire
    « ou collez ce lien : https://… »."""
    for envoi in _tous_les_envois(brand):
        envoi()
        html = envoye["html"]
        debut = html.index('<div style="display:none')
        apercu = html[debut:html.index("</div>", debut)]
        assert "mso-hide:all" in apercu
        texte = re.sub(r"<[^>]+>|&#8203;|&#847;", "", apercu).strip()
        assert texte and "collez ce lien" not in texte
        assert texte not in ("", B.marque(brand).nom)


def test_la_langue_du_document_suit_celle_du_destinataire(envoye):
    E.send_invite_email("q@e.test", "Acme", _URL, brand="tulina", locale="en")
    assert '<html lang="en">' in envoye["html"]
    E.send_invite_email("q@e.test", "Acme", _URL, brand="tulina", locale=None)
    assert '<html lang="fr">' in envoye["html"]


def test_le_bouton_est_une_table_et_disparait_sans_url():
    """Outlook n'applique ni `padding` ni `border-radius` à un `<a>` en `inline-block` :
    le fond et l'arrondi vivent sur le `<td>`. Et sans URL, RIEN — le produit d'un
    partenaire n'a pas forcément la vue visée, et un lien mort ne se diagnostique pas,
    il se subit (`links.py`)."""
    m = B.MARQUES["tulina"]
    bouton = B.bouton(m, _URL, "ouvrir")
    assert bouton.count("<a ") == 1 and f"background:{m.bouton_fond}" in bouton
    assert re.search(r"<td[^>]*border-radius:999px", bouton)
    assert B.bouton(m, None, "ouvrir") == "" and B.bouton(m, "", "ouvrir") == ""


def test_le_bouton_n_offre_aucun_guillemet_nu_a_une_url_d_agent():
    """`cta_url` vient d'un agent, et l'adresse est rendue DEUX fois : dans `href` et
    en clair dessous. Les deux sont échappées en attribut — une donnée d'entrée ne
    ressort jamais avec un guillemet nu, où qu'elle atterrisse."""
    bouton = B.bouton(B.MARQUES["oto"], 'https://e.test/?a="b" onmouseover="y', "ouvrir")
    assert 'onmouseover="' not in bouton and bouton.count("<a ") == 1
    assert bouton.count("&quot;") == 6, "3 guillemets de l'URL × 2 rendus"


def test_les_sept_gabarits_sont_couverts():
    """Un gabarit ajouté sans être ajouté à `_tous_les_envois` échapperait à TOUTES les
    propriétés ci-dessus, en silence. Le compte est donc vérifié, pas supposé."""
    assert len(_tous_les_envois("oto")) == 7
