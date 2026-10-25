"""The `<next>` chips never reach the screen, however the stream cuts the tag; a long
result is cut AND the cut is said."""
from __future__ import annotations

import pytest

from oto_mcp.capabilities.copilot.texte import PLAFOND_RESULTAT, FiltreSuite, couper

TEXTE = "Done: 3 rows.\n<next>Show them | Undo it\n| Make it weekly</next>"


@pytest.mark.parametrize("taille", [1, 2, 3, 5, 7, len(TEXTE)])
def test_suggestions_hors_de_l_ecran_quel_que_soit_le_decoupage(taille):
    vus: list[str] = []
    f = FiltreSuite(vus.append)
    for i in range(0, len(TEXTE), taille):
        f.pousser(TEXTE[i:i + taille])
    items = f.finir()
    assert "".join(vus) == "Done: 3 rows.\n"
    assert items == ["Show them", "Undo it", "Make it weekly"]


def test_sans_bloc_tout_passe_meme_un_chevron():
    vus: list[str] = []
    f = FiltreSuite(vus.append)
    for morceau in ("a <ne", "xt-ish b", " <"):
        f.pousser(morceau)
    assert f.finir() == []
    assert "".join(vus) == "a <next-ish b <"


def test_au_plus_trois():
    vus: list[str] = []
    f = FiltreSuite(vus.append)
    f.pousser("<next>a|b|c|d</next>")
    assert f.finir() == ["a", "b", "c"]


def test_couper_dit_la_coupe():
    assert couper("") == "(empty)"
    court = "x" * 10
    assert couper(court) == court
    long = "y" * (PLAFOND_RESULTAT + 5)
    out = couper(long)
    assert out.startswith("y" * PLAFOND_RESULTAT)
    assert "5 more characters" in out
