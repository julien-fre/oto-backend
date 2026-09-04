"""La forme de `slots` est DITE dans la description, pas apprise par refus (#674).

Signalé le 03/09/2026 : « `slots` est documenté comme "required entities
referenced <slot:name> in the prose" **sans sa forme**. Une liste de noms est
refusée, une liste de `{name}` aussi ; la forme acceptée est `[{name, type}]`.
Une procédure est passée en version 3 en découvrant ça par essais. »

⚠️ **Un point du signalement est FAUX et ne doit pas être recopié** : l'auteur
écrit qu'un slot `connecteur` « a aussi besoin de `connector: "<slug>"` ». Lu
dans le code : `connector` est **facultatif** — absent, c'est le NOM du slot qui
sert de connecteur. Recopier sa croyance dans la description servie aurait gravé
une contrainte qui n'existe pas, à partir d'un rapport de bonne foi. La
description dit donc l'inverse, explicitement.

⚠️ **Et les refus, eux, étaient déjà bons** : ils nomment l'entrée fautive par son
index et disent ce qui était attendu. Ce lot ne les touche pas. Ce qui manquait
n'était pas un message d'erreur, c'était de ne pas avoir à le déclencher.

Éprouvé rouge le 2026-09-03 : la forme retirée de la description ⟹ le premier
test constate qu'elle s'apprend encore par essais.
"""
from __future__ import annotations

import pytest

from oto_mcp.capabilities import procedure_console as C
from oto_mcp.slots import SLOT_TYPES, validate_slots


@pytest.fixture(scope="module")
def prose() -> str:
    textes = [c.description or "" for c in C.CAPABILITIES
              if "procedure" in (c.key or "")]
    assert textes, "capacité de procédure introuvable"
    return " ".join(" ".join(t.split()) for t in textes)


def test_la_FORME_est_donnee(prose):
    assert "[{name, type}]" in prose


def test_les_deux_formes_REFUSEES_sont_nommees(prose):
    """Dire la bonne forme ne suffit pas : l'auteur avait essayé les deux
    mauvaises, et c'est en les voyant nommées qu'on cesse de les tenter."""
    assert "bare list of names is refused" in prose
    assert "`{name}` alone" in prose


def test_les_TYPES_sont_enumeres_et_collent_au_code(prose):
    """Un énuméré recopié à la main diverge au premier type ajouté."""
    for t in SLOT_TYPES:
        assert t in prose, f"le type `{t}` n'est pas dit dans la description"


def test_la_description_CONTREDIT_la_croyance_du_signalement(prose):
    """Le point le plus important du lot : `connector` est facultatif. Le
    signalement affirmait l'inverse ; le recopier aurait gravé une contrainte
    inexistante à partir d'un rapport de bonne foi."""
    assert "it is not required" in prose


def test_et_le_CODE_dit_bien_la_meme_chose():
    """La garde qui compte : on vérifie la promesse contre le comportement, pas
    contre une lecture du source. Un slot `connecteur` sans `connector` passe, et
    le nom du slot fait office de connecteur."""
    out = validate_slots([{"name": "folk", "type": "connecteur"}])
    assert out == [{"name": "folk", "type": "connecteur", "connector": "folk"}]


def test_les_formes_annoncees_comme_refusees_le_sont_VRAIMENT():
    """L'autre moitié : ce que la description dit refusé doit l'être."""
    with pytest.raises(ValueError):
        validate_slots(["vivier"])                       # liste de noms nus
    with pytest.raises(ValueError):
        validate_slots([{"name": "vivier"}])             # sans `type`
