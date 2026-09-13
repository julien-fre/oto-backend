"""Consommation → Coûts : ce qu'un poste de jetons VAUT, et ce qu'il compte au budget.

Deux mesures, jamais confondues :

- la **Consommation** = les jetons, poste par poste, tels que le worker les déclare
  (`usage_input`, `usage_output`, `usage_cache_read`, `usage_cache_write`) ;
- les **Coûts** = un montant en nano-dollars ENTIERS (10⁻⁹ USD), calculé sur ces
  POSTES et jamais sur l'unité de budget `U` (contrat runner, §3).

⚠️ **Le montant se fige à l'écriture, avec le nom de son barème.** Tarifer à la
lecture réécrirait le passé au premier changement de prix. Un barème s'AJOUTE, il ne
se modifie jamais.

⚠️ **Un prix PROVISOIRE n'est pas un prix ABSENT.**
- provisoire : le barème porte un prix que personne n'a encore vérifié. Le montant est
  CALCULÉ, et il porte le marqueur `price_unverified` (motif `usage_price_unverified`) ;
- absent : aucun prix (poste non sourcé), poste requis inconnu, modèle sans tarif. Le
  montant est `None` et la RAISON est rendue. Jamais un zéro, qui se lirait « gratuit ».

⚠️ **Un ZÉRO ATTESTÉ vaut zéro, quelle que soit la famille** (contrat §3) : tous les
postes reçus à 0, et attestés. Zéro jeton coûte zéro dans tout barème — c'est le seul
cas où une famille inconnue ne rend pas `unpriced`.

⚠️ **Ce fichier ne sait rien de la facturation** : ni marge, ni remise, ni crédit. Il
convertit des postes en dollars au tarif PUBLIC, rien d'autre ; sur la clé d'une org,
le montant est une estimation (nous ne voyons pas son tarif négocié).

Pur : aucune dépendance, pour que la base comme les capacités puissent le lire.
"""
from __future__ import annotations

import datetime
from typing import Mapping, NamedTuple, Optional

#: Les quatre postes TARIFABLES. `usage_input_total` (entrée totale, cache compris)
#: n'en fait pas partie : c'est un MAJORANT, il borne, il ne se publie jamais comme un
#: montant exact.
POSTES = ("usage_input", "usage_output", "usage_cache_read", "usage_cache_write")
#: Tous les postes REÇUS — ceux qu'un zéro attesté doit porter, majorant compris.
POSTES_RECUS = POSTES + ("usage_input_total",)

# Les raisons d'un montant ABSENT. Ce sont des CODES servis : ils ne bougent pas.
FAMILLE_INCONNUE = "unknown_provider_family"
POSTE_INCONNU = "required_usage_unknown"
MODELE_ABSENT = "no_model"
MODELE_NON_TARIFE = "model_not_priced"
PRIX_ABSENT = "usage_price_missing"
AUCUN_BAREME = "no_price_list_for_date"
RAISONS = (FAMILLE_INCONNUE, POSTE_INCONNU, MODELE_ABSENT, MODELE_NON_TARIFE,
           PRIX_ABSENT, AUCUN_BAREME)

#: Le MOTIF du marqueur `price_unverified` : un montant calculé au prix provisoire d'un
#: barème non vérifié. Ce n'est PAS une raison d'absence — le montant existe.
PRIX_NON_VERIFIE = "usage_price_unverified"


class Prix(NamedTuple):
    """Nano-dollars PAR JETON, un prix par poste. `None` = prix ABSENT (non sourcé) :
    un poste non nul ne se tarife pas (un poste à zéro reste tarifable, zéro fois rien).
    `verifie=False` = prix PROVISOIRE : les montants se calculent et se marquent."""
    usage_input: int
    usage_output: int
    usage_cache_read: Optional[int]
    usage_cache_write: Optional[int]
    verifie: bool = True


def _anthropic(entree: int, sortie: int) -> Prix:
    """Grille publique Anthropic : lecture de cache 0,1× l'entrée, écriture 1,25×.

    ⚠️ 1,25× vaut pour le TTL de cache par défaut (5 minutes) — celui que le runner
    pose (`cache_control: ephemeral`). Une écriture à TTL d'une heure coûte davantage
    et serait sous-estimée ici : le worker ne distingue pas les deux aujourd'hui."""
    return Prix(entree, sortie, entree // 10, entree * 5 // 4)


#: Du plus ancien au plus récent, chacun daté de son ENTRÉE EN VIGUEUR. Un travail est
#: tarifé au barème en vigueur le jour où son montant s'écrit.
BAREMES: tuple[tuple[str, dict[str, Prix]], ...] = (
    ("2026-09-13", {
        # Tarif public de l'API première partie (grille relevée le 24/06/2026).
        # Bedrock et Vertex sont tarifés séparément ; aucun worker n'y est servi.
        "claude-opus-5": _anthropic(5_000, 25_000),
        "claude-sonnet-5": _anthropic(2_000, 10_000),
        "claude-haiku-4-5": _anthropic(1_000, 5_000),
        # PROVISOIRE : tarif public de la gamme « Mistral Large », 0,50 $ / 1,50 $ par
        # million — la page ne le décline pas par version, `2512` n'est pas vérifié
        # nommément. Les montants se calculent et portent `price_unverified`.
        # ⚠️ Cache lu ABSENT : aucun prix sourcé. Aucun 0,1× dérivé.
        "mistral-large-2512": Prix(500, 1_500, None, None, verifie=False),
    }),
)

#: Le barème le plus récent — ce qu'un écran affiche comme « tarif courant ».
BAREME_COURANT = BAREMES[-1][0]

# Les postes qu'une famille REQUIERT pour un montant, et ceux qui composent `U`.
# ⚠️ OpenAI-compatible, famille `mistral` seule : aucun compteur d'écriture de cache —
# les écritures sont dans `prompt_tokens`. `usage_cache_write` y est SANS OBJET, pas
# inconnu : ni requis ni compté. Le cache lu, lui, est requis : `usage_input` n'est le
# non-caché que si le cache lu est connu. Un hôte OpenAI-compatible sans famille nommée
# n'a PAS de règle ici : il sort `unknown_provider_family`.
_REQUIS: dict[str, tuple[str, ...]] = {
    "anthropic": POSTES,
    "mistral": ("usage_input", "usage_output", "usage_cache_read"),
}
_BUDGET: dict[str, tuple[str, ...]] = {
    "anthropic": ("usage_input", "usage_cache_write", "usage_output"),
    "mistral": ("usage_input", "usage_output"),
}

#: Les familles qui savent se tarifer ET se compter au budget.
FAMILLES_CONNUES = tuple(_REQUIS)


class Tarif(NamedTuple):
    nano_usd: Optional[int]
    bareme: Optional[str]
    raison: Optional[str]
    #: Vrai quand le montant EXISTE mais repose sur un prix provisoire.
    prix_non_verifie: bool = False


def bareme_en_vigueur(le: Optional[datetime.date] = None
                      ) -> Optional[tuple[str, dict[str, Prix]]]:
    """Le barème applicable à cette date (défaut : aujourd'hui), ou `None`.

    Avant le premier barème : `None`, et le montant le dira. Les prix d'avant ne sont
    pas connus de ce dépôt ; en inventer produirait un montant faux qui aurait
    l'apparence de l'exactitude."""
    jour = (le or datetime.date.today()).isoformat()
    retenu = None
    for entree in BAREMES:
        if entree[0] <= jour:
            retenu = entree
    return retenu


def _zero_atteste(postes: Mapping[str, Optional[int]], atteste: bool) -> bool:
    """Tous les postes REÇUS valent 0 — pas NULL, pas absents — et la couverture les
    atteste. Rien d'autre ne vaut zéro par construction."""
    return atteste and all(
        isinstance(postes.get(p), int) and not isinstance(postes.get(p), bool)
        and postes.get(p) == 0 for p in POSTES_RECUS)


def tarifer(famille: Optional[str], modele: Optional[str],
            postes: Mapping[str, Optional[int]],
            le: Optional[datetime.date] = None, *, atteste: bool = False) -> Tarif:
    """Le montant d'UNE tentative, calculé sur ses postes — ou la raison de son absence.

    L'ordre des raisons va du plus structurel au plus ponctuel : une famille inconnue
    ne se répare pas comme un poste manquant. Le zéro attesté passe avant tout."""
    if _zero_atteste(postes, atteste):
        bareme = bareme_en_vigueur(le)
        return Tarif(0, bareme[0] if bareme else None, None)
    requis = _REQUIS.get(famille or "")
    if requis is None:
        return Tarif(None, None, FAMILLE_INCONNUE)
    if any(postes.get(p) is None for p in requis):
        return Tarif(None, None, POSTE_INCONNU)
    if not modele:
        return Tarif(None, None, MODELE_ABSENT)
    bareme = bareme_en_vigueur(le)
    if bareme is None:
        return Tarif(None, None, AUCUN_BAREME)
    prix = bareme[1].get(modele)
    if prix is None:
        return Tarif(None, None, MODELE_NON_TARIFE)
    total = 0
    for p in requis:
        n = int(postes[p])
        unitaire = getattr(prix, p)
        if unitaire is None:
            if n:
                return Tarif(None, None, PRIX_ABSENT)
            continue
        total += n * unitaire
    return Tarif(total, bareme[0], None, not prix.verifie)


def unite_budget(famille: Optional[str], postes: Mapping[str, Optional[int]],
                 *, atteste: bool = False) -> Optional[int]:
    """`U`, l'unité de BUDGET (oto#197) — jamais une base de tarif.

    Anthropic : entrée non cachée + cache écrit + sortie. Famille `mistral` : entrée non
    cachée + sortie. Zéro attesté : 0, quelle que soit la famille. `None` dès qu'un
    poste qui la compose est inconnu : jamais un zéro fabriqué."""
    if _zero_atteste(postes, atteste):
        return 0
    compose = _BUDGET.get(famille or "")
    if compose is None or any(postes.get(p) is None for p in compose):
        return None
    return sum(int(postes[p]) for p in compose)


def unite_budget_sql() -> str:
    """La même `U`, en expression SQL sur les colonnes de `runner_job_attempts`.

    Dérivée des mêmes tables que `unite_budget` : une seule définition de la règle, et
    l'addition SQL propage NULL exactement comme la version Python rend `None`."""
    zero = " AND ".join(f"{p} = 0" for p in POSTES_RECUS)
    branches = " ".join(
        f"WHEN '{famille}' THEN " + " + ".join(compose)
        for famille, compose in _BUDGET.items())
    return (f"(CASE WHEN usage_couverture IS NOT NULL AND {zero} THEN 0 "
            f"ELSE (CASE provider_family {branches} END) END)")


def en_dollars(nano_usd: Optional[int]) -> Optional[float]:
    """Le montant en dollars, pour un écran. ⚠️ Affichage SEULEMENT : ce qui se somme,
    se compare ou se facture reste en nano-dollars entiers."""
    return None if nano_usd is None else nano_usd / 1e9
