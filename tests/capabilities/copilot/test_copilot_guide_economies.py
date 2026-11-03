"""Le guide `saving-credits` et le renvoi de la consigne du copilote.

Le copilote ne lit un guide que si sa consigne le nomme : le renvoi et le fichier
doivent rester d'accord, sans quoi la question « pourquoi c'est si cher ? » retombe
sur une réponse au jugé.
"""
from __future__ import annotations

import pathlib

from oto_mcp.capabilities.copilot import consigne

GUIDE = pathlib.Path(__file__).resolve().parents[3] / "oto_mcp" / "guides" / "saving-credits.md"


def test_le_guide_existe_et_porte_son_en_tete():
    texte = GUIDE.read_text(encoding="utf-8")
    assert texte.startswith("---\ntitle:")
    assert "\ndescription:" in texte.split("---", 2)[1]


def test_la_consigne_renvoie_au_guide_et_aux_deux_mesures():
    c = consigne.consigne("", "app")
    assert "saving-credits" in c
    assert "usage_report" in c and "credit_rates" in c


def test_le_guide_mesure_avant_de_conseiller_et_demande_l_accord():
    texte = GUIDE.read_text(encoding="utf-8")
    for exigence in ("usage_report", "credit_rates", "approval", "one** change", "oto_recipe", "max_units"):
        assert exigence in texte
