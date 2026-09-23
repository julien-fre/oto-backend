"""Un contrôle qui ne peut pas s'exécuter faute de secret doit être ROUGE (#823).

`contrat-front-code` sortait VERT sur toute PR de fork : GitHub ne lui transmet pas
`OTO_FRONTEND_CONTRACT_KEY`, le job avertissait dans son journal et concluait `exit 0`.
Seule la conclusion est lue — un vert « pas jugé » fabrique une preuve positive.

Cliquet : dans tout workflow, une variable d'environnement liée à un `secrets.*`, dont
l'absence est testée par `if [ -z "${VAR…` , doit faire échouer le job dans cette
branche (`exit 1`), jamais le faire réussir (`exit 0`). Même règle que le job `cla`
(`cla.yml`), qui échoue sans son jeton.
"""
from __future__ import annotations

import pathlib
import re

_WORKFLOWS = pathlib.Path(__file__).resolve().parents[1] / ".github" / "workflows"
_LIE_A_UN_SECRET = re.compile(r"^\s*([A-Z_][A-Z0-9_]*):\s*\$\{\{\s*secrets\.[A-Za-z0-9_]+\s*\}\}\s*$")


def _branches_secret_absent(texte: str):
    """(variable, bloc) pour chaque `if [ -z "${VAR…` sur une variable liée à un secret ;
    le bloc court jusqu'au `fi` de même indentation."""
    lignes = texte.splitlines()
    variables = {m.group(1) for l in lignes if (m := _LIE_A_UN_SECRET.match(l))}
    for i, ligne in enumerate(lignes):
        m = re.match(r'^(\s*)if \[ -z "\$\{([A-Z_][A-Z0-9_]*)', ligne)
        if not m or m.group(2) not in variables:
            continue
        retrait = m.group(1)
        fin = next(j for j in range(i + 1, len(lignes)) if lignes[j] == f"{retrait}fi")
        yield m.group(2), "\n".join(lignes[i:fin + 1])


def test_le_cliquet_voit_les_branches_du_contrat_front():
    """Témoin : le balayage trouve bien les deux branches de `deploy-canari.yml` — un
    cliquet qui ne verrait rien serait vert pour rien."""
    texte = (_WORKFLOWS / "deploy-canari.yml").read_text()
    assert [v for v, _ in _branches_secret_absent(texte)] == ["CLE_LECTURE", "CLE_LECTURE"]


def test_un_secret_absent_ne_sort_jamais_vert():
    fautifs = []
    for wf in sorted(_WORKFLOWS.glob("*.y*ml")):
        for variable, bloc in _branches_secret_absent(wf.read_text()):
            if re.search(r"\bexit 0\b", bloc) or not re.search(r"\bexit 1\b", bloc):
                fautifs.append(f"{wf.name} — {variable} absent :\n{bloc}")
    assert not fautifs, (
        "un job SORT VERT (ou sans échec explicite) quand son secret manque — il n'a "
        "rien jugé et le dit seulement dans son journal (#823) :\n\n" + "\n\n".join(fautifs))
