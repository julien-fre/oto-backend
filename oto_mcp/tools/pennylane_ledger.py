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

import hashlib
import json
import time
from typing import Literal, Optional

from fastmcp import FastMCP

from .pennylane_socle import _bad, _client, _ecrit, _need

# Brouillon porté par oto, faute que Pennylane en ait un.
#
# Une écriture comptable est posée immédiatement et ne peut pas être supprimée ;
# la corriger peut détruire des lignes. Une description d'outil ne suffit donc
# pas : l'agent la relit à chaque appel, il ne la respecte pas forcément. La
# garde est mécanique — `op="prepare"` rend le détail exact et un jeton,
# `op="create"` refuse sans ce jeton. L'agent ne PEUT plus écrire sans avoir eu
# le détail sous les yeux au tour précédent, et c'est là que la supervision
# humaine se glisse.
#
# Le jeton est l'empreinte du détail : re-préparer le même détail rend le même
# jeton, en changer un centime en rend un autre — un accord donné sur un détail
# ne vaut donc pas pour un autre. Le registre, lui, porte l'instant : c'est ce
# qui le fait expirer, et ce qui empêche de fabriquer un jeton sans être passé
# par `prepare`.
_PREPARES: dict[str, float] = {}
_VALIDITE_S = 300.0


def _jeton(detail: dict) -> str:
    canon = json.dumps(detail, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=False, default=str)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()[:16]


def _detail_creation(date, label, journal_id, lines, due_date, currency,
                     piece_number) -> dict:
    """Le détail exact qui sera posé — la seule chose qu'on montre et qu'on
    signe. Construit ici pour les DEUX chemins : si `prepare` et `create` le
    composaient chacun de leur côté, un écart entre eux invaliderait tous les
    jetons sans qu'on comprenne pourquoi."""
    return {"date": date, "label": label, "journal_id": journal_id,
            "lignes": lines, "due_date": due_date, "currency": currency,
            "piece_number": piece_number}


def _poser_jeton(detail: dict) -> str:
    maintenant = time.monotonic()
    for vieux in [j for j, t in _PREPARES.items() if maintenant - t > _VALIDITE_S]:
        _PREPARES.pop(vieux, None)
    jeton = _jeton(detail)
    _PREPARES[jeton] = maintenant
    return jeton


def _exiger_jeton(detail: dict, fourni: Optional[str], geste: str) -> None:
    """Refuse le geste tant qu'il n'a pas été préparé, à l'identique et récemment."""
    attendu = _jeton(detail)
    if not fourni:
        raise _bad(
            f"{geste} exige un jeton de préparation, et ce n'est pas une "
            "formalité : le geste est irréversible côté Pennylane. Appelle "
            "d'abord `op=\"prepare\"` avec exactement ces arguments, MONTRE à "
            "l'utilisateur le détail rendu, et ne rappelle ici qu'avec son "
            "accord et le jeton reçu.")
    if fourni != attendu:
        raise _bad(
            f"Ce jeton ne correspond pas à CE détail : il a été émis pour "
            "d'autres valeurs. Un accord donné sur un détail ne vaut pas pour un "
            "autre — reprends `op=\"prepare\"` avec les arguments d'ici, "
            "re-soumets le détail à l'utilisateur, et utilise le nouveau jeton.")
    pose = _PREPARES.get(fourni)
    if pose is None:
        raise _bad(
            "Jeton inconnu : aucune préparation ne lui correspond. Soit elle n'a "
            "jamais eu lieu, soit le serveur a redémarré depuis. Rappelle "
            "`op=\"prepare\"`.")
    if time.monotonic() - pose > _VALIDITE_S:
        raise _bad(
            f"Jeton périmé (plus de {int(_VALIDITE_S // 60)} minutes). Un accord "
            "donné il y a longtemps ne vaut plus pour un geste irréversible : "
            "rappelle `op=\"prepare\"` et re-soumets le détail.")


def register(mcp: FastMCP) -> None:

    @mcp.tool()
    def pennylane_ledger_entry(
        op: Literal["list", "get", "lines", "lettered", "prepare", "create",
                    "update"] = "list",
        entry_id: Optional[int] = None,
        line_id: Optional[int] = None,
        clauses: Optional[list] = None,
        max_pages: Optional[int] = None,
        date: Optional[str] = None,
        label: Optional[str] = None,
        journal_id: Optional[int] = None,
        lines: Optional[list] = None,
        due_date: Optional[str] = None,
        currency: Optional[str] = None,
        piece_number: Optional[str] = None,
        fields: Optional[dict] = None,
        jeton: Optional[str] = None,
    ) -> dict | list:
        """Écritures du grand livre : lire, poser une écriture, la corriger.

        ⚠️ **Écrire ici est irréversible, et le brouillon est porté par oto.**
        Pennylane pose une écriture comptable immédiatement et ne sait pas la
        supprimer ; la corriger peut détruire des lignes. Le palier « brouillon
        puis validation » qui protège le reste de ce connecteur n'existe pas
        chez lui, alors il est tenu ici : `op="prepare"` rend le détail exact et
        un jeton, `op="create"` et `op="update"` REFUSENT sans ce jeton.

        La marche à suivre, sans raccourci possible : appeler `op="prepare"`,
        **montrer le détail rendu à l'utilisateur**, attendre son accord, puis
        rappeler avec le jeton. Le jeton est l'empreinte du détail — en changer
        un centime l'invalide — et il expire en quelques minutes.

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
            date: op="create" — date de l'écriture (YYYY-MM-DD).
            label: op="create" — libellé de l'écriture.
            journal_id: op="create" — le journal où poser l'écriture. Se résout
                avec `pennylane_ref(kind="journals")` : ces ids sont propres à
                la société, jamais à coder en dur.
            lines: op="create" — les lignes, 1 à 1000. Chacune `{"debit": "…",
                "credit": "…", "ledger_account_id": …}` et un `label` optionnel.
                Les montants sont des CHAÎNES décimales ("120.50"), et les
                débits doivent égaler les crédits — sinon l'appel est refusé
                avant d'atteindre Pennylane, avec l'écart chiffré. Les comptes
                se résolvent avec `pennylane_ref(kind="ledger_accounts")`.
            due_date / currency / piece_number: op="create", optionnels
                (devise EUR par défaut, numéro de pièce auto-généré).
            fields: op="update" — les champs à modifier sur l'écriture.
                ⚠️ `ledger_entry_lines` y prend `create`/`update`/`delete` : ce
                geste peut SUPPRIMER des lignes, d'où le même jeton que create.
            jeton: op="create" et op="update" — le jeton rendu par `prepare`
                pour EXACTEMENT ces arguments, après accord de l'utilisateur.
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
        if op == "prepare":
            lignes = _need(lines, "lines", op)
            detail = _detail_creation(
                _need(date, "date", op), _need(label, "label", op),
                _need(journal_id, "journal_id", op), lignes,
                due_date, currency, piece_number)
            try:
                recap = c.controler_ecriture(lignes)
            except ValueError as e:
                raise _bad(str(e))
            return {"a_poser": detail, "recapitulatif": recap,
                    "jeton": _poser_jeton(detail),
                    "ensuite": "MONTRE `a_poser` et `recapitulatif` à "
                               "l'utilisateur, attends son accord, puis rappelle "
                               "op=\"create\" avec ces mêmes arguments et ce "
                               "jeton. L'écriture ne sera pas supprimable."}
        if op == "create":
            detail = _detail_creation(
                _need(date, "date", op), _need(label, "label", op),
                _need(journal_id, "journal_id", op), _need(lines, "lines", op),
                due_date, currency, piece_number)
            _exiger_jeton(detail, jeton, "La création d'écriture comptable")
            return _ecrit(c.create_ledger_entry(
                date=_need(date, "date", op), label=_need(label, "label", op),
                journal_id=_need(journal_id, "journal_id", op),
                ledger_entry_lines=_need(lines, "lines", op),
                due_date=due_date, currency=currency, piece_number=piece_number),
                "la création d'écriture comptable")
        if op == "update":
            _exiger_jeton({"entry_id": _need(entry_id, "entry_id", op),
                           "fields": fields or {}},
                          jeton, "La correction d'écriture comptable")
            return _ecrit(c.update_ledger_entry(_need(entry_id, "entry_id", op),
                                                **(fields or {})),
                          "la correction d'écriture comptable")
        raise _bad("op doit être 'list', 'get', 'lines', 'lettered', 'prepare', "
                   "'create' ou 'update'")

    @mcp.tool()
    def pennylane_ledger_lettering(
        op: Literal["set", "unset"],
        line_ids: list[int],
        unbalanced_lettering_strategy: Literal["none", "partial"] = "none",
    ) -> dict:
        """Lettre des LIGNES du grand livre entre elles, ou défait ce lettrage.

        ⚠️ **Ce n'est pas `pennylane_match`.** Le mot « lettrage » recouvre deux
        gestes sur deux objets : rapprocher une transaction bancaire d'une
        facture, c'est `pennylane_match` ; associer entre elles des lignes
        d'écriture au grand livre, c'est ici. Se tromper d'outil ne produit pas
        d'erreur, seulement un geste posé au mauvais endroit.

        ⚠️ **Le lettrage est ABSORBANT** : si une ligne passée est déjà lettrée,
        le lettrage s'étend à celles qui lui sont déjà associées. Demander
        [A, C] quand A et B sont lettrées produit [A, B, C]. Pour constater ce
        qui a réellement été associé, relire avec
        `pennylane_ledger_entry(op="lettered", line_id=…)`.

        Le geste est réversible (`op="unset"`), ce qui le distingue d'une
        écriture comptable.

        Args:
            op: "set" pour lettrer, "unset" pour défaire.
            line_ids: au moins deux ids de LIGNES d'écriture — pas des ids
                d'écritures. Ils se lisent avec
                `pennylane_ledger_entry(op="lines", entry_id=…)`.
            unbalanced_lettering_strategy: "none" refuse un lettrage
                déséquilibré (défaut), "partial" l'accepte.
        """
        c = _client()
        if op == "set":
            return _ecrit(
                c.letter_ledger_entry_lines(line_ids, unbalanced_lettering_strategy),
                "le lettrage de lignes du grand livre")
        if op == "unset":
            return _ecrit(
                c.unletter_ledger_entry_lines(line_ids, unbalanced_lettering_strategy),
                "le délettrage de lignes du grand livre")
        raise _bad("op doit être 'set' ou 'unset'")
