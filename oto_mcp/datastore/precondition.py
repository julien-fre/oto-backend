"""La PRÉCONDITION de révision, côté appelant : `expected_revision` → l'entier comparé.

La révision elle-même est avancée par PostgreSQL (`db/revision.py`) ; ici vit la seule
chose que le code en fait au MOMENT d'un geste : lire ce que l'appelant a passé, et le
refuser s'il est illisible.

Sortie d'`ecriture_par_id.py` le 23/09/2026 (oto#217), quand la suppression et la
libération ont reçu la même précondition que le patch par `id`. Trois gestes, une seule
lecture du paramètre : deux copies divergeraient au premier assouplissement de forme, et
la plus permissive des deux déciderait alors ce que la protection vaut.
"""
from __future__ import annotations

from typing import Any, Optional


def revision_attendue(valeur: Any) -> Optional[int]:
    """`expected_revision` tel que reçu → l'entier comparé à `rev`, None s'il est omis.

    La révision est SERVIE en chaîne (`_revision`), c'est la forme attendue ; un entier
    désigne la même révision et passe aussi. Tout le reste est REFUSÉ en nommant la
    forme : une précondition illisible ne s'ignore pas, sinon le geste partirait sans
    la protection que l'appelant a demandée."""
    if valeur is None:
        return None
    texte = str(valeur).strip()
    if isinstance(valeur, bool) or not (texte.isascii() and texte.isdigit()):
        raise ValueError(
            f"`expected_revision` = la `_revision` servie avec la ligne que tu as lue "
            f"(une chaîne de chiffres, ex. \"3\") — reçu {valeur!r}. Rien n'est écrit.")
    return int(texte)
