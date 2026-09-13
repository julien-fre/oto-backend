"""LIRE des lignes : une, une page, un curseur, un compte, un agrégat.

Extrait de `core.py` (déplacement pur, 07/09/2026) — un mixin que `DatastorePg`
compose, sur le modèle de `SchemaOpsMixin`. La projection d'une ligne
(`_row_to_dict`) reste au noyau : elle est le seul point par lequel passe TOUTE
ligne servie, écriture comprise.
"""
from __future__ import annotations

from typing import Optional

from .. import db
from . import layers as dsl
from . import versions as dsver
from . import schema as dsv2
from .columns import _refuse_group_by_compose
from .errors import InvalidCursor, RowNotFound
from .outils import (
    _OFFSET_CURSOR_PREFIX,
    _decode_cursor,
    _decode_offset_cursor,
    _encode_cursor,
    _encode_offset_cursor,
    _filter_clauses,
)


class LectureMixin:
    """Les lectures du store. Composé par `DatastorePg`."""

    @staticmethod
    def _order_health(ns_id: int, order_by, otype, oopts, q, filters) -> Optional[dict]:
        """Compteur d'écart d'un tri typé (#336) — présent SEULEMENT quand il y a
        un écart : prévenir là où tout est conforme ferait cesser de lire
        l'avertissement là où il compte (la leçon de l'avertissement scout)."""
        if not otype:
            return None
        health = db.datastore_order_health(
            ns_id, order_by=order_by, order_type=otype, order_options=oopts,
            q=q, filters=filters)
        return health if (health["off_type"] or health["empty"]) else None

    def get_row(self, datastore: str, row_id: str, *,
                layers: str = dsl.DEFAUT,
                versions: tuple = dsver.DEFAUT,
                empties: str = dsl.EMPTIES_DEFAUT) -> dict:
        ns_id = self._resolve(datastore)
        row = db.datastore_get_row(ns_id, row_id)
        if not row:
            raise RowNotFound(row_id)
        return self._row_to_dict(row, self._schema_of(ns_id), layers=layers,
                                 versions=versions, empties=empties)

    def list_rows(
        self,
        datastore: str,
        filter: Optional[dict] = None,
        limit: int = 100,
        versions: tuple = dsver.DEFAUT,
    ) -> list[dict]:
        """Filtre exact k:v en Python (chemin MCP `data_rows`). Ordre stable plus
        ancien d'abord (compat historique)."""
        ns_id = self._resolve(datastore)
        sch = self._schema_of(ns_id)
        out: list[dict] = []
        for row in db.datastore_list_rows(ns_id, order_by="_created_at", order_dir="asc"):
            record = self._row_to_dict(row, sch, versions=versions)
            if filter and not all(str(record.get(k)) == str(v) for k, v in filter.items()):
                continue
            out.append(record)
            if len(out) >= limit:
                break
        return out

    def cursor_rows(
        self,
        datastore: str,
        *,
        filter: Optional[dict] = None,
        limit: int = 100,
        cursor: Optional[str] = None,
        q: Optional[str] = None,
        order_by: Optional[str] = None,
        order_dir: str = "desc",
        filters: Optional[list] = None,
        layers: str = dsl.DEFAUT,
        versions: tuple = dsver.DEFAUT,
        empties: str = dsl.EMPTIES_DEFAUT,
    ) -> dict:
        """Page pour l'agent (chemin MCP `data_rows`), filtre/recherche/tri poussés en
        SQL. Renvoie `{rows, next_cursor}` — `next_cursor` non nul ⇒ il reste des lignes
        (repasse-le pour la suite).

        `filter` et `filters` se cumulent (cf. `_filter_clauses`) : le premier vise une
        colonne, le second en vise plusieurs à la fois.

        Deux régimes de pagination, et le curseur porte lequel :
          - **sans `order_by`** (défaut) → keyset sur `row_id` = ordre de création,
            robuste aux écritures concurrentes (pas d'OFFSET qui dérive) ;
          - **avec `order_by`** → tri SQL demandé + pagination par offset, faute de clé
            keyset stable pour un tri arbitraire.

        Repasser le curseur d'un régime dans l'autre lève `InvalidCursor` plutôt que de
        rendre une page fausse — un curseur d'offset relu comme un `row_id` cadrerait
        silencieusement sur les mauvaises lignes."""
        ns_id = self._resolve(datastore)
        filters = _filter_clauses(filter, filters)
        # ⚠️ Le schéma se lit une fois par PAGE, et seulement quand il y a quelque chose
        # à servir : le lire dès l'entrée ferait payer une requête à un appel qui va
        # refuser son curseur — un coût là où il n'y a même pas de résultat.
        sch = self._schema_of(ns_id) if order_by else None
        if order_by:
            offset = _decode_offset_cursor(cursor) if cursor else 0
            # Même résolution de type que `page_rows` : le tri d'un champ ne peut
            # pas répondre juste sur une face et faux sur l'autre (#336).
            otype, oopts = dsv2.order_spec(sch, order_by)
            rows = db.datastore_list_rows(
                ns_id, offset=offset, limit=limit, order_by=order_by,
                order_dir=order_dir, q=q, filters=filters,
                order_type=otype, order_options=oopts)
            next_cursor = (_encode_offset_cursor(offset + len(rows))
                           if len(rows) == limit else None)
            if sch is None and rows:
                sch = self._schema_of(ns_id)
            out = {"rows": [self._row_to_dict(r, sch, layers=layers, versions=versions,
                                              empties=empties) for r in rows],
                   "next_cursor": next_cursor,
            # ⚠️ La réponse DÉCLARE ce qu'elle sert (oto#140). Sans elle, « je ne
            # l'ai pas demandée » et « elle n'existe pas sur cette case » se lisent
            # pareil — et le lecteur réinventerait un marqueur, en pire, puisque cette
            # fois il l'aurait deviné. Au niveau de la RÉPONSE, jamais de la cellule :
            # coût nul par ligne, et l'enveloppe devient autoportante.
                   "versions_servies": list(versions)}
            health = self._order_health(ns_id, order_by, otype, oopts, q, filters)
            if health:
                out["order_health"] = health
            return out
        after = _decode_cursor(cursor) if cursor else None
        if after and after.startswith(_OFFSET_CURSOR_PREFIX):
            raise InvalidCursor(cursor)  # curseur trié repassé sans `order_by`
        rows = db.datastore_list_rows_after(
            ns_id, after_row_id=after, limit=limit, q=q, filters=filters)
        if sch is None and rows:
            sch = self._schema_of(ns_id)
        out = [self._row_to_dict(r, sch, layers=layers, versions=versions, empties=empties)
               for r in rows]
        next_cursor = _encode_cursor(rows[-1]["row_id"]) if len(rows) == limit else None
        # ⚠️ SECOND retour de cette méthode — le chemin du curseur simple. Le premier
        # (trié) le déclarait déjà ; celui-ci non. Une fonction à deux sorties est une
        # famille de deux chemins, et la question est « combien y en a-t-il ? ».
        return {"rows": out, "next_cursor": next_cursor,
                "versions_servies": list(versions)}

    def count_rows(self, datastore: str, *, filter: Optional[dict] = None,
                   q: Optional[str] = None, filters: Optional[list] = None) -> int:
        """Nombre de lignes (mêmes `filter`/`filters`/`q` que `cursor_rows`), poussé en
        SQL (`COUNT(*)`) — sans rapatrier les lignes (feedback #191 : stats d'un gros
        vivier sans charger 300+ lignes en contexte)."""
        ns_id = self._resolve(datastore)
        clauses = _filter_clauses(filter, filters)
        return db.datastore_count_rows(ns_id, q=q, filters=clauses)

    def aggregate(self, datastore: str, *, group_by=None,
                  metrics: Optional[list] = None, filter: Optional[dict] = None,
                  q: Optional[str] = None, filters: Optional[list] = None) -> list[dict]:
        """Agrégat serveur (feedback #191) : COUNT/SUM/AVG/MIN/MAX sur des champs JSONB,
        `group_by` optionnel — stats d'un vivier sans rapatrier les lignes. Délègue à
        `db.datastore_aggregate`. Deux formes de filtre cumulables : `filter` exact
        `{col: val}` (chemin MCP) et `q`/`filters` riches ({field|fields, op, value},
        mêmes clauses que `page_rows`) — le dashboard agrège ainsi le MÊME jeu que sa
        vue filtrée (tuiles metric).

        `group_by` accepte une LISTE de colonnes (oto#22) : leurs valeurs sont mises en
        commun, une ligne comptant une occurrence par colonne renseignée."""
        ns_id = self._resolve(datastore)
        clauses = _filter_clauses(filter, filters)
        _refuse_group_by_compose(group_by)
        return db.datastore_aggregate(
            ns_id, group_by=group_by, metrics=metrics, q=q, filters=clauses)

    def page_rows(
        self,
        datastore: str,
        *,
        offset: int = 0,
        limit: int = 50,
        order_by: Optional[str] = None,
        order_dir: str = "desc",
        q: Optional[str] = None,
        filter: Optional[dict] = None,
        filters: Optional[list] = None,
        layers: str = dsl.DEFAUT,
        versions: tuple = dsver.DEFAUT,
        empties: str = dsl.EMPTIES_DEFAUT,
    ) -> dict:
        """Page server-side (tri/recherche/filtres SQL) + total — pour le dashboard.
        Deux formes de filtre CUMULABLES, comme `aggregate` : `filter` exact
        `{col: val}` (chemin MCP, et la CLI `--filter`) et `filters` riches
        (liste `{field, op, value}`, combinées en ET). Renvoie
        `{rows, total, offset, limit}`.

        `filter` manquait ici alors que `cursor_rows`, `aggregate` et `claim_next`
        le portent : la face REST du même verbe ignorait donc **en silence** un
        paramètre que la face MCP honore (#303).

        Le tri honore le TYPE DÉCLARÉ de la colonne (#336) : `number` trié
        numériquement, `enum` dans l'ordre déclaré des options, `date` en
        chronologique. Les valeurs non conformes vont en QUEUE dans les deux
        sens (bloc alphabétique), les cases vides tout au bout — et quand il y
        en a, la réponse porte `order_health: {off_type, empty}` (compté sur le
        jeu filtré entier, absent quand tout est conforme)."""
        ns_id = self._resolve(datastore)
        clauses = _filter_clauses(filter, filters) or None
        sch = self._schema_of(ns_id)
        # Le tri honore le TYPE déclaré (#336) — résolu ICI, où le schéma est connu :
        # la couche db reçoit un type générique, jamais le schéma.
        otype, oopts = dsv2.order_spec(sch, order_by)
        rows = db.datastore_list_rows(
            ns_id, offset=offset, limit=limit, order_by=order_by,
            order_dir=order_dir, q=q, filters=clauses,
            order_type=otype, order_options=oopts)
        out = {
            "rows": [self._row_to_dict(r, sch, layers=layers, versions=versions,
                                       empties=empties) for r in rows],
            # Le total doit décrire le MÊME jeu que la page : filtré aussi, sinon la
            # pagination du dashboard annonce des lignes qu'elle ne servira jamais.
            "total": db.datastore_count_rows(ns_id, q=q, filters=clauses),
            "offset": offset, "limit": limit,
            # ⚠️ La réponse DÉCLARE ce qu'elle sert (oto#140). Sans elle, « je ne
            # l'ai pas demandée » et « elle n'existe pas sur cette case » se lisent
            # pareil — et le lecteur réinventerait un marqueur, en pire, puisque cette
            # fois il l'aurait deviné. Au niveau de la RÉPONSE, jamais de la cellule :
            # coût nul par ligne, et l'enveloppe devient autoportante.
            "versions_servies": list(versions),
        }
        health = self._order_health(ns_id, order_by, otype, oopts, q, clauses)
        if health:
            out["order_health"] = health
        return out
