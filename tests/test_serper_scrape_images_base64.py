"""serper_scrape : une image incluse en base64 ne se sert pas au modèle (oto#246).

Mesuré le 14/09/2026 : la page d'accueil d'un petit site pesait 227 106 caractères, dont
91 % d'images en base64 ; la même page sans ses images en fait 19 396. Coupée à 120 k
caractères par le worker, elle coûtait 83 à 95 k jetons d'entrée à CHAQUE tour qui la
relisait, et trois travaux sur la même ligne ont franchi leur borne par ligne.

Ce que ces bancs fixent : l'encodage part, une trace le dit ; le texte, les liens et les
images ordinaires restent ; le HTML brut demandé exprès reste brut.
"""
from __future__ import annotations

import pytest

from oto_mcp.tools import images_base64 as I
from oto_mcp.tools import mail_obfuscation as M

B64 = "iVBORw0KGgo" + "A" * 400
PNG = f"data:image/png;base64,{B64}"


# ── la fonction ──────────────────────────────────────────────────────────────

def test_une_image_base64_est_remplacee_par_une_trace_qui_dit_ce_qui_manque():
    md = (f"# Maison\n\n![logo]({PNG})\n\n"
          "Mentions légales : [ici](https://acme.test/mentions)")

    apres, nombre, caracteres = I.retirer(md)

    assert (nombre, caracteres) == (1, len(PNG))
    assert B64 not in apres
    assert f"![logo](data:image/png — {len(B64)} caractères de base64 retirés)" in apres
    assert "[ici](https://acme.test/mentions)" in apres


@pytest.mark.parametrize("intact", [
    "![photo](https://acme.test/photo.jpg)",                  # une image ordinaire
    "![pixel](data:image/gif;base64,R0lGODlhAQABAAAAACw=)",   # trop courte pour valoir une trace
    "data:text/plain;base64," + "Q" * 200,                    # pas une image : peut porter une adresse
])
def test_ce_qui_n_est_pas_une_image_base64_ne_bouge_pas(intact):
    assert I.retirer(f"avant {intact} après") == (f"avant {intact} après", 0, 0)


def test_alleger_compte_ce_qu_il_retire_et_se_tait_quand_il_n_y_a_rien():
    res = {"markdown": f"a {PNG} b", "text": PNG, "credits": 2}
    propre = {"markdown": "rien à retirer"}

    I.alleger(res)
    I.alleger(propre)

    assert res["images_base64_retirees"] == {"nombre": 2, "caracteres": 2 * len(PNG)}
    assert res["credits"] == 2
    assert "images_base64_retirees" not in propre


# ── l'outil monté ────────────────────────────────────────────────────────────

class _Reg:
    def __init__(self):
        self.tools = {}

    def tool(self, *a, **k):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        if a and callable(a[0]):
            return deco(a[0])
        return deco


@pytest.fixture()
def monte(monkeypatch):
    """`serper_scrape` monté sur un scraper hébergé et une lecture directe pilotés."""
    etat = {"scrape": {}, "html": None}

    class _Client:
        def __init__(self, *a, **k):
            ...

        @classmethod
        def _refuses_scraping(cls, url):
            return None

        def scrape_page(self, url, include_markdown=True, timeout_s=None):
            return dict(etat["scrape"])

    def _fetch(url, deadline_s=M.SONDE_DELAI_S):
        return {"ok": True, "status": 200, "html": etat["html"],
                "final_url": url, "verdict": "lu"}

    monkeypatch.setattr("oto.tools.serper.SerperClient", _Client)
    monkeypatch.setattr("oto_mcp.access.resolve_api_key", lambda p: ("k", False))
    monkeypatch.setattr(M, "fetch", _fetch)
    from oto_mcp.tools import serper
    reg = _Reg()
    serper.register(reg)
    return reg.tools["serper_scrape"], etat


def test_le_scrape_sert_la_page_sans_ses_images(monte):
    fn, etat = monte
    md = f"# Maison\n\n![logo]({PNG})\n\nÉcrire à contact@acme.test"
    etat["scrape"] = {"markdown": md, "text": md, "credits": 2, "metadata": {}}

    res = fn("https://acme.test/")

    assert B64 not in res["markdown"]
    assert "contact@acme.test" in res["markdown"]
    assert res["images_base64_retirees"] == {"nombre": 1, "caracteres": len(PNG)}


def test_le_html_brut_demande_expres_garde_ses_images(monte):
    fn, etat = monte
    etat["html"] = f'<p>Bonjour <img src="{PNG}"></p>'

    res = fn("https://acme.test/", format="html")

    assert B64 in str(res)
    assert "images_base64_retirees" not in res
