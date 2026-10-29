"""LIRE des lignes : une, une page, un curseur, un compte, un agrégat.

Extrait de `core.py` (déplacement pur, 07/09/2026) — un mixin que `DatastorePg`
compose, sur le modèle de `SchemaOpsMixin`. La projection d'une ligne
(`_row_to_dict`) reste au noyau : elle est le seul point par lequel passe TOUTE
ligne servie, écriture comprise.
"""
from __future__ import annotations

from typing import Optional

from .. import db
from ..db import valeurs_servies
from . import acces_agent as aga
from . import layers as dsl
from . import versions as dsver
from . import schema as dsv2
from .columns import _refuse_composite_compare, _refuse_group_by_compose
from .dates import typer_les_clauses
from .errors import InvalidCursor, RowNotFound
from .recherche import recherche
from .outils import (
    _OFFSET_CURSOR_PREFIX,
    _decode_cursor,
    _decode_offset_cursor,
    _encode_cursor,
    _encode_offset_cursor,
    _filter_clauses,
)


# Les tris qui ne lisent pas `data` : ceux que `db.datastore_page_mince` sert.
_TRIS_META = (None, "", "_created_at", "_updated_at", "_id")


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

    def _clauses(self, ns_id: int, filter: Optional[dict], filters: Optional[list],
                 *conditions: list) -> list[dict]:
        """Les clauses d'un appel de lecture, GARDÉES contre la comparaison d'une
        colonne composite entière (oto#22) — un seul passage pour toutes les
        lectures, `conditions` portant les `where` des métriques d'un agrégat."""
        clauses = _filter_clauses(filter, filters)
        _refuse_composite_compare(clauses + [c for cs in conditions for c in cs],
                                  lambda: self._schema_of(ns_id))
        # #859 : une colonne déclarée date se compare en instants, pas en texte.
        return typer_les_clauses(clauses, lambda: self._schema_of(ns_id))

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
        """Les `limit` premières lignes, plus ancienne d'abord (surfaces d'affichage :
        `data_app`, app de revue ; sync du feed). `filter` = la MÊME grammaire que
        `cursor_rows`/`count_rows` (`{col: val}` ou `{col: {op: val}}`), poussée en SQL
        par le même moteur.

        ⚠️ oto#74 : ce chemin filtrait en Python par égalité de chaînes — un opérateur
        (`{"in": [...]}`, `{"gt": 5}`) y était comparé au texte d'un dict, et rendait
        zéro ligne sans erreur, pendant que `count_rows` rendait le vrai compte sur le
        même filtre. Un seul moteur : un opérateur inconnu lève (`ValueError`)."""
        ns_id = self._resolve(datastore)
        rows = db.datastore_list_rows(
            ns_id, limit=limit, order_by="_created_at", order_dir="asc",
            filters=self._clauses(ns_id, filter, None))
        if not rows:
            return []
        sch = self._schema_of(ns_id)
        decl = dsv2.cles_declarees(sch)
        return [self._row_to_dict(r, sch, versions=versions, declarees=decl) for r in rows]

    def cursor_rows(
        self,
        datastore: str,
        *,
        filter: Optional[dict] = None,
        limit: int = 100,
        cursor: Optional[str] = None,
        q: Optional[str] = None,
        q_scope: Optional[str] = None,
        order_by: Optional[str] = None,
        order_dir: str = "desc",
        filters: Optional[list] = None,
        layers: str = dsl.DEFAUT,
        versions: tuple = dsver.DEFAUT,
        empties: str = dsl.EMPTIES_DEFAUT,
        fields: Optional[list[str]] = None,
    ) -> dict:
        """Page pour l'agent (chemin MCP `data_rows`), filtre/recherche/tri poussés en
        SQL. Renvoie `{rows, next_cursor}` — `next_cursor` non nul ⇒ il reste des lignes
        (repasse-le pour la suite).

        `filter` et `filters` se cumulent (cf. `_filter_clauses`) : le premier vise une
        colonne, le second en vise plusieurs à la fois.

        Deux régimes de pagination, et le curseur porte lequel :
          - **sans `order_by` ni `q`** (défaut) → keyset sur `row_id` = ordre de
            création, robuste aux écritures concurrentes (pas d'OFFSET qui dérive) ;
          - **avec `order_by`** → tri SQL demandé + pagination par offset, faute de clé
            keyset stable pour un tri arbitraire ;
          - **avec `q` sans `order_by`** → les meilleures correspondances d'abord
            (#307), donc un tri, donc l'offset : un rang n'est pas une clé keyset.

        Repasser le curseur d'un régime dans l'autre lève `InvalidCursor` plutôt que de
        rendre une page fausse — un curseur d'offset relu comme un `row_id` cadrerait
        silencieusement sur les mauvaises lignes.

        `fields` (oto-backend#980, lot 2) : projette CHAQUE ligne dès sa fabrication
        (`_row_to_dict`), pas après coup — `["*"]` ou `None` = pas de projection,
        la page sort inchangée."""
        cherche = valeurs_servies.resoudre(recherche(q, q_scope))
        ns_id = self._resolve(datastore)
        proj = None if not fields or "*" in fields else frozenset(fields)
        filters = self._clauses(ns_id, filter, filters)
        # ⚠️ Le schéma se lit une fois par PAGE, et seulement quand il y a quelque chose
        # à servir : le lire dès l'entrée ferait payer une requête à un appel qui va
        # refuser son curseur — un coût là où il n'y a même pas de résultat.
        sch = self._schema_of(ns_id) if order_by else None
        if order_by or cherche:
            offset = _decode_offset_cursor(cursor) if cursor else 0
            # Même résolution de type que `page_rows` : le tri d'un champ ne peut
            # pas répondre juste sur une face et faux sur l'autre (#336).
            otype, oopts = dsv2.order_spec(sch, order_by) if order_by else (None, None)
            rows = db.datastore_list_rows(
                ns_id, offset=offset, limit=limit, order_by=order_by,
                order_dir=order_dir, q=cherche, filters=filters,
                order_type=otype, order_options=oopts)
            next_cursor = (_encode_offset_cursor(offset + len(rows))
                           if len(rows) == limit else None)
            if sch is None and rows:
                sch = self._schema_of(ns_id)
            decl = dsv2.cles_declarees(sch)
            out = {"rows": [self._row_to_dict(r, sch, layers=layers, versions=versions,
                                              empties=empties, fields=proj,
                                              declarees=decl) for r in rows],
                   "next_cursor": next_cursor,
            # ⚠️ La réponse DÉCLARE ce qu'elle sert (oto#140). Sans elle, « je ne
            # l'ai pas demandée » et « elle n'existe pas sur cette case » se lisent
            # pareil — et le lecteur réinventerait un marqueur, en pire, puisque cette
            # fois il l'aurait deviné. Au niveau de la RÉPONSE, jamais de la cellule :
            # coût nul par ligne, et l'enveloppe devient autoportante.
                   "versions_servies": list(versions)}
            health = self._order_health(ns_id, order_by, otype, oopts, cherche, filters)
            if health:
                out["order_health"] = health
            return out
        after = _decode_cursor(cursor) if cursor else None
        if after and after.startswith(_OFFSET_CURSOR_PREFIX):
            raise InvalidCursor(cursor)  # curseur trié repassé sans `order_by`
        rows = db.datastore_list_rows_after(
            ns_id, after_row_id=after, limit=limit, filters=filters)
        if sch is None and rows:
            sch = self._schema_of(ns_id)
        decl = dsv2.cles_declarees(sch)
        out = [self._row_to_dict(r, sch, layers=layers, versions=versions, empties=empties,
                                 fields=proj, declarees=decl)
               for r in rows]
        next_cursor = _encode_cursor(rows[-1]["row_id"]) if len(rows) == limit else None
        # ⚠️ SECOND retour de cette méthode — le chemin du curseur simple. Le premier
        # (trié) le déclarait déjà ; celui-ci non. Une fonction à deux sorties est une
        # famille de deux chemins, et la question est « combien y en a-t-il ? ».
        return {"rows": out, "next_cursor": next_cursor,
                "versions_servies": list(versions)}

    def count_rows(self, datastore: str, *, filter: Optional[dict] = None,
                   q: Optional[str] = None, q_scope: Optional[str] = None,
                   filters: Optional[list] = None) -> int:
        """Nombre de lignes (mêmes `filter`/`filters`/`q`/`q_scope` que `cursor_rows`),
        poussé en SQL (`COUNT(*)`) — sans rapatrier les lignes (feedback #191 : stats
        d'un gros vivier sans charger 300+ lignes en contexte)."""
        cherche = valeurs_servies.resoudre(recherche(q, q_scope))
        ns_id = self._resolve(datastore)
        clauses = self._clauses(ns_id, filter, filters)
        return db.datastore_count_rows(ns_id, q=cherche, filters=clauses)

    def aggregate(self, datastore: str, *, group_by=None,
                  metrics: Optional[list] = None, filter: Optional[dict] = None,
                  q: Optional[str] = None, q_scope: Optional[str] = None,
                  filters: Optional[list] = None) -> list[dict]:
        """Agrégat serveur (feedback #191) : COUNT/SUM/AVG/MIN/MAX sur des champs JSONB,
        `group_by` optionnel — stats d'un vivier sans rapatrier les lignes. Délègue à
        `db.datastore_aggregate`. Deux formes de filtre cumulables : `filter` exact
        `{col: val}` (chemin MCP) et `q`/`filters` riches ({field|fields, op, value},
        mêmes clauses que `page_rows`) — le dashboard agrège ainsi le MÊME jeu que sa
        vue filtrée (tuiles metric).

        `group_by` accepte une LISTE de colonnes (oto#22) : leurs valeurs sont mises en
        commun, une ligne comptant une occurrence par colonne renseignée."""
        cherche = valeurs_servies.resoudre(recherche(q, q_scope))
        ns_id = self._resolve(datastore)
        clauses = self._clauses(
            ns_id, filter, filters,
            *[m["where"] for m in metrics or []
              if isinstance(m, dict) and isinstance(m.get("where"), list)])
        _refuse_group_by_compose(group_by)
        return db.datastore_aggregate(
            ns_id, group_by=group_by, metrics=metrics, q=cherche, filters=clauses)

    def count_by(self, datastore: str, fields: list, *, filter: Optional[dict] = None,
                 filters: Optional[list] = None, q: Optional[str] = None,
                 q_scope: Optional[str] = None,
                 values_limit: Optional[int] = None) -> dict:
        """Compteurs de N colonnes en UN appel (`db.datastore_count_by`) : par colonne,
        les valeurs les plus fréquentes, les vides, les distinctes et le total, sous les
        MÊMES `filter`/`filters`/`q` que `page_rows` — ce qu'un écran affiche au-dessus
        de sa vue filtrée, sans N appels qui relisent chacun tout le jeu.

        Une colonne inconnue du schéma déclaré est REFUSÉE : comptée, elle rendrait
        « tout est vide », une réponse qui a l'air d'en être une. Une colonne masquée
        aux agents (`agent_access: "none"`, oto#83) l'est aussi sur la face agent —
        ses valeurs ne sortent pas plus en compteurs qu'en lignes."""
        cherche = valeurs_servies.resoudre(recherche(q, q_scope))
        ns_id = self._resolve(datastore)
        sch = self._schema_of(ns_id)
        declarees = set(dsv2.cles_declarees(sch))
        champs = list(fields) if isinstance(fields, (list, tuple)) else fields
        if declarees and isinstance(champs, list):
            inconnues = [k for k in champs if isinstance(k, str) and "." not in k
                         and "[" not in k and k not in declarees]
            if inconnues:
                raise ValueError(
                    f"`count_by`: column(s) not declared in this table: "
                    f"{', '.join(inconnues)} — `data_get_schema` lists them")
        if aga.appel_d_agent() and isinstance(champs, list):
            cachees = [k for k in champs if k in aga.masquees(sch)]
            if cachees:
                raise ValueError(
                    f"`count_by`: {', '.join(cachees)} is not readable by an "
                    f"agent on this table (`agent_access: \"none\"`)")
        clauses = self._clauses(ns_id, filter, filters)
        return db.datastore_count_by(
            ns_id, champs, q=cherche, filters=clauses,
            values_limit=(db.COUNT_BY_VALUES_DEFAUT if values_limit is None
                          else values_limit))

    def page_rows(
        self,
        datastore: str,
        *,
        offset: int = 0,
        limit: int = 50,
        order_by: Optional[str] = None,
        order_dir: str = "desc",
        q: Optional[str] = None,
        q_scope: Optional[str] = None,
        filter: Optional[dict] = None,
        filters: Optional[list] = None,
        layers: str = dsl.DEFAUT,
        versions: tuple = dsver.DEFAUT,
        empties: str = dsl.EMPTIES_DEFAUT,
        fields: Optional[list[str]] = None,
        total_max: Optional[int] = None,
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
        jeu filtré entier, absent quand tout est conforme).

        `fields` (oto-backend#980, lot 2) : MÊME sémantique que côté MCP
        `data_rows` — colonnes gardées + `_id` toujours présent, une colonne
        déclarée sans valeur rendue `null`. Projette dès `_row_to_dict`, pas
        après coup : un ramassage complet du vivier par cette route coûtait
        ~10,5 s de calcul Python sur son aplatissement de couches (mesuré
        17/09/2026), pour un usage qui ne lit souvent que 3-4 colonnes.
        Absent (ou `["*"]`) = même payload qu'avant ce lot.

        `q`/`q_scope` (#307) : même recherche que `cursor_rows` ; sans `order_by`, les
        meilleures correspondances d'abord (`db.datastore_list_rows`).

        `total_max` : le total n'est compté qu'AU PLUS jusqu'à ce nombre — une barre de
        recherche qui n'affiche que les premières lignes n'a pas à payer le compte
        exact d'une recherche `q`, qui coûte autant que de lignes trouvées
        (`db.datastore_count_rows`). Au-delà, `total` vaut `total_max` et
        `total_capped` le DIT : jamais un total tronqué qui se lirait exact. Absent =
        total exact, sans `total_capped` (contrat inchangé : le dashboard pagine et
        exporte sur ce total)."""
        if total_max is not None and total_max < 1:
            raise ValueError(f"total_max={total_max}: an integer ≥ 1 is expected.")
        cherche = valeurs_servies.resoudre(recherche(q, q_scope))
        ns_id = self._resolve(datastore)
        proj = None if not fields or "*" in fields else frozenset(fields)
        clauses = self._clauses(ns_id, filter, filters) or None
        sch = self._schema_of(ns_id)
        # Le tri honore le TYPE déclaré (#336) — résolu ICI, où le schéma est connu :
        # la couche db reçoit un type générique, jamais le schéma.
        otype, oopts = dsv2.order_spec(sch, order_by)
        # Page + total + compteurs d'écart en UNE requête quand la lecture est
        # amincissable (oto-backend#980, suite) — `None` = pas amincissable
        # (ex. recherche plein texte `q`), on retombe alors sur des appels séparés :
        # page mince au tri méta sans `q`, sinon les trois appels historiques.
        combined = db.datastore_page_with_stats(
            ns_id, offset=offset, limit=limit, order_by=order_by,
            order_dir=order_dir, q=cherche, filters=clauses,
            order_type=otype, order_options=oopts)
        if combined is not None:
            rows, total, off_type, empty = combined
            health = ({"off_type": off_type, "empty": empty}
                      if (off_type or empty) else None)
        elif not cherche and not otype and order_by in _TRIS_META:
            # Tri méta sans recherche (le défaut du tableau, sans filtre amincissable) :
            # la page se coupe sur les clés, sans fenêtre sur tout le jeu — mêmes
            # lignes que `datastore_list_rows`. Le total reste celui d'en dessous :
            # exact par défaut, borné par `total_max`.
            rows = db.datastore_page_mince(
                ns_id, offset=offset, limit=limit, order_by=order_by,
                order_dir=order_dir, filters=clauses)
            total = db.datastore_count_rows(
                ns_id, filters=clauses,
                jusqua=None if total_max is None else total_max + 1)
            health = None
        else:
            rows = db.datastore_list_rows(
                ns_id, offset=offset, limit=limit, order_by=order_by,
                order_dir=order_dir, q=cherche, filters=clauses,
                order_type=otype, order_options=oopts)
            total = db.datastore_count_rows(
                ns_id, q=cherche, filters=clauses,
                jusqua=None if total_max is None else total_max + 1)
            health = self._order_health(ns_id, order_by, otype, oopts, cherche, clauses)
        capped = total_max is not None and total > total_max
        decl = dsv2.cles_declarees(sch)
        out = {
            "rows": [self._row_to_dict(r, sch, layers=layers, versions=versions,
                                       empties=empties, fields=proj, declarees=decl)
                     for r in rows],
            # Le total doit décrire le MÊME jeu que la page : filtré aussi, sinon la
            # pagination du dashboard annonce des lignes qu'elle ne servira jamais.
            "total": total_max if capped else total,
            "offset": offset, "limit": limit,
            # ⚠️ La réponse DÉCLARE ce qu'elle sert (oto#140). Sans elle, « je ne
            # l'ai pas demandée » et « elle n'existe pas sur cette case » se lisent
            # pareil — et le lecteur réinventerait un marqueur, en pire, puisque cette
            # fois il l'aurait deviné. Au niveau de la RÉPONSE, jamais de la cellule :
            # coût nul par ligne, et l'enveloppe devient autoportante.
            "versions_servies": list(versions),
        }
        if total_max is not None:
            out["total_capped"] = capped
        if health:
            out["order_health"] = health
        return out
