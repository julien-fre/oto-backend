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
en masse sans `confirm`, c'est `data_drop_column` ; la clé métier ; un rang de liste ;
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
            "`set` = les colonnes à poser, `{\"tier\": \"Tier 1\"}` — reçu "
            f"{set_!r}. Rien n'est écrit.")
    for cle, v in set_.items():
        if not isinstance(cle, str) or not cle.strip():
            raise ValueError(f"`set` : clé de colonne vide ou non textuelle ({cle!r}).")
        if cle.startswith("_"):
            raise ValueError(
                f"`set` : `{cle}` est une colonne de la plateforme, pas une donnée.")
        if "[" in cle:
            raise ValueError(
                f"`set` : `{cle}` vise un RANG de liste — une règle par filtre pose une "
                f"colonne entière. Écris ce rang ligne par ligne avec `data_write`.")
        if _efface(v):
            # Effacer une colonne sur N lignes d'un seul geste, sans `confirm` : le
            # verbe détruirait en masse là où `data_drop_column` exige une confirmation.
            raise ValueError(
                f"`set` : `{cle}: {v!r}` EFFACERAIT la valeur sur chaque ligne visée — "
                f"refusé ici. Pour une colonne morte, `data_drop_column` ; pour une "
                f"ligne, `data_write(id=…)`.")
        if split_layer(cle)[1] == ORIGIN_LAYER or (names_layers(v) and ORIGIN_LAYER in v):
            raise ValueError(
                f"`set` : `{cle}` pose la couche `origine`, qui appartient à un import "
                f"(`data_write(donnees_d_origine=true)`), jamais à une règle.")
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
        poses = [_valeur_posee(k, v) for k, v in set_.items()]
        colonnes = sorted({c for c, _, _ in poses})
        a_valeur = {c: val for c, porte, val in poses if porte}
        clauses = self._clauses(ns_id, filter, filters)
        if only_if_empty:
            if not a_valeur:
                raise ValueError(
                    "`only_if_empty` attend une colonne dont la VALEUR est posée : `set` "
                    "ne pose ici que des couches (`.comment`, `.link`), qui n'ont pas "
                    "de case vide à attendre. Retire `only_if_empty`, ou pose la valeur.")
            for c in sorted(a_valeur):
                clauses.append({"field": c, "op": "empty"})
        schema = self._schema_of(ns_id)
        cle_metier = self._declared_key_of(schema)
        if cle_metier and cle_metier in colonnes:
            raise ValueError(
                f"`set` vise `{cle_metier}`, la clé métier du tableau : une même valeur "
                f"posée sur plusieurs lignes les ferait entrer en collision. Rien n'est "
                f"écrit.")
        self._juger_la_regle(schema, colonnes, a_valeur, only_if_empty)
        declarees = {f.get("key") for f in _fields(schema)}
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
                out = self._boucle(datastore, ns_id, set_, clauses, cursor)
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
                f"`set` remplace {', '.join(f'`{c}`' for c in verrouillees)}, colonne(s) "
                f"verrouillée(s) (`readonly`) : une règle ne force pas un verrou. Avec "
                f"`only_if_empty=true`, elle remplit les cases encore vides.")
        for c, val in a_valeur.items():
            decl = champ_declare(schema, c) or {}
            opts = decl.get("options")
            if (isinstance(opts, list) and not isinstance(val, (list, dict))
                    and hors_des_options(val, opts)):
                raise RowValidationError([
                    f"{c}: valeur {val!r} hors options "
                    f"({', '.join(str(o) for o in opts)}) — la règle la poserait sur "
                    f"chaque ligne. Rien n'est écrit."])

    def _boucle(self, datastore, ns_id, set_, clauses, cursor) -> dict:
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
                    ecrite = self.update_row(datastore, rid, dict(set_),
                                             expected_revision=r.get("rev"))
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
