"""Grand livre Pennylane — lecture des écritures, de leurs lignes, du lettrage.

Second module du connecteur `pennylane` (cf. `Connector.modules` au registre) :
même clé, même client, domaine distinct. Les journaux, eux, sont un référentiel
et se lisent par `pennylane_ref(kind="journals")`, avec les autres.

⚠️ **Ce n'est pas la GED.** Le connecteur `pennylaneged` vise la même entreprise
par une autre porte (API privée de l'interface, session navigateur), et ses
`company_id` ne sont PAS ceux d'ici. Un id pris dans l'un et joué dans l'autre
rend un refus qui imite une session expirée.

⚠️ **Trois scopes, pas un.** Pennylane a éclaté l'ancien scope `ledger` : lire
les écritures demande `ledger_entries:*`, les journaux `journals:*`, le plan
comptable `ledger_accounts:*`. Une clé qui lit l'un ne lit pas forcément les
autres, et le périmètre est propre à qui a posé la clé. Les droits réels se
lisent avec `pennylane_ref(kind="company")`, champ `scopes`.
"""
from __future__ import annotations

from typing import Literal, Optional

from fastmcp import FastMCP

from .pennylane_socle import _bad, _client, _need


def register(mcp: FastMCP) -> None:

    @mcp.tool()
    def pennylane_ledger_entry(
        op: Literal["list", "get", "lines", "lettered"] = "list",
        entry_id: Optional[int] = None,
        line_id: Optional[int] = None,
        clauses: Optional[list] = None,
        max_pages: Optional[int] = None,
    ) -> dict | list:
        """Écritures du grand livre : les lister, en lire une, lire ses lignes.

        ⚠️ **`op="list"` sans `clauses` remonte TOUT l'historique** — sur une
        comptabilité réelle, des milliers d'écritures, bien au-delà de la limite
        de tokens. Filtrer à la source est le seul moyen de retrouver une
        écriture ; `max_pages` borne les dégâts mais ne cible rien.

        Args:
            op: "list" — les écritures, à filtrer avec `clauses` ;
                "get" — UNE écriture par son `entry_id` ;
                "lines" — les lignes d'une écriture, avec leur `id` (c'est cet
                    id que consomme le lettrage, pas celui de l'écriture) ;
                "lettered" — les lignes lettrées AVEC la ligne `line_id`, pour
                    constater ce qu'un lettrage a réellement associé.
            entry_id: id de l'écriture — requis par "get" et "lines".
            line_id: id d'une LIGNE d'écriture — requis par "lettered".
            clauses: filtre serveur, liste de `{"field", "operator", "value"}`.
                Champs filtrables : `id`, `date`, `journal_id`. Opérateurs :
                `lt`, `lteq`, `gt`, `gteq`, `eq`, `not_eq`, plus `in` et
                `not_in` sur `id` et `journal_id`. Exemple :
                `[{"field": "date", "operator": "gteq", "value": "2026-01-01"}]`.
            max_pages: borne le nombre de pages ramenées.
        """
        c = _client()
        if op == "list":
            return c.get_ledger_entries(max_pages=max_pages, clauses=clauses)
        if op == "get":
            return c.get_ledger_entry(_need(entry_id, "entry_id", op))
        if op == "lines":
            return c.get_ledger_entry_lines(_need(entry_id, "entry_id", op),
                                            max_pages=max_pages)
        if op == "lettered":
            return c.get_lettered_lines(_need(line_id, "line_id", op),
                                        max_pages=max_pages)
        raise _bad("op doit être 'list', 'get', 'lines' ou 'lettered'")
