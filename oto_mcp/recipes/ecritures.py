"""Les outils qui ÉCRIVENT chez un tiers et qu'une recette-code peut appeler (#1202,
lot 3) — une liste FERMÉE, tenue ici.

Le défaut reste la lecture (`tools/lecture`) : un outil de connecteur qui n'est pas
déclaré en lecture est refusé au pont. Cette liste en sort quelques-uns, nommément,
chacun avec SES ops :

- `lire` : les ops qui ne changent rien chez le tiers (chercher un doublon avant de
  créer). Appelables sans déclaration, en épreuve comprise.
- `ecrire` : les ops qui créent ou mettent à jour une fiche. Chacune doit être DÉCLARÉE
  par la recette (`writes: ["folk_record:create", …]`) — c'est ce que la plateforme
  relit avant de publier —, jamais jouée en épreuve.

Jamais `delete`, `merge` ni une op `bulk_*` : une recette relancée repart du début, ses
écritures doivent être sans dégât. Un LOT d'une op permise (`items` / `ids` de l'outil,
50 fiches au plus) passe (`entries` pour la file d'Allo) : le pont le compte fiche par fiche dans `max_writes`
(`pont._fiches`). Un outil multiplexé par `op` exige une
`op` LITTÉRALE dans l'appel (son défaut est une lecture, jamais présumé). Un outil sans
`op` (`ops=None`) est une écriture en entier. Un outil qui peut déclencher un ENVOI (une
piste ajoutée à une campagne active reçoit sa séquence) exige en plus
`allow_sending: true`.

⚠️ C'est une extension de la règle « une recette ne fait que lire » : ajouter un nom ici
se décide, et `tests/test_recettes_ecritures.py` vérifie que chaque outil est servi, que
chaque op est une op qu'il connaît, et qu'aucune ne détruit.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Ecriture:
    #: Ops qui lisent (permises sans déclaration) et ops qui écrivent (à déclarer).
    #: `None` pour les deux : l'outil n'a pas d'`op`, il écrit.
    lire: Optional[frozenset] = None
    ecrire: Optional[frozenset] = None
    envoie: bool = False


ECRITURES: dict[str, Ecriture] = {
    "hubspot_object": Ecriture(frozenset({"search", "get"}), frozenset({"create", "update"})),
    "folk_record": Ecriture(frozenset({"search", "get"}),
                            frozenset({"create", "update", "add_to_group"})),
    "attio_record": Ecriture(frozenset({"search", "get"}), frozenset({"create", "update"})),
    "pipedrive_record": Ecriture(frozenset({"get"}), frozenset({"create", "update"})),
    "salesforce_record": Ecriture(frozenset({"get"}),
                                  frozenset({"create", "update", "upsert"})),
    "lemlist_create_lead": Ecriture(envoie=True),
    # Allo (#Klark, 22/10/2026, décidé par Julien) : la fiche CRM et la file du Power
    # Dialer, rien qui envoie (`send_sms` reste dehors) ni qui remplace une file
    # (`create_queue`). Ses lectures sont l'outil `allo`, déjà en lecture.
    # `add_queue_numbers` ne dédoublonne pas chez Allo : la recette lit la file avant.
    "allo_act": Ecriture(frozenset(), frozenset({"create_person", "update_person",
                                                 "create_company", "update_company",
                                                 "add_queue_numbers"})),
}

#: Ce qu'aucune op permise ne peut être : détruire, fusionner, une op `bulk_*` (un lot
#: passe par `items` / `ids` d'une op permise, compté par fiche au pont).
OPS_DESTRUCTRICES = frozenset({"delete", "merge", "bulk_create", "bulk_update",
                               "bulk_delete", "bulk_upsert"})


def nom_declare(outil: str, op: Optional[str]) -> str:
    """Le nom d'une écriture dans `writes` : `outil:op`, ou `outil` sans op."""
    return f"{outil}:{op}" if op else outil


def declarables() -> frozenset:
    """Tout ce qu'une recette peut déclarer dans `writes`."""
    out = set()
    for outil, e in ECRITURES.items():
        if e.ecrire is None:
            out.add(outil)
        else:
            out.update(nom_declare(outil, op) for op in e.ecrire)
    return frozenset(out)


def envoient(declarees) -> list[str]:
    """Les écritures déclarées qui peuvent déclencher un envoi."""
    return sorted(d for d in declarees
                  if ECRITURES.get(d.split(":", 1)[0], Ecriture()).envoie)


def juger(outil: str, arguments: dict) -> tuple[str, Optional[str]]:
    """`("lire" | "ecrire", nom déclaré)` pour un appel d'un outil de la liste, ou
    `("refus", message)`. L'outil DOIT être dans `ECRITURES`."""
    e = ECRITURES[outil]
    if e.ecrire is None:
        return "ecrire", nom_declare(outil, None)
    op = arguments.get("op")
    if not isinstance(op, str) or not op:
        return "refus", (f"`{outil}` reads or writes depending on its `op`: pass it "
                         f"literally (reads: {sorted(e.lire or ())}, writes: "
                         f"{sorted(e.ecrire)}).")
    if op in (e.lire or ()):
        return "lire", None
    if op in e.ecrire:
        return "ecrire", nom_declare(outil, op)
    return "refus", (f"op `{op}` of `{outil}` is not open to a recipe (reads: "
                     f"{sorted(e.lire or ())}, writes: {sorted(e.ecrire)}; never delete, "
                     "merge or a bulk_* op — batch a permitted op with `items` / `ids`).")
