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
        P.patch_section(BODY, "Contacts", "x", mode="supprime")


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


# ── Le préambule : ce qui précède le premier titre (signaux #481, #492, #507) ─────────
#
# Chaque page de la base de connaissance du client s'ouvre sur un bandeau de provenance
# (« Last verified: … », les sources) posé AVANT le premier titre. Il n'appartient donc à
# aucune section, et `section=` — la seule poignée d'alors — ne pouvait pas l'atteindre :
# rafraîchir une date coûtait la réécriture des 128 000 caractères de la page.

BANNIERE = """> **Source** : ingestion quotidienne — 5 sources.
> **Last verified** : 16 août 2026.

# Fiche

intro générale.

## Panorama

contenu.
"""


def test_le_bandeau_n_appartient_a_aucune_section():
    """Le FAIT qui motive une seconde poignée, et qui restera vrai : `headings()` ne
    couvre pas le début du corps. Ce n'est pas un défaut du repérage par titre — c'est
    sa définition ; il faut donc un autre axe, pas un autre titre."""
    assert "Last verified" not in "".join(P.headings(BANNIERE))
    debut = BANNIERE.index("# Fiche")
    assert "Last verified" in BANNIERE[:debut]      # le bandeau vit AU-DESSUS du 1er titre


def test_le_preambule_se_lit():
    assert "Last verified" in P.preamble(BANNIERE)
    assert "Panorama" not in P.preamble(BANNIERE)
    # Une page qui ouvre sur son titre n'a pas de préambule.
    assert P.preamble("# T\n\nx\n").strip() == ""


def test_replace_du_preambule_ne_touche_ni_les_titres_ni_les_sections():
    out = P.patch_preamble(
        BANNIERE, "> **Source** : ingestion quotidienne — 6 sources.\n"
                  "> **Last verified** : 18 août 2026.", mode="replace")
    assert "18 août 2026" in out and "16 août 2026" not in out
    assert "6 sources" in out and "5 sources" not in out
    # Tout le reste de la page est BIT POUR BIT intact : c'est ce que le patch promet.
    assert out[out.index("# Fiche"):] == BANNIERE[BANNIERE.index("# Fiche"):]
    assert P.headings(out) == P.headings(BANNIERE)


def test_append_et_prepend_du_preambule():
    ap = P.patch_preamble(BANNIERE, "> ligne ajoutée.", mode="append")
    assert ap.index("Last verified") < ap.index("ligne ajoutée") < ap.index("# Fiche")
    pre = P.patch_preamble(BANNIERE, "> ligne en tête.", mode="prepend")
    assert pre.index("ligne en tête") < pre.index("Last verified") < pre.index("# Fiche")


def test_ecrire_un_preambule_sur_une_page_qui_n_en_a_pas():
    """Poser le bandeau la première fois : la page ouvre alors sur lui, pas sur son titre."""
    out = P.patch_preamble("# T\n\nx\n", "> **Last verified** : 18 août 2026.")
    assert out.startswith("> **Last verified**")
    assert P.headings(out) == ["T"] and "x" in out


def test_delete_du_preambule_retire_le_bandeau_et_rien_d_autre():
    out = P.patch_preamble(BANNIERE, mode="delete")
    assert "Last verified" not in out
    assert out.startswith("# Fiche")
    assert P.headings(out) == P.headings(BANNIERE)


def test_un_titre_dans_le_corps_du_preambule_est_REFUSE():
    """Le préambule est ce qui PRÉCÈDE le premier titre : y écrire un titre le referme —
    la région se rétrécirait toute seule et le patch suivant n'atteindrait plus le
    bandeau. On refuse en nommant, on ne devine pas (cf. l'absorption du titre propre,
    signal #328, qui traite le cas symétrique côté section)."""
    with pytest.raises(P.HeadingInPreamble) as ei:
        P.patch_preamble(BANNIERE, "# Nouveau titre\n\ntexte")
    assert ei.value.found == ["Nouveau titre"]


def test_delete_d_un_preambule_absent_est_REFUSE():
    with pytest.raises(P.PreambleAbsent):
        P.patch_preamble("# T\n\nx\n", mode="delete")


def test_delete_du_preambule_d_une_page_SANS_AUCUN_TITRE_est_REFUSE():
    """Sans premier titre, « ce qui précède le premier titre » est la page entière :
    supprimer viderait tout. Forme ambiguë ET destructrice → refus nommé."""
    with pytest.raises(P.PreambleIsWholePage):
        P.patch_preamble("juste du texte, aucun titre.\n", mode="delete")


# ── Supprimer une section, TITRE COMPRIS (signal #583) ────────────────────────────────
#
# `replace` garde le titre : vider une section laissait un titre orphelin. Le journal
# glissant du client ne pouvait donc pas purger son entrée de J-14 — elle a été remplacée
# par un paragraphe-pierre tombale sous un titre désormais vide. Deux fois dans un seul
# déroulé.

def test_delete_retire_la_section_ET_son_titre():
    out = P.patch_section(BODY, "Contacts", mode="delete")
    assert "## Contacts" not in out and "- alice" not in out
    assert "Contacts" not in P.headings(out)
    # Les voisines ne bougent pas.
    assert "## Panorama des marchés" in out and "ancien contenu du panorama." in out


def test_delete_emporte_les_sous_sections_comme_replace():
    """Même portée que `replace` (sémantique de section tranchée au signal #334) :
    ce que `subsections()` annonce est exactement ce qui part."""
    annonce = P.subsections(BODY, "Panorama des marchés")
    out = P.patch_section(BODY, "Panorama des marchés", mode="delete")
    assert annonce == ["Sous-marché A"]
    assert P.headings(out) == ["Fiche", "Contacts"]
    assert "détail A." not in out


def test_delete_ne_laisse_pas_de_lignes_vides_empilees():
    out = P.patch_section(BODY, "Panorama des marchés", mode="delete")
    assert "\n\n\n" not in out
    assert out.endswith("\n")


def test_delete_de_la_derniere_section():
    out = P.patch_section(BODY, "Contacts", mode="delete")
    assert out.endswith("\n") and "\n\n\n" not in out


def test_delete_d_une_section_inconnue_reste_un_refus_nomme():
    with pytest.raises(P.SectionNotFound) as ei:
        P.patch_section(BODY, "Fantôme", mode="delete")
    assert "Contacts" in ei.value.available


def test_un_corps_passe_avec_delete_est_refuse_par_le_moteur():
    """`delete` ne prend pas de contenu : l'accepter-et-l'ignorer est la famille de
    défauts que ce dépôt refuse (leçon #461)."""
    with pytest.raises(ValueError):
        P.patch_section(BODY, "Contacts", "du contenu", mode="delete")
