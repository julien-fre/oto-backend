"""Dans quel ordre servir les campagnes candidates : lesquelles, et avec quel poids.

`db.campagne_a_servir` lit les campagnes ÉLIGIBLES (armées, sans travail en attente,
sous leur plafond de travaux) et tente de les verrouiller une à une. Ce module décide
de l'ordre de ces tentatives — la couche db ne connaît pas le datastore.

⚠️ **Deux règles, posées le 14/09/2026 à la demande d'une campagne en chaîne de passes**,
après un tirage à chance égale qui ignorait les files :

- **une campagne sans ligne réservable n'est pas servie** — sautée, jamais arrêtée :
  la plateforme ne juge pas qu'une passe est finie, une ligne peut y arriver dans la
  minute. Une campagne dont le compte a échoué ne l'est pas non plus. Une campagne qui
  ne vise aucun tableau reste servable : il n'y a rien à compter pour elle ;
- **le tirage est pondéré par la file** : P_i = α/K + (1 − α)·n_i/Σn, sur les K
  campagnes servables. Le terme α/K est le PLANCHER contre la famine : une file d'une
  ligne face à une file de mille garde sa chance.

**α = 0,2, choisi par le demandeur sur chiffres mesurés.** Au débit relevé ce jour-là
(~9 travaux distribués par minute), l'attente moyenne d'une campagne à très petite file
est bornée par K/(α·D) : ~5 min à K = 10 campagnes, contre ~1 min au tirage égal
(α = 1). En échange, une file qui porte 80 % des lignes reçoit ~70 % des tirages au lieu
d'un sur K — c'est ce qui débouche une passe goulot, où une ligne attend des heures.

L'ordre est tiré SANS REMISE (clés u^(1/p), Efraimidis–Spirakis) : la première place
suit exactement P, et si la tirée n'est plus verrouillable, la suivante l'est dans la
même loi — le verrouillage et la revérification de `campagne_a_servir` n'ont pas bougé.
"""
from __future__ import annotations

import random
from typing import Callable, Optional

from ._lignes_reservables import lignes_reservables

#: La part du tirage répartie également entre les campagnes servables.
PLANCHER = 0.2


def probabilites(lignes: dict[int, Optional[int]],
                 plancher: float = PLANCHER) -> dict[int, float]:
    """`{fleet_id: probabilité d'être tirée la première}` pour les campagnes SERVABLES.

    `None` (campagne sans tableau) ne pèse que par le plancher. Sans aucune ligne
    comptée nulle part (Σn = 0), le tirage est égal."""
    if not 0 < plancher <= 1:
        raise ValueError(f"le plancher doit être dans ]0, 1] (reçu {plancher!r})")
    if not lignes:
        return {}
    k = len(lignes)
    total = sum(n for n in lignes.values() if n)
    if not total:
        return {fid: 1 / k for fid in lignes}
    return {fid: plancher / k + (1 - plancher) * (n or 0) / total
            for fid, n in lignes.items()}


def ordre_pondere(probas: dict[int, float], rng: random.Random) -> list[int]:
    """Les campagnes dans l'ordre d'un tirage sans remise selon `probas`."""
    cles = {fid: rng.random() ** (1.0 / p) for fid, p in probas.items()}
    return sorted(cles, key=cles.__getitem__, reverse=True)


def ordonner(candidates: list[dict], *,
             compter: Callable[[list[dict]], dict] = lignes_reservables,
             rng: random.Random = random._inst) -> list[int]:
    """L'ordre dans lequel `campagne_a_servir` tente les candidates — les campagnes à
    file vide ou au compte échoué n'y figurent pas."""
    comptes = compter(candidates)
    servables = {}
    for c in candidates:
        fid = int(c["id"])
        if fid in comptes and comptes[fid] != 0:
            servables[fid] = comptes[fid]
    return ordre_pondere(probabilites(servables), rng)
