"""Ce que sont devenues les lignes d'une automatisation — pour qui la SUPERVISE (oto#77).

Mesuré sur une automatisation d'essai à trois lignes : onze travaux `done`, zéro
`failed`, zéro `abandoned` — et, relues au tableau, **une ligne enrichie, deux
abandonnées** après trois réservations sans écriture. Les compteurs de travaux
n'étaient pas en retard : ils mesuraient autre chose que ce que leur nom promettait.
Un travail qui rend sa ligne sans l'écrire s'arrête de lui-même, donc il est `done`.

**L'issue d'une ligne se lit au TABLEAU, jamais aux travaux** : par la valeur finale de
sa colonne de statut (`role="status"`, lue au schéma, jamais à son nom), ventilée telle
quelle, puis rangée selon le cycle de vie déclaré — terminale, état d'abandon, encore
ouverte. Le motif d'abandon est celui que la plateforme a posé (`abandon_reason`,
`db/rowabandon.py`) ; une ligne versée dans l'état d'abandon par une ÉCRITURE n'en a
pas, et c'est dit (`reason: null`) plutôt que confondu avec l'autre.

⚠️ **Le périmètre est le filtre de l'automatisation PRIVÉ de sa clause de statut** :
ce qui désigne ses lignes quel que soit l'état où elles ont fini — la clause de statut,
elle, ne retient que les lignes encore à traiter. Un filtre qui ne borne QUE le statut
laisse un périmètre vide : la ventilation porte alors sur tout le tableau, et `scope`
le DIT (`table`) au lieu de l'attribuer à l'automatisation. Même règle que le bilan du
runner (`oto_runner/bilan_postes.py`), qui a payé la même erreur le 06/09/2026.

⚠️ **Ce n'est pas une attribution par run.** Une ligne abandonnée perd son
`claimed_run` (`rowabandon`), et le journal des révisions ne porte pas encore de run
(oto#273, M2) : rien ne dit QUEL passage a fait une ligne. Le périmètre est ce qu'on sait
lire sans le deviner ; une ligne du périmètre déjà terminale avant l'automatisation y
figure aussi.

Jamais à l'agent qui travaille : comme `reservable_rows`, ce compte sert le superviseur
(`oto_fleet op=state`) et n'entre dans aucune consigne.
"""
from __future__ import annotations

from typing import Optional

from .. import db
from ..datastore.schema import abandon_state_of, status_field, terminal_states
from ._lignes_reservables import TableauAmbigu, tableau_vise

#: La clé d'une ligne sans valeur de statut — une valeur de statut n'est jamais vide.
SANS_STATUT = "(none)"


def _indisponible(raison: str) -> dict:
    return {"rows": None, "rows_unavailable": raison}


def pour_le_superviseur(f: dict) -> dict:
    """`{rows, rows_unavailable}` pour UNE automatisation.

    `rows_unavailable` dit pourquoi il n'y a pas de ventilation : `no_table` (elle ne
    vise aucun tableau), `table_not_found` (son tableau n'existe plus ou n'est plus
    visible de qui l'a déclarée), `table_ambiguous` (déclarée avant #1067, son NOM ne
    désigne plus un seul tableau — `_lignes_reservables._cle_heritee`),
    `no_status_column` (le schéma ne déclare aucune colonne `role="status"` : l'issue
    d'une ligne n'y a pas de place)."""
    if not (f.get("namespace") or "").strip():
        return _indisponible("no_table")
    try:
        t = tableau_vise(f)
    except TableauAmbigu:
        return _indisponible("table_ambiguous")
    if t is None:
        return _indisponible("table_not_found")
    schema = t.get("schema")
    champ = status_field(schema)
    if not champ or not champ.get("key"):
        return _indisponible("no_status_column")
    colonne = str(champ["key"])
    perimetre = {k: v for k, v in (f.get("row_filter") or {}).items() if k != colonne}
    lues = db.lignes_par_statut(int(t["id"]), colonne, perimetre)
    return {"rows": ventiler(lues, colonne=colonne, perimetre=perimetre,
                             terminaux=terminal_states(schema),
                             abandon=abandon_state_of(schema)),
            "rows_unavailable": None}


def ventiler(lues: list[dict], *, colonne: str, perimetre: dict, terminaux: set,
             abandon: Optional[str]) -> dict:
    """La ventilation servie, depuis les comptes `(statut, abandon_reason, lignes)`.

    `concluded` / `open` valent `null` quand le schéma ne déclare aucun état
    terminal, `abandoned` quand il ne déclare pas d'état d'abandon : la plateforme ne
    peut pas ranger ce que le cycle de vie ne nomme pas, et un zéro le ferait croire."""
    par_statut: dict[str, int] = {}
    motifs: dict[Optional[str], int] = {}
    for x in lues:
        statut = SANS_STATUT if x["statut"] is None else str(x["statut"])
        par_statut[statut] = par_statut.get(statut, 0) + int(x["lignes"])
        if abandon is not None and statut == abandon:
            motifs[x["abandon_reason"]] = motifs.get(x["abandon_reason"], 0) + int(x["lignes"])
    total = sum(par_statut.values())
    abandonnees = par_statut.get(abandon, 0) if abandon is not None else None
    if terminaux:
        terminales = sum(n for s, n in par_statut.items() if s in terminaux)
        conclues = terminales - (abandonnees if abandon in terminaux else 0)
        ouvertes = total - terminales
    else:
        conclues = ouvertes = None
    return {
        "scope": "perimeter" if perimetre else "table",
        "status_column": colonne,
        "perimeter": perimetre,
        "total": total,
        "by_status": par_statut,
        "terminal_states": sorted(terminaux),
        "abandon_state": abandon,
        "concluded": conclues,
        "abandoned": abandonnees,
        "open": ouvertes,
        "abandon_reasons": [{"reason": m, "rows": n}
                            for m, n in sorted(motifs.items(),
                                               key=lambda kv: (-kv[1], kv[0] or ""))],
    }
