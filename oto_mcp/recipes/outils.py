"""Les outils qu'une recette appelle AU-DELÀ de la lecture — deux listes fermées, tenues ici.

Le défaut reste la lecture (`tools/lecture`) : une recette `pull`, `for_each` ou
`per_row` simple n'appelle qu'un outil DÉCLARÉ en lecture. Deux usages en sortent, et
chacun est une liste nommée, pas une annotation qu'un outil se poserait :

- **Soumettre puis collecter** (`async`) : l'outil ne change rien de visible chez le
  fournisseur, mais il dépense des crédits (un enrichissement commandé). Chaque outil de
  soumission nomme l'outil qui en collecte le résultat.
- **Pousser** (`mode: push`) : créer ou mettre à jour une fiche chez le tiers (CRM,
  campagne). C'est un effet visible : chaque outil y vient avec SES ops permises — jamais
  `delete`, `merge` ni les `bulk_*`, et un outil multiplexé par `op` exige une `op`
  littérale. Un outil qui peut déclencher un envoi (une piste ajoutée à une campagne)
  exige en plus `allow_sending: true`.

⚠️ C'est une extension de la règle « une recette ne fait que lire » : ajouter un nom ici
se décide, et la garde `tests/test_recettes_outils.py` vérifie que chaque nom existe et
que ses ops sont des ops que l'outil connaît.
"""
from __future__ import annotations

from typing import Optional

#: Soumission → les outils qui en collectent le résultat.
SOUMISSIONS: dict[str, frozenset] = {
    "dropcontact_enrich": frozenset({"dropcontact_result"}),
    "fullenrich_enrich_linkedin": frozenset({"fullenrich_result"}),
    "apollo_reveal_phone": frozenset({"apollo_reveal_phone_result"}),
    "lemlist_enrich": frozenset({"lemlist_enrich_result"}),
}

#: Outil de poussée → ses ops permises (None : l'outil n'a pas d'`op`).
POUSSEES: dict[str, Optional[frozenset]] = {
    "hubspot_object": frozenset({"create", "update", "search"}),
    "folk_record": frozenset({"create", "update", "search"}),
    "attio_record": frozenset({"create", "update", "search"}),
    "pipedrive_record": frozenset({"create", "update"}),
    "salesforce_record": frozenset({"create", "update", "upsert"}),
    "lemlist_create_lead": None,
}

#: Les ops qui ÉCRIVENT (les autres ops permises lisent : la recherche d'un doublon).
OPS_ECRITURE = frozenset({"create", "update", "upsert"})

#: Les outils de poussée qui peuvent déclencher un ENVOI (une piste ajoutée à une
#: campagne active reçoit sa séquence) : `allow_sending: true` exigé.
ENVOIENT = frozenset({"lemlist_create_lead"})


def collecteurs(soumission: str) -> frozenset:
    return SOUMISSIONS.get(soumission, frozenset())


def op_permise(outil: str, op: object) -> bool:
    """`op` est-elle permise pour cet outil de poussée ? Un outil sans `op` n'en prend
    aucune ; un outil multiplexé exige une op de sa liste."""
    if outil not in POUSSEES:
        return False
    ops = POUSSEES[outil]
    if ops is None:
        return op is None
    return isinstance(op, str) and op in ops


def outils(corps: dict) -> set[str]:
    """TOUS les outils qu'une recette appelle — soumission, collecte, recherche, mise à
    jour. Ce qu'une garde par outil (la liste d'un agent hébergé) doit couvrir."""
    out = {corps.get("tool")}
    out.add(((corps.get("async") or {}).get("collect") or {}).get("tool"))
    for bloc in ("lookup", "update"):
        b = corps.get(bloc) or {}
        out.add(b.get("tool") or (corps.get("tool") if b else None))
    return {o for o in out if isinstance(o, str) and o}
