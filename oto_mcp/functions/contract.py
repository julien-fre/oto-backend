"""Ce qu'une version de fonction a le droit de contenir — validé AVANT d'être stocké.

Une seule définition, lue par la capacité (qui refuse à l'écriture) et par l'exécuteur
(qui ne charge que ce qu'elle autorise). Deux lectures qui divergeraient laisseraient
passer à l'écriture une version que l'exécution refuserait, et l'on ne l'apprendrait
qu'au premier appel.
"""
from __future__ import annotations

import re

# La référence lisible d'une fonction, citée par les procédures.
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")
# Un module Python importable, à plat : pas de dossier, pas de nom réservé caché.
_MODULE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\.py$")
# Une donnée lue par le code ou par ses tests (cas de référence, tables de constantes).
_DONNEE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*\.json$")
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

MAX_FICHIERS = 50
MAX_OCTETS = 512 * 1024

# Les dépendances que l'exécuteur sait fournir SANS réseau : des roues Python pures,
# embarquées avec lui. Une dépendance hors liste est refusée à l'écriture, pas à
# l'exécution. `et_xmlfile` n'y figure pas : c'est une dépendance d'`openpyxl`, jamais
# une demande d'auteur.
DEPENDANCES = frozenset({"openpyxl"})


class VersionInvalide(ValueError):
    """Une version refusée à l'écriture. Le message dit quoi corriger."""


def valider(sources: dict, entrypoint: str, requirements: list[str]) -> None:
    """Refuse une version qui ne pourrait pas s'exécuter, en nommant la raison."""
    if not isinstance(sources, dict) or not sources:
        raise VersionInvalide("`sources` doit être un objet {nom de fichier: contenu}, non vide.")
    if len(sources) > MAX_FICHIERS:
        raise VersionInvalide(f"{len(sources)} fichiers : au plus {MAX_FICHIERS}.")
    total = 0
    for nom, contenu in sources.items():
        if not (_MODULE.match(nom) or _DONNEE.match(nom)):
            raise VersionInvalide(
                f"fichier `{nom}` : seuls des modules `.py` et des données `.json`, "
                "à plat (sans dossier), sont acceptés.")
        if not isinstance(contenu, str):
            raise VersionInvalide(f"fichier `{nom}` : le contenu doit être du texte.")
        total += len(contenu.encode())
    if total > MAX_OCTETS:
        raise VersionInvalide(f"{total} octets de sources : au plus {MAX_OCTETS}.")

    module, sep, fonction = (entrypoint or "").partition(":")
    if not sep or not _IDENT.match(module) or not _IDENT.match(fonction):
        raise VersionInvalide("`entrypoint` s'écrit `module:fonction` (ex. `devis:generer`).")
    if f"{module}.py" not in sources:
        raise VersionInvalide(f"`entrypoint` vise le module `{module}`, absent des sources.")

    hors_liste = sorted(set(requirements or []) - DEPENDANCES)
    if hors_liste:
        raise VersionInvalide(
            f"dépendances non disponibles : {', '.join(hors_liste)} "
            f"(disponibles : {', '.join(sorted(DEPENDANCES))}).")
