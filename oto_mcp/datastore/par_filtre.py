"""METTRE À JOUR des lignes PAR FILTRE — une règle, côté serveur, sans un seul `_id`
dans le contexte de l'appelant (`data_update_where`).

**Le fait.** Après une notation par un agent, le résultat utile est une ÉTIQUETTE
dérivée (Tier 1/2/3, Hot) calculée sur plusieurs colonnes — les sorties de l'agent et
d'autres qu'il n'a jamais vues, sur des milliers de lignes. Le seul chemin était
`data_write(rows=[…])` : rapatrier chaque `_id` dans le contexte du modèle, les
renvoyer par lots — du temps, des jetons, et des lignes PERDUES au-delà de ~57 Ko de
lot (vécu le 29/09/2026). Lire avec un filtre ne suffit pas : l'étiquette n'est ni
visible ni triable dans le tableau, chaque consommateur réimplémente la règle, et
aucune date ne dit quel palier une ligne avait.

**Le geste.** `filter`/`filters` = la grammaire de `data_rows` (le MÊME `_clauses`,
donc mêmes gardes et mêmes dates typées) ; `set` = `{colonne: valeur}` ;
`only_if_empty` ajoute une clause `empty` par colonne visée — les règles ordonnées
jouent alors « la première qui gagne » (Hot, puis Tier 1, 2, 3). ⚠️ `empty` est le
prédicat de `data_rows` : une case `@empty` (« cherché, rien ») y compte comme VIDE,
et la règle la remplit — même grammaire sur les deux verbes, pas une exception ici.

**Refusé AVANT la première ligne** — une faute de la RÈGLE, qui le serait sur toutes :
une valeur qui EFFACE (`null`, `""`, `[]`, `{}`, `@empty`, une couche vide) — effacer
en masse sans `confirm`, c'est `data_drop_column` ; la clé métier ; un rang de liste
(l'AJOUT `col[+]` excepté, ci-dessous) ;
la couche `origine` ; une valeur hors des `options` déclarées ; une colonne
`readonly` hors `only_if_empty` (le verrou laisse remplir une case vide, pas
remplacer). Au-delà : si les `ESSAIS` premières lignes tentées sont TOUTES refusées
et rien n'est écrit, la règle est refusée entière (et la colonne déclarée pour elle,
retirée) ; un refus propre à UNE ligne n'arrête pas les autres.

**Compter juste.** `unchanged` = la ligne porte déjà la valeur — jugé au TYPE près
(`couches.same_value`, sur la valeur déballée et ses couches), sans écriture : une
écriture sans effet remettrait quand même à zéro l'état de file de la ligne.
`updated` = la révision de la ligne a avancé, lue sur ce que `update_row` rend.

**Chaque ligne passe par `update_row`**, jamais par un UPDATE SQL en masse : verrou
de ligne, bail, `readonly`, champs réservés, cycle de vie, dates, formules — tout ce
que le patch par `id` garde vaut ici, sans copie qui divergerait. Le journal des
révisions est un déclencheur PostgreSQL : chaque ligne changée y entre, valeurs
comprises (`data_row_history`).

**La révision lue est passée en précondition.** Entre la sélection et l'écriture, une
case peut se remplir : la ligne est alors SAUTÉE (`conflicts`), pas écrasée.

**La colonne visée est DÉCLARÉE si elle manque** — à partir du 21/10/2026 une colonne
non déclarée est refusée (`colonnes_non_declarees`). Seulement sur un tableau qui
déclare déjà des colonnes : poser un premier champ sur un tableau SANS schéma rendrait
toutes ses autres colonnes « non déclarées » d'un coup.

**L'ajout `col[+]`** (une ligne à un journal texte, un élément à une liste) est la
seule adresse de rang servie : il ne lit rien, chaque ligne le résout sous son verrou
(`rangs`), avec sa borne (`TAILLE_MAX_APRES_AJOUT`). Trois écarts au reste du geste :
- une règle FAITE D'AJOUTS seulement s'écrit SANS précondition de révision — deux
  agents qui ajoutent en même temps sont le cas que l'ajout sert, pas un conflit ; le
  filtre est jugé à la sélection ;
- un ajout n'est jamais « déjà en place » (`unchanged`) : rejouer la règle ajoute une
  seconde fois — reprendre par `next_cursor`, ou filtrer sur un état que la règle pose ;
- sa colonne doit être DÉCLARÉE sur un tableau qui a un schéma : `text` ou `list` ne se
  devine pas d'une valeur. `only_if_empty` lui est refusé : un ajout n'attend pas une
  case vide.

**Borné en temps, pas en lignes.** La face REST coupe à 45 s (`api_routes`) ; le
geste s'arrête au budget et rend `next_cursor` (le dernier `_id` traité, keyset sur
`row_id`) — repasser le même appel avec ce curseur continue. Avec `only_if_empty`,
rejouer l'appel sans curseur est aussi sûr : les lignes déjà posées ne répondent plus
au filtre.
"""
from __future__ import annotations

import time
from typing import Any, Optional

from .. import db
from .couches import (ORIGIN_LAYER, VALUE_LAYER, est_vide, est_vide_delibere,
                      names_layers, same_value, split_layer, unwrap)
from .declaration import _fields, champ_declare, readonly_fields
from .errors import RevisionConflict, RowLocked, RowNotFound, RowValidationError
from .options_declarees import hors_des_options
from .rangs import colonne_ajoutee

#: Secondes de travail avant de rendre la main avec un curseur — sous les 45 s de la
#: face REST, marge comprise pour la réponse et le journal.
BUDGET_S = 30.0
#: Lignes lues par page de sélection (keyset sur `row_id`).
PAGE = 500
#: Exemples rendus par un `dry_run`.
ECHANTILLON = 5
#: Refus, conflits et valeurs écartées CITÉS dans la réponse — les comptes, eux, sont
#: entiers. Une réponse qui grossit avec le tableau est le défaut que ce geste existe
#: pour éviter.
CITES = 20
#: Lignes tentées, toutes refusées, rien d'écrit : la RÈGLE est fautive, pas la ligne.
ESSAIS = 3


class _RegleRefusee(Exception):
    """Porte le refus de la règle hors de la boucle, pour défaire la déclaration."""

    def __init__(self, cause: Exception):
        super().__init__(str(cause))
        self.cause = cause


def _efface(v: Any) -> bool:
    if names_layers(v):
        return any(est_vide(x) or est_vide_delibere(x) for x in v.values())
    return est_vide(v) or est_vide_delibere(v)


def _valeur_posee(cle: str, v: Any) -> tuple[str, bool, Any]:
    """`(colonne, porte une valeur ?, valeur déballée)` — `tier.comment` ou
    `{"comment": …}` seul ne pose pas de valeur."""
    base, couche = split_layer(cle)
    if couche:
        return base, False, None
    if names_layers(v):
        return base, VALUE_LAYER in v, v.get(VALUE_LAYER)
    return base, True, v


def _type_infere(valeur: Any) -> str:
    if isinstance(valeur, bool):
        return "bool"
    if isinstance(valeur, (int, float)):
        return "number"
    if isinstance(valeur, (list, dict)):
        return "json"
    return "text"


def _deja_en_place(data: dict, set_: dict) -> bool:
    if any(colonne_ajoutee(cle) for cle in set_):
        return False                    # un ajout change toujours la cellule
    for cle, v in set_.items():
        base, couche = split_layer(cle)
        stocke = data.get(base)
        couches_en_place = stocke if isinstance(stocke, dict) else {}
        if couche:
            if not same_value(couches_en_place.get(couche), v):
                return False
        elif names_layers(v):
            for lk, lv in v.items():
                cur = unwrap(stocke) if lk == VALUE_LAYER else couches_en_place.get(lk)
                if not same_value(cur, lv):
                    return False
        elif not same_value(unwrap(stocke), v):
            return False
    return True


def _verifier_set(set_: Any) -> dict:
    if not isinstance(set_, dict) or not set_:
        raise ValueError(
            "`set` = the columns to set, `{\"tier\": \"Tier 1\"}` — got "
            f"{set_!r}. Nothing is written.")
    for cle, v in set_.items():
        if not isinstance(cle, str) or not cle.strip():
            raise ValueError(f"`set`: empty or non-text column key ({cle!r}).")
        if cle.startswith("_"):
            raise ValueError(
                f"`set`: `{cle}` is a platform column, not data.")
        if "[" in cle and colonne_ajoutee(cle) is None:
            raise ValueError(
                f"`set`: `{cle}` targets a list INDEX — a rule by filter sets a whole "
                f"column, or APPENDS (`col[+]`). Write this index row by row "
                f"with `data_write`.")
        if _efface(v):
            # Effacer une colonne sur N lignes d'un seul geste, sans `confirm` : le
            # verbe détruirait en masse là où `data_drop_column` exige une confirmation.
            raise ValueError(
                f"`set`: `{cle}: {v!r}` WOULD CLEAR the value on every targeted row — "
                f"refused here. For a dead column, `data_drop_column`; for one "
                f"row, `data_write(id=…)`.")
        if split_layer(cle)[1] == ORIGIN_LAYER or (names_layers(v) and ORIGIN_LAYER in v):
            raise ValueError(
                f"`set`: `{cle}` sets the `origine` layer, which belongs to an import "
                f"(`data_write(donnees_d_origine=true)`), never to a rule.")
    return set_


class ParFiltreMixin:
    """La mise à jour par filtre du store. Composé par `DatastorePg`."""

    def update_where(self, datastore: str, set_: dict, *,
                     filter: Optional[dict] = None,
                     filters: Optional[list] = None,
                     only_if_empty: bool = False,
                     dry_run: bool = False,
                     cursor: Optional[str] = None) -> dict:
        set_ = _verifier_set(set_)
        ns_id = self._resolve(datastore, write=True)
        ajoutees = sorted({c for c in map(colonne_ajoutee, set_) if c})
        poses = [_valeur_posee(k, v) for k, v in set_.items()
                 if colonne_ajoutee(k) is None]
        colonnes = sorted({c for c, _, _ in poses} | set(ajoutees))
        a_valeur = {c: val for c, porte, val in poses if porte}
        clauses = self._clauses(ns_id, filter, filters)
        if only_if_empty and ajoutees:
            raise ValueError(
                f"`only_if_empty` expects an empty cell, and `{ajoutees[0]}[+]` APPENDS to "
                f"the cell whatever it holds. Remove `only_if_empty`, or filter on "
                f"the state that says the entry is missing.")
        if only_if_empty:
            if not a_valeur:
                raise ValueError(
                    "`only_if_empty` expects a column whose VALUE is set: `set` "
                    "only sets layers here (`.comment`, `.link`), which have no "
                    "empty cell to wait for. Remove `only_if_empty`, or set the value.")
            for c in sorted(a_valeur):
                clauses.append({"field": c, "op": "empty"})
        schema = self._schema_of(ns_id)
        cle_metier = self._declared_key_of(schema)
        if cle_metier and cle_metier in colonnes:
            raise ValueError(
                f"`set` targets `{cle_metier}`, the table's business key: the same value "
                f"set on several rows would make them collide. Nothing is "
                f"written.")
        self._juger_la_regle(schema, colonnes, a_valeur, only_if_empty)
        declarees = {f.get("key") for f in _fields(schema)}
        self._juger_les_ajouts(schema, ajoutees, declarees)
        a_declarer = ([{"key": c, "type": _type_infere(a_valeur[c])}
                       for c in sorted(a_valeur) if c not in declarees]
                      if declarees else [])
        matched = db.datastore_count_rows(ns_id, filters=clauses, after_row_id=cursor)

        if dry_run:
            lignes = db.datastore_list_rows_after(
                ns_id, after_row_id=cursor, limit=ECHANTILLON, filters=clauses)
            return {
                "dry_run": True, "matched": matched,
                "sample": [{"_id": r["row_id"],
                            "from": {c: (r.get("data") or {}).get(c) for c in colonnes},
                            "to": dict(set_)} for r in lignes],
                "would_declare": a_declarer,
            }

        for champ in a_declarer:
            self.patch_schema(datastore, fields=[champ])

        # Le tableau et son schéma ne bougent pas pendant le geste (la colonne est
        # déclarée AVANT) : `update_row` les relirait à CHAQUE ligne — 28 % du temps
        # par ligne, mesuré. Figés le temps de la boucle, rendus à la sortie.
        ns_fige = self._ns_of(ns_id)
        self._resolve = lambda _ds, *, write=False: ns_id
        self._ns_of = lambda i: ns_fige if i == ns_id else type(self)._ns_of(self, i)
        try:
            try:
                out = self._boucle(datastore, ns_id, set_, clauses, cursor,
                                   precondition=len(ajoutees) < len(set_))
            finally:
                del self._resolve, self._ns_of
        except _RegleRefusee as e:
            if a_declarer:
                self.patch_schema(datastore, remove=[c["key"] for c in a_declarer])
            raise e.cause
        out["matched"] = matched
        if a_declarer:
            out["declared"] = a_declarer
        return out

    @staticmethod
    def _juger_la_regle(schema: Optional[dict], colonnes: list, a_valeur: dict,
                        only_if_empty: bool) -> None:
        """Les fautes de la RÈGLE, jugées une fois avant toute ligne."""
        verrouillees = sorted(set(a_valeur) & readonly_fields(schema))
        if verrouillees and not only_if_empty:
            raise ValueError(
                f"`set` replaces {', '.join(f'`{c}`' for c in verrouillees)}, locked "
                f"column(s) (`readonly`): a rule does not force a lock. With "
                f"`only_if_empty=true`, it fills the cells that are still empty.")
        for c, val in a_valeur.items():
            decl = champ_declare(schema, c) or {}
            opts = decl.get("options")
            if (isinstance(opts, list) and not isinstance(val, (list, dict))
                    and hors_des_options(val, opts)):
                raise RowValidationError([
                    f"{c}: value {val!r} not among the options "
                    f"({', '.join(str(o) for o in opts)}) — the rule would set it on "
                    f"every row. Nothing is written."])

    @staticmethod
    def _juger_les_ajouts(schema: Optional[dict], ajoutees: list,
                          declarees: set) -> None:
        """Les fautes d'une règle qui AJOUTE, jugées une fois avant toute ligne."""
        verrouillees = sorted(set(ajoutees) & readonly_fields(schema))
        if verrouillees:
            raise ValueError(
                f"`set` appends to `{verrouillees[0]}`, a locked column (`readonly`): "
                f"a rule does not force a lock. Nothing is written.")
        manquantes = [c for c in ajoutees if declarees and c not in declarees]
        if manquantes:
            c = manquantes[0]
            raise ValueError(
                f"`set` appends to `{c}`, which the schema does not declare: a rule does not guess "
                f"whether it is a log or a list. Declare it first "
                f"(`data_patch_schema`, `{{\"key\": \"{c}\", \"type\": \"text\"}}` for a "
                f"log, `\"list\"` for items), then run again. Nothing is written.")

    def _boucle(self, datastore, ns_id, set_, clauses, cursor, *,
                precondition: bool = True) -> dict:
        updated = unchanged = essais = 0
        conflicts: list = []
        refused: list = []
        n_conflicts = n_refused = 0
        premier_refus: Optional[Exception] = None
        debut = time.monotonic()
        apres = cursor
        dernier: Optional[str] = None
        fini = False
        while not fini:
            page = db.datastore_list_rows_after(
                ns_id, after_row_id=apres, limit=PAGE, filters=clauses)
            if not page:
                break
            for r in page:
                # Jugé APRÈS au moins une ligne : un appel avance toujours, et le
                # curseur rendu est la dernière ligne TRAITÉE — la reprise ne saute rien.
                if dernier is not None and time.monotonic() - debut > BUDGET_S:
                    fini = True
                    break
                rid = r["row_id"]
                dernier = rid
                if _deja_en_place(r.get("data") or {}, set_):
                    unchanged += 1
                    continue
                essais += 1
                try:
                    ecrite = self.update_row(
                        datastore, rid, dict(set_),
                        expected_revision=r.get("rev") if precondition else None)
                except RowNotFound:
                    continue                      # supprimée entre-temps
                except (RevisionConflict, RowLocked) as e:
                    n_conflicts += 1
                    if len(conflicts) < CITES:
                        conflicts.append({"_id": rid, "error": str(e)})
                    continue
                except ValueError as e:
                    n_refused += 1
                    premier_refus = premier_refus or e
                    if updated == 0 and n_refused == essais >= ESSAIS:
                        raise _RegleRefusee(premier_refus)
                    if len(refused) < CITES:
                        refused.append({"_id": rid, "error": str(e)})
                    continue
                if str(ecrite.get("_revision")) != str(r.get("rev")):
                    updated += 1
                else:
                    unchanged += 1
            else:
                apres = page[-1]["row_id"]
                if len(page) < PAGE:
                    break
                continue
        out = {"updated": updated, "unchanged": unchanged,
               "conflicts": n_conflicts, "refused": n_refused,
               "next_cursor": dernier if fini else None}
        if conflicts:
            out["conflicts_sample"] = conflicts
        if refused:
            out["refused_sample"] = refused
        # `valeurs_ecartees` (#667) n'est pas bornée par son relevé : une entrée par
        # ligne. Bornée ici, le total dit à part.
        if len(self.off_rejected) > CITES:
            out["valeurs_ecartees_total"] = len(self.off_rejected)
            del self.off_rejected[CITES:]
        return out
