"""Le périmètre de réservation déclaré sur le TABLEAU — `lifecycle.claimable` (#517).

Sans lui, `data_claim_next` sert « la plus ancienne ligne dont le bail est libre ou
expiré » — TOUTE ligne du tableau. Mesuré le 29/08/2026 sur un fichier de 8 910
lignes : un jalon en cible 100 (`lot_test = jalon-100`) ; le harnais dicte le filtre
dans la prose de l'ordre et l'agent le recopie. À 5 % d'oubli, cinq fiches hors lot
par jalon — servies, traitées, écrites, payées. **Une contrainte demandée par la
prose n'est pas une contrainte.**

D'où une déclaration du tableau, à côté de `max_claims` :

    lifecycle: {…, claimable: {"lot_test": "jalon-100", "statut": "a_enrichir"}}

Même grammaire que `filter` (`{col: val}` ou `{col: {op: val}}`), validée à la POSE
par le même moteur que la lecture (`db.query`) — opérateurs whitelistés, colonnes
déclarées quand le format fait contrat (`validation_complete`), refus nommé sinon. Effet, sur les deux réservations :

- le serveur ne sert JAMAIS une ligne hors de ce filtre, quel que soit le `filter`
  passé — celui de l'appelant s'y ajoute en ET : il resserre, jamais n'élargit ;
- la réservation CIBLÉE (REST `claim_row`) refuse une ligne hors périmètre en le
  nommant — sinon la porte de côté ;
- une réservation qui ne trouve rien le dit EN NOMMANT le périmètre : un `row: null`
  nu se lit comme une file vide, alors que c'est peut-être le filtre de l'appelant qui
  contredit la déclaration.

Ce module ne connaît que la grammaire des filtres. ⚠️ Il l'importe À L'APPEL :
`db.query` remonte vers `schema.py` (par `paths`), et `schema.py` importe ce module
pour décider — un import de tête ferait le cycle. `core.py` l'appelle pour servir,
les deux faces pour le dire.
"""
from __future__ import annotations

import re
from typing import Any, Optional

CLE = "claimable"

# Le premier segment d'un chemin de colonne : `contacts[0].email` → `contacts`.
_TETE = re.compile(r"[.\[]")


class RowOutsideClaimable(Exception):
    """Réservation CIBLÉE d'une ligne hors du périmètre déclaré (#517).

    Le périmètre borne ce que le tableau SERT : si `claim_next` ne le franchit
    jamais mais que `claim_row` le franchit sur demande, la déclaration n'est qu'une
    étiquette. Porte le périmètre, pour que la surface le nomme — et le geste qui
    l'élargit, qui est celui du schéma, pas celui de l'appel."""

    def __init__(self, row_id: Any, perimetre: dict):
        self.row_id = row_id
        self.perimetre = dict(perimetre)
        super().__init__(
            f"row `{row_id}` is outside the declared claim scope "
            f"{perimetre_texte(perimetre)} — claim refused. This table only serves "
            "rows within this scope (`lifecycle.claimable` in its schema); "
            "widening it is a change to the schema (data_patch_schema), never to "
            "the call.")


def perimetre_of(lc: Optional[dict], ns_id: Optional[int] = None) -> Optional[dict]:
    """Le périmètre déclaré sur un cycle de vie, ou None = non déclaré.

    Une valeur présente mais inutilisable LÈVE (même parti que `max_claims_of`) :
    l'ignorer rouvrirait le tableau en silence, exactement le défaut que le périmètre
    existe pour fermer.

    ⚠️ **Le message NOMME sa destination depuis le 08/09/2026, et c'est tout le lot.**
    Il décrivait la forme attendue du paramètre — donc un agent le lisait comme un
    reproche sur SON appel. Mesuré par la session de flotte : **60 refus sur 81
    appels, dix agents brûlant leurs tours à varier leur propre argument**, puis
    concluant sereinement leur travail en expliquant « la structure exacte attendue ».
    Aucun n'a compris qu'il n'y avait rien à corriger de son côté.

    Le défaut n'est pas dans l'appel, il est dans le SCHÉMA du tableau — écrit par
    quelqu'un d'autre, réparable par son propriétaire seul. Un refus qui ne dit pas OÙ
    porter l'intention fait rejouer le même appel : ici huit fois par run.

    ⚠️ Et la docstring affirmait qu'une forme illisible « ne peut venir que d'une
    écriture hors surface », puisque la pose la refuse. **C'est démenti** : le schéma
    fautif existe, et il est antérieur à la garde de pose. Un schéma déjà en base ne
    se repose jamais — le raisonnement « c'est refusé à la pose, donc ça n'existe
    pas » est faux pour toute garde ajoutée après coup."""
    if not isinstance(lc, dict) or lc.get(CLE) is None:
        return None
    p = lc[CLE]
    if not isinstance(p, dict) or not p:
        ou = f"Table {ns_id} declares" if ns_id is not None else "This table declares"
        raise ValueError(
            f"{ou} `lifecycle.claimable` in an invalid form — got {p!r}, "
            "expected a non-empty object `{col: val}`. **Your call is not at fault: "
            "there is nothing to fix in your arguments, and retrying will fail "
            "the same way.** It is the table's FORMAT that is wrong, and only its owner "
            "can repair it — `data_patch_schema` on the column that carries "
            "`role: \"status\"`. Report it rather than retrying.")
    return dict(p)


def clauses(perimetre: Optional[dict]) -> list[dict]:
    """Le périmètre en clauses du moteur SQL — celles que le pick ajoute en ET
    DEVANT celles de l'appelant. `[]` sans déclaration : le pick reste ce qu'il
    était."""
    if not perimetre:
        return []
    # ⚠️ **Le périmètre vient du SCHÉMA, pas de l'appel — et le refus doit le dire.**
    #
    # Incident du 09/09/2026 : un tableau portait `claimable: ["a_traiter"]` (une liste
    # là où la grammaire de `filter` attend `{col: val}`). Posé avant que la pose ne
    # garde cette forme, il est resté en base. À la réservation, cette fonction levait
    # un `AttributeError` nu — `'list' object has no attribute 'items'`.
    #
    # Ce que ça a produit chez qui le reçoit est le vrai coût : **dix agents ont conclu
    # que leur APPEL était fautif** et ont passé leur budget à varier leur formulation —
    # 60 refus sur 81 appels, huit par travail, jusqu'au plafond de tours. Les dix se
    # sont conclus « terminé » sans une seule écriture. Et la personne qui avait posé le
    # schéma l'avait relu un quart d'heure plus tôt sans voir l'anomalie.
    #
    # Un refus qui décrit la FORME ATTENDUE d'un paramètre fait chercher la faute chez
    # celui qui appelle. Quand la faute est dans une déclaration que l'appelant ne
    # contrôle pas, le refus doit **dire où elle est et qui peut la corriger** — sans
    # quoi il envoie tout le monde chercher au mauvais endroit, à chaque appel.
    if not isinstance(perimetre, dict):
        raise ValueError(
            f"the table declares an unreadable claim scope in ITS SCHEMA "
            f"(`lifecycle.claimable` = {perimetre!r}, expected {{column: value}}). "
            f"⚠️ Your call has nothing to do with it: retrying it differently will change nothing. "
            f"The table's schema must be fixed, by whoever governs it "
            f"(`data_patch_schema`). Report it rather than insisting.")
    from ..db.query import ds_filter_specs
    return ds_filter_specs(perimetre)


def erreurs(lc: dict, *, declared: set, contraignant: bool,
            status_key: Optional[str], states: set) -> list[str]:
    """Les refus à la POSE — une liste de messages actionnables, vide si tout va.

    Le périmètre est un filtre : il se valide comme un filtre, par le moteur qui le
    servira (`ds_filter_specs` + `_ds_filter_clauses`), jamais par une grammaire
    parallèle qui divergerait le jour où le moteur apprend un opérateur. S'y ajoutent
    ce qu'un filtre d'appel ne vérifie pas et qu'une déclaration doit : une clause
    INERTE (`in: []`) est refusée à la source depuis #353 ; une colonne inconnue quand le
    format fait contrat (`validation_complete.complete`, partout à partir du
    21/10/2026) ; un
    état du statut que le cycle de vie ne déclare pas — la file serait vide pour
    toujours, sans un mot."""
    if CLE not in lc or lc[CLE] is None:
        return []
    p = lc[CLE]
    if not isinstance(p, dict) or not p:
        return [f"lifecycle.claimable must be a non-empty object {{col: val}} or "
                f"{{col: {{op: val}}}} — the grammar of `filter` (got {p!r})"]
    from ..db.query import _DS_META_TEXT_COLS, _DS_META_TS_COLS, _ds_filter_clauses
    out: list[str] = []
    try:
        specs = clauses(p)
        sql, _ = _ds_filter_clauses(specs)
    except ValueError as e:
        # ⚠️ #353 : c'est désormais PAR ICI que passe la clause inerte (`in: []`).
        # Elle était détectée en dessous, en comptant les fragments rendus — une
        # clause qui « s'évaporait » en laissait un de moins. Depuis que le filtre
        # vide LÈVE au lieu de disparaître, ce comptage ne peut plus rien voir, et
        # le refus qui remonte ici nomme la colonne et la sortie, ce que le
        # comptage ne pouvait pas faire.
        out.append(f"lifecycle.claimable: {e}")
    meta = set(_DS_META_TS_COLS) | set(_DS_META_TEXT_COLS)
    for col, val in p.items():
        tete = _TETE.split(str(col), 1)[0]
        if contraignant and tete not in declared and tete not in meta:
            out.append(
                f"lifecycle.claimable: column `{col}` is not declared in the schema, whose "
                "format is binding — declare it, or remove it from the scope")
        if (status_key and col == status_key and states
                and not isinstance(val, dict) and str(val) not in states):
            out.append(
                f"lifecycle.claimable: `{col}` = {val!r} is not a declared state "
                f"({', '.join(sorted(states))}) — the queue would be empty forever")
    return out


def perimetre_texte(perimetre: dict) -> str:
    """`{lot_test: jalon-100, statut: a_enrichir}` — le périmètre tel qu'une phrase
    le cite, opérateur compris quand il y en a un (`{posted_at: {gte: 2026-06}}`).
    Colonnes TRIÉES : le schéma relu de la base (jsonb) ne garde pas l'ordre de la
    pose, et une phrase qui change d'un appel à l'autre se lit comme deux périmètres."""
    def _val(v: Any) -> str:
        if isinstance(v, dict):
            return "{" + ", ".join(f"{k}: {_val(x)}" for k, x in v.items()) + "}"
        if isinstance(v, list):
            return "[" + ", ".join(_val(x) for x in v) + "]"
        return str(v)
    return ("`{" + ", ".join(f"{k}: {_val(perimetre[k])}" for k in sorted(perimetre))
            + "}`")


def phrase_vide(perimetre: dict, filtre: Optional[dict]) -> str:
    """Rien servi, et il y a un périmètre : la phrase le NOMME (#517).

    Écrite ici, une fois, pour les deux faces — deux formules divergent. Quand
    l'appelant a passé un filtre, la phrase dit aussi comment il se combine : en ET.
    C'est le cas de la campagne qui cible un jalon et demande un autre lot — rien
    n'est servi, et sans cette phrase l'agent lirait « file vide »."""
    tete = f"no free row in the declared scope {perimetre_texte(perimetre)}"
    if filtre:
        return (f"{tete} — your filter {perimetre_texte(filtre)} is ANDed with it: it "
                "narrows the scope, it does not widen it")
    return f"{tete} (the table's `lifecycle.claimable`; a `filter` is ANDed with it)"
