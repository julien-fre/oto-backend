"""`email_send` sait afficher UNE image en tête du mail — et ce qu'il refuse.

Le corps d'un `email_send` est échappé : une balise glissée dedans ressort
littéralisée, et le bouton était le seul lien possible. Le gabarit accepte désormais
`image_url` + `image_alt`, placés AVANT le corps. Ce que ces tests décrivent, c'est
le rendu SERVI et les refus, pas l'intention :

- **sans `alt`, refus** — beaucoup de clients bloquent les images, le mail doit garder
  son sens sans elle ; aucun texte par défaut ne le ferait ;
- **`https://` seul** ;
- **URL et alt échappés en attribut** (guillemets compris : `_esc` ne les traite pas,
  et un `"` dans l'alt refermerait l'attribut) ;
- **sans image, le rendu est celui d'avant, à l'octet** (golden calculé sur `main`
  avant ce lot) ;
- le mailer Otomata et la file d'envoi différé portent l'image comme l'envoi direct.
"""
from __future__ import annotations

import asyncio

import pytest
from mcp.shared.exceptions import McpError

from oto_mcp import email as E

_URL = "https://media.exemple.test/images/u1/0123456789abcdef0123456789abcdef.png"
_BODY = "bonjour,\n\nligne 1\nligne 2"

# Rendu de `render_composed_email(_BODY, cta_text="ouvrir", cta_url="https://exemple.test/x")`
# par le code d'AVANT ce lot (origin/main) — le gabarit sans image ne bouge pas d'un octet.
_GOLDEN_SANS_IMAGE = (
    '<div style="font-family:system-ui,sans-serif;max-width:480px;margin:0 auto;color:#2c2112">'
    '<p style="font-size:16px;line-height:1.6;margin:0 0 16px">bonjour,</p>'
    '<p style="font-size:16px;line-height:1.6;margin:0 0 16px">ligne 1<br>ligne 2</p>'
    '<p style="padding:8px 0"><a href="https://exemple.test/x" style="display:inline-block;'
    'background:#2c2112;color:#fefcf5;text-decoration:none;padding:10px 20px;'
    'border-radius:999px;font-weight:600">ouvrir</a></p>'
    '<hr style="border:none;border-top:1px solid #ece4d0;margin:24px 0 16px">'
    '<p style="color:#7a6c50;font-size:13px">oto, par otomata · oto.cx<br>'
    'vous recevez ce message car vous avez un compte oto — répondez à cet email pour '
    'nous parler, ou pour ne plus en recevoir.</p></div>'
)


# --- le gabarit -------------------------------------------------------------

def test_sans_image_le_rendu_est_celui_d_avant_a_l_octet():
    html = E.render_composed_email(_BODY, cta_text="ouvrir", cta_url="https://exemple.test/x")
    assert html == _GOLDEN_SANS_IMAGE
    assert "<img" not in html


def test_avec_image_elle_precede_le_corps_a_la_largeur_du_gabarit():
    html = E.render_composed_email(_BODY, image_url=_URL, image_alt="visuel de bienvenue")
    img = html.index("<img")
    assert img < html.index("bonjour,"), "l'image est en TÊTE, avant le premier paragraphe"
    assert f'src="{_URL}"' in html and 'alt="visuel de bienvenue"' in html
    assert 'width="480"' in html
    assert 'style="max-width:100%;height:auto;display:block;border:0"' in html
    # Le reste du mail est intact : l'image s'ajoute, elle ne remplace rien.
    assert html.replace(html[html.index("<p style=\"margin:0 0 16px\"><img"):html.index("</p>") + 4], "") \
        == E.render_composed_email(_BODY)


def test_sans_alt_refus_explicite_pas_de_valeur_par_defaut():
    with pytest.raises(ValueError, match="image_alt"):
        E.render_composed_email(_BODY, image_url=_URL)
    with pytest.raises(ValueError, match="image_alt"):
        E.render_composed_email(_BODY, image_url=_URL, image_alt="   ")


def test_un_alt_sans_image_est_refuse_aussi():
    with pytest.raises(ValueError, match="image_url"):
        E.render_composed_email(_BODY, image_alt="visuel")


def test_http_est_refuse():
    with pytest.raises(ValueError, match="https://"):
        E.render_composed_email(_BODY, image_url="http://media.exemple.test/a.png",
                                image_alt="visuel")
    with pytest.raises(ValueError, match="https://"):
        E.render_composed_email(_BODY, image_url="data:image/png;base64,AAAA", image_alt="v")


def test_l_alt_et_l_url_sont_echappes_en_attribut():
    """Un `"` dans l'alt refermerait l'attribut ; `<`/`>` ouvriraient une balise. Aucun
    des quatre ne sort brut, et la balise servie reste la NÔTRE (un seul `<img`, un
    seul `>` de fermeture après `style=`)."""
    alt = 'bienvenue" onerror="x <b>gras</b> & co'
    url = "https://media.exemple.test/a.png?x=1&y=\"z\""
    html = E.render_composed_email(_BODY, image_url=url, image_alt=alt)
    assert 'alt="bienvenue&quot; onerror=&quot;x &lt;b&gt;gras&lt;/b&gt; &amp; co"' in html
    assert 'src="https://media.exemple.test/a.png?x=1&amp;y=&quot;z&quot;"' in html
    assert html.count("<img") == 1 and "<b>" not in html and 'onerror="' not in html


def test_send_composed_email_porte_l_image_jusqu_au_mailer(monkeypatch):
    envoye = {}
    monkeypatch.setattr(E, "_send", lambda to, subject, html, **k: envoye.update(html=html) or True)
    assert E.send_composed_email("qui@exemple.test", "objet", _BODY,
                                 image_url=_URL, image_alt="visuel") is True
    assert f'<img src="{_URL}"' in envoye["html"]


# --- l'outil `email_send` ---------------------------------------------------

_ROUTE_MARQUE = {"org_id": None, "connector": None, "from_email": None, "from_name": None,
                 "transport": "mailer", "reply_to": None, "quiet_hours": None}


@pytest.fixture
def outil(monkeypatch):
    from fastmcp import FastMCP
    from oto_mcp.tools import email as T
    monkeypatch.setattr(T, "_resolve_route", lambda from_email: ("u1", dict(_ROUTE_MARQUE)))
    m = FastMCP("t")
    T.register(m)
    return asyncio.run(m.get_tool("email_send"))


def _appel(outil, **kw):
    base = dict(ctx=None, to="qui@exemple.test", subject="objet", body=_BODY)
    base.update(kw)
    return outil.fn(**base)


def test_dry_run_rend_le_html_avec_l_image(outil):
    out = _appel(outil, image_url=_URL, image_alt="visuel", dry_run=True)
    assert out["dry_run"] is True and out["sent"] is False
    assert f'<img src="{_URL}" alt="visuel" width="480"' in out["html"]


def test_dry_run_sans_image_rend_le_html_d_avant(outil):
    out = _appel(outil, cta_text="ouvrir", cta_url="https://exemple.test/x", dry_run=True)
    assert out["html"] == _GOLDEN_SANS_IMAGE


@pytest.mark.parametrize("params, attendu", [
    ({"image_url": _URL}, "image_alt"),
    ({"image_alt": "visuel"}, "image_url"),
    ({"image_url": "http://media.exemple.test/a.png", "image_alt": "v"}, "https://"),
])
def test_l_outil_refuse_en_nommant_le_parametre(outil, params, attendu):
    with pytest.raises(McpError) as e:
        _appel(outil, dry_run=True, **params)
    assert attendu in e.value.error.message


def test_l_envoi_immediat_par_le_mailer_porte_l_image(outil, monkeypatch):
    envoye = {}
    monkeypatch.setattr(E, "_send", lambda to, subject, html, **k: envoye.update(html=html) or True)
    out = _appel(outil, image_url=_URL, image_alt="visuel", force_now=True)
    assert out["sent"] is True and out["transport"] == "mailer"
    assert f'<img src="{_URL}"' in envoye["html"]


def test_l_envoi_differe_met_en_file_le_html_avec_l_image(outil, monkeypatch):
    from oto_mcp.tools import email as T
    file_ = {}
    monkeypatch.setattr(T.db, "enqueue_scheduled_email",
                        lambda **kw: file_.update(kw) or 7)
    out = _appel(outil, image_url=_URL, image_alt="visuel", send_at="2999-01-01T09:00")
    assert out["scheduled"] is True and out["id"] == 7
    assert f'<img src="{_URL}"' in file_["body_html"]


def test_la_description_dit_comment_obtenir_l_url(outil):
    """L'agent lit la description et le schéma, pas la doc : le chemin vers une URL
    publique est dans les deux (fastmcp range la section `Args:` du docstring dans la
    description de chaque paramètre, pas dans celle de l'outil)."""
    assert 'oto_upload_url(target="image")' in (outil.description or "")
    assert "image_alt" in (outil.description or "")
    props = outil.parameters["properties"]
    assert 'oto_upload_url(target="image")' in props["image_url"]["description"]
    assert "REQUIS" in props["image_alt"]["description"]
