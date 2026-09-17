"""Un domaine NU qui ne résout pas est relu sous `www.` avant d'abandonner (oto#262).

Beaucoup de sites n'ont d'enregistrement DNS que sur `www.`. `web_read` sur `acme.fr`
échouait donc à la garde de sortie, en refus immédiat, sans que la forme usuelle soit
jamais essayée.

⚠️ **Ce banc garde les BORNES du repli, pas seulement son existence** : un nom qui
résout vers une adresse interne reste un refus de sécurité franc, sans requête vers sa
variante ; un nom qui résout n'est pas touché ; `www.` déjà présent n'est pas doublé.

Éprouvé rouge le 2026-09-16 : `_cible_avec_repli_www` ramené à `return url, False` ⟹ le
premier test rend le refus « ne résout pas » au lieu de la page.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from oto_mcp import egress                         # noqa: E402
from oto_mcp.mcp_errors import McpError             # noqa: E402
from oto_mcp.tools import web as W                  # noqa: E402
from test_web_read import _web_read                 # noqa: E402

PAGE = {"ok": True, "status": 200, "verdict": "lu",
        "html": "<html><title>Acme</title><body><p>" + "Contenu réel. " * 30 + "</p></body></html>"}


def _dns(monkeypatch, table: dict):
    """DNS simulé : un nom absent de la table ne résout pas."""
    def faux(hote, port):
        if hote not in table:
            raise OSError(f"{hote} : nodename nor servname provided")
        return table[hote]
    monkeypatch.setattr(egress, "resolved_addresses", faux)


def test_un_domaine_nu_qui_ne_resout_pas_est_relu_sous_WWW(monkeypatch):
    _dns(monkeypatch, {"www.acme.test": {"93.184.216.34"}})
    vues = []

    def fetch(url):
        vues.append(url)
        return dict(PAGE, final_url=url)
    monkeypatch.setattr(W, "_fetch_http", fetch)
    lire = _web_read(monkeypatch, repli_www_reel=True)
    out = lire(url="https://acme.test/equipe")
    assert vues == ["https://www.acme.test/equipe"], "le chemin et le schéma sont gardés"
    assert out["chemin"] == "http"
    assert any(t["cran"] == "dns" and "repli" in t["verdict"] for t in out["tentatives"]), (
        "le repli doit être DIT, pas silencieux")


def test_le_site_servi_reste_reconnu_comme_le_MEME(monkeypatch):
    """`acme.test` demandé, `www.acme.test` servi : même maison, aucun avertissement."""
    _dns(monkeypatch, {"www.acme.test": {"93.184.216.34"}})
    monkeypatch.setattr(W, "_fetch_http", lambda url: dict(PAGE, final_url=url))
    out = _web_read(monkeypatch, repli_www_reel=True)(url="https://acme.test/")
    assert out["hote"]["conforme"] is True and "avertissement" not in out


def test_une_adresse_INTERNE_ne_declenche_aucun_repli(monkeypatch):
    """Un nom qui résout en interne est un refus de sécurité : il reste franc."""
    _dns(monkeypatch, {"acme.test": {"10.0.0.7"}, "www.acme.test": {"93.184.216.34"}})
    assert W._cible_avec_repli_www("https://acme.test/") == ("https://acme.test/", False)


def test_un_nom_qui_RESOUT_n_est_pas_touche(monkeypatch):
    _dns(monkeypatch, {"acme.test": {"93.184.216.34"}, "www.acme.test": {"1.2.3.4"}})
    assert W._cible_avec_repli_www("https://acme.test/x") == ("https://acme.test/x", False)


def test_WWW_deja_present_n_est_pas_double(monkeypatch):
    _dns(monkeypatch, {})
    assert W._cible_avec_repli_www("https://www.acme.test/") == ("https://www.acme.test/", False)


def test_aucune_des_deux_ne_resout_le_refus_d_origine_sort(monkeypatch):
    """Le repli ne masque pas l'échec : sans variante joignable, l'URL demandée repart
    telle quelle et c'est la garde qui refuse, avec son propre message."""
    _dns(monkeypatch, {})
    assert W._cible_avec_repli_www("https://acme.test/") == ("https://acme.test/", False)


def test_une_adresse_IP_n_est_jamais_prefixee(monkeypatch):
    _dns(monkeypatch, {})
    assert W._cible_avec_repli_www("http://93.184.216.34/") == ("http://93.184.216.34/", False)
