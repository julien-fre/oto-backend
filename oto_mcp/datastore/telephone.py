"""Le type `phone` : ce qu'est un numéro de téléphone pour le datastore (oto#103).

Arbitré le 24/09/2026 : la famille des types métier reste limitée à ceux dont le
rendu et la validation valent partout — `url`, `email`, et désormais `phone`. Le
reste (SIREN, IBAN, code postal…) se contraint par un `pattern` posé dans le schéma
du consommateur, jamais par un type de plus ici.

**Ce que la validation accepte, et pourquoi si large.** Un numéro s'écrit de dix
façons lisibles (`+33 6 12 34 56 78`, `0033 6-12-34-56-78`, `+33 (0)6 12 34 56 78`,
`06.12.34.56.78`) : refuser la mise en forme apprendrait seulement à la contourner.
On juge donc le numéro une fois ses séparateurs retirés — l'international E.164
(`+`, indicatif qui ne commence pas par 0, 15 chiffres au plus) ou un numéro national
en chiffres. Ce qui n'y passe pas est une phrase (« non trouvé »), un SIREN ou une
adresse : exactement ce que le type doit empêcher de se faire passer pour un numéro.

**La valeur n'est pas réécrite en base.** `normaliser` rend la forme compacte qu'un
consommateur peut dériver (lien d'appel, dédoublonnage), et c'est elle que la
validation juge ; la réécrire à l'écriture changerait l'identité de la valeur face à
la fusion (`_merge_column` compare au type près), et un même numéro remis dans une
autre mise en forme emporterait ses couches `comment`/`link`.
"""
from __future__ import annotations

import re
from typing import Any, Optional

#: Ce qui se retire avant de juger : espaces, points, tirets, barres, parenthèses.
_SEPARATEURS = re.compile(r"[\s.\-/()]")
#: `+33 (0)6…` : le zéro national entre parenthèses, qui n'appartient pas à l'E.164.
_ZERO_NATIONAL = re.compile(r"\(\s*0\s*\)")
_E164 = re.compile(r"^\+[1-9][0-9]{6,14}$")
_NATIONAL = re.compile(r"^[0-9]{6,15}$")


def normaliser(valeur: Any) -> Optional[str]:
    """La forme COMPACTE d'un numéro — `+33612345678` en international (E.164),
    `0612345678` en national — ou `None` si la valeur n'est pas un numéro.

    `00` en tête vaut `+` (préfixe international). Aucune devinette de pays : un
    numéro national reste national, faute de savoir d'où il vient."""
    if not isinstance(valeur, str):
        return None
    brut = valeur.strip()
    if brut.startswith(("+", "00")):
        brut = _ZERO_NATIONAL.sub("", brut)
    compact = _SEPARATEURS.sub("", brut)
    if compact.startswith("00"):
        compact = "+" + compact[2:]
    if _E164.match(compact) or _NATIONAL.match(compact):
        return compact
    return None


def est_un_numero(valeur: Any) -> bool:
    return normaliser(valeur) is not None
