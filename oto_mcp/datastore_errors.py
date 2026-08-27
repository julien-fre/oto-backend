"""Les refus du datastore — un vocabulaire d'erreurs, sans dépendance.

Extrait du store (#325), déplacement pur. Ce module ne connaît rien : c'est ce qui lui
permet d'être importé de partout, y compris par les modules que le store COMPOSE. Sans
lui, chacun d'eux devait remonter vers le store pour lever une erreur — donc un cycle,
donc un import local par fonction.

Le point commun de toutes ces classes : **un refus porte de quoi agir**. Les champs
fautifs, le titulaire d'un bail et sa date d'expiration, la façon de le libérer. Un
refus qui dit seulement « non » fait deviner, et on l'a payé en production : le refus
d'écriture sur une ligne réservée ressortait en « erreur interne », alors que dire qui
la tenait était l'objet même du mécanisme.
"""
from __future__ import annotations

from typing import Any, Optional


class RowValidationError(ValueError):
    """Écriture refusée par le schéma strict / le cycle de vie (ADR 0046 B/C).
    Le message liste les champs fautifs — actionnable, jamais un refus muet.

    `row` = la DÉSIGNATION de la ligne fautive, quand le geste en visait plusieurs
    (#412). Un lot de 200 lignes qui refuse en nommant le champ et la valeur, mais
    pas la ligne, fait chercher à la main dans un fichier client de 8 910 lignes :
    le coût n'est pas les lignes non écrites, c'est le temps de trouver la fautive.
    Le store la connaît — il valide ligne par ligne — l'information existait et ne
    sortait pas."""

    def __init__(self, errors: list[str], *, row: Optional[str] = None):
        self.errors = errors
        self.row = row
        tete = "écriture refusée par le schéma"
        if row:
            tete += f" · {row}"
        super().__init__(tete + " : " + " ; ".join(errors))


class InvalidCursor(ValueError):
    """Curseur de pagination illisible (mal formé / tronqué)."""


class NamespaceNotFound(Exception):
    pass


class RowNotFound(Exception):
    pass


class NamespaceExists(Exception):
    pass


class NamespaceReadOnly(Exception):
    """Écriture tentée sur un namespace partagé en lecture seule."""
    pass


class NamespaceForbidden(Exception):
    """Action de gouvernance (supprimer/transférer) tentée sans droit de gouvernance."""
    pass


class RowLocked(Exception):
    """Écriture refusée sur une ligne sous bail ACTIF d'un autre (#317).

    Le bail protégeait l'ATTRIBUTION, pas la donnée : deux agents ne prenaient pas la
    même ligne, mais rien n'empêchait le second d'écrire dessus. « Verrou natif » veut
    dire que la ligne réservée est aussi protégée en écriture.

    ⚠️ Porte de quoi SORTIR, pas seulement de quoi comprendre : qui tient, jusqu'à
    quand, et le geste — libérer explicitement, puis écrire. Sans la sortie, on
    remplace un silence par un mur."""

    def __init__(self, row_id: str, claimed_by: Any = None, claimed_until: Any = None):
        self.row_id = row_id
        self.claimed_by = claimed_by
        self.claimed_until = claimed_until
        super().__init__(
            f"ligne « {row_id} » réservée par « {claimed_by} » jusqu'à "
            f"{claimed_until} — écriture refusée. Si le travail est terminé ou "
            f"l'agent abandonné, libère la ligne (data_release), puis écris.")


class RowClaimed(Exception):
    """Row nommée déjà sous bail ACTIF d'un autre worker (ADR 0046 D).

    Le conflit qu'il faut rendre visible : deux personnes qui prennent la même
    ligne à la même seconde, l'une des deux doit l'apprendre. Porte le bail en
    place pour que la surface dise QUI la tient et jusqu'à QUAND."""

    def __init__(self, row_id: str, claimed_by: Any = None, claimed_until: Any = None):
        self.row_id = row_id
        self.claimed_by = claimed_by
        self.claimed_until = claimed_until
        super().__init__(f"row {row_id} sous bail de {claimed_by!r} jusqu'à {claimed_until!r}")
