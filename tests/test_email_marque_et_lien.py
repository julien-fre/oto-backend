"""Les emails transactionnels parlent la marque du DESTINATAIRE, et ne fabriquent
jamais de lien mort.

Deux fautes distinctes vivaient dans le même envoi :

- l'ADRESSE était celle d'oto pour tout le monde, alors que le destinataire ouvre
  ce lien chez LUI ;
- le TEXTE disait « ouvrir dans oto » — un faux pour quelqu'un qui ne connaît que
  Tulina, et qu'aucune URL correcte ne rattrape.

Et la correction naïve — garder nos chemins, changer le domaine — est justement
celle que `links.py` interdit : `/projects/12` n'existe pas chez le partenaire, donc
le « lien corrigé » serait un 404. D'où l'email SANS bouton quand le tenant n'a pas
la vue.
"""
from __future__ import annotations

from oto_mcp import email


def test_sans_lien_pas_de_bouton():
    """Un lien mort ne se diagnostique pas, il se subit : mieux vaut pas de bouton."""
    assert email._bouton(None, "ouvrir") == ""
    assert email._bouton("", "ouvrir") == ""


def test_avec_lien_le_bouton_et_lurl_en_clair():
    html = email._bouton("https://app.tulina.ai/org/196/projects/12", "ouvrir")
    assert "https://app.tulina.ai/org/196/projects/12" in html
    assert "<a href=" in html


def test_la_proposition_part_sans_bouton_quand_le_tenant_na_pas_la_vue(monkeypatch):
    envoye = {}
    monkeypatch.setattr(email, "_send",
                        lambda to, subject, html, **k: envoye.update(
                            to=to, subject=subject, html=html) or True)
    email.send_change_request_email(
        "qui@tulina.test", project_name="P", doc_title="D", proposer="A",
        is_create=False, app_url=None, brand="Tulina")
    assert "<a href=" not in envoye["html"]
    # la nouvelle reste utile sans lien
    assert "validation est attendue" in envoye["html"]


def test_la_marque_du_destinataire_remplace_oto_partout(monkeypatch):
    envoye = {}
    monkeypatch.setattr(email, "_send",
                        lambda to, subject, html, **k: envoye.update(
                            to=to, subject=subject, html=html) or True)
    email.send_resource_shared_email(
        "qui@tulina.test", type_label="projet", name="P", permission="read",
        app_url="https://app.tulina.ai", sharer="A", brand="Tulina")
    assert "Tulina" in envoye["subject"] and "oto" not in envoye["subject"]
    assert "ouvrir dans Tulina" in envoye["html"]
    assert "sur oto" not in envoye["html"]


def test_sans_marque_le_defaut_reste_oto(monkeypatch):
    """Aucun compte de la plateforme ne doit voir son email changer."""
    envoye = {}
    monkeypatch.setattr(email, "_send",
                        lambda to, subject, html, **k: envoye.update(
                            to=to, subject=subject, html=html) or True)
    email.send_resource_transferred_email(
        "qui@oto.test", type_label="projet", name="P",
        app_url="https://manage.oto.cx", sharer="A")
    assert "sur oto" in envoye["subject"]
    assert "ouvrir dans oto" in envoye["html"]
