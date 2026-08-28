"""Une section de doc connecteur mal titrée est AVALÉE, pas signalée — plus jamais.

Le parseur ne reconnaît un titre que sous la forme `## <kind> — <titre>`, avec un
kind de la liste fermée. Une ligne qui n'y correspond pas ne lève rien : elle tombe
dans le CORPS de la section précédente, `##` compris. La section s'affiche donc au
mauvais endroit, sous le mauvais intitulé — un texte d'usage dans le prérequis, qui
est montré AVANT connexion.

Trouvé le 2026-08-27 en écrivant `## plusieurs workspaces — …` dans la doc Slack.
Le même jour, le garde-fou a révélé quatre `## gotcha — …` dans lemlist et deux
titres préfixés d'un emoji (grain, snitcher) qui vivaient ainsi depuis longtemps :
la doc servie n'était pas celle qu'on croyait écrire.

Ce test lit les fichiers LIVRÉS, ligne à ligne — pas la sortie du parseur, qui ne
peut par construction pas voir ce qu'il a avalé.
"""
from __future__ import annotations

import pathlib
import re

import pytest

from oto_mcp.connectors.docs_reader import KINDS, _DIR

# Ce que le parseur accepte, écrit ici sous une forme LISIBLE (le patron du module
# est construit par jointure) : `## kind — titre`, tiret cadratin ou simple.
_VALIDE = re.compile(r"^##\s+(" + "|".join(KINDS) + r")\s*[—-]\s*\S")

_FICHIERS = sorted(_DIR.glob("*.md"))


def test_il_y_a_des_docs_a_verifier():
    """Sans ce garde, un dossier vide ou déplacé rendrait les tests ci-dessous
    verts en n'ayant rien vérifié."""
    assert len(_FICHIERS) > 20, f"{len(_FICHIERS)} fiche(s) trouvée(s) dans {_DIR}"


@pytest.mark.parametrize("fichier", _FICHIERS, ids=lambda f: f.stem)
def test_chaque_titre_de_section_est_parsable(fichier: pathlib.Path):
    mauvais = [
        (n, ligne) for n, ligne in enumerate(
            fichier.read_text(encoding="utf-8").splitlines(), start=1)
        if ligne.startswith("## ") and not _VALIDE.match(ligne)
    ]
    assert not mauvais, (
        f"{fichier.name} : titre(s) de section que le parseur n'accepte pas — "
        f"{['l.%d %s' % (n, l) for n, l in mauvais]}\n"
        f"Forme attendue : `## <kind> — <titre>` avec kind ∈ {', '.join(KINDS)}. "
        "Une autre forme n'est pas refusée : elle est AVALÉE dans le corps de la "
        "section précédente, et s'affiche sous son intitulé.")
