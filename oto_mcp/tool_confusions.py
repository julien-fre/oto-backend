"""Un refus qui reconnaît la forme d'un AUTRE outil du même domaine le nomme
(oto-backend#585) — plutôt qu'un refus générique qui ne dit que « champ requis
absent », vrai mais muet sur la vraie faute.

Observé le 29/08/2026 : `data_write({"op": "list"})` — la forme de `data_rows`/des
outils consolidés (ADR 0047, `op` en paramètre), envoyée à `data_write`, qui n'a
jamais eu de paramètre `op`. Le refus disait « namespace : champ requis absent »,
qui ne nomme pas la confusion.

**Une phrase, pas une grammaire** (contrainte du signal d'origine) : chaque entrée
est un cas RÉELLEMENT observé, datée, jamais une règle générale déduite des noms de
paramètres — la liste ne grossit que par un incident, pas par anticipation.
"""
from __future__ import annotations

from typing import Optional

# `(outil, clé inconnue reçue)` -> le message qui nomme la confusion. Une entrée par
# cas OBSERVÉ, datée — jamais une déduction générale sur la forme des noms.
CONFUSIONS_DE_FORME: dict[tuple[str, str], str] = {
    ("data_write", "op"): (
        "`data_write` n'a pas de paramètre `op` — c'est la forme des outils "
        "consolidés (ADR 0047, ex. `lucca_employee`) ou de `data_rows` "
        "(lecture). Pour LISTER des lignes, c'est `data_rows(datastore=…)` ; "
        "`data_write` sert à ÉCRIRE (`row=` ou `rows=`)."
    ),
}


def refus_forme_dun_autre_outil(outil: Optional[str], cle: str) -> Optional[str]:
    """Le refus qui nomme la confusion — `None` si ce couple (outil, clé) n'est pas
    un cas connu (le refus générique de `_arg_error_message` s'applique alors)."""
    if outil is None:
        return None
    return CONFUSIONS_DE_FORME.get((outil, cle))
