"""La recherche `q` dans les lignes d'un tableau : ce qu'elle cherche, et où (#307).

**Ce qu'elle cherche.** Chaque MOT de `q` (séparés par des blancs) doit se retrouver
dans la ligne, dans n'importe quel ordre, de l'une des deux façons :
  - **par le sens** — lemmatisation `french`, accents et casse repliés : « cahiers »
    trouve « Le Cahier », « corp acme » trouve « ACME Corp » ;
  - **par fragment** — sous-chaîne littérale, accents et casse repliés : un bout de
    SIREN, d'URL ou de référence collé tel quel.

⚠️ **Le fragment n'est pas un repli, c'est la moitié du contrat.** Aucune tokenisation
ne retrouve « 52100 » dans « 552100554 », ni « acme.fr/con » dans une URL : une
recherche lexicale SEULE casserait cet usage sans un bruit — la ligne existe, la
recherche répond « rien ». Le banc le verrouille (`tests/datastore/test_recherche_q_307.py`).

**Où.** `q_scope` choisit le texte dans lequel on cherche :
  - `values` — les seules valeurs servies au lecteur : ni les clés, ni les couches
    (`origine`/`comment`/`link`), ni l'échappement JSON ;
  - `all` — la ligne stockée entière (`data::text`) : clés, valeurs ET couches. C'est ce
    qui retrouve une ligne par son commentaire de provenance.

Ce module est PUR : il valide et décrit, il ne touche à aucune base. Le SQL vit dans
`db/search.py` (même construction que la recherche transverse `oto_search`).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional, get_args

#: Le vocabulaire fermé de `q_scope`, lu par les deux faces (MCP et REST) : la
#: validation de forme y est faite par le type, et le refus nomme les valeurs admises.
PorteeRecherche = Literal["values", "all"]
PORTEES: tuple[str, ...] = get_args(PorteeRecherche)

#: La portée d'un appel qui ne la nomme pas. Lue ICI et nulle part ailleurs : une
#: bascule de défaut est un seul geste, et les textes servis la citent depuis ici.
PORTEE_DEFAUT: PorteeRecherche = "all"

DESCRIPTION_Q = (
    "free-text search over the row. Every WORD of `q` must be found, in any order, "
    "either by meaning (French stemming, accents and case folded: `cahiers` finds "
    "\"Le Cahier\", `corp acme` finds \"ACME Corp\") or as a literal FRAGMENT "
    "(a piece of a SIREN, an URL or a reference pasted as is). Without an explicit "
    "sort, best matches come first (rows matched by meaning, then rows matched only "
    "by fragment); an explicit sort stays yours. Where to search is `q_scope`.")

DESCRIPTION_Q_SCOPE = (
    "where `q` searches: `values` = only the values a reader is served (no column "
    "names, no layers — `origine`/`comment`/`link` — no JSON quoting); `all` = the "
    "whole stored row, names and layers included (finds a row by its `comment`). "
    f"Default `{PORTEE_DEFAUT}`. Any other value is refused.")


class PorteeInconnue(ValueError):
    """`q_scope` hors vocabulaire : refusée, jamais ramenée au défaut."""

    def __init__(self, recu) -> None:
        super().__init__(
            f"q_scope={recu!r} inconnu : valeurs admises "
            + ", ".join(f"`{p}`" for p in PORTEES) + f" (défaut `{PORTEE_DEFAUT}`).")


@dataclass(frozen=True)
class Recherche:
    """Une recherche validée : ses mots, et le texte où les chercher."""

    mots: tuple[str, ...]
    portee: PorteeRecherche


def recherche(q: Optional[str], q_scope: Optional[str] = None) -> Optional[Recherche]:
    """La recherche demandée, ou `None` quand `q` ne cherche rien.

    `q_scope` est validé MÊME sans `q` : une valeur inconnue est une faute de
    l'appelant, et la taire parce que `q` est vide la laisserait passer au prochain
    appel, celui qui cherche. Un `q` fait de blancs ne cherche rien — il filtrait
    jusqu'ici les lignes contenant ces blancs, ce que personne ne demande."""
    if q_scope is not None and q_scope not in PORTEES:
        raise PorteeInconnue(q_scope)
    mots = tuple((q or "").split())
    if not mots:
        return None
    return Recherche(mots=mots, portee=q_scope or PORTEE_DEFAUT)
