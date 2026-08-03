"""Moteur d'édition partielle par section (oto/#6 top5 #3)."""
import pytest

from oto_mcp import doc_patch as P

BODY = """# Fiche

intro générale.

## Panorama des marchés

ancien contenu du panorama.
plusieurs lignes.

### Sous-marché A

détail A.

## Contacts

- alice
"""


def test_replace_keeps_heading_and_swaps_body():
    out = P.patch_section(BODY, "Panorama des marchés", "NOUVEAU panorama.", mode="replace")
    assert "## Panorama des marchés" in out
    assert "NOUVEAU panorama." in out
    assert "ancien contenu du panorama." not in out
    # la sous-section fait partie de la section ciblée → remplacée aussi
    assert "détail A." not in out
    # les AUTRES sections intactes
    assert "intro générale." in out and "- alice" in out


def test_replace_stops_at_same_level_heading():
    # « Contacts » (## ) n'est pas touché en remplaçant « Panorama » (## ).
    out = P.patch_section(BODY, "Panorama des marchés", "x", mode="replace")
    assert "## Contacts" in out and "- alice" in out


def test_append_adds_after_existing_section_body():
    out = P.patch_section(BODY, "Contacts", "- bob", mode="append")
    assert "- alice" in out and "- bob" in out
    assert out.index("- alice") < out.index("- bob")


def test_prepend_inserts_right_after_heading():
    out = P.patch_section(BODY, "Contacts", "- zoé", mode="prepend")
    assert out.index("- zoé") < out.index("- alice")


def test_heading_match_is_case_and_hash_insensitive():
    out = P.patch_section(BODY, "## panorama DES marchés", "ok", mode="replace")
    assert "ok" in out and "ancien contenu" not in out


def test_missing_section_raises_with_available():
    with pytest.raises(P.SectionNotFound) as ei:
        P.patch_section(BODY, "Inexistante", "x")
    assert "Panorama des marchés" in ei.value.available
    assert "Contacts" in ei.value.available


def test_headings_lists_all():
    assert P.headings(BODY) == ["Fiche", "Panorama des marchés", "Sous-marché A", "Contacts"]


def test_invalid_mode():
    with pytest.raises(ValueError):
        P.patch_section(BODY, "Contacts", "x", mode="delete")


# --- Le corps ne redéclare pas son propre titre (signal #328) ---------------

def test_replace_absorbs_heading_repeated_in_body():
    """Un agent qui renvoie « ## Contacts\\n- bob » ne doit pas créer 2 sections."""
    out = P.patch_section(BODY, "## Contacts", "## Contacts\n\n- bob", mode="replace")
    assert P.headings(out).count("Contacts") == 1
    assert "- bob" in out


def test_absorption_is_case_and_hash_insensitive_like_the_match():
    out = P.patch_section(BODY, "Contacts", "#### contacts\n- bob", mode="replace")
    assert P.headings(out).count("Contacts") == 1
    assert "- bob" in out


def test_append_and_prepend_absorb_too():
    for mode in ("append", "prepend"):
        out = P.patch_section(BODY, "Contacts", "## Contacts\n- bob", mode=mode)
        assert P.headings(out).count("Contacts") == 1, mode
        assert "- alice" in out and "- bob" in out, mode


def test_a_different_heading_in_body_is_preserved():
    """On n'absorbe QUE le titre visé — une sous-section légitime reste."""
    out = P.patch_section(BODY, "Contacts", "### Internes\n- bob", mode="replace")
    assert "### Internes" in out
    assert P.headings(out).count("Contacts") == 1


def test_body_that_is_only_the_heading_empties_the_section():
    out = P.patch_section(BODY, "Contacts", "## Contacts", mode="replace")
    assert P.headings(out).count("Contacts") == 1
    assert "- alice" not in out


# --- Portée réelle d'une section : ses sous-sections en font partie (signal #334) ---

def test_subsections_lists_nested_headings_only():
    """« Panorama » contient « ### Sous-marché A » ; « Contacts » n'a pas d'enfant."""
    assert P.subsections(BODY, "Panorama des marchés") == ["Sous-marché A"]
    assert P.subsections(BODY, "Contacts") == []


def test_subsections_is_empty_for_an_unknown_heading():
    assert P.subsections(BODY, "Section fantôme") == []


def test_subsections_matches_what_replace_actually_removes():
    """Le contrat : ce que `subsections` annonce est EXACTEMENT ce que replace retire."""
    announced = P.subsections(BODY, "Panorama des marchés")
    out = P.patch_section(BODY, "Panorama des marchés", "nouveau panorama.", mode="replace")
    for h in announced:
        assert h not in P.headings(out)
    assert "détail A." not in out


def test_subsections_goes_deeper_than_one_level():
    body = "## Parent\n\ntexte.\n\n### Enfant\n\nx.\n\n#### Petit-enfant\n\ny.\n\n## Autre\n"
    assert P.subsections(body, "Parent") == ["Enfant", "Petit-enfant"]
    assert P.subsections(body, "Autre") == []


def test_append_does_not_touch_subsections():
    """mode=append n'écrase rien : le caller n'a donc rien à annoncer."""
    out = P.patch_section(BODY, "Panorama des marchés", "ajout.", mode="append")
    assert "### Sous-marché A" in out and "détail A." in out
