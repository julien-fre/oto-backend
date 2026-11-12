"""Le connecteur Custom API s'appelle `api` (ex-`http`), sans alias.

L'ancien nom ne désigne plus rien : le connecteur, ses outils et le `kind` de source de
fichier qui le joignait refusent en nommant le remplaçant ; l'annonce servie aux agents le
dit pendant un mois.
"""
from __future__ import annotations

import datetime

import pytest

from oto_mcp import file_source, outils_retires, providers

ANCIENS = {"http": "api", "http_act": "api_act", "http_get": "api_get",
           "http_post": "api_post", "http_doc": "api_doc"}


def test_le_registre_sert_api_et_plus_http():
    assert "api" in providers.REGISTRY and "http" not in providers.REGISTRY
    assert providers.REGISTRY["api"].label == "Custom API"
    assert providers.CONNECTEURS_RENOMMES == {"http": "api"}


@pytest.mark.parametrize("ancien,nouveau", sorted(ANCIENS.items()))
def test_chaque_ancien_outil_refuse_en_nommant_le_nouveau(ancien, nouveau):
    r = outils_retires.retrait(ancien)
    assert r is not None, ancien
    assert f"`{nouveau}`" in r.message and f"`{nouveau}`" in r.hint
    assert "same arguments" in r.message
    assert outils_retires.remplacant_de_nom(ancien) == nouveau


def test_aucun_nouveau_nom_n_est_retire():
    assert all(outils_retires.retrait(n) is None for n in ANCIENS.values())


def test_l_annonce_est_servie_un_mois():
    jour = outils_retires.LOT_CONNECTEUR_API
    annonce = outils_retires.annonce_connecteurs_du_jour(jour)
    assert all(f"`{a}` → `{n}`" in annonce for a, n in ANCIENS.items())
    fin = outils_retires.ANNONCE_CONNECTEURS_JUSQU_AU
    assert fin - jour == datetime.timedelta(days=30)
    assert outils_retires.annonce_connecteurs_du_jour(fin) is not None
    assert outils_retires.annonce_connecteurs_du_jour(
        fin + datetime.timedelta(days=1)) is None


def test_le_refus_d_un_connecteur_renomme_nomme_le_nouveau():
    assert "`api`" in providers.refus_renomme("http")
    assert providers.refus_renomme("serper") == ""


def test_la_source_de_fichier_http_refuse_en_nommant_api():
    with pytest.raises(file_source.FileSourceError, match='kind="api"'):
        file_source.resolve({"kind": "http", "path": "/x.csv"})
