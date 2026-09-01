"""Le venv exécute-t-il l'oto-core que CE tronc épingle ?

`pip` **ne réinstalle pas** une dépendance VCS déjà présente. Un venv partagé
dérive donc en silence : il garde le tag d'oto-core installé la première fois,
pendant que `pyproject.toml` avance. Rien ne s'en plaint — et la suite se met à
rendre des rouges qui **décrivent fidèlement le venv** et **accusent le dépôt**.

Le 01/09/2026, ce mode de panne a coûté **sept** enquêtes dans la journée, dont
une remontée en « le tronc est rouge, plus aucune PR ne peut entrer » alors que
la CI était verte de bout en bout. `docs/commands.md` §Pin oto-core décrivait
déjà le piège, nommait les fichiers, citait les messages d'assertion mot pour
mot : **la doc était juste, et le détour s'est reproduit quand même.** D'où ce
module — un forçage, pas une phrase de plus.

**Pourquoi les rouges trompent.** Ils sont écrits pour la CI, où oto-core est
installé AU tag : là-bas leur accusation est exacte. En local sur un venv en
retard, le même message désigne la mauvaise pièce — « ta liste d'exceptions »,
« ton pin » — alors que ce qui manque est **le client**.

**Et pourquoi la moitié d'entre eux échappent au discriminant connu.**
`ModuleNotFoundError: No module named 'oto.tools.<x>'` n'attrape qu'un connecteur
**ajouté** après la version installée (`tally`). Un connecteur **déjà présent
mais rabougri** (`lemlist` : 724 lignes en v1.101.0, 2547 en v1.102.0) échoue tout
autrement — « méthodes appelées mais absentes du client », « exception(s) sur un
paramètre inexistant ». Ceux-là ressemblent trait pour trait à une vraie
régression de version-skew, et c'est exactement ceux qu'on met de côté comme
« les vrais, distincts des faux rouges ».

**La coordonnée qu'on lit.** Le tag installé se lit dans `direct_url.json`
(PEP 610), via `oto_mcp.version.oto_core()` — source unique, déjà servie par
`/api/version`. ⚠️ `pip show oto-core` et le nom du `dist-info` **mentent** ici :
le champ `version` d'oto-core est gelé à `1.100.0` depuis que les tags ont cessé
de le bumper, donc l'instrument affiche le même numéro pour l'installé périmé et
pour le bon.

**Ce qu'on refuse de faire.** Crier quand on ne PEUT pas mesurer. Une
installation depuis PyPI n'a pas de `direct_url.json` : il ne reste que le numéro
gelé, qui n'est pas un tag et différerait *toujours* du pin. Comparer là serait
fabriquer une alarme permanente — donc on se tait, ce qui est la vérité (cf.
`version.py` : quand on ne sait pas, on le DIT).
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import NamedTuple

RACINE = Path(__file__).resolve().parent.parent
PYPROJECT = RACINE / "pyproject.toml"

#: Le marqueur dont un test se réclame quand il n'a de SENS que face à l'oto-core
#: épinglé. C'est le test qui se déclare — jamais une liste de noms tenue ici, qui
#: rouillerait au premier fichier ajouté et sur-couvrirait au premier renommage.
MARQUEUR = "exige_pin_oto_core"

#: `oto-core[anonymize] @ git+https://github.com/otomata-tech/oto-core.git@v1.103.0`
_PIN = re.compile(r"oto-core\.git@(?P<tag>[0-9A-Za-z][0-9A-Za-z._\-]*)")

#: Renseignée par les lanceurs de CI (GitHub Actions pose `CI=true`). En CI, une
#: divergence doit rester ROUGE : c'est là que la garde version-skew protège la
#: prod, et un skip y serait une panne muette. Le silence local, lui, est un
#: service ; le silence en CI serait le trou qu'on croyait boucher.
ENV_CI = "CI"


class Ecart(NamedTuple):
    """Un écart mesuré entre ce qui est installé et ce qui est épinglé."""
    installe: str | None      # None = oto-core absent du venv
    epingle: str
    source: str               # d'où vient la coordonnée installée


def tag_epingle(pyproject: Path | None = None) -> str | None:
    """Le tag qu'exige `pyproject.toml`, ou None si on ne peut pas trancher.

    Plusieurs pins DIFFÉRENTS dans le même manifeste (un extra qui diverge, une
    ligne laissée derrière) : on ne désigne pas un gagnant au hasard, on rend
    None — se taire vaut mieux que comparer à la mauvaise référence.
    """
    try:
        texte = (pyproject or PYPROJECT).read_text(encoding="utf-8")
    except OSError:
        return None
    tags = {m.group("tag") for m in _PIN.finditer(texte)}
    return tags.pop() if len(tags) == 1 else None


def etat_installe() -> dict:
    """Ce que le venv porte réellement. Délègue à la source unique du dépôt."""
    from oto_mcp.version import oto_core
    return oto_core()


def ecart(pyproject: Path | None = None, etat: dict | None = None) -> Ecart | None:
    """L'écart s'il est MESURABLE et RÉEL, sinon None.

    None couvre trois cas bien distincts, tous des non-alarmes :
    pas de pin lisible, coordonnée installée non comparable (PyPI), concordance.
    """
    epingle = tag_epingle(pyproject)
    if not epingle:
        return None
    etat = etat_installe() if etat is None else etat
    source = etat.get("source") or "unknown"
    if source == "absent":
        return Ecart(None, epingle, source)
    if source != "direct_url":
        # Numéro gelé, pas un tag : l'instrument ne peut pas voir l'écart.
        return None
    installe = etat.get("tag")
    if installe and installe == epingle:
        return None
    return Ecart(installe, epingle, source)


def lignes_de_banniere(e: Ecart, *, skips: int = 0) -> list[str]:
    """La bannière, en clair. Elle NOMME les deux versions — c'est le couple qui
    se comprend en une seconde, pas le mot « divergence »."""
    if e.installe is None:
        tete = "oto-core N'EST PAS INSTALLÉ dans ce venv"
        detail = ["  installé dans ce venv : AUCUN — aucun connecteur ne peut tourner",
                  f"  épinglé par pyproject : {e.epingle}"]
    else:
        tete = "oto-core INSTALLÉ ≠ oto-core ÉPINGLÉ — ce venv n'exécute pas ce tronc"
        detail = [f"  installé dans ce venv : {e.installe}",
                  f"  épinglé par pyproject : {e.epingle}"]
    corps = [
        "",
        "  Ce venv n'exécute pas le code que ce tronc épingle : `pip` ne réinstalle",
        "  jamais une dépendance VCS déjà présente. Les échecs sur les connecteurs",
        "  décrivent le venv, PAS le dépôt — et leur message accuse la mauvaise",
        "  pièce (« ta liste d'exceptions », « ton pin ») alors que c'est le CLIENT",
        "  qui manque. La CI, elle, installe AU tag : elle passe.",
        "",
        "  Rejouer sur le bon code, sans muter le venv partagé :",
        "    git clone …/oto-core.git \"$SP/core\" && git -C \"$SP/core\" checkout "
        f"{e.epingle}",
        "    PYTHONPATH=\"$SP/core\" .venv/bin/python -m pytest …",
        "",
        "  Détail et recette complète : docs/commands.md §Pin oto-core → « Faux rouge ».",
    ]
    if skips:
        corps.insert(0, f"  {skips} test(s) NON CONCLUANT(S) ici, donc passé(s) : "
                        f"ils n'ont de sens que face à {e.epingle}.")
        corps.insert(1, "")
    return [tete, *detail, *corps]


def skips_autorises() -> bool:
    """Local : un rouge qui ne prouve rien vaut moins qu'un test explicitement non
    concluant. CI : surtout pas — la garde version-skew doit y rester mordante."""
    return not os.environ.get(ENV_CI)
