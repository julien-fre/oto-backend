"""Le dossier d'affichage — un libellé libre que les listes regroupent, porté par les
projets (`projects.folder`) et par les agents (`runner_triggers.folder`).

Une seule définition de ce qu'est un dossier valide : deux validations recopiées
finiraient par diverger (une borne relevée d'un côté, pas de l'autre), et un même
libellé serait accepté pour un agent et refusé pour un projet."""
from __future__ import annotations

from typing import Optional

from ._types import AuthzDenied

# Borné pour rester un libellé — une phrase n'est pas un nom de dossier.
FOLDER_MAX = 80


def dossier_valide(folder: Optional[str]) -> Optional[str]:
    """Le libellé à écrire : `None` = champ non fourni (rien à toucher) ; sinon le
    libellé rogné, `""` voulant dire « sortir du dossier » (l'appelant le stocke NULL).

    Refus nommé `folder_too_long` au-delà de `FOLDER_MAX`, AVANT toute écriture."""
    if folder is None:
        return None
    libelle = folder.strip()
    if len(libelle) > FOLDER_MAX:
        raise AuthzDenied(400, "folder_too_long",
                          f"`folder` est un libellé d'au plus {FOLDER_MAX} caractères, et "
                          "rien n'a été modifié.")
    return libelle
