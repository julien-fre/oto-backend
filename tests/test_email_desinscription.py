"""Le pied MARKETING : la phrase, la langue, et le lien de désinscription.

Trois choses à tenir ensemble, et la troisième est celle qu'on oublie :

1. **le lien est un vrai lien** — il ne passe pas par `mention`, qui est échappée (un
   `<a>` y arriverait en toutes lettres) ;
2. **la phrase CHANGE quand le lien est là** — sans lien, « répondez pour ne plus en
   recevoir » est le seul refus possible et il faut le dire ; avec lien, le laisser
   proposerait deux chemins dont un seul enregistre quoi que ce soit (une réponse
   humaine ne persiste aucun refus) ;
3. **rien ne bouge pour les appelants qui ne demandent rien** — les deux paramètres
   sont additifs, et un email transactionnel continue de REFUSER le désabonnement
   (`email_brand.mention_transactionnelle`), ce qui est délibéré : on ne se désabonne
   pas d'une invitation.
"""
from __future__ import annotations

import pytest

from oto_mcp import email as E

_CORPS = "bonjour,\n\nvoilà de quoi commencer."
_LIEN = "https://mcp.exemple.test/o/u/jeton-signe"


def test_sans_les_nouveaux_parametres_le_rendu_est_INCHANGE():
    """Le contrat des appelants existants, à l'octet près."""
    assert E.render_composed_email(_CORPS, locale=None, unsubscribe_url=None) == \
        E.render_composed_email(_CORPS)


def test_le_lien_de_desinscription_est_un_VRAI_lien():
    html = E.render_composed_email(_CORPS, unsubscribe_url=_LIEN)
    assert f'href="{_LIEN}"' in html
    assert "ne plus recevoir ces messages</a>" in html


def test_avec_un_lien_la_phrase_ne_propose_plus_de_repondre_pour_se_desabonner():
    avec = E.render_composed_email(_CORPS, unsubscribe_url=_LIEN)
    sans = E.render_composed_email(_CORPS)
    assert "ou pour ne plus en recevoir" in sans
    assert "ou pour ne plus en recevoir" not in avec, (
        "avec un lien, proposer AUSSI la réponse offre deux chemins dont un seul "
        "laisse une trace — le refus par email ne se persiste nulle part.")


def test_la_version_anglaise_traduit_la_phrase_ET_le_libelle_du_lien():
    html = E.render_composed_email(_CORPS, locale="en", unsubscribe_url=_LIEN)
    assert 'lang="en"' in html
    assert "you're receiving this because you have a" in html
    assert ">unsubscribe</a>" in html
    assert "vous recevez ce message" not in html


def test_sans_pied_il_n_y_a_pas_de_lien_non_plus():
    """`footer=False` = pas de pied du tout. Un lien de désinscription qui survivrait
    à la suppression du pied flotterait sans la phrase qui l'explique."""
    html = E.render_composed_email(_CORPS, footer=False, unsubscribe_url=_LIEN)
    assert _LIEN not in html


def test_une_url_non_https_est_REFUSEE():
    """Même exigence que l'image de tête : un lien en clair est bloqué ou marqué
    « non sécurisé », et un désabonnement qu'on ne peut pas cliquer n'en est pas un."""
    with pytest.raises(ValueError, match="https://"):
        E.render_composed_email(_CORPS, unsubscribe_url="http://exemple.test/u/x")


def test_une_url_ne_peut_pas_refermer_l_attribut_href():
    """L'URL vient d'un jeton signé, donc de nous — mais l'échappement en ATTRIBUT est
    ce qui garantit que ça reste vrai si un jour elle vient d'ailleurs."""
    html = E.render_composed_email(
        _CORPS, unsubscribe_url='https://exemple.test/u/"><script>x</script>')
    assert "<script>" not in html
    assert "&quot;" in html


def test_le_transactionnel_ne_propose_TOUJOURS_pas_de_desabonnement():
    """La contrepartie, prouvée et non affirmée : le lot n'a pas déteint sur l'autre
    pied. On ne se désabonne pas d'une invitation ni d'un partage."""
    from oto_mcp import email_brand
    m = email_brand.marque("oto")
    for locale in (None, "fr", "en"):
        phrase = email_brand.mention_transactionnelle(m, locale)
        assert "désinscri" not in phrase and "unsubscribe" not in phrase


def test_send_composed_email_porte_le_lien_jusqu_au_mailer(monkeypatch):
    vu = {}
    monkeypatch.setattr(E, "_send",
                        lambda to, subject, html, **kw: vu.update(html=html) or True)
    assert E.send_composed_email("qui@exemple.test", "objet", _CORPS,
                                 locale="en", unsubscribe_url=_LIEN)
    assert _LIEN in vu["html"] and ">unsubscribe</a>" in vu["html"]
