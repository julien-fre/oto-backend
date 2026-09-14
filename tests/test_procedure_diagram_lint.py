"""Le contrat du lint de schéma : zéro faux positif, et il attrape la vraie faute.

Les fixtures sont les 72 doctrines PUBLIQUES telles que servies par
`/api/doctrines/library` le 2026-09-14 — pas des cas inventés : ce sont
exactement les dessins qui se rendent aujourd'hui en figure de flux dans le
front. Un lint qui en signale une seule est cassé, parce qu'un auteur qui voit
un avertissement sur un schéma correct apprend à ignorer TOUS les
avertissements.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from oto_mcp.procedure_diagram import diagram_check, lint_du_trace

FIXTURES = sorted((Path(__file__).parent / "fixtures" / "drawings").glob("*.md"))


def test_fixtures_are_there():
    assert len(FIXTURES) == 72


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.stem)
def test_no_false_positive_on_a_doctrine_that_renders(path: Path):
    """Aucune des 72 doctrines vivantes ne doit être signalée."""
    assert diagram_check(path.read_text())["diagram_warning"] is None


# ── ce que le lint DOIT attraper ────────────────────────────────────────────

# La vraie faute, relevée sur la doctrine d'une org le 2026-09-14 : deux
# flèches étiquetées sur une même ligne, parce que l'auteur voulait une
# branche qui saute en avant — une forme que la grammaire ne sait pas dire.
REAL_CASE = """```
              Twice daily, morning and evening
                         │
                         ▼
┌─────────────────────────────────────────────────┐
│  1 · Pull every company that visited            │
└────────────────────────┬────────────────────────┘
      ▼ known: straight to the recap    ▼ cold or partner
┌─────────────────────────────────────────────────┐
│  2 · Ask what the visit says                    │
└────────────────────────┬────────────────────────┘
```"""


def test_catches_text_between_arrows():
    said = diagram_check(REAL_CASE)["diagram_warning"]
    assert "will NOT render" in said
    assert "between two flow arrows" in said
    assert "named exit" in said


def test_reports_the_line_in_the_body_not_in_the_block():
    """Une ligne que l'auteur peut retrouver dans le corps qu'il vient d'écrire."""
    body = "# Titre\n\nUne phrase.\n\n" + REAL_CASE
    said = diagram_check(body)["diagram_warning"]
    line = int(re.search(r"line (\d+):", said).group(1))
    assert body.split("\n")[line - 1].count("▼") == 2


def test_catches_a_tab():
    assert lint_du_trace("┌──┐\n\t│  │\n└──┘")[0]["rule"] == "tab"


def test_catches_loose_prose_inside_the_drawing():
    drawing = ("┌──────────┐\n│  1 · Step │\n└─────┬────┘\n"
               "   note: worth a second look\n         ▼\n"
               "┌──────────┐\n│  2 · Next │\n└─────┬────┘")
    assert [p["rule"] for p in lint_du_trace(drawing)] == ["loose-text"]


# ── ce que le lint ne doit PAS prétendre ────────────────────────────────────

def test_a_label_after_the_last_arrow_is_legal():
    assert lint_du_trace("┌──┐\n└─┬┘\n   ▼  a real page was read\n┌──┐\n└──┘") == []


def test_a_named_exit_is_legal():
    """La réécriture qu'on conseille doit elle-même passer le lint."""
    assert lint_du_trace(
        "┌──────────┐\n└─────┬────┘\n"
        "      ├───────▶  ▪ known account    straight to the recap\n"
        "      ▼  cold or partner\n┌──────────┐\n└──────────┘"
    ) == []


def test_a_mermaid_block_is_not_this_grammar():
    """Un bloc mermaid n'est pas un dessin : pas de lint, juste « rien dessiné »."""
    assert "NOT render" not in (diagram_check("```mermaid\nflowchart TD\n  A --> B\n```")["diagram_warning"] or "")


def test_a_body_with_no_drawing_is_the_old_warning():
    said = diagram_check("# Just prose\n\nNothing drawn here.")["diagram_warning"]
    assert said is not None and "NOT render" not in said


def test_a_rejoining_lane_is_legal():
    """`▷` — la voie qui repart et revient — ne doit pas passer pour de la prose."""
    assert lint_du_trace(
        "┌──────────┐\n└─────┬────┘\n"
        "      ├───────▶  ▷ known account    straight to the recap\n"
        "      ▼  cold or partner\n┌──────────┐\n└──────────┘\n"
        "  ▷ known account   rejoins at the recap"
    ) == []
