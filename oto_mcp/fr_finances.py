"""Honnêteté du bloc `finances` de l'API Recherche Entreprises (#399).

L'amont sert `finances[année] = {ca, resultat_net}` **sans unité** et **sans
distinguer zéro de non-déclaré**. Les deux manques sont invisibles : le nombre est
toujours plausible pour une petite structure. Vérifié le 12/08/2026 :

- **L'unité varie, y compris d'un exercice à l'autre pour UNE MÊME entreprise.**
  Banque Populaire Val de France (549800373), dépôts consolidés : `FL` = 414 991 000
  en 2021 et 422 562 000 en 2022 (des euros), puis **392 287 en 2024** (des milliers,
  soit 392 M€). Michelin (855200507) fait l'inverse : euros jusqu'en 2018, milliers
  ensuite. Aucune règle par entreprise, par secteur ou par type de bilan ne récupère
  donc l'unité — et personne en amont ne la transmet (ni le payload de l'API, ni le
  parquet des bilans, dont le schéma est `siren, date_cloture_exercice, type_bilan,
  confidentiality, liasse`). **On ne devine pas : on signale.**

- **`ca = 0` code l'absence, pas un chiffre d'affaires nul.** Norauto France
  (480470152) sort à `ca: 0` alors que son dépôt 2023 porte 974 718 176 €.

Ce module ne CORRIGE aucun montant — une conversion fausse serait indétectable en
aval, ce qui est pire que l'absence. Il retire ce qui n'est pas une donnée (le 0, la
valeur négative) et marque ce qui ne peut pas être lu comme des euros.
"""
from __future__ import annotations

from typing import Any, Optional

# Plancher de salariés par code INSEE TEFEN (borne BASSE de la tranche). Sert au seul
# test de vraisemblance ci-dessous, donc la borne basse est la lecture prudente : elle
# maximise le CA par salarié et rend l'alerte plus difficile à déclencher.
# `NN` (non renseigné) et l'absence de code ⟹ aucun contrôle possible.
_TEFEN_FLOOR: dict[str, int] = {
    "00": 0, "01": 1, "02": 3, "03": 6, "11": 10, "12": 20,
    "21": 50, "22": 100, "31": 200, "32": 250, "41": 500, "42": 1000,
    "51": 2000, "52": 5000, "53": 10000,
}

# Sous ce seuil, le montant ne se lit pas comme le chiffre d'affaires en euros d'une
# entreprise qui emploie ce monde-là. Volontairement très bas — on cherche l'anomalie
# de facteur 1000, pas la faible productivité. Marge mesurée sur des cas sains :
# Michelin sort à 557 076 €/salarié (55× au-dessus), la banque à 196 €/salarié.
#
# ⚠️ Le seuil dit « invraisemblable », PAS « libellé en milliers ». Vérifié sur un
# échantillon de 311 entreprises de 50+ salariés : les deux cas marqués étaient une
# association (WIMOOV, 200+ salariés — ses ressources sont des subventions, qui ne
# sont pas du CA) et une holding portant les salariés d'un groupe. L'unité n'est
# qu'une des causes possibles ; l'affirmer serait inventer. Ce qu'on sait est
# suffisant pour décider : ce montant ne peut pas être affiché comme le CA.
_MIN_CA_PER_EMPLOYEE = 10_000

AVERTISSEMENT = (
    "Bloc `finances` : l'amont ne transmet PAS l'unité du dépôt (certaines "
    "entreprises déposent en euros, d'autres en milliers — parfois d'une année sur "
    "l'autre chez la même) et code l'absence de CA par un 0. Les années marquées "
    "`alerte` ne doivent pas être affichées comme un chiffre d'affaires en euros : "
    "`ca_non_declare` = l'amont n'a pas la donnée (le 0 n'est pas un CA nul) ; "
    "`ca_valeur_aberrante` = montant négatif, illisible ; `ca_invraisemblable` = le "
    "montant ne tient pas face à l'effectif (`ca_par_salarie` donne le ratio) — "
    "cause possible mais NON établie : dépôt en milliers, association vivant de "
    "subventions, ou holding portant les salariés. Pour un montant sûr, lire le "
    "dépôt lui-même via `fr_bilans` / `fr_bilan` (qui exposent `type_bilan` et les "
    "postes bruts de la liasse)."
)


def _annotate_year(entry: Any, floor: Optional[int]) -> tuple[dict, list[str]]:
    """Un bloc `{ca, resultat_net}` → (bloc annoté, alertes). Ne convertit rien."""
    if not isinstance(entry, dict):
        return entry, []
    out = dict(entry)
    alertes: list[str] = []
    ca = out.get("ca")

    if not isinstance(ca, (int, float)):
        return out, alertes

    if ca == 0:
        # 0 n'est pas un chiffre d'affaires nul : c'est « pas de donnée » chez
        # l'amont. Le rendre tel quel ferait afficher « 0 € » pour une entreprise
        # dont le CA existe et se compte en centaines de millions.
        out["ca"] = None
        alertes.append("ca_non_declare")
    elif ca < 0:
        # Un CA négatif n'a pas de sens comme produit d'exploitation (vu :
        # -1 002 180 648 chez Safran Nacelles). On ne sait pas le lire.
        out["ca"] = None
        out["ca_valeur_amont"] = ca
        alertes.append("ca_valeur_aberrante")
    elif floor and ca / floor < _MIN_CA_PER_EMPLOYEE:
        # Le montant reste servi — il est réel, c'est sa LECTURE qui ne tient pas.
        # On rend le ratio qui a déclenché l'alerte pour que l'appelant juge
        # lui-même plutôt que de croire un verdict.
        out["ca_par_salarie"] = round(ca / floor)
        alertes.append("ca_invraisemblable")

    if alertes:
        out["alerte"] = alertes
    return out, alertes


def annotate(finances: Any, tranche_effectif: Optional[str]) -> tuple[Any, Optional[str]]:
    """`(finances, tranche_effectif)` → `(finances annotées, avertissement | None)`.

    L'avertissement n'est rendu que si au moins une année porte une alerte : une
    fiche saine ne se voit pas alourdie d'un paragraphe qui ne la concerne pas.
    """
    if not isinstance(finances, dict) or not finances:
        return finances, None
    floor = _TEFEN_FLOOR.get(tranche_effectif or "")
    out: dict[str, Any] = {}
    touched = False
    for annee, entry in finances.items():
        out[annee], alertes = _annotate_year(entry, floor)
        touched = touched or bool(alertes)
    return out, (AVERTISSEMENT if touched else None)


FILTRE_CA_AVERTISSEMENT = (
    "⚠️ `ca_min`/`ca_max` filtrent en amont sur un nombre dont l'unité est INCONNUE "
    "(euros pour les uns, milliers pour les autres) et dont le 0 signifie « non "
    "déclaré ». Conséquences mesurées : la plage laisse passer les entreprises SANS "
    "CA connu (elles valent 0, donc ≤ toute borne haute) et rate celles qui ont "
    "déposé en milliers. Sur `tranche_effectif_salarie=51,52,53 & ca_max=400000`, "
    "les 12 résultats sont des grandes entreprises — 11 n'y sont que par leur 0, et "
    "la 12ᵉ est une banque à 392 M€ lue comme 392 k€. Pour qualifier par taille, "
    "préférer `tranche_effectif_salarie` ou `categorie_entreprise`, et ne conclure "
    "sur un CA qu'après lecture du dépôt (`fr_bilans`)."
)
