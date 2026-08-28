"""Un `limit` d'entrée est BORNÉ avant de partir en `LIMIT %s` (#293).

`SearchInput.limit` l'était par un validateur qui écrête (`max(1, min(v, 50))`),
`PaymentsInput.limit` et `LibraryListInput.limit` ne l'étaient pas : la valeur du
client atteignait le SQL telle quelle. Deux dégâts, pas un — une valeur énorme
sérialise toute la table (avec un `ILIKE` sur `body_md` côté bibliothèque), une
valeur NÉGATIVE fait échouer Postgres (« LIMIT must not be negative ») et remonte
en 500 opaque, alors que c'est une entrée invalide.

Écrêter plutôt que refuser : c'est le patron déjà en place, et il ne casse aucun
appelant existant qui demande large.
"""
from __future__ import annotations

import pytest

from oto_mcp.capabilities.billing import PaymentsInput
from oto_mcp.capabilities.guide_library import LibraryListInput
from oto_mcp.capabilities.search import SearchInput

# (modèle, plafond attendu) — le plancher est 1 partout.
_BORNES = [
    (PaymentsInput, 100),
    (LibraryListInput, 200),
    (SearchInput, 50),      # la référence, déjà bornée : elle garde le patron vivant
]
_IDS = [m.__name__ for m, _ in _BORNES]


def _limit(model, value: int) -> int:
    extra = {"q": "x"} if model is SearchInput else {}
    return model.model_validate({"limit": value, **extra}).limit


@pytest.mark.parametrize("model,plafond", _BORNES, ids=_IDS)
def test_une_valeur_enorme_est_ecretee(model, plafond):
    assert _limit(model, 10**9) == plafond


@pytest.mark.parametrize("model,plafond", _BORNES, ids=_IDS)
def test_une_valeur_negative_ou_nulle_devient_un(model, plafond):
    """Sans plancher, `LIMIT -5` fait lever Postgres : une entrée invalide devient
    une erreur serveur."""
    assert _limit(model, -5) == 1
    assert _limit(model, 0) == 1


@pytest.mark.parametrize("model,plafond", _BORNES, ids=_IDS)
def test_une_valeur_raisonnable_passe_intacte(model, plafond):
    assert _limit(model, 7) == 7
    assert _limit(model, plafond) == plafond
